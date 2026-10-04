# tutnext/api/routes/oauth.py
import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from tutnext.api.auth import reject_unless_caller
from tutnext.config import redis
from tutnext.core.database import db_manager
from tutnext.services.google_classroom import classroom_api

router = APIRouter()
logger = logging.getLogger(__name__)


# encryptedPassword is optional only because current app builds do not send it
# (GoogleOAuthService.swift). When present it is verified; once the app sends it
# everywhere, make it required (see docs §9).
class OAuthTokens(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    access_token: str = Field(min_length=1, max_length=4096)
    refresh_token: str = Field(min_length=1, max_length=4096)
    encryptedPassword: str | None = Field(default=None, max_length=1024)


class OAuthRevoke(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    encryptedPassword: str | None = Field(default=None, max_length=1024)


class OAuthStatus(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    encryptedPassword: str | None = Field(default=None, max_length=1024)


async def _reject(data, response: Response) -> dict | None:
    if data.encryptedPassword is None:
        logger.info("oauth %s without credential (legacy app build)", data.username)
        return None
    return await reject_unless_caller(response, data.username, data.encryptedPassword)


@router.post("/tokens")
async def receive_tokens(data: OAuthTokens, response: Response):
    if (rejected := await _reject(data, response)) is not None:
        return rejected
    # 使用数据库管理器处理用户数据
    try:
        success = await db_manager.upsert_user_tokens(data.username, data.access_token, data.refresh_token)
        if success:
            # 清除 f"{username}:kadai" 的缓存
            await redis.delete(f"{data.username}:kadai")
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "User tokens stored successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to store user tokens"}
    except Exception:
        logger.exception("oauth route error")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": "サーバーエラーが発生しました。しばらくしてから再度お試しください。"}

@router.post("/revoke")
async def revoke_tokens(data: OAuthRevoke, response: Response):
    if (rejected := await _reject(data, response)) is not None:
        return rejected
    # 使用数据库管理器撤销用户令牌
    try:
        success = await classroom_api.revoke_user_authorization(data.username)
        if success["success"]:
            # 清除 f"{username}:kadai" 的缓存
            await redis.delete(f"{data.username}:kadai")
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "User tokens revoked successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to revoke user tokens"}
    except Exception:
        logger.exception("oauth route error")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": "サーバーエラーが発生しました。しばらくしてから再度お試しください。"}

@router.post("/status")
async def check_user_status(data: OAuthStatus, response: Response):
    if (rejected := await _reject(data, response)) is not None:
        return rejected
    # 使用数据库管理器检查用户状态
    try:
        user_status = await db_manager.get_user_tokens_status(data.username)
        if user_status is not None:
            response.status_code = status.HTTP_200_OK
            if user_status["token_status"] == "active":
                return {"status": True, "message": "User status retrieved successfully", "data": user_status}
            else:
                return {"status": False, "message": "User tokens are not active"}
        else:
            response.status_code = status.HTTP_404_NOT_FOUND
            return {"status": False, "message": "User not found"}
    except Exception:
        logger.exception("oauth route error")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": "サーバーエラーが発生しました。しばらくしてから再度お試しください。"}
