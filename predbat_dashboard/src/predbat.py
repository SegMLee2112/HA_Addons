"""Predbat's entities, read into a small, clean set of figures for the cards.

Predbat (https://springfall2008.github.io/batpred/) publishes its results as
Home Assistant entities named after its prefix ("predbat" unless changed):
predbat.status, predbat.soc_kw_h0, predbat.cost_today, predbat.rates,
predbat.plan_html (whose "raw" attribute is the whole plan, slot by slot),
select.predbat_mode, select.predbat_manual_charge and so on.

data(states, prefix, now) turns them into the figures each card shows. It's
pure (no I/O), so it can be tested on its own. Everything is kept in
Predbat's units (kWh, kW, pence) except money, which is in pounds (or whatever
its currency symbols say).
"""
from __future__ import annotations

import datetime
import re
from typing import Optional

PLAN_SLOTS = 96  # the plan's next 48 hours (half-hour slots)
MANUAL = {  # the manual override selects: key -> (entity suffix, name)
    "charge": ("manual_charge", "Force charge"),
    "export": ("manual_export", "Force export"),
    "freeze_charge": ("manual_freeze_charge", "Freeze charge"),
    "freeze_export": ("manual_freeze_export", "Freeze export"),
    "demand": ("manual_demand", "Demand (no charge or export)"),
}
MODES = ("Monitor", "Control SoC only", "Control charge", "Control charge & discharge")
CHARGE_STATES = ("Chrg",)
HOLD_STATES = ("FrzChrg", "HoldChrg", "FrzExp", "HoldExp")  # the battery held (no charge, no discharge)
# The plan's states in plain words (Predbat's state_text has symbols and HTML in it)
STATE_WORDS = {"Chrg": "Charge", "HoldChrg": "Hold charge", "FrzChrg": "Freeze (battery held)",
               "Exp": "Export", "HoldExp": "Hold export", "FrzExp": "Freeze (solar exported)",
               "Demand": "Normal", "": "Normal"}
EXPORT_STATES = ("Exp",)


def num(value) -> Optional[float]:
    """A number from a state or attribute ("12.3", 12.3, "12.3 kWh"), or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None


def _r(v: Optional[float], n: int = 2) -> Optional[float]:
    return None if v is None else round(v, n)


def entity_ids(prefix: str, entity: str) -> list[str]:
    """Where Predbat may have put an entity: its own domain (predbat.x) or a platform one
    (sensor.predbat_x, select.predbat_x...)."""
    return [f"{prefix}.{entity}", f"sensor.{prefix}_{entity}", f"binary_sensor.{prefix}_{entity}",
            f"select.{prefix}_{entity}", f"switch.{prefix}_{entity}", f"input_number.{prefix}_{entity}",
            f"input_select.{prefix}_{entity}"]


def is_predbat(entity_id: str, prefix: str) -> bool:
    """One of Predbat's entities (for the subscription)."""
    domain, _, obj = (entity_id or "").partition(".")
    return domain == prefix or (domain in ("sensor", "binary_sensor", "select", "switch", "input_number", "input_select")
                                and obj.startswith(prefix + "_"))


class Reader:
    def __init__(self, states: dict, prefix: str = "predbat") -> None:
        self.states, self.prefix = states, prefix

    def eid(self, entity: str) -> Optional[str]:
        """The entity's ID, wherever Predbat put it."""
        for eid in entity_ids(self.prefix, entity):
            if eid in self.states:
                return eid
        return None

    def st(self, entity: str) -> Optional[dict]:
        eid = self.eid(entity)
        return None if eid is None else self.states[eid]

    def state(self, entity: str):
        s = self.st(entity)
        v = (s or {}).get("state")
        return None if v in (None, "unknown", "unavailable", "") else v

    def n(self, entity: str) -> Optional[float]:
        return num(self.state(entity))

    def attr(self, entity: str, name: str, default=None):
        return ((self.st(entity) or {}).get("attributes") or {}).get(name, default)

    def on(self, entity: str) -> Optional[bool]:
        v = self.state(entity)
        return None if v is None else str(v).lower() == "on"


