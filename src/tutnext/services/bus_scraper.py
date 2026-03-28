"""
bus_scraper.py — 巴士时刻表自动更新服务

功能：
  1. 抓取 schoolbus.html，找到「通常バスダイヤ」PDF 链接
  2. 用 aiohttp 异步下载 PDF bytes
  3. 调用 scripts/parse_bus_data.py 的解析逻辑（移植到本文件中的异步版本）
  4. 与现有 bus_data.json 比较；若有变更则写入文件并刷新内存缓存

调用方式：
    async def update_bus_schedule() -> bool
    返回 True 表示数据已更新，False 表示无变化或出错。
"""

import asyncio
import io
import json
import logging
import re
import sys
from pathlib import Path

import aiohttp
import pdfplumber

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────
_SCHOOLBUS_URL = "https://www.tama.ac.jp/guide/campus/schoolbus.html"
_BUS_DATA_PATH = Path(__file__).parent.parent / "data" / "bus_data.json"

# 标准 PDF 文件名模式：bus_YYYY.pdf（平日）和 bus_YYYYwed.pdf（水曜）
_PDF_WEEKDAY_RE = re.compile(r"img/bus_(\d{4})\.pdf$")
_PDF_WED_RE = re.compile(r"img/bus_(\d{4})wed\.pdf$")


# ── 异步 HTTP 工具 ─────────────────────────────────────────────────────────────
async def _get_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    """异步 GET 并返回响应体 bytes。"""
    async with session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        resp.raise_for_status()
        return await resp.read()


# ── PDF 解析逻辑（来自 scripts/parse_bus_data.py，接受 bytes）─────────────────
# 保持与原始脚本完全相同的解析算法，仅将 load_pdf_from_url 替换为 bytes 入参。

_GRADE_MAP = {"学年指定なし": None, "中学生": "C", "高校生": "K"}


def _build_grade_lookup(table: list) -> dict:
    """返回 {(hour, minute): "C"/"K"/None}"""
    lookup = {}
    current_grade = None
    for row in table[1:]:
        time_str, grade_str = row[0], row[1]
        if grade_str is not None:
            current_grade = _GRADE_MAP.get(grade_str, None)
        if time_str and ":" in time_str:
            h, m = time_str.split(":")
            lookup[(int(h), int(m))] = current_grade
    return lookup


def _parse_cell(cell, hour: int, grade_lookup: dict) -> list:
    if not cell or not cell.strip():
        return []
    results = []
    for token in cell.split():
        num_match = re.match(r"^(\d+)(.*)", token.strip())
        if not num_match:
            continue
        minute = int(num_match.group(1))
        suffix = num_match.group(2)
        special_note: str | None
        if "M" in suffix and "*" in suffix:
            special_note = "*M"
        elif "M" in suffix:
            special_note = "M"
        elif "◎" in suffix:
            special_note = "◎"
        elif "*" in suffix:
            special_note = "*"
        else:
            special_note = grade_lookup.get((hour, minute))
        results.append(
            {
                "hour": hour,
                "minute": minute,
                "isSpecial": special_note is not None,
                "specialNote": special_note,
            }
        )
    return results


def _build_hourly(rows_by_hour: dict) -> list:
    return [
        {"hour": hour, "times": rows_by_hour[hour]}
        for hour in sorted(rows_by_hour)
        if rows_by_hour[hour]
    ]


def _parse_main_table(table: list, grade_seiseki: dict, grade_nagayama: dict) -> dict:
    ss, ns, sn, se = {}, {}, {}, {}
    for row in table[2:]:
        if len(row) < 5:
            continue
        hour_cell = row[2]
        if not hour_cell or not hour_cell.strip().isdigit():
            continue
        hour = int(hour_cell.strip())
        ss[hour] = _parse_cell(row[0], hour, grade_seiseki)
        ns[hour] = _parse_cell(row[1], hour, grade_nagayama)
        sn[hour] = _parse_cell(row[3], hour, {})
        se[hour] = _parse_cell(row[4], hour, {})

    # 派生班次：fromSchoolToSeiseki 中 * 或 *M 班次 → 补入 fromSchoolToNagayama
    derived: dict = {}
    for hour_block in se.values():
        for entry in hour_block:
            if entry["specialNote"] in ("*", "*M"):
                derived.setdefault(entry["hour"], []).append(
                    {**entry, "derivedFrom": "seiseki"}
                )

    def _merge(base: dict, extra: dict) -> dict:
        merged = dict(base)
        for h, entries in extra.items():
            existing = merged.get(h, [])
            seen = {(e["hour"], e["minute"]) for e in existing}
            for e in entries:
                if (e["hour"], e["minute"]) not in seen:
                    existing.append(e)
                    seen.add((e["hour"], e["minute"]))
            merged[h] = sorted(existing, key=lambda x: x["minute"])
        return merged

    sn = _merge(sn, derived)

    return {
        "fromSeisekiToSchool": _build_hourly(ss),
        "fromNagayamaToSchool": _build_hourly(ns),
        "fromSchoolToNagayama": _build_hourly(sn),
        "fromSchoolToSeiseki": _build_hourly(se),
    }


