"""APNs client.

* server mode keeps the ``aioapns`` singleton (one long-lived HTTP/2 connection);
* Cloudflare Workers mode uses an HTTP client built on ``fetch`` with ES256
  provider-token (JWT) authentication, because ``aioapns`` needs raw sockets.

Both expose the same ``NotificationRequest`` / ``PushType`` /
``send_notification()`` surface so the push code is runtime-agnostic.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from tutnext import runtime
from tutnext.config import get_apns_config

logger = logging.getLogger(__name__)


class PushType(str, Enum):
    ALERT = "alert"
    BACKGROUND = "background"
    VOIP = "voip"
    COMPLICATION = "complication"
    FILEPROVIDER = "fileprovider"
    MDM = "mdm"
    LIVEACTIVITY = "liveactivity"


@dataclass
class NotificationRequest:
    device_token: str
    message: dict[str, Any]
    notification_id: str | None = None
    time_to_live: int | None = None
    priority: int | None = None
    collapse_key: str | None = None
    push_type: PushType = PushType.ALERT
    apns_topic: str | None = None

    def __post_init__(self) -> None:
        if self.notification_id is None:
            self.notification_id = str(uuid4())


@dataclass
class NotificationResult:
    notification_id: str
    status: str
    description: str | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def is_successful(self) -> bool:
        return self.status == "200"


class FetchAPNs:
    """Minimal APNs HTTP client over Workers ``fetch`` (token-based auth)."""

    _JWT_LIFETIME = 50 * 60  # Apple requires refreshing provider tokens at least hourly

    def __init__(self, key: str, key_id: str, team_id: str, topic: str, use_sandbox: bool = False) -> None:
        self.key = key
        self.key_id = key_id
        self.team_id = team_id
        self.topic = topic
        self.host = "https://api.sandbox.push.apple.com" if use_sandbox else "https://api.push.apple.com"
        self._jwt: str | None = None
        self._jwt_issued_at = 0.0

    def _token(self) -> str:
        now = time.time()
        if self._jwt is None or now - self._jwt_issued_at >= self._JWT_LIFETIME:
            import jwt

            self._jwt = jwt.encode(
                {"iss": self.team_id, "iat": int(now)},
                self.key,
                algorithm="ES256",
                headers={"kid": self.key_id},
            )
            self._jwt_issued_at = now
        return self._jwt

    async def send_notification(self, request: NotificationRequest) -> NotificationResult:
        from tutnext.core import http as core_http

        push_type = request.push_type.value if isinstance(request.push_type, PushType) else str(request.push_type)
        priority = request.priority or (5 if push_type == "background" else 10)
        headers = {
            "authorization": f"bearer {self._token()}",
            "apns-topic": request.apns_topic or self.topic,
            "apns-push-type": push_type,
            "apns-priority": str(priority),
            "apns-expiration": str(int(time.time()) + request.time_to_live if request.time_to_live else 0),
            "content-type": "application/json",
        }
        if request.notification_id:
            headers["apns-id"] = request.notification_id
        if request.collapse_key:
            headers["apns-collapse-id"] = request.collapse_key

        url = f"{self.host}/3/device/{request.device_token}"
        resp = await core_http.request(
            "POST", url, headers=headers, data=json.dumps(request.message, ensure_ascii=False), timeout=15
        )
        description: str | None = None
        if resp.status != 200:
            try:
                description = resp.json().get("reason")
            except Exception:  # noqa: BLE001
                description = resp.text()[:200] or None
            logger.warning("APNs %s → %s %s", push_type, resp.status, description)
        return NotificationResult(
            notification_id=resp.headers.get("apns-id") or request.notification_id or "",
            status=str(resp.status),
            description=description,
        )


_apns_client: Any = None


def get_apns_client():
    """Get or create the singleton APNs client for the current runtime.

    Raises:
        RuntimeError: If APNs credentials are not fully configured.
    """
    global _apns_client
    if _apns_client is None:
        config = get_apns_config()
        if not config["key"]:
            raise RuntimeError(
                "APNs key is not configured. "
                "Set APNS_KEY_FILE (server) or the APNS_PRIVATE_KEY secret (Workers) before using push notifications."
            )
        if runtime.IS_WORKERS:
            _apns_client = FetchAPNs(
                key=config["key"],
                key_id=config["key_id"],
                team_id=config["team_id"],
                topic=config["topic"],
                use_sandbox=bool(config["use_sandbox"]),
            )
            logger.info("APNs fetch client created (singleton)")
        else:
            from aioapns import APNs

            _apns_client = APNs(
                key=config["key"],
                key_id=config["key_id"],
                team_id=config["team_id"],
                topic=config["topic"],
                use_sandbox=config["use_sandbox"],
            )
            logger.info("APNs client created (singleton)")
    return _apns_client


if not runtime.IS_WORKERS:  # pragma: no cover - server mode re-exports the aioapns types
    try:
        from aioapns import NotificationRequest, PushType  # type: ignore[assignment]  # noqa: F811
    except ImportError:
        pass
