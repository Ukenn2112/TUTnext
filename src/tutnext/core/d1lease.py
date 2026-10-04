"""Short leases stored in D1, shared by every Worker isolate (API and cron).

The per-user ``asyncio.Lock`` in :mod:`tutnext.services.gakuen.session_manager` only
serialises work inside one isolate.  Since the API and the cron Worker run in
different isolates, both could log the same student into T-NEXT at the same moment.
(The hybrid server does not take part; see ``_default_executor``.)  A lease row in D1 closes that gap.

Acquire is a single upsert that only overwrites an *expired* row, so it is atomic
(D1 has one writer per database).  A holder that dies (CPU limit, isolate eviction)
simply lets its lease expire.  When D1 itself is unreachable the lease degrades to
"no cross-process lock" instead of blocking every request.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from tutnext import runtime
from tutnext.core.d1redis import _changes

logger = logging.getLogger(__name__)

# Expiry uses D1's clock (not the caller's) so processes with skewed clocks agree.
# Re-acquiring our own row succeeds, so a retried request whose first attempt committed
# but lost its response does not lock us out of our own lease.
_ACQUIRE_SQL = (
    "INSERT INTO locks (name, owner, expires_at) VALUES (?1, ?2, unixepoch('subsec') + ?3) "
    "ON CONFLICT(name) DO UPDATE SET owner = excluded.owner, expires_at = excluded.expires_at "
    "WHERE locks.expires_at < unixepoch('subsec') OR locks.owner = excluded.owner"
)
_RELEASE_SQL = "DELETE FROM locks WHERE name = ? AND owner = ?"

POLL_SECONDS = 0.5


class LeaseBusy(Exception):
    """The lease is held by someone else and did not free up within the wait budget."""


def _default_executor() -> Any | None:
    if runtime.IS_WORKERS:
        from tutnext.core.d1client import BindingExecutor

        return BindingExecutor("DB")
    # Server (classic or hybrid): no lease. Through the D1 REST API every lock would cost two
    # extra HTTP calls per monitor login (~500 per cycle), close to the API rate limit; the
    # monitor is moving to Workers, where the binding makes the lease cheap.
    return None


class D1Lease:
    def __init__(self, executor: Any | None) -> None:
        self._executor = executor

    @property
    def enabled(self) -> bool:
        return self._executor is not None

    async def try_acquire(self, name: str, owner: str, ttl: float) -> bool:
        results = await self._executor.batch([(_ACQUIRE_SQL, (name, owner, ttl))])
        return _changes(results[0]) == 1

    async def release(self, name: str, owner: str) -> None:
        await self._executor.batch([(_RELEASE_SQL, (name, owner))])

    @asynccontextmanager
    async def hold(self, name: str, *, ttl: float, wait: float) -> AsyncIterator[None]:
        """Hold lease *name* for the body; raise :class:`LeaseBusy` after *wait* seconds."""
        if not self.enabled:
            yield
            return
        owner = uuid.uuid4().hex
        deadline = time.monotonic() + wait
        acquired = False
        busy = True
        while True:
            try:
                acquired = await self.try_acquire(name, owner, ttl)
            except Exception as e:  # noqa: BLE001 — D1 outage must not take the API down
                logger.warning("[D1Lease] acquire %s failed, continuing without lease: %s", name, e)
                # The upsert may have committed before the error (e.g. a timeout); drop it so
                # the row does not block other processes for a whole ttl.
                try:
                    await self.release(name, owner)
                except Exception:  # noqa: BLE001
                    pass
                busy = False
                break
            if acquired:
                busy = False
                break
            if time.monotonic() + POLL_SECONDS > deadline:
                break
            await asyncio.sleep(POLL_SECONDS)
        if busy:
            raise LeaseBusy(name)
        try:
            yield
        finally:
            if acquired:
                try:
                    await self.release(name, owner)
                except Exception as e:  # noqa: BLE001 — it expires on its own after ttl
                    logger.warning("[D1Lease] release %s failed (expires in %.0fs): %s", name, ttl, e)


_lease: D1Lease | None = None


def get_lease() -> D1Lease:
    global _lease
    if _lease is None:
        _lease = D1Lease(_default_executor())
    return _lease
