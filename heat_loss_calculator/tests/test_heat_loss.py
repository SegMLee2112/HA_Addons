"""The heat loss calculation (src/heat_loss.py), from made-up history."""
import datetime
from zoneinfo import ZoneInfo

import pytest

from src.heat_loss import (efficiency, fit, gas_deltas, results, series, step_mean, summarise_day,
                           temperature_attribute, value_of)

TZ = ZoneInfo("Europe/London")
DAY = datetime.date(2026, 11, 2)


def _ts(day, h, m=0):
    return datetime.datetime.combine(day, datetime.time(h, m), TZ).timestamp()


def _gas_day(day, per_hh, start_value=12.0):
    """Octopus-like readings: every half hour, today's total so far (resets at midnight)."""
    pts = [(_ts(day, 23, 45) - 86400, start_value)]  # the evening before
    total = 0.0
    for n in range(48):
        total += per_hh(n)
        pts.append((_ts(day, 0) + (n + 1) * 1800 - 60, total))  # a minute before each half hour ends
    return pts


def _flat(day, value):
    return [(_ts(day, 0) - 3600, value)]


def test_readings():
    assert value_of({"state": "20.5"}) == 20.5
    assert value_of({"state": "unavailable"}) is None
    assert value_of({"state": "heat", "attributes": {"current_temperature": 19.4}}, "current_temperature") == 19.4
    assert temperature_attribute("climate.loft") == "current_temperature"
    assert temperature_attribute("weather.home") == "temperature"
    assert temperature_attribute("sensor.outside") is None
    rows = [{"state": "2", "last_changed": "2026-11-02T10:00:00+00:00"},
            {"state": "x", "last_changed": "2026-11-02T09:00:00+00:00"},
            {"state": "1", "last_changed": "2026-11-02T08:00:00Z"}]
    assert [v for _, v in series(rows)] == [1.0, 2.0]


def test_gas_resets_at_midnight():
    pts = [(0, 30.0), (1, 31.5), (2, 0.4), (3, 1.0), (4, 1.0)]
    assert gas_deltas(pts) == [(1, 1.5), (2, 0.4), (3, pytest.approx(0.6)), (4, 0.0)]


def test_step_mean_is_time_weighted():
    # 10 for the first hour, 20 for the second
    mean, cover = step_mean([(0, 10.0), (3600, 20.0)], 0, 7200)
    assert mean == 15 and cover == 1
    mean, cover = step_mean([(3600, 20.0)], 0, 7200)  # nothing before the first reading
    assert mean == 20 and cover == 0.5
    mean, cover = step_mean([(0, 10.0), (1800, None), (5400, 30.0)], 0, 7200)  # unavailable for an hour
    assert mean == 20 and cover == 0.5


def test_efficiency_follows_the_flow_temperature():
    assert efficiency(35) == pytest.approx(0.97)
    assert efficiency(37.5) == pytest.approx(0.9625)
    assert efficiency(70) == pytest.approx(0.865)
    assert efficiency(120) == 0.80
    assert efficiency(60, fixed=0.9) == 0.9


def test_a_day_is_summarised():
    gas = _gas_day(DAY, lambda n: 1.0 if 12 <= n < 18 or 32 <= n < 44 else 0.0)  # 18 kWh
    floors = [(_flat(DAY, 20.0), 60, "Ground"), (_flat(DAY, 19.0), 40, "First"), (_flat(DAY, 18.0), 30, "Loft")]
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5)
    assert d["complete"] and d["why"] is None
    assert d["gas_kwh"] == 18.0 and d["heat_kwh"] == pytest.approx(18 * 0.9625, abs=0.01)
    assert d["inside"] == pytest.approx((20 * 60 + 19 * 40 + 18 * 30) / 130, abs=0.01)  # by floor area
    assert d["dt"] == pytest.approx(d["inside"] - 5, abs=0.01)
    assert len(d["hh"]) == 48 and sum(h["gas"] for h in d["hh"]) == pytest.approx(18)
    assert [f["name"] for f in d["floors"]] == ["Ground", "First", "Loft"]


def test_the_clocks_going_back_gives_50_half_hours():
    day = datetime.date(2026, 10, 25)
    d = summarise_day(day, TZ, gas=[], outside=_flat(day, 5.0), floors=[(_flat(day, 20.0), 1, "x")], flow_fixed=40)
    assert len(d["hh"]) == 50


def test_incomplete_days_say_why():
    floors = [(_flat(DAY, 20.0), 1, "x")]
    gas = _gas_day(DAY, lambda n: 0.5)
    gap = [p for p in gas if not (_ts(DAY, 8) < p[0] < _ts(DAY, 12))]
    assert summarise_day(DAY, TZ, gas=gap, outside=_flat(DAY, 5.0), floors=floors)["why"] == "A gap in the gas readings"
    assert summarise_day(DAY, TZ, gas=[], outside=_flat(DAY, 5.0), floors=floors)["why"] == "No gas readings"
    assert summarise_day(DAY, TZ, gas=gas, outside=[], floors=floors)["why"] == "Outside temperature missing"
    assert summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=[])["why"] == "No inside temperature"


def test_the_flow_sensor_sets_the_efficiency_while_burning():
    gas = _gas_day(DAY, lambda n: 1.0 if n >= 24 else 0.0)
    flow = [(_ts(DAY, 0) - 60, 25.0), (_ts(DAY, 12), 70.0)]  # cold overnight, 70 °C while burning
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=[(_flat(DAY, 20.0), 1, "x")], flow=flow)
    assert d["flow"] == 70.0 and d["efficiency"] == pytest.approx(0.865, abs=0.001)


def _day(date, dt, heat, complete=True):
    return {"date": date, "dt": dt, "heat_kwh": heat, "complete": complete}


def test_the_fit_finds_the_heat_loss():
    # 200 W/K: 4.8 kWh a day per °C, with 5 kWh of hot water less free heat
    days = [_day(f"2026-11-{n:02d}", dt, 4.8 * dt + 5) for n, dt in enumerate([6, 8, 10, 12, 14, 16], 1)]
    f = fit(days)
    assert f["ok"] and f["heat_loss_w_k"] == 200.0 and f["baseline_kwh"] == 5.0 and f["r2"] == 1.0
    r = results(days, min_dt=5, excluded=[], design_in=21, design_out=-3, area_m2=130)
    assert r["design_kw"] == 4.8 and r["design_w_m2"] == pytest.approx(36.9, abs=0.1)


def test_the_fit_leaves_days_out():
    days = [_day(f"2026-11-{n:02d}", dt, 4.8 * dt + 5) for n, dt in enumerate([6, 8, 10, 12, 14, 16], 1)]
    days += [_day("2026-11-07", 2, 99), _day("2026-11-08", 9, 99, complete=False), _day("2026-11-09", 10, 99)]
    f = fit(days, min_dt=5, excluded=["2026-11-09"])
    assert f["heat_loss_w_k"] == 200.0 and f["days"] == 6


def test_too_few_days_or_too_little_spread():
    assert "Needs 5 days" in fit([_day("2026-11-01", 10, 50)])["why"]
    same = [_day(f"2026-11-{n:02d}", 10 + n * 0.1, 50) for n in range(1, 7)]
    assert "more different temperatures" in fit(same)["why"]
