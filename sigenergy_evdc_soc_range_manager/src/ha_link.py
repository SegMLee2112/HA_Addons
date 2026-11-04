"""The add-on's link to Home Assistant, through the Supervisor.

- Reads the entities picked on the Settings tab live (subscribe_entities,
  so changes arrive at once) and calls on_entity_changed for each change.
- Lists HA's entities for the pickers on the web page.
- Posts the add-on's own sensors (src/ha_entities.py) and marks them
  unavailable when the add-on stops.
- call_service() and query() for anything else (turn a light on, read
  history...).

homeassistant_api: true in config.yaml gives the add-on SUPERVISOR_TOKEN.
Outside Home Assistant (development, tests) there's no token: the link
stays off and the rest of the add-on still runs.

Settings (which entities to follow) are saved in /data/sensors.json. Add a
picker: a key in default_settings(), a check in validate_settings(), the
key in watched, and a <span class="picker" data-key="..."> on the page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable, Optional

from src.ha_entities import ALL_SENSORS, SensorPublisher, entity_info
from src.helpers import automatic

logger = logging.getLogger(__name__)

SUPERVISOR_WS = "ws://supervisor/core/websocket"
SUPERVISOR_API = "http://supervisor/core/api"
RECONNECT_DELAYS = (2, 5, 10, 20, 30, 60)
ENTITY_LIST_TTL_S = 30
POST_CHECK_S = 0.25  # changed sensors are posted within this long of the add-on working them out
PICKER_DOMAINS = ("sensor", "binary_sensor", "switch", "input_boolean", "input_number", "number",
                  "select", "input_select", "button", "input_button")

# The entity pickers on the Settings tab: key -> domain(s) allowed
ENTITY_KEYS = {
    "running_state": ("sensor",),
    "vehicle_soc": ("sensor", "input_number", "number"),
    "start_button": ("button", "input_button"),
    "stop_button": ("button", "input_button"),
    "battery_power": ("sensor",),
    "grid_power": ("sensor",),
    "export_power": ("sensor",),
    "ems_mode": ("select", "input_select", "sensor"),
    "charge_signal": ("binary_sensor", "input_boolean", "switch"),
    "v2x_mode": ("input_select", "select", "input_boolean", "switch", "binary_sensor"),
    "soc_high": ("input_number", "number"),
    "soc_low": ("input_number", "number"),
    "capacity": ("input_number", "number", "sensor"),
    # For the battery rate sensors
    "available_power": ("sensor",),
    "home_battery_soc": ("sensor",),
    "home_battery_capacity": ("sensor", "input_number", "number"),
    # For the statistics
    "charger_power": ("sensor",),
    "charged_energy": ("sensor",),
    "discharged_energy": ("sensor",),
    "pv_power": ("sensor",),
    "inverter_power": ("sensor",),
    "home_power": ("sensor",),
    # For the costs (e.g. the Octopus Energy integration's current rate sensors)
    "import_rate": ("sensor", "input_number", "number"),
    "export_rate": ("sensor", "input_number", "number"),
    # The power flow card only: other sensors for solar, the grid, the home battery, the
    # house and the car (empty: the ones above, which it decides from)
    "flow_pv_power": ("sensor",),
    "flow_grid_power": ("sensor",),
    "flow_battery_power": ("sensor",),
    "flow_home_power": ("sensor",),
    "flow_car_power": ("sensor",),
    "grid_voltage": ("sensor",),  # shown on the grid circle (phase A)
}
# The power flow card's own devices off the house (a car charger, a heat pump...): each a
# name, an icon, a power sensor and (optional) when it's plugged in. Not shown without a
# power sensor, or while its plugged-in entity is off.
FLOW_ICONS = ("car", "heatpump", "plug", "flash", "water")
FLOW_POWER_DOMAINS = ("sensor",)
FLOW_PLUGGED_DOMAINS = ("binary_sensor", "input_boolean", "switch", "sensor", "device_tracker")
FLOW_DEVICES_MAX = 6
DEFAULT_FLOW_DEVICES = [{"name": "Car charger", "icon": "car", "power": "", "plugged": ""},
                        {"name": "Heat pump", "icon": "heatpump", "power": "", "plugged": ""}]


def validate_flow_devices(raw) -> list:
    """The power flow card's devices (raises ValueError)."""
    if not isinstance(raw, list):
        raise ValueError("flow_devices must be a list")
    if len(raw) > FLOW_DEVICES_MAX:
        raise ValueError(f"At most {FLOW_DEVICES_MAX} devices")
    out = []
    for n, d in enumerate(raw, 1):
        if not isinstance(d, dict):
            raise ValueError(f"Device {n} must be an object")
        name = str(d.get("name") or "").strip()[:30] or f"Device {n}"
        icon = d.get("icon") or "plug"
        if icon not in FLOW_ICONS:
            raise ValueError(f"Device {n}: the icon must be one of {', '.join(FLOW_ICONS)}")
        dev = {"name": name, "icon": icon}
        for key, domains in (("power", FLOW_POWER_DOMAINS), ("plugged", FLOW_PLUGGED_DOMAINS)):
            value = d.get(key) or ""
            if not isinstance(value, str):
                raise ValueError(f"Device {n}: {key} must be an entity ID or empty")
            value = value.strip()
            if value and value.split(".", 1)[0] not in domains:
                raise ValueError(f"Device {n}: {key} must be a {' or '.join(domains)} entity")
            dev[key] = value
        out.append(dev)
    return out
