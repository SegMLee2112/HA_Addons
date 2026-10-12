"""Diagnostics › Debug on the web page: the log level, recent log lines,
the add-on's options for a bug report (secrets hidden) and a restart."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

LOG_LINES = 500
LEVELS = ("debug", "info", "warning", "error")
SECRET_WORDS = ("password", "token", "secret", "key")  # options with these in their name are hidden


class LogBuffer(logging.Handler):
    """The last `size` log lines, formatted as in the add-on's log."""

    def __init__(self, size: int = LOG_LINES) -> None:
        super().__init__()
        self.lines: deque[str] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:
            pass


def install_log_buffer(fmt: str, size: int = LOG_LINES) -> LogBuffer:
    buffer = LogBuffer(size)
    buffer.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(buffer)
    return buffer


def redacted_config(config) -> dict:
    """The add-on's options, safe to share: anything that looks secret is hidden."""
    if config is None:
        return {}
    out = dataclasses.asdict(config) if dataclasses.is_dataclass(config) else dict(vars(config))
    return {k: ("***" if v and any(w in k.lower() for w in SECRET_WORDS) else v) for k, v in out.items()}


class DebugTools:
    def __init__(self, *, config=None, log_buffer: Optional[LogBuffer] = None, settings=None) -> None:
        self._config = config
        self._log_buffer = log_buffer
        self._settings = settings  # src.app_settings.AppSettings: the log level is saved there
        self._tasks: set = set()

    @staticmethod
    def log_level() -> str:
        return logging.getLevelName(logging.getLogger().getEffectiveLevel()).lower()

    def info(self, lines: int = 200) -> dict:
        logs = list(self._log_buffer.lines) if self._log_buffer is not None else []
        lines = max(0, min(int(lines), LOG_LINES))
        return {
            "log_level": self.log_level(),
            "config": redacted_config(self._config),
            "logs": logs[-lines:] if lines else [],
        }

    def set_log_level(self, level) -> dict:
        level = str(level or "").lower()
        if level not in LEVELS:
            raise ValueError("Log level must be one of: " + ", ".join(LEVELS))
        if self._settings is not None:
            self._settings.update({"log_level": level})  # saved and applied (src/app_settings.py)
        else:
            logging.getLogger().setLevel(getattr(logging, level.upper()))
        logger.warning("Log level set to %s from the web page", level)
        return {"log_level": level}

    async def restart(self) -> dict:
        """Ask the Supervisor to restart the add-on (a second later, so the page gets its answer)."""
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise ValueError("Can't reach the Supervisor: restart the add-on from Home Assistant")

        async def restart_soon() -> None:
            await asyncio.sleep(1)
            import aiohttp
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post("http://supervisor/addons/self/restart",
                                            headers={"Authorization": f"Bearer {token}"},
                                            timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status >= 400:
                            logger.warning("The Supervisor refused the restart (HTTP %s): %s",
                                           resp.status, (await resp.text())[:200])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Couldn't ask the Supervisor to restart the add-on", exc_info=True)

        logger.info("Restarting the add-on (asked on the web page)")
        task = asyncio.create_task(restart_soon())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"restarting": True}
