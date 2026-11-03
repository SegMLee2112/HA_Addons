"""The automatic schedule's planner (src/auto_schedule.py)."""

import datetime

from src import auto_schedule as A

TZ = datetime.timezone.utc
D = datetime.datetime(2026, 11, 3, tzinfo=TZ)


def at(h, m=0, day=0):
    return D + datetime.timedelta(days=day, hours=h, minutes=m)


def rates(prices: dict, base=10.0, days=1):
    """{(h, m): p} on a flat `base`: half hours for `days` days from midnight."""
    out = []
    for n in range(48 * days):
        t = D + n * A.HH
        out.append((t, t + A.HH, prices.get((t.hour, t.minute) if t.day == D.day else None, base), False))
    return out


def brief(windows):
    return [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b, *_ in windows]


def test_the_users_example():
    # Highest exports 16:00-17:30, then 18:00-19:00: 5 half hours
    exp = rates({(16, 0): 40, (16, 30): 40, (17, 0): 40, (18, 0): 35, (18, 30): 35})
    p = A.plan([], exp, at(9), 5)
    assert brief(p["exports"]) == [("16:00", "16:30"), ("16:30", "17:00"), ("17:00", "17:30"),
                                   ("18:00", "18:30"), ("18:30", "19:00")]
    assert brief(p["charge"]) == [("14:00", "16:00"), ("17:30", "18:00")]
    assert p["notes"] == []


def test_one_run_charge_matches_its_length():
    exp = rates({(17, 0): 30, (17, 30): 31})
    p = A.plan([], exp, at(9), 2)
    assert brief(p["charge"]) == [("16:00", "17:00")]


def test_too_soon_to_charge_before_is_passed_over():
    exp = rates({(9, 30): 50, (15, 0): 30})
    p = A.plan([], exp, at(9, 10), 1)  # 09:30 can't be charged for (09:30 is the earliest)
    assert brief(p["exports"]) == [("15:00", "15:30")] and brief(p["charge"]) == [("14:30", "15:00")]


def test_offpeak_from_import_and_kept_clear():
    # Intelligent Octopus Go: 7p 23:30-05:30, 30p otherwise, today and tomorrow
    imp = []
    for n in range(96):
        t = D + n * A.HH
        cheap = (t.hour, t.minute) >= (23, 30) or (t.hour, t.minute) < (5, 30)
        imp.append((t, t + A.HH, 7.0 if cheap else 30.0, False))
    assert brief(A.offpeak(imp, at(9))) == [("23:30", "05:30")]
    exp = [(a, b, 60.0 if (a.hour, a.minute) == (23, 30) and a.day == D.day else 20.0 if (a.hour, a.minute) == (12, 0) and a.day == D.day else 5.0, False)
           for a, b, _, _ in imp]
    p = A.plan(imp, exp, at(9), 1)
    assert brief(p["exports"])[0] == ("12:00", "12:30")  # not the off-peak 23:30
    assert all(a.hour != 23 for a, _, _ in p["exports"])
    assert brief(p["offpeak"]) == [("23:30", "05:30")]


def test_dispatch_adjusted_import_isnt_offpeak():
    imp = [(a, b, 7.0 if 15 <= a.hour < 17 else 30.0, 15 <= a.hour < 17) for a, b, _, _ in rates({})]
    assert A.offpeak(imp, at(9)) == []


def test_daily_limit():
    exp = rates({(h, m): 50 - h for h in range(13, 20) for m in (0, 30)})
    day = lambda t: t.date()
    p = A.plan([], exp, at(9), 12, cap_min=120, day_of=day, used=lambda d: 60)
    assert sum((b - a).seconds for a, b in p["charge"]) == 60 * 60  # 2 h less the hour already used


def test_blocks_are_per_day():
    exp = rates({}, days=2)
    exp = [(a, b, 40.0 if a.hour == 17 else 10.0, adj) for a, b, _, adj in exp]  # 17:00-18:00 best each day
    p = A.plan([], exp, at(9), 2)
    assert [(a.strftime("%d %H:%M"), b.strftime("%H:%M")) for a, b in p["charge"]] == [("03 16:00", "17:00"), ("04 16:00", "17:00")]


def test_cuts():
    exp = rates({(17, 0): 30})
    p = A.plan([], exp, at(9), 1, cuts=[(at(16, 30), at(17))])
    assert brief(p["charge"]) == [("16:00", "16:30")]


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


# --- in the schedule (src/automation.py) ------------------------------------------

from src.automation import AUTO_SCHED_SOURCE, CHARGE_NOW_SOURCE, Automation, ReplugOptions  # noqa: E402


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


def _wins(a, day=0):
    d = (D + datetime.timedelta(days=day)).date()
    return [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.windows() if x.date() == d]


EXP = rates({(16, 0): 40, (16, 30): 40, (17, 0): 40, (18, 0): 35, (18, 30): 35})
WEEK = list(range(7))


def test_blocks_go_in_the_schedule():
    a, _ = _auto()
    assert a.sync_auto_schedule([], EXP, 5)
    assert _wins(a) == [("14:00", "16:00"), ("17:30", "18:00")]
    assert all(e.get("source") == AUTO_SCHED_SOURCE for e in a.entries)
    assert not a.sync_auto_schedule([], EXP, 5)  # (nothing changed: not planned again)
    assert a.clear_auto_schedule() and a.entries == [] and a.skips == []


