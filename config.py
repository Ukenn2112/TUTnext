# config.py
import pytz
import logging
from logging.handlers import TimedRotatingFileHandler
from redis import asyncio as aioredis
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 从环境变量获取配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR")
LOG_FILE = os.getenv("LOG_FILE", "./next.log")

# 创建带日期轮转的文件处理器
file_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",  # 每天午夜轮转
    interval=1,  # 每1天
    backupCount=5,  # 保留5个备份文件
    encoding="UTF-8",
)
# 设置日期后缀格式，会产生如 2023-10-15-next.log 的文件名
file_handler.suffix = "%Y-%m-%d"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="[%(levelname)s]%(asctime)s [%(funcName)s:%(lineno)d] -> %(message)s",
    handlers=[
        file_handler,
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
APNS_KEY_FILE_PATH = os.getenv("APNS_KEY_FILE")
APNS_KEY_CONTENT = None

# 读取 APNs 私钥文件内容
if APNS_KEY_FILE_PATH:
    try:
        with open(APNS_KEY_FILE_PATH, "r") as f:
            APNS_KEY_CONTENT = f.read()
    except FileNotFoundError:
        logging.error(f"APNs 私钥文件未找到: {APNS_KEY_FILE_PATH}")
        APNS_KEY_CONTENT = None
    except Exception as e:
        logging.error(f"读取 APNs 私钥文件时出错: {e}")
        APNS_KEY_CONTENT = None

APNS_CONFIG = {
    "key": APNS_KEY_CONTENT,
    "key_id": os.getenv("APNS_KEY_ID"),
    "team_id": os.getenv("APNS_TEAM_ID"),
    "topic": os.getenv("APNS_TOPIC"),
    "use_sandbox": os.getenv("APNS_USE_SANDBOX", "false").lower() == "true",
}

# 验证必需的 APNS 配置
if not all(
    [
        APNS_CONFIG["key"],
        APNS_CONFIG["key_id"],
        APNS_CONFIG["team_id"],
        APNS_CONFIG["topic"],
    ]
):
    raise ValueError(
        "APNS configuration is incomplete. Please check your .env file and ensure the key file exists."
    )

HTTP_PROXY = os.getenv("HTTP_PROXY")  # 可选的 HTTP 代理设置