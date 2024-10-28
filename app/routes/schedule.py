# app/routes/schedule.py
import logging
import traceback

from icalendar import Alarm, Calendar, Event
from fastapi import APIRouter, HTTPException, Response
from datetime import datetime, timedelta, timezone

from app.services.gakuen_api import GakuenAPI, GakuenAPIError

router = APIRouter()


@router.get("")
async def send_schedule(username: str = None, password: str = None):
    if not username or not password:
        raise HTTPException(
            status_code=400, detail="学籍番号またはパスワードを入力してください"
        )
    gakuen = GakuenAPI(username, password, "https://next.tama.ac.jp")
    logging.info(f"login: 学籍番号: {username}")
    try:
        await gakuen.webapi_login()  # webapi login
        await gakuen.login()
        # webapi data start
        api_class_data = await gakuen.class_bulletin()
        for i in api_class_data["jgkmDtoList"]:
            if i["jugyoName"] in gakuen.class_list:
                class_status = await gakuen.class_data_info(i)
                chuqian = class_status["attInfoDtoList"][0]
                if chuqian["kessekiKaisu"] > 0:
                    gakuen.class_list[i["jugyoName"]][
                        "lessonClass"
                    ] += f" 欠席回数 {chuqian['kessekiKaisu']}"
                chuqian_text = f"出欠情报: 出席 {chuqian['shusekiKaisu']} 欠席 {chuqian['kessekiKaisu']} 遅刻 {chuqian['chikokKaisu']} 早退 {chuqian['sotaiKaisu']} 公欠 {chuqian['koketsuKaisu']}"
                gakuen.class_list[i["jugyoName"]]["lessonTeachers"] += (
                    "\n\n" + chuqian_text
                )
        # webapi data end
        cal = Calendar()
        cal.add("prodid", "-//Ukenn//TamaSchedule//")
        cal.add("version", "2.0")
        cal.add("name", "多摩大スケジュール")
        cal.add("x-wr-calname", "多摩大スケジュール")
        cal.add("x-wr-timezone", "Etc/GMT")
        now_month = datetime.now().month
        for month in range(now_month, now_month + 2):
            if month > 12:
                month -= 12
            course_list = await gakuen.month_data(month)
            for course in course_list:
                event = Event()
                if not course["title"]: continue
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
        await gakuen.close()
        return Response(content=cal.to_ical(), media_type="text/calendar")
    except GakuenAPIError as e:
        logging.warning(f"[{username}] error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logging.error(f"[{username}] error: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await gakuen.close()
