"""Assignment monitor and 20:30 push on Cloudflare Queues (Workers mode).

The server runs :class:`MonitorService` as one long asyncio loop; a Worker cannot (cron
invocations are capped at 30 s CPU).  Here the cron only *dispatches*: one message per
user on the ``tutnext-monitor`` queue, and the ``tutnext-monitor`` Worker consumes them.
The MonitorService layers map as follows:

* Layer 1 (concurrency)   → consumer ``max_batch_size`` × ``max_concurrency`` (wrangler.monitor.jsonc)
* Layer 2 (silent window) → checked by the cron before dispatch and again per message
* Layer 3 (backoff)       → ``should_check_user`` per message (skipped users cost one D1 read)
* Layer 4 (time spread)   → per-message ``delaySeconds`` across the monitor interval
* Layer 5 (classmates)    → a changed user enqueues its classmates with no delay (one level
  only, never on the baseline check); a per-user D1 lease keeps checks from overlapping

Messages carry only the username; credentials are read from D1 by the consumer.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from tutnext import runtime
from tutnext.config import JAPAN_TZ, settings

logger = logging.getLogger(__name__)

QUEUE_BINDING = "MONITOR_QUEUE"
KIND_MONITOR = "monitor"
KIND_CLASSMATE = "classmate"  # Layer 5: skip the backoff filter
KIND_DAILY = "daily"
_SEND_BATCH_MAX = 100  # Queues sendBatch limit
INFLIGHT_TTL = 180  # seconds; longest check (5 attempts with logins + sleeps) stays well below
DAILY_DONE_TTL = 36 * 3600


def _in_silent_window(now: datetime) -> bool:
    return 3 <= now.hour < 6 or (now.hour == 6 and now.minute < 10)


async def _send(messages: list[dict[str, Any]]) -> None:
    queue = runtime.binding(QUEUE_BINDING)
    for i in range(0, len(messages), _SEND_BATCH_MAX):
        await queue.sendBatch(messages[i : i + _SEND_BATCH_MAX])


def _spread(usernames: list[str], kind: str, window_seconds: float) -> list[dict[str, Any]]:
    step = window_seconds / max(len(usernames), 1)
    return [{"body": {"kind": kind, "username": u}, "delaySeconds": int(i * step)} for i, u in enumerate(usernames)]


async def dispatch_monitor_cycle() -> int:
    """Cron side of the monitor: enqueue every user, spread over the monitor interval."""
    from tutnext.core.database import db_manager

    users = await db_manager.get_all_users()
    names = [u["username"] for u in users]
    # Finish dispatching inside the cycle so consecutive cycles do not overlap.
    await _send(_spread(names, KIND_MONITOR, max(settings.monitor_interval_seconds - 30, 0)))
    logger.info("monitor: enqueued %d users", len(names))
    return len(names)


async def dispatch_daily_push() -> int:
    """Cron side of the 20:30 push: enqueue every user (gently spread over ~2 min)."""
    from tutnext.core.database import db_manager
    from tutnext.services.push.live_activity import schedule_push_to_start_for_unregistered_users

    users = await db_manager.get_all_users()
    names = [u["username"] for u in users]
    await _send(_spread(names, KIND_DAILY, 120))
    logger.info("daily push: enqueued %d users", len(names))
    try:
        extra = await schedule_push_to_start_for_unregistered_users(set(names))
        if extra:
            logger.info("Live Activity push-to-start: DB 外のユーザー %d 件を排程", extra)
    except Exception as e:  # noqa: BLE001
        logger.error("Live Activity push-to-start 追加排程でエラー: %s", e)
    return len(names)


async def _handle_monitor(service: Any, username: str, kind: str) -> None:
    from tutnext.config import redis
    from tutnext.core.d1lease import get_lease
    from tutnext.core.database import db_manager

    if _in_silent_window(datetime.now(JAPAN_TZ)):
        return
    if kind == KIND_MONITOR and not await service.should_check_user(username):
        return
    # One check per user at a time: a slow consumer must not overlap the next cycle's
    # message for the same user (both would read the old kadai_count and push twice).
    lease, owner = get_lease(), uuid.uuid4().hex
    if lease.enabled and not await lease.try_acquire(f"monitor:{username}", owner, ttl=INFLIGHT_TTL):
        return
    try:
        user = await db_manager.get_user(username)
        if not user:
            return  # deleted since dispatch (e.g. wrong password)
        # No stored count yet (first check on Workers: the server kept it in local Redis) →
        # this check only establishes the baseline; do not treat it as a class-wide change.
        first_check = not await redis.exists(f"kadai_count:{username}")
        changed = await service.check_single_user(user["username"], user["encryptedpassword"], user["devicetoken"])
        # Layer 5 only from regular checks: a classmate check never fans out again, as on the server.
        if changed and kind == KIND_MONITOR and not first_check:
            classmates = await service.find_classmates_to_check(username)
            if classmates:
                logger.info("[Layer 5] %s 变更 → 入队 %d 个同课程用户", username, len(classmates))
                await _send([{"body": {"kind": KIND_CLASSMATE, "username": u}} for u in classmates])
    finally:
        if lease.enabled:
            await lease.release(f"monitor:{username}", owner)


async def _handle_daily(push_manager: Any, username: str) -> None:
    from tutnext.config import redis
    from tutnext.core.database import db_manager
    from tutnext.services.push.sender import check_tmrw_course_user_push

    # Queues deliver at least once; a redelivered batch must not repeat the 休講 alert.
    done_key = f"daily_done:{username}:{datetime.now(JAPAN_TZ).date().isoformat()}"
    if await redis.exists(done_key):
        return
    user = await db_manager.get_user(username)
    if user:
        await check_tmrw_course_user_push(push_manager, user["username"], user["encryptedpassword"], user["devicetoken"])
        await redis.set(done_key, "1", ex=DAILY_DONE_TTL)


async def handle_batch(bodies: list[dict[str, Any]], ack: Callable[[int], None] | None = None) -> None:
    """Consumer side. Never raises; every per-user failure is logged/retried inside the checks.

    *ack(i)* acknowledges message *i* as soon as it is handled, so a batch redelivered after
    the invocation is killed only repeats the unfinished messages."""
    from tutnext.services.gakuen.session_manager import get_session_manager
    from tutnext.services.push.monitor import MonitorService
    from tutnext.services.push.pool import PushPoolManager

    await get_session_manager().cleanup()
    push_manager = PushPoolManager()
    service = MonitorService(push_manager)

    async def one(body: dict[str, Any]) -> None:
        kind, username = body.get("kind"), body.get("username")
        try:
            if not username:
                logger.warning("monitor queue: malformed message %r", body)
            elif kind in (KIND_MONITOR, KIND_CLASSMATE):
                await _handle_monitor(service, username, kind)
            elif kind == KIND_DAILY:
                await _handle_daily(push_manager, username)
            else:
                logger.warning("monitor queue: unknown kind %r", kind)
        except Exception as e:  # noqa: BLE001
            logger.error("monitor queue: %s %s failed: %s", kind, username, e, exc_info=True)

    async def one_and_ack(i: int, body: dict[str, Any]) -> None:
        await one(body)
        if ack is not None:
            try:
                ack(i)
            except Exception as e:  # noqa: BLE001 — the batch is acked on return anyway
                logger.warning("monitor queue: ack failed: %s", e)

    await asyncio.gather(*(one_and_ack(i, b) for i, b in enumerate(bodies)))
