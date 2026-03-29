"""
TUTnext 统一启动入口
使用 asyncio.TaskGroup 并发启动 API 服务器和推送服务。

启动命令: uv run python -m tutnext
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta

import uvicorn

# 初始化配置和日志（必须在其他模块导入前执行）
from tutnext.config import JAPAN_TZ, settings


logger = logging.getLogger(__name__)


class _NoSignalServer(uvicorn.Server):
    """禁用 uvicorn 自带的信号处理，由父级统一管理，避免重复触发"""

    def install_signal_handlers(self) -> None:
        pass


async def start_api_server(stop_event: asyncio.Event):
    """启动 FastAPI/Uvicorn API 服务器"""
    config = uvicorn.Config(
        "tutnext.api.app:app",
        host="0.0.0.0",
        port=2053,
        reload=False,
        log_level="info",
    )
    server = _NoSignalServer(config)

    async def _watch_stop():
        await stop_event.wait()
        server.should_exit = True

    watcher = asyncio.ensure_future(_watch_stop())
    try:
        await server.serve()
    finally:
        watcher.cancel()


async def schedule_daily_push(push_manager):
    """每天晚上8:30 (JST) 运行推送任务"""
    from tutnext.services.push.sender import send_9pm_push_pool

    while True:
        now = datetime.now(JAPAN_TZ)
        target_time = now.replace(hour=20, minute=30, second=0, microsecond=0)

        if now >= target_time:
            target_time = target_time + timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        logger.info(
            f"计划在 {target_time.strftime('%Y-%m-%d %H:%M:%S')} (JST) 运行推送任务，等待 {wait_seconds:.1f} 秒"
        )
        await asyncio.sleep(wait_seconds)

        logger.info("开始执行推送任务...")
        try:
            await send_9pm_push_pool(push_manager)
            logger.info("推送任务完成")
        except Exception as e:
            logger.error(f"推送任务出错: {e}")


async def schedule_bus_scraper():
    """
    巴士时刻表自动更新任务：
      - 启动时立即运行一次（确保数据是最新的）
      - 此后每周一凌晨 3:00 (JST) 运行（巴士时刻表通常在学年初或学期初更新）
    """
    from tutnext.services.bus_scraper import update_bus_schedule

    # 启动时立即运行一次
    logger.info("巴士时刻表爬取器启动，立即执行初次更新...")
    try:
        updated = await update_bus_schedule()
        logger.info("初次巴士时刻表检查完成，数据已更新=%s", updated)
    except Exception as e:
        logger.error("初次巴士时刻表更新出错: %s", e)

    # 之后每周一凌晨 3:00 (JST) 运行
    while True:
        now = datetime.now(JAPAN_TZ)
        # 计算距下一个周一 03:00 的秒数
        days_until_monday = (7 - now.weekday()) % 7  # 0=今天就是周一
        next_monday = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if days_until_monday == 0 and now.hour >= 3:
            # 今天是周一且已过 03:00，等下周一
            days_until_monday = 7
        next_monday = next_monday + timedelta(days=days_until_monday)

        wait_seconds = (next_monday - now).total_seconds()
        logger.info(
            "巴士时刻表下次更新时间: %s (JST)，等待 %.1f 秒",
            next_monday.strftime("%Y-%m-%d %H:%M:%S"),
            wait_seconds,
        )
        await asyncio.sleep(wait_seconds)

        logger.info("开始执行每周巴士时刻表更新...")
        try:
            updated = await update_bus_schedule()
            logger.info("每周巴士时刻表更新完成，数据已更新=%s", updated)
        except Exception as e:
            logger.error("每周巴士时刻表更新出错: %s", e)


async def schedule_monitor_task(push_manager):
    """每5分钟运行一次监测任务，凌晨3:00至6:10之间不执行"""
    from tutnext.services.push.sender import monitor_task_push

    while True:
        now = datetime.now(JAPAN_TZ)

        # 凌晨3:00至6:10之间休眠
        if 3 <= now.hour < 6 or (now.hour == 6 and now.minute < 10):
            next_run = now.replace(hour=6, minute=10, second=0, microsecond=0)
            if now >= next_run:
                next_run = next_run + timedelta(days=1)
            wait_seconds = (next_run - now).total_seconds()
            logger.info(f"当前处于休眠时间段 (3:00-6:10)，将在 {wait_seconds:.1f} 秒后恢复")
            await asyncio.sleep(wait_seconds)
            continue

        logger.info("开始执行监测任务...")
        try:
            await monitor_task_push(push_manager)
            logger.info("监测任务完成")
        except Exception as e:
            logger.error(f"监测任务出错: {e}")

        await asyncio.sleep(300)  # 5分钟


async def run_all():
    """使用 TaskGroup 并发启动所有服务"""
    from tutnext.services.push.pool import PushPoolManager

    logger.info("TUTnext 服务启动中...")

    # 设置信号处理
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    # 仅存储 scheduler 任务，信号到来时只取消这些，让 uvicorn 通过 should_exit 优雅退出
    scheduler_tasks: list[asyncio.Task] = []

    def signal_handler():
        logger.info("收到停止信号，正在关闭...")
        stop_event.set()
        for task in scheduler_tasks:
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    push_manager = PushPoolManager()
    await push_manager.start()
    logger.info("推送池管理器已启动")

    try:
        async with asyncio.TaskGroup() as tg:
            # API 服务器（通过 stop_event → should_exit 优雅退出，不直接取消）
            tg.create_task(start_api_server(stop_event))
            # 每日推送任务 (8:30 PM JST)
            if settings.enable_daily_push:
                scheduler_tasks.append(tg.create_task(schedule_daily_push(push_manager)))
            else:
                logger.info("每日晚间推送已禁用 (ENABLE_DAILY_PUSH=false)")
            # 监测任务 (每5分钟)
            if settings.enable_monitor_push:
                scheduler_tasks.append(tg.create_task(schedule_monitor_task(push_manager)))
            else:
                logger.info("课题监测推送已禁用 (ENABLE_MONITOR_PUSH=false)")
            # 巴士时刻表自动更新 (启动时 + 每周一 3:00 JST)
            scheduler_tasks.append(tg.create_task(schedule_bus_scraper()))
    except* (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("程序被用户中断")
    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error(f"服务异常: {exc}")
    finally:
        await push_manager.stop()
        logger.info("推送池管理器已关闭")


def main():
    """程序主入口"""
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序运行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
