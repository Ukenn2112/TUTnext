"""代理服务器自愈看门狗。

设计思路
---------
- **被动触发**：HTTP 层（``gakuen/http.py``）每次遇到 ``aiohttp.ClientError`` 或 5xx
  都调用 :py:meth:`ProxyWatchdog.report_failure`，成本近似为零（仅附加一个时间戳 + 调度一个协程）。
- **滑动窗口**：窗口内（默认 180s）失败次数达到阈值（默认 5）才进入评估阶段，避免偶发抖动触发重启。
- **独立 TCP 探针**：触发后先做一次 ``asyncio.open_connection`` 探针（默认 2s 超时），
  只有探针失败才认定代理真的挂了 —— 彻底屏蔽"target 侧 5xx 被误判为代理故障"的假阳性。
- **硬重启 + 冷却**：通过 :py:class:`ESXiClient` 发起 ``ResetVM_Task``，
  然后进入冷却期（默认 900s），防止 VM 启动过程中误判再次重启形成风暴。
- **通知**：重启成功 / ESXi 调用失败，均通过 ``NOTIFICATION_API_URL`` 外发一条 GET 提醒。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Optional
from urllib.parse import quote, urlparse

import aiohttp

from tutnext.config import NOTIFICATION_API_URL, settings
from tutnext.services.watchdog.lima import LimaClient

logger = logging.getLogger(__name__)


class ProxyWatchdog:
    """代理服务器自愈看门狗 —— 单例由 :func:`init_watchdog` 创建。"""

    def __init__(
        self,
        proxy_host: str,
        proxy_port: int,
        esxi: LimaClient,
        vm_name: str,
        *,
        window_seconds: float = 180.0,
        failure_threshold: int = 5,
        cooldown_seconds: float = 900.0,
        probe_timeout: float = 2.0,
    ) -> None:
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.esxi = esxi
        self.vm_name = vm_name
        self.window_seconds = window_seconds
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.probe_timeout = probe_timeout

        self._failures: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._rebooting: bool = False
        # 初值设为 -inf 以保证程序启动后第一次触发不被冷却误杀
        self._last_reboot_ts: float = float("-inf")

    # ------------------------------------------------------------------
    # 热路径接口 —— 由 HTTP 客户端在异常路径调用，必须极快且不抛异常
    # ------------------------------------------------------------------

    def report_failure(self, reason: str = "") -> None:
        """记录一次代理侧失败。fire-and-forget，不阻塞调用方。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 理论上不应发生（所有调用点都在 async 上下文）
            return
        loop.create_task(self._evaluate(reason))

    # ------------------------------------------------------------------
    # Evaluation & trigger logic
    # ------------------------------------------------------------------

    async def _evaluate(self, reason: str) -> None:
        try:
            async with self._lock:
                now = time.monotonic()
                # 剔除滑出窗口的旧条目
                while self._failures and now - self._failures[0] > self.window_seconds:
                    self._failures.popleft()
                self._failures.append(now)

                if self._rebooting:
                    return
                if now - self._last_reboot_ts < self.cooldown_seconds:
                    return
                if len(self._failures) < self.failure_threshold:
                    return

                # 锁内置位，阻止并发触发
                self._rebooting = True

            try:
                await self._handle_trigger(reason)
            finally:
                async with self._lock:
                    self._rebooting = False
        except Exception:  # noqa: BLE001
            logger.exception("Watchdog: _evaluate 内部异常")

    async def _handle_trigger(self, reason: str) -> None:
        logger.warning(
            "Watchdog: %ds 内已累计 %d 次失败 (阈值 %d)，对 %s:%d 发起探针...",
            int(self.window_seconds),
            len(self._failures),
            self.failure_threshold,
            self.proxy_host,
            self.proxy_port,
        )

        if await self._probe_healthy():
            logger.info("Watchdog: 探针成功，判为误报，重置失败计数")
            async with self._lock:
                self._failures.clear()
            return

        logger.error(
            "Watchdog: 探针失败，通过 limactl 重启 VM「%s」",
            self.vm_name,
        )

        try:
            await self.esxi.hard_reset(self.vm_name)
        except Exception as e:  # noqa: BLE001
            logger.exception("Watchdog: Lima VM 重启失败")
            await self._notify(
                "TUTnext代理重启失败",
                f"limactl重启{self.vm_name}失败: {e}",
            )
            return

        # 成功：记录冷却起点并清空计数
        self._last_reboot_ts = time.monotonic()
        async with self._lock:
            self._failures.clear()

        logger.info(
            "Watchdog: VM %s 已重启，进入 %ds 冷却期",
            self.vm_name,
            int(self.cooldown_seconds),
        )
        await self._notify(
            "TUTnext代理服务器已重启",
            f"检测到{self.proxy_host}:{self.proxy_port}连续失败，已通过limactl重启VM「{self.vm_name}」",
        )

    # ------------------------------------------------------------------
    # Probe & notify
    # ------------------------------------------------------------------

    async def _probe_healthy(self) -> bool:
        """通过代理发起 HTTP CONNECT 探针，验证端到端通路。

        向代理发送 CONNECT next.tama.ac.jp:443，收到 200 则认为代理可用。
        仅 TCP 握手成功不足以判断代理健康（gost 进程活着但 ppp0 掉了时也能握手）。
        """
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.proxy_host, self.proxy_port),
                timeout=self.probe_timeout,
            )
            connect_req = b"CONNECT next.tama.ac.jp:443 HTTP/1.1\r\nHost: next.tama.ac.jp:443\r\n\r\n"
            writer.write(connect_req)
            await asyncio.wait_for(writer.drain(), timeout=self.probe_timeout)
            response = await asyncio.wait_for(reader.read(64), timeout=self.probe_timeout)
            return response.startswith(b"HTTP/1.1 200") or response.startswith(b"HTTP/1.0 200")
        except (asyncio.TimeoutError, OSError):
            return False
        finally:
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

    async def _notify(self, title: str, body: str) -> None:
        """通过 NOTIFICATION_API_URL 外发 GET 通知。失败仅记录日志。"""
        if not NOTIFICATION_API_URL:
            logger.warning("Watchdog: NOTIFICATION_API_URL 未配置，跳过通知")
            return

        url = NOTIFICATION_API_URL.format(
            title=quote(title, safe=""),
            message=quote(body, safe=""),
        )
        try:
            # 通知本身不走 HTTP_PROXY —— 代理都挂了还走它就是死锁
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        logger.info("Watchdog: 通知已发送 (%s)", title)
                    else:
                        logger.warning(
                            "Watchdog: 通知 API 返回非 200: %d", response.status
                        )
        except Exception as e:  # noqa: BLE001
            logger.error("Watchdog: 发送通知失败: %s", e)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_watchdog: Optional[ProxyWatchdog] = None


