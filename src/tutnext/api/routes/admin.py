"""Operator-only diagnostics (protected by the ``ADMIN_KEY`` secret).

``POST /admin/apns-probe`` sends one APNs request through the runtime's APNs
client and returns Apple's raw answer, so the push path (provider-token JWT,
HTTP/2 to ``api.push.apple.com``) can be verified without touching users: a
made-up token yields ``400 BadDeviceToken`` when authentication works and
``403 InvalidProviderToken`` / ``ExpiredProviderToken`` when it does not.
With a real device token and ``"kind": "background"`` it delivers a silent
push (``content-available``); ``"kind": "alert"`` shows a test notification.
"""
import logging
import secrets
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from tutnext.config import IS_WORKERS, settings

router = APIRouter()
logger = logging.getLogger(__name__)


class ApnsProbe(BaseModel):
    deviceToken: str = Field(default="0" * 64, min_length=8)
    kind: Literal["background", "alert"] = "background"
    title: str = "TUTnext APNs probe"
    body: str = "推送链路测试"


def _require_admin(key: str | None) -> None:
    expected = settings.admin_key
    if not expected or not key or not secrets.compare_digest(key, expected):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@router.post("/apns-probe")
async def apns_probe(data: ApnsProbe, response: Response, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    from tutnext.services.push.apns_client import NotificationRequest, PushType, get_apns_client

    if data.kind == "alert":
        message = {"aps": {"alert": {"title": data.title, "body": data.body}, "sound": "default"}}
        push_type = PushType.ALERT
    else:
        message = {"aps": {"content-available": 1}, "updateType": "apnsProbe"}
        push_type = PushType.BACKGROUND
    try:
        result = await get_apns_client().send_notification(
            NotificationRequest(device_token=data.deviceToken, message=message, push_type=push_type)
        )
    except Exception as e:  # noqa: BLE001 - report instead of 500 so the probe is informative
        logger.error("APNs probe error: %s", e)
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return {"ok": False, "error": str(e)}
    logger.info("APNs probe: status=%s description=%s", result.status, result.description)
    return {
        "ok": result.is_successful,
        "status": result.status,
        "description": result.description,
        "notification_id": result.notification_id,
        "runtime": "workers" if IS_WORKERS else "server",
    }
