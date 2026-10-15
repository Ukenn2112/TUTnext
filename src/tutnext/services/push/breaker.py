"""Circuit breaker for the assignment monitor (Workers).

When T-NEXT fails for nearly everyone (its assignment endpoint answered HTTP 500 for all
students for ~21 h on 2026-10-10/11), checking every user just multiplies failed logins
(5 attempts each) and slows the queue. The breaker:

* opens when, over the last two 5-minute buckets, school-side failures are
  >= OPEN_MIN_FAILURES and successes < OPEN_MAX_OK_RATIO of them (credential errors
  are not counted: they are the student's problem, not an outage);
* while open, the cron enqueues no regular checks; every PROBE_EVERY_MINUTES it sends
  PROBES_PER_ROUND probes (read-only assignment fetches for random users — never a fixed
  user, whose password may have changed meanwhile and keep the breaker shut forever);
* on the first successful probe it closes, re-checks every user at once (ignoring
  backoff) and re-sends tonight's 20:30 push to whoever missed it if it is not too
  late — "recover as fast as possible";
* notifies the operator (NOTIFICATION_API_URL) when it opens and when it recovers.

State lives in D1 through the shared redis facade so cron, consumer and API agree.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from tutnext.config import JAPAN_TZ, redis

logger = logging.getLogger(__name__)

STATE_KEY = "monitor:breaker"
LAST_OK_USER_KEY = "monitor:health:last_ok_user"
BUCKET_SECONDS = 300
OPEN_MIN_FAILURES = 8
OPEN_MAX_OK_RATIO = 0.2
PROBE_EVERY_MINUTES = 2
PROBES_PER_ROUND = 2
CATCHUP_SPREAD_SECONDS = 180
DAILY_CATCHUP_UNTIL = (23, 30)  # JST: after this a 20:30 push re-sent would be too late


def _bucket(ts: float) -> int:
    return int(ts // BUCKET_SECONDS)


def _decode(v: Any) -> str | None:
    if v is None:
        return None
    return v.decode() if isinstance(v, bytes) else str(v)


async def record(ok: bool, username: str | None = None) -> None:
    """Called by MonitorService after each check: ok=True on success, False when T-NEXT
    kept failing after all retries (not for wrong passwords / permission errors)."""
    try:
        key = f"monitor:health:{'ok' if ok else 'fail'}:{_bucket(time.time())}"
        await redis.incr(key)
        await redis.expire(key, BUCKET_SECONDS * 6)
        if ok and username:
            await redis.set(LAST_OK_USER_KEY, username, ex=7 * 86400)
    except Exception as e:  # noqa: BLE001 — health bookkeeping must never break a check
        logger.warning("breaker: could not record health: %s", e)


async def state() -> dict[str, Any] | None:
    raw = _decode(await redis.get(STATE_KEY))
    return json.loads(raw) if raw else None


async def is_open() -> bool:
    try:
        return await state() is not None
    except Exception:  # noqa: BLE001 — unknown state: behave as closed
        return False


async def _recent_counts(now: float) -> tuple[int, int]:
    ok = fail = 0
    for b in (_bucket(now), _bucket(now) - 1):
        ok += int(float(_decode(await redis.get(f"monitor:health:ok:{b}")) or 0))
        fail += int(float(_decode(await redis.get(f"monitor:health:fail:{b}")) or 0))
    return ok, fail


async def evaluate(now_utc: datetime) -> None:
    """Cron, every minute (outside the silent window): open the breaker or send a probe."""
    from tutnext.services.push import monitor_queue as mq

    current = await state()
    if current is None:
        ok, fail = await _recent_counts(now_utc.timestamp())
        if fail >= OPEN_MIN_FAILURES and ok < fail * OPEN_MAX_OK_RATIO:
            opened = {"opened_at": now_utc.timestamp(), "failures": fail, "ok": ok}
            await redis.set(STATE_KEY, json.dumps(opened))
            logger.warning("breaker: OPEN (T-NEXT failures=%d ok=%d in 10 min)", fail, ok)
            await notify("TUTnext 監視を一時停止",
                         f"学校システムの課題取得が連続で失敗しています（10分間に失敗{fail}件・成功{ok}件）。"
                         f"監視を止め、{PROBE_EVERY_MINUTES}分ごとに復旧を確認します。")
        return
    if now_utc.minute % PROBE_EVERY_MINUTES == 0:
        import random

        from tutnext.core.database import db_manager

        names = [u["username"] for u in await db_manager.get_all_users()]
        last_ok = _decode(await redis.get(LAST_OK_USER_KEY))
        picks = random.sample(names, min(PROBES_PER_ROUND, len(names)))
        if last_ok in names and last_ok not in picks:
            picks[0] = last_ok  # likely-good first, plus random ones in case it went stale
        if picks:
            await mq._send([{"body": {"kind": mq.KIND_PROBE, "username": u}} for u in picks])
            logger.info("breaker: probes enqueued (%s)", ", ".join(picks))


_CREDENTIAL_MARKERS = ("パスワードが正しくありません", "SESSION_BUSY", "他端末で同時に実行")


async def probe(username: str) -> bool:
    """Consumer: one read-only assignment fetch. True only if T-NEXT answered normally.

    A credential or lock problem says nothing about the outage (that student's issue),
    so it counts as "no answer" — the next round probes other, random users.
    """
    from tutnext.core.database import db_manager
    from tutnext.services.gakuen.errors import GakuenPermissionError
    from tutnext.services.gakuen.session_manager import get_session_manager

    user = await db_manager.get_user(username)
    if not user:
        return False
    try:
        async with get_session_manager().acquire(username, user["encryptedpassword"]) as gakuen:
            await gakuen.get_user_kadai(username, user["encryptedpassword"], skip_login=True)
        return True
    except GakuenPermissionError as e:
        logger.info("breaker: probe user %s unusable (%s); other users next round", username, e)
        return False
    except Exception as e:  # noqa: BLE001
        text = f"{getattr(e, 'error_code', '')} {e}"
        if any(m in text for m in _CREDENTIAL_MARKERS):
            logger.info("breaker: probe user %s unusable (%s); other users next round", username, e)
        else:
            logger.info("breaker: probe with %s failed, T-NEXT still down: %s", username, e)
        return False


async def recover() -> None:
    """First successful probe: re-check everyone now, catch up tonight's push, then close.

    Concurrent probes are serialised by a lease; if queuing the catch-up fails the lease is
    released and the breaker stays open, so the next successful probe simply retries.
    """
    from tutnext.core.d1lease import get_lease
    from tutnext.core.database import db_manager
    from tutnext.services.push import monitor_queue as mq

    current = await state()
    if current is None:
        return
    lease, owner = get_lease(), uuid.uuid4().hex
    name = f"breaker:recover:{current['opened_at']}"
    if lease.enabled and not await lease.try_acquire(name, owner, ttl=300):
        return  # another probe is recovering right now
    try:
        if await state() is None:
            return  # recovered meanwhile
        minutes = int((time.time() - float(current["opened_at"])) / 60)
        names = [u["username"] for u in await db_manager.get_all_users()]
        catchup = mq._spread(names, mq.KIND_CATCHUP, CATCHUP_SPREAD_SECONDS)
        await mq._send(catchup)
        # Marked like regular messages so the next cycle does not enqueue them a second time.
        await mq._mark_queued(catchup, time.time())
        daily = await _catch_up_daily(names)
        await redis.delete(STATE_KEY)  # closed only once the catch-up is queued
    except Exception:
        if lease.enabled:
            await lease.release(name, owner)
        raise
    logger.warning("breaker: CLOSED after %d min; re-checking %d users, daily catch-up %d", minutes, len(names), daily)
    extra = f"今夜の通知を{daily}人に再送します。" if daily else ""
    await notify("TUTnext 復旧",
                 f"学校システムが復旧しました（停止{minutes}分）。全{len(names)}人の課題を今すぐ再確認します。{extra}")


async def _catch_up_daily(names: list[str]) -> int:
    """Re-send tonight's push to users without today's daily_done marker, if 20:30 has
    passed and it is not too late in the evening."""
    from tutnext.services.push import monitor_queue as mq

    now = datetime.now(JAPAN_TZ)
    if not ((20, 30) <= (now.hour, now.minute) < DAILY_CATCHUP_UNTIL):
        return 0
    today = now.date().isoformat()
    missing = [u for u in names if not await redis.exists(f"daily_done:{u}:{today}")]
    if missing:
        await mq._send(mq._spread(missing, mq.KIND_DAILY, CATCHUP_SPREAD_SECONDS))
    return len(missing)


async def notify(title: str, message: str) -> None:
    from tutnext.services.push.sender import notify_operator

    await notify_operator(title, message)
