"""Scheduled limits (src/schedule.py), discharge dropouts and the money (src/stats.py)."""

import datetime

import pytest

from src.schedule import Schedule, describe, validate_entry
from src.stats import Sample, StatsRecorder

TUE_23 = datetime.datetime(2026, 10, 6, 23, 30)  # a Tuesday


def test_entries_are_checked():
    e = validate_entry({"kind": "weekly", "days": ["Tuesday", "thu"], "start": "23:00", "end": "08:00", "high": "50"})
    assert e["days"] == ["tue", "thu"] and e["high"] == 50 and e["low"] is None
    assert describe(e) == "Tue, Thu 23:00-08:00: high 50%"
    for bad in ({"kind": "weekly", "days": [], "start": "23:00", "end": "08:00", "high": 50},
                {"kind": "weekly", "days": ["tue"], "start": "23:00", "end": "23:00", "high": 50},
                {"kind": "once", "date": "soon", "start": "06:00", "end": "10:00", "low": 95},
                {"kind": "once", "date": "2026-10-09", "start": "06:00", "end": "10:00"},
                {"kind": "once", "date": "2026-10-09", "start": "6am", "end": "10:00", "low": 95},
                {"kind": "once", "date": "2026-10-09", "start": "06:00", "end": "10:00", "low": 60, "high": 50}):
        with pytest.raises(ValueError):
            validate_entry(bad)


def test_weekly_entry_runs_past_midnight(tmp_path):
    s = Schedule(str(tmp_path))
    s.add({"kind": "weekly", "days": ["tue", "thu"], "start": "23:00", "end": "08:00", "high": 50})
    assert s.apply(80, 40, TUE_23)[:2] == (50, 40)
    assert s.apply(80, 40, TUE_23 + datetime.timedelta(hours=8))[:2] == (50, 40)  # Wed 07:30: still on
    assert s.apply(80, 40, TUE_23 + datetime.timedelta(hours=9))[:2] == (80, 40)  # Wed 08:30: off
    assert s.apply(80, 40, TUE_23 + datetime.timedelta(days=1))[:2] == (80, 40)  # Wed 23:30: not a Wed entry
    assert s.apply(80, 60, TUE_23)[:2] == (50, 49)  # the low gets out of the way
    assert len(Schedule(str(tmp_path)).entries) == 1  # saved
    up = s.upcoming(TUE_23 + datetime.timedelta(hours=9))
    assert up[0]["starts"] == "2026-10-08T23:00"


def test_one_off_wins_when_it_starts_later_and_is_tidied_away(tmp_path):
    s = Schedule(str(tmp_path))
    s.add({"kind": "weekly", "days": ["wed"], "start": "00:00", "end": "12:00", "low": 30})
    one = s.add({"kind": "once", "date": "2026-10-07", "start": "06:00", "end": "10:00", "low": 95, "label": "trip"})
    wed_7 = datetime.datetime(2026, 10, 7, 7, 0)
    high, low, on = s.apply(80, 40, wed_7)
    assert (high, low) == (96, 95) and on[-1]["id"] == one["id"]  # the high gets out of the way
    assert s.apply(80, 40, datetime.datetime(2026, 10, 7, 11, 0))[:2] == (80, 30)
    s.update(one["id"], {"enabled": False})
    assert s.apply(80, 40, wed_7)[:2] == (80, 30)
    s.tidy(datetime.datetime(2026, 10, 7, 10, 1))
    assert [e["kind"] for e in s.entries] == ["weekly"]
    with pytest.raises(KeyError):
        s.remove("nope")


