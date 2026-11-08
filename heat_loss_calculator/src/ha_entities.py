"""The add-on's own Home Assistant entities, posted as states through the Supervisor (no
integration needed). They show as "unavailable" while the add-on is stopped, and are posted
again when it (or HA) restarts.

Kept free of I/O so it can be tested on its own; HaLink posts them."""
from __future__ import annotations

from typing import Optional

PREFIX = "heat_loss_calculator"  # entity IDs: sensor.heat_loss_calculator_...
NAME = "Heat Loss Calculator"

SENSORS = {
    "status": (f"sensor.{PREFIX}_status", {"friendly_name": f"{NAME} Status", "icon": "mdi:home-thermometer"}),
    # The heat loss: W for each °C warmer inside than out
    "heat_loss": (f"sensor.{PREFIX}_heat_loss", {
        "friendly_name": f"{NAME} Heat Loss", "unit_of_measurement": "W/K", "icon": "mdi:home-export-outline",
        "state_class": "measurement"}),
    # ...at the design temperatures: what a heat pump survey quotes
    "design_heat_loss": (f"sensor.{PREFIX}_design_heat_loss", {
        "friendly_name": f"{NAME} Design Heat Loss", "unit_of_measurement": "kW", "device_class": "power",
        "icon": "mdi:heat-pump-outline", "state_class": "measurement"}),
    # Yesterday's heat into the house (the boiler's output, from the gas)
    "heat_yesterday": (f"sensor.{PREFIX}_heat_yesterday", {
        "friendly_name": f"{NAME} Heat Yesterday", "unit_of_measurement": "kWh", "device_class": "energy",
        "icon": "mdi:fire"}),
}
ALL_SENSORS = [entity_id for entity_id, _ in SENSORS.values()]

REFRESH_S = 300  # everything posted again at least this often, even unchanged


def values(state) -> dict:
    """The value of each sensor (by its key in SENSORS) from the shared state: the value, or
    (value, extra attributes)."""
    r = state.result or {}
    ok = bool(r.get("ok"))
    fit = {"method": r.get("method"), "days_used": r.get("days"), "days_counted": r.get("weight_days"),
           "r2": r.get("r2"), "plus_minus_w_k": r.get("plus_minus_w_k"),
           "baseline_kwh_per_day": r.get("baseline_kwh"), "dt_range": r.get("dt_range")}
    latest = next((d for d in state.days or [] if d.get("complete")), None)
    return {
        "status": (state.status, {"note": state.status_note}),
        "heat_loss": (r.get("heat_loss_w_k") if ok else None,
                      {**fit, "w_k_per_m2": r.get("heat_loss_w_k_m2"), "why": r.get("why")}),
        "design_heat_loss": (r.get("design_kw") if ok else None,
                             {"plus_minus_kw": r.get("design_plus_minus_kw"), "design_inside": r.get("design_in"),
                              "design_outside": r.get("design_out"), "w_per_m2": r.get("design_w_m2"),
                              "floor_area_m2": r.get("area_m2")}),
        "heat_yesterday": ((latest or {}).get("heat_kwh"),
                           {"date": (latest or {}).get("date"), "gas_kwh": (latest or {}).get("gas_kwh"),
                            "inside": (latest or {}).get("inside"), "outside": (latest or {}).get("outside")}),
    }


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
            value, extra = value if isinstance(value, tuple) else (value, {})
            text = "unknown" if value is None else str(value)
            all_attrs = {**attrs, **{k: v for k, v in extra.items() if v is not None}}
            sig = text + repr(sorted(all_attrs.items()))
            last = self._sent.get(entity_id)
            if last is None or last[0] != sig or now - last[1] >= REFRESH_S:
                self._sent[entity_id] = (sig, now)
                out.append((entity_id, text, all_attrs))
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
        "temperature": attrs.get("current_temperature", attrs.get("temperature")),
    }
