"""The add-on's own Home Assistant entities, posted as states through the
Supervisor (no integration needed). They show as "unavailable" while the
add-on is stopped, and are posted again when it (or HA) restarts.

Add a sensor: an entry in SENSORS (entity ID, attributes) and a value for
it in values() below: the value, or (value, extra attributes).

Kept free of I/O so it can be tested on its own; HaLink posts them."""
from __future__ import annotations

import json
from typing import Optional

PREFIX = "evdc_soc_range"  # entity IDs: sensor.evdc_soc_range_...
NAME = "EVDC SoC Range"

SENSORS = {
    "status": (f"sensor.{PREFIX}_status", {"friendly_name": f"{NAME} Status", "icon": "mdi:ev-station"}),
    "last_action": (f"sensor.{PREFIX}_last_action", {"friendly_name": f"{NAME} Last Action", "icon": "mdi:history"}),
    "vehicle_soc": (f"sensor.{PREFIX}_vehicle_soc", {
        "friendly_name": f"{NAME} Vehicle SoC", "unit_of_measurement": "%", "device_class": "battery",
        "state_class": "measurement"}),
    "plugged_in": (f"binary_sensor.{PREFIX}_plugged_in", {
        "friendly_name": f"{NAME} Plugged In", "device_class": "plug"}),
    "charger_running": (f"binary_sensor.{PREFIX}_charger_running", {
        "friendly_name": f"{NAME} Charger Running", "device_class": "running"}),
    "charger_alarm": (f"binary_sensor.{PREFIX}_charger_alarm", {
        "friendly_name": f"{NAME} Charger Alarm", "device_class": "problem"}),
    # The virtual battery: the car between its limits, as one battery (e.g. for Predbat)
    "vb_soc": (f"sensor.{PREFIX}_virtual_battery_soc", {
        "friendly_name": f"{NAME} Virtual Battery SoC", "unit_of_measurement": "%", "device_class": "battery",
        "state_class": "measurement"}),
    "vb_usable": (f"sensor.{PREFIX}_virtual_battery_usable_energy", {
        "friendly_name": f"{NAME} Virtual Battery Usable Energy", "unit_of_measurement": "kWh",
        "device_class": "energy_storage", "state_class": "measurement", "icon": "mdi:car-battery"}),
    "vb_max": (f"sensor.{PREFIX}_virtual_battery_max_energy", {
        "friendly_name": f"{NAME} Virtual Battery Max Energy", "unit_of_measurement": "kWh",
        "device_class": "energy_storage", "state_class": "measurement", "icon": "mdi:arrow-expand-vertical"}),
    # The power flow for the dashboard card (evdc-power-flow-card.js): the house's use, with
    # the rest as attributes. No state_class, so it isn't kept in the long-term statistics.
    "power_flow": (f"sensor.{PREFIX}_power_flow", {
        "friendly_name": f"{NAME} Power Flow", "unit_of_measurement": "kW", "device_class": "power",
        "icon": "mdi:home-lightning-bolt-outline"}),
    "battery_rate": (f"sensor.{PREFIX}_battery_rate", {
        "friendly_name": f"{NAME} Battery Rate", "unit_of_measurement": "kW", "device_class": "power",
        "state_class": "measurement"}),
    "battery_rate_car": (f"sensor.{PREFIX}_battery_rate_car", {
        "friendly_name": f"{NAME} Battery Rate Car", "unit_of_measurement": "kW", "device_class": "power",
        "state_class": "measurement"}),
    "battery_rate_house": (f"sensor.{PREFIX}_battery_rate_house", {
        "friendly_name": f"{NAME} Battery Rate House", "unit_of_measurement": "kW", "device_class": "power",
        "state_class": "measurement"}),
    "capacity_estimate": (f"sensor.{PREFIX}_capacity_estimate", {
        "friendly_name": f"{NAME} Capacity Estimate", "unit_of_measurement": "kWh",
        "device_class": "energy_storage", "state_class": "measurement", "icon": "mdi:car-battery"}),
    "battery_health": (f"sensor.{PREFIX}_battery_health", {
        "friendly_name": f"{NAME} Battery Health", "unit_of_measurement": "%", "state_class": "measurement",
        "icon": "mdi:battery-heart-variant"}),
    "charge_efficiency": (f"sensor.{PREFIX}_charge_efficiency", {
        "friendly_name": f"{NAME} Charge Efficiency", "unit_of_measurement": "%", "state_class": "measurement",
        "icon": "mdi:transmission-tower-import"}),
    "discharge_efficiency": (f"sensor.{PREFIX}_discharge_efficiency", {
        "friendly_name": f"{NAME} Discharge Efficiency", "unit_of_measurement": "%", "state_class": "measurement",
        "icon": "mdi:transmission-tower-export"}),
    "conversion_loss_today": (f"sensor.{PREFIX}_conversion_loss_today", {
        "friendly_name": f"{NAME} Conversion Loss Today", "unit_of_measurement": "kWh",
        "state_class": "measurement", "icon": "mdi:fire"}),
    "round_trip_efficiency": (f"sensor.{PREFIX}_round_trip_efficiency", {
        "friendly_name": f"{NAME} Round Trip Efficiency", "unit_of_measurement": "%", "state_class": "measurement",
        "icon": "mdi:sync"}),
    "dropouts_today": (f"sensor.{PREFIX}_dropouts_today", {
        "friendly_name": f"{NAME} Discharge Dropouts Today", "icon": "mdi:car-off"}),
    "presses_today": (f"sensor.{PREFIX}_presses_today", {
        "friendly_name": f"{NAME} Button Presses Today", "icon": "mdi:gesture-tap-button"}),
}
ALL_SENSORS = [entity_id for entity_id, _ in SENSORS.values()]

