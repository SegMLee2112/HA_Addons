"""The automatic schedule (src/auto_schedule.py, and in the schedule: src/automation.py)."""

import datetime

from src import auto_schedule as A
from src.automation import AUTO_SCHED_SOURCE, CHARGE_NOW_SOURCE, Automation, ReplugOptions

TZ = datetime.timezone.utc  # (GMT in November: local time)
D = datetime.datetime(2026, 11, 3, tzinfo=TZ)  # a Tuesday
WEEK = list(range(7))


def at(h, m=0, day=0):
    return D + datetime.timedelta(days=day, hours=h, minutes=m)


def brief(windows):
    return [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b, *_ in windows]


# The user's export rates for Tue 3 Nov from 12:30 (Octopus Agile Outgoing, via Predbat)
USER_EXP = {"12:30": 18.57, "13:00": 18.13, "13:30": 18.13, "14:00": 18.79, "14:30": 18.78, "15:00": 22.07,
            "15:30": 23.26, "16:00": 29.97, "16:30": 31.42, "17:00": 30.89, "17:30": 41.90, "18:00": 42.16,
            "18:30": 30.45, "19:00": 23.06, "19:30": 21.95, "20:00": 19.51, "20:30": 19.93, "21:00": 20.02,
            "21:30": 19.68, "22:00": 19.03, "22:30": 17.90, "23:00": 16.40, "23:30": 15.80}


def user_exp(days=1, morning=0.0):
    """Those rates (with `morning` from 00:00 if given), and the same again for later days."""
    out = []
    for n in range(days):
        if morning:
            out += [(at(0, 30 * k, n), at(0, 30 * (k + 1), n), morning, False) for k in range(25)]
        for k, p in USER_EXP.items():
            h, m = map(int, k.split(":"))
            out.append((at(h, m, n), at(h, m + 30, n), p, False))
    return out


def iog_import(days=2):
    """Intelligent Octopus Go: 7p 23:30-05:30, 30p otherwise."""
    out = []
    for n in range(48 * days):
        t = D + n * A.HH
        cheap = (t.hour, t.minute) >= (23, 30) or (t.hour, t.minute) < (5, 30)
        out.append((t, t + A.HH, 7.0 if cheap else 30.0, False))
    return out


# --- the planner -------------------------------------------------------------------


def test_the_users_rates():
    """The best 12 are 15:00-20:00 and 20:30-21:30: charging straight before each run, as
    far as there's time (from 12:30, now 12:25), not between the exports."""
    p = A.plan([], user_exp(), at(12, 25), 12)
    assert brief(p["exports"])[0] == ("15:00", "15:30") and len(p["exports"]) == 12
    assert brief(p["charge"]) == [("12:30", "15:00"), ("20:00", "20:30")]
    assert any("150 of 300 min" in n for n in p["notes"])


def test_charge_matches_the_run_when_theres_time():
    p = A.plan([], user_exp(morning=5), at(9), 12)
    assert brief(p["charge"]) == [("10:00", "15:00"), ("20:00", "20:30")]
    p = A.plan([], user_exp(morning=5), at(9), 2)  # the best two: 17:30-18:30
    assert brief(p["exports"]) == [("17:30", "18:00"), ("18:00", "18:30")]
    assert brief(p["charge"]) == [("16:30", "17:30")]


def test_a_copied_day_isnt_used():
    """Predbat fills tomorrow (not published till 16:00) with today's: left out."""
    p = A.plan([], user_exp(2), at(9), 12)
    assert p["charge"] and all(a.date() == D.date() for a, _ in p["charge"])
    real = user_exp(2)
    real[-6] = (real[-6][0], real[-6][1], 99.0, False)  # tomorrow's own rates: used
    assert any(a.date() != D.date() for a, _ in A.plan([], real, at(9), 12)["charge"])


def test_busy_and_cuts():
    """A charge before a saving session comes first: no export or block in it; the session
    itself is still an export time, but not a charging one."""
    busy = [(at(16, 50), at(17, 50))]
    cuts = [(at(17, 50), at(18, 30))]
    p = A.plan([], user_exp(), at(9), 12, cuts=cuts, busy=busy)
    ex = [a for a, _, _ in p["exports"]]
    assert at(17) not in ex and at(17, 30) not in ex and at(18) in ex
    assert not any(a < y and b > x for a, b in p["charge"] for x, y in busy + cuts)


def test_offpeak_from_import_and_kept_clear():
    imp = iog_import()
    assert brief(A.offpeak(imp, at(9))) == [("23:30", "05:30")]
    p = A.plan(imp, user_exp(), at(9), 12)
    assert at(23, 30) not in [a for a, _, _ in p["exports"]]
    assert brief(p["offpeak"]) == [("23:30", "05:30")]


