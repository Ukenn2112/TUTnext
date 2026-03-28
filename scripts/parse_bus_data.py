"""
校车时刻表解析器
直接从 URL 读取 PDF，无需保存文件到本地

依赖: pip install pdfplumber
用法:
    python parse_bus_2025.py                   # 使用默认 URL
    python parse_bus_2025.py [平日URL] [水曜URL]
"""

import io
import sys
import json
import re
from requests import get
import pdfplumber

# ── 默认 URL ──────────────────────────────────────────────────────────────────
URL_WEEKDAY = "https://www.tama.ac.jp/guide/campus/img/bus_2025.pdf"
URL_WED = "https://www.tama.ac.jp/guide/campus/img/bus_2025wed.pdf"


# ── PDF 流式加载 ───────────────────────────────────────────────────────────────
def load_pdf_from_url(url: str) -> pdfplumber.PDF: # type: ignore
    """从 URL 以流方式加载 PDF，不写入磁盘"""
    resp = get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    return pdfplumber.open(io.BytesIO(resp.content))


# ── 时刻解析 ───────────────────────────────────────────────────────────────────
def parse_cell(cell, hour: int, grade_lookup: dict) -> list:
    """
    将单元格文本解析为时刻对象列表。
    specialNote 取值：
      ◎  经永山站→学校
      *  经永山站→圣迹樱丘站
      M  大学生微型巴士（月～木のみ）
      C  中学生专用班次（来自早班年级表）
      K  高中生专用班次（来自早班年级表）
    """
    if not cell or not cell.strip():
        return []

    results = []
    for token in cell.split():
        num_match = re.match(r"^(\d+)(.*)", token.strip())
        if not num_match:
            continue

        minute = int(num_match.group(1))
        suffix = num_match.group(2)

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


def build_hourly(rows_by_hour: dict) -> list:
    """将 {hour: [time_obj, ...]} 整理为按小时分组列表（跳过空小时）"""
    return [
        {"hour": hour, "times": rows_by_hour[hour]}
        for hour in sorted(rows_by_hour)
        if rows_by_hour[hour]
    ]


# ── 年级表解析（早班 Table 0 / Table 1）──────────────────────────────────────
GRADE_MAP = {"学年指定なし": None, "中学生": "C", "高校生": "K"}


def build_grade_lookup(table: list) -> dict:
    """返回 {(hour, minute): "C"/"K"/None}"""
    lookup = {}
    current_grade = None
    for row in table[1:]:  # 跳过标题行
        time_str, grade_str = row[0], row[1]
        if grade_str is not None:
            current_grade = GRADE_MAP.get(grade_str, None)
        if time_str and ":" in time_str:
            h, m = time_str.split(":")
            lookup[(int(h), int(m))] = current_grade
    return lookup