def _sessions(rec, kwh_in_per_pct, kwh_out_per_pct, t0, prices=(0.07, 0.25)):
    """A charge 30->70% at the cheap price while importing, then a discharge 70->30% saving imports."""
    imp_cheap, imp_dear = prices
    e_in = e_out = 100.0
    now = t0
    for soc in range(30, 71):
        rec.feed(Sample(now, "charge", soc, e_in, e_out, 3, 0, 0, -3, grid_kw=3.5, import_price=imp_cheap,
                        export_price=0.15))
        now += 60
        e_in += kwh_in_per_pct
    rec.feed(Sample(now, None, 0, e_in, e_out, 0, 0, 0, 0, grid_kw=0.2, import_price=imp_dear, export_price=0.15))
    now += 3600
    for soc in range(70, 29, -1):
        rec.feed(Sample(now, "discharge", soc, e_in, e_out, -3, 0, 0, 3, grid_kw=0.0, import_price=imp_dear,
                        export_price=0.15))
        now += 60
        e_out += kwh_out_per_pct
    rec.feed(Sample(now, None, 0, e_in, e_out, 0, 0, 0, 0, grid_kw=0.2, import_price=imp_dear, export_price=0.15))


def test_round_trip_from_kwh_per_percent_and_whether_it_pays(tmp_path):
    rec = StatsRecorder(str(tmp_path))
    t0 = datetime.datetime(2026, 10, 7, 1, 0).timestamp()
    _sessions(rec, 0.6, 0.54, t0)  # 0.6 kWh in per 1%, 0.54 kWh out: 90%
    s = rec.summary(64, now=t0 + 6 * 3600, settings={"inverter_efficiency_pct": 100})
    assert s["per_pct_in"]["kwh"] == 0.6 and s["per_pct_out"]["kwh"] == 0.54 and s["round_trip"] == 0.9
    m = s["money"]
    assert m["in_price"] == 0.07 and m["out_price"] == 0.25  # charged cheap, discharging saved dear imports
    assert m["break_even_price"] == round(0.07 / 0.9, 4)
    assert abs(m["margin_per_kwh"] - (0.25 - 0.07 / 0.9)) < 1e-4 and m["profit"] > 0
    assert abs(s["soc_loss_30_kwh"] - s["last30"]["car_out_kwh"] * (1 / 0.9 - 1)) < 0.01
    with_inverter = rec.summary(64, now=t0 + 6 * 3600, settings={"inverter_efficiency_pct": 95})["money"]
    assert abs(with_inverter["whole_round_trip"] - 0.9 * 0.95 * 0.95) < 1e-4
    assert with_inverter["profit"] < m["profit"]


def test_dropouts_are_counted_per_day(tmp_path):
    rec = StatsRecorder(str(tmp_path))
    t = datetime.datetime(2026, 10, 7, 9, 0).timestamp()
    rec.record_dropout(t)
    rec.record_dropout(t + 600)
    s = rec.summary(now=t + 3600)
    assert s["dropouts_today"] == 2 and s["dropouts_7"] == 2 and s["dropouts_30"] == 2


def test_one_tap_holds(tmp_path):
    from src.controller import bound_limits
    from src.holds import make_hold

    now = datetime.datetime(2026, 10, 7, 21, 37, 20)
    e = make_hold("no_discharge", 57.4, now, until="08:00")
    assert (e["date"], e["start"], e["end"], e["low"], e["high"]) == ("2026-10-07", "21:37", "08:00", 57, None)
    h = make_hold("hold", 57.6, now, hours=4)
    assert (h["end"], h["low"], h["high"]) == ("01:37", 58, 59)
    a = make_hold("at_least", None, now, until="10:00", low=90)
    assert a["low"] == 90 and a["label"] == "Hold: at least 90%"
    assert make_hold("no_discharge", 12, now, hours=1)["low"] == 20  # never below the floor
    for bad in (dict(kind="hold", soc=None, hours=1), dict(kind="x", soc=50, hours=1), dict(kind="hold", soc=50, hours=30),
                dict(kind="at_least", soc=50, hours=1), dict(kind="hold", soc=50)):
        with pytest.raises(ValueError):
            make_hold(bad.pop("kind"), bad.pop("soc"), now, **bad)
    with pytest.raises(ValueError):
        make_hold("at_least", 50, now, hours=1, low=10)  # below the floor: refused when it's added
        Schedule(str(tmp_path)).add(make_hold("at_least", 50, now, hours=1, low=10))
    s = Schedule(str(tmp_path))
    s.add(e)
    assert s.apply(80, 40, now + datetime.timedelta(minutes=5))[:2] == (80, 57)
    assert bound_limits(100, 10) == (99, 20) and bound_limits(20, 20) == (21, 20) and bound_limits(None, 5) == (None, 20)


