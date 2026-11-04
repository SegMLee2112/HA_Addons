"""The add-on: starts the web page, the Home Assistant link and the calculation, and stops them
cleanly when Home Assistant stops the add-on.

The calculation (src/calculator.py) brings the days up to date from Home Assistant's history
every UPDATE_S (and straight away when the settings change); the live readings for the page
come from the entities the link follows.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import signal
from typing import Optional

from aiohttp import web

from src import heat_loss, log_filters
from src.api import create_api_app
from src.app_settings import AppSettings
from src.calc_settings import CalcSettings
from src.calculator import Calculator
from src.config import load_config
from src.debug_tools import DebugTools, install_log_buffer
from src.ha_link import HaLink
from src.health import Health
from src.shared_state import SharedState

logger = logging.getLogger("heat_loss_calculator")

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATA_DIR = os.environ.get("ADDON_DATA", "/data")
PORT = 8099  # ingress_port in config.yaml defaults to 8099
UPDATE_S = 30 * 60


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_readings(link: HaLink, calc: Calculator) -> dict:
    """Now: the outside, each floor's and the area-weighted inside temperature, the flow
    temperature and today's gas so far."""
    s = calc.settings.data
    get = lambda entity: heat_loss.value_of(link.states.get(entity) or {}, heat_loss.temperature_attribute(entity)) \
        if entity else None  # noqa: E731
    floors = [{"name": f["name"], "entity": f.get("entity"), "area": f.get("area"), "temp": get(f.get("entity"))}
              for f in s["floors"]]
    pairs = [(f["temp"], f["area"] or 1.0) for f in floors if f["temp"] is not None]
    inside = sum(t * a for t, a in pairs) / sum(a for _, a in pairs) if pairs else None
    flow = get(link.settings.get("flow")) if s["flow_mode"] == "sensor" else s["flow_fixed"]
    return {"outside": get(link.settings.get("outside")), "inside": None if inside is None else round(inside, 2),
            "floors": floors, "flow": flow, "gas_today": get(link.settings.get("gas"))}


def describe(state: SharedState, calc: Calculator) -> None:
    r = calc.result
    if state.missing:
        state.status = "Needs setting up"
        state.status_note = "Pick " + ", ".join(state.missing) + " on the Settings tab."
    elif r.get("ok"):
        state.status = "Working"
        state.status_note = f"From {r['days']} days, ΔT {r['dt_range'][0]:g} to {r['dt_range'][1]:g} °C."
    elif not calc.days:
        state.status = "Collecting days"
        state.status_note = "Reading Home Assistant's history: the first days appear shortly."
    else:
        state.status = "Collecting days"
        state.status_note = r.get("why") or ""


async def calculation(state: SharedState, link: HaLink, calc: Calculator, wake: asyncio.Event) -> None:
    """Bring the days up to date every UPDATE_S, or when woken (the settings changed)."""
    while True:
        if link.connected:
            try:
                if not link.settings.get("gas"):
                    found = await link.find_gas_sensor()
                    if found:
                        logger.info("Using the gas sensor %s", found)
                        await link.update_settings({"gas": found})
                state.missing = calc.missing(link.settings)
                done = await calc.update(link, link.settings)
                if done:
                    logger.info("%d day%s summarised", done, "" if done == 1 else "s")
                state.error = None
                state.last_run = _iso_now()
            except Exception as err:
                state.error = calc.error = str(err) or type(err).__name__
                logger.warning("Couldn't bring the days up to date: %s", state.error, exc_info=True)
        state.result, state.days = calc.result, calc.day_list()
        state.live = live_readings(link, calc)
        describe(state, calc)
        try:
            await asyncio.wait_for(wake.wait(), UPDATE_S if link.connected else 10)
        except asyncio.TimeoutError:
            pass
        wake.clear()


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
    wake = asyncio.Event()

    calc_settings = CalcSettings(data_dir)
    calc = Calculator(data_dir, calc_settings)
    state.result, state.days = calc.result, calc.day_list()

    def calc_changed(raw: dict) -> None:
        """Your home changed (src/calc_settings.py): the floors, flow temperature or hot water change the
        days themselves; the rest just the fit."""
        if {"floors", "flow_mode", "flow_fixed", "efficiency", "hot_water", "hot_water_kwh"} & set(raw):
            calc.redo = True
        if "floors" in raw:
            link.watch_again()
        calc.refit()
        state.result = calc.result
        state.live = live_readings(link, calc)
        state.missing = calc.missing(link.settings)
        describe(state, calc)
        wake.set()

    calc_settings.on_change = calc_changed

    async def entity_changed(entity_id: str, st: Optional[dict]) -> None:
        """A followed entity changed in Home Assistant (src/ha_link.py): the live readings."""
        state.live = live_readings(link, calc)

    link = HaLink(data_dir, state, on_entity_changed=entity_changed)
    link.extra_watched = lambda: [f["entity"] for f in calc_settings.data["floors"] if f.get("entity")]
    original_update = link.update_settings

    async def update_settings(raw: dict) -> dict:
        new = await original_update(raw)
        calc.redo = True  # another gas, outside or flow sensor: the days again
        state.missing = calc.missing(link.settings)
        wake.set()
        return new

    link.update_settings = update_settings
    debug = DebugTools(config=config, log_buffer=log_buffer, settings=settings)

    app = create_api_app(state, ha_link=link, health=health, debug=debug, app_settings=settings,
                         calc=calc, wake=wake)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info("Web page and API on port %d (version %s)", PORT, health.version)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows, when run by hand
            pass

    tasks = [asyncio.create_task(link.run()), asyncio.create_task(calculation(state, link, calc, wake))]
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
        await runner.cleanup()
        logger.info("Stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
