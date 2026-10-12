"""Diagnostics › Health: the add-on's version and uptime."""
from __future__ import annotations

import datetime
import os
import time
from typing import Callable, Optional


class Health:
    def __init__(self, version: Optional[str] = None, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.version = version or os.environ.get("ADDON_VERSION") or "dev"
        self.started = self._clock()

    def snapshot(self) -> dict:
        return {
            "version": self.version,
            "started": datetime.datetime.fromtimestamp(self.started, datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "uptime_s": int(self._clock() - self.started),
        }
