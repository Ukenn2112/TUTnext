# tutnext/core/database.py
"""User / OAuth-token persistence.

Two implementations with the same async interface:

* :class:`DatabaseManager`   — PostgreSQL via ``asyncpg`` (server mode)
* :class:`D1DatabaseManager` — Cloudflare D1 via the ``DB`` binding (Workers mode;
  schema lives in ``migrations/0001_init.sql``)

Row dicts use lower-case column names (``encryptedpassword``, ``devicetoken``)
in both backends, matching what PostgreSQL returns for unquoted identifiers.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from tutnext import runtime
from tutnext.config import DATABASE_URL, settings


class DatabaseManager:
    """数据库管理器，提供统一的数据库访问接口 (PostgreSQL / asyncpg)"""

    def __init__(self, db_url: Optional[str] = None):
        if db_url is None:
            # 从配置文件获取PostgreSQL连接URL
            self.db_url = DATABASE_URL
        else:
            self.db_url = db_url

        self._lock = asyncio.Lock()
        self._initialized = False
        self._pool: Any = None

    async def init_db(self):
        """初始化数据库，创建连接池和表结构"""
        import asyncpg

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

                    # 清理历史脏数据：删除 username/encryptedPassword/deviceToken 为空的记录
                    deleted = await conn.execute(
                        """
                    DELETE FROM users
                    WHERE username = '' OR encryptedPassword = '' OR deviceToken = ''
                    """
                    )
                    if deleted != "DELETE 0":
                        logging.warning(f"启动清理：已删除无效用户记录 ({deleted})")

                    # 为 deviceToken 创建索引，加速按设备token查询/删除
                    await conn.execute(
                        """
                    CREATE INDEX IF NOT EXISTS idx_users_devicetoken ON users (deviceToken);
                    """
                    )

                    # 添加 CHECK 约束，防止今后写入空字符串（已存在则忽略）
                    await conn.execute(
                        """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conname = 'users_nonempty_fields'
                        ) THEN
                            ALTER TABLE users
                            ADD CONSTRAINT users_nonempty_fields
                            CHECK (username <> '' AND encryptedPassword <> '' AND deviceToken <> '');
                        END IF;
                    END
                    $$;
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

    def _require_pool(self):
        if not self._pool:
            raise RuntimeError("数据库连接池未初始化，请先调用 init_db()")
        return self._pool

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """获取所有用户"""
        async with self._require_pool().acquire() as conn:
            rows = await conn.fetch("SELECT * FROM users")
            return [dict(row) for row in rows]

    async def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名获取用户"""
        async with self._require_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE username = $1", username
            )
            return dict(row) if row else None

    async def upsert_user(
        self, username: str, encrypted_password: str, device_token: str
    ) -> bool:
        """插入或更新用户"""
        if not username or not encrypted_password or not device_token:
            logging.warning(f"拒绝写入：用户名、密码或设备Token为空 (username={repr(username)})")
            return False
        pool = self._require_pool()
        try:
            async with pool.acquire() as conn:
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
        pool = self._require_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute("DELETE FROM users WHERE username = $1", username)
                if result == "DELETE 0":
                    logging.info(f"用户 {username} 不存在，跳过删除")
                    return False
                logging.info(f"用户 {username} 已被删除")
            return True
        except Exception as e:
            logging.error(f"删除用户 {username} 时出错: {e}")
            return False

    async def delete_user_by_device_token(self, device_token: str) -> bool:
        """根据设备token删除用户"""
        pool = self._require_pool()
        try:
            async with pool.acquire() as conn:
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
        pool = self._require_pool()
        try:
            async with pool.acquire() as conn:
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
        pool = self._require_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute("DELETE FROM user_tokens WHERE username = $1", username)
                if result == "DELETE 0":
                    logging.info(f"用户 {username} 的令牌不存在，跳过撤销")
                    return False
                logging.info(f"用户 {username} 的令牌已被撤销")
            return True
        except Exception as e:
            logging.error(f"撤销用户 {username} 令牌时出错: {e}")
            return False

    async def get_user_tokens_status(self, username: str) -> Optional[Dict[str, Any]]:
        """获取用户OAuth令牌状态"""
        pool = self._require_pool()
        try:
            async with pool.acquire() as conn:
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
        pool = self._require_pool()
        try:
            async with pool.acquire() as conn:
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


def _row_dict(row: Any) -> Dict[str, Any]:
    return {str(k): row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)


class D1DatabaseManager:
    """Same interface as :class:`DatabaseManager`, backed by Cloudflare D1.

    ``executor`` is a binding name (Workers) or an executor object from
    :mod:`tutnext.core.d1client` (the server uses the REST API one).
    """

    def __init__(self, executor: Any = "DB"):
        if isinstance(executor, str):
            from tutnext.core.d1client import BindingExecutor

            executor = BindingExecutor(executor)
        self._executor = executor
        self.db_url = "d1://" + getattr(executor, "binding_name", getattr(executor, "url", "http"))

    async def init_db(self):
        """Schema is managed by `migrations/` (wrangler/cf d1 migrations apply)."""
        return None

    async def close(self):
        return None

    async def _query(self, sql: str, params: tuple) -> Any:
        results = await self._executor.batch([(sql, params)])
        return results[0]

    async def _all(self, sql: str, *params: Any) -> List[Dict[str, Any]]:
        result = await self._query(sql, params)
        return [_row_dict(r) for r in (result["results"] or [])]

    async def _first(self, sql: str, *params: Any) -> Optional[Dict[str, Any]]:
        rows = await self._all(sql, *params)
        return rows[0] if rows else None

    async def _run(self, sql: str, *params: Any) -> int:
        result = await self._query(sql, params)
        try:
            return int(result["meta"]["changes"])
        except Exception:  # noqa: BLE001
            return 0

    async def get_all_users(self) -> List[Dict[str, Any]]:
        return await self._all("SELECT username, encryptedpassword, devicetoken FROM users")

    async def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        return await self._first(
            "SELECT username, encryptedpassword, devicetoken FROM users WHERE username = ?", username
        )

    async def upsert_user(self, username: str, encrypted_password: str, device_token: str) -> bool:
        if not username or not encrypted_password or not device_token:
            logging.warning(f"拒绝写入：用户名、密码或设备Token为空 (username={repr(username)})")
            return False
        try:
            await self._run(
                """INSERT INTO users (username, encryptedpassword, devicetoken) VALUES (?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET encryptedpassword = excluded.encryptedpassword,
                                                      devicetoken = excluded.devicetoken""",
                username,
                encrypted_password,
                device_token,
            )
            return True
        except Exception as e:
            logging.error(f"插入/更新用户 {username} 时出错: {e}")
            return False

    async def delete_user(self, username: str) -> bool:
        try:
            changes = await self._run("DELETE FROM users WHERE username = ?", username)
            if changes == 0:
                logging.info(f"用户 {username} 不存在，跳过删除")
                return False
            logging.info(f"用户 {username} 已被删除")
            return True
        except Exception as e:
            logging.error(f"删除用户 {username} 时出错: {e}")
            return False

    async def delete_user_by_device_token(self, device_token: str) -> bool:
        try:
            await self._run("DELETE FROM users WHERE devicetoken = ?", device_token)
            return True
        except Exception as e:
            logging.error(f"删除设备token {device_token} 对应的用户时出错: {e}")
            return False

    async def upsert_user_tokens(self, username: str, access_token: str, refresh_token: str) -> bool:
        try:
            await self._run(
                """INSERT INTO user_tokens (username, access_token, refresh_token) VALUES (?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET access_token = excluded.access_token,
                                                      refresh_token = excluded.refresh_token,
                                                      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
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
        try:
            changes = await self._run("DELETE FROM user_tokens WHERE username = ?", username)
            if changes == 0:
                logging.info(f"用户 {username} 的令牌不存在，跳过撤销")
                return False
            logging.info(f"用户 {username} 的令牌已被撤销")
            return True
        except Exception as e:
            logging.error(f"撤销用户 {username} 令牌时出错: {e}")
            return False

    async def get_user_tokens_status(self, username: str) -> Optional[Dict[str, Any]]:
        try:
            row = await self._first(
                "SELECT username, access_token, created_at, updated_at FROM user_tokens WHERE username = ?",
                username,
            )
            if row:
                return {
                    "username": row["username"],
                    "token_status": "active" if row["access_token"] else "inactive",
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "has_tokens": True,
                }
            return {
                "username": username,
                "token_status": "inactive",
                "created_at": None,
                "updated_at": None,
                "has_tokens": False,
            }
        except Exception as e:
            logging.error(f"获取用户 {username} 令牌状态时出错: {e}")
            return None

    async def get_user_tokens(self, username: str) -> Optional[Dict[str, Optional[str]]]:
        try:
            row = await self._first(
                "SELECT access_token, refresh_token, created_at, updated_at FROM user_tokens WHERE username = ?",
                username,
            )
            if row:
                return {
                    "access_token": row["access_token"],
                    "refresh_token": row["refresh_token"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None
        except Exception as e:
            logging.error(f"获取用户 {username} 令牌时出错: {e}")
            return None


# 全局数据库管理器实例
def _make_db_manager() -> Any:
    if runtime.IS_WORKERS:
        return D1DatabaseManager("DB")
    if settings.storage_backend == "d1":
        from tutnext.core.d1client import get_http_executor

        return D1DatabaseManager(get_http_executor())
    return DatabaseManager()


db_manager: Any = _make_db_manager()
