# app/routes/kadai.py
import json
import logging
import traceback
import asyncio
from typing import List, Dict, Any
from fastapi import APIRouter, Response, status
from app.services.gakuen_api import GakuenAPI, GakuenAPIError

from config import redis
from app.database import db_manager
from app.services.google_classroom import classroom_api

router = APIRouter()


test_data = [
    {
        "title": "第3回レポート提出",
        "courseId": "110302400",
        "courseName": "キャリア・デザインII C",
        "dueDate": "2025-03-07",
        "dueTime": "23:59",
        "description": "第3章の内容に関するレポートを提出してください。",
        "url": "https://example.com/assignments/1",
    },
    {
        "title": "期末レポート",
        "courseId": "110302401",
        "courseName": "中国ビジネスコミュニケーションII",
        "dueDate": "2025-03-15",
        "dueTime": "23:59",
        "description": "期末レポートを提出してください。",
        "url": "https://example.com/assignments/2",
    },
    {
        "title": "小テスト",
        "courseId": "110302402",
        "courseName": "コンピュータ・サイエンス",
        "dueDate": "2025-04-07",
        "dueTime": "10:00",
        "description": "第3章の内容に関するレポートを提出してください。",
        "url": "https://example.com/assignments/1",
    },
    {
        "title": "期末レポート",
        "courseId": "110302403",
        "courseName": "経営情報特講",
        "dueDate": "2025-03-19",
        "dueTime": "23:59",
        "description": "期末レポートを提出してください。",
        "url": "https://example.com/assignments/2",
    },
    {
        "title": "小テスト",
        "courseId": "110302404",
        "courseName": "消費心理学",
        "dueDate": "2025-03-20",
        "dueTime": "10:00",
        "description": "第7章の内容に関する小テストを受けてください。",
        "url": "https://example.com/assignments/1",
    },
    {
        "title": "小テスト",
        "courseId": "110302405",
        "courseName": "消費心理学",
        "dueDate": "2025-03-15",
        "dueTime": "23:59",
        "description": "第7章の内容に関する小テストを受けてください。",
        "url": "https://example.com/assignments/1",
    },
]


@router.post("")
async def get_kadai(data: dict, response: Response):
    username = data["username"]
    encryptedPassword = data["encryptedPassword"]
    if not username or not encryptedPassword:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {
            "status": False,
            "message": "学籍番号またはパスワードを入力してください",
        }
    try:
        if await redis.exists(f"{username}:kadai"):
            # 如果缓存中存在数据，则直接返回
            redis_kadai_list = await redis.get(f"{username}:kadai")
            kadai_list = json.loads(redis_kadai_list)
        else:
            gakuen = GakuenAPI(
                username, "", "https://next.tama.ac.jp", encryptedPassword
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
            kadai_list: List[Dict[str, Any]] = []
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
                await redis.set(f"{username}:kadai", json.dumps(kadai_list), ex=60) # 缓存用户课题
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
