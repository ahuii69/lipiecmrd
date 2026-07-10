import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from aihub.core.config import settings


def setup_logging() -> None:
    os.makedirs(settings.data_dir, exist_ok=True)
    log_path = os.path.join(settings.data_dir, "aihub.log")

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
