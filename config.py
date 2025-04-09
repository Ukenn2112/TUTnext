# config.py
import pytz
import logging
import aioredis

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s]%(asctime)s [%(funcName)s:%(lineno)d] -> %(message)s",
    handlers=[
        logging.FileHandler("./next.log", encoding="UTF-8"),
        logging.StreamHandler(),
    ],
)

# 日本时区
JAPAN_TZ = pytz.timezone("Asia/Tokyo")

# redis 配置
redis = aioredis.from_url("redis://localhost:6379")

# Apple Push Notification Service (APNs) 配置
APNS_CONFIG = {
    "key": "AuthKey_XJMT5JFC32.p8",
    "key_id": "XJMT5JFC32",
    "team_id": "4H88DC6RMN",
    "topic": "com.meikenn.tama",
    "use_sandbox": False,
}