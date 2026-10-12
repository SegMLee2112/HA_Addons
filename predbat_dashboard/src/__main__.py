"""The add-on: starts the web page, the Home Assistant link and the runner
(src/runner.py) that keeps the dashboard's figures up to date, and stops
them cleanly when Home Assistant stops the add-on.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Optional

from aiohttp import web

from src import log_filters
from src.api import create_api_app
from src.app_settings import AppSettings
from src.config import load_config
from src.debug_tools import DebugTools, install_log_buffer
from src.ha_link import HaLink
from src.health import Health
from src.layout import Layout
from src.runner import Runner
from src.shared_state import SharedState

logger = logging.getLogger("predbat_dashboard")

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATA_DIR = os.environ.get("ADDON_DATA", "/data")
PORT = 8099  # ingress_port in config.yaml defaults to 8099


async def worker(runner: Runner, link: HaLink) -> None:
    """Finds Predbat's entities (again every few minutes, and when the prefix changes) and
    refreshes the dashboard's figures within a second of a change."""
    was_connected = False
    last_refresh = 0.0
    while True:
        try:
            if link.connected:
                await runner.discover(force=not was_connected)
            was_connected = link.connected
            now = asyncio.get_running_loop().time()
            if runner.dirty or now - last_refresh >= 30:  # (and every 30 s: what "now" is moves on)
                runner.refresh()
                last_refresh = now
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Updating the dashboard failed", exc_info=True)
            await asyncio.sleep(5)
        await asyncio.sleep(1)


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    log_filters.install()
    log_buffer = install_log_buffer(LOG_FORMAT)
    data_dir: Optional[str] = DATA_DIR if os.path.isdir(DATA_DIR) else None

    settings = AppSettings(data_dir)  # applies a new log level itself
    settings.apply_log_level()
    config = load_config()
    state = SharedState()
    health = Health()
    layout = Layout(data_dir)

    link = HaLink(data_dir, state)
    runner = Runner(link, layout, settings, state)
    link.on_entity_changed = runner.entity_changed

    def settings_changed(changes: dict) -> None:
        if "prefix" in changes:
            runner.rediscover = True  # look for the new prefix's entities
        runner.dirty = True
    settings.on_change = settings_changed
    debug = DebugTools(config=config, log_buffer=log_buffer, settings=settings)

    app = create_api_app(state, ha_link=link, health=health, debug=debug, app_settings=settings, runner=runner,
                         layout=layout)
    web_runner = web.AppRunner(app)  # (not `runner`: that's the dashboard's Runner, used below)
    await web_runner.setup()
    await web.TCPSite(web_runner, "0.0.0.0", PORT).start()
    logger.info("Web page and API on port %d (version %s)", PORT, health.version)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows, when run by hand
            pass

    tasks = [asyncio.create_task(link.run()), asyncio.create_task(worker(runner, link))]
    try:
        await stop.wait()
        logger.info("Stopping")
    finally:
        state.status = "Stopped"
        try:
            await asyncio.wait_for(link.shutdown(), 5)  # its sensors show unavailable while stopped
        except Exception:
            logger.debug("Couldn't mark the sensors unavailable", exc_info=True)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await web_runner.cleanup()
        logger.info("Stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
