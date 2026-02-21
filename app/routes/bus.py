# app/routes/bus.py
from requests import get
from bs4 import BeautifulSoup, Tag
from fastapi import APIRouter
from datetime import datetime, timedelta
import re
import json

from app.utils.parse_bus_temp import parse_temp_pdf

router = APIRouter()


@router.get("/app_data")
async def app_schedule():
    app_data = json.loads(open("app/data/bus_data.json", "r", encoding="utf-8").read())
    # app_data = {'title': '2025年度 基準時刻表', 'notes': {'◎': '经永山站到达学校的班次', '*': '经永山站到达圣迹樱丘站的班次', 'M': '大学生专用微型巴士（仅周一～周四）', 'C': '中学生专用班次', 'K': '高中生专用班次'}, 'weekday': {'fromSeisekiToSchool': [...], 'fromNagayamaToSchool': [...], 'fromSchoolToNagayama': [...], 'fromSchoolToSeiseki': [...]}, 'saturday': {'fromSeisekiToSchool': [...], 'fromNagayamaToSchool': [...], 'fromSchoolToNagayama': [...], 'fromSchoolToSeiseki': [...]}, 'wednesday': {'fromSeisekiToSchool': [...], 'fromNagayamaToSchool': [...], 'fromSchoolToNagayama': [...], 'fromSchoolToSeiseki': [...]}}

    # 从学校主页上获取临时巴士数据
    web_data = get("https://www.tama.ac.jp/guide/campus/schoolbus.html")
    soup = BeautifulSoup(web_data.text, "html.parser")
    _messages = []
    pin_messages = None
    # 获取祝日信息
    holidays_data = get("https://holidays-jp.github.io/api/v1/date.json").json()
    now_day = datetime.now()
    if now_day.strftime("%Y-%m-%d") in holidays_data:
        _messages.append(
            {
                "title": f"本日 {now_day.strftime('%Y年%m月%d日')} 祝日授業日のスクールバス时刻表 ",
                "url": "https://www.tama.ac.jp/guide/campus/img/bus_2025holidays.pdf",
            }
        )
        pin_messages = {
            "title": f"本日は祝日授業日のスクールバス時刻表らしいです",
            "url": "https://www.tama.ac.jp/guide/campus/img/bus_2025holidays.pdf",
        }
    # 获取临时巴士信息
    if isinstance(_web_data := soup.find("div", class_="rinji"), Tag):
        for web_data in _web_data.find_all("a"):
            title = web_data.text.strip()
            date_range = []
            # 检测格式：2025年7月14日(月)～17日(木) 或 2025年7月14日(月)～8月17日(木)
            if match := re.match(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日\((.)\)～(?:(\d{1,2})月)?(\d{1,2})日\((.)\)",
                title,
            ):
                year = int(match.group(1))
                start_month = int(match.group(2))
                start_day = int(match.group(3))
                end_month = int(match.group(5)) if match.group(5) else start_month
                end_day = int(match.group(6))
                # 计算开始和结束日期
                start_date = datetime(year, start_month, start_day)
                end_date = datetime(year, end_month, end_day)
                # 生成日期范围
                date_range = [
                    (start_date + timedelta(days=i)).strftime("%Y年%m月%d日")
                    for i in range((end_date - start_date).days + 1)
                ]
            # 检测混合格式：2026年2月9日(月)、10日(火)、16日(月)～20日(金)、24日(火)～27日(金)
            elif match := re.match(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日\(.\)"
                r"((?:、(?:(?:\d{1,2})月)?(?:\d{1,2})日\(.\)(?:～(?:(?:\d{1,2})月)?(?:\d{1,2})日\(.\))?)+)",
                title,
            ):
                year = int(match.group(1))
                current_month = int(match.group(2))
                first_day = int(match.group(3))
                tail = match.group(4)

                date_range = [
                    datetime(year, current_month, first_day).strftime("%Y年%m月%d日")
                ]

                for seg in (s for s in tail.split("、") if s):
                    # 判断是否为区间段：dd日(曜)～dd日(曜) 或跨月
                    range_match = re.match(
                        r"(?:(\d{1,2})月)?(\d{1,2})日\(.\)～(?:(\d{1,2})月)?(\d{1,2})日\(.\)",
                        seg,
                    )
                    if range_match:
                        start_month = (
                            int(range_match.group(1))
                            if range_match.group(1)
                            else current_month
                        )
                        start_day = int(range_match.group(2))
                        end_month = (
                            int(range_match.group(3))
                            if range_match.group(3)
                            else start_month
                        )
                        end_day = int(range_match.group(4))
                        current_month = end_month  # 更新月份上下文
                        start_dt = datetime(year, start_month, start_day)
                        end_dt = datetime(year, end_month, end_day)
                        date_range.extend(
                            (start_dt + timedelta(days=i)).strftime("%Y年%m月%d日")
                            for i in range((end_dt - start_dt).days + 1)
                        )
                    else:
                        # 单一日期段：dd日(曜) 或 mm月dd日(曜)
                        single_match = re.match(
                            r"(?:(\d{1,2})月)?(\d{1,2})日\(.\)", seg
                        )
                        if single_match:
                            month = (
                                int(single_match.group(1))
                                if single_match.group(1)
                                else current_month
                            )
                            day = int(single_match.group(2))
                            current_month = month  # 更新月份上下文
                            date_range.append(
                                datetime(year, month, day).strftime("%Y年%m月%d日")
                            )
            # 检测格式：2025年7月11日(金)、18日(金)、25日(金)...
            elif match := re.match(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日\((.)\)((?:、(\d{1,2})日\((.)\))+)",
                title,
            ):
                year = int(match.group(1))
                month = int(match.group(2))
                first_day = int(match.group(3))
                additional_days_str = match.group(5)

                # 解析第一个日期
                date_range = [datetime(year, month, first_day).strftime("%Y年%m月%d日")]

                # 解析后续的日期
                additional_matches = re.findall(
                    r"(\d{1,2})日\((.)\)", additional_days_str
                )
                for day_str, _ in additional_matches:
                    day = int(day_str)
                    date_range.append(
                        datetime(year, month, day).strftime("%Y年%m月%d日")
                    )
            # 检测格式：2025年7月11日(金)
            elif match := re.match(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日\((.)\)",
                title,
            ):
                year = int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3))
                # 计算日期
                date_range = [datetime(year, month, day).strftime("%Y年%m月%d日")]
            if now_day.strftime("%Y年%m月%d日") in date_range:
                # 如果今天在日期范围内，添加消息
                pin_messages = {
                    "title": f"本日はスクールバス臨時ダイヤらしいです",
                    "url": "https://www.tama.ac.jp/guide/campus/"
                    + web_data.get("href"),
                }
            _messages.append(
                {
                    "title": title,
                    "url": "https://www.tama.ac.jp/guide/campus/"
                    + web_data.get("href"),
                }
            )
    if pin_messages:
        try:
            pin_data = parse_temp_pdf(pin_messages["url"])
        except Exception as e:
            pass
        else:
            # 判断当天星期几，并选择对应的时刻表
            today_weekday = datetime.now().weekday()
            # 0=周一, 1=周二, 2=周三, 3=周四, 4=周五, 5=周六, 6=周日
            if today_weekday == 6 or today_weekday == 5:  # 周六或周日
                app_data["saturday"] = pin_data
            elif today_weekday == 2:  # 周三
                app_data["wednesday"] = pin_data
            else:  # 工作日
                app_data["weekday"] = pin_data
    return {"messages": _messages, "data": app_data, "pin": pin_messages}
