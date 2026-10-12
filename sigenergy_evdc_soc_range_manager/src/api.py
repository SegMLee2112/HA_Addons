"""The web page (Home Assistant ingress) and its API.

The page (static/index.html) calls these with relative URLs ("./api/..."),
so it works behind ingress and on the add-on's own port (8099). Add an
endpoint: a handler below and a line in create_api_app().
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from src.controller import check_limit

logger = logging.getLogger(__name__)

EVENTS_CHECK_INTERVAL = 1.0  # how often the event stream looks for changes
EVENTS_KEEPALIVE = 10.0  # ...and sends the state at least this often anyway
# Fields that change all the time: a change in only these isn't pushed at once
LIVE_FIELDS = {"timers", "updated"}


def create_api_app(shared_state, *, ha_link=None, health=None, debug=None, app_settings=None,
                   runner=None, schedule=None) -> web.Application:
    app = web.Application()
    app["shared_state"] = shared_state  # src.shared_state.SharedState
    app["ha_link"] = ha_link  # src.ha_link.HaLink, or None
    app["health"] = health  # src.health.Health, or None
    app["debug"] = debug  # src.debug_tools.DebugTools, or None
    app["app_settings"] = app_settings  # src.app_settings.AppSettings, or None
    app["runner"] = runner  # src.__main__.Runner, or None
    app["schedule"] = schedule  # src.schedule.Schedule, or None
    app["events_stop"] = asyncio.Event()
    app["event_clients"] = {"gui": 0}
    app["static_dir"] = Path(__file__).parent / "static"
    app.on_shutdown.append(_close_event_streams)

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/state", handle_get_state)
    app.router.add_get("/api/events", handle_events)
    app.router.add_get("/api/health", handle_health)
    # Your Home Assistant entities (Settings tab)
    app.router.add_get("/api/sensors", handle_get_sensors)
    app.router.add_post("/api/sensors", handle_set_sensors)
    app.router.add_get("/api/sensors/entities", handle_sensor_entities)
    # The charger and its limits (Overview)
    app.router.add_post("/api/charger", handle_charger)
    app.router.add_post("/api/limits", handle_limits)
    # Scheduled limits (src/schedule.py)
    app.router.add_get("/api/schedule", handle_get_schedule)
    app.router.add_post("/api/schedule", handle_add_schedule)
    app.router.add_post("/api/schedule/{id}", handle_update_schedule)
    app.router.add_delete("/api/schedule/{id}", handle_delete_schedule)
    app.router.add_post("/api/schedule/{id}/skip", handle_skip_schedule)
    app.router.add_get("/api/history", handle_history)
    app.router.add_post("/api/hold", handle_hold)
    app.router.add_get("/api/soc_history", handle_soc_history)
    # Statistics tab (src/stats.py)
    app.router.add_get("/api/stats", handle_stats)
    # Settings made on the web page (src/app_settings.py)
    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_post("/api/settings", handle_set_settings)
    # Diagnostics › Debug
    app.router.add_get("/api/debug/info", handle_debug_info)
    app.router.add_post("/api/debug/log_level", handle_debug_log_level)
    app.router.add_post("/api/debug/restart", handle_debug_restart)
    return app


def _dumps(data) -> str:
    return json.dumps(data, default=str)


def _unavailable() -> web.Response:
    return web.json_response({"status": "error", "message": "Not available"}, status=501)


def _bad_request(err) -> web.Response:
    return web.json_response({"status": "error", "message": str(err)}, status=400)


async def handle_index(request: web.Request) -> web.Response:
    index = request.app["static_dir"] / "index.html"
    # Never cached: after an update the browser (or HA's ingress) must not
    # keep showing the old page
    return web.Response(body=index.read_bytes(), content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store, max-age=0"})


async def handle_get_state(request: web.Request) -> web.Response:
    return web.json_response(request.app["shared_state"].to_dict(), dumps=_dumps)


async def handle_events(request: web.Request) -> web.StreamResponse:
    """The state as Server-Sent Events: once straight away, then on every
    change (within ~1 s), and at least every EVENTS_KEEPALIVE seconds."""
    app = request.app
    stop: asyncio.Event = app["events_stop"]
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)
    last_sig, last_sent = None, 0.0
    app["event_clients"]["gui"] += 1
    try:
        while not stop.is_set():
            data = app["shared_state"].to_dict()
            sig = {k: v for k, v in data.items() if k not in LIVE_FIELDS}
            now = time.monotonic()
            if sig != last_sig or now - last_sent >= EVENTS_KEEPALIVE:
                await resp.write(f"data: {_dumps(data)}\n\n".encode())
                last_sig, last_sent = sig, now
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
    """End open event streams so shutting down isn't held up by them."""
    app["events_stop"].set()


