"""The automatic schedule (src/auto_schedule.py, and in the schedule: src/automation.py)."""

import copy
import datetime

from src import auto_schedule as A
from src.automation import AUTO_SCHED_SOURCE, AUTO_SOURCE, CHARGE_NOW_SOURCE, Automation, ReplugOptions, longest_day, minus

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
    """12 half hours from 12:25: where exporting pays least (from 12:30)."""
    p = A.plan([], user_exp(), at(12, 25), 12)
    assert "exports" not in p
    assert brief(p["charge"]) == [("12:30", "15:00"), ("20:00", "21:00"), ("21:30", "00:00")]
    assert not p["notes"]

def test_charging_where_exporting_pays_least():
    p = A.plan([], user_exp(morning=5), at(9), 2)
    assert brief(p["charge"]) == [("09:00", "10:00")]  # (5p: the cheapest)
    p = A.plan([], user_exp(morning=5), at(9), 12)
    assert brief(p["charge"]) == [("09:00", "12:30"), ("13:00", "14:00"), ("22:30", "00:00")]

def test_a_copied_day_isnt_used():
    """Predbat fills tomorrow (not published till 16:00) with today's: left out."""
    p = A.plan([], user_exp(2), at(9), 12)
    assert p["charge"] and all(a.date() == D.date() for a, _ in p["charge"])
    real = user_exp(2)
    real[-6] = (real[-6][0], real[-6][1], 99.0, False)  # tomorrow's own rates: used
    assert any(a.date() != D.date() for a, _ in A.plan([], real, at(9), 12)["charge"])


def test_busy_and_cuts():
    """No charging in a charge that comes first (e.g. before a saving session, or an auto plug-in)
    or a cut (an off-peak time, a saving session)."""
    busy = [(at(16, 50), at(17, 50))]
    cuts = [(at(17, 50), at(18, 30))]
    p = A.plan([], user_exp(), at(9), 12, cuts=cuts, busy=busy)
    assert p["charge"] and not any(a < y and b > x for a, b in p["charge"] for x, y in busy + cuts)

def test_offpeak_from_import_and_kept_clear():
    imp = iog_import()
    assert brief(A.offpeak(imp, at(9))) == [("23:30", "05:30")]
    p = A.plan(imp, user_exp(), at(9), 12)
    assert not any(b > at(23, 30) for _, b in p["charge"])
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
    assert _wins(a) == [("12:30", "15:00"), ("20:00", "21:00"), ("21:30", "00:00")]
    assert all(e.get("source") == AUTO_SCHED_SOURCE for e in a.entries)
    assert not a.sync_auto_schedule([], user_exp(), 12)  # (nothing changed: not planned again)
    assert a.clear_auto_schedule() and a.entries == [] and a.skips == []


