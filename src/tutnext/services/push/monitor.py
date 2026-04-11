"""
MonitorService — 限速版用户作业监测服务
=======================================
五层防护 + 智能调度机制:

  Layer 1: asyncio.Semaphore
      限制并发登录数，默认同时最多 3 个并发请求，防止瞬间涌入大量登录请求。

  Layer 2: 静默时段
      凌晨 3:00-6:10 由调度器（__main__.py）控制，完全不触发本服务，
      避免在大学系统维护窗口期发起无效请求。

  Layer 3: 指数退避
      连续无课题变化时，逐步延长该用户的检查间隔（5→10→20→30 分钟），
      减少对无活跃课题用户的重复轮询。

  Layer 4: 时间分散
      将本轮所有用户的检查请求均匀分散到整个监测间隔内，
      而非同时发起，避免形成流量峰值。

  Layer 5: 课程关联变更传播 (Course-Correlated Change Propagation)
      当某用户检测到作业变化时，将其所在课程标记为"热点"。
      下一轮监测中，同课程的其他用户即使处于退避窗口也会被优先检查。
      这利用了"老师布置作业 → 同班所有学生同时收到"的特性，
      使全班推送延迟从最差 30 分钟缩短到 ~5 分钟。
"""
# tutnext/services/push/monitor.py

import asyncio
import json
import logging
from datetime import datetime

from tutnext.config import settings, redis, HTTP_PROXY, JAPAN_TZ
from tutnext.core.database import db_manager
from tutnext.services.gakuen.client import GakuenAPI
from tutnext.services.gakuen.errors import GakuenLoginError, GakuenPermissionError
from tutnext.services.gakuen.session_manager import get_session_manager
from tutnext.services.google_classroom import classroom_api
from tutnext.services.push.pool import PushPoolManager

logger = logging.getLogger(__name__)


