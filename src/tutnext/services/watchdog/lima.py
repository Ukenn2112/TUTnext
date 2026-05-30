"""Lima VM 控制 —— 看门狗重启动作的执行者。"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

LIMACTL = "/opt/homebrew/bin/limactl"


class LimaClient:
    """通过 limactl 对 Lima VM 执行 stop → start（等效硬重启）。"""

    async def hard_reset(self, vm_name: str, timeout: float = 120.0) -> None:
        """Stop then start the named Lima VM. Raises RuntimeError on failure."""
        await asyncio.wait_for(self._restart(vm_name), timeout=timeout)

    async def _restart(self, vm_name: str) -> None:
        async def run(args: list[str]) -> None:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"limactl {args[1]} {vm_name} failed "
                    f"(exit {proc.returncode}): {stderr.decode().strip()}"
                )

        logger.info("Lima: stopping VM %s", vm_name)
        await run([LIMACTL, "stop", vm_name])
        logger.info("Lima: starting VM %s", vm_name)
        await run([LIMACTL, "start", vm_name])
        logger.info("Lima: VM %s restarted", vm_name)
