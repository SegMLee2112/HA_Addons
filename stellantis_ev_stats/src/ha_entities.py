"""The add-on's own Home Assistant entities, posted as states through the
Supervisor (no integration needed). They show as "unavailable" while the
add-on is stopped, and are posted again when it (or HA) restarts.

Add a sensor: an entry in SENSORS (entity ID, attributes) and a value for it
in values() below: the value, or (value, extra attributes).

Kept free of I/O so it can be tested on its own; HaLink posts them."""
from __future__ import annotations

import json
from typing import Optional

PREFIX = "stellantis_ev_stats"  # entity IDs: sensor.stellantis_ev_stats_...
NAME = "EV Stats"


def _measure(name: str, unit: str, icon: str, **extra) -> dict:
    return {"friendly_name": f"{NAME} {name}", "unit_of_measurement": unit, "state_class": "measurement",
            "icon": icon, **extra}


SENSORS = {
    "status": (f"sensor.{PREFIX}_status", {"friendly_name": f"{NAME} Status", "icon": "mdi:car-electric"}),
    "real_range_full": (f"sensor.{PREFIX}_real_range_full",
                        _measure("Real Range at 100%", "mi", "mdi:map-marker-distance", device_class="distance")),
    "real_range_left": (f"sensor.{PREFIX}_real_range_left",
                        _measure("Real Range Left", "mi", "mdi:map-marker-distance", device_class="distance")),
    "car_range_full": (f"sensor.{PREFIX}_car_range_full",
                       _measure("Car's Range at 100%", "mi", "mdi:car-cruise-control", device_class="distance")),
    "efficiency": (f"sensor.{PREFIX}_efficiency", _measure("Efficiency (30 days)", "mi/kWh", "mdi:leaf")),
    "efficiency_now": (f"sensor.{PREFIX}_efficiency_now", _measure("Efficiency Now", "mi/kWh", "mdi:thermometer")),
    "efficiency_now_town": (f"sensor.{PREFIX}_efficiency_now_town", _measure("Efficiency Now Town", "mi/kWh", "mdi:city")),
    "efficiency_now_mixed": (f"sensor.{PREFIX}_efficiency_now_mixed", _measure("Efficiency Now Mixed", "mi/kWh", "mdi:road-variant")),
    "efficiency_now_faster_roads": (f"sensor.{PREFIX}_efficiency_now_faster_roads",
                                    _measure("Efficiency Now Faster Roads", "mi/kWh", "mdi:road")),
    "efficiency_now_motorway": (f"sensor.{PREFIX}_efficiency_now_motorway",
                                _measure("Efficiency Now Motorway", "mi/kWh", "mdi:highway")),
    **{f"range_left_{key}": (f"sensor.{PREFIX}_range_left_{key}",
                             _measure(f"Range Left {name}", "mi", "mdi:map-marker-distance", device_class="distance"))
       for key, name in (("town", "Town"), ("mixed", "Mixed"), ("faster_roads", "Faster Roads"), ("motorway", "Motorway"))},
    "usable_capacity": (f"sensor.{PREFIX}_usable_capacity",
                        _measure("Usable Capacity", "kWh", "mdi:car-battery", device_class="energy_storage")),
    "battery_soh": (f"sensor.{PREFIX}_battery_soh", _measure("Battery Health", "%", "mdi:battery-heart-variant")),
    "commute_charge": (f"sensor.{PREFIX}_commute_charge",
                       _measure("Charge for the Commute", "%", "mdi:battery-arrow-up", device_class="battery")),
    "drain": (f"sensor.{PREFIX}_drain", _measure("Drain While Parked", "%/day", "mdi:battery-minus-outline")),
    "trips": (f"sensor.{PREFIX}_trips", {"friendly_name": f"{NAME} Trips Recorded", "icon": "mdi:map-marker-path",
                                         "state_class": "total_increasing"}),
}
ALL_SENSORS = [entity_id for entity_id, _ in SENSORS.values()]

REFRESH_S = 300  # everything posted again at least this often, even unchanged


