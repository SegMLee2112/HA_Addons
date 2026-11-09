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
    assert plan["days"]["Mon"] == [{"start": "18:00", "end": "19:00", "avg_kw": 5.0}]


def test_weeks_and_slots_are_whole_numbers():
    import pytest
    from src.ha_link import validate_settings
    assert validate_settings({"populate_weeks": "6", "populate_slots": 12})["populate_slots"] == 12
    for bad in ({"populate_weeks": True}, {"populate_slots": 2.5}, {"populate_weeks": 0}, {"populate_slots": "x"}):
        with pytest.raises(ValueError):
            validate_settings(bad)
