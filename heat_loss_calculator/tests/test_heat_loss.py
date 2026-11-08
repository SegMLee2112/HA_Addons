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


def test_today_so_far_is_scaled_to_a_whole_day():
    gas = _gas_day(DAY, lambda n: 1.0)  # 24 kWh by noon
    floors = [(_flat(DAY, 20.0), 1, "x")]
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      now=_ts(DAY, 12))
    assert d["partial"] and not d["complete"] and d["why"] == "So far today" and d["why_data"] is None
    assert d["covered"] == pytest.approx(0.5) and d["fit_weight"] == pytest.approx(0.5)
    assert d["gas_kwh"] == pytest.approx(24) and d["heat_kwh"] == pytest.approx(24 * 0.9625, abs=0.01)
    assert d["fit_heat_kwh"] == pytest.approx(d["heat_kwh"] / 0.5, abs=0.01)  # a whole day's worth
    # Past the end of the day it is a whole day again
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      now=_ts(DAY + datetime.timedelta(days=1), 0))
    assert d["complete"] and not d["partial"] and d["why"] is None and d["fit_heat_kwh"] == d["heat_kwh"]


def test_a_sliver_of_today_is_shown_but_not_used():
    # 20 minutes of the day: too little to stand for a whole day
    gas = [(_ts(DAY, 0) - 1800, 0.0), (_ts(DAY, 0, 10), 1.0)]
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=[(_flat(DAY, 20.0), 1, "x")],
                      flow_fixed=37.5, now=_ts(DAY, 0, 20))
    assert d["partial"] and d["covered"] < 0.1 and d["why"] == "So far today"
    assert d["fit_heat_kwh"] is None and d["fit_weight"] is None


def test_a_missing_half_hour_is_filled_from_readings_either_side():
    from src.heat_loss import gas_charges
    floors = [(_flat(DAY, 20.0), 1, "x")]
    rows = _charges_rows(DAY, lambda k: 1.0, arrives=(10, 20, 26))  # every half hour arrives
    charges = gas_charges(rows)
    charges.pop(int(_ts(DAY, 10)))  # one half hour's figure never came
    gas = _gas_day(DAY, lambda k: 1.0)  # the readings every half hour bracket it
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      charges=charges)
    assert d["complete"] and d["gas_from"] == "half hours + readings" and d["gas_kwh"] == 48.0
    # Readings hours apart can't say what happened in that half hour: left as missing
    sparse = [(_ts(DAY, 0) - 3600, 0.0), (_ts(DAY, 9), 9.0), (_ts(DAY, 15), 15.0)]
    d = summarise_day(DAY, TZ, gas=sparse, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      charges=charges)
    assert not d["complete"] and d["why"].startswith("Gas missing for 1 half hour")
    assert d["gas_kwh"] == 47.0


def _day(date, dt, heat, complete=True):
    return {"date": date, "dt": dt, "heat_kwh": heat, "complete": complete}


def test_the_fit_finds_the_heat_loss():
    # 200 W/K: 4.8 kWh a day per °C, with 5 kWh of hot water less free heat
    days = [_day(f"2026-11-{n:02d}", dt, 4.8 * dt + 5) for n, dt in enumerate([6, 8, 10, 12, 14, 16], 1)]
    f = fit(days)
    assert f["ok"] and f["heat_loss_w_k"] == 200.0 and f["baseline_kwh"] == 5.0 and f["r2"] == 1.0
    r = results(days, min_dt=5, excluded=[], design_in=21, design_out=-3, area_m2=130)
    assert r["design_kw"] == 4.8 and r["design_w_m2"] == pytest.approx(36.9, abs=0.1)


def test_a_part_day_weighs_into_the_fit():
    days = [_day(f"2026-11-{n:02d}", dt, 4.8 * dt + 5) for n, dt in enumerate([6, 8, 10, 12, 14, 16], 1)]
    full = 4.8 * 10 + 5
    days.append({"date": "2026-11-07", "dt": 10.0, "heat_kwh": full / 2, "complete": False, "partial": True,
                 "fit_heat_kwh": full, "fit_weight": 0.5, "why": "So far today"})
    f = fit(days)
    assert f["days"] == 7 and f["weight_days"] == pytest.approx(6.5)
    assert f["heat_loss_w_k"] == 200.0 and f["baseline_kwh"] == 5.0
    # Without the part-day fields an incomplete day is left out (as days stored by older versions are)
    days[-1].pop("fit_heat_kwh")
    days[-1].pop("fit_weight")
    assert fit(days)["days"] == 6


