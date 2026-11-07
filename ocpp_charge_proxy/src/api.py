from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Awaitable

from aiohttp import web

from src.charger_sim import VALID_CURRENT_SETTINGS
from src.shared_state import SharedState

logger = logging.getLogger(__name__)


def _error(message: str, status: int = 400) -> web.Response:
    """A failed request: {"status": "error", "message"} with its HTTP status."""
    return web.json_response({"status": "error", "message": message}, status=status)


def create_api_app(
    shared_state: SharedState,
    on_plug: Callable[[], Awaitable[None]],
    on_unplug: Callable[[], Awaitable[None]],
    on_set_current: Callable[[int], Awaitable[None]],
    on_set_power: Callable[[float | None], Awaitable[None]] | None = None,
    on_refresh: Callable[[], None] | None = None,
    on_set_soc: Callable[[float | None], Awaitable[None]] | None = None,
    gui=None,
    automation=None,
    on_set_ramp: Callable[[float, float], None] | None = None,
    ha_link=None,
    on_charge_now: Callable[[float, bool], Awaitable[dict]] | None = None,
    debug=None,
    app_settings=None,
    on_cancel_charge: Callable[[], Awaitable[dict]] | None = None,
    mover=None,
    news=None,
) -> web.Application:
    app = web.Application(client_max_size=50 * 1024 * 1024)  # (an imported settings file, with its history)
    app["shared_state"] = shared_state
    app["on_plug"] = on_plug
    app["on_unplug"] = on_unplug
    app["on_set_current"] = on_set_current
    app["on_set_power"] = on_set_power
    app["on_refresh"] = on_refresh
    app["on_set_soc"] = on_set_soc
    app["events_stop"] = asyncio.Event()
    app["gui"] = gui  # src.gui_data.GuiSources, or None (GUI tabs then empty)
    app["automation"] = automation  # src.automation.Automation, or None
    app["ha_link"] = ha_link  # src.ha_link.HaLink, or None
    app["on_set_ramp"] = on_set_ramp
    app["on_charge_now"] = on_charge_now
    app["on_cancel_charge"] = on_cancel_charge
    app["mover"] = mover  # src.migrate.Mover: Diagnostics › Move to another install
    app["debug"] = debug  # src.debug_tools.DebugTools, or None
    app["app_settings"] = app_settings  # src.app_settings.AppSettings, or None
    app["news"] = news  # src.supplier_news.SupplierNews, or None
    # Open /api/events streams (web pages)
    app["event_clients"] = {"gui": 0}
    app.on_shutdown.append(_close_event_streams)

    # Ingress serves the status page — use relative path for API calls
    static_dir = Path(__file__).parent / "static"
    app.router.add_get("/", handle_index)
    app["static_dir"] = static_dir
    app.router.add_get("/api/state", handle_get_state)
    app.router.add_post("/api/plug", handle_plug)
    app.router.add_post("/api/unplug", handle_unplug)
    app.router.add_post("/api/charge_now", handle_charge_now)
    app.router.add_post("/api/charge_now/cancel", handle_cancel_charge)
    app.router.add_get("/api/migrate/export", handle_migrate_export)
    app.router.add_post("/api/migrate/check", handle_migrate_check)
    app.router.add_post("/api/migrate/import", handle_migrate_import)
    app.router.add_post("/api/current", handle_current)
    app.router.add_post("/api/power", handle_power)
    app.router.add_post("/api/soc", handle_soc)
    app.router.add_post("/api/ramp", handle_ramp)
    app.router.add_get("/api/events", handle_events)
    # The web page's tabs
    app.router.add_get("/api/messages", handle_messages)
    app.router.add_get("/api/sessions", handle_sessions)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/api/energy/daily", handle_energy_daily)
    app.router.add_get("/api/provider", handle_provider)
    app.router.add_post("/api/supplier_news/seen", handle_news_seen)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/version", handle_version)
    # Schedule and auto re-plug
    app.router.add_get("/api/automation", handle_automation)
    app.router.add_post("/api/automation/schedule", handle_schedule)
    app.router.add_post("/api/automation/replug", handle_replug)
    app.router.add_post("/api/automation/skip", handle_skip)
    # Your HA sensors (Settings tab)
    app.router.add_get("/api/sensors", handle_get_sensors)
    app.router.add_post("/api/sensors", handle_set_sensors)
    app.router.add_get("/api/sensors/entities", handle_sensor_entities)
    app.router.add_get("/api/notify/services", handle_notify_services)
    app.router.add_post("/api/notify/test", handle_notify_test)
    app.router.add_get("/api/smart_charging", handle_smart_charging)
    # Settings made on the web page (log level, continue session)
    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_post("/api/settings", handle_set_settings)
    # Diagnostics › Debug
    app.router.add_get("/api/debug/info", handle_debug_info)
    app.router.add_post("/api/debug/send", handle_debug_send)
    app.router.add_post("/api/debug/drop", handle_debug_drop)
    app.router.add_post("/api/debug/restart", handle_debug_restart)
    app.router.add_post("/api/debug/log_level", handle_debug_log_level)
    app.router.add_post("/api/debug/clear", handle_debug_clear)

    return app


