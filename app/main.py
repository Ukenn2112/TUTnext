# app/main.py
import os
from pydantic import BaseModel
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from app.routes import schedule, bus, kadai, push, tmail
from app.services.gakuen_api import GakuenAPI, GakuenAPIError
from app.database import db_manager


class UserData(BaseModel):
    username: str
    password: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库
    await db_manager.init_db()
    yield
    # 关闭时关闭数据库连接池
    await db_manager.close()


app = FastAPI(lifespan=lifespan)

# Include other routes
app.include_router(schedule.router, prefix="/schedule", tags=["Schedule"])
app.include_router(bus.router, prefix="/bus", tags=["Bus"])
app.include_router(kadai.router, prefix="/kadai", tags=["Kadai"])
app.include_router(push.router, prefix="/push", tags=["Push"])
app.include_router(tmail.router, prefix="/tmail", tags=["Tmail"])


# Home page
@app.get("/")
async def help_page():
    return FileResponse(os.path.join("app", "static", "index.html"))


# User agreement page
@app.get("/user-agreement")
async def user_agreement_page():
    return FileResponse(os.path.join("app", "static", "user-agreement.html"))


# Policy page
@app.get("/policy")
async def policy_page():
    return FileResponse(os.path.join("app", "static", "policy.html"))


@app.post("/login_check")
async def login_check(data: UserData):
    gakuen = GakuenAPI(data.username, data.password, "https://next.tama.ac.jp")
    try:
        await gakuen.api_login()
        return {"status": "success"}
    except GakuenAPIError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        await gakuen.close()
