"""Tests for the Populate schedule plan (best charging times per weekday)."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.populate_schedule import DAYS, _merge_bins, build_plan

# Fixed now: Monday 2026-11-02 12:00:00 UTC (weekday 0)
NOW = datetime.datetime(2026, 11, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _ts(dt):
    return dt.timestamp()


def _stat(dt, mean, period_s=300):
    return {"start": _ts(dt), "mean": mean}


def test_merge_bins_basic():
    assert _merge_bins([]) == []
    assert _merge_bins([4]) == [(4, 5)]
    assert _merge_bins([1, 2, 3, 7, 8]) == [(1, 4), (7, 9)]
    assert _merge_bins([8, 7, 2, 1, 3]) == [(1, 4), (7, 9)]  # unsorted
    assert _merge_bins([2, 2, 3]) == [(2, 4)]  # duplicates


def test_weekday_isolation():
    # Monday 01:00 (bin 2) has high power; Tuesday 01:00 same clock time has low power.
    mon = datetime.datetime(2026, 10, 26, 1, 0, 0, tzinfo=datetime.timezone.utc)  # Mon
    tue = datetime.datetime(2026, 10, 27, 1, 0, 0, tzinfo=datetime.timezone.utc)  # Tue
    assert mon.weekday() == 0 and tue.weekday() == 1
    five = [_stat(mon, 5.0), _stat(tue, 0.5)]
    plan = build_plan(five, [], weeks=2, slots=1, now=NOW)
    assert plan["ok"] is True
    assert plan["days"]["Mon"][0]["start"] == "01:00"
    assert plan["days"]["Mon"][0]["avg_kw"] == 5.0
    # Tuesday picked its own 01:00 (only data it has), not influenced to be empty/different by Mon
    assert plan["days"]["Tue"][0]["start"] == "01:00"
    assert plan["days"]["Tue"][0]["avg_kw"] == 0.5
    # Wednesday has no data at all
    assert plan["days"]["Wed"] == []


def test_top_slots_and_merge_adjacent():
    mon = datetime.datetime(2026, 10, 26, 0, 0, 0, tzinfo=datetime.timezone.utc)
    five = [
        _stat(mon.replace(hour=1, minute=0), 3.0),   # bin 2
        _stat(mon.replace(hour=1, minute=30), 4.0),  # bin 3 (adjacent)
        _stat(mon.replace(hour=5, minute=0), 2.0),   # bin 10
        _stat(mon.replace(hour=2, minute=0), 1.0),   # bin 4, not in top 2 by power? top 3 slots
    ]
    plan = build_plan(five, [], weeks=2, slots=3, now=NOW)
    windows = plan["days"]["Mon"]
    # top 3 by avg: bin3(4.0), bin2(3.0), bin10(2.0) -> bins 2,3,10 merged => 01:00-02:00, 05:00-05:30
    assert windows[0]["start"] == "01:00" and windows[0]["end"] == "02:00"
    assert windows[1]["start"] == "05:00" and windows[1]["end"] == "05:30"


def test_slots_more_than_available_and_notes():
    mon = datetime.datetime(2026, 10, 26, 1, 0, 0, tzinfo=datetime.timezone.utc)
    plan = build_plan([_stat(mon, 2.0)], [], weeks=2, slots=5, now=NOW)
    assert len(plan["days"]["Mon"]) == 1
    assert any("Mon: only 1 slot with data" == n for n in plan["notes"])
    assert any("No Tue data" == n for n in plan["notes"])


def test_scale_applied():
    mon = datetime.datetime(2026, 10, 26, 1, 0, 0, tzinfo=datetime.timezone.utc)
    plan = build_plan([_stat(mon, 3500.0)], [], weeks=2, slots=1, scale=0.001, now=NOW)
    assert plan["days"]["Mon"][0]["avg_kw"] == 3.5


def test_extra_notes_preserved_and_empty():
    plan = build_plan([], [], weeks=2, slots=1, now=NOW, extra_notes=["Unit assumed"])
    assert "Unit assumed" in plan["notes"]
    assert "No history found for that sensor in the selected period" in plan["notes"]
    assert all(v == [] for v in plan["days"].values())


def test_slots_clamped():
    plan = build_plan([], [], weeks=999, slots=999, now=NOW)
    assert plan["weeks"] == 104 and plan["slots"] == 48


def test_weeks_from_before_the_clocks_changed_stay_at_their_local_time():
    from zoneinfo import ZoneInfo
    london = ZoneInfo("Europe/London")
    # Busy 18:00-19:00 local on four Mondays: two in BST (12 and 19 Oct), two in GMT (26 Oct, 2 Nov)
    rows = []
    for d in (datetime.date(2026, 10, 12), datetime.date(2026, 10, 19), datetime.date(2026, 10, 26), datetime.date(2026, 11, 2)):
        for h in range(24):
            for m in range(0, 60, 5):
                rows.append(_stat(datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=london), 5000 if h == 18 else 200))
    plan = build_plan(rows, [], weeks=5, slots=2, scale=0.001, now=datetime.datetime(2026, 11, 8, 12, tzinfo=london))
    assert plan["days"]["Mon"] == [{"start": "18:00", "end": "19:00", "start_day": 0, "end_day": 0, "avg_kw": 5.0}]


def test_weeks_and_slots_are_whole_numbers():
    import pytest
    from src.ha_link import validate_settings
    assert validate_settings({"populate_weeks": "6", "populate_slots": 12})["populate_slots"] == 12
    for bad in ({"populate_weeks": True}, {"populate_slots": 2.5}, {"populate_weeks": 0}, {"populate_slots": "x"}):
        with pytest.raises(ValueError):
            validate_settings(bad)


def _busy(hours_kw, days=(datetime.date(2026, 10, 26),)):
    """5-minute stats (W) for whole days in Europe/London: {hour: kW}, 0.2 kW otherwise."""
    from zoneinfo import ZoneInfo
    london = ZoneInfo("Europe/London")
    rows = []
    for d in days:
        for h in range(24):
            for m in range(0, 60, 5):
                rows.append(_stat(datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=london), hours_kw.get(h, 0.2) * 1000))
    return rows


def test_with_a_daily_limit_each_day_is_a_limit_day():
    from zoneinfo import ZoneInfo
    now = datetime.datetime(2026, 11, 8, 12, tzinfo=ZoneInfo("Europe/London"))
    # Monday 26 Oct busy 18:00-22:00 and Tuesday 27 Oct busy 07:00-09:00: Monday's limit day (12:00 to 12:00) has both
    rows = _busy({18: 3, 19: 3, 20: 3, 21: 3}) + _busy({7: 2, 8: 2}, days=(datetime.date(2026, 10, 27),))
    plan = build_plan(rows, [], weeks=4, slots=14, scale=0.001, now=now, day_start_min=720, cap_slots=12)
    assert plan["slots"] == 12 and any("over the daily limit" in n for n in plan["notes"])
    mon = plan["days"]["Mon"]
    assert [(w["start"], w["end"], w["start_day"]) for w in mon] == [("18:00", "22:00", 0), ("07:00", "09:00", 1)]
    assert plan["day_start"] == "12:00"
    # Without one (no supplier): midnight to midnight, and as many hours as asked
    plan = build_plan(rows, [], weeks=4, slots=14, scale=0.001, now=now)
    assert plan["slots"] == 14 and ("18:00", "22:00") in [(w["start"], w["end"]) for w in plan["days"]["Mon"]]
    assert ("07:00", "09:00") in [(w["start"], w["end"]) for w in plan["days"]["Tue"]]


def test_plan_entries_are_weekly_times_on_the_right_days():
    from src.populate_schedule import plan_entries
    plan = {"days": {"Mon": [{"start": "18:00", "end": "21:00", "start_day": 0, "end_day": 0},
                             {"start": "23:00", "end": "01:00", "start_day": 0, "end_day": 1}],
                     "Tue": [{"start": "18:00", "end": "21:00", "start_day": 0, "end_day": 0}],
                     "Sun": [{"start": "22:00", "end": "00:00", "start_day": 0, "end_day": 0}]}}
    entries = plan_entries(plan)
    assert {"time": "18:00", "action": "plug", "days": [0, 1]} in entries  # one entry for both days
    assert {"time": "01:00", "action": "unplug", "days": [1]} in entries  # Monday's, past midnight: Tuesday
    assert {"time": "00:00", "action": "unplug", "days": [0]} in entries  # Sunday's ends at Monday 00:00


def test_add_to_schedule_replaces_your_slots_and_charges_take_their_time_from_it():
    from tests.test_automation import _fresh_cap, _slot
    from src.populate_schedule import plan_entries
    for trim, moved in (("start", "now starts at 20:00"), ("end", "now ends at 21:00")):
        a = _fresh_cap("12:00")  # Monday 14:00, Octopus
        a.trim_from = trim
        a.set_schedule(entries=_slot("01:00", "05:00", days=[1, 2]) + [
            {"time": "09:00", "action": "plug", "date": "2026-10-07"}, {"time": "10:00", "action": "unplug", "date": "2026-10-07"}],
            daily_cap_min=360)
        a.plan_auto_plug(180, cap_min=360)  # 14:00-17:00 today: an auto plug-in charge
        plan = {"days": {d: [{"start": "18:00", "end": "23:00", "start_day": 0, "end_day": 0}] for d in DAYS}}
        out = a.populate_schedule(plan_entries(plan), cap_min=360)
        yours = [e for e in a.entries if not e.get("source") and not e.get("date")]
        assert sorted(e["time"] for e in yours) == ["18:00", "23:00"] and yours[0]["days"] == list(range(7))
        assert not any(e.get("date") == "2026-10-07" and not e.get("source") for e in a.entries)  # one-offs of yours gone too
        assert any(e.get("source") == "auto_plug" for e in a.entries)  # the charge stays
        # (your 4 times, and the one-off plug-in the charge had added when it trimmed the 01:00 slot)
        assert out["removed"] == 5 and any(moved in n for n in out["notes"]), out
        assert a._longest_day(a.entries)[0] == 360  # back within the limit
        # Deleting the charge puts tonight's slot back as it was
        a.set_schedule(entries=[e for e in a.entries if e.get("source") != "auto_plug"])
        assert not a.skips and a._longest_day(a.entries)[0] == 300


def test_add_to_schedule_with_no_supplier():
    from tests.test_automation import _automation, _slot
    from src.populate_schedule import plan_entries
    a, _ = _automation(start=datetime.datetime(2026, 10, 5, 14, 0, tzinfo=datetime.timezone.utc))
    a.set_schedule(enabled=True, entries=_slot("01:00", "05:00"))
    plan = {"days": {"Mon": [{"start": "17:00", "end": "23:30", "start_day": 0, "end_day": 0}]}}
    out = a.populate_schedule(plan_entries(plan))  # no limit: 6.5 hours is fine
    assert out == {"added": 1, "removed": 2, "notes": []}
    assert [(e["time"], e["days"]) for e in a.entries] == [("17:00", [0]), ("23:30", [0])]


def test_limit_day_from_the_supplier():
    from src.populate_schedule import limit_day
    assert limit_day({}) == (0, None)  # no supplier: midnight to midnight
    assert limit_day({"found": False, "limit_reset": "12:00", "limit_min": 360}) == (0, None)
    assert limit_day({"found": True, "limit_reset": "12:00", "limit_min": 360}) == (720, 12)  # Octopus
    assert limit_day({"found": True, "limit_reset": "24h", "limit_min": 360}) == (0, 12)  # rolling: from midnight
    assert limit_day({"found": True, "limit_reset": "rolling", "limit_min": None}) == (0, None)


def test_off_peak_times_are_left_out():
    from src.populate_schedule import blocked_bins
    # 23:30-05:30 every night: Mon 23:30 is Monday's bin 47, Tue 05:00 is Tuesday's bin 10
    off = blocked_bins([{"start": "23:30", "end": "05:30", "days": [0]}])
    assert (0, 47) in off and (1, 0) in off and (1, 10) in off and (1, 11) not in off and (0, 46) not in off
    # By Octopus's limit day (from 12:00) Monday 23:30 is Monday's bin 23; 05:15-05:45 touches two
    off = blocked_bins([{"start": "05:15", "end": "05:45", "days": [1]}], day_start_min=720)
    assert off == {(0, 34), (0, 35)}
    # The busiest half hours in them are dropped (not swapped for others), with a note
    now = datetime.datetime(2026, 11, 9, 12, 0, tzinfo=datetime.timezone.utc)
    rows = []
    for d in range(1, 15):
        day = now - datetime.timedelta(days=d)
        for b in range(48):
            start = day.replace(hour=b // 2, minute=30 * (b % 2))
            rows.append({"start": start.timestamp(), "mean": 7.0 if b < 12 else 0.5})
    plan = build_plan([], rows, weeks=2, slots=4, now=now, blocks=[{"start": "00:00", "end": "01:00", "days": list(range(7))}])
    mon = plan["days"]["Mon"]
    assert mon and all(w["start"] >= "01:00" for w in mon)  # nothing in the off-peak time
    half_hours = sum((int(w["end"][:2]) * 60 + int(w["end"][3:]) - int(w["start"][:2]) * 60 - int(w["start"][3:])) // 30 for w in mon)
    assert half_hours < 4  # dropped, not swapped for the next busiest
    assert any(n.startswith("Left out, in your off-peak times") and "Mon" in n for n in plan["notes"])
