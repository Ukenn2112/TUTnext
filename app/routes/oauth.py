# app/routes/oauth.py
from fastapi import APIRouter, Response, status
from app.database import db_manager
from config import redis
from app.services.google_classroom import classroom_api

router = APIRouter()

@router.post("/tokens")
async def receive_tokens(data: dict, response: Response):
    # 验证数据
    username = data.get("username")
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    
    if not username or not access_token or not refresh_token:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": False, "message": "Missing required parameters, please provide username, access_token and refresh_token"}
    
    # 使用数据库管理器处理用户数据
    try:
        success = await db_manager.upsert_user_tokens(username, access_token, refresh_token)
        if success:
            # 清除 f"{username}:kadai" 的缓存
            await redis.delete(f"{username}:kadai")
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "User tokens stored successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to store user tokens"}
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}

@router.post("/revoke")
async def revoke_tokens(data: dict, response: Response):
    username = data.get("username")
    
    if not username:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": False, "message": "Missing required parameter username"}
    
    # 使用数据库管理器撤销用户令牌
    try:
        success = await classroom_api.revoke_user_authorization(username)
        if success["success"]:
            # 清除 f"{username}:kadai" 的缓存
            await redis.delete(f"{username}:kadai")
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "User tokens revoked successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to revoke user tokens"}
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}

@router.post("/status")
async def check_user_status(data: dict, response: Response):
    username = data.get("username")
    
    if not username:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": False, "message": "Missing required parameter username"}
    
    # 使用数据库管理器检查用户状态
    try:
        user_status = await db_manager.get_user_tokens_status(username)
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