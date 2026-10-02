"""Small HTTP client facade shared by the bus, Google Classroom and notification code.

* server mode: one bounded ``aiohttp.ClientSession`` per process (keeps the
  original FD-exhaustion protection);
* workers mode: the Workers ``fetch`` API (``workers.fetch``) with an abort
  timeout.  Cloudflare has no proxy support, so ``proxy`` is ignored there.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from tutnext import runtime

logger = logging.getLogger(__name__)

_MAX_CONNECTIONS = 50


class HttpError(Exception):
    """Raised for non-2xx responses by :meth:`HttpResponse.raise_for_status`."""

    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}: {message}")
        self.status = status
        self.url = url


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    set_cookies: list[str] = field(default_factory=list)
    url: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())

    def raise_for_status(self) -> None:
        if not self.ok:
            raise HttpError(self.status, self.reason or "error", self.url)


_session: Any = None
_session_lock: asyncio.Lock | None = None


async def _aiohttp_session() -> Any:
    global _session, _session_lock
    import aiohttp

    if _session_lock is None:
        _session_lock = asyncio.Lock()
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                connector = aiohttp.TCPConnector(
                    limit=_MAX_CONNECTIONS, limit_per_host=_MAX_CONNECTIONS, ttl_dns_cache=300
                )
                _session = aiohttp.ClientSession(connector=connector)
    return _session


async def close() -> None:
    """Close the shared server-mode session (no-op in Workers)."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: str | bytes | None = None,
    timeout: float = 30,
    follow_redirects: bool = True,
    proxy: str | None = None,
) -> HttpResponse:
    if runtime.IS_WORKERS:
        return await _fetch_request(method, url, headers, data, timeout, follow_redirects)
    return await _aiohttp_request(method, url, headers, data, timeout, follow_redirects, proxy)


async def get(url: str, **kwargs: Any) -> HttpResponse:
    return await request("GET", url, **kwargs)


async def post(url: str, **kwargs: Any) -> HttpResponse:
    return await request("POST", url, **kwargs)


async def _aiohttp_request(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    data: str | bytes | None,
    timeout: float,
    follow_redirects: bool,
    proxy: str | None,
) -> HttpResponse:
    import aiohttp

    session = await _aiohttp_session()
    async with session.request(
        method,
        url,
        headers=headers,
        data=data,
        timeout=aiohttp.ClientTimeout(total=timeout),
        allow_redirects=follow_redirects,
        proxy=proxy,
    ) as resp:
        body = await resp.read()
        return HttpResponse(
            status=resp.status,
            body=body,
            headers={k.lower(): v for k, v in resp.headers.items()},
            set_cookies=list(resp.headers.getall("Set-Cookie", [])),
            url=str(resp.url),
            reason=resp.reason or "",
        )


async def _fetch_request(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    data: str | bytes | None,
    timeout: float,
    follow_redirects: bool,
) -> HttpResponse:
    import js  # type: ignore[import-not-found]
    from workers import fetch  # type: ignore[import-not-found]

    options: dict[str, Any] = {
        "method": method,
        "headers": dict(headers or {}),
        "redirect": "follow" if follow_redirects else "manual",
        "signal": js.AbortSignal.timeout(int(timeout * 1000)),
    }
    if data is not None:
        options["body"] = data.decode("utf-8") if isinstance(data, bytes) else data

    resp = await fetch(url, **options)
    body = bytes(await resp.bytes())
    js_headers = resp.js_object.headers
    header_map: dict[str, str] = {}
    try:
        for pair in js_headers.entries():
            key, value = pair.to_py() if hasattr(pair, "to_py") else pair
            header_map[str(key).lower()] = str(value)
    except Exception:  # noqa: BLE001 - header iteration is best effort
        logger.debug("fetch: could not iterate response headers", exc_info=True)
    try:
        set_cookies = [str(c) for c in js_headers.getSetCookie().to_py()]
    except Exception:  # noqa: BLE001
        set_cookies = []
    return HttpResponse(
        status=int(resp.status),
        body=body,
        headers=header_map,
        set_cookies=set_cookies,
        url=str(resp.js_object.url or url),
        reason=str(resp.js_object.statusText or ""),
    )
