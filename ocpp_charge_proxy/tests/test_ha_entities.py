import asyncio
import enum
import json
import sys
import types

from src.ha_entities import (
    CHARGE_NOW_HOURS, HELPER_ID, LIVE_INTERVAL_S, REFRESH_S, SLOT_SENSORS, ButtonPresses, PluggedInSync, SensorPublisher,
    charge_now_entities, charge_now_helper_id, helper_entity_id,
)
from src.shared_state import SharedState

try:
    import aiohttp
except ImportError:  # local runs without aiohttp: a minimal stand-in
    aiohttp = types.ModuleType("aiohttp")

    class WSMsgType(enum.Enum):
        TEXT = 1
        CLOSED = 2
        ERROR = 3
    aiohttp.WSMsgType = WSMsgType
    aiohttp.ClientTimeout = lambda **kw: None
    sys.modules["aiohttp"] = aiohttp

from src.ha_link import HaLink  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- Plugged In sync ------------------------------------------------------------


def test_first_reading_is_not_a_command_and_addon_wins():
    sync = PluggedInSync()
    assert sync.from_ha("off", plugged_in=True) is None
    assert sync.to_ha("off", plugged_in=True) is True  # set HA to the add-on's value
    assert sync.from_ha("on", plugged_in=True) is None  # our own update coming back
    assert sync.to_ha("on", plugged_in=True) is None


def test_toggling_in_ha_plugs_and_unplugs():
    sync = PluggedInSync()
    sync.to_ha("off", plugged_in=False)
    assert sync.from_ha("on", plugged_in=False) == "plug"
    assert sync.to_ha("on", plugged_in=True) is None
    assert sync.from_ha("off", plugged_in=True) == "unplug"


def test_addon_changes_are_pushed_and_refused_plug_reverted():
    sync = PluggedInSync()
    sync.to_ha("off", plugged_in=False)
    assert sync.to_ha("off", plugged_in=True) is True  # e.g. schedule plugged in
    assert sync.from_ha("on", plugged_in=True) is None
    # HA asks to unplug but the add-on stays plugged in: HA is set back
    assert sync.from_ha("off", plugged_in=True) == "unplug"
    assert sync.to_ha("off", plugged_in=True) is True


def test_unavailable_helper_ignored():
    sync = PluggedInSync()
    assert sync.to_ha(None, True) is None and sync.from_ha("unavailable", True) is None


# --- sensors ------------------------------------------------------------------


def test_sensor_publishing_throttles_live_values():
    pub = SensorPublisher()
    st = SharedState(power_kw=1.37, current_a=5.96, energy_kwh=6612.5)
    first = [x for x in pub.due(st, 1000.0) if x[0] not in SLOT_SENSORS]  # (the next slots: test_next_slots)
    assert {e for e, _, _ in first} == {
        "sensor.ocpp_charge_proxy_power", "sensor.ocpp_charge_proxy_energy", "sensor.ocpp_charge_proxy_current",
        "sensor.ocpp_charge_proxy_status", "sensor.ocpp_charge_proxy_current_limit",
    }
    energy = next(a for e, _, a in first if e.endswith("energy"))
    assert energy["state_class"] == "total_increasing" and energy["device_class"] == "energy"
    st.power_kw, st.energy_kwh = 1.4, 6612.6
    assert [e for e, _, _ in pub.due(st, 1002.0)] == ["sensor.ocpp_charge_proxy_energy"]  # power waits
    assert [e for e, _, _ in pub.due(st, 1000.0 + LIVE_INTERVAL_S)] == ["sensor.ocpp_charge_proxy_power"]
    assert pub.due(st, 1000.0 + LIVE_INTERVAL_S + 1) == []
    assert len([x for x in pub.due(st, 1000.0 + LIVE_INTERVAL_S + REFRESH_S) if x[0] not in SLOT_SENSORS]) == 5  # periodic refresh
    assert all(s == "unavailable" for _, s, _ in SensorPublisher.unavailable())


def test_registry_helpers():
    reg = [
        {"entity_id": "sensor.ocpp_charge_proxy_energy", "platform": "ocpp_charge_proxy"},
        {"entity_id": "input_boolean.car", "platform": "input_boolean", "unique_id": HELPER_ID},
    ]
    assert helper_entity_id(reg) == "input_boolean.car"
    assert helper_entity_id([]) == f"input_boolean.{HELPER_ID}"


# --- the whole conversation with a fake Home Assistant ------------------------


class Msg:
    def __init__(self, data):
        self.type = aiohttp.WSMsgType.TEXT
        self.data = json.dumps(data)


