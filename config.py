# config.py
import pytz
import logging
from redis import asyncio as aioredis
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 从环境变量获取配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR")
LOG_FILE = os.getenv("LOG_FILE", "./next.log")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="[%(levelname)s]%(asctime)s [%(funcName)s:%(lineno)d] -> %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="UTF-8"),
        logging.StreamHandler(),
    ],
)

# 日本时区
JAPAN_TZ = pytz.timezone("Asia/Tokyo")

# redis 配置
redis = aioredis.from_url("redis://localhost:6379")

# PostgreSQL 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# Apple Push Notification Service (APNs) 配置
APNS_CONFIG = {
    "key": os.getenv("APNS_KEY_FILE"),
    "key_id": os.getenv("APNS_KEY_ID"),
    "team_id": os.getenv("APNS_TEAM_ID"),
    "topic": os.getenv("APNS_TOPIC"),
    "use_sandbox": os.getenv("APNS_USE_SANDBOX", "false").lower() == "true",
}

# 验证必需的 APNS 配置
if not all([APNS_CONFIG["key_id"], APNS_CONFIG["team_id"], APNS_CONFIG["topic"]]):
    raise ValueError("APNS configuration is incomplete. Please check your .env file.")
