# tutnext/api/routes/schedule.py
import logging
import traceback

from icalendar import Alarm, Calendar, Event
from fastapi import APIRouter, HTTPException, Response, status as http_status
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel

from tutnext.services.gakuen.client import GakuenAPI, GakuenAPIError
from tutnext.config import HTTP_PROXY, redis

router = APIRouter()


class LaterScheduleRequest(BaseModel):
    username: str
    encryptedPassword: str
    targetDate: Optional[str] = None  # YYYY-MM-DD 格式，省略时默认为明天


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

    gakuen = GakuenAPI(username, password, "https://next.tama.ac.jp", http_proxy=HTTP_PROXY)
    logging.info(f"login: 学籍番号: {username}")
    try:
        # web_login_data = await gakuen.api_login()  # webapi login
        await gakuen.login()
        # if web_login_data["userShkbtKbn"] == "Student":
        #     # webapi data start
        #     api_class_data = await gakuen.class_bulletin()
        #     for i in api_class_data["jgkmDtoList"]:
        #         if i["jugyoName"] in gakuen.class_list:
        #             class_status = await gakuen.class_data_info(i)
        #             chuqian = class_status["attInfoDtoList"][0]
        #             if chuqian["kessekiKaisu"] > 0 and "ゼミ" not in i["jugyoName"]:
        #                 gakuen.class_list[i["jugyoName"]][
        #                     "lessonClass"
        #                 ] += f" 欠席回数 {chuqian['kessekiKaisu']}"
        #             chuqian_text = f"出欠情报: 出席 {chuqian['shusekiKaisu']} 欠席 {chuqian['kessekiKaisu']} 遅刻 {chuqian['chikokKaisu']} 早退 {chuqian['sotaiKaisu']} 公欠 {chuqian['koketsuKaisu']}"
        #             gakuen.class_list[i["jugyoName"]]["lessonTeachers"] += (
        #                 "\n\n" + chuqian_text
        #             )
        #     # webapi data end
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

    # ---- 测试用假数据: 22311330mw ----
    if username == "22311330mw":
        from tutnext.config import JAPAN_TZ
        from datetime import timedelta
        now_jst = datetime.now(JAPAN_TZ)
        today = now_jst.date()
        test_start = now_jst + timedelta(minutes=6)
        test_end = now_jst + timedelta(minutes=3)
        # 午前0時を超えないようにする
        midnight = datetime(today.year, today.month, today.day, 23, 59, tzinfo=JAPAN_TZ)
        if test_end > midnight:
            test_end = midnight
        weekdays_jp = ["月", "火", "水", "木", "金", "土", "日"]
        fake_result = {
            "date_info": {
                "date": today.strftime("%Y/%m/%d"),
                "day_of_week": weekdays_jp[today.weekday()],
            },
            "all_day_events": [],
            "time_table": [
                {
                    "time": f"{test_start.strftime('%H:%M')} - {test_end.strftime('%H:%M')}",
                    "lesson_num": 5,
                    "name": "テスト授業",
                    "teachers": ["テスト先生"],
                    "room": "101",
                }
            ],
        }
        response.status_code = http_status.HTTP_200_OK
        return {"status": True, "data": fake_result}
    # ---- 测试用假数据 END ----

    target_date: Optional[date] = None
    if data.targetDate:
        try:
            target_date = date.fromisoformat(data.targetDate)
        except ValueError:
            response.status_code = http_status.HTTP_400_BAD_REQUEST
            return {"status": False, "message": "targetDate の形式が無効です。YYYY-MM-DD 形式で指定してください。"}

    gakuen = GakuenAPI(
        username, "", "https://next.tama.ac.jp", encryptedPassword, http_proxy=HTTP_PROXY
    )
    try:
        result = await gakuen.get_later_user_schedule(
            user_id=username,
            encrypted_login_password=encryptedPassword,
            target_date=target_date,
        )
        response.status_code = http_status.HTTP_200_OK
        return {"status": True, "data": result}
    except GakuenAPIError as e:
        logging.warning(f"[{username}] get_later_schedule error: {e}")
        response.status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": str(e)}
    except Exception as e:
        logging.error(f"[{username}] get_later_schedule error: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        response.status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"status": False, "message": str(e)}
    finally:
        await gakuen.close()
