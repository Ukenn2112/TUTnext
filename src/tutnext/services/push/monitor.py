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

  Layer 5: 课程关联即时传播 (Course-Correlated Immediate Propagation)
      当某用户检测到作业变化时，通过反向索引 course_users:{课程名} 立即找到
      同课程的其他用户，在同一轮内触发检查。
      利用"老师布置作业 → 同班所有学生同时收到"的特性，
      全班推送延迟从最差 30 分钟缩短到 ~30 秒。
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

    # Layer 5: 课程关联即时传播
    USER_COURSES_PREFIX = "user_courses:"
    COURSE_USERS_PREFIX = "course_users:"  # 反向索引：课程 → 用户集合
    COURSE_INDEX_TTL = 86400  # 正向/反向索引缓存 24 小时

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
    # Layer 5 helpers: 课程关联即时传播
    # ------------------------------------------------------------------

    async def _update_course_index(self, username: str, course_names: set[str]):
        """更新正向索引 user_courses + 反向索引 course_users（pipeline 批量写入）。"""
        if not course_names:
            return
        pipe = redis.pipeline()
        # 正向：user → courses
        fwd_key = f"{self.USER_COURSES_PREFIX}{username}"
        pipe.sadd(fwd_key, *course_names)
        pipe.expire(fwd_key, self.COURSE_INDEX_TTL)
        # 反向：course → users
        for cn in course_names:
            rev_key = f"{self.COURSE_USERS_PREFIX}{cn}"
            pipe.sadd(rev_key, username)
            pipe.expire(rev_key, self.COURSE_INDEX_TTL)
        await pipe.execute()

    async def _cache_user_courses_from_kadai(self, username: str, kadai_list: list):
        """从作业列表中提取课程名并更新正向/反向索引。"""
        course_names = {
            item.get("courseName") or item.get("courseSemesterName", "")
            for item in kadai_list
        }
        course_names.discard("")
        await self._update_course_index(username, course_names)

    async def _trigger_classmate_checks(self, trigger_username: str):
        """通过反向索引找到同课程的同学，立即触发检查（Layer 5 核心）。"""
        courses = await redis.smembers(f"{self.USER_COURSES_PREFIX}{trigger_username}")
        if not courses:
            return

        # 收集所有同课程用户（去重 + 排除触发者自身）
        classmate_set: set[str] = set()
        for c in courses:
            cn = c if isinstance(c, str) else c.decode()
            members = await redis.smembers(f"{self.COURSE_USERS_PREFIX}{cn}")
            for m in members:
                classmate_set.add(m if isinstance(m, str) else m.decode())
        classmate_set.discard(trigger_username)

        if not classmate_set:
            return

        # 过滤：跳过刚检查过的用户（避免同一轮重复检查）
        to_check: list[str] = []
        for uname in classmate_set:
            if not await redis.exists(f"monitor:last_check:{uname}"):
                to_check.append(uname)

        if not to_check:
            return

        logger.info(
            f"[Layer 5] {trigger_username} 变更 → 立即检查 {len(to_check)} 个同课程用户"
        )

        # 从数据库获取凭据并立即检查（仍受 semaphore 限流）
        tasks = []
        for uname in to_check:
            user = await db_manager.get_user(uname)
            if user:
                tasks.append(
                    self.check_single_user(
                        uname, user["encryptedpassword"], user["devicetoken"]
                    )
                )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Core per-user check (ports monitor_task logic from sender.py)
    # ------------------------------------------------------------------

    async def check_single_user(
        self, username: str, encrypted_password: str, device_token: str
    ) -> bool:
        """检查单个用户的作业变化，通过信号量限制并发登录数（Layer 1）。

        Returns:
            bool: 是否检测到作业变化（用于 Layer 5 触发同课程用户检查）。
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
                        return False
                    except Exception as api_error:
                        if "パスワードが正しくありません" in str(api_error):
                            logger.warning(
                                f"用户 {username} 密码错误，删除用户: {api_error}"
                            )
                            await db_manager.delete_user(username)
                            return False
                        if attempt + 1 >= max_retries:
                            if "セッション情報の抽出に失敗しました" in str(api_error):
                                logger.warning(
                                    f"用户 {username} 会话信息无效，可能密码错误，删除用户: {api_error}"
                                )
                                await db_manager.delete_user(username)
                                return False
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
                    return False

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
                    return changed

                await redis.set(
                    f"{username}:kadai", json.dumps(kadai_list), ex=120
                )
                logger.info(f"用户 {username} 的作业监测任务已完成")
                return changed

            except Exception as e:
                logger.error(f"处理用户 {username} 时出错: {e}")
                return False

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

        # Layer 3: 过滤退避中的用户
        users_to_check = []
        for user in users:
            if await self.should_check_user(user["username"]):
                users_to_check.append(user)

        if not users_to_check:
            logger.info("所有用户处于退避窗口内，跳过本轮监测")
            return

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
            changed = await self.check_single_user(
                user["username"],
                user["encryptedpassword"],
                user["devicetoken"],
            )
            # Layer 5: 变更检测到 → 立即检查同课程的同学
            if changed:
                await self._trigger_classmate_checks(user["username"])
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