_index_cache: dict = {}  # the page, as sent: read once (again only if the file changes)


def _index_body(index: Path) -> bytes:
    key = (str(index), index.stat().st_mtime, _version())  # (a changed file or version: read again)
    if _index_cache.get("key") != key:
        # The version it's from, so an open page can tell when the add-on's been updated
        _index_cache["body"] = index.read_bytes().replace(
            b'<meta name="addon-version" content="">',
            f'<meta name="addon-version" content="{_version()}">'.encode(), 1)
        _index_cache["key"] = key
    return _index_cache["body"]


async def handle_index(request: web.Request) -> web.Response:
    static_dir: Path = request.app["static_dir"]
    # Never cached by the browser: after an update it (or HA's ingress) must
    # not keep showing the old page
    body = _index_body(static_dir / "index.html")
    return web.Response(
        body=body,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _version() -> str:
    return os.environ.get("ADDON_VERSION") or "dev"


async def handle_version(request: web.Request) -> web.Response:
    """The add-on's version, for an open page to notice an update (it then offers a reload)."""
    return web.json_response({"version": _version()}, headers={"Cache-Control": "no-store"})


async def handle_get_state(request: web.Request) -> web.Response:
    state: SharedState = request.app["shared_state"]
    on_refresh = request.app["on_refresh"]
    if on_refresh is not None:
        try:
            on_refresh()  # live power, not just the last meter reading
        except Exception:
            logger.debug("Live power refresh failed", exc_info=True)
    return web.json_response(state.to_dict())


async def handle_plug(request: web.Request) -> web.Response:
    await request.app["on_plug"]()
    return web.json_response({"status": "ok"})


async def handle_unplug(request: web.Request) -> web.Response:
    await request.app["on_unplug"]()
    return web.json_response({"status": "ok"})


async def handle_charge_now(request: web.Request) -> web.Response:
    """{"hours": 0.5..12 in half hours, "confirm": bool}: plug in now and add
    the charge to the schedule as a one-off, as auto plug-in does (see
    src/automation.py). If it would go over Octopus's 6 hours a day, the
    answer is {"needs_confirm": true, "plan": ...} and nothing is done until
    it's sent again with "confirm": true."""
    on_charge_now = request.app["on_charge_now"]
    if on_charge_now is None:
        return _error("Not available", 503)
    try:
        body = await request.json()
        hours = float(body["hours"])
        if not 0.5 <= hours <= 12 or hours * 2 != int(hours * 2):
            raise ValueError
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return _error("hours must be 0.5 to 12, in half hours", 400)
    try:
        out = await on_charge_now(hours, bool(body.get("confirm")))
    except ValueError as err:
        return _error(str(err), 400)
    return web.json_response({"status": "ok", **out}, dumps=_dumps)


async def handle_cancel_charge(request: web.Request) -> web.Response:
    """Cancel a Charge now that's going or still to come: out of the schedule (what it
    changed put back), and unplugged if nothing else has the car plugged in now."""
    on_cancel = request.app["on_cancel_charge"]
    if on_cancel is None:
        return _error("Not available", 503)
    try:
        out = await on_cancel()
    except ValueError as err:
        return _error(str(err), 400)
    return web.json_response({"status": "ok", **out}, dumps=_dumps)


async def handle_migrate_export(request: web.Request) -> web.Response:
    """The settings to move to another install (src/migrate.py), as a download:
    ?history=0 leaves out the sessions and message log, ?password=0 the password."""
    mover = request.app["mover"]
    if mover is None:
        return _error("Not available", 503)
    try:
        out = mover.export(history=request.query.get("history") != "0", password=request.query.get("password") != "0")
    except ValueError as err:  # (a session in progress)
        return _error(str(err), 409)
    name = f"ocpp_charge_proxy_settings_{out['exported_at'][:16].replace(':', '-').replace('T', '_')}.json"
    return web.json_response(out, dumps=_dumps, headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def _migrate_body(request: web.Request):
    try:
        return await request.json()
    except (ValueError, json.JSONDecodeError):
        raise ValueError("That file isn't JSON: pick the file Export made") from None


async def handle_migrate_check(request: web.Request) -> web.Response:
    """What a settings file holds, before importing it (nothing changes)."""
    mover = request.app["mover"]
    if mover is None:
        return _error("Not available", 503)
    try:
        return web.json_response({"status": "ok", **await mover.check(await _migrate_body(request))}, dumps=_dumps)
    except ValueError as err:
        return _error(str(err), 409 if "in progress" in str(err) else 400)


async def handle_migrate_import(request: web.Request) -> web.Response:
    """Import a settings file: kept for the next start, the options set, and the add-on restarts."""
    mover = request.app["mover"]
    if mover is None:
        return _error("Not available", 503)
    try:
        return web.json_response({"status": "ok", **await mover.import_now(await _migrate_body(request))})
    except ValueError as err:
        return _error(str(err), 400)


async def handle_current(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        amps = int(body["amps"])
    except Exception:
        return _error("Invalid request. Send JSON: {\"amps\": N}", 400)
    if amps not in VALID_CURRENT_SETTINGS:
        return _error(f"Invalid current. Valid: {VALID_CURRENT_SETTINGS}", 400)
    await request.app["on_set_current"](amps)
    return web.json_response({"status": "ok"})


async def handle_power(request: web.Request) -> web.Response:
    on_set_power = request.app["on_set_power"]
    if on_set_power is None:
        return _error("Power override not supported", 501)
    try:
        body = await request.json()
        power_kw = body.get("power_kw")
        if power_kw is not None:
            power_kw = float(power_kw)
    except Exception:
        return _error("Invalid request. Send JSON: {\"power_kw\": N} or {\"power_kw\": null}", 400)
    await on_set_power(power_kw)
    return web.json_response({"status": "ok"})


async def handle_soc(request: web.Request) -> web.Response:
    """Car state of charge (%) set on the web page; null = unknown / not set."""
    on_set_soc = request.app["on_set_soc"]
    if on_set_soc is None:
        return _error("SoC not supported", 501)
    try:
        body = await request.json()
        soc = body.get("soc")
        if soc is not None:
            soc = float(soc)
            if not 0 <= soc <= 100:
                raise ValueError("soc must be 0-100")
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return _error("Invalid request. Send JSON: {\"soc\": 0-100} or {\"soc\": null}", 400)
    await on_set_soc(soc)
    return web.json_response({"status": "ok"})


async def handle_ramp(request: web.Request) -> web.Response:
    """Simulated car start-up: {"start_delay_s": 0-60, "ramp_up_s": 0-60}."""
    on_set_ramp = request.app["on_set_ramp"]
    if on_set_ramp is None:
        return _error("Not supported", 501)
    try:
        body = await request.json()
        on_set_ramp(float(body["start_delay_s"]), float(body["ramp_up_s"]))
    except (KeyError, TypeError, ValueError, AttributeError, json.JSONDecodeError) as err:
        message = str(err) if isinstance(err, ValueError) and "60" in str(err) else (
            "Send JSON: {\"start_delay_s\": 0-60, \"ramp_up_s\": 0-60}")
        return _error(message, 400)
    return web.json_response({"status": "ok"})


# --- Push updates (Server-Sent Events) -------------------------------------

# Fields that change on every live-power refresh: pushed at most every
# EVENTS_LIVE_INTERVAL seconds, so the page isn't flooded. Any other
# change (state, plug, connection, energy, commands...) is pushed at once.
_LIVE_FIELDS = {
    "power_kw", "voltage", "current_a", "frequency_hz", "power_offered_kw",
    "power_entity_value",
}
EVENTS_CHECK_INTERVAL = 1.0
EVENTS_LIVE_INTERVAL = 10.0


def _significant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in _LIVE_FIELDS}


def _state_snapshot(app: web.Application) -> dict:
    on_refresh = app["on_refresh"]
    if on_refresh is not None:
        try:
            on_refresh()
        except Exception:
            logger.debug("Live power refresh failed", exc_info=True)
    return app["shared_state"].to_dict()


async def handle_events(request: web.Request) -> web.StreamResponse:
    """Stream the state to the web page as Server-Sent Events.

    One event straight away, then on every significant change (within ~1s),
    and at least every EVENTS_LIVE_INTERVAL seconds with live power (which
    also keeps the connection alive).
    """
    app = request.app
    stop: asyncio.Event = app["events_stop"]
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)
    last_sig = None
    last_sent = 0.0
    app["event_clients"]["gui"] += 1
    try:
        while not stop.is_set():
            sig = _significant(app["shared_state"].to_dict())
            now = time.monotonic()
            if sig != last_sig or now - last_sent >= EVENTS_LIVE_INTERVAL:
                data = _state_snapshot(app)
                await resp.write(f"data: {json.dumps(data, default=str)}\n\n".encode())
                last_sig = _significant(data)
                last_sent = now
            try:
                await asyncio.wait_for(stop.wait(), EVENTS_CHECK_INTERVAL)
            except asyncio.TimeoutError:
                pass
    except (ConnectionResetError, ConnectionError):
        pass  # page closed
    finally:
        app["event_clients"]["gui"] -= 1
    return resp


async def _close_event_streams(app: web.Application) -> None:
    """End open event streams so add-on shutdown isn't held up by them."""
    app["events_stop"].set()


# --- Web GUI -----------------------------------------------------------------


def _gui_unavailable() -> web.Response:
    return _error("Not available", 501)


def _number_param(request: web.Request, name: str, default: float = 0) -> float:
    try:
        return float(request.query.get(name, default))
    except ValueError:
        return default


async def handle_messages(request: web.Request) -> web.Response:
    """OCPP frames after ?after=<seq> (all kept ones without it)."""
    gui = request.app["gui"]
    if gui is None:
        return _gui_unavailable()
    log = gui.message_log
    after = int(_number_param(request, "after", 0))
    if after > log.last_seq:
        after = 0  # the add-on restarted: send everything again
    return web.json_response(
        {"messages": log.since(after), "last_seq": log.last_seq}, dumps=_dumps,
    )


async def handle_sessions(request: web.Request) -> web.Response:
    gui = request.app["gui"]
    if gui is None:
        return _gui_unavailable()
    return web.json_response(gui.sessions(), dumps=_dumps)


async def handle_history(request: web.Request) -> web.Response:
    """Chart samples newer than ?since=<unix time>: 10-second ones for up to
    24 hours, or with ?resolution=long 5-minute / hourly ones for up to 14
    days (read from Home Assistant's history, see src/ha_history.py)."""
    gui = request.app["gui"]
    if gui is None:
        return _gui_unavailable()
    since = _number_param(request, "since", 0)
    if request.query.get("resolution") == "long":
        samples = await gui.history.long_samples(since)
    else:
        samples = await gui.history.samples(since)
    return web.json_response(
        {"samples": samples, "now": time.time(), "error": getattr(gui.history, "error", None)}, dumps=_dumps)


async def handle_energy_daily(request: web.Request) -> web.Response:
    """kWh per day for the last ?days=N days (default 14, max 62)."""
    gui = request.app["gui"]
    if gui is None:
        return _gui_unavailable()
    days = int(min(62, max(1, _number_param(request, "days", 14))))
    try:
        sessions = gui.sessions()
        kept = list(sessions.get("history") or []) + ([sessions["current"]] if sessions.get("current") else [])
        since = sessions.get("cleared_at")
    except Exception:
        kept, since = [], None
    return web.json_response({"days": await gui.history.daily(days, kept, since)}, dumps=_dumps)


async def handle_provider(request: web.Request) -> web.Response:
    gui = request.app["gui"]
    if gui is None:
        return _gui_unavailable()
    return web.json_response(gui.provider(), dumps=_dumps)


async def handle_news_seen(request: web.Request) -> web.Response:
    """Diagnostics › Health › Supplier: mark everything new as seen ({"clear": true}: forget it all)."""
    news = request.app["news"]
    if news is None:
        return _gui_unavailable()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict) and body.get("clear"):
        news.clear()
    else:
        news.mark_seen()
    return web.json_response({"items": news.snapshot()}, dumps=_dumps)