async def handle_health(request: web.Request) -> web.Response:
    app = request.app
    out = app["health"].snapshot() if app["health"] is not None else {}
    link = app["ha_link"]
    out["home_assistant"] = None if link is None else {
        "available": link.available, "connected": link.connected, "error": link.error,
    }
    out["event_streams"] = dict(app["event_clients"])
    return web.json_response(out, dumps=_dumps)


# --- Your Home Assistant entities (src/ha_link.py) -------------------------------


async def handle_get_sensors(request: web.Request) -> web.Response:
    link = request.app["ha_link"]
    if link is None:
        return _unavailable()
    return web.json_response(link.snapshot(), dumps=_dumps)


async def handle_set_sensors(request: web.Request) -> web.Response:
    link = request.app["ha_link"]
    if link is None:
        return _unavailable()
    try:
        await link.update_settings(await request.json())
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    return web.json_response({"status": "ok", **link.snapshot()}, dumps=_dumps)


async def handle_sensor_entities(request: web.Request) -> web.Response:
    link = request.app["ha_link"]
    if link is None:
        return _unavailable()
    try:
        entities = await link.list_entities()
    except Exception as err:
        return web.json_response({"status": "error", "message": f"Couldn't list entities: {err}"}, status=502)
    return web.json_response({"entities": entities, "available": link.available}, dumps=_dumps)


# --- The charger and its limits -----------------------------------------------------


async def handle_charger(request: web.Request) -> web.Response:
    """{"action": "start"|"stop"}: press the charger's button now."""
    runner = request.app["runner"]
    if runner is None:
        return _unavailable()
    try:
        body = await request.json()
        action = (body or {}).get("action") if isinstance(body, dict) else None
        if action not in ("start", "stop"):
            raise ValueError("action must be start or stop")
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    entry = await runner.manual(action)
    if entry.get("error"):
        return web.json_response({"status": "error", "message": entry["error"]}, status=502)
    return web.json_response({"status": "ok", "entry": entry})


async def handle_limits(request: web.Request) -> web.Response:
    """{"high": 80, "low": 40} (either or both): set the limit helpers in Home Assistant."""
    link = request.app["ha_link"]
    if link is None:
        return _unavailable()
    try:
        body = await request.json()
        if not isinstance(body, dict) or not ({"high", "low"} & set(body)):
            raise ValueError("Send high and/or low")
        wanted = {}
        for key in ("high", "low"):
            if key in body:
                value = float(body[key])
                check_limit(key, value)
                wanted[key] = value
        st = request.app["shared_state"]
        current, base = st.inputs or {}, st.readings or {}  # the default limits, not scheduled ones
        high = wanted.get("high", base.get("base_high", current.get("high")))
        low = wanted.get("low", base.get("base_low", current.get("low")))
        if high is not None and low is not None and low >= high:
            raise ValueError("The low limit must be below the high limit")
        for key, value in wanted.items():
            entity_id = link.settings.get("soc_" + key)
            if not entity_id:
                raise ValueError(f"No {key} limit helper set on the Settings tab")
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    try:
        for key, value in wanted.items():
            entity_id = link.settings["soc_" + key]
            await link.call_service(entity_id.split(".", 1)[0], "set_value", data={"value": value},
                                    target={"entity_id": entity_id})
    except Exception as err:
        return web.json_response({"status": "error", "message": f"Home Assistant refused it: {err}"}, status=502)
    return web.json_response({"status": "ok", **wanted})


def _schedule_view(schedule) -> dict:
    import datetime

    now = datetime.datetime.now()
    return {"entries": schedule.entries, "active": schedule.active(now), "upcoming": schedule.upcoming(now)}


async def _schedule_call(request: web.Request, apply) -> web.Response:
    schedule = request.app["schedule"]
    if schedule is None:
        return _unavailable()
    try:
        body = await request.json() if request.can_read_body else {}
        apply(schedule, body)
    except KeyError:
        return web.json_response({"status": "error", "message": "No such entry"}, status=404)
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    runner = request.app["runner"]
    if runner is not None:
        runner.wake.set()  # use the new limits straight away
    return web.json_response({"status": "ok", **_schedule_view(schedule)})


