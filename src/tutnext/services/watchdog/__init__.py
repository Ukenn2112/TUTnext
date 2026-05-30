"""Proxy self-healing watchdog (ESXi-backed).

Exports the singleton watchdog used by the gakuen HTTP layer to report
network failures, and initialized at application startup.
"""
from tutnext.services.watchdog.proxy_watchdog import (
    ProxyWatchdog,
    get_watchdog,
    init_watchdog,
)

__all__ = ["ProxyWatchdog", "get_watchdog", "init_watchdog"]