class MonitorService:
    """限速版用户作业监测服务。

    每轮监测循环：
    1. 从数据库读取所有用户
    2. 基于退避计数过滤掉"暂不需要检查"的用户（Layer 3）
    3. 将剩余用户均匀分散到本轮间隔内（Layer 4）
    4. 通过 Semaphore 限制并发登录数（Layer 1）
    5. 根据检查结果更新 Redis 中的退避状态
    """

    # Layer 3: 退避间隔梯级（秒）
    # 连续无变化 1 次 → 等 5 分钟；2 次 → 等 10 分钟；
    # 3 次 → 等 20 分钟；4 次及以上 → 等 30 分钟
    BACKOFF_INTERVALS = [300, 600, 1200, 1800]
    BACKOFF_KEY_PREFIX = "monitor:backoff:"

    # Layer 5: 课程关联变更传播
    HOT_COURSE_PREFIX = "hot_course:"
    HOT_COURSE_TTL = 600  # 热点课程标记保留 10 分钟
    USER_COURSES_PREFIX = "user_courses:"
    USER_COURSES_TTL = 86400  # 用户课程列表缓存 24 小时

    def __init__(self, push_manager: PushPoolManager):
        self.push_manager = push_manager
        # Layer 1: 信号量，限制并发登录数
        self.semaphore = asyncio.Semaphore(settings.monitor_max_concurrent)
        self.interval = settings.monitor_interval_seconds

    # ------------------------------------------------------------------
    # Layer 3 helpers
    # ------------------------------------------------------------------

    async def should_check_user(self, username: str) -> bool:
        """判断该用户是否需要在本轮被检查。

        若退避计数存在且对应的"上次检查"时间戳键仍有效（TTL > 0），
        则跳过本轮检查；否则需要检查。
        """
        backoff_key = f"{self.BACKOFF_KEY_PREFIX}{username}"
        backoff_count = await redis.get(backoff_key)
        if backoff_count is None:
            # 从未退避，总是检查
            return True
        # 检查上次检查的时间戳键是否仍存在（TTL 未到期 = 还需等待）
        last_check_key = f"monitor:last_check:{username}"
        if await redis.exists(last_check_key):
            return False  # 退避窗口尚未结束，跳过
        return True

    async def record_check_result(self, username: str, changed: bool):
        """根据本次检查结果更新退避状态。

        变化时重置退避；无变化时递增退避计数并设置对应的等待窗口。
        """
        backoff_key = f"{self.BACKOFF_KEY_PREFIX}{username}"
        if changed:
            # 有变化 → 重置退避，下次立即检查
            await redis.delete(backoff_key)
            # Layer 5: 标记该用户的课程为热点，同课程的其他用户将被优先检查
            await self._mark_courses_hot(username)
        else:
            # 无变化 → 递增退避计数
            await redis.incr(backoff_key)
            await redis.expire(backoff_key, 7200)  # 退避计数最多保留 2 小时

        # 设置"上次检查"时间戳键，TTL = 当前退避间隔
        backoff_count = int(await redis.get(backoff_key) or 0)
        idx = min(backoff_count, len(self.BACKOFF_INTERVALS) - 1)
        interval = self.BACKOFF_INTERVALS[idx]
        last_check_key = f"monitor:last_check:{username}"
        await redis.set(last_check_key, "1", ex=interval)

    # ------------------------------------------------------------------
    # Layer 5 helpers: 课程关联变更传播
    # ------------------------------------------------------------------

    async def _has_hot_course(self, username: str) -> bool:
        """检查用户是否有处于"热点"状态的课程（SMEMBERS + MGET，共 2 次 Redis 调用）。"""
        courses = await redis.smembers(f"{self.USER_COURSES_PREFIX}{username}")
        if not courses:
            return False
        hot_keys = [
            f"{self.HOT_COURSE_PREFIX}{(c if isinstance(c, str) else c.decode())}"
            for c in courses
        ]
        values = await redis.mget(*hot_keys)
        return any(v is not None for v in values)

    async def _mark_courses_hot(self, username: str):
        """将用户所在的所有课程标记为热点（pipeline 批量写入）。"""
        courses = await redis.smembers(f"{self.USER_COURSES_PREFIX}{username}")
        if not courses:
            return
        pipe = redis.pipeline()
        for c in courses:
            cn = c if isinstance(c, str) else c.decode()
            pipe.set(f"{self.HOT_COURSE_PREFIX}{cn}", "1", ex=self.HOT_COURSE_TTL)
        await pipe.execute()

    async def _cache_user_courses_from_kadai(self, username: str, kadai_list: list):
        """从作业列表中提取课程名并补充到用户课程缓存（增量添加）。"""
        course_names = {
            item.get("courseName") or item.get("courseSemesterName", "")
            for item in kadai_list
        }
        course_names.discard("")
        if not course_names:
            return
        key = f"{self.USER_COURSES_PREFIX}{username}"
        await redis.sadd(key, *course_names)
        await redis.expire(key, self.USER_COURSES_TTL)

    # ------------------------------------------------------------------
    # Core per-user check (ports monitor_task logic from sender.py)
    # ------------------------------------------------------------------

    async def check_single_user(
        self, username: str, encrypted_password: str, device_token: str
    ):
        """检查单个用户的作业变化，通过信号量限制并发登录数（Layer 1）。

        逻辑与原 monitor_task() 完全一致，额外加入退避状态更新。
        """
        # Layer 1: 信号量保证同一时刻最多 N 个并发登录
        async with self.semaphore:
            try:
                max_retries = 5
                kadai_list = None

                for attempt in range(max_retries):
                    try:
                        if attempt > 0:
                            await get_session_manager().invalidate(username)
                            await asyncio.sleep(2)
                        async with get_session_manager().acquire(username, encrypted_password) as gakuen:
                            kadai_list = await gakuen.get_user_kadai(
                                username, encrypted_password, skip_login=True
                            )
                            if await db_manager.get_user_tokens(username):
                                classroom_kadai_list = (
                                    await classroom_api.get_user_assignments(username)
                                )
                                if classroom_kadai_list:
                                    kadai_list.extend(classroom_kadai_list)
                            break
                    except GakuenPermissionError as perm_error:
                        logger.warning(
                            f"用户 {username} 凭据无效，跳过: {perm_error}"
                        )
                        return
                    except Exception as api_error:
                        if "パスワードが正しくありません" in str(api_error):
                            logger.warning(
                                f"用户 {username} 密码错误，删除用户: {api_error}"
                            )
                            await db_manager.delete_user(username)
                            return
                        if attempt + 1 >= max_retries:
                            if "セッション情報の抽出に失敗しました" in str(api_error):
                                logger.warning(
                                    f"用户 {username} 会话信息无效，可能密码错误，删除用户: {api_error}"
                                )
                                await db_manager.delete_user(username)
                                return
                            logger.error(
                                f"用户 {username} 获取作业数据失败，已达最大重试次数: {api_error}"
                            )
                            from tutnext.services.push.sender import record_api_error
                            await record_api_error()
                            raise api_error
                        logger.warning(
                            f"用户 {username} 获取作业数据失败，重试第 {attempt + 1} 次: {api_error}"
                        )

                if kadai_list is None:
                    return

                # Layer 5: 从作业列表补充用户课程缓存（自举）
                await self._cache_user_courses_from_kadai(username, kadai_list)

                # --- 对比作业数量，决定是否推送 ---
                changed = False
                kadai_count_key = f"kadai_count:{username}"

                if await redis.exists(kadai_count_key):
                    old_count = int(await redis.get(kadai_count_key))
                    if len(kadai_list) == 0:
                        await redis.delete(kadai_count_key)
                        changed = True
                        await self.push_manager.add_background_message_to_pool(
                            "realtime",
                            device_token,
                            {"updateType": "kaidaiNumChange", "num": 0},
                        )
                    elif old_count != len(kadai_list):
                        await redis.set(kadai_count_key, len(kadai_list))
                        changed = True
                        await self.push_manager.add_background_message_to_pool(
                            "realtime",
                            device_token,
                            {
                                "updateType": "kaidaiNumChange",
                                "num": len(kadai_list),
                            },
                        )
                else:
                    if len(kadai_list) > 0:
                        await redis.set(kadai_count_key, len(kadai_list))
                        changed = True
                        await self.push_manager.add_background_message_to_pool(
                            "realtime",
                            device_token,
                            {
                                "updateType": "kaidaiNumChange",
                                "num": len(kadai_list),
                            },
                        )

                # Layer 3: 记录退避结果
                await self.record_check_result(username, changed)

                if not kadai_list:
                    logger.info(f"用户 {username} 没有作业")
                    return

                await redis.set(
                    f"{username}:kadai", json.dumps(kadai_list), ex=120
                )
                logger.info(f"用户 {username} 的作业监测任务已完成")

            except Exception as e:
                logger.error(f"处理用户 {username} 时出错: {e}")

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    async def run_monitoring_cycle(self):
        """执行一轮监测循环（Layer 1-5 综合应用）。

        步骤：
        1. 读取所有用户
        2. 分类：正常检查 / 热点课程拉入 / 跳过（Layer 3 + Layer 5）
        3. 热点用户排在前面优先检查，然后是正常用户
        4. 将检查请求均匀分散到本轮间隔内（Layer 4）
        5. asyncio.gather 并发执行，信号量在内部限制实际并发（Layer 1）
        """
        await get_session_manager().cleanup()
        users = await db_manager.get_all_users()
        if not users:
            logger.info("没有用户需要监测")
            return

        # Layer 3 + 5: 分类用户
        hot_users = []      # 退避中但有热点课程 → 拉入优先检查
        normal_users = []   # 退避到期或从未退避 → 正常检查
        for user in users:
            username = user["username"]
            if await self.should_check_user(username):
                normal_users.append(user)
            elif await self._has_hot_course(username):
                hot_users.append(user)
            # else: 退避中且无热点课程 → 跳过

        users_to_check = hot_users + normal_users

        if not users_to_check:
            logger.info("所有用户处于退避窗口内，跳过本轮监测")
            return

        if hot_users:
            logger.info(
                f"本轮监测 {len(users_to_check)}/{len(users)} 个用户"
                f"（其中 {len(hot_users)} 个因热点课程优先拉入）"
            )
        else:
            logger.info(
                f"本轮监测 {len(users_to_check)}/{len(users)} 个用户"
            )

        # Layer 4: 将检查请求均匀分散到整个间隔内，避免同时发起 N 个登录
        tasks = []
        delay_per_user = self.interval / max(len(users_to_check), 1)
        for i, user in enumerate(users_to_check):
            delay = i * delay_per_user
            tasks.append(self._delayed_check(user, delay))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _delayed_check(self, user: dict, delay: float):
        """等待指定时间后执行用户检查（Layer 4 的时间分散实现）。"""
        if delay > 0:
            await asyncio.sleep(delay)
        # Layer 2: 若等待后已进入静默时段，跳过本次检查
        now = datetime.now(JAPAN_TZ)
        if 3 <= now.hour < 6 or (now.hour == 6 and now.minute < 10):
            logger.debug(f"静默时段，跳过用户 {user['username']} 的检查")
            return
        try:
            await self.check_single_user(
                user["username"],
                user["encryptedpassword"],
                user["devicetoken"],
            )
        except GakuenLoginError as e:
            if "パスワードが正しくありません" in str(e):
                logger.warning(
                    f"用户 {user['username']} 密码错误，删除用户: {e}"
                )
                await db_manager.delete_user(user["username"])
            else:
                logger.error(f"监测用户 {user['username']} 登录错误: {e}")
        except Exception as e:
            logger.error(f"监测用户 {user['username']} 时出错: {e}")
