"""Storage for the baseline bus timetable (``bus_data.json``).

* server mode: the JSON file under ``src/tutnext/data/`` (written by the weekly
  scraper, read by ``/bus/app_data``);
* workers mode: the ``bus:data`` key in the D1-backed store, falling back to the
  timetable bundled at build time (``assets_data/bus_data_default.py``).
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from tutnext import runtime

logger = logging.getLogger(__name__)

BUS_DATA_PATH = Path(__file__).parent.parent / "data" / "bus_data.json"
BUS_DATA_KEY = "bus:data"

_file_cache: dict | None = None


def _default_bus_data() -> dict:
    from tutnext.assets_data.bus_data_default import BUS_DATA

    return copy.deepcopy(BUS_DATA)


def load_bus_data_file() -> dict:
    """Server mode: load (and memoize) the JSON file; bundled default if missing."""
    global _file_cache
    if _file_cache is None:
        if BUS_DATA_PATH.exists():
            logger.info("从磁盘加载 bus_data.json 到内存缓存")
            _file_cache = json.loads(BUS_DATA_PATH.read_text(encoding="utf-8"))
        else:
            logger.warning("bus_data.json 不存在，使用内置基准时刻表")
            _file_cache = _default_bus_data()
    assert _file_cache is not None
    return _file_cache


def reload_bus_data_file() -> dict:
    """Server mode: drop the memoized copy and re-read the file."""
    global _file_cache
    _file_cache = None
    return load_bus_data_file()


async def load_bus_data() -> dict:
    """Return the current baseline timetable for the active runtime."""
    if not runtime.IS_WORKERS:
        return load_bus_data_file()
    from tutnext.config import redis

    try:
        raw = await redis.get(BUS_DATA_KEY)
    except Exception as e:  # noqa: BLE001 - storage failure must not break /bus/app_data
        logger.warning("读取 %s 失败，使用内置基准时刻表: %s", BUS_DATA_KEY, e)
        raw = None
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("%s 内容不是合法 JSON，使用内置基准时刻表", BUS_DATA_KEY)
    return _default_bus_data()


async def save_bus_data(data: dict[str, Any]) -> None:
    """Persist a new baseline timetable (scraper)."""
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if runtime.IS_WORKERS:
        from tutnext.config import redis

        await redis.set(BUS_DATA_KEY, payload)
        logger.info("巴士时刻表已更新，写入 D1 键 %s", BUS_DATA_KEY)
        return
    BUS_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUS_DATA_PATH.write_text(payload, encoding="utf-8")
    logger.info("巴士时刻表已更新，写入 %s", BUS_DATA_PATH)
    reload_bus_data_file()