def test_joined_to_a_slot_it_meets():
    a, _ = _auto()
    a.set_schedule(enabled=True, entries=[{"time": "16:00", "action": "plug", "days": WEEK},
                                          {"time": "21:30", "action": "unplug", "days": WEEK}])
    a.sync_auto_schedule([], EXP, 3)
    assert _wins(a) == [("14:30", "21:30")]  # 14:30-16:00 joined to 16:00-21:30
    assert _wins(a, 1) == [("16:00", "21:30")]
    a.clear_auto_schedule()
    assert _wins(a) == [("16:00", "21:30")] and a.skips == []


def test_takes_time_off_your_slot_in_a_day_it_takes_over():
    a, _ = _auto()
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, entries=[{"time": "19:30", "action": "plug", "days": WEEK},
                                          {"time": "23:30", "action": "unplug", "days": WEEK}])
    a.sync_auto_schedule([], EXP, 5, cap_min=360)  # 2.5h + your 4h: 30 min off yours
    assert [w for w in _wins(a) if w[0] >= "12:00"] == [("14:00", "16:00"), ("17:30", "18:00"), ("19:30", "23:00")]
    assert any("now ends at 23:00" in n for n in a.auto_plan["notes"])
    assert a._longest_day(a._active_entries(), a.skips)[0] <= 360
    a.clear_auto_schedule()
    assert _wins(a) == [("19:30", "23:30")] and a.skips == []


def test_charges_come_first():
    a, clock = _auto()
    a.limit_reset = "12:00"
    a.sync_auto_schedule([], EXP, 5, cap_min=360)
    clock.dt = at(10)
    a.plan_auto_plug(300, cap_min=360, source=CHARGE_NOW_SOURCE)  # Charge now, 5 hours from 10:00
    assert not any(e.get("source") == AUTO_SCHED_SOURCE for e in a.entries)  # its blocks are out
    assert a.sync_auto_schedule([], EXP, 5, cap_min=360)  # planned again around it
    total = sum((y - x).seconds for x, y in a.windows() if x.date() == D.date() and x >= at(12))
    assert total <= 360 * 60


def test_dry_run_leaves_it():
    a, _ = _auto()
    a.sync_auto_schedule([], EXP, 5)
    before = (list(a.entries), list(a.skips))
    a.plan_auto_plug(60, source=CHARGE_NOW_SOURCE, dry_run=True)
    assert (a.entries, a.skips) == before and a.auto_plan["charge"]


def test_not_changed_while_a_block_is_going():
    a, clock = _auto()
    a.sync_auto_schedule([], EXP, 5)
    clock.dt = at(14, 10)
    new = rates({(20, 0): 90})
    assert not a.sync_auto_schedule([], new, 1)
    clock.dt = at(16, 5)
    assert a.sync_auto_schedule([], new, 1)
    assert _wins(a)[-1] == ("19:30", "20:00")


def test_offpeak_is_a_no_charge_time():
    a, _ = _auto()
    imp = [(t, t + A.HH, 7.0 if (t.hour, t.minute) >= (23, 30) or t.hour < 5 or (t.hour, t.minute) == (5, 0) else 30.0, False)
           for t in (D + n * A.HH for n in range(96))]
    a.sync_auto_schedule(imp, EXP, 1)
    assert [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.auto_offpeak()] == [("23:30", "05:30")]
    assert any(x.hour == 23 for x, _ in a.block_windows(D.date(), 2))


def _users_schedule(a):
    ents = []
    for p, u in (("06:30", "07:30"), ("08:30", "09:30"), ("15:30", "16:30"), ("17:30", "19:30"), ("21:30", "22:30")):
        ents += [{"time": p, "action": "plug", "days": WEEK}, {"time": u, "action": "unplug", "days": WEEK}]
    a.set_schedule(enabled=True, ready_time=True, entries=ents,
                   blocks=[{"start": "23:30", "end": "05:30", "days": WEEK}], daily_cap_min=360)


def test_twelve_half_hours_replace_your_slots_in_the_day():
    """The user's schedule (6 hours a day of slots), 12 half hours: the automatic blocks get
    the day's 6 hours, joined to, inside or between your slots, and your slots give way."""
    a, _ = _auto(at(12, 20))
    a.limit_reset = "12:00"
    _users_schedule(a)
    exp = [(a0, b0, 30.0 if 13 * 60 <= a0.hour * 60 + a0.minute < 16 * 60 + 30 or 19 * 60 + 30 <= a0.hour * 60 + a0.minute < 20 * 60 + 30
            else 15.0, False) for a0, b0, _, _ in rates({}, days=2)]
    a.sync_auto_schedule([], exp, 12, cap_min=360)
    worst = a._longest_day(a._active_entries(), a.skips)
    assert worst[0] <= 360
    day1 = at(12) + datetime.timedelta(days=1)
    auto = [(x, y) for x, y in a.windows() if at(12) <= x < day1]
    charge = [(A._when(x), A._when(y)) for x, y in a.auto_plan["charge"]]
    mine = sum((min(y, day1) - x).seconds for x, y in charge if at(12) <= x < day1)
    assert mine == 6 * 3600  # all 6 hours are the automatic schedule's
    assert sum((min(y, day1) - x).seconds for x, y in auto) == 6 * 3600  # and nothing else plugs in that day
    a.clear_auto_schedule()
    assert _wins(a) == [("06:30", "07:30"), ("08:30", "09:30"), ("15:30", "16:30"), ("17:30", "19:30"), ("21:30", "22:30")]
