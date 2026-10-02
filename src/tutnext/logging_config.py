# logging_config.py
# Named to avoid shadowing the stdlib `logging` module.
import logging
import logging.config
from pathlib import Path
from typing import Optional


def setup_logging(log_level: str = "ERROR", log_file: Optional[str] = "./next.log") -> None:
    """Configure root logger using dictConfig with console output and, when a
    log file is given, daily file rotation.

    Pass ``log_file=None`` (Cloudflare Workers) for console-only logging: the
    Workers runtime captures stdout into Workers Logs / observability.
    """
    log_format = "[%(levelname)s]%(asctime)s [%(name)s:%(funcName)s:%(lineno)d] -> %(message)s"
    level = log_level.upper()

    handlers: dict = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "standard",
            "level": level,
        },
    }
    root_handlers = ["console"]

    if log_file:
        # Ensure the parent directory for the log file exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(log_path),
            "when": "midnight",
            "interval": 1,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "standard",
            "level": level,
        }
        root_handlers.append("file")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": log_format,
                },
            },
            "handlers": handlers,
            "root": {
                "level": level,
                "handlers": root_handlers,
            },
        }
    )