def init_watchdog() -> Optional[ProxyWatchdog]:
    """根据 settings 创建单例看门狗。配置不全则禁用并返回 None。

    该函数幂等：重复调用返回同一实例。
    """
    global _watchdog
    if _watchdog is not None:
        return _watchdog

    if not settings.http_proxy:
        logger.info("Watchdog: HTTP_PROXY 未配置，代理看门狗已禁用")
        return None

    if not settings.lima_vm_name:
        logger.info("Watchdog: LIMA_VM_NAME 未配置，代理看门狗已禁用")
        return None

    parsed = urlparse(settings.http_proxy)
    host = parsed.hostname
    port = parsed.port or 80
    if not host:
        logger.error(
            "Watchdog: 无法解析 HTTP_PROXY 主机名 (%s)，代理看门狗已禁用",
            settings.http_proxy,
        )
        return None

    _watchdog = ProxyWatchdog(
        proxy_host=host,
        proxy_port=port,
        esxi=LimaClient(),
        vm_name=settings.lima_vm_name,
        window_seconds=settings.watchdog_window_seconds,
        failure_threshold=settings.watchdog_failure_threshold,
        cooldown_seconds=settings.watchdog_cooldown_seconds,
    )
    logger.info(
        "Watchdog 就绪: proxy=%s:%d vm=%s threshold=%d/%ds cooldown=%ds",
        host,
        port,
        settings.lima_vm_name,
        settings.watchdog_failure_threshold,
        int(settings.watchdog_window_seconds),
        int(settings.watchdog_cooldown_seconds),
    )
    return _watchdog


def get_watchdog() -> Optional[ProxyWatchdog]:
    """返回已初始化的看门狗实例；未初始化或禁用时返回 None。"""
    return _watchdog
