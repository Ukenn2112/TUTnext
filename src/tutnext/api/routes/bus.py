# tutnext/api/routes/bus.py
# 巴士时刻表路由
# 缓存策略：
#   Layer 1a — 内存缓存：bus_data.json 在首次请求时加载到模块级变量，此后直接从内存读取
#   Layer 1b — Redis 缓存：schoolbus.html 抓取结果，key=bus:temp_schedule，TTL=600s（10分钟）
#   Layer 1c — Redis 缓存：祝日 API 结果，key=bus:holidays:{YYYY-MM-DD}，TTL 到当天结束
import copy
import io
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup, Tag
from fastapi import APIRouter

from tutnext.config import redis
from tutnext.services.bus_parser import parse_temp_pdf

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Layer 1a：模块级内存缓存 bus_data.json ────────────────────────────────────
# 巴士基准时刻表极少变化，启动后一次加载，长期驻留内存
_BUS_DATA_PATH = Path(__file__).parent.parent.parent / "data" / "bus_data.json"
_bus_data_cache: dict | None = None


def _load_bus_data() -> dict:
    """首次调用时从磁盘加载，之后直接返回内存缓存。"""
    global _bus_data_cache
    if _bus_data_cache is None:
        logger.info("从磁盘加载 bus_data.json 到内存缓存")
        _bus_data_cache = json.loads(_BUS_DATA_PATH.read_text(encoding="utf-8"))
    assert _bus_data_cache is not None
    return _bus_data_cache


def reload_bus_data() -> dict:
    """强制从磁盘重新加载（bus_scraper 更新数据后调用）。"""
    global _bus_data_cache
    logger.info("重新加载 bus_data.json 到内存缓存")
    _bus_data_cache = json.loads(_BUS_DATA_PATH.read_text(encoding="utf-8"))
    assert _bus_data_cache is not None
    return _bus_data_cache


# ── Layer 1b：Redis 缓存 schoolbus.html ──────────────────────────────────────
_SCHOOLBUS_URL = "https://www.tama.ac.jp/guide/campus/schoolbus.html"
_REDIS_KEY_TEMP = "bus:temp_schedule"
_REDIS_TTL_TEMP = 600  # 10 分钟