async def handle_health(request: web.Request) -> web.Response:
    gui = request.app["gui"]
    if gui is None:
        return _gui_unavailable()
    info = dict(gui.health())
    info["event_streams"] = dict(request.app["event_clients"])
    return web.json_response(info, dumps=_dumps)


def _dumps(data) -> str:
    return json.dumps(data, default=str, separators=(",", ":"))


# --- Schedule and auto re-plug ----------------------------------------------


async def handle_automation(request: web.Request) -> web.Response:
    automation = request.app["automation"]
    if automation is None:
        return _gui_unavailable()
    return web.json_response(automation.snapshot(), dumps=_dumps)


async def _automation_change(request: web.Request, apply) -> web.Response:
    automation = request.app["automation"]
    if automation is None:
        return _gui_unavailable()
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object")
        apply(automation, body)
    except (ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError) as err:
        return _error(str(err) or "Bad request", 400)
    automation.publish(request.app["shared_state"])
    return web.json_response({"status": "ok", **automation.snapshot()}, dumps=_dumps)


def _smart(request: web.Request) -> dict:
    """Your supplier's smart charging info (its daily limit, ready times...), or {} if it can't be read."""
    link = request.app["ha_link"]
    if link is None:
        return {}
    try:
        return link.smart_charging() or {}
    except Exception:
        logger.debug("Couldn't read the supplier's smart charging info", exc_info=True)
        return {}


