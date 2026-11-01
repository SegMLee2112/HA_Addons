import datetime

from src.automation import SAVING_SOURCE, Automation, ReplugOptions, validate_entry
from src.saving_sessions import (find_calendar, find_sensor_sources, is_saving_calendar, merge, parse_events,
                                 sensor_events, supplier_of)

TZ = datetime.timezone.utc


class Clock:
    def __init__(self, start):
        self.dt = start

    def now(self):
        return self.dt

    def epoch(self):
        return self.dt.timestamp()


def _automation(tmp_path=None, start=datetime.datetime(2026, 10, 5, 12, 0, tzinfo=TZ)):  # Monday
    clock = Clock(start)
    a = Automation(str(tmp_path) if tmp_path else None, ReplugOptions(), now=clock.now, clock=clock.epoch,
                   localize=lambda naive: naive.replace(tzinfo=TZ))
    return a, clock


def _at(day, hh, mm=0):
    return datetime.datetime(2026, 10, day, hh, mm, tzinfo=TZ)


def _session(day, h1, h2, m1=0, m2=0):
    return {"start": _at(day, h1, m1), "end": _at(day, h2, m2), "summary": "Saving session"}


def _windows(a):
    return [(x.strftime("%d %H:%M"), y.strftime("%d %H:%M")) for x, y in a.windows() if y > a._now()]


# --- finding the calendar and reading it -----------------------------------------


def test_calendars_found_by_name():
    assert is_saving_calendar("calendar.octopus_energy_a_1_octoplus_power_down")
    assert is_saving_calendar("calendar.octopus_energy_a_1_octoplus_saving_sessions")
    assert not is_saving_calendar("calendar.octopus_energy_a_1_octoplus_power_up")
    assert not is_saving_calendar("calendar.octopus_energy_a_1_octoplus_free_electricity_session")
    assert is_saving_calendar("calendar.eon_next_saver_sessions")
    assert is_saving_calendar("calendar.x", "EDF Demand Flexibility")
    assert not is_saving_calendar("calendar.family") and not is_saving_calendar("sensor.saving_session")
    assert supplier_of("calendar.eon_next_saver_sessions") == "E.ON Next"
    states = [{"entity_id": "calendar.octopus_energy_a_1_octoplus_saving_sessions"},
              {"entity_id": "calendar.octopus_energy_a_1_octoplus_power_down"}, {"entity_id": "calendar.home"}]
    assert find_calendar(states) == "calendar.octopus_energy_a_1_octoplus_power_down"
    assert find_calendar([{"entity_id": "calendar.home"}]) is None


def test_events_parsed():
    now = _at(5, 12)
    resp = {"calendar.x": {"events": [
        {"start": "2026-10-05T17:00:00+00:00", "end": "2026-10-05T18:00:00+00:00", "summary": "Saving Session"},
        {"start": "2026-10-04T17:00:00+00:00", "end": "2026-10-04T18:00:00+00:00"},  # over
        {"start": "2026-10-06", "end": "2026-10-07"},  # all day
        {"start": "2026-10-05T17:30:00+00:00", "end": "2026-10-05T18:30:00+00:00"},  # overlaps: merged
    ]}}
    out = parse_events(resp, "calendar.x", now)
    assert [(s["start"].hour, s["end"].hour, s["end"].minute) for s in out] == [(17, 18, 30)]
    assert parse_events(None, "calendar.x", now) == []


def _axle(start, end, kind, prefix="sensor.axle_energy_event_"):
    return [{"entity_id": prefix + "start", "state": start}, {"entity_id": prefix + "end", "state": end},
            {"entity_id": prefix + "type", "state": kind}, {"entity_id": "sensor.other", "state": "1"}]


