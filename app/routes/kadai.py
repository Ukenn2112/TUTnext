# app/routes/kadai.py
import traceback
from fastapi import APIRouter, Response, status
from app.services.gakuen_api import GakuenAPI, GakuenAPIError
import logging

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
        return {
            "status": False,
            "message": "学籍番号またはパスワードを入力してください",
        }
    try:
        gakuen = GakuenAPI(username, "", "https://next.tama.ac.jp", encryptedPassword)
        kadai_list = await gakuen.get_user_kadai()
        response.status_code = status.HTTP_200_OK
        return {"status": True, "data": kadai_list}
    except GakuenAPIError as e:
        logging.warning(f"[{username}] error: {e}")
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {
            "status": False,
            "message": str(e),
        }
    except Exception as e:
        logging.error(f"[{username}] error: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {
            "status": False,
            "message": str(e),
        }
    finally:
        await gakuen.close()
