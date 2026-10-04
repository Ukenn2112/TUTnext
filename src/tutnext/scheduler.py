"""Cron dispatcher for Cloudflare Workers.

The server deployment runs the background jobs as asyncio loops in
``tutnext.__main__``.  On Workers a **single** Cron Trigger (``* * * * *``)
fires :func:`every_minute`, which runs the per-minute work and, by looking at
the clock, the less frequent jobs:

====================================  ===============================================
when (UTC)                            job
====================================  ===============================================
every minute                          push pools, Live Activity dispatcher (one pass),
                                      pending retries, TTL purge
minute % 5 == 0 (not 3:00–6:10 JST)   enqueue assignment monitor (only if ENABLE_MONITOR_PUSH)
11:30                                 enqueue next-day push (only if ENABLE_DAILY_PUSH)
Sunday 18:00 (Monday 03:00 JST)       bus timetable update
====================================  ===============================================

One trigger instead of four because Pyodide cannot enter a second
``scheduled()`` invocation while another one in the same isolate is suspended
("Cannot enter a promising task from inside another running promising task");
overlapping triggers poisoned the isolate for half an hour on 2026-10-02.
The legacy per-job cron strings are still accepted by :func:`run_cron`.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from tutnext.config import JAPAN_TZ, settings

logger = logging.getLogger(__name__)

CRON_EVERY_MINUTE = "* * * * *"
CRON_MONITOR = "*/5 * * * *"
CRON_DAILY_PUSH = "30 11 * * *"
CRON_BUS = "0 18 * * SUN"



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

    # Live Activity transitions: ONE pass per invocation. Keeping the invocation short matters
    # more than the server's 10 s granularity: while a Python invocation is suspended in this
    # isolate, any other event entering Python fails with Pyodide's "Cannot enter a promising
    # task" SystemError, so a 48 s loop here made ~1/3 of API requests fail (2026-10-03).
    try:
        sent = await dispatch_live_activity_pushes()
        if sent:
            logger.info("LA dispatcher: sent %d pushes", sent)
    except Exception as e:  # noqa: BLE001
        logger.error("LA dispatcher error: %s", e)
    await _safe("la_pending_retry", retry_pending_schedules())
    await _safe("rate_counter_gc", _purge_rate_counters())
    purge = getattr(redis, "purge_expired", None)
    if purge is not None:
        await _safe("purge_expired", purge())
    logger.info("every_minute done in %.1fs", time.monotonic() - started)


async def _purge_rate_counters() -> None:
    """Drop tutnext-gateway's per-student counters older than an hour (rate_counters table)."""
    from tutnext.core.d1client import BindingExecutor

    await BindingExecutor("DB").batch([("DELETE FROM rate_counters WHERE window < ?", (int(time.time() // 60) - 60,))])


async def monitor() -> None:
    if not settings.enable_monitor_push:
        logger.info("课题监测推送已禁用 (ENABLE_MONITOR_PUSH=false)")
        return
    now = datetime.now(JAPAN_TZ)
    if _in_silent_window(now):
        logger.info("静默时段 (3:00-6:10 JST)，跳过监测")
        return
    # Workers: dispatch one queue message per user; the tutnext-monitor Worker does the checks.
    from tutnext.services.push.monitor_queue import dispatch_monitor_cycle

    await dispatch_monitor_cycle()


async def daily_push() -> None:
    if not settings.enable_daily_push:
        logger.info("每日晚间推送已禁用 (ENABLE_DAILY_PUSH=false)")
        return
    from tutnext.services.push.monitor_queue import dispatch_daily_push

    await dispatch_daily_push()


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
