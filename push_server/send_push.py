# push_server/send_push.py
import asyncio
import aiosqlite
import logging

from datetime import datetime, timedelta
from app.services.gakuen_api import GakuenAPI, GakuenAPIError
from push_server.push_pool import PushPoolManager
from config import JAPAN_TZ, redis


# 轮询监测任务（课题 等）
async def monitor_task(
    push_manager: PushPoolManager, username, encryptedPassword, deviceToken
):
    """处理单个用户的监测任务"""
    try:
        # 为每个用户创建新的GakuenAPI实例
        gakuen = GakuenAPI("", "", "https://next.tama.ac.jp")
        try:
            max_retries = 3
            retry_count = 0
            while retry_count < max_retries:
                try:
                    kadai_list = await gakuen.get_user_kadai(
                        username, encryptedPassword
                    )
                    break  # 如果成功获取数据，跳出循环
                except GakuenAPIError as api_error:
                    retry_count += 1
                    if retry_count >= max_retries:
                        logging.error(
                            f"用户 {username} 获取课题数据失败，已达最大重试次数: {api_error}"
                        )
                        raise api_error
                    logging.warning(
                        f"用户 {username} 获取课题数据失败，重试第 {retry_count} 次: {api_error}"
                    )
                    await asyncio.sleep(2)  # 等待2秒后重试
            if not kadai_list:
                logging.info(f"用户 {username} 没有课题")
                return
            now_time = datetime.now(JAPAN_TZ)
            for kadai in kadai_list:
                kadai_due_time = datetime.strptime(
                    f"{kadai['dueDate']} {kadai['dueTime']}", "%Y-%m-%d %H:%M"
                ).astimezone(JAPAN_TZ)
                if kadai_due_time - now_time < timedelta(hours=1):
                    # 生成唯一的课题标识符
                    kadai_id = f"{username}:{kadai['courseId']}:{kadai['title']}{kadai.get('description', '')}"
                    # 检查是否已经推送过这个课题
                    if await redis.exists(f"kadai_notification:{kadai_id}"):
                        logging.info(f"课题 {kadai_id} 已经发送过通知，跳过")
                        continue
                    await push_manager.add_message_to_pool(
                        "realtime",
                        deviceToken,
                        "【注意】課題の締切が近づいています！",
                        f"授業「{kadai['courseName']}」の課題の締切が近づいています！\n締め切り: {kadai['dueDate']} {kadai['dueTime']}",
                    )
                    # 将课题标识符存储到Redis，设置过期时间为1小时
                    await redis.setex(
                        f"kadai_notification:{kadai_id}", 3600, "notified"
                    )
            logging.info(f"用户 {username} 的课题监测任务已完成")
        except Exception as e:
            logging.error(f"处理用户 {username} 时出错: {e}")
        finally:
            # 确保关闭API连接
            await gakuen.close()
    except Exception as e:
        logging.error(f"处理用户 {username} 时出错: {e}")


# 检查用户明日课程信息
async def check_tmrw_course_user_push(
    push_manager: PushPoolManager, username, encryptedPassword, deviceToken
):
    """处理单个用户的推送任务"""
    try:
        # 为每个用户创建新的GakuenAPI实例
        gakuen = GakuenAPI("", "", "https://next.tama.ac.jp")
        try:
            max_retries = 3
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
                        raise api_error
                    logging.warning(
                        f"用户 {username} 获取课程数据失败，重试第 {retry_count} 次: {api_error}"
                    )
                    await asyncio.sleep(2)  # 等待2秒后重试
            if all_day_events := data["all_day_events"]:
                for event in all_day_events:
                    if "SMIS:授業" in event["title"]:
                        await push_manager.add_message_to_pool(
                            "night_9pm",
                            deviceToken,
                            "明日の授業のお知らせ",
                            event["title"],
                        )
                        await push_manager.add_message_to_pool(
                            "morning_7am",
                            deviceToken,
                            "本日の授業のお知らせ",
                            event["title"],
                        )
                        continue
            if not data["time_table"]:
                logging.info(f"用户 {username} 没有课程数据")
                return

            for t in data["time_table"]:
                if "previous_room" not in t:
                    continue
                push_data = {
                    "updateType": "roomChange",
                    "name": t["name"],
                    "room": f"({t['room']})",
                }
                await push_manager.add_background_message_to_pool(
                    "realtime", deviceToken, push_data
                )
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
                await push_manager.add_message_to_pool(
                    pool_name,
                    deviceToken,
                    "【注意】次の授業の教室変更あり",
                    f"「{t['name']}」の教室が{t['room']}に変更されました。",
                )
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
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute("SELECT * FROM users")
        users = await cursor.fetchall()

        # 创建任务列表
        tasks = []
        for user in users:
            username, encryptedPassword, deviceToken = user
            # 为每个用户创建独立的任务
            task = asyncio.create_task(
                check_tmrw_course_user_push(
                    push_manager, username, encryptedPassword, deviceToken
                )
            )
            tasks.append(task)

        # 等待所有任务完成
        if tasks:
            logging.info(f"正在处理 {len(tasks)} 个用户的推送任务")
            await asyncio.gather(*tasks)
            logging.info("所有用户的推送任务处理完成")
        else:
            logging.info("没有用户需要处理推送任务")


async def monitor_task_push(push_manager):
    """为所有用户设置监测任务"""
    logging.info("处理监测任务")

    # 读取数据库
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute("SELECT * FROM users")
        users = await cursor.fetchall()

        # 创建任务列表
        tasks = []
        for user in users:
            username, encryptedPassword, deviceToken = user
            # 为每个用户创建独立的任务
            task = asyncio.create_task(
                monitor_task(push_manager, username, encryptedPassword, deviceToken)
            )
            tasks.append(task)

        # 等待所有任务完成
        if tasks:
            logging.info(f"正在处理 {len(tasks)} 个用户的监测任务")
            await asyncio.gather(*tasks)
            logging.info("所有用户的监测任务处理完成")
        else:
            logging.info("没有用户需要处理监测任务")
