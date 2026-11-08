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
NOW = datetime.datetime.combine(TODAY, datetime.time(12), TZ).timestamp()  # today so far: half of it


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


def test_the_previous_days_sensor_goes_with_the_current_one():
    from src.calculator import companion_gas
    assert companion_gas("sensor.octopus_energy_gas_e6s1_398_current_accumulative_consumption_kwh") == \
        "sensor.octopus_energy_gas_e6s1_398_previous_accumulative_consumption_kwh"
    assert companion_gas("sensor.gas") is None and companion_gas(None) is None


def test_days_are_summarised_once_and_fitted(tmp_path):
    s = CalcSettings(str(tmp_path))
    s.update({"floors": [{"name": "Ground", "entity": "climate.hall", "area": 60}]})
    calc, link = Calculator(str(tmp_path), s), FakeLink()
    picked = {"gas": "sensor.gas", "outside": "sensor.outside"}
    assert calc.missing({}) == ["the gas sensor", "the outside temperature"]
    assert _run(calc.update(link, picked, today=TODAY, now=NOW)) == 11  # Nov 2 to today, so far
    r = calc.result
    assert r["ok"] and r["heat_loss_w_k"] == pytest.approx(190, abs=0.5) and r["baseline_kwh"] == pytest.approx(5, abs=0.1)
    assert r["design_kw"] == pytest.approx(190 * 24 / 1000, abs=0.02) and r["area_m2"] == 60
    asked = link.asked
    assert _run(calc.update(link, picked, today=TODAY, now=NOW)) == 0 and link.asked - asked == 2  # today's two reads
    # Today is a part-day in the fit: the same heat scaled to a whole day, counted as half a day
    today = calc.days["2026-11-12"]
    assert today["partial"] and today["covered"] == pytest.approx(0.5) and not today["complete"]
    assert today["fit_heat_kwh"] == pytest.approx(today["heat_kwh"] * 2, abs=0.01)
    assert calc.result["weight_days"] == pytest.approx(10.5, abs=0.01)
    again = Calculator(str(tmp_path), s)  # kept after a restart
    assert len(again.days) == 11 and again.result["heat_loss_w_k"] == r["heat_loss_w_k"]
    # Days stored by an older version (without the part-day fields) are read again
    for d in again.days.values():
        d.pop("fit_heat_kwh", None)
        d.pop("fit_weight", None)
        d["calc"] = 2
    again._save()
    fresh = Calculator(str(tmp_path), s)
    assert fresh.redo and _run(fresh.update(link, picked, today=TODAY, now=NOW)) == 11
    assert fresh.days["2026-11-12"]["fit_heat_kwh"] is not None
    s.update({"excluded": ["2026-11-11"]})
    assert fresh.refit()["days"] == 10
    assert fresh.day_list()[0]["date"] == "2026-11-12" and "hh" not in fresh.day_list()[0]


def test_days_from_before_the_half_hours_are_read_again(tmp_path):
    s = CalcSettings(str(tmp_path))
    calc = Calculator(str(tmp_path), s)
    assert calc.redo is False
    calc.days = {"2026-11-02": {"date": "2026-11-02", "complete": False, "why": "A gap in the gas readings"}}
    calc._save()
    assert Calculator(str(tmp_path), s).redo is True


def test_the_days_used_can_be_the_last_few_all_or_dates(tmp_path):
    s = CalcSettings(str(tmp_path))
    s.update({"floors": [{"name": "Ground", "entity": "climate.hall", "area": 60}]})
    calc = Calculator(str(tmp_path), s)
    _run(calc.update(FakeLink(), {"gas": "sensor.gas", "outside": "sensor.outside"}, today=TODAY, now=NOW))  # Nov 2 to today
    s.update({"period": "last", "window_days": 7})
    assert calc.refit()["days"] == 7 and calc.result["period"] == {
        "mode": "last", "window_days": 7, "from": "2026-11-06", "to": "2026-11-12", "days_in": 7}
    s.update({"period": "all"})
    assert calc.refit()["days"] == 11
    s.update({"period": "range", "date_from": "2026-11-04", "date_to": "2026-11-09"})
    assert calc.refit()["days"] == 6
    s.update({"date_to": None})  # open-ended
    assert calc.refit()["days"] == 9
    with pytest.raises(ValueError):
        s.update({"date_from": "2026-11-09", "date_to": "2026-11-04"})
    for bad in ({"period": "week"}, {"date_from": "soon"}):
        with pytest.raises(ValueError):
            validate(bad, s.data)