REFRESH_S = 300  # everything posted again at least this often, even unchanged
# The power flow changes all the time: posted at most this often (s)
THROTTLE_S = {f"sensor.{PREFIX}_power_flow": 5}


def _pct(fraction) -> Optional[float]:
    return None if fraction is None else round(fraction * 100, 1)


def _onoff(value) -> Optional[str]:
    return None if value is None else ("on" if value else "off")


def values(state) -> dict:
    """The value of each sensor (by its key in SENSORS) from the shared state:
    the value, or (value, extra attributes)."""
    i, energy, last, st = state.inputs or {}, state.energy or {}, state.last_action or {}, state.stats or {}
    vb_attrs = {"low_limit": i.get("low"), "high_limit": i.get("high"),
                "car_capacity_kwh": (state.readings or {}).get("capacity_kwh"),
                "in_use": (energy.get("window_kwh") or 0) > 0.01}
    action = None
    if last:
        verb = last.get("action") or ""
        action = (f"Failed to {verb}" if last.get("error") else f"Would {verb}" if last.get("observe_only")
                  else "Started" if verb == "start" else "Stopped")
    return {
        "status": (state.status, {"reason": state.reason, "rule": state.rule, "watch_only": state.observe_only}),
        "last_action": (action, {k: last.get(k) for k in ("at", "rule", "reason", "soc", "error")} if last else {}),
        "vehicle_soc": (i.get("soc"), {"read_at": state.soc_at, "from_before_plug_in": state.soc_before_plug_in}),
        "plugged_in": _onoff(i.get("plugged_in")) if i else None,
        "charger_running": (_onoff(i.get("active")) if i else None, {"running_state": i.get("running_state")}),
        "charger_alarm": (_onoff(i.get("alarm")) if i else None, {"running_state": i.get("running_state")}),
        "vb_soc": (energy.get("window_pct"), vb_attrs),
        "vb_usable": (energy.get("available_kwh"), vb_attrs),
        "vb_max": (energy.get("window_kwh"), vb_attrs),
        "power_flow": _power_flow(state),
        "battery_rate": (state.rates or {}).get("rate_kw"),
        "battery_rate_car": (state.rates or {}).get("car_kw"),
        "battery_rate_house": (state.rates or {}).get("house_kw"),
        "capacity_estimate": st.get("capacity_kwh"),
        "battery_health": st.get("health_pct"),
        "charge_efficiency": _pct(st.get("charge_efficiency")),
        "discharge_efficiency": _pct(st.get("discharge_efficiency")),
        "conversion_loss_today": st.get("today_car_loss_kwh"),
        "round_trip_efficiency": _pct(st.get("round_trip")),
        "dropouts_today": st.get("dropouts_today"),
        "presses_today": state.presses_today,
    }


def _r2(v):
    return None if v is None else round(float(v), 2)


def _power_flow(state):
    """The power flow card's sensor: the house's use (kW), and as attributes each reading (kW:
    grid + importing / - exporting, battery and car + charging / - discharging), the SoCs and
    the kW along each line (src/flow.py)."""
    f = state.flow or {}
    if not f:
        return None
    return (_r2(f.get("house_kw")), {
        "pv_kw": _r2(f.get("pv_kw")), "grid_kw": _r2(f.get("grid_kw")), "battery_kw": _r2(f.get("battery_kw")),
        "car_kw": _r2(f.get("car_kw")), "house_kw": _r2(f.get("house_kw")),
        "home_soc": _r2(f.get("home_soc")), "car_soc": _r2((state.inputs or {}).get("soc")),
        "lines": {k: _r2(v) for k, v in (f.get("lines") or {}).items()}})


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
            attrs = {**attrs, **extra}
            sig = text + json.dumps(extra, sort_keys=True, default=str)
            last = self._sent.get(entity_id)
            if last is not None and last[0] != sig and now - last[1] < THROTTLE_S.get(entity_id, 0):
                continue  # changed, but posted only moments ago: next time
            if last is None or last[0] != sig or now - last[1] >= REFRESH_S:
                self._sent[entity_id] = (sig, now)
                out.append((entity_id, text, attrs))
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
