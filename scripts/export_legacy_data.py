"""Export the server deployment's PostgreSQL + Redis state as SQL for Cloudflare D1.

Run this ON THE SERVER (or any host that can reach the LAN PostgreSQL and Redis),
inside the project venv so the server extras are installed::

    uv run --extra server python scripts/export_legacy_data.py --out tutnext_export.sql

Then import into D1 from a machine with the Cloudflare CLI::

    npx wrangler d1 execute tutnext --remote --file tutnext_export.sql
    # or: cf d1 execute ... (see docs/cloudflare-python-workers.md)

What is exported
----------------
* ``users``        — username / encryptedpassword / devicetoken (push registrations)
* ``user_tokens``  — Google Classroom OAuth tokens
* Redis keys that must survive the move (everything else is cache and is rebuilt):
    - ``la:pts:*``       push-to-start tokens (30 d TTL)      → kv_string + kv_meta
    - ``la:pts:pw:*``    fallback encrypted passwords          → kv_string + kv_meta
    - ``room:*``         classroom cache (1 week TTL)          → kv_string + kv_meta
    - ``kadai_count:*``  assignment counters (so the first Workers monitor cycle does
                         not push "num changed" to everyone)   → kv_string + kv_meta

Credentials are read from the same ``.env`` the server uses (DATABASE_URL, REDIS_URL).
Plaintext secrets are written to the SQL file: keep it private and delete it after import.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

_KEY_PATTERNS = ("la:pts:*", "room:*", "kadai_count:*")


def q(value: str | bytes | None) -> str:
    """SQL-quote a text value."""
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return "'" + value.replace("'", "''") + "'"


async def export_postgres(dsn: str, out: list[str]) -> tuple[int, int]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        users = await conn.fetch("SELECT username, encryptedpassword, devicetoken FROM users")
        for row in users:
            if not (row["username"] and row["encryptedpassword"] and row["devicetoken"]):
                continue
            out.append(
                "INSERT INTO users (username, encryptedpassword, devicetoken) VALUES "
                f"({q(row['username'])}, {q(row['encryptedpassword'])}, {q(row['devicetoken'])}) "
                "ON CONFLICT(username) DO UPDATE SET encryptedpassword = excluded.encryptedpassword, "
                "devicetoken = excluded.devicetoken;"
            )
        tokens = await conn.fetch(
            "SELECT username, access_token, refresh_token, created_at, updated_at FROM user_tokens"
        )
        for row in tokens:
            created = row["created_at"].isoformat() if row["created_at"] else None
            updated = row["updated_at"].isoformat() if row["updated_at"] else None
            out.append(
                "INSERT INTO user_tokens (username, access_token, refresh_token, created_at, updated_at) VALUES "
                f"({q(row['username'])}, {q(row['access_token'])}, {q(row['refresh_token'])}, "
                f"{q(created) if created else 'strftime(\'%Y-%m-%dT%H:%M:%fZ\', \'now\')'}, "
                f"{q(updated) if updated else 'strftime(\'%Y-%m-%dT%H:%M:%fZ\', \'now\')'}) "
                "ON CONFLICT(username) DO UPDATE SET access_token = excluded.access_token, "
                "refresh_token = excluded.refresh_token, updated_at = excluded.updated_at;"
            )
        return len(users), len(tokens)
    finally:
        await conn.close()


async def export_redis(url: str, out: list[str]) -> int:
    from redis import asyncio as aioredis

    client = aioredis.from_url(url)
    count = 0
    now = time.time()
    try:
        for pattern in _KEY_PATTERNS:
            async for raw_key in client.scan_iter(pattern):
                key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                if await client.type(key) != b"string":
                    continue
                value = await client.get(key)
                if value is None:
                    continue
                ttl = await client.ttl(key)
                expires = "NULL" if ttl is None or ttl < 0 else repr(now + ttl)
                out.append(
                    f"INSERT INTO kv_meta (key, expires_at) VALUES ({q(key)}, {expires}) "
                    "ON CONFLICT(key) DO UPDATE SET expires_at = excluded.expires_at;"
                )
                out.append(
                    f"INSERT INTO kv_string (key, value) VALUES ({q(key)}, {q(value)}) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value;"
                )
                count += 1
        return count
    finally:
        await client.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="tutnext_export.sql", help="output SQL file")
    parser.add_argument("--env", default=".env", help=".env file with DATABASE_URL / REDIS_URL")
    parser.add_argument("--skip-redis", action="store_true")
    args = parser.parse_args()

    if load_dotenv is not None and Path(args.env).exists():
        load_dotenv(args.env)
    dsn = os.environ.get("DATABASE_URL")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    out: list[str] = ["-- TUTnext legacy export → Cloudflare D1", "-- generated " + time.strftime("%Y-%m-%dT%H:%M:%S")]
    users, tokens = await export_postgres(dsn, out)
    keys = 0 if args.skip_redis else await export_redis(redis_url, out)
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {users} users, {tokens} oauth token rows, {keys} redis keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
