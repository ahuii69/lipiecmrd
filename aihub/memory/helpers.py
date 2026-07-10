from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger(__name__)


def log_info(message: str, channel: str = "MEMORY") -> None:
    logger.info("[%s] %s", channel, message)


def log_warning(message: str, channel: str = "MEMORY") -> None:
    logger.warning("[%s] %s", channel, message)


def log_error(message: str, channel: str = "MEMORY") -> None:
    logger.error("[%s] %s", channel, message)


def tokenize(text: str) -> List[str]:
    return re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", (text or "").lower())
