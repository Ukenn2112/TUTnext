"""ESXi VM 控制（基于 pyvmomi，异步封装）。

pyvmomi 自身是同步 SOAP 客户端，这里用 ``asyncio.to_thread`` 把阻塞调用丢到线程池，
以便与事件循环协作。仅暴露 :py:meth:`ESXiClient.hard_reset` —— 这是看门狗唯一需要的动作。
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import Optional

logger = logging.getLogger(__name__)


class ESXiError(RuntimeError):
    """ESXi 连接 / 任务失败时抛出。"""


class ESXiClient:
    """ESXi 主机上 VM 电源控制的最小客户端。

    - 支持禁用 SSL 证书校验（自签环境必备）
    - 连接在每次调用内临时建立并立即断开，避免长连接在 ESXi 侧超时失效
    - ``hard_reset`` 行为取决于 VM 当前电源状态：
        * poweredOn  → ``ResetVM_Task``（硬重置，等效按 reset 键）
        * poweredOff → ``PowerOnVM_Task``（启动）
        * suspended  → ``PowerOnVM_Task``（从挂起恢复并启动）
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl

    async def hard_reset(self, vm_name: str, timeout: float = 60.0) -> None:
        """对指定 VM 执行硬重启。阻塞直到 ESXi 任务完成或超时。

        Raises:
            ESXiError: 连接失败 / VM 未找到 / 任务报错
            asyncio.TimeoutError: 超出 ``timeout`` 秒未完成
        """
        await asyncio.wait_for(
            asyncio.to_thread(self._hard_reset_sync, vm_name),
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Sync implementation (runs in a worker thread)
    # ------------------------------------------------------------------

    def _hard_reset_sync(self, vm_name: str) -> None:
        # Lazy-import so a partially-configured env doesn't fail at module load.
        from pyVim.connect import Disconnect, SmartConnect
        from pyVmomi import vim

        ctx: Optional[ssl.SSLContext]
        if self.verify_ssl:
            ctx = None
        else:
            ctx = ssl._create_unverified_context()

        try:
            si = SmartConnect(
                host=self.host,
                user=self.username,
                pwd=self.password,
                sslContext=ctx,
                disableSslCertValidation=not self.verify_ssl,
            )
        except TypeError:
            # Older pyvmomi without ``disableSslCertValidation`` kwarg
            si = SmartConnect(
                host=self.host,
                user=self.username,
                pwd=self.password,
                sslContext=ctx,
            )
        except Exception as e:
            raise ESXiError(f"ESXi 连接失败 ({self.host}): {e}") from e

        try:
            content = si.RetrieveContent()
            vm = self._find_vm_by_name(content, vm_name)
            if vm is None:
                raise ESXiError(f"VM 未找到: {vm_name}")

            state = vm.runtime.powerState
            logger.info("ESXi: VM %s 当前电源状态 = %s", vm_name, state)

            if state == vim.VirtualMachinePowerState.poweredOn:
                task = vm.ResetVM_Task()
                self._wait_for_task(task)
                logger.info("ESXi: VM %s 硬重置完成", vm_name)
            elif state == vim.VirtualMachinePowerState.poweredOff:
                task = vm.PowerOnVM_Task()
                self._wait_for_task(task)
                logger.info("ESXi: VM %s 已开机（原为关机状态）", vm_name)
            elif state == vim.VirtualMachinePowerState.suspended:
                task = vm.PowerOnVM_Task()
                self._wait_for_task(task)
                logger.info("ESXi: VM %s 已恢复运行（原为挂起状态）", vm_name)
            else:
                raise ESXiError(f"VM {vm_name} 状态异常: {state}")
        finally:
            try:
                Disconnect(si)
            except Exception:  # noqa: BLE001
                # Disconnect 失败不影响主流程
                pass

    @staticmethod
    def _find_vm_by_name(content, name: str):
        from pyVmomi import vim

        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        try:
            for vm in container.view:
                if vm.name == name:
                    return vm
        finally:
            container.Destroy()
        return None

    @staticmethod
    def _wait_for_task(task) -> None:
        from pyVmomi import vim

        # 轻量轮询：ESXi 任务通常 1-5 秒完成
        while task.info.state in (
            vim.TaskInfo.State.queued,
            vim.TaskInfo.State.running,
        ):
            time.sleep(0.5)
        if task.info.state == vim.TaskInfo.State.error:
            msg = task.info.error.msg if task.info.error else "unknown error"
            raise ESXiError(f"ESXi 任务失败: {msg}")
