# tutnext/api/routes/schedule.py
import asyncio
import logging
import traceback

from icalendar import Alarm, Calendar, Event
from fastapi import APIRouter, HTTPException, Response, status as http_status
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel

from tutnext.services.gakuen.client import GakuenAPI, GakuenAPIError
from tutnext.config import HTTP_PROXY, redis
from tutnext.services.gakuen.session_manager import get_session_manager

router = APIRouter()


class LaterScheduleRequest(BaseModel):
    username: str
    encryptedPassword: str
    targetDate: Optional[str] = None  # YYYY-MM-DD 格式，省略时默认为明天


class ClassBulletinData(BaseModel):
    kaikoNendo: int = 0
    gakkiNo: Literal[0, 1, 2] = 0


class ClassBulletinRequest(BaseModel):
    loginUserId: str
    encryptedLoginPassword: str
    plainLoginPassword: Optional[str] = None
    productCd: Optional[str] = None
    subProductCd: Optional[str] = None
    langCd: Optional[str] = None
    data: ClassBulletinData = ClassBulletinData()


@router.get("")
async def send_schedule(username=None, password=None):
    if not username or not password:
        raise HTTPException(
            status_code=400, detail="学籍番号またはパスワードを入力してください"
        )

    # 缓存策略：优先从 Redis 读取已生成的 iCal 内容，命中时直接返回，
    # 避免每次请求都重复登录大学系统并拉取两个月的课程数据（TTL 300秒）
    cache_key = f"schedule:ical:{username}"
    cached = await redis.get(cache_key)
    if cached:
        logging.info(f"cache hit: 学籍番号: {username}")
        return Response(content=cached, media_type="text/calendar")

    async with get_session_manager().lock_only(username):
        gakuen = GakuenAPI(username, password, "https://next.tama.ac.jp", http_proxy=HTTP_PROXY)
        logging.info(f"login: 学籍番号: {username}")
        try:
            await gakuen.login()
            cal = Calendar()
            cal.add("prodid", "-//Ukenn//TamaSchedule//")
            cal.add("version", "2.0")
            cal.add("name", "多摩大スケジュール")
            cal.add("x-wr-calname", "多摩大スケジュール")
            cal.add("x-wr-timezone", "Etc/GMT")
            now_year = datetime.now().year
            now_month = datetime.now().month
            for month in range(now_month, now_month + 2):
                if month > 12:
                    now_year += 1
                    month -= 12
                course_list = await gakuen.month_data(now_year, month)
                for course in course_list:
                    event = Event()
                    if not course["title"]:
                        continue
                    event.add("summary", course["title"])
                    event.add("dtstart", course["start"])
                    event.add("dtend", course["end"])
                    event.add("dtstamp", datetime.now())
                    alarm1 = Alarm()
                    alarm1.add("action", "DISPLAY")
                    t = course["start"] - timedelta(days=1)
                    alarm1.add(
                        "trigger",
                        datetime(t.year, t.month, t.day, 12, 0, 0, tzinfo=timezone.utc),
                    )
                    event.add_component(alarm1)
                    if not course["allDay"] and "room" in course:
                        event.add("location", course["room"])
                        event.add("description", course["teacher"])
                        alarm2 = Alarm()
                        alarm2.add("action", "AUDIO")
                        alarm2.add("trigger", timedelta(minutes=-10))
                        event.add_component(alarm2)
                    cal.add_component(event)
            katai_list = await gakuen.kadai_data()
            for katai in katai_list:
                event = Event()
                event.add("summary", "未完成の課題!!!" + katai["title"])
                event.add("dtstart", katai["deadline"] - timedelta(minutes=30))
                event.add("dtend", katai["deadline"])
                event.add("dtstamp", datetime.now())
                event.add("location", katai["from"])
                event.add("description", katai["date"] + " からの課題")
                alarm = Alarm()
                alarm.add("action", "AUDIO")
                alarm.add("trigger", timedelta(minutes=-20))
                event.add_component(alarm)
                cal.add_component(event)
            ical_content = cal.to_ical()
            await redis.set(cache_key, ical_content, ex=300)
            return Response(content=ical_content, media_type="text/calendar")
        except GakuenAPIError as e:
            logging.warning(f"[{username}] error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logging.error(f"[{username}] error: {e}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            await gakuen.close()


@router.post("/later")
async def get_later_schedule(data: LaterScheduleRequest, response: Response):
    username = data.username
    encryptedPassword = data.encryptedPassword

    # # ---- 测试用假数据: 22311330mw ----
    # if username == "22311330mw":
    #     from tutnext.config import JAPAN_TZ
    #     from datetime import timedelta
    #     now_jst = datetime.now(JAPAN_TZ)
    #     today = now_jst.date()
    #     # 1限目: +7分後開始、3分間
    #     test_start1 = now_jst + timedelta(minutes=7)
    #     test_end1 = test_start1 + timedelta(minutes=3)
    #     # 2限目: 1限目終了の12分後に開始（昼休みテスト）、3分間
    #     test_start2 = test_end1 + timedelta(minutes=12)
    #     test_end2 = test_start2 + timedelta(minutes=3)
    #     # 午前0時を超えないようにする
    #     midnight = datetime(today.year, today.month, today.day, 23, 59, tzinfo=JAPAN_TZ)
    #     if test_end2 > midnight:
    #         test_end2 = midnight
    #     weekdays_jp = ["月", "火", "水", "木", "金", "土", "日"]
    #     fake_result = {
    #         "date_info": {
    #             "date": today.strftime("%Y/%m/%d"),
    #             "day_of_week": weekdays_jp[today.weekday()],
    #         },
    #         "all_day_events": [],
    #         "time_table": [
    #             {
    #                 "time": f"{test_start1.strftime('%H:%M')} - {test_end1.strftime('%H:%M')}",
    #                 "lesson_num": 3,
    #                 "name": "情報工学概論",
    #                 "teachers": ["中村 教授"],
    #                 "room": "101",
    #             },
    #             {
    #                 "time": f"{test_start2.strftime('%H:%M')} - {test_end2.strftime('%H:%M')}",
    #                 "lesson_num": 4,
    #                 "name": "データサイエンス入門",
    #                 "teachers": ["田中 准教授"],
    #                 "room": "305",
    #                 "previous_room": "242",
    #             },
    #         ],
    #     }
    #     response.status_code = http_status.HTTP_200_OK
    #     return {"status": True, "data": fake_result}
    # # ---- 测试用假数据 END ----

    target_date: Optional[date] = None
    if data.targetDate:
        try:
            target_date = date.fromisoformat(data.targetDate)
        except ValueError:
            response.status_code = http_status.HTTP_400_BAD_REQUEST
            return {"status": False, "message": "targetDate の形式が無効です。YYYY-MM-DD 形式で指定してください。"}

    for attempt in range(2):
        try:
            if attempt > 0:
                await get_session_manager().invalidate(username)
                await asyncio.sleep(2)
                logging.info(f"[{username}] get_later_schedule: 並行ログイン競合のためリトライ中...")
            async with get_session_manager().acquire(username, encryptedPassword) as gakuen:
                result = await gakuen.get_later_user_schedule(
                    user_id=username,
                    encrypted_login_password=encryptedPassword,
                    target_date=target_date,
                    skip_login=True,
                )
            # 時間表記を "10:40-12:10" → "10:40 - 12:10" に変換
            for entry in result.get("time_table", []):
                if "time" in entry:
                    entry["time"] = entry["time"].strip().replace("-", " - ")
            response.status_code = http_status.HTTP_200_OK
            return {"status": True, "data": result}
        except GakuenAPIError as e:
            if "他端末で同時に実行" in str(e) and attempt == 0:
                logging.warning(f"[{username}] get_later_schedule: 並行ログイン競合検出、2秒後にリトライ")
                continue
            logging.warning(f"[{username}] get_later_schedule error: {e}")
            response.status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": str(e)}
        except Exception as e:
            logging.error(f"[{username}] get_later_schedule error: {e}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            response.status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"status": False, "message": str(e)}


@router.post("/class_bulletin")
async def get_class_bulletin_with_room(data: ClassBulletinRequest, response: Response):
    """class_bulletin のプロキシ。Redis から教室情報を補完して返す。"""
    username = data.loginUserId
    encrypted_password = data.encryptedLoginPassword

    # 1. class_bulletin を呼び出す
    gakuen = GakuenAPI(
        username, "", "https://next.tama.ac.jp",
        encrypted_login_password=encrypted_password,
        http_proxy=HTTP_PROXY,
    )
    error_response = {
        "responseCode": 500,
        "statusDto": {"success": False, "messageList": []},
        "data": None,
        "langCd": "ja",
    }
    try:
        gakuen._state.api_is_logged_in = True
        result = await gakuen.class_bulletin(data.data.kaikoNendo, data.data.gakkiNo)
    except GakuenAPIError as e:
        logging.warning(f"[{username}] class_bulletin error: {e}")
        response.status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR
        error_response["statusDto"]["messageList"] = [str(e)]
        return error_response
    except Exception as e:
        logging.error(f"[{username}] class_bulletin error: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        response.status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR
        error_response["statusDto"]["messageList"] = [str(e)]
        return error_response
    finally:
        await gakuen.close()

    # 2. Redis から教室情報を補完
    jgkm_list = result.get("jgkmDtoList", [])
    missing_rooms: list[str] = []
    for item in jgkm_list:
        jugyo_name = item.get("jugyoName", "")
        if not item.get("kyostName") and jugyo_name:
            cached = await redis.get(f"room:{jugyo_name}")
            if cached:
                item["kyostName"] = cached if isinstance(cached, str) else cached.decode()
            else:
                missing_rooms.append(jugyo_name)

    # 3. キャッシュミスがある場合、1週間分のスケジュールを取得して Redis を埋める
    if missing_rooms:
        try:
            async with get_session_manager().acquire(username, encrypted_password) as gakuen_mobile:
                today = date.today()
                for offset in range(7):
                    target = today + timedelta(days=offset)
                    await gakuen_mobile.get_later_user_schedule(
                        target_date=target, skip_login=True,
                    )
            # 再度 Redis から補完
            for item in jgkm_list:
                if not item.get("kyostName"):
                    cached = await redis.get(f"room:{item.get('jugyoName', '')}")
                    if cached:
                        item["kyostName"] = cached if isinstance(cached, str) else cached.decode()
        except Exception as e:
            logging.warning(f"[{username}] room cache population failed: {e}")

    return {
        "responseCode": 200,
        "statusDto": {"success": True, "messageList": []},
        "data": result,
        "langCd": "ja",
    }
