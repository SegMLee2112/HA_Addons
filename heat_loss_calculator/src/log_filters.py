"""Keeps the add-on's log readable: the web page asks for its data every few
seconds while it's open, so those successful GETs are logged at DEBUG, not
INFO. Commands (POST) and failed requests stay at INFO."""
from __future__ import annotations

import logging
import re

# A successful GET from the web page: "GET /api/state HTTP/1.1" 200 ...
_PAGE_GET = re.compile(r'"(?:GET|HEAD) \S+ HTTP/[\d.]+" (?:2\d\d|304) ')


class DemoteWebPageRequests(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.INFO or not _PAGE_GET.search(record.getMessage()):
            return True
        # The log level is set on the root logger. getEffectiveLevel, not
        # isEnabledFor, which caches its answer.
        if logging.getLogger().getEffectiveLevel() > logging.DEBUG:
            return False
        record.levelno = logging.DEBUG
        record.levelname = logging.getLevelName(logging.DEBUG)
        return True


def install() -> None:
    logging.getLogger("aiohttp.access").addFilter(DemoteWebPageRequests())
