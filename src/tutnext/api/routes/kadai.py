# tutnext/api/routes/kadai.py
import json
import logging
import traceback
import asyncio
from typing import List, Dict, Any
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from tutnext.services.gakuen.client import GakuenAPI, GakuenAPIError

from tutnext.config import redis, HTTP_PROXY
from tutnext.core.database import db_manager
from tutnext.services.google_classroom import classroom_api

router = APIRouter()


class KadaiRequest(BaseModel):
    username: str
    encryptedPassword: str


@router.post("")
async def get_kadai(data: KadaiRequest, response: Response):
    username = data.username
    encryptedPassword = data.encryptedPassword
    try:
        kadai_list: List[Dict[str, Any]]
        if await redis.exists(f"{username}:kadai"):
            # 如果缓存中存在数据，则直接返回
            redis_kadai_list = await redis.get(f"{username}:kadai")
            kadai_list = json.loads(redis_kadai_list)
        else:
            gakuen = GakuenAPI(
                username, "", "https://next.tama.ac.jp", encryptedPassword, http_proxy=HTTP_PROXY
            )
            tasks = []
            tasks.append(gakuen.get_user_kadai())

            # 检查是否有Google Classroom令牌，如果有则添加获取任务
            has_classroom_tokens = await db_manager.get_user_tokens(username)
            if has_classroom_tokens:
                tasks.append(classroom_api.get_user_assignments(username))
            # 并行执行所有任务
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # 处理结果
            kadai_list = []
            # 处理学校系统课题结果（第一个任务）
            if len(results) > 0:
                gakuen_result = results[0]
                if isinstance(gakuen_result, Exception):
                    logging.error(f"获取学校系统课题失败: {gakuen_result}")
                elif isinstance(gakuen_result, list):
                    kadai_list.extend(gakuen_result)

            # 处理Google Classroom课题结果（第二个任务，如果存在）
            if len(results) > 1:
                classroom_result = results[1]
                if isinstance(classroom_result, Exception):
                    logging.error(f"获取Google Classroom课题失败: {classroom_result}")
                elif isinstance(classroom_result, list):
                    kadai_list.extend(classroom_result)
            if kadai_list:
                await redis.set(f"{username}:kadai", json.dumps(kadai_list), ex=120) # 缓存用户课题
        response.status_code = status.HTTP_200_OK
        return {"status": True, "data": kadai_list}
    except GakuenAPIError as e:
        logging.warning(f"[{username}] error: {e}")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {
            "status": False,
            "message": str(e),
        }
    except Exception as e:
        logging.error(f"[{username}] error: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {
            "status": False,
            "message": str(e),
        }
    finally:
        if "gakuen" in locals():
            await gakuen.close()
