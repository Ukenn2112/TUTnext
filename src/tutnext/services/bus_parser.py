"""
校车【临时时刻表】解析器 —— URL 流处理版
适配仅含单张时刻表、无年级信息的临时班次 PDF

特性：
  - 自动从 PDF 文本提取适用日期描述
  - 支持内嵌备注：(水曜日のみ) / (水曜日運休)，根据当天星期自动过滤
  - ◎ / * / *M 特殊标记解析
  - 规则：fromSchoolToSeiseki 中的 * 班次同步到 fromSchoolToNagayama（无偏移）

水曜日过滤规则：
  当天为周三 → (水曜日のみ) 班次保留，(水曜日運休) 班次移除
  当天非周三 → (水曜日のみ) 班次移除，(水曜日運休) 班次保留（去掉备注标签正常显示）

依赖: pip install pdfplumber
用法:
    python -m tutnext.services.bus_parser <URL> [URL2] ...
"""

import io
import sys
import re
import json
from datetime import date as datetime_date
import pdfplumber


# ── 星期判断 ──────────────────────────────────────────────────────────────────
def is_wednesday(date: datetime_date | None = None) -> bool:
    """返回指定日期（默认今天）是否为周三（水曜日）"""
    d = date or datetime_date.today()
    return d.weekday() == 2  # Monday=0 … Wednesday=2 … Sunday=6


# ── PDF 加载（接受 bytes，避免在 async 环境中使用同步 HTTP）──────────────────
def load_pdf_from_bytes(data: bytes) -> pdfplumber.PDF:  # type: ignore
    """从已下载的 bytes 打开 PDF，适用于 async 环境中由调用方负责下载。"""
    return pdfplumber.open(io.BytesIO(data))


# ── 水曜日備考フィルタ ─────────────────────────────────────────────────────────
WED_ONLY = "水曜日のみ"  # specialNote に含まれる文字列
WED_CLOSED = "水曜日運休"  # specialNote に含まれる文字列


def apply_wednesday_filter(entries: list[dict], wed: bool) -> list[dict]:
    """
    水曜日フィルタを適用してエントリリストを返す。

    wed=True（今日は水曜日）:
      ・(水曜日のみ) → 保留（備考を除いた通常班次として扱う）
      ・(水曜日運休) → 除外

    wed=False（今日は水曜日以外）:
      ・(水曜日のみ) → 除外
      ・(水曜日運休) → 保留（備考を除いた通常班次として扱う）
    """
    result = []
    for e in entries:
        note = e.get("specialNote") or ""

        if WED_ONLY in note:
            if not wed:
                continue  # 非周三：仅周三班次 → 移除
            # 周三：去掉 (水曜日のみ) 标签，还原真实 specialNote
            clean_note = _strip_wed_tag(note, WED_ONLY)
            result.append(
                {**e, "isSpecial": clean_note is not None, "specialNote": clean_note}
            )

        elif WED_CLOSED in note:
            if wed:
                continue  # 周三：水曜运休 → 移除
            # 非周三：去掉 (水曜日運休) 标签，还原真实 specialNote
            clean_note = _strip_wed_tag(note, WED_CLOSED)
            result.append(
                {**e, "isSpecial": clean_note is not None, "specialNote": clean_note}
            )

        else:
            result.append(e)  # 普通班次不受影响

    return result


def _strip_wed_tag(note: str, tag: str) -> str | None:
    """
    从 specialNote 中去除水曜備考括号部分，返回剩余符号或 None。
    例：
      "*(水曜日のみ)"  → "*"
      "(水曜日のみ)"   → None（普通班次）
      "*(水曜日運休)"  → "*"
    """
    # 去除 (tag) 括号整体
    cleaned = note.replace(f"({tag})", "").strip()
    return cleaned if cleaned else None


# ── 单 token 解析（含内嵌备注）───────────────────────────────────────────────
# 内嵌备注格式：数字 + 可选符号 + 括号备注，如：55(水曜日のみ)、0*(水曜日のみ)、0(水曜日運休)
INLINE_NOTE_RE = re.compile(r"^(\d+)(\*|◎)?\(([^)]+)\)(\*|◎)?$")
SIMPLE_RE = re.compile(r"^(\d+)([◎*M]*)$")


def parse_token(token: str, hour: int) -> dict | None:
    """解析单个时刻 token，返回时刻对象（含原始 specialNote）或 None"""
    token = token.strip()
    if not token:
        return None

    inline = INLINE_NOTE_RE.match(token)
    if inline:
        minute = int(inline.group(1))
        pre_sym = inline.group(2) or ""
        note_text = inline.group(3)  # 水曜日のみ / 水曜日運休
        post_sym = inline.group(4) or ""
        sym = pre_sym or post_sym

        if sym == "◎":
            special_note = "◎"  # ◎ 优先，忽略水曜备注（实际不出现）
        elif sym == "*":
            special_note = f"*({note_text})"
        else:
            special_note = f"({note_text})"

        return {
            "hour": hour,
            "minute": minute,
            "isSpecial": True,
            "specialNote": special_note,
        }

    simple = SIMPLE_RE.match(token)
    if simple:
        minute = int(simple.group(1))
        suffix = simple.group(2)

        if "M" in suffix and "*" in suffix:
            note = "*M"
        elif "◎" in suffix:
            note = "◎"
        elif "*" in suffix:
            note = "*"
        elif "M" in suffix:
            note = "M"
        else:
            note = None

        return {
            "hour": hour,
            "minute": minute,
            "isSpecial": note is not None,
            "specialNote": note,
        }

    return None


