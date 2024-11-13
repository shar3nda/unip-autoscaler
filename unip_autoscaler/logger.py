import logging

from .settings import DEBUG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

if DEBUG:
    logger.setLevel(logging.DEBUG)
else:
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