async def handle_get_schedule(request: web.Request) -> web.Response:
    if request.app["schedule"] is None:
        return _unavailable()
    return web.json_response(_schedule_view(request.app["schedule"]))


async def handle_add_schedule(request: web.Request) -> web.Response:
    return await _schedule_call(request, lambda s, b: s.add(b))


async def handle_update_schedule(request: web.Request) -> web.Response:
    return await _schedule_call(request, lambda s, b: s.update(request.match_info["id"], b))


async def handle_delete_schedule(request: web.Request) -> web.Response:
    return await _schedule_call(request, lambda s, b: s.remove(request.match_info["id"]))


async def handle_skip_schedule(request: web.Request) -> web.Response:
    """{"date": "2026-10-15", "skip": true}: skip a weekly entry's on-period starting that day (or not)."""
    return await _schedule_call(request, lambda s, b: s.skip(request.match_info["id"], b.get("date"),
                                                             b.get("skip", True) is not False))


async def handle_hold(request: web.Request) -> web.Response:
    """A one-tap hold from the Overview: {"kind", "until": "HH:MM" | "hours": n, "low": n} (src/holds.py)."""
    import datetime

    from src.holds import make_hold

    runner, schedule = request.app["runner"], request.app["schedule"]
    if runner is None or schedule is None:
        return _unavailable()
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object")
        entry = make_hold(body.get("kind"), runner.manager.soc, datetime.datetime.now(), until=body.get("until"),
                          hours=body.get("hours"), low=body.get("low"))
        schedule.add(entry)
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    runner.wake.set()
    return web.json_response({"status": "ok", **_schedule_view(schedule)})


async def handle_soc_history(request: web.Request) -> web.Response:
    """The Overview's SoC chart: ?hours=24 or 48."""
    runner = request.app["runner"]
    if runner is None:
        return _unavailable()
    try:
        hours = min(48.0, max(1.0, float(request.query.get("hours", 24))))
    except ValueError:
        hours = 24.0
    return web.json_response(runner.soc_chart(hours), dumps=_dumps)


async def handle_history(request: web.Request) -> web.Response:
    """Every start, stop (or would-be one, watching only), dropout and change of limits, newest first."""
    runner = request.app["runner"]
    if runner is None:
        return _unavailable()
    return web.json_response({"entries": list(runner.manager.log)[::-1]}, dumps=_dumps)


async def handle_stats(request: web.Request) -> web.Response:
    runner = request.app["runner"]
    if runner is None:
        return _unavailable()
    return web.json_response(runner.stats_summary(), dumps=_dumps)


# --- Settings made on the web page (src/app_settings.py) --------------------------


async def handle_get_settings(request: web.Request) -> web.Response:
    settings = request.app["app_settings"]
    if settings is None:
        return _unavailable()
    return web.json_response(dict(settings.data))


async def handle_set_settings(request: web.Request) -> web.Response:
    settings = request.app["app_settings"]
    if settings is None:
        return _unavailable()
    try:
        out = settings.update(await request.json())
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    return web.json_response({"status": "ok", **out})


# --- Diagnostics › Debug (src/debug_tools.py) ---------------------------------------


async def handle_debug_info(request: web.Request) -> web.Response:
    """The log level, the add-on's options (secrets hidden) and the last ?lines= log lines."""
    debug = request.app["debug"]
    if debug is None:
        return _unavailable()
    try:
        lines = int(request.query.get("lines", 200))
    except ValueError:
        lines = 200
    return web.json_response(debug.info(lines), dumps=_dumps)


async def _debug_call(request: web.Request, apply) -> web.Response:
    debug = request.app["debug"]
    if debug is None:
        return _unavailable()
    try:
        body = await request.json() if request.can_read_body else {}
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object")
        out = apply(debug, body)
        if asyncio.iscoroutine(out):
            out = await out
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    return web.json_response({"status": "ok", **(out or {})}, dumps=_dumps)


async def handle_debug_log_level(request: web.Request) -> web.Response:
    """{"level": "debug"|"info"|"warning"|"error"}, saved (src/app_settings.py)."""
    return await _debug_call(request, lambda d, b: d.set_log_level(b.get("level")))


async def handle_debug_restart(request: web.Request) -> web.Response:
    return await _debug_call(request, lambda d, b: d.restart())
