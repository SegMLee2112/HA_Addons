"""Keeps the days and the result up to date: each complete day is summarised once from Home
Assistant's history (src/heat_loss.py) and kept in /data/days.json, so the add-on isn't
limited to the history Home Assistant keeps (10 days by default). Changing the sensors, floors
or flow temperature summarises again the days Home Assistant still has the history for."""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
from typing import Optional
from zoneinfo import ZoneInfo

from src import heat_loss

logger = logging.getLogger(__name__)

HISTORY_DAYS = 10  # how far back to look in Home Assistant's history (its recorder's default)
RETRY_DAYS = 3  # an incomplete day is tried again for this long (e.g. a reading came late)
KEEP_HALF_HOURS_DAYS = 45  # days keep their half hours this long (for the day chart), then just the totals
KEEP_DAYS = 800
BEFORE_S = 6 * 3600  # history from this long before the day: the last readings before midnight
GAS_AFTER_S = 48 * 3600  # ...and the gas's until this long after it: half hours that arrive late


def companion_gas(entity_id: Optional[str]) -> Optional[str]:
    """Octopus Energy's other accumulative gas sensor: the previous day's for the current day's.
    It has each whole day's half hours the next day, including any the current one missed."""
    if entity_id and entity_id.endswith("_current_accumulative_consumption_kwh"):
        return entity_id[: -len("_current_accumulative_consumption_kwh")] + "_previous_accumulative_consumption_kwh"
    return None


