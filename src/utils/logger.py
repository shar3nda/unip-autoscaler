import logging
from logging.config import dictConfig

from src.settings import DEBUG


def setup_logging():
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s in %(name)s: %(message)s",
            },
            "detailed": {
                "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d]: %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "detailed",
                "level": "DEBUG",
            },
        },
        "root": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "loggers": {
            "uvicorn": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "uvicorn.error": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
        },
    }

    dictConfig(logging_config)


setup_logging()
logger = logging.getLogger(__name__)

if DEBUG:
    logger.setLevel(logging.DEBUG)
else:
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
