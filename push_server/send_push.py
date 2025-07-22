# push_server/send_push.py
import json
import asyncio
import logging

from datetime import datetime, timedelta
from app.services.gakuen_api import GakuenAPI, GakuenAPIError
from push_server.push_pool import PushPoolManager
from app.database import db_manager
from app.services.google_classroom import classroom_api
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
            max_retries = 5
            retry_count = 0
            while retry_count < max_retries:
                try:
                    kadai_list = await gakuen.get_user_kadai(
                        username, encryptedPassword
                    )
                    if await db_manager.get_user_tokens(username):
                        # 如果用户有Google Classroom令牌，则获取Google Classroom课题
                        classroom_kadai_list = await classroom_api.get_user_assignments(
                            username
                        )
                        if classroom_kadai_list:
                            # 合并学园系统课题和Google Classroom课题
                            kadai_list.extend(classroom_kadai_list)
                    break  # 如果成功获取数据，跳出循环
                except Exception as api_error:
                    if "パスワードが正しくありません" in str(api_error):
                        logging.warning(
                            f"用户 {username} 的密码错误，无法获取课题数据: {api_error}"
                        )
                        await db_manager.delete_user(username)
                        return
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
            # 用户课题数量存在Redis中
            if await redis.exists(f"kadai_count:{username}"):
                old_kadai_count = int(await redis.get(f"kadai_count:{username}"))
                if len(kadai_list) == 0:
                    # 课题数量为 0 时，删除Redis中的课题数量，并推送后台消息
                    await redis.delete(f"kadai_count:{username}")
                    await push_manager.add_background_message_to_pool(
                        "realtime",
                        deviceToken,
                        {"updateType": "kaidaiNumChange", "num": 0},
                    )
                elif old_kadai_count < len(kadai_list):  # 课题数量增加
                    # 更新Redis中的课题数量，并推送消息
                    await redis.set(f"kadai_count:{username}", len(kadai_list))
                    await push_manager.add_background_message_to_pool(
                        "realtime",
                        deviceToken,
                        {"updateType": "kaidaiNumChange", "num": len(kadai_list)},
                    )
                    await push_manager.add_message_to_pool(
                        "realtime",
                        deviceToken,
                        "新しい課題が追加されました",
                        "詳しくはこのメッセージをタップしてください",
                        data={"toPage": "assignment"},
                    )
                elif old_kadai_count > len(kadai_list):  # 课题数量减少
                    # 更新Redis中的课题数量，并推送后台消息
                    await redis.set(f"kadai_count:{username}", len(kadai_list))
                    await push_manager.add_background_message_to_pool(
                        "realtime",
                        deviceToken,
                        {"updateType": "kaidaiNumChange", "num": len(kadai_list)},
                    )
            else:
                if (
                    len(kadai_list) > 0
                ):  # 课题数量大于 0 时，设置Redis中的课题数量，并推送后台消息
                    await redis.set(f"kadai_count:{username}", len(kadai_list))
                    await push_manager.add_background_message_to_pool(
                        "realtime",
                        deviceToken,
                        {"updateType": "kaidaiNumChange", "num": len(kadai_list)},
                    )
                    await push_manager.add_message_to_pool(
                        "realtime",
                        deviceToken,
                        "新しい課題が追加されました",
                        "詳しくはこのメッセージをタップしてください",
                        data={"toPage": "assignment"},
                    )
            if not kadai_list:
                logging.info(f"用户 {username} 没有课题")
                return
            await redis.set(
                f"{username}:kadai", json.dumps(kadai_list), ex=180
            )  # 缓存用户课题
            now_time = datetime.now(JAPAN_TZ)
            for kadai in kadai_list:
                naive_due_time = datetime.strptime(
                    f"{kadai['dueDate']} {kadai['dueTime']}", "%Y-%m-%d %H:%M"
                )
                kadai_due_time = JAPAN_TZ.localize(naive_due_time)
                if (kadai_due_time - now_time) < timedelta(hours=1):
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
                        interruption_level="time-sensitive",
                        data={"toPage": "assignment"},
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
        # 创建任务列表
        tasks = []
        for user in users:
            # 为每个用户创建独立的任务
            task = asyncio.create_task(
                check_tmrw_course_user_push(
                    push_manager,
                    user["username"],
                    user["encryptedpassword"],
                    user["devicetoken"],
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
    except Exception as e:
        logging.error(f"处理9点推送池任务时出错: {e}")


async def monitor_task_push(push_manager):
    """为所有用户设置监测任务"""
    logging.info("处理监测任务")

    # 读取数据库
    try:
        users = await db_manager.get_all_users()
        # 创建任务列表
        tasks = []
        for user in users:
            # 为每个用户创建独立的任务
            task = asyncio.create_task(
                monitor_task(
                    push_manager,
                    user["username"],
                    user["encryptedpassword"],
                    user["devicetoken"],
                )
            )
            tasks.append(task)

        # 等待所有任务完成
        if tasks:
            logging.info(f"正在处理 {len(tasks)} 个用户的监测任务")
            await asyncio.gather(*tasks)
            logging.info("所有用户的监测任务处理完成")
        else:
            logging.info("没有用户需要处理监测任务")
    except Exception as e:
        logging.error(f"处理监测任务时出错: {e}")
