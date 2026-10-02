"""Executors that run SQL statements against Cloudflare D1.

Two transports share one tiny interface — ``await executor.batch(stmts)`` where
``stmts`` is a list of ``(sql, params)`` and the result is one D1 result object
per statement (a mapping with ``results`` rows and ``meta.changes``):

* :class:`BindingExecutor` — inside a Worker, through the ``DB`` binding
  (``prepare().bind().batch()``, atomic per batch);
* :class:`HttpExecutor` — from the classic server, through the D1 REST API
  (one HTTP request per statement, sequential, not atomic).

The server uses the HTTP executor so that the hybrid deployment (API on
Workers, monitor + daily push on the server) has a single source of truth in D1
for users, OAuth tokens, Live Activity state and the API caches.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tutnext import runtime

logger = logging.getLogger(__name__)

Stmt = tuple[str, tuple[Any, ...]]

_CF_API = "https://api.cloudflare.com/client/v4"


class D1Error(RuntimeError):
    """Raised when the D1 REST API reports an error."""


class BindingExecutor:
    """Run statements through a Worker D1 binding (Workers mode)."""

    def __init__(self, binding_name: str = "DB", *, timeout: float = 25) -> None:
        self.binding_name = binding_name
        self.timeout = timeout

    @property
    def _db(self) -> Any:
        return runtime.binding(self.binding_name)

    async def batch(self, stmts: list[Stmt]) -> list[Any]:
        if not stmts:
            return []
        db = self._db
        prepared = []
        for sql, params in stmts:
            stmt = db.prepare(sql)
            if params:
                stmt = stmt.bind(*params)
            prepared.append(stmt)
        # A hung D1 call would otherwise keep the invocation (and the isolate) suspended forever.
        return list(await asyncio.wait_for(db.batch(prepared), self.timeout))


class HttpExecutor:
    """Run statements through the D1 REST API (server mode).

    Requires an API token with *D1 Edit* on the account.  Statements of one
    batch run sequentially so that ordering-dependent writes (meta row before a
    TTL update, for example) behave like the binding executor.
    """

    def __init__(self, account_id: str, database_id: str, api_token: str, *, timeout: float = 30) -> None:
        self.url = f"{_CF_API}/accounts/{account_id}/d1/database/{database_id}/query"
        self._headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self.timeout = timeout

    _RETRIES = 2  # extra attempts on 429 / 5xx / network errors

    async def _query(self, sql: str, params: tuple[Any, ...]) -> Any:
        import asyncio
        import random

        from tutnext.core import http as core_http

        body = json.dumps({"sql": sql, "params": list(params)}, ensure_ascii=False)
        last_error: Exception | None = None
        for attempt in range(self._RETRIES + 1):
            try:
                resp = await core_http.request(
                    "POST", self.url, headers=self._headers, data=body, timeout=self.timeout
                )
            except Exception as e:  # noqa: BLE001 - network layer
                last_error = D1Error(f"D1 HTTP request failed: {e}")
            else:
                if resp.status == 429 or resp.status >= 500:
                    last_error = D1Error(f"D1 HTTP {resp.status}: {resp.text()[:200]}")
                else:
                    try:
                        payload = resp.json()
                    except Exception as e:  # noqa: BLE001
                        raise D1Error(f"D1 HTTP {resp.status}: non-JSON response {resp.text()[:200]!r}") from e
                    if resp.status != 200 or not payload.get("success"):
                        raise D1Error(f"D1 HTTP {resp.status}: {payload.get('errors') or payload}")
                    results = payload.get("result") or []
                    if not results:
                        raise D1Error("D1 HTTP: empty result list")
                    return results[0]
            if attempt < self._RETRIES:
                await asyncio.sleep(0.5 * (2**attempt) + random.random() * 0.3)
        assert last_error is not None
        raise last_error

    async def batch(self, stmts: list[Stmt]) -> list[Any]:
        return [await self._query(sql, params) for sql, params in stmts]


_http_executor: HttpExecutor | None = None


def get_http_executor() -> HttpExecutor:
    """Executor for the server, configured by ``CF_ACCOUNT_ID`` / ``CF_D1_DATABASE_ID`` / ``CF_API_TOKEN``."""
    global _http_executor
    if _http_executor is None:
        from tutnext.config import settings

        missing = [
            name
            for name, value in (
                ("CF_ACCOUNT_ID", settings.cf_account_id),
                ("CF_D1_DATABASE_ID", settings.cf_d1_database_id),
                ("CF_API_TOKEN", settings.cf_api_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"STORAGE_BACKEND=d1 requires {', '.join(missing)} in the environment")
        _http_executor = HttpExecutor(settings.cf_account_id, settings.cf_d1_database_id, settings.cf_api_token)
    return _http_executor