def test_the_fit_leaves_days_out():
    days = [_day(f"2026-11-{n:02d}", dt, 4.8 * dt + 5) for n, dt in enumerate([6, 8, 10, 12, 14, 16], 1)]
    days += [_day("2026-11-07", 2, 99), _day("2026-11-08", 9, 99, complete=False), _day("2026-11-09", 10, 99)]
    f = fit(days, min_dt=5, excluded=["2026-11-09"])
    assert f["heat_loss_w_k"] == 200.0 and f["days"] == 6


def test_too_few_days_or_too_little_spread_give_an_estimate():
    one = fit([_day("2026-11-01", 10, 48)])  # 48 kWh at ΔT 10: 4.8 kWh a day per °C, 200 W/K
    assert one["ok"] and one["method"] == "estimate" and one["heat_loss_w_k"] == 200.0
    assert one["plus_minus_w_k"] is None and one["r2"] is None and "Needs 5 days" in one["why"]
    two = fit([_day("2026-11-01", 10, 48), _day("2026-11-02", 12, 60)])
    assert two["heat_loss_w_k"] == pytest.approx(108 / 22 * 1000 / 24, abs=0.1) and two["plus_minus_w_k"] > 0
    same = [_day(f"2026-11-{n:02d}", 10 + n * 0.1, 50) for n in range(1, 7)]
    f = fit(same)
    assert f["method"] == "estimate" and "more different temperatures" in f["why"]
    assert not fit([])["ok"] and fit([])["method"] is None
    r = results([_day("2026-11-01", 10, 48)], min_dt=5, excluded=[], design_in=21, design_out=-3, area_m2=None)
    assert r["design_kw"] == 4.8 and r["design_plus_minus_kw"] is None


def _charges_rows(day, per_hh, arrives=(10, 20, 26), upto=48):
    """An Octopus accumulative sensor whose data arrives in bursts (at 10:00, 20:00 and 02:00
    the next day): each state lists every half hour so far as charges."""
    rows = []
    for hour in arrives:
        known = min(upto, hour * 2 - 2)  # data up to an hour before it arrives
        charges = [{"start": datetime.datetime.fromtimestamp(_ts(day, 0) + k * 1800, TZ).isoformat(),
                    "end": datetime.datetime.fromtimestamp(_ts(day, 0) + (k + 1) * 1800, TZ).isoformat(),
                    "consumption": per_hh(k)} for k in range(min(known, 48))]
        rows.append({"state": str(sum(c["consumption"] for c in charges)), "attributes": {"charges": charges},
                     "last_changed": datetime.datetime.fromtimestamp(_ts(day, 0) + hour * 3600, TZ).isoformat()})
    return rows


def test_half_hours_come_from_the_charges_whenever_they_arrive():
    from src.heat_loss import gas_charges
    per_hh = lambda k: 1.0 if 12 <= k < 18 or 32 <= k < 44 else 0.0  # noqa: E731
    rows = _charges_rows(DAY, per_hh)
    floors = [(_flat(DAY, 20.0), 1, "x")]
    # The readings alone: two bursts, hours apart, so the day can't be used
    bursts = summarise_day(DAY, TZ, gas=series(rows), outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5)
    assert not bursts["complete"]
    # The charges: each half hour where it belongs, and the day's whole
    d = summarise_day(DAY, TZ, gas=series(rows), outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      charges=gas_charges(rows))
    assert d["complete"] and d["gas_from"] == "half hours" and d["gas_kwh"] == 18.0
    assert [h["gas"] for h in d["hh"]] == [per_hh(k) for k in range(48)]
    # Not all there yet (the last burst hasn't come): left out until it does
    early = _charges_rows(DAY, per_hh, arrives=(10, 20))
    d = summarise_day(DAY, TZ, gas=series(early), outside=_flat(DAY, 5.0), floors=floors, charges=gas_charges(early))
    assert d["why"].startswith("Gas missing for 10 half hours") and "19:00" in d["why"]