def test_a_future_day_is_left_alone(tmp_path):
    s = CalcSettings(str(tmp_path))
    s.update({"floors": [{"name": "Ground", "entity": "climate.hall", "area": 60}]})
    calc = Calculator(str(tmp_path), s)
    later = TODAY + datetime.timedelta(days=2)
    assert _run(calc.update(FakeLink(), {"gas": "sensor.gas", "outside": "sensor.outside"},
                            today=later, now=NOW)) == 9  # Nov 4 to today: the 13th and 14th aren't here yet
    assert "2026-11-13" not in calc.days and "2026-11-14" not in calc.days


def test_a_part_day_too_early_to_use_doesnt_shift_the_window(tmp_path):
    s = CalcSettings(str(tmp_path))
    s.update({"period": "last", "window_days": 7})
    calc = Calculator(str(tmp_path), s)
    calc.days = {
        "2026-11-10": {"date": "2026-11-10", "complete": True, "dt": 10.0, "heat_kwh": 50.0},
        "2026-11-11": {"date": "2026-11-11", "complete": True, "dt": 11.0, "heat_kwh": 55.0},
        "2026-11-12": {"date": "2026-11-12", "complete": False, "partial": True, "covered": 0.02,
                       "fit_heat_kwh": None, "why": "So far today"},
    }
    assert calc.period_bounds() == ("2026-11-05", "2026-11-11")
    calc.days["2026-11-12"].update({"fit_heat_kwh": 60.0, "fit_weight": 0.4, "covered": 0.4, "dt": 9.0})
    assert calc.period_bounds() == ("2026-11-06", "2026-11-12")  # now there's enough of it to use


def test_other_heating_is_checked():
    s = validate({"extra_heat": [{"name": "Loft air con", "entity": "sensor.loft_ac_energy", "cop": 3.2}]})
    assert s["extra_heat"] == [{"name": "Loft air con", "entity": "sensor.loft_ac_energy", "cop": 3.2}]
    assert validate({"extra_heat": [{"entity": "sensor.x"}]})["extra_heat"][0]["cop"] == 3.5
    for bad in ({"extra_heat": [{"entity": "climate.loft"}]}, {"extra_heat": [{"entity": "sensor.x", "cop": 20}]}, {"extra_heat": "x"}):
        with pytest.raises(ValueError):
            validate(bad)


def test_weather_compensation_curve():
    s = validate({"wc_cold_outside": -3, "wc_cold_flow": 45, "wc_warm_outside": 15, "wc_warm_flow": 25})
    assert s["wc_cold_flow"] == 45 and s["wc_warm_outside"] == 15
    partial = validate({"wc_cold_outside": -3})
    assert partial["wc_cold_outside"] == -3 and partial["wc_warm_flow"] is None
    cleared = validate({"wc_cold_outside": None, "wc_cold_flow": None, "wc_warm_outside": None, "wc_warm_flow": None}, s)
    assert cleared["wc_cold_outside"] is None
    for bad in ({"wc_cold_outside": -3, "wc_cold_flow": 45, "wc_warm_outside": -3, "wc_warm_flow": 25},
                {"wc_cold_outside": -3, "wc_cold_flow": 45, "wc_warm_outside": 15, "wc_warm_flow": 120},
                {"wc_cold_outside": -40, "wc_cold_flow": 45, "wc_warm_outside": 15, "wc_warm_flow": 25}):
        with pytest.raises(ValueError):
            validate(bad)