def test_dispatch_adjusted_import_isnt_offpeak():
    imp = [(a, b, 7.0 if 15 <= a.hour < 17 else 30.0, 15 <= a.hour < 17) for a, b, _, _ in iog_import(1)]
    assert A.offpeak(imp, at(9)) == []


def test_daily_limit():
    p = A.plan([], user_exp(morning=5), at(9), 12, cap_min=120, day_of=lambda t: t.date(), used=lambda d: 60)
    assert sum((b - a).seconds for a, b in p["charge"]) == 60 * 60  # 2 h less the hour already used


def test_parse_octopus_and_predbat():
    st = {"attributes": {"rates": [
        {"start": "2026-11-03T16:00:00+00:00", "end": "2026-11-03T17:00:00+00:00", "value_inc_vat": 0.2307,
         "is_intelligent_adjusted": False}]}}
    r = A.parse_rates(st)
    assert [(a.strftime("%H:%M"), p) for a, _, p, _ in r] == [("16:00", 23.07), ("16:30", 23.07)]
    st = {"attributes": {"results": {"2026-11-03T16:00:00+00:00": 30.5, "2026-11-03T16:30:00+00:00": 12}}}
    assert [p for _, _, p, _ in A.parse_rates(st)] == [30.5, 12]
    assert A.parse_rates({"attributes": {}}) == []


def test_find_rate_entities():
    rl = {"attributes": {"rates": []}}
    states = [{"entity_id": "event.octopus_energy_electricity_s1_m1_current_day_rates", **rl},
              {"entity_id": "event.octopus_energy_electricity_s1_m1_next_day_rates", **rl},
              {"entity_id": "event.octopus_energy_electricity_s1_m2_export_current_day_rates", **rl},
              {"entity_id": "predbat.rates", "attributes": {}}]
    f = A.find_rate_entities(states)
    assert f["import"] == ["event.octopus_energy_electricity_s1_m1_current_day_rates",
                           "event.octopus_energy_electricity_s1_m1_next_day_rates"]
    assert f["export"] == ["event.octopus_energy_electricity_s1_m2_export_current_day_rates"]
    assert A.find_rate_entities(states[3:]) == {"import": ["predbat.rates"], "export": [], "source": "Predbat"}


# --- in the schedule ------------------------------------------------------------------


class Clock:
    def __init__(self, start):
        self.dt = start

    def now(self):
        return self.dt

    def epoch(self):
        return self.dt.timestamp()


def _auto(start=None):
    clock = Clock(start or at(9))
    a = Automation(None, ReplugOptions(), now=clock.now, clock=clock.epoch,
                   localize=lambda naive: naive.replace(tzinfo=TZ))
    return a, clock


def _wins(a, day=0, after="00:00"):
    d = (D + datetime.timedelta(days=day)).date()
    return [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.windows()
            if x.date() == d and x.strftime("%H:%M") >= after]


def _users_schedule(a, skip_tomorrow_0830=True):
    """The user's: 06:30, 08:30, 15:30, 17:30-19:30 and 21:30 (6 hours a day), off-peak 23:30-05:30."""
    ents = []
    for p, u in (("06:30", "07:30"), ("08:30", "09:30"), ("15:30", "16:30"), ("17:30", "19:30"), ("21:30", "22:30")):
        ents += [{"time": p, "action": "plug", "days": WEEK}, {"time": u, "action": "unplug", "days": WEEK}]
    a.set_schedule(enabled=True, ready_time=True, entries=ents,
                   blocks=[{"start": "23:30", "end": "05:30", "days": WEEK}], daily_cap_min=360)
    if skip_tomorrow_0830:
        e = {x["time"]: x["id"] for x in a.entries}
        a.set_skips([{"entry_id": e["08:30"], "date": "2026-11-04"}, {"entry_id": e["09:30"], "date": "2026-11-04"}])