# What a new install starts with: the Sigenergy integration's entity IDs. The
# limits, capacity and V2X mode are made as helpers when left empty
# (src/helpers.py); the charge signal (e.g. Predbat) is optional.
DEFAULT_ENTITIES = {
    "running_state": "sensor.sigen_inverter_dc_charger_running_state",
    "vehicle_soc": "sensor.sigen_inverter_dc_charger_vehicle_soc",
    "start_button": "button.sigen_inverter_dc_charger_start_charging",
    "stop_button": "button.sigen_inverter_dc_charger_stop_charging",
    "battery_power": "sensor.sigen_plant_battery_power",
    "grid_power": "sensor.sigen_plant_grid_active_power",
    "export_power": "sensor.sigen_plant_grid_export_power",
    "ems_mode": "select.sigen_plant_remote_ems_control_mode",
    "available_power": "sensor.sigen_plant_available_max_active_power",
    "home_battery_soc": "sensor.sigen_plant_battery_state_of_charge",
    "home_battery_capacity": "sensor.sigen_inverter_rated_battery_capacity",
    "charger_power": "sensor.sigen_inverter_dc_charger_output_power",
    "charged_energy": "sensor.sigen_inverter_dc_charger_total_charging_capacity",
    "discharged_energy": "sensor.sigen_inverter_dc_charger_total_discharging_capacity",
    "pv_power": "sensor.sigen_plant_pv_power",
    "inverter_power": "sensor.sigen_inverter_active_power",
    "home_power": "sensor.sigen_plant_consumed_power",
    "grid_voltage": "sensor.sigen_inverter_phase_a_voltage",
}


def default_settings() -> dict:
    return {**{key: DEFAULT_ENTITIES.get(key, "") for key in ENTITY_KEYS},
            "flow_devices": [dict(d) for d in DEFAULT_FLOW_DEVICES],
            "flow_subtract": True}  # the house shows what's left after its devices (False: its whole use)


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
    if "flow_devices" in raw:
        out["flow_devices"] = validate_flow_devices(raw["flow_devices"])
    if "flow_subtract" in raw:
        if not isinstance(raw["flow_subtract"], bool):
            raise ValueError("flow_subtract must be true or false")
        out["flow_subtract"] = raw["flow_subtract"]
    unknown = [k for k in raw if k not in out]
    if unknown:
        raise ValueError("Unknown setting: " + ", ".join(unknown))
    return out


class HaLink:
    def __init__(self, data_dir: Optional[str], shared_state, token: Optional[str] = None,
                 on_entity_changed: Optional[Callable[[str, Optional[dict]], Awaitable[None]]] = None,
                 on_connected: Optional[Callable[[], Awaitable[None]]] = None) -> None:
        self._path = os.path.join(data_dir, "sensors.json") if data_dir else None
        self._shared = shared_state
        self.on_entity_changed = on_entity_changed  # (entity_id, state or None): set by __main__
        self.on_connected = on_connected  # run (as a task) each time the link connects
        self._token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.settings = default_settings()
        self.configured = False  # settings saved at least once
        self.states: dict[str, dict] = {}  # entity_id -> {"state", "attributes", "last_changed"}, the watched ones
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
                        {k: v for k, v in json.load(f).items() if k in ENTITY_KEYS or k in ("flow_devices", "flow_subtract")},
                        default_settings())
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
        own = {v for k, v in self.settings.items() if k in ENTITY_KEYS and v}
        for d in self.settings.get("flow_devices") or []:
            own |= {v for v in (d.get("power"), d.get("plugged")) if v}
        return sorted(own)

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
                                      "last_changed": full.get("lc") or full.get("lu")}
            changed.append(entity_id)
        for entity_id, diff in (event.get("c") or {}).items():
            current = self.states.setdefault(entity_id, {"state": None, "attributes": {}})
            plus = diff.get("+") or {}
            if "s" in plus:
                current["state"] = plus["s"]
                current["last_changed"] = plus.get("lc") or plus.get("lu")  # lc is left out when it equals lu
            if "a" in plus:
                current["attributes"] = {**current["attributes"], **plus["a"]}
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
                if self.on_connected is not None:
                    hook = asyncio.ensure_future(self.on_connected())
                    hook.add_done_callback(lambda t: t.cancelled() or not t.exception() or logger.warning(
                        "Setting up after connecting failed: %s", t.exception()))
                while True:
                    if reader_task.done():
                        reader_task.result()  # raises why it ended
                    try:
                        event = await asyncio.wait_for(events.get(), POST_CHECK_S)
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
            "entities": {key: entity_info(self.states, value) for key, value in self.settings.items() if key in ENTITY_KEYS},
            "devices": [{"power": entity_info(self.states, d.get("power")), "plugged": entity_info(self.states, d.get("plugged"))}
                        for d in self.settings.get("flow_devices") or []],
            "own_sensors": list(ALL_SENSORS),
            "automatic": {key: automatic(key, value) for key, value in self.settings.items() if key in ENTITY_KEYS},
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
