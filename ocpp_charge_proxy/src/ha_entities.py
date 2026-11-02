"""The add-on's own Home Assistant entities (no integration needed).

- Plugged In: an input_boolean helper the add-on creates
  (input_boolean.ocpp_charge_proxy_plugged_in). Turning it on/off in HA
  plugs in / unplugs; the add-on keeps it in step with its own Plugged In
  (web page, schedule, auto plug-in...). The add-on is the source of truth:
  while it's stopped the helper can be toggled but nothing happens, and it's
  set back when the add-on reconnects.
- Power, Energy and Current: posted as sensor states
  (sensor.ocpp_charge_proxy_power / _energy / _current), with the device and
  state classes the Energy dashboard needs. They're "unavailable" while the
  add-on is stopped, and re-posted when it (or HA) restarts.
- Charge now: input_button helpers the add-on creates, one per length the
  web page offers (input_button.ocpp_charge_proxy_charge_now_1h ... _12h).
  Pressing one (from the UI or an automation's input_button.press) does what
  the page's Charge now button does, going ahead even over the supplier's
  daily limit, as auto plug-in does. Presses while the add-on is stopped are
  ignored.
- Next slots, for Predbat: the date,
  start and end of the schedule's next three slots
  (sensor.ocpp_charge_proxy_next_slot_date / _start_time / _end_time, then
  _next_slot_2_... and _next_slot_3_...), for Predbat's rates_import_override
  (the rate is set there).
  1999-01-01 and 00:00:00 when there isn't one, which Predbat ignores. A slot
  drops off when it starts, or when the add-on plugs in for it.
- Status (the OCPP state: Charging, Preparing..., or Waiting for supplier /
  Scheduled while plugged
  in with a slot planned by your supplier) and Current limit (the
  current the charger uses, with your max and the supplier's limit as
  attributes). HA records all of these, and the web page's charts and daily
  energy are read back from that history (src/ha_history.py).

Kept free of I/O so the decisions can be tested on their own; HaLink does the
talking.
"""

from __future__ import annotations

from typing import Optional

from src.shared_state import display_status

HELPER_ID = "ocpp_charge_proxy_plugged_in"
HELPER_NAME = "OCPP Charge Proxy Plugged In"
HELPER_ICON = "mdi:ev-plug-type2"

LIVE_INTERVAL_S = 10  # power / current posted at most this often
REFRESH_S = 300  # everything re-posted at least this often

SENSORS = {
    "power_kw": ("sensor.ocpp_charge_proxy_power", {
        "friendly_name": "OCPP Charge Proxy Power", "unit_of_measurement": "kW",
        "device_class": "power", "state_class": "measurement", "icon": "mdi:ev-station",
    }, 3),
    "energy_kwh": ("sensor.ocpp_charge_proxy_energy", {
        "friendly_name": "OCPP Charge Proxy Energy", "unit_of_measurement": "kWh",
        "device_class": "energy", "state_class": "total_increasing", "icon": "mdi:ev-station",
    }, 3),
    "current_a": ("sensor.ocpp_charge_proxy_current", {
        "friendly_name": "OCPP Charge Proxy Current", "unit_of_measurement": "A",
        "device_class": "current", "state_class": "measurement", "icon": "mdi:ev-station",
    }, 2),
}
LIVE_KEYS = {"power_kw", "current_a"}

STATUS_SENSOR = "sensor.ocpp_charge_proxy_status"
STATUS_ATTRS = {"friendly_name": "OCPP Charge Proxy Status", "icon": "mdi:ev-plug-type2"}
LIMIT_SENSOR = "sensor.ocpp_charge_proxy_current_limit"
LIMIT_ATTRS = {
    "friendly_name": "OCPP Charge Proxy Current limit", "unit_of_measurement": "A",
    "device_class": "current", "state_class": "measurement", "icon": "mdi:current-ac",
}
ALL_SENSORS = [e for e, _, _ in SENSORS.values()] + [STATUS_SENSOR, LIMIT_SENSOR]

# The next slots, for Predbat's rates_import_override (src/automation.py next_slots)
SLOT_COUNT = 3
SLOT_NONE = {"date": "1999-01-01", "start": "00:00:00", "end": "00:00:00"}  # (as Octopus Charge Pack's)


def slot_sensor_id(n: int, part: str) -> str:
    """n 1..3, part date/start/end: sensor.ocpp_charge_proxy_next_slot_date, ..._next_slot_2_start_time..."""
    return f"sensor.ocpp_charge_proxy_next_slot{'' if n == 1 else f'_{n}'}_{part if part == 'date' else part + '_time'}"


def slot_states(slots: list) -> list[tuple[str, str, dict]]:
    """The slot sensors' states: (entity_id, state, attributes)."""
    out = []
    for n in range(1, SLOT_COUNT + 1):
        slot = slots[n - 1] if n <= len(slots) else SLOT_NONE
        label = "Next slot" if n == 1 else f"Next slot {n}"
        for part, name, icon in (("date", "date", "mdi:calendar"), ("start", "start time", "mdi:clock-start"),
                                 ("end", "end time", "mdi:clock-end")):
            out.append((slot_sensor_id(n, part), slot[part],
                        {"friendly_name": f"OCPP Charge Proxy {label} {name}", "icon": icon}))
    return out


SLOT_SENSORS = [e for e, _, _ in slot_states([])]

CHARGE_NOW_HOURS = (1, 2, 3, 4, 6, 8, 12)  # as the web page's Charge now buttons
CHARGE_NOW_ICON = "mdi:ev-station"


def charge_now_helper_id(hours) -> str:
    return f"ocpp_charge_proxy_charge_now_{hours}h"