def _time(value) -> Optional[str]:
    """A Predbat time ("2026-10-12T23:30:00+0100", "23:30:00") as ISO, or None."""
    if not value or value in ("unknown", "unavailable", "undefined"):
        return None
    text = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.datetime.strptime(text, fmt).isoformat()
        except ValueError:
            pass
    try:
        return datetime.datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text if re.match(r"^\d\d:\d\d", text) else None


def _window(r: Reader, start: str, end: str, value: Optional[str] = None, value_name: str = "value") -> Optional[dict]:
    s, e = _time(r.state(start)), _time(r.state(end))
    if not s:
        return None
    out = {"start": s, "end": e}
    if value:
        out[value_name] = r.n(value)
    return out


def manual_options(st: Optional[dict]) -> dict:
    """A manual override select. Predbat lists "off", then every slot from the one now on
    ("Mon 22:00", "[Mon 22:30]" when chosen), then the current value ("+Mon 22:30,Tue 01:00")."""
    attrs = (st or {}).get("attributes") or {}
    options = [str(o) for o in attrs.get("options") or []]
    slots = [o for o in options if o != "off" and not o.startswith("+")]
    chosen = [o.strip("[]") for o in slots if o.startswith("[")]
    return {"chosen": chosen, "slots": [o.strip("[]") for o in slots], "options": options, "available": st is not None}


def slot_option(options: list[str]) -> Optional[str]:
    """The option for the slot now: Predbat lists the slots from now on, so the first one
    (in [brackets] if it's already chosen)."""
    for o in options:
        if o != "off" and not o.startswith("+"):
            return o
    return None


