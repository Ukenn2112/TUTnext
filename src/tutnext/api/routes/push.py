# tutnext/api/routes/push.py
import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from tutnext.api.auth import reject_unless_caller
from tutnext.core.database import db_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class PushRegistration(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    encryptedPassword: str = Field(min_length=1, max_length=1024)
    deviceToken: str = Field(min_length=1, max_length=256)


class PushUnregister(BaseModel):
    deviceToken: str = Field(min_length=1, max_length=256)


@router.post("/send")
async def send_push(data: PushRegistration, response: Response):
    # 只有持有该学生凭据的调用方才能（覆盖）登记：与已存凭据一致即可，否则由 T-NEXT 登录验证。
    # 否则任何人凭学籍番号就能把他人的推送改到自己设备，或写入错误密码让监测删除该用户。
    if (rejected := await reject_unless_caller(response, data.username, data.encryptedPassword)) is not None:
        return rejected
    try:
        success = await db_manager.upsert_user(data.username, data.encryptedPassword, data.deviceToken)
        if success:
            response.status_code = status.HTTP_200_OK
            return {"status": True, "message": "Data stored and pushed successfully"}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": "Failed to store user data"}
    except Exception:
        logger.exception("push route database error")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": "サーバーエラーが発生しました。しばらくしてから再度お試しください。"}

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
    except Exception:
        logger.exception("push route database error")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": "サーバーエラーが発生しました。しばらくしてから再度お試しください。"}