def test_crossing_limits_from_two_entries_the_later_one_wins(tmp_path):
    s = Schedule(str(tmp_path))
    s.add({"kind": "weekly", "days": ["tue"], "start": "23:00", "end": "08:00", "high": 50})
    s.add({"kind": "weekly", "days": ["tue"], "start": "23:10", "end": "08:00", "low": 60})
    assert s.apply(80, 40, TUE_23)[:2] == (61, 60)  # the low started later: the high moves
    t = Schedule(None)
    t.add({"kind": "weekly", "days": ["tue"], "start": "23:00", "end": "08:00", "low": 60})
    t.add({"kind": "weekly", "days": ["tue"], "start": "23:10", "end": "08:00", "high": 50})
    assert t.apply(80, 40, TUE_23)[:2] == (50, 49)  # the high started later: the low moves
    assert t.apply(80, 40, TUE_23 - datetime.timedelta(minutes=25))[:2] == (80, 60)  # only the low is on yet: no clash
    assert t.apply(80, 40, TUE_23 - datetime.timedelta(minutes=35))[:2] == (80, 40)  # before either


def _short(rec, direction, socs, per_pct, now, e):
    """One short session: the SoC through `socs`, a minute a step."""
    for soc in socs:
        e_in, e_out = (e, 100.0) if direction == "charge" else (100.0, e)
        rec.feed(Sample(now, direction, soc, e_in, e_out))
        now += 60
        e += per_pct
    rec.feed(Sample(now, None, 0, *((e, 100.0) if direction == "charge" else (100.0, e))))
    return now + 600, e


def test_short_sessions_add_up_to_a_round_trip_and_a_battery_size(tmp_path):
    # Discharges cut short by dropouts (3-4% each) still count, added up
    rec = StatsRecorder(str(tmp_path))
    now = datetime.datetime(2026, 10, 7, 9, 0).timestamp()
    e_in = e_out = 100.0
    for start in (30, 34, 38):  # three 4% charges at 0.5 kWh per 1%
        now, e_in = _short(rec, "charge", range(start, start + 5), 0.5, now, e_in)
    for start in (70, 66, 62, 58):  # four 4% discharges at 0.45 kWh per 1%
        now, e_out = _short(rec, "discharge", range(start, start - 5, -1), 0.45, now, e_out)
    s = rec.summary(45, now=now)
    assert s["per_pct_in"]["pct"] == 9 and s["per_pct_in"]["kwh"] is None  # 3 x 3% between tick-overs: not 10% yet
    now, e_in = _short(rec, "charge", range(42, 47), 0.5, now, e_in)
    s = rec.summary(45, now=now)
    assert s["per_pct_in"]["kwh"] == 0.5 and s["per_pct_out"]["kwh"] == 0.45 and s["per_pct_out"]["sessions"] == 4
    assert s["round_trip"] == 0.9 and abs(s["battery_kwh"] - (0.5 * 0.45) ** 0.5 * 100) < 0.1


def test_older_sessions_get_their_soc_steps_from_history(tmp_path):
    rec = StatsRecorder(str(tmp_path))
    now = datetime.datetime(2026, 10, 7, 9, 0).timestamp()
    samples = []

    class Tap(StatsRecorder):  # the same readings, as Home Assistant's history would have them
        def feed(self, s, settings=None, live=True):
            samples.append(s)
            super().feed(s, settings, live)
    tap = Tap()
    _short(tap, "discharge", range(70, 65, -1), 0.45, now, 100.0)
    rec.sessions = [dict(x, span_kwh=None) for x in tap.sessions]  # as recorded before 0.17.0
    assert rec.fill_spans(samples) == 1 and rec.sessions[0]["span_kwh"] == 1.35 and rec.spans_filled