def plan_rows(raw, now: datetime.datetime) -> list[dict]:
    """plan_html's raw rows from the slot now on: time, state, SoC %, import/export rate,
    PV and load (kWh), cost so far (£) and car charging (kWh)."""
    if not isinstance(raw, dict):
        return []
    out = []
    for row in raw.get("rows") or []:
        if not isinstance(row, dict):
            continue
        try:
            t = datetime.datetime.strptime(str(row.get("time")), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        if t <= now - datetime.timedelta(minutes=30):
            continue  # over (a slot is half an hour)
        state = str(row.get("state") or "")
        out.append({
            "t": t.isoformat(), "state": state,
            "kind": "charge" if state in CHARGE_STATES else "export" if state in EXPORT_STATES
            else "hold" if state in HOLD_STATES else "demand" if state == "Demand" else "",
            "label": STATE_WORDS.get(state, state),
            "soc": num(row.get("soc_percent")), "import": num(row.get("import_rate")),
            "export": num(row.get("export_rate")), "pv": num(row.get("pv_forecast")),
            "load": num(row.get("load_forecast")), "cost": num(row.get("total_cost")),
            "car": num(row.get("car_charging")),
        })
        if len(out) >= PLAN_SLOTS:
            break
    return out


def _manual(st: Optional[dict], name: str) -> dict:
    m = manual_options(st)
    now = slot_option(m["options"])
    m.pop("options")
    m["slots"] = m["slots"][:48]  # the next day is plenty to pick from
    return {**m, "name": name, "now": None if now is None else now.strip("[]"),
            "now_on": bool(now and now.startswith("["))}


def data(states: dict, prefix: str = "predbat", now: Optional[datetime.datetime] = None) -> dict:
    """Everything the cards show, from Home Assistant's states ({entity_id: {"state", "attributes"}})."""
    now = now or datetime.datetime.now().astimezone()
    r = Reader(states, prefix)
    found = any(is_predbat(e, prefix) for e in states)
    cfg = (r.st("inverter_config") or {}).get("attributes") or {}
    soc_kwh = r.n("soc_kw_h0")
    soc_max = num(cfg.get("soc_max"))
    raw = r.attr("plan_html", "raw")
    if isinstance(raw, dict) and soc_max is None:
        soc_max = num(raw.get("soc_max"))
    rows = plan_rows(raw, now)
    soc_pct = (soc_kwh / soc_max * 100) if soc_kwh is not None and soc_max else (rows[0]["soc"] if rows else None)
    currency = (raw or {}).get("currency_symbols") if isinstance(raw, dict) else None
    status = r.state("status")
    cost_p = lambda e: _r(r.n(e) / 100, 2) if r.n(e) is not None else None  # noqa: E731  pence -> pounds
    mode_st = r.st("mode")
    return {
        "found": found, "prefix": prefix,
        "currency": (currency or ["£", "p"]),
        "status": {"text": status, "error": bool(r.attr("status", "error")) or "error" in str(status or "").lower(),
                   "version": r.attr("status", "version"), "updated": r.attr("status", "last_updated"),
                   "healthy": r.on("components_healthy")},
        "mode": {"value": r.state("mode"),
                 "options": [str(o) for o in ((mode_st or {}).get("attributes") or {}).get("options") or MODES],
                 "available": mode_st is not None},
        "battery": {"kwh": _r(soc_kwh), "pct": _r(soc_pct, 0), "max_kwh": _r(soc_max),
                    "reserve_kwh": _r(num(cfg.get("reserve"))), "hours_left": _r(r.n("best_battery_hours_left"), 1),
                    "kw": _r(r.n("battery_power"), 2), "charging": r.on("charging"), "exporting": r.on("exporting")},
        # kW, as Predbat has them: battery + discharging / - charging, grid + exporting / - importing
        "power": {"load_kw": _r(r.n("load_power"), 2), "pv_kw": _r(r.n("pv_power"), 2), "grid_kw": _r(r.n("grid_power"), 2),
                  "battery_kw": _r(r.n("battery_power"), 2), "car_kw": _r(r.n("car_charging_power"), 2)},
        "cost": {"today": cost_p("cost_today"), "today_import": cost_p("cost_today_import"),
                 "today_export": cost_p("cost_today_export"), "ppkwh_today": _r(r.n("ppkwh_today")),
                 "yesterday": cost_p("cost_yesterday"), "saving_yesterday": cost_p("savings_yesterday_predbat"),
                 "saving_total": cost_p("savings_total_predbat"), "saving_pvbat_yesterday": cost_p("savings_yesterday_pvbat")},
        "rates": {"import": _r(r.n("rates")), "export": _r(r.n("rates_export")),
                  "import_min": _r(num(r.attr("rates", "min"))), "import_max": _r(num(r.attr("rates", "max"))),
                  "low_slot": r.on("low_rate_slot"), "high_export_slot": r.on("high_rate_export_slot")},
        "next_cheap": _window(r, "low_rate_start", "low_rate_end", "low_rate_cost", "rate"),
        "next_export_rate": _window(r, "high_rate_export_start", "high_rate_export_end", "high_rate_export_cost", "rate"),
        "next_charge": _window(r, "best_charge_start", "best_charge_end", "best_charge_limit", "limit"),
        "next_export": _window(r, "best_export_start", "best_export_end", "best_export_limit", "limit"),
        "pv": {"today": _r(r.n("pv_today")), "today_left": _r(num(r.attr("pv_today", "remaining"))),
               "tomorrow": _r(r.n("pv_tomorrow")), "now_kw": _r(r.n("pv_forecast_h0"))},
        "energy_today": {"import": _r(r.n("import_energy_h0")), "export": _r(r.n("export_energy_h0")),
                         "load": _r(r.n("load_energy_h0")), "pv": _r(r.n("pv_energy_h0"))},
        "carbon": {"now": _r(r.n("carbon_now"), 0), "today_kg": _r(r.n("carbon_today") / 1000, 1) if r.n("carbon_today") is not None else None},
        "car": {"slot": r.on("car_charging_slot"), "start": r.state("car_charging_start"),
                "soc_best": _r(r.n("car_soc_best"), 0), "cost_today": cost_p("cost_today_car")},
        "manual": {k: _manual(r.st(suffix), name) for k, (suffix, name) in MANUAL.items()},
        "plan": rows,
        "end_soc": rows[-1]["soc"] if rows else None,
    }
