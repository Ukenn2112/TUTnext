"""Cron dispatcher for Cloudflare Workers.

The server deployment runs the background jobs as asyncio loops in
``tutnext.__main__``.  On Workers a **single** Cron Trigger (``* * * * *``)
fires :func:`every_minute`, which runs the per-minute work and, by looking at
the clock, the less frequent jobs:

====================================  ===============================================
when (UTC)                            job
====================================  ===============================================
every minute                          push pools, Live Activity dispatcher (10 s ticks
                                      until ~50 s elapsed), pending retries, TTL purge
minute % 5 == 0 (not 3:00–6:10 JST)   assignment monitor (only if ENABLE_MONITOR_PUSH)
11:30                                 next-day schedule push (only if ENABLE_DAILY_PUSH)
Sunday 18:00 (Monday 03:00 JST)       bus timetable update
====================================  ===============================================

One trigger instead of four because Pyodide cannot enter a second
``scheduled()`` invocation while another one in the same isolate is suspended
("Cannot enter a promising task from inside another running promising task");
overlapping triggers poisoned the isolate for half an hour on 2026-10-02.
The legacy per-job cron strings are still accepted by :func:`run_cron`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from tutnext.config import JAPAN_TZ, settings

logger = logging.getLogger(__name__)

CRON_EVERY_MINUTE = "* * * * *"
CRON_MONITOR = "*/5 * * * *"
CRON_DAILY_PUSH = "30 11 * * *"
CRON_BUS = "0 18 * * SUN"

_LA_TICK_SECONDS = 10  # keeps the server's 10 s Live Activity granularity
_LA_BUDGET_SECONDS = 48  # stop ticking before the next minute's invocation starts


def _in_silent_window(now: datetime) -> bool:
    """3:00–6:10 JST: the school system's maintenance window."""
    return 3 <= now.hour < 6 or (now.hour == 6 and now.minute < 10)


async def _safe(name: str, coro) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001 - one failing job must not stop the others
        logger.error("cron job %s failed: %s", name, e, exc_info=True)


async def every_minute() -> None:
    from tutnext.config import redis
    from tutnext.services.push.live_activity import dispatch_live_activity_pushes, retry_pending_schedules
    from tutnext.services.push.pool import PushPoolManager

    started = time.monotonic()
    now_utc = datetime.now(UTC)

    # Scheduled push pools (07:00, 08:50, ... 21:15 JST) within ±60 s of now.
    await _safe("push_pools", PushPoolManager().process_due_pools())

    # Less frequent jobs, folded into this single trigger (see module docstring).
    if now_utc.minute % 5 == 0:
        await _safe("monitor", monitor())
    if (now_utc.hour, now_utc.minute) == (11, 30):
        await _safe("daily_push", daily_push())
    if now_utc.weekday() == 6 and (now_utc.hour, now_utc.minute) == (18, 0):
        await _safe("bus_update", bus_update())

    # Live Activity transitions: 10 s ticks until the time budget is spent.
    tick = 0
    while True:
        try:
            sent = await dispatch_live_activity_pushes()
            if sent:
                logger.info("LA dispatcher: sent %d pushes", sent)
        except Exception as e:  # noqa: BLE001
            logger.error("LA dispatcher error: %s", e)
        if tick == 0:
            await _safe("la_pending_retry", retry_pending_schedules())
            purge = getattr(redis, "purge_expired", None)
            if purge is not None:
                await _safe("purge_expired", purge())
        tick += 1
        if time.monotonic() - started + _LA_TICK_SECONDS > _LA_BUDGET_SECONDS:
            break
        await asyncio.sleep(_LA_TICK_SECONDS)


async def monitor() -> None:
    if not settings.enable_monitor_push:
        logger.info("课题监测推送已禁用 (ENABLE_MONITOR_PUSH=false)")
        return
    now = datetime.now(JAPAN_TZ)
    if _in_silent_window(now):
        logger.info("静默时段 (3:00-6:10 JST)，跳过监测")
        return
    from tutnext.services.push.pool import PushPoolManager
    from tutnext.services.push.sender import monitor_task_push

    logger.info("开始执行监测任务...")
    await monitor_task_push(PushPoolManager())
    logger.info("监测任务完成")


async def daily_push() -> None:
    if not settings.enable_daily_push:
        logger.info("每日晚间推送已禁用 (ENABLE_DAILY_PUSH=false)")
        return
    from tutnext.services.push.pool import PushPoolManager
    from tutnext.services.push.sender import send_9pm_push_pool

    logger.info("开始执行推送任务...")
    await send_9pm_push_pool(PushPoolManager())
    logger.info("推送任务完成")


async def bus_update() -> None:
    from tutnext.services.bus_scraper import update_bus_schedule

    updated = await update_bus_schedule()
    logger.info("巴士时刻表更新完成，数据已更新=%s", updated)


_JOBS = {
    CRON_EVERY_MINUTE: every_minute,
    CRON_MONITOR: monitor,
    CRON_DAILY_PUSH: daily_push,
    CRON_BUS: bus_update,
}


async def run_cron(cron: str) -> None:
    job = _JOBS.get(cron.strip())
    if job is None:
        logger.warning("unknown cron expression %r — nothing to run", cron)
        return
    logger.info("cron %s → %s", cron, job.__name__)
    await job()