def parse_cell(cell: str | None, hour: int) -> list[dict]:
    """将单元格文本拆分并解析为时刻对象列表（保留所有原始备注，过滤在外部进行）"""
    if not cell or not cell.strip():
        return []
    tokens = re.findall(r"\d+[◎*M]*(?:\([^)]+\))?[◎*M]*", cell)
    return [obj for t in tokens for obj in [parse_token(t, hour)] if obj]


# ── 按小时分组 ────────────────────────────────────────────────────────────────
def build_hourly(rows_by_hour: dict) -> list[dict]:
    return [
        {"hour": h, "times": rows_by_hour[h]}
        for h in sorted(rows_by_hour)
        if rows_by_hour[h]
    ]


# ── 主解析函数 ────────────────────────────────────────────────────────────────
def parse_temp_pdf(pdf_bytes: bytes, date: datetime_date | None = None) -> dict:
    """
    从已下载的 PDF bytes 解析临时时刻表，返回结构化时刻表 dict。

    参数：
      pdf_bytes — PDF 的原始字节内容（由调用方负责下载）
      date      — 指定日期（默认今天），用于判断是否为水曜日

    临时 PDF 列顺序（固定）：
      col 0: 聖蹟桜ヶ丘駅発 → 学校  (fromSeisekiToSchool)
      col 1: 永山駅発 → 学校        (fromNagayamaToSchool)
      col 2: 小时
      col 3: 学校 → 永山駅          (fromSchoolToNagayama)
      col 4: 学校 → 聖蹟桜ヶ丘駅    (fromSchoolToSeiseki)
    """
    with load_pdf_from_bytes(pdf_bytes) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables()

    wed = is_wednesday(date)
    today_str = (date or datetime_date.today()).strftime("%Y-%m-%d")
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][
        (date or datetime_date.today()).weekday()
    ]

    print(
        f"  → 解析日期：{today_str}（{weekday_ja}曜日）{'★水曜日モード' if wed else ''}",
        file=sys.stderr,
    )
    print(f"  → 检测到 {len(tables)} 张表格", file=sys.stderr)

    if not tables:
        raise ValueError("未在 PDF 中找到表格")

    table = tables[0]

    # ── 解析原始数据（含所有备注）────────────────────────────────────────────
    ss_raw, ns_raw, sn_raw, se_raw = {}, {}, {}, {}

    for row in table[2:]:
        if len(row) < 5:
            continue
        hour_cell = row[2]
        if not hour_cell or not hour_cell.strip().isdigit():
            continue
        hour = int(hour_cell.strip())

        ss_raw[hour] = parse_cell(row[0], hour)
        ns_raw[hour] = parse_cell(row[1], hour)
        sn_raw[hour] = parse_cell(row[3], hour)
        se_raw[hour] = parse_cell(row[4], hour)

    # ── 应用水曜日过滤 ────────────────────────────────────────────────────────
    ss = {h: apply_wednesday_filter(v, wed) for h, v in ss_raw.items()}
    ns = {h: apply_wednesday_filter(v, wed) for h, v in ns_raw.items()}
    sn = {h: apply_wednesday_filter(v, wed) for h, v in sn_raw.items()}
    se = {h: apply_wednesday_filter(v, wed) for h, v in se_raw.items()}

    # ── 派生规则：学校→圣迹 * 班次同步到 学校→永山（无偏移）────────────────
    # 注意：此处使用过滤后的 se，确保已删除的水曜班次不会被派生
    derived_sn: dict = {}
    for blk in se.values():
        for e in blk:
            note = e.get("specialNote") or ""
            if note.startswith("*"):
                derived_sn.setdefault(e["hour"], []).append(
                    {
                        **e,
                        "derivedFrom": "seiseki",
                    }
                )

    def merge_and_sort(base: dict, derived: dict) -> dict:
        merged = dict(base)
        for hour, entries in derived.items():
            existing = merged.get(hour, [])
            seen_keys = {(x["hour"], x["minute"]) for x in existing}
            for e in entries:
                if (e["hour"], e["minute"]) not in seen_keys:
                    existing.append(e)
                    seen_keys.add((e["hour"], e["minute"]))
            merged[hour] = sorted(existing, key=lambda x: x["minute"])
        return merged

    sn = merge_and_sort(sn, derived_sn)

    return {
        "fromSeisekiToSchool": build_hourly(ss),
        "fromNagayamaToSchool": build_hourly(ns),
        "fromSchoolToNagayama": build_hourly(sn),
        "fromSchoolToSeiseki": build_hourly(se),
    }


# ── 主入口 ────────────────────────────────────────────────────────────────────
# def main():
#     if len(sys.argv) < 2:
#         print("用法: python parse_bus_temp.py <URL> [URL2] ...", file=sys.stderr)
#         sys.exit(1)

#     results = []
#     for url in sys.argv[1:]:
#         print(f"[加载] {url}", file=sys.stderr)
#         results.append(parse_temp_pdf(url))

#     output = results[0] if len(results) == 1 else results
#     print(json.dumps(output, ensure_ascii=False, indent=2))


# if __name__ == "__main__":
#     main()
