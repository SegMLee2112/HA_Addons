"""Your home's settings (src/calc_settings.py) and keeping the days (src/calculator.py), with a
pretend Home Assistant."""
import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest

from src.calc_settings import CalcSettings, validate
from src.calculator import Calculator

TZ = ZoneInfo("Europe/London")
TODAY = datetime.date(2026, 11, 12)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_settings_are_checked_and_saved(tmp_path):
    s = CalcSettings(str(tmp_path))
    assert s.data["flow_fixed"] == 37.5 and s.area is None
    s.update({"floors": [{"name": "Ground", "entity": "climate.wireless_temperature_sensor_x", "area": 60},
                         {"name": "First", "entity": "climate.master", "area": 40},
                         {"name": "Loft", "entity": "climate.loft", "area": 30}],
              "surveys": [{"name": "Octopus", "kw": 5.6}]})
    assert s.area == 130
    s.update({"area_m2": 120})
    assert CalcSettings(str(tmp_path)).area == 120  # saved
    for bad in ({"floors": [{"entity": "light.x"}]}, {"flow_fixed": 200}, {"design_out": 30}, {"min_dt": "x"},
                {"excluded": ["yesterday"]}, {"window_days": 7.5}, {"nope": 1}, {"efficiency": True}):
        with pytest.raises(ValueError):
            validate(bad, s.data)


class FakeLink:
    """Home Assistant's history for 12 days: 190 W/K, 5 kWh of hot water a day."""

    def __init__(self):
        self.asked = 0

    async def query(self, payload):
        return {"time_zone": "Europe/London"}

    async def history(self, ids, start, end):
        self.asked += 1
        day = datetime.datetime.fromtimestamp(start + 6 * 3600, TZ).date()
        n = (TODAY - day).days
        outside, inside = 3 + n * 0.8, 20.0
        heat = 190 * (inside - outside) * 24 / 1000 + 5
        gas_per_hh = heat / 0.9625 / 48
        midnight = datetime.datetime.combine(day, datetime.time(), TZ).timestamp()
        iso = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()  # noqa: E731
        gas = [{"state": "30", "last_changed": iso(midnight - 900)}]
        gas += [{"state": str(gas_per_hh * (k + 1)), "last_changed": iso(midnight + (k + 1) * 1800 - 60)} for k in range(48)]
        before = iso(midnight - 3600)
        return {"sensor.gas": gas,
                "sensor.outside": [{"state": str(outside), "last_changed": before}],
                "climate.hall": [{"state": "heat", "attributes": {"current_temperature": inside},
                                  "last_changed": before, "last_updated": before}]}


def test_days_are_summarised_once_and_fitted(tmp_path):
    s = CalcSettings(str(tmp_path))
    s.update({"floors": [{"name": "Ground", "entity": "climate.hall", "area": 60}]})
    calc, link = Calculator(str(tmp_path), s), FakeLink()
    picked = {"gas": "sensor.gas", "outside": "sensor.outside"}
    assert calc.missing({}) == ["the gas sensor", "the outside temperature"]
    assert _run(calc.update(link, picked, today=TODAY)) == 10
    r = calc.result
    assert r["ok"] and r["heat_loss_w_k"] == pytest.approx(190, abs=0.5) and r["baseline_kwh"] == pytest.approx(5, abs=0.1)
    assert r["design_kw"] == pytest.approx(190 * 24 / 1000, abs=0.02) and r["area_m2"] == 60
    asked = link.asked
    assert _run(calc.update(link, picked, today=TODAY)) == 0 and link.asked == asked  # nothing new to read
    again = Calculator(str(tmp_path), s)  # kept after a restart
    assert len(again.days) == 10 and again.result["heat_loss_w_k"] == r["heat_loss_w_k"]
    calc.redo = True
    assert _run(calc.update(link, picked, today=TODAY)) == 10  # the sensors changed: again
    s.update({"excluded": ["2026-11-11"]})
    assert calc.refit()["days"] == 9
    assert calc.day_list()[0]["date"] == "2026-11-11" and "hh" not in calc.day_list()[0]
