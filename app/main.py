# app/main.py
import os
from pydantic import BaseModel

from fastapi import FastAPI
from fastapi.responses import FileResponse
from app.routes import schedule, bus, kadai, push, tmail
from app.services.gakuen_api import GakuenAPI, GakuenAPIError


class UserData(BaseModel):
    username: str
    password: str


app = FastAPI()

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
        await gakuen.webapi_login()
        return {"status": "success"}
    except GakuenAPIError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        await gakuen.close()