def test_the_users_day_twelve_half_hours():
    """12 asked for, 6 placed (from 12:25 there's only time for 12:30-15:00 and 20:00-20:30):
    your slots give way for those 3 hours only (where export pays most first), not for
    all 12; the rest is planned when tomorrow's rates are out."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    _users_schedule(a)
    a.sync_auto_schedule(iog_import(1), user_exp(), 12, cap_min=360)
    assert ("12:30", "15:00") in _wins(a) and ("20:00", "20:30") in _wins(a)
    assert _day_minutes(a, at(12), at(12, 0, 1)) == 360  # 3 hours of yours kept
    assert _wins(a, 1)[:1] == [("06:30", "07:30")]  # its export price isn't known yet: kept
    # Your slots' half hours where export pays most gave way to the day's limit (15:30-16:30,
    # 17:30-19:30), and what was left in the best export times (15:30, 19:00) moved out of them,
    # to 22:30-23:30 (the cheapest free time that day), joined on to your 21:30-22:30
    assert _wins(a, after="12:00") == [("12:30", "15:00"), ("20:00", "20:30"), ("21:30", "23:30")]
    a.clear_auto_schedule()
    assert _wins(a, after="12:00") == [("15:30", "16:30"), ("17:30", "19:30"), ("21:30", "22:30")]

def test_the_users_day_fewer_half_hours():
    """6 (3 hours): your slots where the rates are known give way (tomorrow's 06:30-07:30, rates not
    known yet, stays: it counts only towards the limit), and the 3 hours go where exporting pays
    least: 12:30-14:00, 14:30-15:00 and 22:30-23:30 (23:30's your off-peak time)."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    _users_schedule(a)
    a.sync_auto_schedule([], user_exp(), 6, cap_min=360)
    assert _wins(a, after="12:00") == [("12:30", "14:00"), ("14:30", "15:00"), ("22:30", "23:30")]
    assert _wins(a, 1)[0] == ("06:30", "07:30")
    assert _day_minutes(a, at(12), at(12, 0, 1)) == 240


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
    going = next((A._when(x), A._when(y)) for x, y in a.auto_plan["charge"] if A._when(x) <= at(10) < A._when(y))
    a.plan_auto_plug(300, cap_min=360, source=CHARGE_NOW_SOURCE)  # Charge now, 5 hours from 10:00
    # its blocks still to come are out; the one going (from 09:00) stays to its end
    assert all(a._skip_time({"entry_id": e["id"], "date": e["date"]}) <= going[1]
               for e in a.entries if e.get("source") == AUTO_SCHED_SOURCE)
    assert any(e.get("source") == AUTO_SCHED_SOURCE for e in a.entries)
    assert not a.sync_auto_schedule([], user_exp(morning=5), 12, cap_min=360)  # (not while that's going)
    clock.dt = going[1]
    assert a.sync_auto_schedule([], user_exp(morning=5), 12, cap_min=360)  # planned again around it, after
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
    assert _wins(a) == [("12:30", "15:00")]  # (done: stays, and counts in the day: no more for 2)


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
    assert brief(p["charge"]) == [("13:00", "15:00"), ("20:00", "00:00")]
    assert all(a.date() == D.date() for a, _ in p["charge"])


def test_each_limit_day_gets_its_hours_within_its_limit():
    """Two days of rates, 12:00 to 12:00 limit days: each gets its 6 hours, its charging where
    exporting pays least (not tomorrow's 12:00-18:00 peak)."""
    exp = []
    for n in range(96):
        t = D + n * A.HH
        hm = t.hour * 60 + t.minute
        p = (30 if 16 * 60 <= hm < 19 * 60 else 15) if t.day == 3 else (35 if 12 * 60 <= hm < 18 * 60 else 15)
        exp.append((t, t + A.HH, p + (n % 5) * 0.3, False))
    a, _ = _auto(at(13))
    a.limit_reset = "12:00"
    _users_schedule(a, skip_tomorrow_0830=False)
    a.sync_auto_schedule(iog_import(), exp, 12, cap_min=360)
    assert _day_minutes(a, at(12), at(12, 0, 1)) == 360
    assert _day_minutes(a, at(12, 0, 1), at(12, 0, 2)) == 360
    assert not any(A._when(x) < at(18, 0, 1) and A._when(y) > at(12, 0, 1) for x, y in a.auto_plan["charge"])

def test_rolling_limit_is_kept():
    """A rolling limit (any 48 hours, 4 hours of it): blocks only go where no 48 hours they're
    in goes over, and none where your slots alone already take it over."""
    a, _ = _auto(at(9))
    a.limit_reset = "48h"
    a.set_schedule(enabled=True, entries=[{"time": "01:00", "action": "plug", "days": WEEK},
                                          {"time": "03:00", "action": "unplug", "days": WEEK}])  # 4 h in 48
    a.sync_auto_schedule([], user_exp(morning=5), 12, cap_min=240)
    assert a.auto_plan["charge"] == []
    a.set_schedule(entries=[{"time": "01:00", "action": "plug", "days": [0]},
                            {"time": "02:00", "action": "unplug", "days": [0]}])  # Mondays only
    a.sync_auto_schedule([], user_exp(morning=5), 12, cap_min=240)
    w = longest_day(a.windows(open_ended=True), at(9) - datetime.timedelta(days=1), at(9, 0, 3), "48h", a._localize)
    assert a.auto_plan["charge"] and w[0] <= 240


def test_half_hours_counted_by_reset_day_without_a_limit():
    a, _ = _auto(at(9))
    a.limit_reset = "17:00"
    a.sync_auto_schedule([], user_exp(morning=5), 4)  # no limit known, but it resets at 17:00
    per = {}
    for x, y in a.auto_plan["charge"]:
        t = A._when(x)
        while t < A._when(y):
            k = (t - datetime.timedelta(hours=17)).date()
            per[k] = per.get(k, 0) + 1
            t += A.HH
    assert all(v <= 4 for v in per.values())


def test_an_unplug_on_its_own_inside_a_block_is_skipped():
    """A slot whose plug-in is skipped but not its unplug (an unplug on its own) mustn't cut a block."""
    a, _ = _auto(at(9))
    a.set_schedule(enabled=True, entries=[{"time": "09:30", "action": "unplug", "days": WEEK}])
    a.sync_auto_schedule([], user_exp(morning=5), 2)  # 09:00-10:00 (5p): 09:30's inside
    assert [(A._when(x).strftime("%H:%M"), A._when(y).strftime("%H:%M")) for x, y in a.auto_plan["charge"]] == [("09:00", "10:00")]
    for x, y in a.auto_plan["charge"]:
        x, y = A._when(x), A._when(y)
        assert not minus([(x, y)], a.windows())
    a.clear_auto_schedule()
    assert a.skips == []


def test_a_slot_joined_to_a_charge_that_comes_first_isnt_trimmed():
    """Your 12:30-14:30, joined by Charge now 13:30-15:00: one stretch, left as it is (it used
    to be taken for 12:30 to your next unplug, 01:00, and 'trimmed', leaving it plugged in all night)."""
    a, clock = _auto(at(13, 30))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, ready_time=True, daily_cap_min=120, allow_over=True, entries=[
        {"time": "12:30", "action": "plug", "days": WEEK}, {"time": "14:30", "action": "unplug", "days": WEEK},
        {"time": "22:00", "action": "plug", "days": WEEK}, {"time": "01:00", "action": "unplug", "days": WEEK}])
    a.plan_auto_plug(90, cap_min=120, source=CHARGE_NOW_SOURCE)
    before = a._longest_day(a._active_entries(), a.skips)[0]
    a.sync_auto_schedule([], user_exp(), 8, cap_min=120)
    assert a._longest_day(a._active_entries(), a.skips)[0] <= before
    assert not any(y - x > datetime.timedelta(hours=6) for x, y in a.windows())