def test_charges_with_kwh_and_m3_and_later_states_win():
    from src.heat_loss import gas_charges
    rows = [{"attributes": {"charges": [{"start": "2026-11-02T00:00:00+00:00", "consumption_kwh": 1.0, "consumption_m3": 0.1}]}},
            {"attributes": {"charges": [{"start": "2026-11-02T00:00:00+00:00", "consumption_kwh": 1.5},
                                        {"start": "bad", "consumption": 1}, {"start": "2026-11-02T00:30:00Z", "consumption": "x"}]}}]
    assert gas_charges(rows) == {int(_ts(DAY, 0)): 1.5}


def _calling(day, spans):
    """A thermostat calling for heat in these (from, to) hours, idle otherwise."""
    rows = [(_ts(day, 0) - 3600, 0.0)]
    for a, b in spans:
        rows += [(_ts(day, a), 1.0), (_ts(day, b), 0.0)]
    return rows


def test_showers_while_the_heating_is_off_are_hot_water():
    from src.heat_loss import heating_series
    # Heating 06:00-09:00 and 16:00-22:00 (1 kWh a half hour); showers at 07:00 (in the heating)
    # and 22:30 (heating off): 1.5 kWh each
    per_hh = lambda k: (1.0 if 12 <= k < 18 or 32 <= k < 44 else 0.0) + (1.5 if k in (14, 45) else 0.0)  # noqa: E731
    gas = _gas_day(DAY, per_hh)
    floors = [(_flat(DAY, 20.0), 1, "x")]
    calling = [_calling(DAY, [(6, 9), (16, 22)])]
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      hot_water="auto", calling=calling)
    assert d["dhw_from"] == "heating off" and d["dhw_kwh"] == 1.5  # the one while the heating was off
    assert d["heat_kwh"] == pytest.approx((d["gas_kwh"] - 1.5) * 0.9625, abs=0.01)
    assert d["hh"][45]["dhw"] == 1.5 and d["hh"][14]["dhw"] == 0
    # A set amount a day instead
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      hot_water="fixed", hot_water_kwh=3)
    assert d["dhw_from"] == "set" and d["heat_kwh"] == pytest.approx((d["gas_kwh"] - 3) * 0.9625, abs=0.01)
    # Thermostats that don't say: all heating
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      hot_water="auto", calling=[[]])
    assert d["dhw_from"] is None and d["heat_kwh"] == pytest.approx(d["gas_kwh"] * 0.9625, abs=0.01)
    rows = [{"attributes": {"hvac_action": "heating"}, "last_updated": "2026-11-02T06:00:00+00:00"},
            {"attributes": {"hvac_action": "idle"}, "last_updated": "2026-11-02T09:00:00+00:00"},
            {"attributes": {}, "last_updated": "2026-11-02T10:00:00+00:00"}]
    assert [v for _, v in heating_series(rows)] == [1.0, 0.0, None]


def test_calling_for_heat_is_worked_out_without_hvac_action():
    from src.heat_loss import calling_from, heating_series
    # Like yours: no hvac_action, just the mode, its temperature and what it's set to
    row = lambda h, state, cur, target: {"state": state, "last_updated": f"2026-11-02T{h:02d}:00:00+00:00",  # noqa: E731
                                         "attributes": {"hvac_modes": ["off", "heat"], "current_temperature": cur,
                                                        "temperature": target}}
    rows = [row(5, "heat", 17.0, 16), row(6, "heat", 17.5, 21), row(8, "heat", 20.8, 21), row(9, "heat", 20.6, 21),
            row(22, "off", 19.0, 21)]
    assert [v for _, v in heating_series(rows)] == [0.0, 1.0, 0.0, 1.0, 0.0]
    assert calling_from(rows) == "worked out"
    assert calling_from([{"attributes": {"hvac_action": "idle"}}]) == "reported"
    assert calling_from([{"state": "heat", "attributes": {}}]) is None


