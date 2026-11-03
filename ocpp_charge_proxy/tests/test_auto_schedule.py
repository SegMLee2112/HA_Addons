"""The automatic schedule (src/auto_schedule.py, and in the schedule: src/automation.py)."""

import datetime

from src import auto_schedule as A
from src.automation import AUTO_SCHED_SOURCE, CHARGE_NOW_SOURCE, Automation, ReplugOptions, longest_day, minus

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
    """The best 12 are 15:00-20:00 and 20:30-21:30 (marked); the 12 half hours of charging go where
    exporting pays least, still free (from 12:30, now 12:25): only 11 are, so 330 of 360 min."""
    p = A.plan([], user_exp(), at(12, 25), 12)
    assert brief(p["exports"])[0] == ("15:00", "15:30") and len(p["exports"]) == 12
    assert brief(p["charge"]) == [("12:30", "15:00"), ("20:00", "20:30"), ("21:30", "00:00")]
    assert any("330 of 360 min" in n for n in p["notes"])


def test_charging_where_exporting_pays_least():
    p = A.plan([], user_exp(morning=5), at(9), 2)  # the best two: 17:30-18:30
    assert brief(p["exports"]) == [("17:30", "18:00"), ("18:00", "18:30")]
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
    assert _wins(a) == [("12:30", "15:00"), ("20:00", "20:30"), ("21:30", "00:00")]
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
    assert brief(p["charge"]) == [("13:00", "15:00"), ("20:00", "20:30"), ("21:30", "00:00")]
    assert all(a.date() == D.date() for a, _ in p["charge"])


def test_each_limit_day_gets_its_hours_within_its_limit():
    """Two days of rates, 12:00 to 12:00 limit days: each gets its 6 hours, none in its best exports
    (tomorrow's 12:00-18:00 peak is in the next limit day's)."""
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
    best = {A._when(x) for x, _, _ in a.auto_plan["exports"]}
    assert at(12, 0, 1) in best and at(17, 30, 1) in best
    assert not any(x < t + A.HH and y > t for x, y in a.windows() for t in best)

# --- found by the randomised checks ------------------------------------------------------



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


def test_a_saving_session_counts_as_a_best_export():
    """A saving session's half hours are ones of the best X, whatever the export price; the
    charge before it (busy) isn't charged in again: the 4 half hours go where it pays least."""
    sess = [(at(13, 50), at(14, 30))]  # (13:30 and 14:00: cheap export then)
    p = A.plan([], user_exp(), at(12, 25), 4, cuts=sess, busy=[(at(12, 50), at(13, 50))], sessions=sess)
    assert brief(p["exports"]) == [("13:30", "14:00"), ("14:00", "14:30"), ("17:30", "18:00"), ("18:00", "18:30")]
    assert brief(p["charge"]) == [("14:30", "15:00"), ("22:30", "00:00")]


def test_a_saving_session_in_the_schedule_counts():
    a, _ = _auto(at(12, 25))
    a.limit_reset = "12:00"
    a.sync_saving_sessions([{"start": at(13, 50), "end": at(14, 30), "summary": "Saving session"}], True, None,
                           360, "Octopus Energy", 1.0)
    a.sync_auto_schedule([], user_exp(), 4, cap_min=360)
    ex = [(A._when(x).strftime("%H:%M")) for x, _, _ in a.auto_plan["exports"]]
    assert ex == ["13:30", "14:00", "17:30", "18:00"]


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


def test_the_charge_before_a_saving_session_counts_where_export_pays():
    """The user's day at 14:55: a session 17:30-18:30 with an hour's charge before it. 16:30 and
    17:00 (31.42p, 30.89p) are charged in for it, but still count as ones of the best 12, so
    20:00 and 21:30 (19.51p, 19.68p) aren't added to make up the number."""
    a, _ = _auto(at(14, 55))
    a.limit_reset = "12:00"
    a.sync_saving_sessions([{"start": at(17, 30), "end": at(18, 30), "summary": "Saving session"}], True, None,
                           360, "Octopus Energy", 1.0)
    a.sync_auto_schedule([], user_exp(), 12, cap_min=360)
    ex = [A._when(x).strftime("%H:%M") for x, _, _ in a.auto_plan["exports"]]
    assert ex == ["15:00", "15:30", "16:00", "16:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30",
                  "20:30", "21:00"]
    assert not any(A._when(x) < at(17, 30) and A._when(y) > at(16, 30) for x, y in a.auto_plan["charge"])


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
