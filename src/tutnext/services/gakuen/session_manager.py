"""
Per-User Session 管理器
=======================
提供两层优化，解决 T-NEXT 并发登录冲突问题：

1. **Per-user asyncio.Lock** — 同一用户同一时刻只允许一个 T-NEXT 操作，
   不同用户完全并行，互不阻塞。
2. **Session 缓存** — 登录后缓存 GakuenAPI 实例（含 aiohttp cookies + rx_tokens），
   后续请求复用已登录的 session，跳过 ~1.2s 的 _mobile_login()。

用法::

    from tutnext.services.gakuen.session_manager import get_session_manager

    async with get_session_manager().acquire(username, enc_pw) as gakuen:
        result = await gakuen.get_user_kadai(skip_login=True)
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from tutnext.config import HTTP_PROXY
from tutnext.services.gakuen.client import GakuenAPI

logger = logging.getLogger(__name__)

BASE_URL = "https://next.tama.ac.jp"
SESSION_TTL = 300  # 缓存 session 最多 5 分钟
# 等待 per-user lock 的上限。等待太久的请求会被 Cloudflare 运行时判定为"挂起"并取消（实测 25–40 s），
# 所以超过 LOCK_WAIT_SECONDS 就直接返回可重试的错误，让调用方按「他端末で同時に実行」路径重试。
LOCK_WAIT_SECONDS = 20.0
# 锁被持有超过这个时间视为遗留锁（持有者已被运行时直接终止，例如 CPU 超限），重建后继续。
STALE_LOCK_SECONDS = 150.0


@dataclass
class _UserSession:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    gakuen: GakuenAPI | None = None
    last_used: float = 0.0
    held_since: float = 0.0


@asynccontextmanager
async def _locked(us: _UserSession, username: str):
    """``async with us.lock`` 加等待上限与遗留锁检测。"""
    from tutnext.services.gakuen.errors import GakuenAPIError

    try:
        await asyncio.wait_for(us.lock.acquire(), LOCK_WAIT_SECONDS)
    except TimeoutError:
        held = time.monotonic() - us.held_since if us.held_since else 0.0
        if held > STALE_LOCK_SECONDS:
            logger.warning("[SessionManager] %s 的锁已被持有 %.0fs，视为遗留锁并重建", username, held)
            us.lock = asyncio.Lock()
            us.gakuen = None
            await us.lock.acquire()
        else:
            raise GakuenAPIError(
                "他端末で同時に実行中の処理があります。しばらくしてから再試行してください。",
                error_code="SESSION_BUSY",
            ) from None
    us.held_since = time.monotonic()
    try:
        yield
    finally:
        us.held_since = 0.0
        if us.lock.locked():
            us.lock.release()


class SessionManager:
    """Per-user Lock + Session 缓存管理器。"""

    def __init__(self) -> None:
        self._sessions: dict[str, _UserSession] = {}
        self._meta_lock = asyncio.Lock()

    async def _get_user_session(self, username: str) -> _UserSession:
        """获取或创建用户的 session entry（线程安全）。"""
        async with self._meta_lock:
            if username not in self._sessions:
                self._sessions[username] = _UserSession()
            return self._sessions[username]

    @asynccontextmanager
    async def acquire(self, username: str, encrypted_password: str):
        """获取已登录的 GakuenAPI 实例。

        - 如果有缓存且未过期，直接返回（~0ms）
        - 如果没有缓存，新建并登录（~1.2s）
        - per-user lock 保证同一用户串行执行

        Yields:
            GakuenAPI: 已登录的实例，调用方应使用 skip_login=True
        """
        us = await self._get_user_session(username)

        async with _locked(us, username):
            now = time.monotonic()

            # 检查缓存是否可用
            if (
                us.gakuen is not None
                and (now - us.last_used) < SESSION_TTL
            ):
                # 复用缓存的 session
                us.gakuen.user_id = username
                us.gakuen.encrypted_login_password = encrypted_password
                us.last_used = now
                logger.debug(f"[SessionManager] 复用缓存 session: {username}")
                try:
                    yield us.gakuen
                    return
                except Exception:
                    # 操作失败，可能是 session 过期，清除缓存
                    logger.info(
                        f"[SessionManager] 缓存 session 操作失败，清除: {username}"
                    )
                    await self._close_session(us)
                    raise

            # 没有缓存或已过期，关闭旧的并新建
            await self._close_session(us)
            gakuen = GakuenAPI(
                username, "", BASE_URL, encrypted_password, http_proxy=HTTP_PROXY
            )
            try:
                await gakuen._mobile_login()
                us.gakuen = gakuen
                us.last_used = time.monotonic()
                logger.debug(f"[SessionManager] 新建 session: {username}")
                yield gakuen
            except Exception:
                # 登录失败或操作失败，清理
                await gakuen.close()
                us.gakuen = None
                raise

    @asynccontextmanager
    async def lock_only(self, username: str):
        """仅获取 per-user lock，不做 session 缓存。

        用于 web login 和 api_login 等不走 mobile login 的场景。
        """
        us = await self._get_user_session(username)
        async with _locked(us, username):
            yield

    async def _close_session(self, us: _UserSession) -> None:
        """安全关闭并清除缓存的 session。"""
        if us.gakuen is not None:
            try:
                await us.gakuen.close()
            except Exception:
                pass
            us.gakuen = None

    async def cleanup(self, max_idle_seconds: float = SESSION_TTL) -> None:
        """清理超时的 session，释放连接和内存。"""
        now = time.monotonic()
        to_clean: list[str] = []

        async with self._meta_lock:
            for username, us in self._sessions.items():
                if us.lock.locked():
                    continue  # 正在使用，跳过
                if us.gakuen is not None and (now - us.last_used) >= max_idle_seconds:
                    to_clean.append(username)

        for username in to_clean:
            us = self._sessions.get(username)
            if us is None:
                continue
            # Acquire the per-user lock to prevent race with acquire()
            async with us.lock:
                # Re-check after acquiring lock — session may have been refreshed
                if us.gakuen is not None and (time.monotonic() - us.last_used) >= max_idle_seconds:
                    await self._close_session(us)
                    logger.debug(f"[SessionManager] 清理过期 session: {username}")

    async def invalidate(self, username: str) -> None:
        """主动失效指定用户的缓存 session。"""
        us = self._sessions.get(username)
        if us:
            await self._close_session(us)


# 模块级单例
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
