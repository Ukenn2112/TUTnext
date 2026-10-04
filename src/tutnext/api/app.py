# app/main.py
from pathlib import Path
from pydantic import BaseModel
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from tutnext.api.markers import install_gateway_markers
from tutnext.api.routes import admin, oauth, schedule, bus, kadai, push, tmail, live_activity
from tutnext.services.gakuen.client import GakuenAPI, GakuenAPIError
from tutnext.core.database import db_manager
from tutnext.config import HTTP_PROXY, IS_WORKERS
from tutnext.services.gakuen.session_manager import get_session_manager
from tutnext.services.google_classroom import classroom_api


class UserData(BaseModel):
    username: str
    password: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    if IS_WORKERS:
        # Workers: no connection pool to warm up and the ASGI adapter runs the
        # lifespan per request, so keep it a no-op.
        yield
        return
    # 启动时初始化数据库
    await db_manager.init_db()
    yield
    # 关闭时释放资源：共享 HTTP 会话 + 数据库连接池
    await classroom_api.close()
    await db_manager.close()


app = FastAPI(lifespan=lifespan)


install_gateway_markers(app)

# Include other routes
app.include_router(schedule.router, prefix="/schedule", tags=["Schedule"])
app.include_router(bus.router, prefix="/bus", tags=["Bus"])
app.include_router(kadai.router, prefix="/kadai", tags=["Kadai"])
app.include_router(push.router, prefix="/push", tags=["Push"])
app.include_router(tmail.router, prefix="/tmail", tags=["Tmail"])
app.include_router(oauth.router, prefix="/oauth", tags=["OAuth"])
app.include_router(live_activity.router, prefix="/live-activity", tags=["LiveActivity"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])


# Home page
@app.get("/")
async def help_page():
    return FileResponse(Path(__file__).parent.parent / "static" / "index.html")


# User agreement page
@app.get("/user-agreement")
async def user_agreement_page():
    return FileResponse(Path(__file__).parent.parent / "static" / "user-agreement.html")


# Policy page
@app.get("/policy")
async def policy_page():
    return FileResponse(Path(__file__).parent.parent / "static" / "policy.html")


@app.post("/login_check")
async def login_check(data: UserData):
    try:
        async with get_session_manager().lock_only(data.username):
            gakuen = GakuenAPI(data.username, data.password, "https://next.tama.ac.jp", http_proxy=HTTP_PROXY)
            try:
                await gakuen.api_login()
                return {"status": "success"}
            finally:
                await gakuen.close()
    except Exception as e:  # GakuenAPIError (incl. SESSION_BUSY from the lock) or anything else
        return {"status": "error", "message": str(e)}