class Calculator:
    def __init__(self, data_dir: Optional[str], settings) -> None:
        self._path = os.path.join(data_dir, "days.json") if data_dir else None
        self.settings = settings  # src.calc_settings.CalcSettings
        self.days: dict[str, dict] = self._load()
        self.result: dict = {}
        # The sensors changed: summarise the days again. Also days read before 0.1.3, when the
        # gas was only taken from the readings (not each half hour's own figure)
        self.redo = any("gas_from" not in d or "dhw_from" not in d for d in self.days.values())
        self.tz = None
        self.last_run: Optional[float] = None
        self.error: Optional[str] = None
        self.refit()

    # --- what it reads -------------------------------------------------------------

    def entities(self, picked: dict) -> dict:
        """{"gas", "outside", "flow" (None when fixed), "floors": [(entity, area, name)]}."""
        s = self.settings.data
        return {
            "gas": picked.get("gas") or None,
            "outside": picked.get("outside") or None,
            "flow": (picked.get("flow") or None) if s["flow_mode"] == "sensor" else None,
            "floors": [(f["entity"], f.get("area"), f["name"]) for f in s["floors"] if f.get("entity")],
        }

    def missing(self, picked: dict) -> list[str]:
        e = self.entities(picked)
        out = []
        if not e["gas"]:
            out.append("the gas sensor")
        if not e["outside"]:
            out.append("the outside temperature")
        if not e["floors"]:
            out.append("an inside thermostat")
        if self.settings.data["flow_mode"] == "sensor" and not e["flow"]:
            out.append("the flow temperature sensor")
        return out

    # --- updating ------------------------------------------------------------------

    async def update(self, link, picked: dict, today: Optional[datetime.date] = None) -> int:
        """Summarise the days still to do from Home Assistant's history. Returns how many."""
        if self.missing(picked):
            self.refit()
            return 0
        if self.tz is None:
            config = await link.query({"type": "get_config"})
            self.tz = ZoneInfo((config or {}).get("time_zone") or "UTC")
        today = today or datetime.datetime.now(self.tz).date()
        redo, self.redo = self.redo, False
        done = 0
        for back in range(HISTORY_DAYS, 0, -1):
            day = today - datetime.timedelta(days=back)
            key = day.isoformat()
            have = self.days.get(key)
            if have and not redo and (have.get("complete") or back > RETRY_DAYS):
                continue
            summary = await self._summarise(link, picked, day)
            if summary is None:
                continue
            if summary["why"] == "No gas readings" and not have:
                continue  # before the history starts (or the meter wasn't there yet)
            self.days[key] = summary
            done += 1
        self._trim(today)
        if done:
            self._save()
        self.refit()
        self.last_run, self.error = time.time(), None
        return done

    async def _summarise(self, link, picked: dict, day: datetime.date) -> Optional[dict]:
        e = self.entities(picked)
        start, end = heat_loss.day_bounds(day, self.tz)
        ids = [e["outside"]] + ([e["flow"]] if e["flow"] else []) + [f[0] for f in e["floors"]]
        rows = await link.history(sorted(set(ids)), start - BEFORE_S, end + 60)
        gas_ids = [e["gas"]] + ([companion_gas(e["gas"])] if companion_gas(e["gas"]) else [])
        gas_rows = await link.history(gas_ids, start - BEFORE_S, min(end + GAS_AFTER_S, time.time()))
        charges = {}
        for entity in reversed(gas_ids):  # the sensor picked wins where both have a half hour
            charges.update(heat_loss.gas_charges(gas_rows.get(entity, [])))
        temp = lambda entity: heat_loss.series(rows.get(entity, []), heat_loss.temperature_attribute(entity),  # noqa: E731
                                               gaps=True)
        s = self.settings.data
        return heat_loss.summarise_day(
            day, self.tz,
            gas=heat_loss.series([r for r in gas_rows.get(e["gas"], []) if heat_loss.parse_ts(r.get("last_changed")) is None
                                  or heat_loss.parse_ts(r.get("last_changed")) <= end + 3600]),
            charges=charges,
            outside=temp(e["outside"]),
            floors=[(temp(entity), area, name) for entity, area, name in e["floors"]],
            flow=temp(e["flow"]) if e["flow"] else None,
            flow_fixed=s["flow_fixed"], efficiency_fixed=s["efficiency"],
            hot_water=s["hot_water"], hot_water_kwh=s["hot_water_kwh"],
            calling=[heat_loss.heating_series(rows.get(entity, [])) for entity, _, _ in e["floors"]
                     if entity.startswith("climate.")])

    def refit(self) -> dict:
        s = self.settings.data
        recent = self.recent_days()
        self.result = heat_loss.results(
            recent, min_dt=s["min_dt"], excluded=s["excluded"], design_in=s["design_in"],
            design_out=s["design_out"], area_m2=self.settings.area)
        first, last = self.period_bounds()
        self.result["period"] = {"mode": s["period"], "window_days": s["window_days"], "from": first, "to": last,
                                 "days_in": len(recent)}
        return self.result

    def period_bounds(self) -> tuple[Optional[str], Optional[str]]:
        """The first and last day the fit may use (YYYY-MM-DD; None: open): the last
        window_days (up to the latest day read), every day, or the dates set."""
        s = self.settings.data
        if s["period"] == "all":
            return None, None
        if s["period"] == "range":
            return s["date_from"], s["date_to"]
        keys = sorted(self.days)
        if not keys:
            return None, None
        last = datetime.date.fromisoformat(keys[-1])
        return (last - datetime.timedelta(days=s["window_days"] - 1)).isoformat(), keys[-1]

    def recent_days(self) -> list[dict]:
        """The days in the period, oldest first."""
        first, last = self.period_bounds()
        return [self.days[k] for k in sorted(self.days)
                if (first is None or k >= first) and (last is None or k <= last)]

    def day_list(self) -> list[dict]:
        """Every day without its half hours (for the page), newest first."""
        return [{k: v for k, v in self.days[key].items() if k != "hh"} for key in sorted(self.days, reverse=True)]

    def forget(self) -> None:
        """Start again (e.g. a new gas meter): forget every day."""
        self.days = {}
        self.redo = True
        self._save()
        self.refit()

    def _trim(self, today: datetime.date) -> None:
        hh_from = (today - datetime.timedelta(days=KEEP_HALF_HOURS_DAYS)).isoformat()
        for key in sorted(self.days)[:-KEEP_DAYS]:
            del self.days[key]
        for key, day in self.days.items():
            if key < hh_from:
                day.pop("hh", None)

    # --- saving --------------------------------------------------------------------

    def _load(self) -> dict:
        if not self._path:
            return {}
        for path in (self._path, self._path + ".bak"):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except FileNotFoundError:
                continue
            except Exception:
                logger.warning("Days %s are unreadable", path)
        return {}

    def _save(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.days, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(self._path):
                os.replace(self._path, self._path + ".bak")
            os.replace(tmp, self._path)
        except Exception:
            logger.warning("Could not save the days", exc_info=True)