def test_axle_energy_events_from_its_sensors():
    now, ahead = _at(5, 12), datetime.timedelta(days=8)
    states = _axle("2026-10-05T19:00:00+00:00", "2026-10-05T20:00:00+00:00", "export")
    sources = find_sensor_sources(states)
    assert sources == [{"name": "Axle Energy", "types": ["export"], "start": "sensor.axle_energy_event_start",
                        "end": "sensor.axle_energy_event_end", "type": "sensor.axle_energy_event_type"}]
    out = sensor_events(states, sources, now, ahead)
    assert [(s["start"].hour, s["end"].hour, s["summary"]) for s in out] == [(19, 20, "Axle Energy event (export)")]
    # an import event (use more) isn't a saving session; nor is no event, or one that's over
    for start, end, kind in (("2026-10-05T19:00:00+00:00", "2026-10-05T20:00:00+00:00", "import"),
                             ("unknown", "unknown", "unknown"),
                             ("2026-10-05T09:00:00+00:00", "2026-10-05T10:00:00+00:00", "export"),
                             ("2026-10-20T19:00:00+00:00", "2026-10-20T20:00:00+00:00", "export")):  # too far ahead
        st = _axle(start, end, kind)
        assert sensor_events(st, find_sensor_sources(st), now, ahead) == []
    # unavailable (e.g. while the integration starts): not read, so nothing is taken as cancelled
    st = _axle("unavailable", "unavailable", "unavailable")
    assert sensor_events(st, find_sensor_sources(st), now, ahead) is None
    # named with something in front (a second install of the integration)
    st = _axle("2026-10-05T19:00:00+00:00", "2026-10-05T20:00:00+00:00", "export", "sensor.home_axle_energy_event_")
    assert find_sensor_sources(st)[0]["end"] == "sensor.home_axle_energy_event_end"
    assert find_sensor_sources([{"entity_id": "sensor.axle_energy_event_start"}]) == []  # no end sensor
    # the same session from a calendar and from Axle: one
    both = merge(out + [_session(5, 19, 20, m2=30)])
    assert [(s["start"].hour, s["end"].hour, s["end"].minute) for s in both] == [(19, 20, 30)]


# --- the schedule around a session --------------------------------------------------


def test_slot_keeps_its_end_and_starts_earlier():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    assert a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    assert _windows(a)[:2] == [("05 14:30", "05 18:00"), ("05 19:00", "05 21:30")]
    assert _windows(a)[2] == ("06 15:30", "06 21:30")  # only that day
    assert a.saving_sessions[0]["notes"][0] == "the 15:30–21:30 slot is now 14:30–18:00, 19:00–21:30"
    assert not a.sync_saving_sessions([_session(5, 18, 19)], block=True)  # nothing more to do
    # cancelled: put back
    assert a.sync_saving_sessions([], block=True)
    assert _windows(a)[0] == ("05 15:30", "05 21:30") and a.saving_sessions == []


def test_slots_moved_for_a_session_are_tagged_so_once_run():
    a, clock = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    clock.dt = _at(5, 22)
    runs = a.one_off_runs()
    assert [(r["start"][11:16], r["end"][11:16], r["source"]) for r in runs] == \
        [("19:00", "21:30", "saving_move"), ("14:30", "18:00", "saving_move")]


def test_slot_moved_earlier_joins_the_slot_before():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "13:00", "action": "plug"}, {"time": "14:00", "action": "unplug"},
                                          {"time": "14:30", "action": "plug"}, {"time": "18:30", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 17, 18)], block=True)
    assert _windows(a)[:2] == [("05 13:00", "05 17:00"), ("05 18:00", "05 18:30")]


def test_slot_starting_in_a_session_moves_whole():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "17:30", "action": "plug"}, {"time": "18:30", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 17, 19)], block=True)
    assert _windows(a)[0] == ("05 16:00", "05 17:00")  # ends before the session instead


def test_block_off_puts_slots_back_and_charges_avoid_sessions():
    a, clock = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "16:00", "action": "plug"}, {"time": "19:00", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 17, 18)], block=True)
    assert _windows(a)[:2] == [("05 15:00", "05 17:00"), ("05 18:00", "05 19:00")]
    a.sync_saving_sessions([_session(5, 17, 18)], block=False)
    assert _windows(a)[0] == ("05 16:00", "05 19:00")
    assert a.session_windows() == []
    # with it on, Charge now is planned around the session
    b, clock = _automation(start=_at(5, 16, 30))
    b.sync_saving_sessions([_session(5, 17, 18)], block=True)
    plan = b.plan_auto_plug(180)
    assert [(s["start"][11:16], s["end"][11:16]) for s in plan["segments"]] == [("16:30", "17:00"), ("18:00", "20:30")]  # the hour lost carries on after
    assert any("saving session" in n for n in plan["notes"])


def test_saving_a_schedule_isnt_refused_for_a_session():
    a, _ = _automation()
    a.sync_saving_sessions([_session(5, 17, 18)], block=True)
    a.set_schedule(enabled=True, entries=[{"time": "16:00", "action": "plug"}, {"time": "19:00", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 17, 18)], block=True)
    assert _windows(a)[:2] == [("05 15:00", "05 17:00"), ("05 18:00", "05 19:00")]