def test_other_heating_adds_its_heat():
    gas = _gas_day(DAY, lambda k: 1.0 if 32 <= k < 44 else 0.0)  # 12 kWh of gas
    # Air con: 4 kWh of electricity in the day (a daily total, from 1.0 at midnight), COP 3.5
    ac = [(_ts(DAY, 0) - 3600, 1.0), (_ts(DAY, 0) + 60, 0.0), (_ts(DAY, 8), 2.0), (_ts(DAY, 20), 4.0)]
    floors = [(_flat(DAY, 20.0), 1, "x")]
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, flow_fixed=37.5,
                      extra=[(ac, 3.5, "Loft air con")])
    assert d["complete"] and d["other"] == [{"name": "Loft air con", "kwh": 4.0, "cop": 3.5, "heat_kwh": 14.0}]
    assert d["boiler_heat_kwh"] == pytest.approx(12 * 0.9625, abs=0.01)
    assert d["heat_kwh"] == pytest.approx(12 * 0.9625 + 14, abs=0.01)
    assert d["efficiency"] == pytest.approx(0.9625, abs=0.001)  # the boiler's, not the air con's
    assert sum(h["extra"] for h in d["hh"]) == pytest.approx(14)
    # No readings at all: the day can't be used
    d = summarise_day(DAY, TZ, gas=gas, outside=_flat(DAY, 5.0), floors=floors, extra=[([], 3.5, "Loft air con")])
    assert d["why"] == "No readings from Loft air con"


def test_a_lifetime_total_that_glitches_isnt_counted_as_used():
    from src.heat_loss import energy_series
    # A lifetime total (5,000 kWh) that drops to 0 for a moment (a restart) and comes back
    ac = [(_ts(DAY, 0) - 3600, 5000.0), (_ts(DAY, 6), 5001.0), (_ts(DAY, 9), 0.0), (_ts(DAY, 9, 1), 5001.2),
          (_ts(DAY, 20), 5004.0)]
    d = summarise_day(DAY, TZ, gas=_gas_day(DAY, lambda k: 0.5), outside=_flat(DAY, 5.0),
                      floors=[(_flat(DAY, 20.0), 1, "x")], extra=[(ac, 3.5, "Loft air con")])
    assert d["other"][0]["kwh"] == pytest.approx(3.8)  # 1.0 + 2.8; the drop and jump back left out
    # In Wh: read as kWh
    rows = [{"state": "4000", "attributes": {"unit_of_measurement": "Wh"}, "last_changed": "2026-11-02T01:00:00+00:00"},
            {"state": "5.5", "attributes": {"unit_of_measurement": "kWh"}, "last_changed": "2026-11-02T02:00:00+00:00"}]
    assert [v for _, v in energy_series(rows)] == [4.0, 5.5]


def test_fit_quality_and_validation():
    # 200 W/K: 4.8 kWh/day per C, baseline 5; dt max 16 vs design dt 24 => extrapolation
    days = [_day(f"2026-11-{n:02d}", dt, 4.8 * dt + 5) for n, dt in enumerate([6, 8, 10, 12, 14, 16], 1)]
    # give them inside/outside for validation output
    for d in days:
        d["inside"], d["outside"] = 20.0, 20.0 - d["dt"]
    r = results(days, min_dt=5, excluded=[], design_in=21, design_out=-3, area_m2=130)
    q = r["quality"]
    assert q["label"] in ("Strong", "OK", "Weak")
    assert q["n"] == 6 and q["r2"] == 1.0
    assert q["dt_max"] == 16.0 and q["design_dt"] == 24.0 and q["extrapolation_k"] == 8.0
    assert any("extrapolat" in w.lower() for w in q["warnings"])
    v = r["validation"]
    assert v["summary"]["n"] == 6
    # implied W/K per day: heat_kwh*1000/(24*dt) varies slightly because of baseline, but design-implied should be near 4.8 kW
    assert 4.0 < v["summary"]["implied_design_kw_avg"] < 6.0
    # perfect fit => residuals ~0
    assert abs(v["summary"]["residual_mean_kwh"]) < 0.01
    row = next(x for x in v["days"] if x["date"] == "2026-11-01")
    assert row["dt"] == 6 and abs(row["residual_kwh"]) < 0.01 and row["implied_design_kw"] > 0


def test_estimate_has_quality_and_validation():
    days = [_day("2026-11-01", 10, 48), _day("2026-11-02", 12, 60)]
    r = results(days, min_dt=5, excluded=[], design_in=21, design_out=-3, area_m2=None)
    assert r["ok"] and r["method"] == "estimate"
    assert r["quality"]["label"] == "Estimate"
    assert r["quality"]["r2"] is None
    assert r["validation"]["summary"]["n"] == 2


def test_no_fit_has_quality_no_validation():
    r = results([], min_dt=5, excluded=[], design_in=21, design_out=-3, area_m2=None)
    assert not r["ok"]
    assert r["quality"]["label"] == "Not enough data"
    assert "validation" not in r