def _parse_pdf_bytes(data: bytes) -> tuple:
    """
    从 PDF bytes 解析时刻表，返回 (schedule1, schedule2 | None)。
    与 scripts/parse_bus_data.py 的 parse_pdf() 逻辑完全相同。
    """
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        tables = pdf.pages[0].extract_tables()

    logger.debug("PDF 检测到 %d 张表格", len(tables))

    grade_nagayama = _build_grade_lookup(tables[0])
    grade_seiseki = _build_grade_lookup(tables[1])

    schedule1 = _parse_main_table(tables[2], grade_seiseki, grade_nagayama)
    schedule2 = (
        _parse_main_table(tables[3], grade_seiseki, grade_nagayama)
        if len(tables) >= 4
        else None
    )
    return schedule1, schedule2


# ── schoolbus.html 解析：找「通常バスダイヤ」PDF 链接 ──────────────────────────
def _find_standard_pdf_links(html: str) -> tuple[str | None, str | None]:
    """
    从 schoolbus.html 中找到标准巴士时刻表 PDF 链接。
    返回 (url_weekday, url_wednesday)，找不到则对应值为 None。

    策略：在所有 <a href="..."> 中匹配 bus_YYYY.pdf 和 bus_YYYYwed.pdf，
    取年份最大（最新）的链接。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    weekday_url: str | None = None
    wed_url: str | None = None
    best_weekday_year = 0
    best_wed_year = 0

    base = "https://www.tama.ac.jp/guide/campus/"
    for a in soup.find_all("a", href=True):
        href: str = str(a["href"])

        m = _PDF_WEEKDAY_RE.search(href)
        if m:
            year = int(m.group(1))
            if year > best_weekday_year:
                best_weekday_year = year
                weekday_url = base + href if not href.startswith("http") else href

        m = _PDF_WED_RE.search(href)
        if m:
            year = int(m.group(1))
            if year > best_wed_year:
                best_wed_year = year
                wed_url = base + href if not href.startswith("http") else href

    return weekday_url, wed_url


# ── 主更新函数 ─────────────────────────────────────────────────────────────────
async def update_bus_schedule() -> bool:
    """
    检查并更新巴士时刻表数据。

    步骤：
      1. 异步拉取 schoolbus.html
      2. 找出最新平日 / 水曜 PDF URL
      3. 并发下载两份 PDF bytes
      4. 解析为结构化数据
      5. 与 bus_data.json 比较；有差异则写入并刷新内存缓存

    返回：
      True  — 数据已更新
      False — 无变化或发生错误（不抛出异常，保证调度任务不崩溃）
    """
    logger.info("开始检查巴士时刻表更新...")

    try:
        async with aiohttp.ClientSession() as session:
            # 步骤 1：拉取主页
            logger.info("拉取 schoolbus.html")
            html_bytes = await _get_bytes(session, _SCHOOLBUS_URL)
            html = html_bytes.decode("utf-8", errors="replace")

            # 步骤 2：解析 PDF 链接
            url_weekday, url_wed = _find_standard_pdf_links(html)
            if not url_weekday or not url_wed:
                logger.warning(
                    "未找到标准 PDF 链接（weekday=%s, wed=%s），跳过更新",
                    url_weekday,
                    url_wed,
                )
                return False

            logger.info("平日 PDF: %s", url_weekday)
            logger.info("水曜 PDF: %s", url_wed)

            # 步骤 3：并发下载两份 PDF
            weekday_bytes, wed_bytes = await asyncio.gather(
                _get_bytes(session, url_weekday),
                _get_bytes(session, url_wed),
            )

        # 步骤 4：解析 PDF（CPU 密集，在事件循环中同步执行；PDF 解析耗时通常 <1s）
        logger.info("解析平日时刻表 PDF")
        weekday, saturday = _parse_pdf_bytes(weekday_bytes)

        logger.info("解析水曜日时刻表 PDF")
        wednesday, _ = _parse_pdf_bytes(wed_bytes)

        # 读取当前 bus_data.json 以获取 title / notes 字段
        if _BUS_DATA_PATH.exists():
            current_raw = json.loads(_BUS_DATA_PATH.read_text(encoding="utf-8"))
        else:
            current_raw = {}

        new_data = {
            "title": current_raw.get("title", "基準時刻表"),
            "notes": current_raw.get(
                "notes",
                {
                    "◎": "经永山站到达学校的班次",
                    "*": "经永山站到达圣迹樱丘站的班次",
                    "M": "大学生专用微型巴士（仅周一～周四）",
                    "C": "中学生专用班次",
                    "K": "高中生专用班次",
                },
            ),
            "weekday": weekday,
            "saturday": saturday,
            "wednesday": wednesday,
        }

        # 步骤 5：比较新旧数据（只比较时刻表部分，忽略 title/notes）
        def _schedule_eq(a: dict, b: dict) -> bool:
            keys = ("weekday", "saturday", "wednesday")
            return all(a.get(k) == b.get(k) for k in keys)

        if _schedule_eq(new_data, current_raw):
            logger.info("巴士时刻表无变化，跳过写入")
            return False

        # 写入更新后的数据
        _BUS_DATA_PATH.write_text(
            json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("巴士时刻表已更新，写入 %s", _BUS_DATA_PATH)

        # 刷新内存缓存（让下次 /bus/app_data 请求立即使用新数据）
        try:
            from tutnext.api.routes.bus import reload_bus_data

            reload_bus_data()
            logger.info("内存缓存已刷新")
        except Exception as cache_err:
            logger.warning("刷新内存缓存失败（不影响数据写入）：%s", cache_err)

        return True

    except Exception as e:
        logger.error("巴士时刻表更新失败：%s", e, exc_info=True)
        return False