def test_session_already_running_unplugs_now():
    a, clock = _automation(start=_at(5, 16, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:00", "action": "plug"}, {"time": "18:00", "action": "unplug"}])
    clock.dt = _at(5, 16, 30)
    a.sync_saving_sessions([_session(5, 16, 17)], block=True)
    w = [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.windows() if y > _at(5, 15)]
    assert w[:2] == [("15:00", "16:31"), ("17:00", "18:29")]  # it had begun: carries on after its end
    assert "ends later" in a.saving_sessions[0]["notes"][0]


def test_turning_it_off_clears_everything():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "16:00", "action": "plug"}, {"time": "19:00", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 17, 18)], block=True)
    assert a.clear_saving_sessions()
    assert _windows(a)[0] == ("05 16:00", "05 19:00") and a.saving_sessions == [] and not a.saving_block
    assert not a.clear_saving_sessions()


def test_start_in_the_past_ends_later_instead():
    a, clock = _automation(start=_at(5, 12, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    clock.dt = _at(5, 14, 40)  # 14:30 has gone: from the next half hour
    a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    assert [w for w in _windows(a) if w[0].startswith("05")] == [("05 15:00", "05 18:00"), ("05 19:00", "05 22:00")]
    assert "starts at the next half hour and ends later" in a.saving_sessions[0]["notes"][0]


def test_found_after_the_next_half_hour_would_be_its_start_keeps_its_start():
    a, clock = _automation(start=_at(5, 12, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    clock.dt = _at(5, 15, 10)
    a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    assert [w for w in _windows(a) if w[0].startswith("05")] == [("05 15:30", "05 18:00"), ("05 19:00", "05 22:30")]
    assert "(ends later: starting earlier would be in the past)" in a.saving_sessions[0]["notes"][0]


def test_found_at_13_45_starts_at_14_00():
    a, clock = _automation(start=_at(5, 12, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    clock.dt = _at(5, 13, 45)
    a.sync_saving_sessions([_session(5, 18, 20)], block=True)
    assert [w for w in _windows(a) if w[0].startswith("05")] == [("05 14:00", "05 18:00"), ("05 20:00", "05 22:00")]


def test_ending_later_joins_the_next_slot():
    a, clock = _automation(start=_at(5, 12, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"},
                                          {"time": "22:30", "action": "plug"}, {"time": "23:30", "action": "unplug"}])
    clock.dt = _at(5, 15, 10)  # (its start kept)
    a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    assert [w for w in _windows(a) if w[0].startswith("05")] == [("05 15:30", "05 18:00"), ("05 19:00", "05 23:30")]
    assert "joined with the next slot" in a.saving_sessions[0]["notes"][0]


def test_ending_later_stops_at_a_short_off_peak_time_too():
    a, clock = _automation(start=_at(5, 12, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"},
                                          {"time": "23:00", "action": "plug"}, {"time": "23:30", "action": "unplug"}],
                   blocks=[{"start": "21:30", "end": "22:00"}])
    clock.dt = _at(5, 15, 10)
    a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    assert [w for w in _windows(a) if w[0].startswith("05")] == [
        ("05 15:30", "05 18:00"), ("05 19:00", "05 21:30"), ("05 23:00", "05 23:30")]


def _today(a, day=5):
    return [w for w in _windows(a) if w[0].startswith(f"{day:02d}")]


def test_charge_before_a_session_is_added_when_its_found(tmp_path):
    a, clock = _automation(tmp_path, start=_at(5, 12, 0))
    a.sync_saving_sessions([_session(5, 17, 18)], block=True, plug_hours=2)
    assert _today(a) == [("05 15:00", "05 17:00")]
    assert a.saving_sessions[0]["notes"] == ["charge before it: 15:00–17:00"]
    assert not a.sync_saving_sessions([_session(5, 17, 18)], block=True, plug_hours=2)  # done once
    # remembered after a restart
    b = Automation(str(tmp_path), ReplugOptions(), now=clock.now, clock=clock.epoch,
                   localize=lambda naive: naive.replace(tzinfo=TZ))
    assert b.saving_sessions[0]["pre"]["hours"] == 2 and b.saving_block
    # other hours: redone; off: taken out
    a.sync_saving_sessions([_session(5, 17, 18)], block=True, plug_hours=1)
    assert _today(a) == [("05 16:00", "05 17:00")]
    a.sync_saving_sessions([_session(5, 17, 18)], block=True, plug_hours=None)
    assert _today(a) == [] and a.saving_sessions[0]["notes"] == []


def test_charge_before_only_adds_what_the_schedule_doesnt_cover():
    a, clock = _automation(start=_at(5, 12, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 18, 19)], block=True, plug_hours=2)  # moved: 14:30-18:00, 19:00-21:30
    assert _today(a) == [("05 14:30", "05 18:00"), ("05 19:00", "05 21:30")]
    assert "already" in a.saving_sessions[0]["notes"][-1]
    # partly covered: joins the slot, adding only the missing hour
    b, clock = _automation(start=_at(5, 12, 0))
    b.set_schedule(enabled=True, entries=[{"time": "17:00", "action": "plug"}, {"time": "18:00", "action": "unplug"}])
    b.sync_saving_sessions([_session(5, 18, 19)], block=True, plug_hours=2)
    assert _today(b) == [("05 16:00", "05 18:00")]
    assert "16:00–17:00 (joined" in b.saving_sessions[0]["notes"][-1]


def test_charge_before_found_late_and_too_close():
    a, clock = _automation(start=_at(5, 16, 20))
    a.sync_saving_sessions([_session(5, 17, 18)], block=True, plug_hours=2)
    assert _today(a) == [("05 16:21", "05 17:00")]
    b, _ = _automation(start=_at(5, 16, 50))
    b.sync_saving_sessions([_session(5, 17, 18)], block=True, plug_hours=2)
    assert _today(b) == [] and "too close" in b.saving_sessions[0]["notes"][-1]


def test_charge_before_takes_the_daily_limit_off_the_next_slot():
    a, clock = _automation(start=_at(5, 12, 0))
    a.ready_time = True
    a.set_schedule(enabled=True, entries=[{"time": "19:00", "action": "plug"}, {"time": "23:00", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 18, 19)], block=True, plug_hours=3, cap_min=360)
    assert _today(a) == [("05 15:00", "05 18:00"), ("05 20:00", "05 23:00")]
    assert "starts at 20:00" in a.saving_sessions[0]["notes"][-1]


def test_charge_before_trims_your_slot_with_the_schedule_off_too():
    """2.50.5: with the schedule off the charge before a session is separate from your slots, but
    counts them for the daily limit (as if on) and trims the next one, as a car slot does."""
    a, clock = _automation(start=_at(5, 12, 0))
    a.ready_time = True
    a.set_schedule(enabled=False, entries=[{"time": "19:00", "action": "plug"}, {"time": "23:00", "action": "unplug"}])
    a.sync_saving_sessions([_session(5, 18, 19)], block=True, plug_hours=3, cap_min=360)
    assert "starts at 20:00" in a.saving_sessions[0]["notes"][-1]
    a.set_schedule(enabled=True)
    assert _today(a) == [("05 15:00", "05 18:00"), ("05 20:00", "05 23:00")]


def test_no_unplug_added_at_a_session_start():
    a, clock = _automation(start=_at(5, 12, 0))
    a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    assert a.entries == []
    a.sync_saving_sessions([_session(5, 18, 19)], block=True, plug_hours=1)
    assert len([e for e in a.entries if e["action"] == "unplug" and e["time"] == "18:00"]) == 1


def test_unplug_at_the_start_of_a_no_charging_time():
    a, clock = _automation(start=_at(5, 23, 20))
    a.set_schedule(blocks=[{"start": "23:30", "end": "05:30"}])
    a.sync_saving_sessions([_session(5, 23, 23, 40, 50)], block=True)
    assert a.block_starts_due() == []  # the first look
    clock.dt = _at(5, 23, 29)
    assert a.block_starts_due() == []
    clock.dt = _at(5, 23, 30)
    assert [t.strftime("%H:%M") for t in a.block_starts_due()] == ["23:30"]
    clock.dt = _at(5, 23, 45)
    assert a.block_starts_due() == []  # not again, and not for the saving session at 23:40



def test_auto_plug_in_just_before_a_session_carries_on_after_it():
    a, clock = _automation(start=_at(5, 12, 0))
    a.sync_saving_sessions([_session(5, 18, 19)], block=True)
    clock.dt = _at(5, 17, 55)
    plan = a.plan_auto_plug(180)
    assert [(s["start"][11:16], s["end"][11:16]) for s in plan["segments"]] == [("17:55", "18:00"), ("19:00", "22:00")]


def test_ending_later_stops_at_an_off_peak_time():
    """A 15:30-22:00 slot going when an 18:00-20:00 session cuts it: what's left
    goes after the session up to the off-peak time (23:30), not on past it to the
    next morning (the car can charge in the off-peak hours anyway)."""
    a, clock = _automation(start=_at(5, 12, 0))
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "22:00", "action": "unplug"}],
                   blocks=[{"start": "23:30", "end": "05:30"}])
    clock.dt = _at(5, 16, 20)
    a.sync_saving_sessions([_session(5, 18, 20)], block=True)
    assert [w for w in _windows(a) if w[0].startswith(("05", "06 0"))] == [("05 15:30", "05 18:00"), ("05 20:00", "05 23:30")]


def test_overlapping_sessions_are_one_for_the_schedule_but_keep_their_parts():
    """Octopus's 18:00-19:00 and Axle's 18:30-20:00: one 18:00-20:00 for the
    schedule, with each kept (parts) so the page shows which is running."""
    octo = {"start": _at(5, 18), "end": _at(5, 19), "summary": "Octopus Energy Saving Session"}
    axle = {"start": _at(5, 18, 30), "end": _at(5, 20), "summary": "Axle Energy event (export)"}
    one = merge([axle, octo, dict(octo)])  # (the same one twice: once)
    assert len(one) == 1 and (one[0]["start"].hour, one[0]["end"].hour) == (18, 20)
    assert [(p["start"].strftime("%H:%M"), p["summary"]) for p in one[0]["parts"]] == [
        ("18:00", "Octopus Energy Saving Session"), ("18:30", "Axle Energy event (export)")]
    assert "parts" not in merge([octo])[0]
    a, _ = _automation()
    a.sync_saving_sessions(one, block=True)
    snap = a.saving_snapshot()[0]
    assert [p["summary"] for p in snap["parts"]] == ["Octopus Energy Saving Session", "Axle Energy event (export)"]
    assert snap["parts"][1]["start"].startswith("2026-10-05T18:30")


# --- 2.66.4: the charge before a morning session, with a limit that resets at 12:00 ---

def _octopus_morning(found_at):
    """Octopus: 6 hours a day from 12:00. 15:30-21:30 every day (6 hours), off-peak
    23:30-05:30, and a saving session Sun 11 Oct 06:00-07:00 with 1 hour's charge
    before it: 05:30-06:00 (the off-peak time takes the rest). That half hour counts
    in Saturday's limit day (Sat 12:00 - Sun 12:00), which already has 6 hours."""
    a, clock = _automation(start=found_at)
    a.ready_time = True
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}],
                   blocks=[{"start": "23:30", "end": "05:30", "days": list(range(7))}])
    a.sync_saving_sessions([_session(11, 6, 7)], block=True, plug_hours=1, cap_min=360)
    return a