class FakeHA:
    """Speaks just enough of HA's websocket API."""

    def __init__(self, helper_exists=False, registry=None, helper_state="off"):
        self.sent = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.helpers = [{"id": HELPER_ID}] if helper_exists else []
        self.registry = registry or []
        self.helper_state = helper_state
        self.posted = []
        self.buttons = []  # input_button helper IDs

    # aiohttp ClientSession bits
    def ws_connect(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json=None, **kw):
        self.posted.append((url.rsplit("/", 1)[-1], json["state"]))
        return FakeResp()

    # websocket
    async def receive_json(self, timeout=None):
        if not self.sent:
            return {"type": "auth_required"}
        return {"type": "auth_ok"}

    async def send_json(self, msg):
        self.sent.append(msg)
        t, i = msg.get("type"), msg.get("id")
        if t == "auth":
            return
        result, ok = None, True
        if t == "config/entity_registry/list":
            result = self.registry + ([{"entity_id": f"input_boolean.{HELPER_ID}", "platform": "input_boolean",
                                        "unique_id": HELPER_ID}] if self.helpers else []) + [
                {"entity_id": f"input_button.{b}", "platform": "input_button", "unique_id": b} for b in self.buttons]
        elif t == "input_button/list":
            result = [{"id": b} for b in self.buttons]
        elif t == "input_button/create":
            bid = msg["name"].lower().replace(" ", "_")
            self.buttons.append(bid)
            result = {"id": bid}
        elif t == "get_states":
            result = getattr(self, "all_states", [])
        elif t == "input_boolean/list":
            result = self.helpers
        elif t == "input_boolean/create":
            self.helpers.append({"id": HELPER_ID})
            result = {"id": HELPER_ID}
        elif t == "call_service":
            self.helper_state = "on" if msg["service"] == "turn_on" else "off"
            self.inbox.put_nowait(Msg({"type": "event", "id": self.sub, "event": {
                "c": {msg["target"]["entity_id"]: {"+": {"s": self.helper_state}}}}}))
        elif t == "subscribe_entities":
            self.sub = i
            self.inbox.put_nowait(Msg({"type": "result", "id": i, "success": True}))
            known = {st["entity_id"]: st for st in getattr(self, "all_states", [])}
            self.inbox.put_nowait(Msg({"type": "event", "id": i, "event": {"a": {
                e: ({"s": known[e]["state"], "a": known[e]["attributes"]} if e in known else
                    {"s": self.helper_state if e.startswith("input_boolean") else
                     "unknown" if e.startswith("input_button") else "50", "a": {}})
                for e in msg["entity_ids"]}}}))
            return
        self.inbox.put_nowait(Msg({"type": "result", "id": i, "success": ok, "result": result}))

    def user_toggles(self, state):
        self.helper_state = state
        self.inbox.put_nowait(Msg({"type": "event", "id": self.sub, "event": {
            "c": {f"input_boolean.{HELPER_ID}": {"+": {"s": state}}}}}))

    def user_presses(self, entity_id, when):
        self.inbox.put_nowait(Msg({"type": "event", "id": self.sub, "event": {
            "c": {entity_id: {"+": {"s": when}}}}}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.inbox.get()


class FakeResp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass


def _scenario(fake, steps, on_charge_now=None):
    state = SharedState(power_kw=1.37, current_a=5.96, energy_kwh=6612.5, plugged_in=False)
    calls = []

    async def plug(source=None):
        calls.append("plug")
        state.plugged_in = True

    async def unplug(source=None):
        calls.append("unplug")
        state.plugged_in = False

    async def set_soc(soc):
        pass

    link = HaLink(None, state, lambda kw: None, set_soc, plug, token="t", unplug=unplug)
    link.on_charge_now = on_charge_now

    async def main():
        link._session = fake
        task = asyncio.ensure_future(link._connection(fake))
        await asyncio.sleep(0.3)
        for step in steps:
            step(fake, state)
            await asyncio.sleep(1.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(main())
    return link, state, calls


def test_helper_created_and_kept_in_step():
    fake = FakeHA()
    link, state, calls = _scenario(fake, [
        lambda f, s: f.user_toggles("on"),          # turned on in HA -> plug
        lambda f, s: setattr(s, "plugged_in", False),  # unplugged in the add-on -> HA off
    ])
    types_sent = [m.get("type") for m in fake.sent]
    assert "input_boolean/create" in types_sent
    assert link.plug_sync.entity_id == f"input_boolean.{HELPER_ID}"
    assert calls == ["plug"]
    assert fake.helper_state == "off"
    assert {"ocpp_charge_proxy_power", "ocpp_charge_proxy_energy", "ocpp_charge_proxy_current"} <= {
        e.split(".", 1)[1] for e, _ in fake.posted}


def test_addon_value_wins_on_connect():
    fake = FakeHA(helper_exists=True, helper_state="on")  # HA says on, add-on unplugged
    _, _, calls = _scenario(fake, [])
    assert calls == [] and fake.helper_state == "off"


def test_status_and_limit_sensors_posted_on_change():
    pub = SensorPublisher()
    st = SharedState(state="Preparing", current_amps_setting=6, current_amps_effective=6,
                     current_amps_provider_limit=None)
    pub.due(st, 1000.0)
    assert pub.due(st, 1001.0) == []
    st.state = "Charging"
    st.current_amps_provider_limit = 32.0
    due = {e: (v, a) for e, v, a in pub.due(st, 1002.0)}
    assert due["sensor.ocpp_charge_proxy_status"][0] == "Charging"
    value, attrs = due["sensor.ocpp_charge_proxy_current_limit"]
    assert value == "6" and attrs["max_amps"] == 6 and attrs["provider_limit_amps"] == 32.0
    assert attrs["state_class"] == "measurement"
    pub.forget("sensor.ocpp_charge_proxy_status")  # a failed post is sent again
    assert [e for e, _, _ in pub.due(st, 1003.0)] == ["sensor.ocpp_charge_proxy_status"]
    assert len(SensorPublisher.unavailable()) == 5


def test_status_sensor_says_scheduled():
    pub = SensorPublisher()
    st = SharedState(state="Preparing", scheduled=True)
    due = {e: v for e, v, _ in pub.due(st, 1000.0)}
    assert due["sensor.ocpp_charge_proxy_status"] == "Scheduled"
    st.scheduled = False
    assert {e: v for e, v, _ in pub.due(st, 1001.0)}["sensor.ocpp_charge_proxy_status"] == "Preparing"


def test_supplier_dispatch_sensor_found_and_followed():
    fake = FakeHA(helper_exists=True)
    sensor = "binary_sensor.octopus_energy_x_intelligent_dispatching"
    fake.all_states = [{"entity_id": sensor, "state": "off", "attributes": {"planned_dispatches": [
        {"start": "2099-01-01T23:30:00+00:00", "end": "2099-01-02T05:30:00+00:00", "charge_in_kwh": -20.0}]}}]
    link, _, _ = _scenario(fake, [])
    assert link.dispatch_entities == [sensor]
    sub = next(m for m in fake.sent if m.get("type") == "subscribe_entities")
    assert sensor in sub["entity_ids"]
    info = link.smart_charging()
    assert info["found"] and info["provider"] == "Octopus Energy" and len(info["planned"]) == 1
    assert link.scheduled() is True and link.slot_now() is False
    health = link.smart_charging_health()
    assert health["found"] and health["entity_id"] == sensor and health["searched"]


def test_no_supplier_sensor():
    fake = FakeHA(helper_exists=True)
    link, _, _ = _scenario(fake, [])
    assert link.smart_charging()["found"] is False and link.scheduled() is None and link.slot_now() is None
    assert link.smart_charging_health()["found"] is False


# --- Charge now buttons ------------------------------------------------------------


def test_button_presses_ignore_the_state_on_subscribe():
    p = ButtonPresses()
    assert not p.pressed("input_button.x", "2026-10-15T08:00:00+00:00")  # what it was before we looked
    assert p.pressed("input_button.x", "2026-10-15T09:00:00+00:00")
    assert not p.pressed("input_button.x", "2026-10-15T09:00:00+00:00")  # same again
    assert not p.pressed("input_button.x", "unavailable")
    p.reset()
    assert not p.pressed("input_button.x", "2026-10-15T10:00:00+00:00")


def test_charge_now_entities_follow_renames():
    reg = [{"entity_id": "input_button.charge_2", "platform": "input_button", "unique_id": charge_now_helper_id(2)}]
    found = charge_now_entities(reg)
    assert found["input_button.charge_2"] == 2
    assert found[f"input_button.{charge_now_helper_id(12)}"] == 12
    assert sorted(found.values()) == list(CHARGE_NOW_HOURS)


def test_charge_now_buttons_created_and_pressing_one_charges():
    fake = FakeHA(helper_exists=True)
    asked = []

    async def charge_now(hours):
        asked.append(hours)
        return {"plan": {"end": "2026-10-15T12:00:00+01:00", "notes": []}}

    link, _, _ = _scenario(fake, [
        lambda f, s: f.user_presses("input_button.ocpp_charge_proxy_charge_now_2h", "2026-10-15T09:00:00+00:00"),
    ], on_charge_now=charge_now)
    assert sorted(fake.buttons) == sorted(charge_now_helper_id(h) for h in CHARGE_NOW_HOURS)
    sub = [m for m in fake.sent if m.get("type") == "subscribe_entities"][-1]
    assert "input_button.ocpp_charge_proxy_charge_now_12h" in sub["entity_ids"]
    assert asked == [2]
    assert len(link.snapshot()["ha_entities"]["charge_now"]) == len(CHARGE_NOW_HOURS)


def test_charge_now_buttons_not_created_twice():
    fake = FakeHA(helper_exists=True)
    fake.buttons = [charge_now_helper_id(h) for h in CHARGE_NOW_HOURS]
    _scenario(fake, [])
    assert "input_button/create" not in [m.get("type") for m in fake.sent]
