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


@router.post("/register")
async def register_live_activity(data: LiveActivityRegistration, response: Response):
    """Register a Live Activity push token and schedule transition pushes."""
    from tutnext.services.push.live_activity import schedule_live_activity_pushes

    try:
        logger.info("LA register: user=%s, activity=%s", data.username, data.activityId)
        count = await schedule_live_activity_pushes(
            username=data.username,
            encrypted_password=data.encryptedPassword,
            la_token=data.liveActivityToken,
            activity_id=data.activityId,
        )
        logger.info("LA register success: user=%s, scheduled=%d", data.username, count)
        return {"status": True, "scheduled": count}
    except Exception as e:
        logger.error("LA register error for %s: %s", data.username, e)
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
