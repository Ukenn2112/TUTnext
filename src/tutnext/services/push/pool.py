"""
推送通知池管理
=============
系统维护 10 个时间槽推送池，对应大学课程时间:
- realtime: 实时推送（教室变更、休讲通知立即发送）
- morning_8_50am: 第1限前（09:00 开始）
- morning_10_30am: 第2限前（10:40 开始）
- lunch_12_50pm: 第3限前（13:00 开始）
- afternoon_2_30pm: 第4限前（14:40 开始）
- afternoon_4_10pm: 第5限前（16:20 开始）
- evening_5_50pm: 第6限前（18:00 开始）
- evening_7_30pm: 第7限前（19:40 开始）
- night_9pm: 次日日程推送（21:15 发送）
- morning_7am: 当日提醒（07:00 发送）

工作流程:
1. 每晚 20:30 触发 check_tmrw_course_user_push，检查每个用户的明日课程
2. 检测到教室变更或休讲时，将消息加入对应时间槽的推送池
3. 调度器每分钟检查一次，到达预定时间时发送该池中的所有消息
4. 通过 APNs 单例客户端发送到 iOS 设备
"""
# tutnext/services/push/pool.py
import asyncio
import logging
import json

from uuid import uuid4
from aioapns import NotificationRequest, PushType
from datetime import datetime, time
from typing import Awaitable, Dict, Optional, Any, Literal, cast

from tutnext.config import JAPAN_TZ, redis
from tutnext.services.push.apns_client import get_apns_client

# 定义消息类型
MessageType = Literal["alert", "background"]


class PushPool:
    def __init__(self, name: str, scheduled_time: Optional[time] = None):
        self.name = name
        self.scheduled_time = scheduled_time  # 为None表示实时推送池
        self.redis_key = f"push_pool:{name}"  # Redis中存储消息的键

    async def add_message(
        self,
        device_token: str,
        data: Optional[Dict] = None,
        message_type: MessageType = "alert",
        title: Optional[str] = None,
        body: Optional[str] = None,
        interruption_level: Optional[str] = None,
    ):
        """添加消息到推送池

        Args:
            device_token: 设备令牌
            data: 附加数据
            message_type: 消息类型，"alert"为普通通知，"background"为后台通知
            title: 通知标题（仅用于alert类型）
            body: 通知内容（仅用于alert类型）
        """
        message: Dict[str, Any] = {
            "device_token": device_token,
            "data": data or {},
            "message_type": message_type,
            "created_at": datetime.now(JAPAN_TZ).isoformat(),
        }

        # 对于普通通知类型，添加标题和内容
        if message_type == "alert":
            if title is None or body is None:
                raise ValueError("alert类型的消息必须提供title和body")
            message["title"] = title
            message["body"] = body
            message["interruption-level"] = interruption_level

        # 将消息序列化为JSON并存储在Redis中
        message_id = str(uuid4())
        await cast(Awaitable[int], redis.hset(self.redis_key, message_id, json.dumps(message)))

        # 如果是实时推送池，立即发送
        if self.scheduled_time is None:
            await self.send_message(message)
            # 发送后从Redis中删除消息
            await cast(Awaitable[int], redis.hdel(self.redis_key, message_id))
        else:
            logging.info(
                f"{message_type} 消息已添加到 {self.name} 推送池，将在 {self.scheduled_time} (JST) 发送"
            )

    async def send_message(self, message: Dict[str, Any]):
        """发送推送消息，支持普通通知和后台通知"""
        try:
            message_type = message["message_type"]
            message_payload = {}
            if message_type == "alert":
                # 普通通知消息
                message_payload = {
                    "aps": {
                        "alert": {"title": message["title"], "body": message["body"]},
                        "sound": "default",
                    },
                    **message["data"],
                }
                if interruption_level := message.get("interruption-level"):
                    message_payload["aps"]["interruption-level"] = interruption_level
                push_type = PushType.ALERT
                log_info = message["title"]
            else:
                # 后台通知消息
                message_payload = {
                    "aps": {
                        "content-available": 1,
                    },
                    **message["data"],
                }
                push_type = PushType.BACKGROUND
                log_info = str(message["data"])

            notification = NotificationRequest(
                device_token=message["device_token"],
                message=message_payload,
                notification_id=str(uuid4()),
                push_type=push_type,
            )
            # 使用单例 APNs 客户端（复用 HTTP/2 连接，避免每条消息各建一条）
            apns_client = get_apns_client()
            result = await apns_client.send_notification(notification)
            if result.is_successful:
                logging.info(f"{message_type} 推送成功: {log_info}")
            else:
                logging.error(f"{message_type} 推送失败: {result.description}")
            return result.is_successful
        except Exception as e:
            logging.error(f"发送推送时出错: {e}")
            return False

    async def process_scheduled_messages(self):
        """处理并发送所有排队的消息"""
        # 从Redis获取所有待处理消息
        all_messages = await cast(Awaitable[dict[str, bytes]], redis.hgetall(self.redis_key))
        
        if not all_messages:
            return

        logging.info(f"开始处理 {self.name} 推送池中的 {len(all_messages)} 条消息")

        sent_count = 0
        failed_count = 0
        messages_to_delete = []

        for message_id, message_json in all_messages.items():
            message = json.loads(message_json)
            # 如果存储了ISO格式的日期时间，将其转换回datetime对象
            if isinstance(message.get("created_at"), str):
                message["created_at"] = datetime.fromisoformat(message["created_at"])
            
            success = await self.send_message(message)
            if success:
                sent_count += 1
                messages_to_delete.append(message_id)
            else:
                failed_count += 1
                messages_to_delete.append(message_id)

        # 从Redis中删除已成功处理的消息
        if messages_to_delete:
            await cast(Awaitable[int], redis.hdel(self.redis_key, *messages_to_delete))

        logging.info(
            f"{self.name} 推送池处理完成: 成功 {sent_count}, 失败 {failed_count}"
        )


