# tutnext/api/routes/push.py
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from tutnext.core.database import db_manager

router = APIRouter()


class PushRegistration(BaseModel):
    username: str
    encryptedPassword: str
    deviceToken: str


class PushUnregister(BaseModel):
    deviceToken: str


@router.post("/send")
async def send_push(data: PushRegistration, response: Response):
    # 使用数据库管理器处理用户数据
    try:
        success = await db_manager.upsert_user(data.username, data.encryptedPassword, data.deviceToken)
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
async def unregister_push(data: PushUnregister, response: Response):
    # 使用数据库管理器删除用户
    try:
        success = await db_manager.delete_user_by_device_token(data.deviceToken)
        if success:
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "Device unregistered successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to unregister device"}
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": f"Database error: {str(e)}"}
