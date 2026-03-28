# APNs 单例客户端
# 避免每条消息创建新的 APNs 连接，减少资源消耗
import logging
from aioapns import APNs
from tutnext.config import APNS_CONFIG

logger = logging.getLogger(__name__)

_apns_client: APNs | None = None


def get_apns_client() -> APNs:
    """Get or create the singleton APNs client.

    首次调用时创建 APNs 客户端并缓存，后续调用直接返回缓存实例。
    这样整个进程生命周期内只维护一条 APNs HTTP/2 连接，而不是每条消息各建一条。

    Raises:
        RuntimeError: If APNs credentials are not fully configured.
    """
    global _apns_client
    if _apns_client is None:
        if APNS_CONFIG["key"] is None:
            raise RuntimeError(
                "APNs key is not configured. "
                "Set APNS_KEY_PATH (or APNS_KEY_CONTENT) in your environment before using push notifications."
            )
        _apns_client = APNs(
            key=APNS_CONFIG["key"],
            key_id=APNS_CONFIG["key_id"],
            team_id=APNS_CONFIG["team_id"],
            topic=APNS_CONFIG["topic"],
            use_sandbox=APNS_CONFIG["use_sandbox"],
        )
        logger.info("APNs client created (singleton)")
    return _apns_client
