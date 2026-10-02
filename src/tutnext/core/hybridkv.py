"""Key-routing Redis facade for the hybrid deployment.

In the hybrid topology the Worker serves the API while the classic server runs
the assignment monitor and the 20:30 push job.  State written by the API has to
be visible to the server and vice versa, so *shared* keys live in D1 (reached
from the server through the D1 REST API) while *private* monitor state stays in
the fast local Redis.

Shared (D1)                                  Private (local Redis)
-------------------------------------------  --------------------------------------
``la:*``   Live Activity tokens/schedules     ``monitor:*``      back-off windows
``room:*`` classroom cache (class_bulletin)   ``kadai_count:*``  assignment counters
``schedule:ical:*`` iCal cache (invalidated   ``user_courses:*`` / ``course_users:*``
   by the monitor on room changes)           ``api_error_*``, ``push_pool:*``
``{user}:kadai`` assignment list cache        anything else
   (written by the monitor, served by /kadai)
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

SHARED_PREFIXES: tuple[str, ...] = ("la:", "room:", "schedule:ical:")
SHARED_SUFFIXES: tuple[str, ...] = (":kadai",)


def default_is_shared(key: str) -> bool:
    return key.startswith(SHARED_PREFIXES) or key.endswith(SHARED_SUFFIXES)


def _pattern_targets(pattern: str) -> tuple[bool, bool]:
    """(use_remote, use_local) for a scan pattern."""
    literal = pattern.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0]
    if literal and literal.startswith(SHARED_PREFIXES):
        return True, False
    if literal and any(p.startswith(literal) for p in SHARED_PREFIXES):
        return True, True  # e.g. "l*" — could match shared and private keys
    if pattern.endswith(SHARED_SUFFIXES) or not literal:
        return True, True
    return False, True


class HybridRedis:
    """Routes each command to the remote (D1) or local Redis client by key."""

    def __init__(self, local: Any, remote: Any, is_shared: Callable[[str], bool] = default_is_shared) -> None:
        self.local = local
        self.remote = remote
        self.is_shared = is_shared

    def _for(self, key: Any) -> Any:
        key = key.decode() if isinstance(key, bytes) else str(key)
        return self.remote if self.is_shared(key) else self.local

    def _split(self, keys: tuple[Any, ...]) -> tuple[list[Any], list[Any]]:
        remote: list[Any] = []
        local: list[Any] = []
        for key in keys:
            (remote if self._for(key) is self.remote else local).append(key)
        return remote, local

    # -- single-key commands --------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Fallback for single-key commands not listed explicitly (hget, hgetall, zadd, ...)."""

        async def call(key: Any, *args: Any, **kwargs: Any) -> Any:
            return await getattr(self._for(key), name)(key, *args, **kwargs)

        return call

    async def get(self, key: Any) -> Any:
        return await self._for(key).get(key)

    async def set(self, key: Any, value: Any, *args: Any, **kwargs: Any) -> Any:
        return await self._for(key).set(key, value, *args, **kwargs)

    async def expire(self, key: Any, seconds: int) -> Any:
        return await self._for(key).expire(key, seconds)

    async def incr(self, key: Any, amount: int = 1) -> Any:
        return await self._for(key).incr(key, amount)

    # -- multi-key commands ---------------------------------------------------

    async def delete(self, *keys: Any) -> int:
        remote, local = self._split(keys)
        total = 0
        if remote:
            total += int(await self.remote.delete(*remote) or 0)
        if local:
            total += int(await self.local.delete(*local) or 0)
        return total

    async def exists(self, *keys: Any) -> int:
        remote, local = self._split(keys)
        total = 0
        if remote:
            total += int(await self.remote.exists(*remote) or 0)
        if local:
            total += int(await self.local.exists(*local) or 0)
        return total

    async def scan_iter(self, match: str = "*", count: int | None = None) -> AsyncIterator[Any]:
        use_remote, use_local = _pattern_targets(match)
        seen: set[str] = set()
        if use_remote:
            async for key in self.remote.scan_iter(match=match, count=count):
                k = key.decode() if isinstance(key, bytes) else str(key)
                if self.is_shared(k) and k not in seen:
                    seen.add(k)
                    yield key
        if use_local:
            async for key in self.local.scan_iter(match=match, count=count):
                k = key.decode() if isinstance(key, bytes) else str(key)
                if not self.is_shared(k) and k not in seen:
                    seen.add(k)
                    yield key

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:  # noqa: A003
        target = self._for(keys_and_args[0]) if numkeys and keys_and_args else self.local
        return await target.eval(script, numkeys, *keys_and_args)

    def pipeline(self, transaction: bool = True) -> HybridPipeline:
        return HybridPipeline(self)

    async def purge_expired(self) -> int:
        purge = getattr(self.remote, "purge_expired", None)
        return int(await purge()) if purge else 0

    async def ping(self) -> bool:
        await self.local.ping()
        await self.remote.ping()
        return True

    async def aclose(self) -> None:
        for client in (self.local, self.remote):
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    close = aclose


class HybridPipeline:
    """Records commands, forwards each to the right sub-pipeline, restores result order."""

    def __init__(self, hybrid: HybridRedis) -> None:
        self._hybrid = hybrid
        self._remote = hybrid.remote.pipeline()
        self._local = hybrid.local.pipeline()
        self._order: list[tuple[str, int]] = []  # (side, index within that side's results)
        self._counts = {"remote": 0, "local": 0}
        self._merge_last_two: list[int] = []  # positions whose result pairs with the next one (split delete/exists)

    def _record(self, side: str, name: str, *args: Any, **kwargs: Any) -> None:
        pipe = self._remote if side == "remote" else self._local
        getattr(pipe, name)(*args, **kwargs)
        self._order.append((side, self._counts[side]))
        self._counts[side] += 1

    def __getattr__(self, name: str) -> Any:
        def call(key: Any, *args: Any, **kwargs: Any) -> HybridPipeline:
            side = "remote" if self._hybrid._for(key) is self._hybrid.remote else "local"
            self._record(side, name, key, *args, **kwargs)
            return self

        return call

    def _multi_key(self, name: str, keys: tuple[Any, ...]) -> HybridPipeline:
        """delete/exists may span both sides: split the keys and sum the two results."""
        remote, local = self._hybrid._split(keys)
        if remote and local:
            self._record("remote", name, *remote)
            self._record("local", name, *local)
            self._merge_last_two.append(len(self._order) - 2)
        elif remote:
            self._record("remote", name, *remote)
        elif local:
            self._record("local", name, *local)
        return self

    def delete(self, *keys: Any) -> HybridPipeline:
        return self._multi_key("delete", keys)

    def exists(self, *keys: Any) -> HybridPipeline:
        return self._multi_key("exists", keys)

    async def execute(self) -> list[Any]:
        remote_results = await self._remote.execute() if self._counts["remote"] else []
        local_results = await self._local.execute() if self._counts["local"] else []
        ordered: list[Any] = [
            (remote_results if side == "remote" else local_results)[idx] for side, idx in self._order
        ]
        for pos in sorted(self._merge_last_two, reverse=True):
            ordered[pos : pos + 2] = [int(ordered[pos] or 0) + int(ordered[pos + 1] or 0)]
        self._order = []
        self._counts = {"remote": 0, "local": 0}
        self._merge_last_two = []
        return ordered

    async def __aenter__(self) -> HybridPipeline:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None