def test_morning_charge_before_comes_off_the_evening_before_in_the_same_limit_day():
    a = _octopus_morning(_at(10, 18, 0))  # found Sat 18:00: Saturday's slot is still running
    assert ("10 15:30", "10 21:00") in _windows(a)  # ends half an hour early
    assert ("11 05:30", "11 06:00") in _windows(a)  # the charge before it
    assert ("11 15:30", "11 21:30") in _windows(a)  # Sunday's (the next limit day): untouched
    assert "now ends at 21:00" in a.saving_sessions[0]["notes"][-1]


def test_morning_charge_before_not_added_when_the_evening_before_has_gone():
    a = _octopus_morning(_at(10, 22, 0))  # found Sat 22:00: nothing left in Saturday's day to shorten
    wins = _windows(a)
    assert ("11 05:30", "11 06:00") not in wins and ("11 15:30", "11 21:30") in wins
    note = a.saving_sessions[0]["notes"][-1]
    assert note.startswith("charge before it: not added") and "resets at 12:00" in note
    assert a.saving_sessions[0]["pre"].get("error") is True
    assert not a.sync_saving_sessions([_session(11, 6, 7)], block=True, plug_hours=1, cap_min=360)  # not tried again


def test_charge_before_a_session_after_12_00_comes_off_that_days_later_slot():
    """A session 14:00-15:00 with 2 hours before it (12:00-14:00): in the limit day
    from 12:00 with 15:30-21:30 (8 hours): that slot starts 2 hours later."""
    a, clock = _automation(start=_at(10, 9, 0))
    a.ready_time = True
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    a.sync_saving_sessions([_session(10, 14, 15)], block=True, plug_hours=2, cap_min=360)
    wins = _windows(a)
    assert ("10 12:00", "10 14:00") in wins and ("10 17:30", "10 21:30") in wins
    assert "now starts at 17:30" in a.saving_sessions[0]["notes"][-1]


def test_charge_before_not_added_when_the_limit_day_before_is_full():
    """11:00-12:00 is in the limit day from Friday 12:00, which had Friday's 6 hours
    (15:30-21:30): nothing in it is still to come, so the charge isn't added."""
    a, clock = _automation(start=_at(10, 9, 0))
    a.ready_time = True
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, entries=[{"time": "15:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}])
    a.sync_saving_sessions([_session(10, 12, 13)], block=True, plug_hours=1, cap_min=360)
    assert ("10 11:00", "10 12:00") not in _windows(a)
    assert a.saving_sessions[0]["notes"][-1].startswith("charge before it: not added")