def _bool(b: dict, key: str, default: bool = False) -> bool:
    """A true/false field of a request: only real booleans (not "false")."""
    value = b.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


async def handle_schedule(request: web.Request) -> web.Response:
    """{"enabled": bool}, {"ready_time": bool}, {"unplug_wait_s": N}, {"start_delay": {"enabled", "max_min"}} and/or
    {"entries": [{time, days, action, enabled}, ...]}."""
    sc = _smart(request)
    if request.app["automation"] is not None:
        request.app["automation"].limit_reset = sc.get("limit_reset")  # e.g. Octopus: 12:00
    return await _automation_change(
        request, lambda a, b: a.set_schedule(
            enabled=b.get("enabled"), entries=b.get("entries"), ready_time=b.get("ready_time"),
            ready_times=sc.get("ready_times"), provider=sc.get("provider") or "your supplier",
            # e.g. Octopus schedules at most 6 hours of smart charging a day
            daily_cap_min=sc.get("limit_min"),
            unplug_wait_s=b.get("unplug_wait_s"),
            override_guards=b.get("override_guards"),
            blocks=b.get("blocks"),
            allow_over=_bool(b, "allow_over"),  # save it anyway, over the daily limit
            start_delay=b.get("start_delay"),  # {"enabled", "max_min"}: a random wait before plug-ins
            add_skips=b.get("skips"),  # times to skip with it, e.g. where a new slot overlaps another
        ),
    )


