"""Live Activity token registration and management."""
import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)


class LiveActivityRegistration(BaseModel):
    username: str = Field(min_length=1)
    encryptedPassword: str = Field(min_length=1)
    liveActivityToken: str = Field(min_length=1)
    activityId: str = Field(min_length=1)


class LiveActivityUnregistration(BaseModel):
    username: str = Field(min_length=1)
    activityId: str = Field(min_length=1)


class PushToStartRegistration(BaseModel):
    username: str = Field(min_length=1)
    encryptedPassword: str = Field(min_length=1)
    pushToStartToken: str = Field(min_length=1)


@router.post("/register")
async def register_live_activity(data: LiveActivityRegistration, response: Response):
    """Register a Live Activity push token and schedule transition pushes.

    The token is stored *first*: if T-NEXT is unreachable the registration still
    succeeds and the scheduling is retried in the background.
    """
    from tutnext.services.push.live_activity import (
        enqueue_pending_schedule,
        schedule_live_activity_pushes,
        store_la_token,
    )

    logger.info("LA register: user=%s, activity=%s", data.username, data.activityId)
    try:
        await store_la_token(data.username, data.liveActivityToken, data.activityId)
    except Exception as e:
        logger.error("LA register token store error for %s: %s", data.username, e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": str(e)}

    try:
        count = await schedule_live_activity_pushes(
            username=data.username,
            encrypted_password=data.encryptedPassword,
            la_token=data.liveActivityToken,
            activity_id=data.activityId,
        )
    except Exception as e:
        logger.warning("LA register: schedule fetch failed for %s (will retry): %s", data.username, e)
        try:
            await enqueue_pending_schedule(data.username, data.encryptedPassword)
        except Exception as queue_error:
            logger.error("LA register: retry enqueue failed for %s: %s", data.username, queue_error)
        return {
            "status": True,
            "scheduled": 0,
            "message": "トークンを保存しました。スケジュールの取得に失敗したため、後で再試行します。",
        }

    logger.info("LA register success: user=%s, scheduled=%d", data.username, count)
    return {"status": True, "scheduled": count}


@router.post("/push-to-start")
async def register_push_to_start(data: PushToStartRegistration, response: Response):
    """Register a push-to-start token so the server can start tomorrow's activity."""
    from tutnext.core.database import db_manager
    from tutnext.services.push.live_activity import store_push_to_start_token

    try:
        logger.info("LA push-to-start register: user=%s", data.username)
        # Prefer the credentials already stored for push-registered users; only
        # fall back to the request value when the user is not in the DB.
        fallback_password: str | None = data.encryptedPassword
        try:
            user = await db_manager.get_user(data.username)
            if user and user.get("encryptedpassword"):
                fallback_password = None
        except Exception as e:
            logger.warning("LA push-to-start: user lookup failed for %s: %s", data.username, e)

        await store_push_to_start_token(data.username, data.pushToStartToken, fallback_password)
        return {"status": True}
    except Exception as e:
        logger.error("LA push-to-start error for %s: %s", data.username, e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": str(e)}


@router.post("/unregister")
async def unregister_live_activity(data: LiveActivityUnregistration, response: Response):
    """Remove a Live Activity token. Cleans up transitions if no tokens remain."""
    from tutnext.config import redis

    try:
        logger.info("LA unregister: user=%s, activity=%s", data.username, data.activityId)
        token_key = f"la:tokens:{data.username}"
        await redis.hdel(token_key, data.activityId)  # type: ignore[misc]

        # If no tokens remain, clean up transitions too
        remaining: int = await redis.hlen(token_key)  # type: ignore[misc]
        if remaining == 0:
            await redis.delete(f"la:transitions:{data.username}")
            logger.info("LA unregister: user=%s のトークンなし → transitions 削除", data.username)

        return {"status": True}
    except Exception as e:
        logger.error("LA unregister error for %s: %s", data.username, e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": str(e)}
