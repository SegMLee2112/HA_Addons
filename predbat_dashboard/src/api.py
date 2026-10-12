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

logger = logging.getLogger(__name__)

EVENTS_CHECK_INTERVAL = 1.0  # how often the event stream looks for changes
EVENTS_KEEPALIVE = 10.0  # ...and sends the state at least this often anyway
# Fields that change all the time: a change in only these isn't pushed at once
LIVE_FIELDS: set = set()


def create_api_app(shared_state, *, ha_link=None, health=None, debug=None, app_settings=None,
                   runner=None, layout=None) -> web.Application:
    app = web.Application()
    app["shared_state"] = shared_state  # src.shared_state.SharedState
    app["ha_link"] = ha_link  # src.ha_link.HaLink, or None
    app["health"] = health  # src.health.Health, or None
    app["debug"] = debug  # src.debug_tools.DebugTools, or None
    app["app_settings"] = app_settings  # src.app_settings.AppSettings, or None
    app["runner"] = runner  # src.runner.Runner, or None
    app["layout"] = layout  # src.layout.Layout, or None
    app["events_stop"] = asyncio.Event()
    app["event_clients"] = {"gui": 0}
    app["static_dir"] = Path(__file__).parent / "static"
    app.on_shutdown.append(_close_event_streams)

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/state", handle_get_state)
    app.router.add_get("/api/events", handle_events)
    app.router.add_get("/api/health", handle_health)
    # The dashboard's cards (src/layout.py) and controls (src/runner.py)
    app.router.add_get("/api/layout", handle_get_layout)
    app.router.add_post("/api/layout", handle_set_layout)
    app.router.add_post("/api/layout/reset", handle_reset_layout)
    app.router.add_post("/api/control", handle_control)
    # Home Assistant: the link, and its entities (for the entity card's picker)
    app.router.add_get("/api/sensors", handle_get_sensors)
    app.router.add_get("/api/sensors/entities", handle_sensor_entities)
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


# --- The dashboard (src/layout.py, src/runner.py) --------------------------------


def _layout_reply(app) -> web.Response:
    from src.layout import catalog

    return web.json_response({"cards": app["layout"].cards, "catalog": catalog()}, dumps=_dumps)


def _layout_changed(app) -> None:
    runner = app["runner"]
    if runner is not None:
        runner.follow_cards()
    else:
        app["shared_state"].cards = list(app["layout"].cards)


async def handle_get_layout(request: web.Request) -> web.Response:
    """The cards on the dashboard, in order, and every kind of card there is."""
    if request.app["layout"] is None:
        return _unavailable()
    return _layout_reply(request.app)


async def handle_set_layout(request: web.Request) -> web.Response:
    """{"cards": [{"type", "id"?, "title"?, "entity"?}, ...]}: the whole dashboard, in order."""
    layout = request.app["layout"]
    if layout is None:
        return _unavailable()
    try:
        body = await request.json()
        layout.save(body.get("cards") if isinstance(body, dict) else body)
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    _layout_changed(request.app)
    return _layout_reply(request.app)


async def handle_reset_layout(request: web.Request) -> web.Response:
    layout = request.app["layout"]
    if layout is None:
        return _unavailable()
    layout.reset()
    _layout_changed(request.app)
    return _layout_reply(request.app)


async def handle_control(request: web.Request) -> web.Response:
    """Change Predbat's mode, or force charge / export / freeze (src/runner.py control())."""
    runner = request.app["runner"]
    if runner is None:
        return _unavailable()
    try:
        out = await runner.control(await request.json())
    except (ValueError, TypeError, json.JSONDecodeError) as err:
        return _bad_request(err)
    except Exception as err:
        return web.json_response({"status": "error", "message": f"Home Assistant said no: {err}"}, status=502)
    return web.json_response(out, dumps=_dumps)


# --- Your Home Assistant entities (src/ha_link.py) -------------------------------


async def handle_get_sensors(request: web.Request) -> web.Response:
    link = request.app["ha_link"]
    if link is None:
        return _unavailable()
    return web.json_response(link.snapshot(), dumps=_dumps)


async def handle_sensor_entities(request: web.Request) -> web.Response:
    link = request.app["ha_link"]
    if link is None:
        return _unavailable()
    try:
        entities = await link.list_entities()
    except Exception as err:
        return web.json_response({"status": "error", "message": f"Couldn't list entities: {err}"}, status=502)
    return web.json_response({"entities": entities, "available": link.available}, dumps=_dumps)


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
