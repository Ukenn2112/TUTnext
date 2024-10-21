# app/main.py
import os
import app.config
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from app.routes import schedule, bus
from app.services.gakuen_api import GakuenAPI, GakuenAPIError


class UserData(BaseModel):
    username: str
    password: str


app = FastAPI()

# Include other routes
app.include_router(schedule.router, prefix="/schedule", tags=["Schedule"])
app.include_router(bus.router, prefix="/bus", tags=["Bus"])


# Home page
@app.get("/")
async def help_page():
    return FileResponse(os.path.join("app", "static", "index.html"))


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