# ── 主时刻表解析（平日/土曜/水曜共用）──────────────────────────────────────────
def parse_main_table(table: list, grade_seiseki: dict, grade_nagayama: dict) -> dict:
    from_seiseki_to_school = {}
    from_nagayama_to_school = {}
    from_school_to_nagayama = {}
    from_school_to_seiseki = {}

    for row in table[2:]:  # 跳过两行标题
        if len(row) < 5:
            continue
        hour_cell = row[2]
        if not hour_cell or not hour_cell.strip().isdigit():
            continue
        hour = int(hour_cell.strip())

        from_seiseki_to_school[hour] = parse_cell(row[0], hour, grade_seiseki)
        from_nagayama_to_school[hour] = parse_cell(row[1], hour, grade_nagayama)
        from_school_to_nagayama[hour] = parse_cell(row[3], hour, {})
        from_school_to_seiseki[hour] = parse_cell(row[4], hour, {})

    # ── 派生班次注入 ──────────────────────────────────────────────────────────
    #
    # 规则：fromSchoolToSeiseki 中 specialNote=="*" 或 "*M"（经永山站到圣迹）
    #       → 在 fromSchoolToNagayama 中补入同一班次，时刻不变
    #         （巴士从学校出发途经永山，出发时刻相同）

    derived_to_nagayama = {}

    for hour_block in from_school_to_seiseki.values():
        for entry in hour_block:
            if entry["specialNote"] in ("*", "*M"):
                derived_to_nagayama.setdefault(entry["hour"], []).append(
                    {
                        "hour": entry["hour"],
                        "minute": entry["minute"],
                        "isSpecial": True,
                        "specialNote": entry["specialNote"],
                        "derivedFrom": "seiseki",
                    }
                )

    # 合并派生班次，按分钟排序去重
    def merge_and_sort(base: dict, derived: dict) -> dict:
        merged = dict(base)
        for hour, entries in derived.items():
            existing = merged.get(hour, [])
            existing_keys = {(e["hour"], e["minute"]) for e in existing}
            for e in entries:
                if (e["hour"], e["minute"]) not in existing_keys:
                    existing.append(e)
                    existing_keys.add((e["hour"], e["minute"]))
            merged[hour] = sorted(existing, key=lambda x: x["minute"])
        return merged

    from_school_to_nagayama = merge_and_sort(
        from_school_to_nagayama, derived_to_nagayama
    )

    return {
        "fromSeisekiToSchool": build_hourly(from_seiseki_to_school),
        "fromNagayamaToSchool": build_hourly(from_nagayama_to_school),
        "fromSchoolToNagayama": build_hourly(from_school_to_nagayama),
        "fromSchoolToSeiseki": build_hourly(from_school_to_seiseki),
    }


# ── 解析单个 PDF（含两张时刻表）────────────────────────────────────────────────
def parse_pdf(url: str) -> tuple:
    """
    从 URL 流式读取 PDF，返回 (schedule1, schedule2 | None) 元组。
    PDF 结构：
      Table 0 — 永山駅発 早班年级
      Table 1 — 聖蹟桜ヶ丘駅発 早班年级
      Table 2 — 第一张时刻表（平日PDF→平日；水曜PDF→水曜日）
      Table 3 — 第二张时刻表（平日PDF→土曜日；水曜PDF→なし の場合 None）
    """
    with load_pdf_from_url(url) as pdf:
        tables = pdf.pages[0].extract_tables()

    print(f"  → 检测到 {len(tables)} 张表格", file=sys.stderr)

    grade_nagayama = build_grade_lookup(tables[0])
    grade_seiseki = build_grade_lookup(tables[1])

    schedule1 = parse_main_table(tables[2], grade_seiseki, grade_nagayama)
    schedule2 = (
        parse_main_table(tables[3], grade_seiseki, grade_nagayama)
        if len(tables) >= 4
        else None
    )

    return schedule1, schedule2


# ── 主入口 ────────────────────────────────────────────────────────────────────
def main():
    url_weekday = sys.argv[1] if len(sys.argv) > 1 else URL_WEEKDAY
    url_wed = sys.argv[2] if len(sys.argv) > 2 else URL_WED

    # 当没问题时才写入文件，避免覆盖已有数据
    try:
        print(f"[1/2] 加载平日时刻表: {url_weekday}", file=sys.stderr)
        weekday, saturday = parse_pdf(url_weekday)

        print(f"[2/2] 加载水曜日时刻表: {url_wed}", file=sys.stderr)
        wednesday, _ = parse_pdf(url_wed)

        result = {
            "title": "2025年度 基準時刻表",
            "notes": {
                "◎": "经永山站到达学校的班次",
                "*": "经永山站到达圣迹樱丘站的班次",
                "M": "大学生专用微型巴士（仅周一～周四）",
                "C": "中学生专用班次",
                "K": "高中生专用班次",
            },
            "weekday": weekday,
            "saturday": saturday,
            "wednesday": wednesday,
        }

    except Exception as e:
        print(f"解析失败，跳过写入: {e}", file=sys.stderr)
    else:
        with open("app/data/bus_data.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False, indent=2))
        print("写入成功", file=sys.stderr)


if __name__ == "__main__":
    main()