async def handle_skip(request: web.Request) -> web.Response:
    """{"times": [{"entry_id", "date": "YYYY-MM-DD"}, ...], "skip": bool}: skip
    upcoming times once (a slot: its plug-in and unplug), or undo. A single
    {"entry_id", "date"} works too."""
    sc = _smart(request)
    if request.app["automation"] is not None:
        request.app["automation"].limit_reset = sc.get("limit_reset")  # e.g. Octopus: 12:00
    return await _automation_change(
        request, lambda a, b: a.set_skips(
            b.get("times") if "times" in b else [{"entry_id": b.get("entry_id"), "date": b.get("date")}],
            _bool(b, "skip", True),
            # e.g. Octopus schedules at most 6 hours of smart charging a day
            daily_cap_min=sc.get("limit_min"),
            provider=sc.get("provider") or "your supplier",
            allow_over=_bool(b, "allow_over"),
        ),
    )


async def handle_replug(request: web.Request) -> web.Response:
    """Any of {"enabled": bool, "after_min": N, "attempts": N, "off_s": N}."""
    return await _automation_change(
        request, lambda a, b: a.set_replug(
            enabled=b.get("enabled"), after_min=b.get("after_min"), attempts=b.get("attempts"),
            off_s=b.get("off_s"),
        ),
    )


