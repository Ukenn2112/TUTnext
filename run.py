# run.py
import uvicorn
import asyncio
import logging
from asyncio import sleep
from datetime import datetime, timedelta

# 导入推送相关模块
from config import JAPAN_TZ
from push_server.push_pool import PushPoolManager
from push_server.send_push import send_9pm_push_pool, monitor_task_push

# 创建全局推送池管理器
push_manager = PushPoolManager()


async def schedule_daily_push():
    """每天晚上8:30运行发送推送的函数"""
    while True:
        now = datetime.now(JAPAN_TZ)
        target_time = now.replace(hour=20, minute=30, second=0, microsecond=0)

        # 如果当前时间已经过了今天的目标时间，就等到明天
        if now >= target_time:
            target_time = target_time + timedelta(days=1)

        # 计算需要等待的秒数
        wait_seconds = (target_time - now).total_seconds()
        logging.info(
            f"计划在 {target_time.strftime('%Y-%m-%d %H:%M:%S')} (JST) 运行推送任务，等待 {wait_seconds:.1f} 秒"
        )

        # 等待到目标时间
        await sleep(wait_seconds)

        # 执行推送任务
        logging.info("开始执行推送任务...")
        try:
            await send_9pm_push_pool(push_manager)
            logging.info("推送任务完成")
        except Exception as e:
            logging.error(f"推送任务出错: {e}")


async def schedule_monitor_task():
    """每5分钟运行一次监测任务，但在凌晨3:00至6:00之间不执行"""
    while True:
        now = datetime.now(JAPAN_TZ)

        # 检查是否在凌晨3:00至6:00之间
        if 3 <= now.hour < 6:
            # 计算到早上6点的等待时间
            next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now.hour == 6 and now.minute >= 0:
                next_run = next_run + timedelta(days=1)

            wait_seconds = (next_run - now).total_seconds()
            logging.info(
                f"当前处于休眠时间段 (凌晨3:00-6:00)，将在 {wait_seconds:.1f} 秒后恢复监测任务"
            )
            await sleep(wait_seconds)
            continue

        # 执行监测任务
        logging.info("开始执行监测任务...")
        try:
            await monitor_task_push(push_manager)
            logging.info("监测任务完成")
        except Exception as e:
            logging.error(f"监测任务出错: {e}")

        # 等待5分钟
        await sleep(300)  # 5分钟 = 300秒


async def start_api_and_push():
    """启动API服务器和推送服务"""
    # 启动推送池管理器
    await push_manager.start()
    logging.info("推送池管理器已启动")

    # 启动定时任务
    asyncio.create_task(schedule_daily_push())
    logging.info("定时推送任务已设置")

    # 启动监测任务
    asyncio.create_task(schedule_monitor_task())
    logging.info("监测任务已设置")

    # 配置并启动API服务器
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=2053, reload=False)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    # 启动主程序
    try:
        asyncio.run(start_api_and_push())
    except KeyboardInterrupt:
        logging.info("程序被用户中断")
    except Exception as e:
        logging.error(f"程序运行出错: {e}")
    finally:
        # 确保关闭推送池管理器
        if "push_manager" in globals():
            asyncio.run(push_manager.stop())
            logging.info("推送池管理器已关闭")
