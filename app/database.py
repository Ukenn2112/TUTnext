# app/database.py
import asyncpg
import asyncio
import logging
from typing import List, Optional, Any, Dict
from config import DATABASE_URL


class DatabaseManager:
    """数据库管理器，提供统一的数据库访问接口"""

    def __init__(self, db_url: Optional[str] = None):
        if db_url is None:
            # 从配置文件获取PostgreSQL连接URL
            self.db_url = DATABASE_URL
        else:
            self.db_url = db_url

        self._lock = asyncio.Lock()
        self._initialized = False
        self._pool: Optional[asyncpg.Pool] = None

    async def init_db(self):
        """初始化数据库，创建连接池和表结构"""
        async with self._lock:
            if not self._initialized:
                # 创建连接池
                self._pool = await asyncpg.create_pool(
                    self.db_url, min_size=1, max_size=10, command_timeout=60
                )

                # 创建表结构
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                    CREATE TABLE IF NOT EXISTS users (
                        username TEXT PRIMARY KEY,
                        encryptedPassword TEXT NOT NULL,
                        deviceToken TEXT NOT NULL
                    )
                    """
                    )
                    
                    # 创建用户令牌表
                    await conn.execute(
                        """
                    CREATE TABLE IF NOT EXISTS user_tokens (
                        username TEXT PRIMARY KEY,
                        access_token TEXT NOT NULL,
                        refresh_token TEXT NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                    )

                self._initialized = True
                logging.info(f"数据库已初始化: {self.db_url}")

    async def close(self):
        """关闭数据库连接池"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            self._initialized = False

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """获取所有用户"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM users")
            return [dict(row) for row in rows]

    async def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名获取用户"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE username = $1", username
            )
            return dict(row) if row else None

    async def upsert_user(
        self, username: str, encrypted_password: str, device_token: str
    ) -> bool:
        """插入或更新用户"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        try:
            async with self._pool.acquire() as conn:
                # 使用 ON CONFLICT 来处理插入或更新
                await conn.execute(
                    """INSERT INTO users (username, encryptedPassword, deviceToken) 
                       VALUES ($1, $2, $3)
                       ON CONFLICT (username) 
                       DO UPDATE SET encryptedPassword = $2, deviceToken = $3""",
                    username,
                    encrypted_password,
                    device_token,
                )
            return True
        except Exception as e:
            logging.error(f"插入/更新用户 {username} 时出错: {e}")
            return False

    async def delete_user(self, username: str) -> bool:
        """删除用户"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        try:
            async with self._pool.acquire() as conn:
                # 检查用户是否存在
                user = await conn.fetchrow(
                    "SELECT * FROM users WHERE username = $1", username
                )
                if not user:
                    logging.info(f"用户 {username} 不存在，跳过删除")
                    return False

                await conn.execute("DELETE FROM users WHERE username = $1", username)
                logging.info(f"用户 {username} 已被删除")
            return True
        except Exception as e:
            logging.error(f"删除用户 {username} 时出错: {e}")
            return False

    async def delete_user_by_device_token(self, device_token: str) -> bool:
        """根据设备token删除用户"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM users WHERE deviceToken = $1", device_token
                )
            return True
        except Exception as e:
            logging.error(f"删除设备token {device_token} 对应的用户时出错: {e}")
            return False


    # OAuth 令牌管理方法
    async def upsert_user_tokens(
        self, username: str, access_token: str, refresh_token: str
    ) -> bool:
        """插入或更新用户OAuth令牌"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        try:
            async with self._pool.acquire() as conn:
                # 使用 ON CONFLICT 来处理插入或更新
                await conn.execute(
                    """INSERT INTO user_tokens (username, access_token, refresh_token, updated_at) 
                       VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                       ON CONFLICT (username) 
                       DO UPDATE SET access_token = $2, refresh_token = $3, updated_at = CURRENT_TIMESTAMP""",
                    username,
                    access_token,
                    refresh_token,
                )
                logging.info(f"用户 {username} 的令牌已更新")
            return True
        except Exception as e:
            logging.error(f"插入/更新用户 {username} 令牌时出错: {e}")
            return False

    async def revoke_user_tokens(self, username: str) -> bool:
        """撤销用户OAuth令牌"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        try:
            async with self._pool.acquire() as conn:
                # 检查用户是否存在
                user_tokens = await conn.fetchrow(
                    "SELECT * FROM user_tokens WHERE username = $1", username
                )
                if not user_tokens:
                    logging.info(f"用户 {username} 的令牌不存在，跳过撤销")
                    return False

                await conn.execute("DELETE FROM user_tokens WHERE username = $1", username)
                logging.info(f"用户 {username} 的令牌已被撤销")
            return True
        except Exception as e:
            logging.error(f"撤销用户 {username} 令牌时出错: {e}")
            return False

    async def get_user_tokens_status(self, username: str) -> Optional[Dict[str, Any]]:
        """获取用户OAuth令牌状态"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT username, 
                              CASE WHEN access_token IS NOT NULL THEN 'active' ELSE 'inactive' END as token_status,
                              created_at, 
                              updated_at 
                       FROM user_tokens 
                       WHERE username = $1""", 
                    username
                )
                if row:
                    return {
                        "username": row["username"],
                        "token_status": row["token_status"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                        "has_tokens": True
                    }
                else:
                    # 用户不存在令牌记录
                    return {
                        "username": username,
                        "token_status": "inactive",
                        "created_at": None,
                        "updated_at": None,
                        "has_tokens": False
                    }
        except Exception as e:
            logging.error(f"获取用户 {username} 令牌状态时出错: {e}")
            return None

    async def get_user_tokens(self, username: str) -> Optional[Dict[str, Optional[str]]]:
        """获取用户的OAuth令牌"""
        await self.init_db()
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化")
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT access_token, refresh_token, created_at, updated_at FROM user_tokens WHERE username = $1", 
                    username
                )
                if row:
                    return {
                        "access_token": row["access_token"],
                        "refresh_token": row["refresh_token"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
                    }
                else:
                    return None
        except Exception as e:
            logging.error(f"获取用户 {username} 令牌时出错: {e}")
            return None


# 全局数据库管理器实例
db_manager = DatabaseManager()