def test_only_energy_that_crosses_between_ac_and_dc_has_the_inverters_loss(tmp_path):
    from src.stats import Sample, StatsRecorder

    def run(pv_kw, ac_kw, batt_kw=0.0):
        rec = StatsRecorder()
        t = datetime.datetime(2026, 10, 7, 12, 0).timestamp()
        e = 100.0
        for i in range(11):  # 3 kW into the car for 10 minutes: 0.5 kWh
            rec.feed(Sample(t + i * 60, "charge", 50, e, 0.0, 3.0, pv_kw, batt_kw, ac_kw, grid_kw=-1.0 if pv_kw else 3.0,
                            import_price=0.20, export_price=0.15), {"inverter_efficiency_pct": 90})
            e += 0.05
        return rec.summary(64, now=t + 3600, settings={"inverter_efficiency_pct": 90})["money"]
    solar = run(pv_kw=4.0, ac_kw=1.0)  # solar into the car (DC to DC), the rest exported
    assert solar["in_ac_share"] == 0 and solar["in_cost"] == round(0.5 * 0.15, 2)  # the export given up, no loss
    grid = run(pv_kw=0.0, ac_kw=-3.33)  # all from the grid: AC to DC
    assert grid["in_ac_share"] == 1 and grid["in_cost"] == round(0.5 * 0.20 / 0.9, 2)
    half = run(pv_kw=0.0, ac_kw=-1.5, batt_kw=-1.5)  # half from the home battery
    assert half["in_ac_share"] == 0.5


def test_a_weekly_entry_can_be_skipped_once(tmp_path):
    from src.schedule import Schedule

    s = Schedule(str(tmp_path))
    e = s.add({"kind": "weekly", "days": ["tue", "thu"], "start": "23:00", "end": "08:00", "high": 50})
    tue = datetime.datetime(2026, 10, 13, 23, 30)  # Tuesday night
    assert s.apply(80, 40, tue)[0] == 50
    s.skip(e["id"], "2026-10-13")
    assert s.apply(80, 40, tue)[0] == 80  # skipped: the default
    assert s.apply(80, 40, datetime.datetime(2026, 10, 14, 7, 0))[0] == 80  # ...into the next morning too
    assert s.apply(80, 40, datetime.datetime(2026, 10, 15, 23, 30))[0] == 50  # Thursday still on
    assert s.apply(80, 40, tue + datetime.timedelta(days=7))[0] == 50  # and next Tuesday
    nxt = s.upcoming(datetime.datetime(2026, 10, 13, 12, 0))
    assert nxt[0]["starts"].startswith("2026-10-15")  # the next one is Thursday's
    assert Schedule(str(tmp_path)).entries[0]["skips"] == ["2026-10-13"]  # saved
    s.skip(e["id"], "2026-10-13", skip=False)
    assert s.apply(80, 40, tue)[0] == 50
    for bad in (("2026-10-14", ValueError), ("nope", ValueError)):  # a Wednesday: not one of its days
        try:
            s.skip(e["id"], bad[0])
            raise AssertionError("accepted " + bad[0])
        except bad[1]:
            pass
    once = s.add({"kind": "once", "date": "2026-10-16", "start": "06:00", "end": "10:00", "low": 95})
    try:
        s.skip(once["id"], "2026-10-16")
        raise AssertionError("skipped a one-off")
    except ValueError:
        pass
    # Old skips are forgotten
    s.skip(e["id"], "2026-10-13")
    s.tidy(datetime.datetime(2026, 10, 20, 12, 0))
    assert s.entries[0]["skips"] == []
