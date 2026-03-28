# config.py
# Application settings via pydantic-settings.
# All module-level variables remain importable for backward compatibility.
import logging
from pathlib import Path
from typing import Optional

import pytz
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load configuration from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Required ---
    database_url: str = ""

    # --- Redis ---
    redis_url: str = "redis://localhost:6379"

    # --- APNs ---
    apns_key_file: Optional[str] = None
    apns_key_id: Optional[str] = None
    apns_team_id: Optional[str] = None
    apns_topic: Optional[str] = None
    apns_use_sandbox: bool = False

    # --- Logging ---
    log_level: str = "ERROR"
    log_file: str = "./next.log"

    # --- Google OAuth ---
    client_id: Optional[str] = None

    # --- HTTP / Notifications ---
    http_proxy: Optional[str] = None
    notification_api_url: Optional[str] = None

    # --- Monitor tuning ---
    monitor_max_concurrent: int = 3
    monitor_interval_seconds: int = 300

    # --- Feature toggles ---
    enable_monitor_push: bool = True
    enable_daily_push: bool = True

    # --- Gakuen ---
    gakuen_base_url: str = "https://next.tama.ac.jp"

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# Instantiate settings (reads .env once at import time)
# ---------------------------------------------------------------------------
settings = Settings()

# ---------------------------------------------------------------------------
# Set up structured logging as early as possible
# ---------------------------------------------------------------------------
from tutnext.logging_config import setup_logging  # noqa: E402

setup_logging(settings.log_level, settings.log_file)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve APNs key content
# ---------------------------------------------------------------------------
_apns_key_content: Optional[str] = None
if settings.apns_key_file:
    try:
        with open(settings.apns_key_file, "r") as _f:
            _apns_key_content = _f.read()
    except FileNotFoundError:
        _logger.error("APNs private key file not found: %s", settings.apns_key_file)
    except Exception as _e:
        _logger.error("Error reading APNs private key file: %s", _e)

# Warn (not raise) when APNs is not fully configured so the app can still
# start in environments that don't use push notifications.
if not all([_apns_key_content, settings.apns_key_id, settings.apns_team_id, settings.apns_topic]):
    _logger.warning(
        "APNs configuration is incomplete. Push notifications will be disabled. "
        "Check .env and ensure the key file exists."
    )

# ---------------------------------------------------------------------------
# Backward-compatible module-level exports
# ---------------------------------------------------------------------------
JAPAN_TZ = pytz.timezone("Asia/Tokyo")

DATABASE_URL: str = settings.database_url

APNS_KEY_CONTENT: Optional[str] = _apns_key_content

APNS_CONFIG: dict = {
    "key": APNS_KEY_CONTENT,
    "key_id": settings.apns_key_id,
    "team_id": settings.apns_team_id,
    "topic": settings.apns_topic,
    "use_sandbox": settings.apns_use_sandbox,
}

HTTP_PROXY: Optional[str] = settings.http_proxy

NOTIFICATION_API_URL: Optional[str] = settings.notification_api_url

LOG_LEVEL: str = settings.log_level
LOG_FILE: str = settings.log_file

# ---------------------------------------------------------------------------
# Redis client (async) — created from settings
# ---------------------------------------------------------------------------
from tutnext.core.redis import get_redis  # noqa: E402

redis = get_redis(settings.redis_url)
