# push_server/run.py
import asyncio
import logging
import sys
import os
from asyncio import sleep
from datetime import datetime, timedelta

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    """每5分钟运行一次监测任务，但在凌晨3:00至6:10之间不执行"""
    while True:
        now = datetime.now(JAPAN_TZ)

        # 检查是否在凌晨3:00至6:10之间
        if 3 <= now.hour < 6:
            # 计算到早上6点的等待时间
            next_run = now.replace(hour=6, minute=10, second=0, microsecond=0)
            if now.hour == 6 and now.minute >= 10:
                next_run = next_run + timedelta(days=1)

            wait_seconds = (next_run - now).total_seconds()
            logging.info(
                f"当前处于休眠时间段 (凌晨3:00-6:10)，将在 {wait_seconds:.1f} 秒后恢复监测任务"
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


async def start_push_service():
    """启动推送服务"""
    # 启动推送池管理器
    await push_manager.start()
    logging.info("推送池管理器已启动")

    # 启动定时任务
    daily_task = asyncio.create_task(schedule_daily_push())
    logging.info("定时推送任务已设置")

    # 启动监测任务
    monitor_task = asyncio.create_task(schedule_monitor_task())
    logging.info("监测任务已设置")

    # 等待任务完成（实际上这些任务是无限循环，不会正常结束）
    try:
        await asyncio.gather(daily_task, monitor_task)
    except asyncio.CancelledError:
        logging.info("任务被取消")
    except Exception as e:
        logging.error(f"任务执行出错: {e}")
    finally:
        # 确保关闭推送池管理器
        await push_manager.stop()
        logging.info("推送池管理器已关闭")


if __name__ == "__main__":
    # 启动主程序
    try:
        asyncio.run(start_push_service())
    except KeyboardInterrupt:
        logging.info("程序被用户中断")
    except Exception as e:
        logging.error(f"程序运行出错: {e}")
