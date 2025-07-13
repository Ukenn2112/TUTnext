# app/database.py
import aiosqlite
import asyncio
import os
import logging
from typing import List, Tuple, Optional, Any

class DatabaseManager:
    """数据库管理器，提供统一的数据库访问接口"""
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 统一使用项目根目录下的数据库文件
            self.db_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), 
                "users.db"
            )
        else:
            self.db_path = db_path
        
        self._lock = asyncio.Lock()
        self._initialized = False
    
    async def init_db(self):
        """初始化数据库，创建表结构"""
        async with self._lock:
            if not self._initialized:
                async with aiosqlite.connect(self.db_path) as db:
                    # 启用 WAL 模式以支持更好的并发访问
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA synchronous=NORMAL")
                    await db.execute("PRAGMA cache_size=1000")
                    await db.execute("PRAGMA temp_store=memory")
                    
                    await db.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        username TEXT PRIMARY KEY,
                        encryptedPassword TEXT NOT NULL,
                        deviceToken TEXT NOT NULL
                    )
                    ''')
                    await db.commit()
                self._initialized = True
                logging.info(f"数据库已初始化: {self.db_path}")
    
    async def get_all_users(self) -> List[Any]:
        """获取所有用户"""
        await self.init_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM users")
            rows = await cursor.fetchall()
            return list(rows)
    
    async def get_user(self, username: str) -> Optional[Any]:
        """根据用户名获取用户"""
        await self.init_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            return await cursor.fetchone()
    
    async def upsert_user(self, username: str, encrypted_password: str, device_token: str) -> bool:
        """插入或更新用户"""
        await self.init_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 使用 INSERT OR REPLACE 来处理插入或更新
                await db.execute(
                    "INSERT OR REPLACE INTO users (username, encryptedPassword, deviceToken) VALUES (?, ?, ?)",
                    (username, encrypted_password, device_token)
                )
                await db.commit()
            return True
        except Exception as e:
            logging.error(f"插入/更新用户 {username} 时出错: {e}")
            return False
    
    async def delete_user(self, username: str) -> bool:
        """删除用户"""
        await self.init_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT * FROM users WHERE username = ?", (username,)
                )
                user = await cursor.fetchone()
                if not user:
                    logging.info(f"用户 {username} 不存在，跳过删除")
                    return False
                
                await db.execute("DELETE FROM users WHERE username = ?", (username,))
                await db.commit()
                logging.info(f"用户 {username} 已被删除")
            return True
        except Exception as e:
            logging.error(f"删除用户 {username} 时出错: {e}")
            return False
    
    async def delete_user_by_device_token(self, device_token: str) -> bool:
        """根据设备token删除用户"""
        await self.init_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM users WHERE deviceToken = ?", (device_token,))
                await db.commit()
            return True
        except Exception as e:
            logging.error(f"删除设备token {device_token} 对应的用户时出错: {e}")
            return False

# 全局数据库管理器实例
db_manager = DatabaseManager()
