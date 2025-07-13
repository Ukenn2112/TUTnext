# app/routes/push.py
from fastapi import APIRouter, Response, status
from app.database import db_manager

router = APIRouter()

@router.post("/send")
async def send_push(data: dict, response: Response):
    # 验证数据
    username = data.get("username")
    encryptedPassword = data.get("encryptedPassword")
    deviceToken = data.get("deviceToken")
    
    if not username or not encryptedPassword or not deviceToken:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": False, "message": "Missing required parameters, please provide username, encryptedPassword and deviceToken"}
    
    # 使用数据库管理器处理用户数据
    try:
        success = await db_manager.upsert_user(username, encryptedPassword, deviceToken)
        if success:
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "Data stored and pushed successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to store user data"}
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}

@router.post("/unregister")
async def unregister_push(data: dict, response: Response):
    deviceToken = data.get("deviceToken")
    
    if not deviceToken:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": False, "message": "Missing required parameter deviceToken"}
    
    # 使用数据库管理器删除用户
    try:
        success = await db_manager.delete_user_by_device_token(deviceToken)
        if success:
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "Device unregistered successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to unregister device"}
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}