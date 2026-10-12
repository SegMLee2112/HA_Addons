"""The add-on's link to Home Assistant, through the Supervisor.

- Reads Predbat's entities (and any the dashboard's cards show) live (subscribe_entities,
  so changes arrive at once) and calls on_entity_changed for each change.
- Lists HA's entities for the pickers on the web page.
- Posts the add-on's own sensors (src/ha_entities.py) and marks them
  unavailable when the add-on stops.
- call_service() and query() for anything else (turn a light on, read
  history...).

homeassistant_api: true in config.yaml gives the add-on SUPERVISOR_TOKEN.
Outside Home Assistant (development, tests) there's no token: the link
stays off and the rest of the add-on still runs.

Which entities to follow is set by set_extra() (src/runner.py): Predbat's,
found by name, and the entity cards'.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable, Optional

from src.ha_entities import ALL_SENSORS, SensorPublisher, entity_info

logger = logging.getLogger(__name__)

SUPERVISOR_WS = "ws://supervisor/core/websocket"
SUPERVISOR_API = "http://supervisor/core/api"
RECONNECT_DELAYS = (2, 5, 10, 20, 30, 60)
ENTITY_LIST_TTL_S = 30
PICKER_DOMAINS = ("sensor", "binary_sensor", "switch", "input_boolean", "input_number", "input_select", "select",
                  "number", "light", "climate", "weather", "predbat")

# The entity pickers on the Settings tab: key -> domain(s) allowed. None: the
# add-on follows Predbat's own entities (found by name, see src/runner.py) and
# the entities the dashboard's entity cards show.
ENTITY_KEYS: dict = {}


def default_settings() -> dict:
    return {key: "" for key in ENTITY_KEYS}


def validate_settings(raw: dict, current: Optional[dict] = None) -> dict:
    """Merge `raw` into `current` and check it (raises ValueError). Empty entity = not used."""
    if not isinstance(raw, dict):
        raise ValueError("Send a JSON object")
    out = dict(current or default_settings())
    for key, domains in ENTITY_KEYS.items():
        if key in raw:
            value = raw[key]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key} must be an entity ID or empty")
            value = (value or "").strip()
            if value and value.split(".", 1)[0] not in domains:
                raise ValueError(f"{key} must be a {' or '.join(domains)} entity")
            out[key] = value
    unknown = [k for k in raw if k not in out]
    if unknown:
        raise ValueError("Unknown setting: " + ", ".join(unknown))
    return out


class HaLink:
    def __init__(self, data_dir: Optional[str], shared_state, token: Optional[str] = None,
                 on_entity_changed: Optional[Callable[[str, Optional[dict]], Awaitable[None]]] = None) -> None:
        self._path = os.path.join(data_dir, "sensors.json") if data_dir else None
        self._shared = shared_state
        self.on_entity_changed = on_entity_changed  # (entity_id, state or None): set by __main__
        self._token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.settings = default_settings()
        self.configured = False  # settings saved at least once
        self.states: dict[str, dict] = {}  # entity_id -> {"state", "attributes"}, for the watched ones
        self.extra: set[str] = set()  # more entities to follow (set_extra)
        self.connected = False
        self.error: Optional[str] = None
        self.publisher = SensorPublisher()
        self._session = None
        self._call = None  # websocket request function while connected
        self._changed = asyncio.Event()  # the watched entities changed: subscribe again
        self._states_cache: tuple[float, list] = (0.0, [])
        self._load()

    # --- settings ------------------------------------------------------------

    def _load(self) -> None:
        if not self._path:
            return
        for path in (self._path, self._path + ".bak"):
            try:
                with open(path, encoding="utf-8") as f:
                    self.settings = validate_settings(
                        {k: v for k, v in json.load(f).items() if k in ENTITY_KEYS}, default_settings())
                self.configured = True
                return
            except FileNotFoundError:
                continue
            except Exception:
                logger.warning("Sensor settings %s are unreadable", path)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(self._path):
                os.replace(self._path, self._path + ".bak")
            os.replace(tmp, self._path)
        except Exception:
            logger.warning("Could not save sensor settings", exc_info=True)

    async def update_settings(self, raw: dict) -> dict:
        new = validate_settings(raw, self.settings)
        changed = new != self.settings
        self.settings = new
        self.configured = True
        self._save()
        if changed:
            logger.info("Sensors: %s", ", ".join(f"{k} {v or '-'}" for k, v in new.items()))
            self._changed.set()  # follow the new ones
            for entity_id in self.watched:
                await self._notify(entity_id)
        return new

    @property
    def watched(self) -> list[str]:
        return sorted({v for k, v in self.settings.items() if k in ENTITY_KEYS and v} | self.extra)

    def set_extra(self, ids) -> bool:
        """Follow these entities as well (subscribing again if they changed). True if they did."""
        ids = {str(i) for i in ids if i}
        if ids == self.extra:
            return False
        self.extra = ids
        self._changed.set()
        return True

    # --- reacting to states ----------------------------------------------------

    async def _notify(self, entity_id: str) -> None:
        if self.on_entity_changed is not None:
            try:
                await self.on_entity_changed(entity_id, self.states.get(entity_id))
            except Exception:
                logger.warning("Handling a change of %s failed", entity_id, exc_info=True)

    async def handle_entities_event(self, event: dict) -> None:
        """A subscribe_entities event: a = added (full), c = changed (diff), r = removed."""
        changed: list[str] = []
        for entity_id, full in (event.get("a") or {}).items():
            self.states[entity_id] = {"state": full.get("s"), "attributes": full.get("a") or {},
                                      "ts": full.get("lu") or full.get("lc") or time.time()}
            changed.append(entity_id)
        for entity_id, diff in (event.get("c") or {}).items():
            current = self.states.setdefault(entity_id, {"state": None, "attributes": {}})
            plus = diff.get("+") or {}
            if "s" in plus:
                current["state"] = plus["s"]
            if "a" in plus:
                current["attributes"] = {**current["attributes"], **plus["a"]}
            current["ts"] = plus.get("lu") or plus.get("lc") or time.time()
            for key in (diff.get("-") or {}).get("a") or []:
                current["attributes"].pop(key, None)
            changed.append(entity_id)
        for entity_id in event.get("r") or []:
            self.states.pop(entity_id, None)
            changed.append(entity_id)
        for entity_id in changed:
            await self._notify(entity_id)

    # --- connection --------------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self._token)

    async def run(self) -> None:
        """Stay connected to HA for the life of the add-on."""
        if not self.available:
            self.error = "Not running under the Home Assistant Supervisor"
            logger.info("Home Assistant link off: %s", self.error)
            return
        import aiohttp

        attempt = 0
        async with aiohttp.ClientSession() as session:
            self._session = session
            try:
                while True:
                    try:
                        await self._connection(session)
                        attempt = 0
                    except asyncio.CancelledError:
                        raise
                    except Exception as err:
                        self.error = str(err) or type(err).__name__
                        logger.warning("Home Assistant link lost (%s); reconnecting", self.error)
                    self.connected = False
                    await asyncio.sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
                    attempt += 1
            finally:
                self._session = None

    async def _connection(self, session) -> None:
        import aiohttp

        async with session.ws_connect(SUPERVISOR_WS, heartbeat=30, max_msg_size=64 * 1024 * 1024) as ws:
            msg = await ws.receive_json(timeout=10)
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"Unexpected greeting {msg.get('type')}")
            await ws.send_json({"type": "auth", "access_token": self._token})
            msg = await ws.receive_json(timeout=10)
            if msg.get("type") != "auth_ok":
                raise RuntimeError("Home Assistant refused the add-on's token")
            self.connected, self.error = True, None
            logger.info("Connected to Home Assistant")

            pending: dict[int, asyncio.Future] = {}
            events: asyncio.Queue = asyncio.Queue()
            counter = {"id": 0, "sub": None}

            async def call(payload: dict, timeout: float = 15):
                counter["id"] += 1
                msg_id = counter["id"]
                fut = asyncio.get_running_loop().create_future()
                pending[msg_id] = fut
                await ws.send_json({**payload, "id": msg_id})
                try:
                    reply = await asyncio.wait_for(fut, timeout)
                finally:
                    pending.pop(msg_id, None)
                if not reply.get("success", True):
                    raise RuntimeError((reply.get("error") or {}).get("message") or "request failed")
                return reply.get("result")

            async def reader() -> None:
                async for raw in ws:
                    if raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    if raw.type != aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(raw.data)
                    for message in data if isinstance(data, list) else [data]:
                        if message.get("type") == "result" and message.get("id") in pending:
                            pending[message["id"]].set_result(message)
                        elif message.get("type") == "event" and message.get("id") == counter["sub"]:
                            events.put_nowait(message.get("event") or {})
                raise ConnectionError("Home Assistant closed the connection")

            async def subscribe() -> None:
                if counter["sub"] is not None:
                    try:
                        await call({"type": "unsubscribe_events", "subscription": counter["sub"]})
                    except Exception:
                        pass
                    counter["sub"] = None
                self.states = {}  # the subscription sends the current states first
                ids = self.watched
                if ids:
                    counter["id"] += 1
                    counter["sub"] = counter["id"]
                    await ws.send_json({"id": counter["sub"], "type": "subscribe_entities", "entity_ids": ids})

            reader_task = asyncio.ensure_future(reader())
            self._call = call
            try:
                self._changed.clear()
                await subscribe()
                self.publisher.reset()
                while True:
                    if reader_task.done():
                        reader_task.result()  # raises why it ended
                    try:
                        event = await asyncio.wait_for(events.get(), 1.0)
                    except asyncio.TimeoutError:
                        event = None
                    if event is not None:
                        await self.handle_entities_event(event)
                    if self._changed.is_set():
                        self._changed.clear()
                        await subscribe()
                    for entity_id, value, attrs in self.publisher.due(self._shared, time.time()):
                        await self._post_state(entity_id, value, attrs)
            finally:
                self._call = None
                reader_task.cancel()
                for fut in pending.values():
                    fut.cancel()

    # --- talking to HA -------------------------------------------------------------

    async def query(self, payload: dict, timeout: float = 30):
        """A websocket request to HA (e.g. {"type": "get_states"}). Raises if not connected."""
        if self._call is None:
            raise ConnectionError("Not connected to Home Assistant")
        return await self._call(payload, timeout=timeout)

    async def call_service(self, domain: str, service: str, data: Optional[dict] = None,
                           target: Optional[dict] = None):
        """e.g. call_service("light", "turn_on", target={"entity_id": "light.hall"})."""
        payload = {"type": "call_service", "domain": domain, "service": service}
        if data:
            payload["service_data"] = data
        if target:
            payload["target"] = target
        return await self.query(payload)

    async def _post_state(self, entity_id: str, state: str, attrs: dict) -> None:
        if self._session is None:
            return
        import aiohttp

        try:
            async with self._session.post(
                f"{SUPERVISOR_API}/states/{entity_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"state": state, "attributes": attrs},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
        except Exception as err:
            self.publisher.forget(entity_id)
            logger.debug("Couldn't post %s: %s", entity_id, err)

    async def shutdown(self) -> None:
        """Mark the add-on's sensors unavailable while it's stopped."""
        if not self.connected:
            return
        for entity_id, state, attrs in SensorPublisher.unavailable():
            await self._post_state(entity_id, state, attrs)

    async def all_states(self, fresh: bool = False) -> list[dict]:
        """Every entity's state in HA (cached briefly)."""
        cached_at, cached = self._states_cache
        if cached and not fresh and time.monotonic() - cached_at < ENTITY_LIST_TTL_S:
            return cached
        if not self.available:
            return []
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SUPERVISOR_API}/states",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                states = await resp.json()
        self._states_cache = (time.monotonic(), states)
        return states

    async def list_entities(self) -> list[dict]:
        """Entities for the pickers on the web page."""
        return entity_list(await self.all_states())

    # --- status ------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "settings": self.settings,
            "configured": self.configured,
            "available": self.available,
            "connected": self.connected,
            "error": self.error,
            "entities": {key: entity_info(self.states, value) for key, value in self.settings.items()},
            "own_sensors": list(ALL_SENSORS),
        }


def entity_list(states: list[dict]) -> list[dict]:
    out = []
    for st in states or []:
        entity_id = st.get("entity_id", "")
        if entity_id.split(".", 1)[0] not in PICKER_DOMAINS:
            continue
        attrs = st.get("attributes") or {}
        out.append({
            "entity_id": entity_id,
            "name": attrs.get("friendly_name") or entity_id,
            "device_class": attrs.get("device_class"),
            "unit": attrs.get("unit_of_measurement"),
            "state": st.get("state"),
        })
    out.sort(key=lambda e: (e["name"] or "").lower())
    return out
