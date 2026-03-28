# tutnext/api/routes/tmail.py
import json
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

# 模块加载时从 JSON 文件读取教师数据并缓存在内存中
_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "teachers.json"
with _DATA_FILE.open(encoding="utf-8") as _f:
    _teachers: list = json.load(_f)


@router.get("")
async def get_tmail():
    return {
        "status": True,
        "data": _teachers,
    }