async def handle_populate_plan(request: web.Request) -> web.Response:
    """Best charging times per weekday from the selected power sensor's history."""
    link = request.app["ha_link"]
    if link is None or not getattr(link, "available", False):
        return web.json_response({"ok": False, "message": "Not connected to Home Assistant"})
    from src.populate_schedule import calculate
    s = getattr(link, "settings", {}) or {}
    try:
        plan = await calculate(link, s.get("populate_power_entity") or "",
                               s.get("populate_weeks", 4), s.get("populate_slots", 4))
    except Exception as err:
        logger.debug("Populate schedule plan failed", exc_info=True)
        return web.json_response({"ok": False, "message": str(err) or "Couldn't calculate the plan"})
    return web.json_response(plan, dumps=_dumps)


# --- Your HA sensors ----------------------------------------------------------


async def handle_get_sensors(request: web.Request) -> web.Response:
    """Settings, live values and the add-on's own HA entities.
    "configured": false until saved once."""
    link = request.app["ha_link"]
    if link is None:
        return _gui_unavailable()
    return web.json_response(link.snapshot(), dumps=_dumps)


async def handle_set_sensors(request: web.Request) -> web.Response:
    """Any of power_entity, soc_entity, plug_entity, plug_auto, auto_plug, auto_plug_entity,
    auto_plug_soc, auto_plug_ready, auto_plug_hours (only those sent change)."""
    link = request.app["ha_link"]
    if link is None:
        return _gui_unavailable()
    try:
        body = await request.json()
        await link.update_settings(body)
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _error(str(err), 400)
    return web.json_response({"status": "ok", **link.snapshot()}, dumps=_dumps)


async def handle_smart_charging(request: web.Request) -> web.Response:
    """Your supplier's smart charging plan (src/smart_charging.py)."""
    link = request.app["ha_link"]
    if link is None or not link.available:
        return web.json_response({"found": False, "error": "Not connected to Home Assistant"})
    try:
        return web.json_response(link.smart_charging(), dumps=_dumps)
    except Exception as err:
        return web.json_response({"found": False, "error": f"Couldn't read Home Assistant's states: {err}"})