async def _fetch_schoolbus_html(session: aiohttp.ClientSession | None = None) -> str:
    """
    优先从 Redis 取已缓存的 HTML；缓存未命中则用 aiohttp 拉取，
    结果写入 Redis 并设 600s TTL。
    接受可选的共享 session；若未提供则自行创建。
    """
    # 尝试 Redis 命中
    cached = await redis.get(_REDIS_KEY_TEMP)
    if cached:
        logger.debug("Redis 命中 bus:temp_schedule")
        return cached.decode("utf-8") if isinstance(cached, bytes) else cached

    # 缓存未命中，异步拉取
    logger.info("Redis 未命中 bus:temp_schedule，拉取 schoolbus.html")

    async def _do_fetch(s: aiohttp.ClientSession) -> str:
        async with s.get(_SCHOOLBUS_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.text()

    if session is not None:
        html = await _do_fetch(session)
    else:
        async with aiohttp.ClientSession() as s:
            html = await _do_fetch(s)

    await redis.set(_REDIS_KEY_TEMP, html, ex=_REDIS_TTL_TEMP)
    return html


# ── Layer 1c：Redis 缓存祝日数据 ─────────────────────────────────────────────
_HOLIDAYS_URL = "https://holidays-jp.github.io/api/v1/date.json"


async def _fetch_holidays(today_str: str, session: aiohttp.ClientSession | None = None) -> dict:
    """
    优先从 Redis 取当天的祝日缓存；未命中则拉取并缓存到当天结束。
    key: bus:holidays:{YYYY-MM-DD}，TTL = 当天剩余秒数 + 60s 缓冲。
    接受可选的共享 session；若未提供则自行创建。
    """
    redis_key = f"bus:holidays:{today_str}"

    cached = await redis.get(redis_key)
    if cached:
        logger.debug("Redis 命中 %s", redis_key)
        return json.loads(cached)

    logger.info("Redis 未命中 %s，拉取祝日数据", redis_key)

    async def _do_fetch(s: aiohttp.ClientSession) -> dict:
        async with s.get(_HOLIDAYS_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    if session is not None:
        holidays = await _do_fetch(session)
    else:
        async with aiohttp.ClientSession() as s:
            holidays = await _do_fetch(s)

    # TTL = 当天 00:00 到明天 00:00 的秒数 + 60s 缓冲，确保缓存不会跨天残留
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = int((midnight - now).total_seconds()) + 60

    await redis.set(redis_key, json.dumps(holidays, ensure_ascii=False), ex=ttl)
    return holidays


# ── 临时 PDF 异步下载 ─────────────────────────────────────────────────────────
async def _download_pdf_bytes(url: str, session: aiohttp.ClientSession | None = None) -> bytes:
    """用 aiohttp 异步下载 PDF，返回原始 bytes（由 parse_temp_pdf 处理）。
    接受可选的共享 session；若未提供则自行创建。
    """
    async def _do_fetch(s: aiohttp.ClientSession) -> bytes:
        async with s.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            return await resp.read()

    if session is not None:
        return await _do_fetch(session)
    async with aiohttp.ClientSession() as s:
        return await _do_fetch(s)


@router.get("/app_data")
async def app_schedule():
    # Layer 1a：从内存缓存读取基准时刻表（极少变化，无需网络）
    app_data = copy.deepcopy(_load_bus_data())

    now_day = datetime.now()
    today_ymd = now_day.strftime("%Y-%m-%d")
    today_ja = now_day.strftime("%Y年%m月%d日")

    _messages = []
    pin_messages = None

    # 所有网络请求共用一个 aiohttp session
    async with aiohttp.ClientSession() as session:
        # Layer 1c：获取祝日信息（Redis 缓存到当天结束）
        try:
            holidays_data = await _fetch_holidays(today_ymd, session)
        except Exception as e:
            logger.warning("祝日数据获取失败，跳过：%s", e)
            holidays_data = {}

        if today_ymd in holidays_data:
            _messages.append(
                {
                    "title": f"本日 {now_day.strftime('%Y年%m月%d日')} 祝日授業日のスクールバス时刻表 ",
                    "url": f"https://www.tama.ac.jp/guide/campus/img/bus_{datetime.now().year}holidays.pdf",
                }
            )
            pin_messages = {
                "title": "本日は祝日授業日のスクールバス時刻表らしいです",
                "url": f"https://www.tama.ac.jp/guide/campus/img/bus_{datetime.now().year}holidays.pdf",
            }

        # Layer 1b：获取临时巴士页面（Redis 缓存 10 分钟）
        try:
            html = await _fetch_schoolbus_html(session)
        except Exception as e:
            logger.warning("schoolbus.html 获取失败，跳过临时班次：%s", e)
            html = ""

        if html:
            soup = BeautifulSoup(html, "html.parser")
            if isinstance(_web_div := soup.find("div", class_="rinji"), Tag):
                for web_data in _web_div.find_all("a"):
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
                        start_date = datetime(year, start_month, start_day)
                        end_date = datetime(year, end_month, end_day)
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
                                current_month = end_month
                                start_dt = datetime(year, start_month, start_day)
                                end_dt = datetime(year, end_month, end_day)
                                date_range.extend(
                                    (start_dt + timedelta(days=i)).strftime("%Y年%m月%d日")
                                    for i in range((end_dt - start_dt).days + 1)
                                )
                            else:
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
                                    current_month = month
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

                        date_range = [datetime(year, month, first_day).strftime("%Y年%m月%d日")]

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
                        date_range = [datetime(year, month, day).strftime("%Y年%m月%d日")]

                    href = str(web_data.get("href") or "")
                    if today_ja in date_range:
                        pin_messages = {
                            "title": "本日はスクールバス臨時ダイヤらしいです",
                            "url": "https://www.tama.ac.jp/guide/campus/" + href,
                        }
                    _messages.append(
                        {
                            "title": title,
                            "url": "https://www.tama.ac.jp/guide/campus/" + href,
                        }
                    )

        # 如果今天有临时/祝日班次，异步下载 PDF 并覆盖对应时刻表
        if pin_messages:
            try:
                pdf_bytes = await _download_pdf_bytes(pin_messages["url"], session)
                pin_data = parse_temp_pdf(pdf_bytes)
            except Exception as e:
                logger.warning("临时 PDF 解析失败：%s", e)
            else:
                # 判断当天星期几，并选择对应的时刻表
                today_weekday = now_day.weekday()
                # 0=周一, 1=周二, 2=周三, 3=周四, 4=周五, 5=周六, 6=周日
                if today_weekday in (5, 6):  # 周六或周日
                    app_data["saturday"] = pin_data
                elif today_weekday == 2:  # 周三
                    app_data["wednesday"] = pin_data
                else:  # 工作日（周一、二、四、五）
                    app_data["weekday"] = pin_data

    return {"messages": _messages, "data": app_data, "pin": pin_messages}
