# logging_config.py
# Named to avoid shadowing the stdlib `logging` module.
import logging
import logging.config
from pathlib import Path


def setup_logging(log_level: str = "ERROR", log_file: str = "./next.log") -> None:
    """Configure root logger using dictConfig with file rotation and console output."""

    # Ensure the parent directory for the log file exists
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_format = "[%(levelname)s]%(asctime)s [%(name)s:%(funcName)s:%(lineno)d] -> %(message)s"
    level = log_level.upper()

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": log_format,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "standard",
                    "level": level,
                },
                "file": {
                    "class": "logging.handlers.TimedRotatingFileHandler",
                    "filename": str(log_path),
                    "when": "midnight",
                    "interval": 1,
                    "backupCount": 5,
                    "encoding": "utf-8",
                    "formatter": "standard",
                    "level": level,
                },
            },
            "root": {
                "level": level,
                "handlers": ["console", "file"],
            },
        }
    )
