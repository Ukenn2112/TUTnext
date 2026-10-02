# tutnext/api/routes/tmail.py
from fastapi import APIRouter

# 教师数据以 Python 模块形式打包（Cloudflare Workers 只上传 *.py 文件）。
# 源数据位于 data/teachers.json，修改后运行 scripts/build_assets_data.py 重新生成。
from tutnext.assets_data.teachers_data import TEACHERS as _teachers

router = APIRouter()


@router.get("")
async def get_tmail():
    return {
        "status": True,
        "data": _teachers,
    }
