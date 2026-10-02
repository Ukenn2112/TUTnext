# config.py
# Application settings via pydantic-settings.
#
# Two runtimes share this module (see tutnext.runtime):
#   * server  — settings come from environment variables / .env at import time
#   * workers — settings come from Cloudflare Worker vars & secrets, which only
#               exist per invocation, so everything env-dependent is lazy.
# All historical module-level names remain importable for backward compatibility.
import logging
from typing import Any, Optional

import pytz
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tutnext import runtime

IS_WORKERS = runtime.IS_WORKERS


class Settings(BaseSettings):
    """Load configuration from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Required (server mode) ---
    database_url: str = ""

    # --- Redis (server mode; Workers use D1 instead) ---
    redis_url: str = "redis://localhost:6379"

    # --- APNs ---
    apns_key_file: Optional[str] = None
    apns_private_key: Optional[str] = None  # PEM content (Workers secret APNS_PRIVATE_KEY)
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
    classmate_max_concurrent: int = 50  # Layer 5 同班即时检查并发上限

    # --- Feature toggles ---
    enable_monitor_push: bool = True
    enable_daily_push: bool = True
    enable_bus_scraper: bool = True  # server: weekly bus timetable job (off in hybrid, the Worker does it)
    enable_live_activity_dispatch: bool = True  # server: 10 s Live Activity dispatcher (off in hybrid)

    # --- Operator diagnostics (/admin/*); unset = routes answer 404 ---
    admin_key: Optional[str] = None

    # --- Storage backend (server) ---
    # "local": PostgreSQL + Redis (classic).  "d1": users/tokens and the shared cache keys live in
    # Cloudflare D1 via the REST API so the server and the Worker see one state (hybrid deployment).
    storage_backend: str = "local"
    cf_account_id: Optional[str] = None
    cf_d1_database_id: Optional[str] = None
    cf_api_token: Optional[str] = None

    # --- Gakuen ---
    gakuen_base_url: str = "https://next.tama.ac.jp"

    # --- Lima proxy watchdog ---
    lima_vm_name: Optional[str] = None
    watchdog_window_seconds: float = 180.0
    watchdog_failure_threshold: int = 5
    watchdog_cooldown_seconds: float = 900.0

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# Settings construction
# ---------------------------------------------------------------------------


def _build_settings() -> Settings:
    if not IS_WORKERS:
        return Settings()
    # Worker vars/secrets → Settings kwargs (case-insensitive field match).
    fields = set(Settings.model_fields)
    kwargs: dict[str, Any] = {}
    for key, value in runtime.env_vars().items():
        name = key.lower()
        if name in fields:
            kwargs[name] = value
    return Settings(_env_file=None, **kwargs)


class _LazySettings:
    """Proxy that instantiates :class:`Settings` on first attribute access.

    In Workers the env is unavailable while the module graph is imported (and
    snapshotted at deploy time), so the real object is created lazily.
    """

    _instance: Optional[Settings] = None

    def _get(self) -> Settings:
        if self._instance is None:
            self._instance = _build_settings()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def reload(self) -> Settings:
        self._instance = _build_settings()
        return self._instance


settings: Any = _LazySettings() if IS_WORKERS else Settings()

# ---------------------------------------------------------------------------
# Set up structured logging as early as possible
# ---------------------------------------------------------------------------
from tutnext.logging_config import setup_logging  # noqa: E402

if IS_WORKERS:
    setup_logging("INFO", None)  # console only; level is re-applied once env is readable
else:
    setup_logging(settings.log_level, settings.log_file)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# APNs configuration
# ---------------------------------------------------------------------------


def _read_apns_key() -> Optional[str]:
    if settings.apns_private_key:
        return settings.apns_private_key.replace("\\n", "\n")
    if settings.apns_key_file and not IS_WORKERS:
        try:
            with open(settings.apns_key_file) as _f:
                return _f.read()
        except FileNotFoundError:
            _logger.error("APNs private key file not found: %s", settings.apns_key_file)
        except Exception as _e:  # noqa: BLE001
            _logger.error("Error reading APNs private key file: %s", _e)
    return None


_apns_config_cache: Optional[dict] = None


def get_apns_config() -> dict:
    """Return the APNs configuration dict (``key``, ``key_id``, ``team_id``, ``topic``, ``use_sandbox``)."""
    global _apns_config_cache
    if _apns_config_cache is None:
        key = _read_apns_key()
        _apns_config_cache = {
            "key": key,
            "key_id": settings.apns_key_id,
            "team_id": settings.apns_team_id,
            "topic": settings.apns_topic,
            "use_sandbox": settings.apns_use_sandbox,
        }
        if not all([key, settings.apns_key_id, settings.apns_team_id, settings.apns_topic]):
            _logger.warning(
                "APNs configuration is incomplete. Push notifications will be disabled. "
                "Check .env / Worker secrets and ensure the key is available."
            )
    return _apns_config_cache


def live_activity_topic() -> str:
    """APNs topic for Live Activity pushes (main app bundle ID + suffix)."""
    return f"{get_apns_config()['topic']}.push-type.liveactivity"


# ---------------------------------------------------------------------------
# Backward-compatible module-level exports
# ---------------------------------------------------------------------------
JAPAN_TZ = pytz.timezone("Asia/Tokyo")

if IS_WORKERS:
    # Values that depend on the env are unavailable at import time in Workers;
    # callers in the Workers path use `settings.*` / `get_apns_config()` instead.
    DATABASE_URL: str = ""
    APNS_KEY_CONTENT: Optional[str] = None
    APNS_CONFIG: dict = {}
    HTTP_PROXY: Optional[str] = None  # Cloudflare cannot use the LAN proxy
    NOTIFICATION_API_URL: Optional[str] = None
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = ""
else:
    DATABASE_URL = settings.database_url
    APNS_CONFIG = get_apns_config()
    APNS_KEY_CONTENT = APNS_CONFIG["key"]
    HTTP_PROXY = settings.http_proxy
    NOTIFICATION_API_URL = settings.notification_api_url
    LOG_LEVEL = settings.log_level
    LOG_FILE = settings.log_file

# ---------------------------------------------------------------------------
# Key/value store client — Redis (server) or D1-backed (Workers)
# ---------------------------------------------------------------------------
if IS_WORKERS:
    from tutnext.core.d1redis import D1Redis

    redis: Any = D1Redis("DB")
else:
    from tutnext.core.redis import get_redis  # noqa: E402

    redis = get_redis(settings.redis_url)
    if settings.storage_backend == "d1":
        # Hybrid deployment: shared keys (la:*, room:*, schedule:ical:*, *:kadai) go to D1 over
        # HTTP so the Worker API and this server share them; monitor-private keys stay local.
        from tutnext.core.d1client import get_http_executor  # noqa: E402
        from tutnext.core.d1redis import D1Redis  # noqa: E402
        from tutnext.core.hybridkv import HybridRedis  # noqa: E402

        redis = HybridRedis(local=redis, remote=D1Redis(get_http_executor(), lazy_purge=False))
