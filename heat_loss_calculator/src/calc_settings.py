"""Your home, for the calculation: the floors and their thermostats, the flow temperature, the
design temperatures, your heat pump surveys and which days count. Set on the Settings tab,
saved in /data/calc.json. (The gas and outside temperature sensors are with the other entity
pickers, in src/ha_link.py.)"""
from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

FLOOR_DOMAINS = ("climate", "sensor", "input_number")
MAX_FLOORS = 8
MAX_SURVEYS = 6
MAX_EXTRA = 6

DEFAULTS = {
    # Each: a name, a climate entity (its current temperature) or temperature sensor, and the
    # floor area (m²) it stands for: the inside temperature is their mean weighted by area
    "floors": [],
    "flow_mode": "fixed",  # "fixed": flow_fixed; "sensor": the flow entity (src/ha_link.py)
    "flow_fixed": 37.5,  # °C (a low flow temperature, as for a heat pump)
    "efficiency": None,  # None: from the flow temperature; else a fixed 0.5..1
    # Hot water (showers, taps) isn't heat for the house: "none" leaves it in (it's about the same
    # each day, so it ends up in where the line crosses ΔT 0, not the slope: best for most);
    # "fixed" takes hot_water_kwh a day; "auto" the gas burnt while no floor's thermostat is
    # calling for heat (only if they follow the boiler closely)
    "hot_water": "none",
    "hot_water_kwh": 5.0,
    "design_in": 21.0,  # °C, as heat pump surveys use
    "design_out": -3.0,  # °C, your area's design outside temperature
    "area_m2": None,  # None: the floors' areas added up
    "surveys": [],  # [{"name", "kw"}]: heat loss from your surveys, to compare
    # Other heating (air con heat pumps, electric heaters): [{"name", "entity" (an energy sensor,
    # kWh), "cop" (heat out for each kWh in)}]
    "extra_heat": [],
    "min_dt": 5.0,  # days warmer than this inside than out count (milder ones barely heat)
    # Which days the fit uses: "last" (the last window_days), "all", or "range" (date_from to
    # date_to, either open)
    "period": "last",
    "window_days": 60,
    "date_from": None,
    "date_to": None,
    "excluded": [],  # days left out by hand (YYYY-MM-DD)
    # Weather compensation curve: a straight line between a cold and a warm outside temperature,
    # giving the flow temperature at each. All four set = curve shown; all null = off.
    "wc_cold_outside": None,  # °C, the colder (design) point
    "wc_cold_flow": None,  # °C flow temperature at wc_cold_outside
    "wc_warm_outside": None,  # °C, the milder point
    "wc_warm_flow": None,  # °C flow temperature at wc_warm_outside
}


def _num(value, name, lo, hi, allow_none=False):
    if value is None or value == "":
        if allow_none:
            return None
        raise ValueError(f"{name} is needed")
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not lo <= out <= hi:
        raise ValueError(f"{name} must be {lo:g} to {hi:g}")
    return out