def values(state) -> dict:
    """The value of each sensor (by its key in SENSORS) from the shared state:
    the value, or (value, extra attributes)."""
    b = state.brief or {}
    now, eff, health = b.get("now") or {}, b.get("efficiency") or {}, b.get("health") or {}
    band = now.get("band")
    at = {"temperature_c": now.get("temp_c"), "temperature_from": now.get("temp_from"),
          "band": None if band is None else f"{band} to {band + 5} °C", "mi_per_kwh": now.get("mi_per_kwh")}
    return {
        "status": (state.status, {"reason": state.reason}),
        "real_range_full": (now.get("real_full_mi"), {**at, "usable_kwh": b.get("usable_kwh")}),
        "real_range_left": (now.get("real_left_mi"), {**at, "soc": now.get("soc")}),
        "car_range_full": (now.get("car_full_mi"), {"car_range_mi": now.get("car_range_mi"), "soc": now.get("soc")}),
        "efficiency": ((eff.get("last30") or {}).get("mi_per_kwh"),
                       {"trips": (eff.get("last30") or {}).get("trips"), "miles": (eff.get("last30") or {}).get("mi")}),
        "efficiency_now": (now.get("mi_per_kwh"), {**at, "trips": now.get("band_trips"),
                                                   "real_range_full_mi": now.get("real_full_mi")}),
        **{f"efficiency_now_{x['name'].lower().replace(' ', '_')}": (x.get("mi_per_kwh"), {
            "temperature_c": now.get("temp_c"), "temperature_from": now.get("temp_from"),
            "band": None if x.get("band") is None else f"{x['band']} to {x['band'] + 5} °C",
            "at_this_temperature": x.get("exact"), "trips": x.get("trips"), "real_range_full_mi": x.get("real_full_mi"),
            "range_left_mi": x.get("real_left_mi")})
           for x in _by_speed(now)},
        **{f"range_left_{x['name'].lower().replace(' ', '_')}": (x.get("real_left_mi"), {
            "soc": now.get("soc"), "mi_per_kwh": x.get("mi_per_kwh"), "temperature_c": now.get("temp_c"),
            "real_range_full_mi": x.get("real_full_mi")})
           for x in _by_speed(now)},
        "usable_capacity": (b.get("usable_kwh"), {"from": b.get("usable_from")}),
        "battery_soh": (health.get("soh_capacity"), {"soh_resistance": health.get("soh_resistance")}),
        "commute_charge": ((b.get("commute") or {}).get("charge_to"), {
            k: (b.get("commute") or {}).get(k) for k in ("mi", "departure", "temp_c", "mi_per_kwh", "kwh", "need_pct",
                                                          "arrive_pct", "basis", "enough")}),
        "drain": ((b.get("drain") or {}).get("pct_per_day"),
                  {"mi_per_day": (b.get("drain") or {}).get("mi_per_day"), "spells": (b.get("drain") or {}).get("spells")}),
        "trips": state.trips,
    }


def _by_speed(now: dict) -> list:
    """Each kind of driving's estimate now (all of them, even before there's one)."""
    from src.evstats import SPEED_BANDS

    have = {x["name"]: x for x in now.get("by_speed") or []}
    return [have.get(name) or {"name": name} for name, _, _ in SPEED_BANDS]


class SensorPublisher:
    """Decides what to post: anything that changed, everything every REFRESH_S."""

    def __init__(self) -> None:
        self._sent: dict[str, tuple[str, float]] = {}  # entity_id -> (what was posted, when)

    def reset(self) -> None:
        self._sent.clear()  # e.g. HA restarted: post everything again

    def forget(self, entity_id: str) -> None:
        self._sent.pop(entity_id, None)  # posting failed: try again next time

    def due(self, state, now: float) -> list[tuple[str, str, dict]]:
        out = []
        for key, value in values(state).items():
            entity_id, attrs = SENSORS[key]
            extra = {}
            if isinstance(value, tuple):
                value, extra = value
            text = "unknown" if value is None else str(value)
            sig = text + json.dumps(extra, sort_keys=True, default=str)
            last = self._sent.get(entity_id)
            if last is None or last[0] != sig or now - last[1] >= REFRESH_S:
                self._sent[entity_id] = (sig, now)
                out.append((entity_id, text, {**attrs, **extra}))
        return out

    @staticmethod
    def unavailable() -> list[tuple[str, str, dict]]:
        return [(entity_id, "unavailable", dict(attrs)) for entity_id, attrs in SENSORS.values()]


def entity_info(states: dict, entity_id: Optional[str]) -> Optional[dict]:
    """An entity's state and name for the web page."""
    if not entity_id:
        return None
    st = states.get(entity_id)
    attrs = (st or {}).get("attributes") or {}
    return {
        "entity_id": entity_id,
        "name": attrs.get("friendly_name") or entity_id,
        "state": (st or {}).get("state"),
        "unit": attrs.get("unit_of_measurement"),
    }