def test_the_plan_is_in_the_schedule_even_when_a_day_cant_fit():
    """It plans a day with fewer blocks until it fits; the last try is what's kept (the
    blocks used to be left out of the schedule but still shown)."""
    a, _ = _auto(at(1, 5))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, entries=[{"time": "09:30", "action": "plug", "days": WEEK},
                                          {"time": "12:30", "action": "unplug", "days": WEEK},
                                          {"time": "04:30", "action": "plug", "days": WEEK},
                                          {"time": "06:30", "action": "unplug", "days": WEEK}])
    a.set_skips([{"entry_id": a.entries[0]["id"], "date": D.date().isoformat()}])
    a.sync_auto_schedule([], user_exp(2, morning=5), 4, cap_min=240)
    for x, y in a.auto_plan["charge"]:
        x, y = A._when(x), A._when(y)
        if y > at(1, 5):
            assert not minus([(max(x, at(1, 5)), y)], a.windows()), (x, y)


def test_no_charging_in_a_saving_session_or_the_charge_before_it():
    """A saving session (cut) and the charge before it (busy) aren't charged in again: the 4 half
    hours go where it pays least of the rest."""
    sess = [(at(13, 50), at(14, 30))]
    p = A.plan([], user_exp(), at(12, 25), 4, cuts=sess, busy=[(at(12, 50), at(13, 50))])
    assert brief(p["charge"]) == [("14:30", "15:00"), ("22:30", "00:00")]

def test_a_saving_session_in_the_schedule_is_kept_clear():
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    a.sync_saving_sessions([{"start": at(13, 50), "end": at(14, 30), "summary": "Saving session"}], True, None,
                           360, "Octopus Energy", 1.0)
    a.sync_auto_schedule([], user_exp(), 4, cap_min=360)
    assert a.auto_plan["charge"] and not any(A._when(x) < at(14, 30) and A._when(y) > at(13, 50) for x, y in a.auto_plan["charge"])

def test_planned_again_when_the_limit_or_its_rules_change():
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    assert a.sync_auto_schedule([], user_exp(), 12, cap_min=360)
    assert not a.sync_auto_schedule([], user_exp(), 12, cap_min=360)
    assert a.sync_auto_schedule([], user_exp(), 12, cap_min=300)  # (the limit changed)
    a.auto_plan["v"] = 1  # (made by an older version)
    assert a.sync_auto_schedule([], user_exp(), 12, cap_min=300)