def charge_now_helper_name(hours) -> str:
    # HA makes the helper's ID from its name, so this gives charge_now_helper_id()
    return f"OCPP Charge Proxy Charge Now {hours}h"


def charge_now_entities(registry: list[dict]) -> dict[str, int]:
    """entity_id -> hours, for each Charge now button (the user may have renamed them)."""
    by_unique = {e.get("unique_id"): e.get("entity_id") for e in registry if e.get("platform") == "input_button"}
    out = {}
    for hours in CHARGE_NOW_HOURS:
        hid = charge_now_helper_id(hours)
        out[by_unique.get(hid) or f"input_button.{hid}"] = hours
    return out



class ButtonPresses:
    """Tells a press of an input_button (its state becomes the press time)
    from the state it already had when the add-on (re)subscribed."""

    def __init__(self) -> None:
        self.seen: dict[str, Optional[str]] = {}

    def reset(self) -> None:
        self.seen = {}

    def pressed(self, entity_id: str, state: Optional[str]) -> bool:
        first = entity_id not in self.seen
        before = self.seen.get(entity_id)
        self.seen[entity_id] = state
        if first or state in (None, "unknown", "unavailable"):
            return False
        return state != before


class PluggedInSync:
    """Keeps the input_boolean and the add-on's Plugged In in step."""

    def __init__(self) -> None:
        self.entity_id: Optional[str] = None
        self.synced: Optional[bool] = None  # last value both sides agreed on (None: not yet)

    def reset(self) -> None:
        self.synced = None

    def from_ha(self, state: Optional[str], plugged_in: bool) -> Optional[str]:
        """The helper changed in HA. Returns "plug"/"unplug" to act on, or None.

        The first reading after (re)connecting isn't a command: the add-on's
        own value wins and is pushed to HA (see to_ha)."""
        if state not in ("on", "off"):
            return None
        wanted = state == "on"
        if self.synced is None:
            return None  # to_ha() sets HA to the add-on's value
        if wanted == self.synced:
            return None  # our own update coming back
        self.synced = wanted
        if wanted == plugged_in:
            return None
        return "plug" if wanted else "unplug"

    def to_ha(self, ha_state: Optional[str], plugged_in: bool) -> Optional[bool]:
        """Returns the value to set the helper to, or None if it's in step."""
        if ha_state not in ("on", "off"):
            return None  # helper not seen yet
        if self.synced is None or plugged_in != self.synced:
            self.synced = plugged_in
            if (ha_state == "on") != plugged_in:
                return plugged_in
        return None


class SensorPublisher:
    """Decides which sensor states to post."""

    def __init__(self) -> None:
        self.sent: dict[str, object] = {}
        self.sent_at: dict[str, float] = {}

    def reset(self) -> None:
        self.sent.clear()
        self.sent_at.clear()

    def forget(self, entity_id: str) -> None:
        """A post failed: send it again next time."""
        key = next((k for k, v in SENSORS.items() if v[0] == entity_id), entity_id)
        self.sent.pop(key, None)

    def due(self, shared_state, now: float) -> list[tuple[str, str, dict]]:
        out = []
        # The next slots, for Predbat: whenever they change
        for entity_id, value, attrs in slot_states(list(getattr(shared_state, "next_slots", None) or [])):
            key = ("slot", value)
            if key != self.sent.get(entity_id) or now - self.sent_at.get(entity_id, 0.0) >= REFRESH_S:
                self.sent[entity_id], self.sent_at[entity_id] = key, now
                out.append((entity_id, value, attrs))
        # Status and current limit: whenever they change (rarely)
        limit_attrs = {
            **LIMIT_ATTRS,
            "max_amps": shared_state.current_amps_setting,
            "provider_limit_amps": shared_state.current_amps_provider_limit,
        }
        for entity_id, value, attrs in (
            (STATUS_SENSOR, display_status(shared_state), STATUS_ATTRS),
            (LIMIT_SENSOR, str(shared_state.current_amps_effective), limit_attrs),
        ):
            key = (value, tuple(sorted((k, str(v)) for k, v in attrs.items())))
            if key != self.sent.get(entity_id) or now - self.sent_at.get(entity_id, 0.0) >= REFRESH_S:
                self.sent[entity_id], self.sent_at[entity_id] = key, now
                out.append((entity_id, value, attrs))
        for key, (entity_id, attrs, digits) in SENSORS.items():
            value = round(float(getattr(shared_state, key) or 0.0), digits)
            last, at = self.sent.get(key), self.sent_at.get(key, 0.0)
            changed = value != last
            if key in LIVE_KEYS and changed and last is not None and now - at < LIVE_INTERVAL_S:
                changed = False  # live values: at most every LIVE_INTERVAL_S
            if changed or now - at >= REFRESH_S:
                self.sent[key], self.sent_at[key] = value, now
                out.append((entity_id, str(value), attrs))
        return out

    @staticmethod
    def unavailable() -> list[tuple[str, str, dict]]:
        return [(entity_id, "unavailable", attrs) for entity_id, attrs, _ in SENSORS.values()] + [
            (STATUS_SENSOR, "unavailable", STATUS_ATTRS), (LIMIT_SENSOR, "unavailable", LIMIT_ATTRS)]


def helper_entity_id(registry: list[dict], helper_id: str = HELPER_ID) -> str:
    """The helper's entity ID (the user may have renamed it)."""
    for e in registry:
        if e.get("platform") == "input_boolean" and e.get("unique_id") == helper_id:
            return e.get("entity_id") or f"input_boolean.{helper_id}"
    return f"input_boolean.{helper_id}"
