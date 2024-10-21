# app/config.py
import logging

logging.basicConfig(
    level=logging.WARNING,
    format="[%(levelname)s]%(asctime)s [%(funcName)s:%(lineno)d] -> %(message)s",
    handlers=[
        logging.FileHandler("./next.log", encoding="UTF-8"),
        logging.StreamHandler(),
    ],
)