def validate(raw: dict, current: Optional[dict] = None) -> dict:
    """Merge raw into current (raises ValueError)."""
    if not isinstance(raw, dict):
        raise ValueError("Send a JSON object")
    out = json.loads(json.dumps(current or DEFAULTS))
    unknown = [k for k in raw if k not in DEFAULTS]
    if unknown:
        raise ValueError("Unknown setting: " + ", ".join(unknown))
    if "floors" in raw:
        floors = raw["floors"]
        if not isinstance(floors, list) or len(floors) > MAX_FLOORS:
            raise ValueError(f"floors must be a list of at most {MAX_FLOORS}")
        out["floors"] = []
        for n, f in enumerate(floors, 1):
            if not isinstance(f, dict):
                raise ValueError(f"Floor {n} must be an object")
            entity = str(f.get("entity") or "").strip()
            if entity and entity.split(".", 1)[0] not in FLOOR_DOMAINS:
                raise ValueError(f"Floor {n}: a climate entity or a temperature sensor")
            out["floors"].append({
                "name": str(f.get("name") or "").strip()[:30] or f"Floor {n}",
                "entity": entity,
                "area": _num(f.get("area"), f"Floor {n}'s area", 0, 2000, allow_none=True),
            })
    if "flow_mode" in raw:
        if raw["flow_mode"] not in ("fixed", "sensor"):
            raise ValueError("flow_mode must be fixed or sensor")
        out["flow_mode"] = raw["flow_mode"]
    if "flow_fixed" in raw:
        out["flow_fixed"] = _num(raw["flow_fixed"], "Flow temperature", 20, 90)
    if "hot_water" in raw:
        if raw["hot_water"] not in ("auto", "fixed", "none"):
            raise ValueError("hot_water must be auto, fixed or none")
        out["hot_water"] = raw["hot_water"]
    if "hot_water_kwh" in raw:
        out["hot_water_kwh"] = _num(raw["hot_water_kwh"], "Hot water a day", 0, 100)
    if "efficiency" in raw:
        e = _num(raw["efficiency"], "Boiler efficiency", 0.5, 1.0, allow_none=True)
        out["efficiency"] = e
    for key, name, lo, hi in (("design_in", "Design inside temperature", 10, 30),
                              ("design_out", "Design outside temperature", -30, 15)):
        if key in raw:
            out[key] = _num(raw[key], name, lo, hi)
    if "area_m2" in raw:
        out["area_m2"] = _num(raw["area_m2"], "Floor area", 1, 5000, allow_none=True)
    if "surveys" in raw:
        surveys = raw["surveys"]
        if not isinstance(surveys, list) or len(surveys) > MAX_SURVEYS:
            raise ValueError(f"surveys must be a list of at most {MAX_SURVEYS}")
        out["surveys"] = [{"name": str(s.get("name") or "").strip()[:30] or f"Survey {n}",
                           "kw": _num(s.get("kw"), f"Survey {n}'s heat loss", 0, 100)}
                          for n, s in enumerate(surveys, 1) if isinstance(s, dict)]
    if "extra_heat" in raw:
        extra = raw["extra_heat"]
        if not isinstance(extra, list) or len(extra) > MAX_EXTRA:
            raise ValueError(f"extra_heat must be a list of at most {MAX_EXTRA}")
        out["extra_heat"] = []
        for n, x in enumerate(extra, 1):
            if not isinstance(x, dict):
                raise ValueError(f"Heating {n} must be an object")
            entity = str(x.get("entity") or "").strip()
            if entity and entity.split(".", 1)[0] not in ("sensor", "input_number"):
                raise ValueError(f"Heating {n}: an energy sensor (kWh)")
            out["extra_heat"].append({"name": str(x.get("name") or "").strip()[:30] or f"Heating {n}", "entity": entity,
                                      "cop": _num(x.get("cop", 3.5), f"Heating {n}'s COP", 0.5, 8)})
    if "min_dt" in raw:
        out["min_dt"] = _num(raw["min_dt"], "Smallest ΔT", 0, 20)
    if "window_days" in raw:
        days = _num(raw["window_days"], "Days to use", 7, 400)
        if days != int(days):
            raise ValueError("Days to use must be a whole number")
        out["window_days"] = int(days)
    if "period" in raw:
        if raw["period"] not in ("last", "all", "range"):
            raise ValueError("period must be last, all or range")
        out["period"] = raw["period"]
    for key, name in (("date_from", "From"), ("date_to", "To")):
        if key in raw:
            value = raw[key]
            if value in (None, ""):
                out[key] = None
                continue
            try:
                out[key] = datetime.date.fromisoformat(str(value)).isoformat()
            except ValueError:
                raise ValueError(f"{name} isn't a date (YYYY-MM-DD)") from None
    if out["date_from"] and out["date_to"] and out["date_from"] > out["date_to"]:
        raise ValueError("From must be on or before To")
    if "excluded" in raw:
        ex = raw["excluded"]
        if not isinstance(ex, list):
            raise ValueError("excluded must be a list of dates")
        clean = set()
        for d in ex:
            try:
                clean.add(datetime.date.fromisoformat(str(d)).isoformat())
            except ValueError:
                raise ValueError(f"{d} isn't a date (YYYY-MM-DD)") from None
        out["excluded"] = sorted(clean)
    if out["design_in"] <= out["design_out"]:
        raise ValueError("The design inside temperature must be above the outside one")
    for key, name, lo, hi in (("wc_cold_outside", "Weather compensation cold outside temperature", -30, 30),
                              ("wc_cold_flow", "Weather compensation cold flow temperature", 20, 90),
                              ("wc_warm_outside", "Weather compensation warm outside temperature", -30, 30),
                              ("wc_warm_flow", "Weather compensation warm flow temperature", 20, 90)):
        if key in raw:
            out[key] = _num(raw[key], name, lo, hi, allow_none=True)
    if out["wc_cold_outside"] is not None and out["wc_warm_outside"] is not None \
            and out["wc_cold_outside"] == out["wc_warm_outside"]:
        raise ValueError("The weather compensation outside temperatures must be different")
    return out


class CalcSettings:
    def __init__(self, data_dir: Optional[str] = None, on_change: Optional[Callable[[dict], None]] = None) -> None:
        self._path = os.path.join(data_dir, "calc.json") if data_dir else None
        self.on_change = on_change  # called with what was sent
        self.data = json.loads(json.dumps(DEFAULTS))
        saved = self._load()
        if saved:
            try:
                self.data = validate({k: v for k, v in saved.items() if k in DEFAULTS})
            except ValueError as err:
                logger.warning("Calculation settings: %s; using the defaults", err)

    @property
    def area(self) -> Optional[float]:
        """The floor area: set, or the floors' added up."""
        if self.data.get("area_m2"):
            return self.data["area_m2"]
        total = sum(f.get("area") or 0 for f in self.data["floors"])
        return total or None

    def update(self, raw) -> dict:
        self.data = validate(raw, self.data)
        self._save()
        if self.on_change is not None:
            self.on_change(raw)
        return self.data

    def _load(self) -> dict:
        if not self._path:
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            logger.warning("Calculation settings %s are unreadable: using the defaults", self._path)
            return {}

    def _save(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self._path)
        except Exception:
            logger.warning("Could not save the calculation settings", exc_info=True)