def test_until_uses_the_reset_it_was_planned_with():
    """Just after a restart, before your supplier's reset is known: its planned day, not all the rates."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    a.sync_auto_schedule([], user_exp(2), 12, cap_min=360)
    until = a.auto_snapshot()["until"]
    a.limit_reset = None
    assert a.auto_snapshot()["until"] == until


def test_the_charge_before_a_saving_session_comes_first():
    """The user's day at 14:55: a session 17:30-18:30 with an hour's charge before it (16:30-17:30).
    5 hours of charging still fit in the day's 6: 10 half hours, where exporting pays least, not
    in the charge before it."""
    a, _ = _auto(at(14, 55))
    a.limit_reset = "12:00"
    a.sync_saving_sessions([{"start": at(17, 30), "end": at(18, 30), "summary": "Saving session"}], True, None,
                           360, "Octopus Energy", 1.0)
    a.sync_auto_schedule([], user_exp(), 12, cap_min=360)
    assert sum((A._when(y) - A._when(x)).seconds // 1800 for x, y in a.auto_plan["charge"]) == 10
    assert not any(A._when(x) < at(18, 30) and A._when(y) > at(16, 30) for x, y in a.auto_plan["charge"])

def test_planned_again_later_its_earlier_blocks_still_count():
    """Its 12:30-15:00 block done, planned again at 16:00 (e.g. tomorrow's rates out): that block
    and the time it took off your slots stay, and today's limit day doesn't get more on top."""
    a, clock = _auto(at(12, 25))
    a.limit_reset = "12:00"
    _users_schedule(a)
    a.sync_auto_schedule(iog_import(1), user_exp(), 12, cap_min=360)
    before = _day_minutes(a, at(12), at(12, 0, 1))
    clock.dt = at(16)
    assert a.sync_auto_schedule(iog_import(1), user_exp(), 12, cap_min=300)  # (planned again)
    assert ("12:30", "15:00") in _wins(a)
    assert _day_minutes(a, at(12), at(12, 0, 1)) <= max(before, 360)
    a.clear_auto_schedule()
    assert _wins(a, after="12:00") == [("15:30", "16:30"), ("17:30", "19:30"), ("21:30", "22:30")]


def test_your_slots_give_way_where_the_rates_are_known():
    """No limit: your 17:30-19:30 is where the rates are known, so it's skipped; the 4 half hours
    go where exporting pays least: 13:00-13:30 and 22:30-00:00. Turned off, it's back."""
    a, _ = _auto(at(12, 25))
    a.set_schedule(enabled=True, entries=[{"time": "17:30", "action": "plug", "days": WEEK},
                                          {"time": "19:30", "action": "unplug", "days": WEEK}])
    a.sync_auto_schedule([], user_exp(), 4)
    assert _wins(a, after="12:00") == [("13:00", "13:30"), ("22:30", "00:00")]
    assert _wins(a, 1) == [("17:30", "19:30")]  # (tomorrow's rates aren't known: stays)
    assert any("17:30–19:30 skipped" in n for n in a.auto_plan["notes"])
    a.clear_auto_schedule()
    assert _wins(a, after="12:00") == [("17:30", "19:30")]


def test_a_saving_session_after_it_keeps_its_slot_out():
    """The automatic schedule first, then a saving session: planned again, the session's changes go
    in on your own schedule first, so your slot isn't put back in the session, and the day's
    within its limit; it settles (not planned again and again)."""
    a, _ = _auto(at(15, 35))
    a.limit_reset = "12:00"
    ents = [{"time": "13:30", "action": "plug", "date": "2026-11-03"}, {"time": "15:00", "action": "unplug", "date": "2026-11-03"}]
    for p, u in (("17:00", "18:00"), ("19:00", "20:00"), ("21:00", "22:00"), ("22:30", "23:00"), ("07:00", "08:30")):
        ents += [{"time": p, "action": "plug", "days": WEEK}, {"time": u, "action": "unplug", "days": WEEK}]
    a.set_schedule(enabled=True, ready_time=True, entries=ents, blocks=[{"start": "23:30", "end": "05:30", "days": WEEK}],
                   daily_cap_min=360, allow_over=True)
    exp = [(at(15, 30 * k), at(15, 30 * k + 30), p, False) for k, p in enumerate(
        [23.26, 29.97, 31.42, 30.89, 41.90, 42.16, 30.45, 23.06, 21.95, 19.51, 19.93, 20.02, 19.68, 19.03, 17.90, 16.40, 15.80])]
    sess = [{"start": at(17, 30), "end": at(18, 30), "summary": "Saving session"}]
    a.sync_auto_schedule(iog_import(2), exp, 12, cap_min=360, provider="Octopus Energy")
    for _ in range(3):
        a.sync_saving_sessions(sess, True, None, 360, "Octopus Energy", 1.0)
        a.sync_auto_schedule(iog_import(2), exp, 12, cap_min=360, provider="Octopus Energy")
        assert not any(x < at(18, 30) and y > at(17, 30) for x, y in a.windows())
        assert _day_minutes(a, at(12), at(12, 0, 1)) <= 360
    assert not a.sync_saving_sessions(sess, True, None, 360, "Octopus Energy", 1.0)
    assert not a.sync_auto_schedule(iog_import(2), exp, 12, cap_min=360, provider="Octopus Energy")


def test_a_skip_two_things_need_stays_when_one_lets_go():
    a, _ = _auto(at(9))
    a.set_schedule(enabled=True, entries=[{"time": "17:00", "action": "plug", "days": WEEK},
                                          {"time": "18:00", "action": "unplug", "days": WEEK}])
    k = {"entry_id": a.entries[0]["id"], "date": "2026-11-03"}
    a.auto_plan = {"changes": {"added": [], "skips": []}, "charge": []}
    owned = a._claim_skips([k], a.auto_plan["changes"])
    a.auto_plan["changes"]["skips"] += owned
    other = {"skips": []}
    a.saving_sessions = [{"id": "x", "start": at(17).isoformat(), "end": at(18).isoformat(), "changes": other}]
    other["skips"] += a._claim_skips([k], other)  # (already there: still its own too)
    a._release_skips(a.auto_plan["changes"]["skips"], a.auto_plan["changes"])
    a.auto_plan["changes"] = {"added": [], "skips": []}  # (as undoing it does)
    assert k in a.skips
    a._release_skips(other["skips"], other)
    assert k not in a.skips
    # one you made yourself isn't theirs
    a.skips = [k]
    a.auto_plan["changes"] = {"added": [], "skips": []}
    a.saving_sessions = []
    assert a._claim_skips([k], a.auto_plan["changes"]) == []


def test_a_charge_that_comes_first_counts_only_near_the_limit():
    """Charge now for an hour in the day: its 4 half hours all go in, unless the day's limit is
    near: with 2.5 hours, 3 (1.5 hours) do."""
    a, _ = _auto(at(9))
    a.limit_reset = "00:00"
    a.plan_auto_plug(60, source=CHARGE_NOW_SOURCE)
    a.sync_auto_schedule([], user_exp(morning=5), 4)
    assert sum((A._when(y) - A._when(x)).seconds // 1800 for x, y in a.auto_plan["charge"]) == 4
    a.sync_auto_schedule([], user_exp(morning=5), 4, cap_min=150)
    assert sum((A._when(y) - A._when(x)).seconds // 1800 for x, y in a.auto_plan["charge"]) == 3
    assert _day_minutes(a, at(0), at(0, 0, 1)) == 150


def test_a_slot_a_saving_session_shortened_gives_way_too():
    """The charge before a saving session (16:30-17:30) shortened your 19:00-20:00 to keep within
    the limit; turned on once that charge has started, that shortened slot gives way like your
    others (where the rates are known), and the charge before the session stays."""
    a, clock = _auto(at(15, 0))
    a.limit_reset = "12:00"
    ents = [{"time": "13:00", "action": "plug", "date": "2026-11-03"}, {"time": "15:00", "action": "unplug", "date": "2026-11-03"}]
    for p, u in (("19:00", "20:00"), ("21:00", "22:00"), ("07:00", "08:30")):
        ents += [{"time": p, "action": "plug", "days": WEEK}, {"time": u, "action": "unplug", "days": WEEK}]
    a.set_schedule(enabled=True, ready_time=True, entries=ents, daily_cap_min=360, allow_over=True)
    sess = [{"start": at(17, 30), "end": at(18, 30), "summary": "Saving session"}]
    a.sync_saving_sessions(sess, True, None, 360, "Octopus Energy", 1.0)
    assert ("19:30", "20:00") in _wins(a)  # (shortened by it)
    clock.dt = at(17)
    a.sync_auto_schedule([], user_exp(), 12, cap_min=360)
    assert ("16:30", "17:30") in _wins(a)
    assert not any(x < at(20) and y > at(19) for x, y in a.windows())
    assert _day_minutes(a, at(12), at(12, 0, 1)) <= 360


def test_a_charge_that_comes_first_keeps_what_it_did():
    """Planned at 09:00 (charging 09:00-12:30 at 5p, in the day from 12:00 the day before), then an
    auto plug-in at 17:00: what it did before stays, and the day's limit counts it."""
    a, clock = _auto(at(9))
    a.limit_reset = "12:00"
    a.sync_auto_schedule([], user_exp(morning=5, days=2), 12, cap_min=360)
    clock.dt = at(17)
    a.sync_auto_schedule([], user_exp(morning=5, days=2), 12, cap_min=360)
    before = [w for w in a.windows() if w[1] <= at(17)]
    a.plan_auto_plug(120, cap_min=360, source=AUTO_SOURCE)
    assert [w for w in a.windows() if w[1] <= at(17)] == before
    a.sync_auto_schedule([], user_exp(morning=5, days=2), 12, cap_min=360)
    assert [w for w in a.windows() if w[1] <= at(17)] == before
    assert _day_minutes(a, at(12), at(12, 0, 1)) <= 360


def test_the_plan_is_logged(caplog):
    import logging
    caplog.set_level(logging.INFO, "src.automation")
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    a.sync_auto_schedule([], user_exp(), 12, cap_min=360, provider="Octopus Energy")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Automatic schedule planned (12 half hours a day, where exporting pays least, Octopus Energy's limit 6h from 12:00):" in text
    assert "  Charging: Tue 12:30–15:00, Tue 20:00–21:00, Tue 21:30–00:00" in text
    assert "  Plugged in per day: Tue 12:00–Wed 12:00: 6h of 6h" in text


def test_late_in_the_day_the_cheapest_of_whats_left():
    """At 21:30, with 4 asked for: the cheapest of what's left, 22:00-00:00."""
    p = A.plan([], user_exp(), at(21, 30), 4)
    assert brief(p["charge"]) == [("22:00", "00:00")]

def test_a_slot_you_unskip_stays_yours():
    """Your 17:30-19:30 skipped by it (the rates are known); you un-skip it: it runs, and it's
    planned again around it (not skipped again), and once it's going and planned again later its
    plug-in stays un-skipped (not brought back as one of the skips it made before now)."""
    a, clock = _auto(at(12, 25))
    a.set_schedule(enabled=True, entries=[{"time": "17:30", "action": "plug", "days": WEEK},
                                          {"time": "19:30", "action": "unplug", "days": WEEK}])
    a.sync_auto_schedule([], user_exp(), 4)
    assert ("17:30", "19:30") not in _wins(a)
    e = {x["time"]: x["id"] for x in a.entries if not x.get("source")}
    a.set_skips([{"entry_id": e["17:30"], "date": "2026-11-03"}, {"entry_id": e["19:30"], "date": "2026-11-03"}], skip=False)
    assert ("17:30", "19:30") in _wins(a, after="12:00")
    assert a.sync_auto_schedule([], user_exp(), 4)  # (planned again around it)
    assert ("17:30", "19:30") in _wins(a, after="17:00")
    assert not any(k["date"] == "2026-11-03" and k["entry_id"] in e.values() for k in a.skips)
    clock.dt = at(18)
    a.sync_auto_schedule([], user_exp(), 4)
    clock.dt = at(19, 45)
    a.sync_auto_schedule([], user_exp(1, 0.1), 4)  # (new rates: planned again)
    assert not any(k["date"] == "2026-11-03" and k["entry_id"] in e.values() for k in a.skips)
    assert ("17:30", "19:30") in _wins(a, after="17:00")


def test_while_off_it_shows_what_it_would_do():
    """Off: what it would do is worked out (its charging, your slots that would give way) and
    the schedule left as it was; the same as it does once it's turned on. On, there's none."""
    a, clock = _auto(at(12, 25))
    a.set_schedule(enabled=True, entries=[{"time": "17:30", "action": "plug", "days": WEEK},
                                          {"time": "19:30", "action": "unplug", "days": WEEK}])
    before = (copy.deepcopy(a.entries), copy.deepcopy(a.skips), a.auto_plan, copy.deepcopy(a.saving_sessions))
    assert a.preview_auto_schedule([], user_exp(), 4)
    assert (a.entries, a.skips, a.auto_plan, a.saving_sessions) == before
    p = a.auto_preview
    assert brief_iso(p["charge"]) == [("13:00", "13:30"), ("22:30", "00:00")]
    assert p["gave_way"] == ["Tue 17:30–19:30 skipped"]
    assert not a.preview_auto_schedule([], user_exp(), 4)  # (nothing changed: not again)
    assert a.snapshot()["schedule"]["auto_preview"] == p
    a.sync_auto_schedule([], user_exp(), 4)
    assert brief_iso(a.auto_plan["charge"]) == brief_iso(p["charge"]) and a.auto_plan["gave_way"] == p["gave_way"]
    assert a.preview_auto_schedule([], user_exp(), 4) and a.auto_preview is None
    assert a.snapshot()["schedule"]["auto_preview"] is None


def brief_iso(wins):
    return [(x[11:16], y[11:16]) for x, y in wins]


def test_tomorrows_rates_the_same_as_todays_are_used_from_the_supplier():
    """Octopus Energy's next day rates the same as today's (e.g. a tariff with the same times
    each day): they're real, so tomorrow is planned too (not all of it crammed into this
    evening). Predbat's: a copy of today is its guess, so tomorrow isn't."""
    octo = {"export": ["event.octopus_energy_electricity_x_y_export_current_day_rates",
                       "event.octopus_energy_electricity_x_y_export_next_day_rates"], "source": "Octopus Energy"}
    a, _ = _auto(at(16, 10))
    a.sync_auto_schedule([], user_exp(2), 4, info=octo)
    assert any(_parse(x).date() != D.date() for x, _ in a.auto_plan["charge"])
    pb, _ = _auto(at(16, 10))
    pb.sync_auto_schedule([], user_exp(2), 4, info={"export": ["predbat.rates_export"], "source": "Predbat"})
    assert all(_parse(x).date() == D.date() for x, _ in pb.auto_plan["charge"])
    assert A.plan([], user_exp(2), at(16, 10), 4, copies=False)["charge"] != A.plan([], user_exp(2), at(16, 10), 4)["charge"]


def _parse(x):
    return datetime.datetime.fromisoformat(x)


def _cheap_from(lo, hi):
    """Export rates from 09:00 today: 2p from lo to hi (hours), dearer otherwise."""
    out = []
    for k in range(30):
        t = at(9, 30 * k)
        out.append((t, t + A.HH, 2.0 if at(*lo) <= t < at(*hi) else 20.0 + k, False))
    return out


def test_a_block_longer_than_the_limit_is_split_where_it_resets():
    """With the ready time and Octopus's 6 hours from 12:00: its 09:00-15:30 (6½ hours, across
    12:00) unplugs and plugs back in at 12:00, as your slots are, so the supplier sees two
    charges (ready times 12:00 and 15:30). 10:00-14:00 (within the limit) stays one charge.
    Turned off, the split goes with it."""
    a, _ = _auto(at(9))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, ready_time=True, entries=[], daily_cap_min=360)
    a.sync_auto_schedule([], _cheap_from((9,), (15, 30)), 7, cap_min=360)
    assert _wins(a) == [("09:00", "12:00"), ("12:00", "15:30")]
    assert all(e.get("source") == AUTO_SCHED_SOURCE for e in a.entries)
    assert a.next_unplug(at(9, 5)) == at(12)
    a.clear_auto_schedule()
    assert a.entries == []
    b, _ = _auto(at(9))
    b.limit_reset = "12:00"
    b.set_schedule(enabled=True, ready_time=True, entries=[], daily_cap_min=360)
    b.sync_auto_schedule([], _cheap_from((10,), (14,)), 4, cap_min=360)
    assert _wins(b) == [("10:00", "14:00")] and b.next_unplug(at(10, 5)) == at(14)


def test_a_block_joined_to_your_slot_is_split_too():
    """Your 06:00-09:00 (rates not known then) with its block from 09:00 joined on: over 6
    hours across 12:00, so split there."""
    a, _ = _auto(at(5))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, ready_time=True, daily_cap_min=360,
                   entries=[{"time": "06:00", "action": "plug", "days": WEEK}, {"time": "09:00", "action": "unplug", "days": WEEK}])
    a.sync_auto_schedule([], _cheap_from((9,), (13,)), 8, cap_min=360)
    assert _wins(a) == [("06:00", "12:00"), ("12:00", "15:00")] and a.next_unplug(at(6, 5)) == at(12)


def test_the_export_rates_for_the_page():
    """The export rates known (from yesterday on), for the schedule on the page, whether it's on or
    off; a Predbat copy of the day before isn't one of them. Changed only when they change."""
    a, _ = _auto(at(12, 25))
    assert a.set_export_rates(user_exp(2), {"export": ["event.octopus_energy_x_export_current_day_rates"]})
    assert len(a.export_rates) == 2 * len(USER_EXP) and a.export_rates[0][2] == USER_EXP["12:30"]
    assert a.snapshot()["schedule"]["export_rates"] == a.export_rates
    assert not a.set_export_rates(user_exp(2), {"export": ["event.octopus_energy_x_export_current_day_rates"]})
    assert a.set_export_rates(user_exp(2), {"export": ["predbat.rates_export"]}) and len(a.export_rates) == len(USER_EXP)


def test_your_slots_stay_given_way_while_a_block_is_going():
    """Its block 05:30-11:30 going (6 hours, the day's limit from 04:00) and your 12:00-13:30 and
    17:30-19:30 skipped for it; a Charge now then: it isn't planned again until the block ends, so
    your slots stay skipped till then (they'd take the day over its limit)."""
    def exp():
        out = []
        for k in range(40):
            t = at(4, 30 * k)
            out.append((t, t + A.HH, 2.0 if at(5, 30) <= t < at(11, 30) else 20.0 + k, False))
        return out
    a, clock = _auto(at(5, 10))
    a.limit_reset = "04:00"
    a.set_schedule(enabled=True, ready_time=True, daily_cap_min=360, entries=[
        {"time": "12:00", "action": "plug", "days": WEEK}, {"time": "13:30", "action": "unplug", "days": WEEK},
        {"time": "17:30", "action": "plug", "days": WEEK}, {"time": "19:30", "action": "unplug", "days": WEEK}])
    a.sync_auto_schedule([], exp(), 12, cap_min=360)
    assert ("05:30", "11:30") in _wins(a) and ("12:00", "13:30") not in _wins(a)
    clock.dt = at(5, 52)
    a.plan_auto_plug(60, cap_min=360, source=CHARGE_NOW_SOURCE)
    a.sync_auto_schedule([], exp(), 12, cap_min=360)
    assert ("12:00", "13:30") not in _wins(a) and ("17:30", "19:30") not in _wins(a)
    assert _day_minutes(a, at(4), at(4, 0, 1)) <= 360


def test_the_high_export_half_hours_are_marked():
    """Each limit day's highest export rates asked for (12:00 to 12:00): marked only, the charging
    goes where exporting pays least as before. 0: none. The day from 12:00 yesterday, whose
    afternoon isn't known (only this morning's rates), isn't marked; it is once it's been seen."""
    a, clock = _auto(at(12, 25))
    a.limit_reset = "12:00"
    assert a.set_export_rates(user_exp(morning=5), {}, high=4)
    assert [x[0][11:16] for x in a.high_rates] == ["16:30", "17:00", "17:30", "18:00"]
    clock.dt = at(9, 0, 1)  # (tomorrow morning: only its own rates now, yesterday's remembered)
    a.set_export_rates([(at(0, 30 * k, 1), at(0, 30 * k + 30, 1), 50.0 if k == 20 else 10.0, False) for k in range(48)], {}, high=4)
    # (the day from Tue 12:00: its afternoon's remembered, so this morning's 50p at 10:00 is one of its 4)
    assert [x[0][5:16] for x in a.high_rates if x[0] < "2026-11-04T12"] == ["11-03T16:30", "11-03T17:30", "11-03T18:00", "11-04T10:00"]
    assert a.snapshot()["schedule"]["high_rates"] == a.high_rates
    assert a.set_export_rates(user_exp(), {}, high=0) and a.high_rates == []
    assert not a.set_export_rates(user_exp(), {}, high=0)
    b, _ = _auto(at(12, 25))
    b.set_export_rates(user_exp(), {}, high=4)
    b.sync_auto_schedule([], user_exp(), 8)  # (marked only: the charging as before)
    assert brief_iso(b.auto_plan["charge"]) == [("12:30", "15:00"), ("22:30", "00:00")]


def test_charging_before_the_high_export_times():
    """Before mode, 4 high export half hours marked (16:30-18:30) and 8 of charging: a charge
    straight before the run, as long as it, 14:30-16:30; nothing else (the charging is for the high
    export times). With 2 of charging: only the 2 half hours nearest it. Planned again when the
    mode changes."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    a.set_export_rates(user_exp(morning=5), {}, high=4)
    assert [x[0][11:16] for x in a.high_rates] == ["16:30", "17:00", "17:30", "18:00"]
    a.sync_auto_schedule([], user_exp(), 8, cap_min=360, mode="before")
    assert brief_iso(a.auto_plan["charge"]) == [("14:30", "16:30")]
    assert a.sync_auto_schedule([], user_exp(), 2, cap_min=360, mode="before")
    assert brief_iso(a.auto_plan["charge"]) == [("15:30", "16:30")]
    assert any("didn't fit" in n for n in a.auto_plan["notes"])
    assert a.sync_auto_schedule([], user_exp(), 2, cap_min=360)  # (cheapest again)
    assert brief_iso(a.auto_plan["charge"]) == [("23:00", "00:00")]


def test_before_the_high_export_times_two_runs_and_offpeak():
    """Two runs, 13:00-14:00 and 16:30-18:30 (marked by hand here): each gets its own charge before
    it, not reaching back past the run before (12:30 is the only room before 13:00), and not in an
    off-peak time; none marked: nothing planned, and the notes say why."""
    highs = [at(13), at(13, 30), at(16, 30), at(17), at(17, 30), at(18)]
    p = A.plan([], user_exp(), at(12, 25), 12, mode="before", highs=highs)
    assert brief(p["charge"]) == [("12:30", "13:00"), ("14:30", "16:30")]
    p = A.plan([], user_exp(), at(12, 25), 12, cuts=[(at(15), at(16))], mode="before", highs=highs[2:])
    assert brief(p["charge"]) == [("13:30", "15:00"), ("16:00", "16:30")]
    p = A.plan([], user_exp(), at(12, 25), 12, mode="before", highs=[])
    assert p["charge"] == [] and "no high export half hours marked" in p["notes"][0]


# --- found in the review -----------------------------------------------------------------------

def test_rates_after_the_clocks_change_are_put_at_the_right_time():
    """Sat 24 Oct 2026 20:00 BST (now's offset fixed, +01:00, as in the add-on), the clocks go back
    at 02:00 Sunday: export cheapest 13:00-14:00 GMT on Sunday (in UTC). Its charge plugs in at
    13:00 local (GMT), not 14:00 (which BST's offset would make it)."""
    from zoneinfo import ZoneInfo
    lon = ZoneInfo("Europe/London")
    bst = datetime.timezone(datetime.timedelta(hours=1))
    now = datetime.datetime(2026, 10, 24, 20, 0, tzinfo=bst)
    clock = Clock(now)
    a = Automation(None, ReplugOptions(), now=clock.now, clock=clock.epoch, localize=lambda naive: naive.replace(tzinfo=lon))
    utc = datetime.timezone.utc
    exp = []
    for k in range(48):
        t = datetime.datetime(2026, 10, 25, 0, 0, tzinfo=utc) + k * A.HH
        exp.append((t, t + A.HH, 2.0 if datetime.datetime(2026, 10, 25, 13, tzinfo=utc) <= t < datetime.datetime(2026, 10, 25, 14, tzinfo=utc) else 20.0, False))
    a.set_schedule(enabled=True, entries=[])
    a.sync_auto_schedule([], exp, 2)
    plug = next(e for e in a.entries if e["action"] == "plug")
    assert (plug["date"], plug["time"]) == ("2026-10-25", "13:00")
    assert a.set_export_rates(exp, {}) and a.export_rates[0][0] == "2026-10-25T01:00:00+01:00"


def test_before_a_high_run_already_under_way_isnt_short():
    """Before mode at 17:05, the high export run 16:30-18:30 under way: nothing to charge before
    it now, and no note that its charge didn't fit."""
    highs = [at(16, 30), at(17), at(17, 30), at(18)]
    p = A.plan([], user_exp(), at(17, 5), 8, mode="before", highs=highs)
    assert p["charge"] == [] and not any("didn't fit" in n for n in p["notes"])


def test_planned_again_when_the_ready_time_or_the_rates_source_changes():
    """Force schedule on supplier turned on (splits at the limit's reset) or the export rates
    now Predbat's (a copy of the day before is a guess): planned again."""
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, entries=[], daily_cap_min=360)
    assert a.sync_auto_schedule([], user_exp(2), 4, cap_min=360)
    assert not a.sync_auto_schedule([], user_exp(2), 4, cap_min=360)
    a.set_schedule(ready_time=True, daily_cap_min=360)
    assert a.sync_auto_schedule([], user_exp(2), 4, cap_min=360)
    assert a.sync_auto_schedule([], user_exp(2), 4, cap_min=360, info={"export": ["predbat.rates_export"]})
    # (and its rates known until: not into the copied day)
    assert a.auto_plan["rates_until"] == "2026-11-04T00:00:00+00:00"


def test_the_rates_seen_are_kept_over_a_restart(tmp_path):
    """The export rates seen (for the high ones of the limit day from 12:00 yesterday) are saved:
    after a restart in the morning, yesterday afternoon's still count."""
    def make(t):
        clock = Clock(t)
        return Automation(str(tmp_path), ReplugOptions(), now=clock.now, clock=clock.epoch,
                          localize=lambda naive: naive.replace(tzinfo=TZ))
    a = make(at(16))
    a.limit_reset = "12:00"
    a.set_export_rates(user_exp(morning=5), {}, high=4)
    b = make(at(9, 0, 1))
    b.limit_reset = "12:00"
    wed = [(at(0, 30 * k, 1), at(0, 30 * k + 30, 1), 10.0, False) for k in range(48)]
    b.set_export_rates(wed, {}, high=4)
    assert [x[0][5:16] for x in b.high_rates if x[0] < "2026-11-04T12"] == ["11-03T16:30", "11-03T17:00", "11-03T17:30", "11-03T18:00"]


def test_local_time_around_both_clock_changes():
    """Each half hour around the clocks going back (Oct) and forward (Mar), in UTC: put in local
    time with the offset then (the hour that comes twice, both ways round; none in the skipped
    hour), whatever now's offset."""
    from zoneinfo import ZoneInfo
    lon, utc = ZoneInfo("Europe/London"), datetime.timezone.utc
    for day in ((2026, 10, 25), (2027, 3, 28)):
        for now_off in (0, 1):
            now = datetime.datetime(*day, tzinfo=datetime.timezone(datetime.timedelta(hours=now_off))) - datetime.timedelta(hours=4)
            a = Automation(None, ReplugOptions(), now=lambda: now, localize=lambda naive: naive.replace(tzinfo=lon))
            for k in range(-24, 72):
                t = datetime.datetime(*day, tzinfo=utc) + k * A.HH
                got = a._to_local(t)
                assert got == t and got.utcoffset() == t.astimezone(lon).utcoffset(), (t, got)
