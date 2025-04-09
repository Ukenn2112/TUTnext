# app/routes/push.py
from fastapi import APIRouter, Response, status
import aiosqlite
import os

router = APIRouter()

# 数据库文件路径
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "users.db")

# 确保数据目录存在
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

async def init_db():
    """初始化数据库，创建表结构"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            encryptedPassword TEXT NOT NULL,
            deviceToken TEXT NOT NULL
        )
        ''')
        await db.commit()

@router.post("/send")
async def send_push(data: dict, response: Response):
    # 验证数据
    username = data.get("username")
    encryptedPassword = data.get("encryptedPassword")
    deviceToken = data.get("deviceToken")
    
    if not username or not encryptedPassword or not deviceToken:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": False, "message": "Missing required parameters, please provide username, encryptedPassword and deviceToken"}
    
    # 确保数据库已初始化
    await init_db()
    
    async with aiosqlite.connect(DB_PATH) as db:
        # 检查用户是否已存在
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        existing_user = await cursor.fetchone()
        
        if existing_user:
            # 更新用户信息
            await db.execute(
                "UPDATE users SET encryptedPassword = ?, deviceToken = ? WHERE username = ?",
                (encryptedPassword, deviceToken, username)
            )
        else:
            # 添加新用户
            await db.execute(
                "INSERT INTO users (username, encryptedPassword, deviceToken) VALUES (?, ?, ?)",
                (username, encryptedPassword, deviceToken)
            )
        
        await db.commit()
    
    response.status_code = status.HTTP_200_OK
    return {"status": True, "message": "Data stored and pushed successfully"}

@router.post("/unregister")
async def unregister_push(data: dict, response: Response):
    deviceToken = data.get("deviceToken")
    
    if not deviceToken:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": False, "message": "Missing required parameter deviceToken"}
    
    # 确保数据库已初始化
    await init_db()
    
    async with aiosqlite.connect(DB_PATH) as db:
        # 删除对应deviceToken的记录
        await db.execute("DELETE FROM users WHERE deviceToken = ?", (deviceToken,))
        await db.commit()
    
    response.status_code = status.HTTP_200_OK
    return {"status": True, "message": "Device unregistered successfully"}