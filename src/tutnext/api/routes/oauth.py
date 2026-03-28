# tutnext/api/routes/oauth.py
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from tutnext.core.database import db_manager
from tutnext.config import redis
from tutnext.services.google_classroom import classroom_api

router = APIRouter()


class OAuthTokens(BaseModel):
    username: str
    access_token: str
    refresh_token: str


class OAuthRevoke(BaseModel):
    username: str


class OAuthStatus(BaseModel):
    username: str


@router.post("/tokens")
async def receive_tokens(data: OAuthTokens, response: Response):
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
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}

@router.post("/revoke")
async def revoke_tokens(data: OAuthRevoke, response: Response):
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
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}

@router.post("/status")
async def check_user_status(data: OAuthStatus, response: Response):
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
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}
