# tutnext/services/push/sender.py
import asyncio
import logging
import sys
import aiohttp

from tutnext.services.gakuen.client import GakuenAPI, GakuenAPIError
from tutnext.services.push.pool import PushPoolManager
from tutnext.core.database import db_manager
from tutnext.config import redis, HTTP_PROXY, NOTIFICATION_API_URL

# API错误计数常量
API_ERROR_LIMIT = 50
API_ERROR_REDIS_KEY = "api_error_count"
API_ERROR_EXPIRY = 86400  # 24小时(秒)


async def record_api_error():
    """记录API错误并检查是否达到限制"""
    try:
        # 增加错误计数
        error_count = await redis.incr(API_ERROR_REDIS_KEY)
        
        # 如果是第一次记录错误,设置过期时间
        if error_count == 1:
            await redis.expire(API_ERROR_REDIS_KEY, API_ERROR_EXPIRY)
        
        logging.warning(f"API错误计数: {error_count}/{API_ERROR_LIMIT}")
        
        # 检查是否达到限制
        if error_count >= API_ERROR_LIMIT:
            logging.critical(f"API错误次数已达到限制({API_ERROR_LIMIT}次/天),程序即将终止")
            
            if NOTIFICATION_API_URL:
                # 发送通知
                try:
                    title = "TUTnext推送服务通知"
                    message = f"API错误次数已达到限制({API_ERROR_LIMIT}次/天),程序已终止"
                    notification_url = NOTIFICATION_API_URL.format(title=title, message=message)
                    async with aiohttp.ClientSession() as session:
                        async with session.get(notification_url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                            if response.status == 200:
                                logging.info("通知API调用成功")
                            else:
                                logging.warning(f"通知API调用失败,状态码: {response.status}")
                except Exception as notify_error:
                    logging.error(f"发送通知时发生异常: {notify_error}")
            
            # 清理Redis连接
            await redis.close()
            # 终止程序
            sys.exit(1)
    except Exception as e:
        logging.error(f"记录API错误时发生异常: {e}")


# 检查用户明日课程信息
async def check_tmrw_course_user_push(
    push_manager: PushPoolManager, username, encryptedPassword, deviceToken
):
    """处理单个用户的推送任务"""
    try:
        # 为每个用户创建新的GakuenAPI实例
        gakuen = GakuenAPI("", "", "https://next.tama.ac.jp", http_proxy=HTTP_PROXY)
        try:
            max_retries = 5
            retry_count = 0
            while retry_count < max_retries:
                try:
                    data = await gakuen.get_later_user_schedule(
                        username, encryptedPassword
                    )
                    break  # 如果成功获取数据，跳出循环
                except GakuenAPIError as api_error:
                    retry_count += 1
                    if retry_count >= max_retries:
                        logging.error(
                            f"用户 {username} 获取课程数据失败，已达最大重试次数: {api_error}"
                        )
                        # 只在重试全部失败后才记录API错误
                        await record_api_error()
                        raise api_error
                    logging.warning(
                        f"用户 {username} 获取课程数据失败，重试第 {retry_count} 次: {api_error}"
                    )
                    await asyncio.sleep(2)  # 等待2秒后重试
            # if all_day_events := data["all_day_events"]:
            #     for event in all_day_events:
            #         if "SMIS:授業" in event["title"]:
            #             await push_manager.add_message_to_pool(
            #                 "night_9pm",
            #                 deviceToken,
            #                 "明日の授業のお知らせ",
            #                 event["title"],
            #             )
            #             await push_manager.add_message_to_pool(
            #                 "morning_7am",
            #                 deviceToken,
            #                 "本日の授業のお知らせ",
            #                 event["title"],
            #             )
            #             continue
            if not data["time_table"]:
                logging.info(f"用户 {username} 没有课程数据")
                return

            has_changes = False
            for t in data["time_table"]:
                if t["lesson_num"] == 1:
                    pool_name = "morning_8_50am"
                elif t["lesson_num"] == 2:
                    pool_name = "morning_10_30am"
                elif t["lesson_num"] == 3:
                    pool_name = "lunch_12_50pm"
                elif t["lesson_num"] == 4:
                    pool_name = "afternoon_2_30pm"
                elif t["lesson_num"] == 5:
                    pool_name = "afternoon_4_10pm"
                elif t["lesson_num"] == 6:
                    pool_name = "evening_5_50pm"
                elif t["lesson_num"] == 7:
                    pool_name = "evening_7_30pm"
                else:
                    pool_name = "realtime"
                if "special_tags" in t:
                    if "休講" in t["special_tags"]:
                        await push_manager.add_message_to_pool(
                            "realtime",
                            deviceToken,
                            "明日の授業の休講お知らせ",
                            f"明日の「{t['name']}」授業は休講となります。",
                            data={"toPage": "timetable"},
                        )
                        await push_manager.add_message_to_pool(
                            pool_name,
                            deviceToken,
                            "【注意】次の授業の休講お知らせ",
                            f"次の「{t['name']}」授業は休講となります。",
                            interruption_level="time-sensitive",
                            data={"toPage": "timetable"},
                        )
                        has_changes = True
                        continue
                elif "previous_room" not in t:
                    continue
                t["room"] = t["room"].replace("教室", "")
                push_data = {
                    "updateType": "roomChange",
                    "name": t["name"],
                    "room": f"({t['room']})",
                }
                await push_manager.add_background_message_to_pool(
                    "realtime", deviceToken, push_data
                )
                await push_manager.add_message_to_pool(
                    pool_name,
                    deviceToken,
                    "【注意】次の授業の教室変更あり",
                    f"「{t['name']}」の教室が{t['room']}に変更されました。",
                    interruption_level="time-sensitive",
                    data={"toPage": "timetable"},
                )
                has_changes = True
            # 检测到课程变更（休講或教室変更）时，清除该用户的日程缓存，确保下次请求返回最新数据
            if has_changes:
                await redis.delete(f"schedule:ical:{username}")
                logging.info(f"用户 {username} 的日程缓存已清除")
            logging.info(f"用户 {username} 的推送教室变更消息已添加到推送池")
        except Exception as e:
            logging.error(f"处理用户 {username} 时出错: {e}")
        finally:
            # 确保关闭API连接
            await gakuen.close()
    except Exception as e:
        logging.error(f"处理用户 {username} 时出错: {e}")


async def send_9pm_push_pool(push_manager):
    """为所有用户设置9点的推送消息"""
    logging.info("开始处理9点推送池任务")

    # 读取数据库
    try:
        users = await db_manager.get_all_users()

        if not users:
            logging.info("没有用户需要处理推送任务")
            return

        semaphore = asyncio.Semaphore(5)

        async def _limited_check(user):
            async with semaphore:
                await check_tmrw_course_user_push(
                    push_manager,
                    user["username"],
                    user["encryptedpassword"],
                    user["devicetoken"],
                )

        tasks = [asyncio.create_task(_limited_check(user)) for user in users]
        logging.info(f"正在处理 {len(tasks)} 个用户的推送任务")
        await asyncio.gather(*tasks)
        logging.info("所有用户的推送任务处理完成")
    except Exception as e:
        logging.error(f"处理9点推送池任务时出错: {e}")


async def monitor_task_push(push_manager: PushPoolManager):
    """委托 MonitorService 执行限速监测任务。

    不再直接 gather 所有用户——由 MonitorService 的四层防护机制控制并发和退避。
    """
    from tutnext.services.push.monitor import MonitorService

    service = MonitorService(push_manager)
    await service.run_monitoring_cycle()