async def handle_sensor_entities(request: web.Request) -> web.Response:
    link = request.app["ha_link"]
    if link is None:
        return _gui_unavailable()
    try:
        entities = await link.list_entities()
    except Exception as err:
        return _error(f"Couldn't list entities: {err}", 502)
    return web.json_response({"entities": entities, "available": link.available}, dumps=_dumps)


async def handle_notify_services(request: web.Request) -> web.Response:
    """Home Assistant's notify services, for Settings › Notifications."""
    link = request.app["ha_link"]
    if link is None:
        return _gui_unavailable()
    try:
        services = await link.notify_services()
    except Exception as err:
        return _error(f"Couldn't list Home Assistant's notify services: {err}", 502)
    return web.json_response({"services": services})


async def handle_notify_test(request: web.Request) -> web.Response:
    """Send a test notification through the service picked."""
    link = request.app["ha_link"]
    if link is None:
        return _gui_unavailable()
    error = await link.notify("OCPP Charge Proxy", "Test: notifications about your charging schedule come here.",
                              force=True)
    return _error(error, 502) if error else web.json_response({"status": "ok"})


# --- Settings made on the web page (src/app_settings.py) ----------------------


async def handle_get_settings(request: web.Request) -> web.Response:
    settings = request.app["app_settings"]
    if settings is None:
        return _gui_unavailable()
    return web.json_response(dict(settings.data))


async def handle_set_settings(request: web.Request) -> web.Response:
    """Any of {"log_level": "debug"|"info"|"warning"|"error", "continue_session": bool}."""
    settings = request.app["app_settings"]
    if settings is None:
        return _gui_unavailable()
    try:
        out = settings.update(await request.json())
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _error(str(err), 400)
    return web.json_response({"status": "ok", **out})


# --- Diagnostics › Debug (src/debug_tools.py) ---------------------------------


async def handle_debug_info(request: web.Request) -> web.Response:
    """The log level, the add-on's settings (redacted) and the last ?lines= log lines."""
    debug = request.app["debug"]
    if debug is None:
        return _gui_unavailable()
    return web.json_response(debug.info(int(_number_param(request, "lines", 200))), dumps=_dumps)


async def _debug_call(request: web.Request, apply) -> web.Response:
    debug = request.app["debug"]
    if debug is None:
        return _gui_unavailable()
    try:
        body = await request.json() if request.can_read_body else {}
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object")
        out = apply(debug, body)
        if asyncio.iscoroutine(out):
            out = await out
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _error(str(err), 400)
    return web.json_response({"status": "ok", **(out or {})}, dumps=_dumps)


async def handle_debug_send(request: web.Request) -> web.Response:
    """{"message": "StatusNotification"|"MeterValues"|"Heartbeat"|"BootNotification", "status": ...}"""
    return await _debug_call(request, lambda d, b: d.send(b.get("message"), b.get("status")))


async def handle_debug_drop(request: web.Request) -> web.Response:
    """{"seconds": 0..600}: close the connection, reconnect after that long (at least 5 s)."""
    return await _debug_call(request, lambda d, b: d.drop(b.get("seconds", 0)))


async def handle_debug_restart(request: web.Request) -> web.Response:
    return await _debug_call(request, lambda d, b: d.restart())


async def handle_debug_log_level(request: web.Request) -> web.Response:
    """{"level": "debug"|"info"|"warning"|"error"}, saved (src/app_settings.py)."""
    return await _debug_call(request, lambda d, b: d.set_log_level(b.get("level")))


async def handle_debug_clear(request: web.Request) -> web.Response:
    """{"what": "sessions"|"messages"}"""
    return await _debug_call(request, lambda d, b: d.clear(b.get("what")))