def _day_minutes(a, d0, d1):
    return sum((min(y, d1) - max(x, d0)).seconds // 60 for x, y in a.windows() if y > d0 and x < d1)


def test_blocks_go_in_the_schedule_and_come_out():
    a, _ = _auto(at(12, 25))
    assert a.sync_auto_schedule([], user_exp(), 12)
    assert _wins(a) == [("12:30", "15:00"), ("20:00", "20:30")]
    assert all(e.get("source") == AUTO_SCHED_SOURCE for e in a.entries)
    assert not a.sync_auto_schedule([], user_exp(), 12)  # (nothing changed: not planned again)
    assert a.clear_auto_schedule() and a.entries == [] and a.skips == []


def test_the_users_day_twelve_half_hours():
    """12 (Octopus's 6 hours): your slots give way in the limit day (Tue 12:00 - Wed 12:00),
    and what it can't place yet (until tomorrow's rates at 16:00) is kept for it."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    _users_schedule(a)
    a.sync_auto_schedule(iog_import(1), user_exp(), 12, cap_min=360)
    assert _wins(a, after="12:00") == [("12:30", "15:00"), ("20:00", "20:30")]
    assert _wins(a, 1) == [("15:30", "16:30"), ("17:30", "19:30"), ("21:30", "22:30")]  # Wed 06:30 gave way too
    assert _day_minutes(a, at(12), at(12, 0, 1)) == 180
    a.clear_auto_schedule()
    assert _wins(a, after="12:00") == [("15:30", "16:30"), ("17:30", "19:30"), ("21:30", "22:30")]
    assert _wins(a, 1)[0] == ("06:30", "07:30")


def test_the_users_day_fewer_half_hours():
    """6: your slots give way only as far as the day needs, those in the best exports first."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    _users_schedule(a)
    a.sync_auto_schedule([], user_exp(), 6, cap_min=360)
    assert _day_minutes(a, at(12), at(12, 0, 1)) == 360
    assert ("13:00", "16:30") in _wins(a)  # charging 13:00-16:00, joined to your 15:30


def test_saving_session_charge_comes_first():
    """A saving session 17:50-18:30 with an hour's charge before it: kept as it is."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    _users_schedule(a)
    a.sync_saving_sessions([{"start": at(17, 50), "end": at(18, 30), "summary": "Saving session"}], True, None,
                           360, "Octopus Energy", 1.0)
    a.sync_auto_schedule([], user_exp(), 12, cap_min=360)
    assert ("16:50", "17:50") in _wins(a)
    assert not any(x < at(17, 50) and y > at(16, 50) for x, y in
                   ((A._when(p), A._when(q)) for p, q in a.auto_plan["charge"]))
    assert _day_minutes(a, at(12), at(12, 0, 1)) <= 360


def test_charges_come_first():
    a, clock = _auto(at(9))
    a.limit_reset = "12:00"
    a.sync_auto_schedule([], user_exp(morning=5), 12, cap_min=360)
    clock.dt = at(10)
    a.plan_auto_plug(300, cap_min=360, source=CHARGE_NOW_SOURCE)  # Charge now, 5 hours from 10:00
    assert not any(e.get("source") == AUTO_SCHED_SOURCE for e in a.entries)  # its blocks are out
    assert a.sync_auto_schedule([], user_exp(morning=5), 12, cap_min=360)  # planned again around it
    assert _day_minutes(a, at(12), at(12, 0, 1)) <= 360


def test_dry_run_leaves_it():
    a, _ = _auto(at(12, 25))
    a.sync_auto_schedule([], user_exp(), 12)
    before = (list(a.entries), list(a.skips))
    a.plan_auto_plug(60, source=CHARGE_NOW_SOURCE, dry_run=True)
    assert (a.entries, a.skips) == before and a.auto_plan["charge"]


def test_not_changed_while_a_block_is_going():
    a, clock = _auto(at(12, 25))
    a.sync_auto_schedule([], user_exp(), 12)
    clock.dt = at(13)
    assert not a.sync_auto_schedule([], user_exp(), 2)
    clock.dt = at(15, 5)
    assert a.sync_auto_schedule([], user_exp(), 2)
    assert _wins(a)[-1] == ("16:30", "17:30")


def test_offpeak_is_a_no_charge_time():
    a, _ = _auto()
    a.sync_auto_schedule(iog_import(), user_exp(), 1)
    assert [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.auto_offpeak()] == [("23:30", "05:30")]
    assert any(x.hour == 23 for x, _ in a.block_windows(D.date(), 2))


def test_a_flat_day_isnt_used():
    """Octopus's next day rates before 16:00: the fixed export rate (12p) all day: left out."""
    flat = [(at(0, 30 * k, 1), at(0, 30 * (k + 1), 1), 12.0, False) for k in range(48)]
    p = A.plan([], user_exp() + flat, at(12, 50), 12, cap_min=360,
               day_of=lambda t: (t - datetime.timedelta(hours=12)).date())
    assert brief(p["charge"]) == [("13:00", "15:00"), ("20:00", "20:30")]
    assert all(a.date() == D.date() for a, _ in p["charge"])