class PushPoolManager:
    def __init__(self):
        # 创建10个不同的推送池
        self.pools: Dict[str, PushPool] = {
            "realtime": PushPool("实时推送池"),
            "morning_7am": PushPool("早上7点推送池", time(7, 0)),
            "morning_8_50am": PushPool("8:50推送池", time(8, 50)),
            "morning_10_30am": PushPool("10:30推送池", time(10, 30)),
            "lunch_12_50pm": PushPool("12:50推送池", time(12, 50)),
            "afternoon_2_30pm": PushPool("14:30推送池", time(14, 30)),
            "afternoon_4_10pm": PushPool("16:10推送池", time(16, 10)),
            "evening_5_50pm": PushPool("17:50推送池", time(17, 50)),
            "evening_7_30pm": PushPool("19:30推送池", time(19, 30)),
            "night_9pm": PushPool("晚上9点推送池", time(21, 15)),
        }

        # 启动调度器
        self.scheduler_task = None

    async def add_message_to_pool(
        self,
        pool_name: str,
        device_token: str,
        title: str,
        body: str,
        interruption_level: Optional[str] = None,
        data: Optional[Dict] = None,
    ):
        """添加普通通知消息到指定的推送池"""
        if pool_name not in self.pools:
            raise ValueError(f"推送池 '{pool_name}' 不存在")

        await self.pools[pool_name].add_message(
            device_token=device_token,
            title=title,
            body=body,
            data=data,
            interruption_level=interruption_level,
            message_type="alert",
        )

    async def add_background_message_to_pool(
        self,
        pool_name: str,
        device_token: str,
        data: Optional[Dict] = None,
    ):
        """添加后台通知消息到指定的推送池"""
        if pool_name not in self.pools:
            raise ValueError(f"推送池 '{pool_name}' 不存在")

        await self.pools[pool_name].add_message(
            device_token=device_token, data=data, message_type="background"
        )

    async def _scheduler(self):
        """调度器，负责定时检查并发送推送"""
        while True:
            try:
                now = datetime.now(JAPAN_TZ)
                current_time = now.time()

                for pool_name, pool in self.pools.items():
                    if pool.scheduled_time is not None:
                        # 检查当前时间是否接近预定时间 (允许1分钟误差)
                        scheduled_seconds = (
                            pool.scheduled_time.hour * 3600
                            + pool.scheduled_time.minute * 60
                        )
                        current_seconds = (
                            current_time.hour * 3600 + current_time.minute * 60
                        )

                        if abs(scheduled_seconds - current_seconds) <= 60:
                            logging.info(f"执行定时推送: {pool_name}")
                            await pool.process_scheduled_messages()

                # 每分钟检查一次
                await asyncio.sleep(60)
            except Exception as e:
                logging.error(f"推送调度器出错: {e}")
                # 出错后短暂等待再继续
                await asyncio.sleep(10)

    async def start(self):
        """启动推送池管理器"""
        logging.info("启动推送池管理器")
        self.scheduler_task = asyncio.create_task(self._scheduler())

    async def stop(self):
        """停止推送池管理器"""
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
            self.scheduler_task = None
        logging.info("推送池管理器已停止")
