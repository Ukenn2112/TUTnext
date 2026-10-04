"""Redis-compatible key/value store backed by Cloudflare D1.

Cloudflare Workers cannot reach the LAN Redis that the server deployment uses,
so this module re-implements the subset of the ``redis.asyncio`` API that
TUTnext relies on (strings, hashes, sets, sorted sets, TTLs, ``scan_iter``,
pipelines and the one Lua "pop due member" script) on top of a few SQLite
tables in D1.

Differences from real Redis that callers must tolerate:

* values are returned as ``str`` (like ``decode_responses=True``);
* expiry is enforced on read and purged by :meth:`D1Redis.purge_expired`
  (called from the every-minute cron), not by a background thread;
* ``pipeline()`` batches statements into a single D1 ``batch`` call, which is
  atomic per batch.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

from tutnext import runtime

logger = logging.getLogger(__name__)

# D1 allows at most 100 bound parameters per statement.
_MAX_PARAMS = 90

_ALIVE = "(m.expires_at IS NULL OR m.expires_at > ?)"

Stmt = tuple[str, tuple[Any, ...]]
Extractor = Callable[[list[Any]], Any]
Command = tuple[list[Stmt], Extractor]


def _now() -> float:
    return time.time()


def _to_text(value: Any) -> str:
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8")
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _rows(result: Any) -> list[Any]:
    """Rows of one D1Result as a list of mapping-like objects."""
    if result is None:
        return []
    rows = result["results"] if isinstance(result, dict) else getattr(result, "results", None)
    return list(rows or [])


def _changes(result: Any) -> int:
    try:
        meta = result["meta"] if isinstance(result, dict) else result.meta
        return int(meta["changes"] if isinstance(meta, dict) else meta.changes)
    except Exception:  # noqa: BLE001
        return 0


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _purge_key_stmts(key: str, now: float) -> list[Stmt]:
    """Drop all data of *key* when its TTL has already elapsed (lazy expiry)."""
    cond = "key = ? AND (SELECT expires_at FROM kv_meta WHERE key = ?) <= ?"
    return [
        (f"DELETE FROM kv_string WHERE {cond}", (key, key, now)),
        (f"DELETE FROM kv_hash WHERE {cond}", (key, key, now)),
        (f"DELETE FROM kv_set WHERE {cond}", (key, key, now)),
        (f"DELETE FROM kv_zset WHERE {cond}", (key, key, now)),
        ("DELETE FROM kv_meta WHERE key = ? AND expires_at IS NOT NULL AND expires_at <= ?", (key, now)),
    ]


def _purge_keys_stmts(keys: list[str], now: float) -> list[Stmt]:
    """Lazy-expiry purge for several keys at once (one statement per table per chunk)."""
    stmts: list[Stmt] = []
    for chunk in _chunks(keys, _MAX_PARAMS - 1):
        marks = ",".join("?" * len(chunk))
        expired = f"SELECT key FROM kv_meta WHERE key IN ({marks}) AND expires_at IS NOT NULL AND expires_at <= ?"
        params = (*chunk, now)
        for table in ("kv_string", "kv_hash", "kv_set", "kv_zset"):
            stmts.append((f"DELETE FROM {table} WHERE key IN ({expired})", params))
        stmts.append((f"DELETE FROM kv_meta WHERE key IN ({marks}) AND expires_at IS NOT NULL AND expires_at <= ?", params))
    return stmts


def _ensure_meta_stmt(key: str) -> Stmt:
    return ("INSERT INTO kv_meta (key, expires_at) VALUES (?, NULL) ON CONFLICT(key) DO NOTHING", (key,))


# ---------------------------------------------------------------------------
# Command builders: each returns (statements, extractor(results_of_statements))
# ---------------------------------------------------------------------------


def _cmd_get(key: str) -> Command:
    stmts: list[Stmt] = [
        (
            "SELECT s.value AS value FROM kv_string s JOIN kv_meta m ON m.key = s.key "
            f"WHERE s.key = ? AND {_ALIVE}",
            (key, _now()),
        )
    ]

    def extract(results: list[Any]) -> str | None:
        rows = _rows(results[0])
        return rows[0]["value"] if rows else None

    return stmts, extract


def _cmd_set(key: str, value: Any, ex: int | None = None, px: int | None = None, keepttl: bool = False, purge: bool = True) -> Command:
    now = _now()
    expires_at: float | None = None
    if ex is not None:
        expires_at = now + float(ex)
    elif px is not None:
        expires_at = now + px / 1000.0
    stmts = _purge_key_stmts(key, now) if purge else []
    if keepttl:
        stmts.append(_ensure_meta_stmt(key))
    else:
        stmts.append(
            (
                "INSERT INTO kv_meta (key, expires_at) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET expires_at = excluded.expires_at",
                (key, expires_at),
            )
        )
    stmts.append(
        (
            "INSERT INTO kv_string (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, _to_text(value)),
        )
    )
    return stmts, lambda results: True


def _cmd_delete(keys: tuple[str, ...]) -> Command:
    stmts: list[Stmt] = []
    meta_indexes: list[int] = []
    for chunk in _chunks(list(keys), _MAX_PARAMS):
        marks = ",".join("?" * len(chunk))
        params = tuple(chunk)
        for table in ("kv_string", "kv_hash", "kv_set", "kv_zset"):
            stmts.append((f"DELETE FROM {table} WHERE key IN ({marks})", params))
        meta_indexes.append(len(stmts))
        stmts.append((f"DELETE FROM kv_meta WHERE key IN ({marks})", params))

    def extract(results: list[Any]) -> int:
        return sum(_changes(results[i]) for i in meta_indexes)

    return stmts, extract


def _cmd_exists(keys: tuple[str, ...]) -> Command:
    marks = ",".join("?" * len(keys))
    stmts: list[Stmt] = [
        (f"SELECT COUNT(*) AS c FROM kv_meta m WHERE key IN ({marks}) AND {_ALIVE}", (*keys, _now()))
    ]
    return stmts, lambda results: int(_rows(results[0])[0]["c"])


def _cmd_expire(key: str, seconds: int) -> Command:
    now = _now()
    stmts: list[Stmt] = [
        (f"UPDATE kv_meta AS m SET expires_at = ? WHERE key = ? AND {_ALIVE}", (now + float(seconds), key, now))
    ]
    return stmts, lambda results: _changes(results[0]) > 0


def _cmd_ttl(key: str) -> Command:
    stmts: list[Stmt] = [("SELECT expires_at FROM kv_meta WHERE key = ?", (key,))]

    def extract(results: list[Any]) -> int:
        rows = _rows(results[0])
        if not rows:
            return -2
        expires_at = rows[0]["expires_at"]
        if expires_at is None:
            return -1
        remaining = int(expires_at - _now())
        return remaining if remaining > 0 else -2

    return stmts, extract


def _cmd_incr(key: str, amount: int = 1, purge: bool = True) -> Command:
    now = _now()
    stmts = _purge_key_stmts(key, now) if purge else []
    stmts.append(_ensure_meta_stmt(key))
    stmts.append(
        (
            "INSERT INTO kv_string (key, value) VALUES (?, ?) "
            # CAST the bound amount too: from Workers a Python int arrives as a JS number and
            # D1 binds it as REAL, which made the stored text "2.0" and broke int() readers.
            "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(kv_string.value AS INTEGER) + CAST(? AS INTEGER) AS TEXT) "
            "RETURNING value",
            (key, str(amount), str(amount)),
        )
    )
    return stmts, lambda results: int(float(_rows(results[-1])[0]["value"]))


def _cmd_hset(key: str, field: str | None, value: Any, mapping: dict[str, Any] | None, purge: bool = True) -> Command:
    items: dict[str, str] = {}
    if field is not None:
        items[field] = _to_text(value)
    if mapping:
        items.update({k: _to_text(v) for k, v in mapping.items()})
    now = _now()
    stmts = _purge_key_stmts(key, now) if purge else []
    stmts.append(_ensure_meta_stmt(key))
    first = len(stmts)
    for f, v in items.items():
        stmts.append(
            (
                "INSERT INTO kv_hash (key, field, value) VALUES (?, ?, ?) "
                "ON CONFLICT(key, field) DO UPDATE SET value = excluded.value",
                (key, f, v),
            )
        )
    return stmts, lambda results: sum(_changes(r) for r in results[first:])


def _cmd_hget(key: str, field: str) -> Command:
    stmts: list[Stmt] = [
        (
            "SELECT h.value AS value FROM kv_hash h JOIN kv_meta m ON m.key = h.key "
            f"WHERE h.key = ? AND h.field = ? AND {_ALIVE}",
            (key, field, _now()),
        )
    ]

    def extract(results: list[Any]) -> str | None:
        rows = _rows(results[0])
        return rows[0]["value"] if rows else None

    return stmts, extract


def _cmd_hgetall(key: str) -> Command:
    stmts: list[Stmt] = [
        (
            "SELECT h.field AS field, h.value AS value FROM kv_hash h JOIN kv_meta m ON m.key = h.key "
            f"WHERE h.key = ? AND {_ALIVE}",
            (key, _now()),
        )
    ]
    return stmts, lambda results: {row["field"]: row["value"] for row in _rows(results[0])}


def _cmd_hdel(key: str, fields: tuple[str, ...]) -> Command:
    stmts: list[Stmt] = []
    for chunk in _chunks(list(fields), _MAX_PARAMS - 1):
        marks = ",".join("?" * len(chunk))
        stmts.append((f"DELETE FROM kv_hash WHERE key = ? AND field IN ({marks})", (key, *chunk)))
    return stmts, lambda results: sum(_changes(r) for r in results)


def _cmd_hlen(key: str) -> Command:
    stmts: list[Stmt] = [
        (
            f"SELECT COUNT(*) AS c FROM kv_hash h JOIN kv_meta m ON m.key = h.key WHERE h.key = ? AND {_ALIVE}",
            (key, _now()),
        )
    ]
    return stmts, lambda results: int(_rows(results[0])[0]["c"])


def _cmd_sadd(key: str, members: tuple[Any, ...], purge: bool = True) -> Command:
    now = _now()
    stmts = _purge_key_stmts(key, now) if purge else []
    stmts.append(_ensure_meta_stmt(key))
    first = len(stmts)
    for member in members:
        stmts.append(("INSERT OR IGNORE INTO kv_set (key, member) VALUES (?, ?)", (key, _to_text(member))))
    return stmts, lambda results: sum(_changes(r) for r in results[first:])


def _cmd_smembers(key: str) -> Command:
    stmts: list[Stmt] = [
        (
            f"SELECT s.member AS member FROM kv_set s JOIN kv_meta m ON m.key = s.key WHERE s.key = ? AND {_ALIVE}",
            (key, _now()),
        )
    ]
    return stmts, lambda results: {row["member"] for row in _rows(results[0])}


def _cmd_srem(key: str, members: tuple[Any, ...]) -> Command:
    stmts: list[Stmt] = []
    for chunk in _chunks([_to_text(m) for m in members], _MAX_PARAMS - 1):
        marks = ",".join("?" * len(chunk))
        stmts.append((f"DELETE FROM kv_set WHERE key = ? AND member IN ({marks})", (key, *chunk)))
    return stmts, lambda results: sum(_changes(r) for r in results)


def _cmd_zadd(key: str, mapping: dict[Any, float], purge: bool = True) -> Command:
    now = _now()
    stmts = _purge_key_stmts(key, now) if purge else []
    stmts.append(_ensure_meta_stmt(key))
    first = len(stmts)
    for member, score in mapping.items():
        stmts.append(
            (
                "INSERT INTO kv_zset (key, member, score) VALUES (?, ?, ?) "
                "ON CONFLICT(key, member) DO UPDATE SET score = excluded.score",
                (key, _to_text(member), float(score)),
            )
        )
    return stmts, lambda results: sum(_changes(r) for r in results[first:])


def _cmd_zcard(key: str) -> Command:
    stmts: list[Stmt] = [
        (
            f"SELECT COUNT(*) AS c FROM kv_zset z JOIN kv_meta m ON m.key = z.key WHERE z.key = ? AND {_ALIVE}",
            (key, _now()),
        )
    ]
    return stmts, lambda results: int(_rows(results[0])[0]["c"])


def _cmd_zrangebyscore(key: str, min_score: float, max_score: float, withscores: bool) -> Command:
    stmts: list[Stmt] = [
        (
            "SELECT z.member AS member, z.score AS score FROM kv_zset z JOIN kv_meta m ON m.key = z.key "
            f"WHERE z.key = ? AND z.score >= ? AND z.score <= ? AND {_ALIVE} ORDER BY z.score",
            (key, float(min_score), float(max_score), _now()),
        )
    ]

    def extract(results: list[Any]) -> list[Any]:
        rows = _rows(results[0])
        if withscores:
            return [(row["member"], float(row["score"])) for row in rows]
        return [row["member"] for row in rows]

    return stmts, extract


def _cmd_zrem(key: str, members: tuple[Any, ...]) -> Command:
    stmts: list[Stmt] = []
    for chunk in _chunks([_to_text(m) for m in members], _MAX_PARAMS - 1):
        marks = ",".join("?" * len(chunk))
        stmts.append((f"DELETE FROM kv_zset WHERE key = ? AND member IN ({marks})", (key, *chunk)))
    return stmts, lambda results: sum(_changes(r) for r in results)


def _cmd_zpop_due(key: str, max_score: float) -> Command:
    """Atomically remove and return the lowest-scored member with score <= max_score.

    Equivalent of the Lua ``ZRANGEBYSCORE ... LIMIT 0 1`` + ``ZREM`` script.
    """
    stmts: list[Stmt] = [
        (
            "DELETE FROM kv_zset WHERE rowid = ("
            "  SELECT z.rowid FROM kv_zset z JOIN kv_meta m ON m.key = z.key "
            f"  WHERE z.key = ? AND z.score <= ? AND {_ALIVE} ORDER BY z.score LIMIT 1"
            ") RETURNING member",
            (key, float(max_score), _now()),
        )
    ]

    def extract(results: list[Any]) -> str | None:
        rows = _rows(results[0])
        return rows[0]["member"] if rows else None

    return stmts, extract


def _cmd_scan(pattern: str) -> Command:
    stmts: list[Stmt] = [
        (f"SELECT key FROM kv_meta m WHERE key GLOB ? AND {_ALIVE} ORDER BY key", (pattern, _now()))
    ]
    return stmts, lambda results: [row["key"] for row in _rows(results[0])]


def _cmd_purge_expired() -> Command:
    now = _now()
    sub = "SELECT key FROM kv_meta WHERE expires_at IS NOT NULL AND expires_at <= ?"
    stmts: list[Stmt] = [
        (f"DELETE FROM kv_string WHERE key IN ({sub})", (now,)),
        (f"DELETE FROM kv_hash WHERE key IN ({sub})", (now,)),
        (f"DELETE FROM kv_set WHERE key IN ({sub})", (now,)),
        (f"DELETE FROM kv_zset WHERE key IN ({sub})", (now,)),
        ("DELETE FROM kv_meta WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)),
    ]
    return stmts, lambda results: _changes(results[-1])


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class D1Redis:
    """Async Redis-like client whose storage is a D1 database.

    ``executor`` is a binding name (Workers), or any object with
    ``await batch(stmts)`` (see :mod:`tutnext.core.d1client`).  ``lazy_purge``
    prepends per-key expiry cleanup to every write; the server's HTTP executor
    turns it off because the Worker's every-minute cron purges globally and each
    extra statement is a full HTTP round trip there.
    """

    def __init__(self, executor: Any = "DB", *, lazy_purge: bool | None = None) -> None:
        if isinstance(executor, str):
            from tutnext.core.d1client import BindingExecutor

            executor = BindingExecutor(executor)
        self._executor = executor
        if lazy_purge is None:
            lazy_purge = runtime.IS_WORKERS
        self._purge = lazy_purge

    # -- plumbing -----------------------------------------------------------

    async def _batch(self, stmts: list[Stmt]) -> list[Any]:
        if not stmts:
            return []
        return list(await self._executor.batch(stmts))

    async def _run(self, command: Command) -> Any:
        stmts, extract = command
        results = await self._batch(stmts)
        return extract(results)

    # -- strings ------------------------------------------------------------

    async def get(self, key: str) -> str | None:
        return await self._run(_cmd_get(key))

    async def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
    ) -> bool:
        if nx and await self.exists(key):
            return False
        if xx and not await self.exists(key):
            return False
        return await self._run(_cmd_set(key, value, ex=ex, px=px, keepttl=keepttl, purge=self._purge))

    async def setex(self, key: str, seconds: int, value: Any) -> bool:
        return await self.set(key, value, ex=seconds)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return await self._run(_cmd_delete(keys))

    async def exists(self, *keys: str) -> int:
        if not keys:
            return 0
        return await self._run(_cmd_exists(keys))

    async def expire(self, key: str, seconds: int) -> bool:
        return await self._run(_cmd_expire(key, int(seconds)))

    async def ttl(self, key: str) -> int:
        return await self._run(_cmd_ttl(key))

    async def incr(self, key: str, amount: int = 1) -> int:
        return await self._run(_cmd_incr(key, amount, purge=self._purge))

    async def incrby(self, key: str, amount: int = 1) -> int:
        return await self.incr(key, amount)

    # -- hashes -------------------------------------------------------------

    async def hset(self, key: str, field: str | None = None, value: Any = None, mapping: dict | None = None) -> int:
        return await self._run(_cmd_hset(key, field, value, mapping, purge=self._purge))

    async def hget(self, key: str, field: str) -> str | None:
        return await self._run(_cmd_hget(key, field))

    async def hgetall(self, key: str) -> dict[str, str]:
        return await self._run(_cmd_hgetall(key))

    async def hdel(self, key: str, *fields: str) -> int:
        if not fields:
            return 0
        return await self._run(_cmd_hdel(key, fields))

    async def hlen(self, key: str) -> int:
        return await self._run(_cmd_hlen(key))

    # -- sets ---------------------------------------------------------------

    async def sadd(self, key: str, *members: Any) -> int:
        if not members:
            return 0
        return await self._run(_cmd_sadd(key, members, purge=self._purge))

    async def smembers(self, key: str) -> set[str]:
        return await self._run(_cmd_smembers(key))

    async def srem(self, key: str, *members: Any) -> int:
        if not members:
            return 0
        return await self._run(_cmd_srem(key, members))

    # -- sorted sets --------------------------------------------------------

    async def zadd(self, key: str, mapping: dict[Any, float]) -> int:
        if not mapping:
            return 0
        return await self._run(_cmd_zadd(key, mapping, purge=self._purge))

    async def zcard(self, key: str) -> int:
        return await self._run(_cmd_zcard(key))

    async def zrangebyscore(self, key: str, min: Any, max: Any, withscores: bool = False) -> list[Any]:  # noqa: A002
        lo = float("-inf") if min in ("-inf", None) else float(min)
        hi = float("inf") if max in ("+inf", "inf", None) else float(max)
        return await self._run(_cmd_zrangebyscore(key, lo, hi, withscores))

    async def zrem(self, key: str, *members: Any) -> int:
        if not members:
            return 0
        return await self._run(_cmd_zrem(key, members))

    async def zpop_due(self, key: str, max_score: float) -> str | None:
        return await self._run(_cmd_zpop_due(key, max_score))

    # -- scripting / scanning ----------------------------------------------

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:  # noqa: A003
        if "ZRANGEBYSCORE" in script and "ZREM" in script and numkeys == 1:
            key = str(keys_and_args[0])
            max_score = float(keys_and_args[1])
            return await self.zpop_due(key, max_score)
        raise NotImplementedError("D1Redis.eval only supports the sorted-set pop-due script")

    async def scan_iter(self, match: str = "*", count: int | None = None) -> AsyncIterator[str]:
        for key in await self._run(_cmd_scan(match)):
            yield key

    async def keys(self, pattern: str = "*") -> list[str]:
        return await self._run(_cmd_scan(pattern))

    async def purge_expired(self) -> int:
        """Delete every key whose TTL has elapsed. Call periodically (cron)."""
        removed = await self._run(_cmd_purge_expired())
        if removed:
            logger.debug("D1Redis: purged %d expired keys", removed)
        return int(removed)

    # -- misc ---------------------------------------------------------------

    def pipeline(self, transaction: bool = True) -> D1Pipeline:
        return D1Pipeline(self)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None

    close = aclose


class D1Pipeline:
    """Collects commands and runs them as one D1 batch on :meth:`execute`."""

    def __init__(self, client: D1Redis) -> None:
        self._client = client
        self._commands: list[Command] = []
        self._written_keys: list[str] = []

    def _add(self, command: Command, *written: str) -> D1Pipeline:
        self._commands.append(command)
        for key in written:
            if key not in self._written_keys:
                self._written_keys.append(key)
        return self

    # Mirrors of the client API (synchronous, chainable, like redis-py pipelines)
    def get(self, key: str) -> D1Pipeline:
        return self._add(_cmd_get(key))

    def set(self, key: str, value: Any, ex: int | None = None, px: int | None = None, **_: Any) -> D1Pipeline:
        return self._add(_cmd_set(key, value, ex=ex, px=px, purge=False), key)

    def delete(self, *keys: str) -> D1Pipeline:
        return self._add(_cmd_delete(keys)) if keys else self

    def exists(self, *keys: str) -> D1Pipeline:
        return self._add(_cmd_exists(keys))

    def expire(self, key: str, seconds: int) -> D1Pipeline:
        return self._add(_cmd_expire(key, int(seconds)))

    def incr(self, key: str, amount: int = 1) -> D1Pipeline:
        return self._add(_cmd_incr(key, amount, purge=False), key)

    def hset(self, key: str, field: str | None = None, value: Any = None, mapping: dict | None = None) -> D1Pipeline:
        return self._add(_cmd_hset(key, field, value, mapping, purge=False), key)

    def hdel(self, key: str, *fields: str) -> D1Pipeline:
        return self._add(_cmd_hdel(key, fields)) if fields else self

    def sadd(self, key: str, *members: Any) -> D1Pipeline:
        return self._add(_cmd_sadd(key, members, purge=False), key) if members else self

    def srem(self, key: str, *members: Any) -> D1Pipeline:
        return self._add(_cmd_srem(key, members)) if members else self

    def zadd(self, key: str, mapping: dict[Any, float]) -> D1Pipeline:
        return self._add(_cmd_zadd(key, mapping, purge=False), key) if mapping else self

    def zrem(self, key: str, *members: Any) -> D1Pipeline:
        return self._add(_cmd_zrem(key, members)) if members else self

    async def execute(self) -> list[Any]:
        commands, self._commands = self._commands, []
        written, self._written_keys = self._written_keys, []
        # One lazy-expiry purge for every key written in this pipeline (instead of per command).
        all_stmts: list[Stmt] = _purge_keys_stmts(written, _now()) if (written and self._client._purge) else []
        spans: list[tuple[int, int]] = []
        for stmts, _ in commands:
            spans.append((len(all_stmts), len(all_stmts) + len(stmts)))
            all_stmts.extend(stmts)
        results = await self._client._batch(all_stmts)
        return [extract(results[start:end]) for (_, extract), (start, end) in zip(commands, spans, strict=True)]

    async def __aenter__(self) -> D1Pipeline:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._commands = []
        self._written_keys = []
