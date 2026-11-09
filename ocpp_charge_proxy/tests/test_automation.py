import asyncio
import datetime
import json

import pytest

from src.automation import Automation, ReplugOptions, automation_loop, validate_entry
from src.shared_state import SharedState

TZ = datetime.timezone.utc


class Clock:
    """Fake local time (UTC here) and epoch clock moving together."""

    def __init__(self, start: datetime.datetime):
        self.dt = start

    def now(self):
        return self.dt

    def epoch(self):
        return self.dt.timestamp()

    def advance(self, **kw):
        self.dt += datetime.timedelta(**kw)


def _automation(tmp_path=None, options=ReplugOptions(), start=datetime.datetime(2026, 10, 5, 23, 0, tzinfo=TZ)):
    clock = Clock(start)  # Monday 5 Oct 2026
    a = Automation(str(tmp_path) if tmp_path else None, options, now=clock.now, clock=clock.epoch,
                   localize=lambda naive: naive.replace(tzinfo=TZ))
    return a, clock


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- entries -----------------------------------------------------------------


def test_validate_entry():
    e = validate_entry({"time": "7:05", "action": "unplug", "days": [4, 0, 0]})
    assert e["time"] == "07:05" and e["days"] == [0, 4] and e["enabled"] is True and e["id"]
    assert validate_entry({"time": "23:30", "action": "plug"})["days"] == list(range(7))
    for bad in ({"time": "24:00", "action": "plug"}, {"time": "x", "action": "plug"},
                {"time": "10:00", "action": "toggle"}, {"time": "10:00", "action": "plug", "days": []},
                {"time": "10:00", "action": "plug", "days": [7]}):
        with pytest.raises(ValueError):
            validate_entry(bad)


# --- schedule ----------------------------------------------------------------


def test_due_fires_once_at_the_time():
    a, clock = _automation()
    a.set_schedule(enabled=True, entries=[
        {"time": "23:30", "action": "plug", "days": [0, 1, 2, 3, 4]},
        {"time": "07:00", "action": "unplug"},
    ])
    assert a.due() == []  # first check only sets the starting point
    clock.advance(minutes=29)
    assert a.due() == []
    clock.advance(seconds=75)  # 23:30:15
    assert [e["action"] for e in a.due()] == ["plug"]
    clock.advance(seconds=15)
    assert a.due() == []  # not again
    clock.advance(hours=7, minutes=29)  # Tue 06:59:30
    assert a.due() == []
    clock.advance(seconds=45)  # 07:00:15
    assert [e["action"] for e in a.due()] == ["unplug"]


def test_schedule_respects_days_and_disabled():
    a, clock = _automation(start=datetime.datetime(2026, 10, 10, 23, 29, tzinfo=TZ))  # Saturday
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug", "days": [0, 1, 2, 3, 4]}])
    a.due()
    clock.advance(minutes=2)
    assert a.due() == []  # weekdays only
    a.set_schedule(enabled=False)
    assert a.next_action() is None


def test_missed_times_are_not_caught_up_after_a_long_gap():
    a, clock = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug"}])
    a.due()
    clock.advance(hours=2)  # stopped / asleep through 23:30
    assert a.due() == []


def test_next_action():
    a, clock = _automation()  # Monday 23:00
    a.set_schedule(enabled=True, entries=[
        {"time": "07:00", "action": "unplug", "days": [1]},
        {"time": "23:30", "action": "plug", "days": [0]},
        {"time": "22:00", "action": "plug", "enabled": False},
    ])
    nxt = a.next_action()
    assert nxt["action"] == "plug" and nxt["time"].startswith("2026-10-05T23:30")
    clock.advance(minutes=31)
    assert a.next_action()["time"].startswith("2026-10-06T07:00")


def test_schedule_saved(tmp_path):
    a, _ = _automation(tmp_path)
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug"}])
    b, _ = _automation(tmp_path)
    assert b.schedule_enabled is True and b.entries[0]["time"] == "23:30"


def test_bad_schedule_rejected_and_unchanged(tmp_path):
    a, _ = _automation(tmp_path)
    a.set_schedule(entries=[{"time": "23:30", "action": "plug"}])
    with pytest.raises(ValueError):
        a.set_schedule(entries=[{"time": "99:00", "action": "plug"}])
    assert len(a.entries) == 1


# --- re-plug -----------------------------------------------------------------


def test_replug_after_wait_up_to_the_limit():
    a, clock = _automation(options=ReplugOptions(True, 10, 2))
    assert not a.replug_due(True, False, True)  # starts the wait
    clock.advance(minutes=9)
    assert not a.replug_due(True, False, True)
    assert a.replug_status()["status"] == "waiting"
    clock.advance(minutes=1)
    assert a.replug_due(True, False, True)

    calls = []

    async def unplug(source=None):
        calls.append("unplug")

    async def plug(source=None):
        calls.append("plug")

    _run(a.run_replug(unplug, plug, wait_s=0))
    assert calls == ["unplug", "plug"] and a.attempts_used == 1
    assert not a.replug_due(True, False, True)  # waits again from the re-plug
    clock.advance(minutes=10)
    assert a.replug_due(True, False, True)
    _run(a.run_replug(unplug, plug, wait_s=0))
    clock.advance(minutes=10)
    assert not a.replug_due(True, False, True)  # 2 tries used: give up
    assert a.replug_status()["status"] == "gave_up"


def test_replug_resets_when_session_starts_or_unplugged():
    a, clock = _automation()
    a.replug_due(True, False, True)
    a.attempts_used = 3
    a.gave_up = True
    assert not a.replug_due(True, True, True)  # session started
    assert a.attempts_used == 0 and not a.gave_up and a.replug_status()["status"] == "idle"
    a.attempts_used = 2
    assert not a.replug_due(False, False, True)  # unplugged
    assert a.attempts_used == 0


def test_replug_waits_only_while_online_and_enabled():
    a, clock = _automation()
    a.replug_due(True, False, True)
    clock.advance(minutes=9)
    assert not a.replug_due(True, False, False)  # offline: the wait restarts
    clock.advance(minutes=5)
    assert not a.replug_due(True, False, True)
    clock.advance(minutes=10)
    assert a.replug_due(True, False, True)
    a.set_replug(enabled=False)
    assert not a.replug_due(True, False, True)
    assert a.replug_status()["status"] == "off"


def test_replug_settings_saved_win_over_defaults(tmp_path):
    a, _ = _automation(tmp_path, ReplugOptions(True, 10, 3))
    assert a.replug["after_min"] == 10  # nothing saved: the defaults
    a.set_replug(after_min=15, attempts=5)
    b, _ = _automation(tmp_path, ReplugOptions(True, 20, 3))
    assert (b.replug["after_min"], b.replug["attempts"]) == (15, 5)


def test_replug_settings_validated():
    a, _ = _automation()
    for bad in ({"after_min": 0}, {"attempts": -1}, {"after_min": "x"}):
        with pytest.raises(ValueError):
            a.set_replug(**bad)


# --- the loop ----------------------------------------------------------------


def test_loop_unplugs_at_the_start_of_a_no_charging_time():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 23, 20, tzinfo=TZ))
    a.set_schedule(blocks=[{"start": "23:30", "end": "05:30"}])
    state = SharedState(connected_to_server=True, plugged_in=True)  # plugged in by hand
    sources = []

    async def plug(source=None):
        state.plugged_in = True

    async def unplug(source=None):
        sources.append(source)
        state.plugged_in = False

    async def scenario():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01))
        await asyncio.sleep(0.03)
        assert sources == []
        clock.advance(minutes=11)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
    assert sources == ["off-peak time"]


def test_loop_runs_schedule_and_publishes():
    a, clock = _automation()
    # (an unplug too, so 23:00 isn't inside a plugged-in stretch at start-up)
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug"}, {"time": "07:00", "action": "unplug"}])
    state = SharedState(connected_to_server=True)
    calls = []

    async def plug(source=None):
        calls.append("plug")
        state.plugged_in = True
        state.state = "Preparing"

    async def unplug(source=None):
        calls.append("unplug")

    async def scenario():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01))
        await asyncio.sleep(0.03)
        clock.advance(minutes=30)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
    assert calls == ["plug"]
    assert state.schedule_enabled is True
    assert state.replug["status"] == "waiting"
    assert state.schedule_next["action"] == "unplug"  # 07:00 next
    json.dumps(state.to_dict())  # serialisable for the API


def test_no_replug_while_supplier_has_a_slot_planned():
    a, clock = _automation(options=ReplugOptions(True, 10, 3))
    assert not a.replug_due(True, False, True, scheduled=False)  # plugged in, nothing planned yet
    clock.advance(minutes=5)
    # Octopus plans a slot for tonight: no re-plug, however long until it starts
    assert not a.replug_due(True, False, True, scheduled=True)
    assert a.replug_status()["status"] == "scheduled"
    clock.advance(hours=3)
    assert not a.replug_due(True, False, True, scheduled=True)
    # The plan is dropped: the wait starts again from now
    assert not a.replug_due(True, False, True, scheduled=False)
    assert a.replug_status()["status"] == "waiting"
    clock.advance(minutes=10)
    assert a.replug_due(True, False, True, scheduled=False)


def test_replug_without_supplier_info_waits_for_a_session():
    a, clock = _automation(options=ReplugOptions(True, 10, 3))
    a.replug_due(True, False, True, scheduled=None)
    clock.advance(minutes=10)
    assert a.replug_due(True, False, True, scheduled=None)


def test_automation_loop_publishes_scheduled():
    from src.automation import automation_loop
    from src.shared_state import SharedState, display_status
    a, clock = _automation()
    state = SharedState(state="Preparing", plugged_in=True, connected_to_server=True)

    async def noop(source=None):
        pass

    async def one_tick():
        task = asyncio.ensure_future(automation_loop(a, state, noop, noop, tick_s=60, scheduled=lambda: True))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(one_tick())
    assert state.scheduled is True and display_status(state) == "Scheduled"
    assert state.replug["status"] == "scheduled"
    state.state = "Charging"
    assert display_status(state) == "Charging"


def test_octopus_six_hours_a_day():
    every = list(range(7))
    six = [{"time": "23:30", "action": "plug", "days": every}, {"time": "05:30", "action": "unplug", "days": every}]
    seven = [{"time": "23:30", "action": "plug", "days": every}, {"time": "06:30", "action": "unplug", "days": every}]
    a, clock = _automation()
    a.set_schedule(entries=seven, daily_cap_min=360)  # ready time off: no limit
    a.set_schedule(entries=six, ready_time=True, daily_cap_min=360)  # exactly 6 hours: fine
    try:
        a.set_schedule(entries=seven, daily_cap_min=360, provider="Octopus Energy")
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "6 hours" in str(err) and "7h 00m" in str(err)
    assert a.entries[1]["time"] == "05:30"  # unchanged
    # Two 4-hour stretches 12 hours apart are 8 hours in 24
    split = [{"time": "00:00", "action": "plug", "days": every}, {"time": "04:00", "action": "unplug", "days": every},
             {"time": "12:00", "action": "plug", "days": every}, {"time": "16:00", "action": "unplug", "days": every}]
    try:
        a.set_schedule(entries=split, daily_cap_min=360)
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "8h 00m" in str(err)
    a.set_schedule(entries=split)  # no cap (not Octopus): fine
    a.set_schedule(ready_time=False)
    a.set_schedule(entries=seven, daily_cap_min=360)  # ready time off again: fine
    try:
        a.set_schedule(ready_time=True, daily_cap_min=360)  # can't turn it on while over
        raise AssertionError("should have refused")
    except ValueError:
        assert a.ready_time is False
    a.set_schedule(enabled=False, daily_cap_min=360)  # switching off is always allowed
    assert a.schedule_enabled is False


def test_in_plug_window():
    every = list(range(7))
    a, clock = _automation(start=datetime.datetime(2026, 10, 6, 2, 0, tzinfo=TZ))  # Tuesday 02:00
    a.set_schedule(enabled=True, entries=[
        {"time": "23:30", "action": "plug", "days": every}, {"time": "07:00", "action": "unplug", "days": every}])
    assert a.in_plug_window().strftime("%a %H:%M") == "Mon 23:30"
    clock.advance(hours=6)  # 08:00: unplugged by then
    assert a.in_plug_window() is None
    a.set_schedule(enabled=False)
    clock.advance(hours=16)  # 00:00, inside again, but the schedule is off
    assert a.in_plug_window() is None


def test_startup_plugs_in_inside_a_scheduled_stretch():
    from src.automation import automation_loop
    from src.shared_state import SharedState
    every = list(range(7))

    def run(plugged_in, ready_results):
        a, clock = _automation(start=datetime.datetime(2026, 10, 6, 2, 0, tzinfo=TZ))
        a.set_schedule(enabled=True, ready_time=True, entries=[
            {"time": "23:30", "action": "plug", "days": every}, {"time": "07:00", "action": "unplug", "days": every}])
        state = SharedState(plugged_in=plugged_in)
        calls, asked = [], []

        async def plug(source=None):
            calls.append(("plug", source))
            state.plugged_in = True

        async def unplug(source=None):
            calls.append(("unplug", source))

        async def set_ready(unplug_at):
            asked.append(unplug_at)
            return ready_results.pop(0) if ready_results else {"ready": "07:00"}

        async def ticks():
            task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
            await asyncio.sleep(0.1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        _run(ticks())
        return a, calls, asked

    a, calls, asked = run(False, [{"error": "Not connected to Home Assistant"}])
    assert calls == [("plug", "schedule")]
    assert len(asked) == 2 and a.ready_status["ready"] == "07:00"  # tried again once HA was there
    assert a.last_run["startup"] is True
    _, calls, asked = run(True, [])  # already plugged in: not plugged in again, but the ready time set
    assert calls == [] and len(asked) == 1  # (it may have failed before the restart)


# --- 2.9.0: one-off times and skips ---------------------------------------------


def test_one_off_time_runs_once_and_is_removed(tmp_path):
    a, clock = _automation(tmp_path)  # Monday 5 Oct 2026, 23:00
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug", "date": "2026-10-06"}])
    assert a.entries[0]["days"] == [1] and a.entries[0]["date"] == "2026-10-06"
    a.due()
    clock.advance(minutes=31)  # Monday 23:31: not today
    assert a.due() == []
    assert a.next_action()["time"].startswith("2026-10-06T23:30")
    clock.advance(days=1)  # Tuesday 23:31
    assert [e["action"] for e in a.due()] == ["plug"]
    clock.advance(days=13)  # kept for the Sessions tab...
    a.due()
    assert len(a.entries) == 1
    clock.advance(days=2)  # ... 14 days, then gone (and saved)
    a.due()
    assert a.entries == [] and Automation(str(tmp_path)).entries == []
    try:
        validate_entry({"time": "07:00", "action": "unplug", "date": "06/10/2026"})
        raise AssertionError("should have refused")
    except ValueError:
        pass


def _skip(a, entry_id, date, skip=True):
    """Skip (or un-skip) one upcoming time."""
    a.set_skips([{"entry_id": entry_id, "date": date}], skip)


def test_skip_one_time(tmp_path):
    every = list(range(7))
    a, clock = _automation(tmp_path)  # Monday 23:00
    a.set_schedule(enabled=True, entries=[
        {"time": "23:30", "action": "plug", "days": every}, {"time": "07:00", "action": "unplug", "days": every}])
    plug_id = a.entries[0]["id"]
    _skip(a, plug_id, "2026-10-05")  # tonight
    assert Automation(str(tmp_path)).skips == [{"entry_id": plug_id, "date": "2026-10-05"}]
    nxt = a.next_action()
    assert nxt["action"] == "unplug" and nxt["time"].startswith("2026-10-06T07:00")
    up = a.upcoming()
    assert up[0]["skipped"] is True and up[0]["action"] == "plug" and not up[1]["skipped"]
    a.due()
    clock.advance(minutes=31)
    assert a.due() == []  # skipped
    clock.advance(days=1)  # Tuesday 23:31: runs again
    assert [e["action"] for e in a.due()] == ["plug"]
    clock.advance(days=15)
    a.due()
    assert a.skips == []  # Monday's skip forgotten (after 14 days)
    _skip(a, plug_id, "2026-10-09")
    _skip(a, plug_id, "2026-10-09", skip=False)  # undo
    assert a.skips == []
    try:
        _skip(a, "nope", "2026-10-07")
        raise AssertionError("should have refused")
    except ValueError:
        pass


def test_skipped_unplug_moves_the_ready_time_and_start_up():
    every = list(range(7))
    a, clock = _automation(start=datetime.datetime(2026, 10, 6, 2, 0, tzinfo=TZ))  # Tuesday 02:00
    a.set_schedule(enabled=True, entries=[
        {"time": "23:30", "action": "plug", "days": every}, {"time": "07:00", "action": "unplug", "days": every}])
    plug_id, unplug_id = a.entries[0]["id"], a.entries[1]["id"]
    _skip(a, unplug_id, "2026-10-06")
    assert a.next_unplug(clock.now()).strftime("%a %H:%M") == "Wed 07:00"
    _skip(a, plug_id, "2026-10-05")  # last night's plug-in skipped: not inside a stretch
    assert a.in_plug_window() is None


def test_skip_a_whole_slot():
    every = list(range(7))
    a, clock = _automation()  # Monday 23:00
    a.set_schedule(enabled=True, entries=[
        {"time": "23:30", "action": "plug", "days": every}, {"time": "07:00", "action": "unplug", "days": every}])
    plug_id, unplug_id = a.entries[0]["id"], a.entries[1]["id"]
    slot = [{"entry_id": plug_id, "date": "2026-10-05"}, {"entry_id": unplug_id, "date": "2026-10-06"}]
    a.set_skips(slot)
    nxt = a.next_action()
    assert nxt["action"] == "plug" and nxt["time"].startswith("2026-10-06T23:30")  # the whole night skipped
    try:
        a.set_skips(slot + [{"entry_id": "nope", "date": "2026-10-07"}], skip=False)  # all or nothing
        raise AssertionError("should have refused")
    except ValueError:
        assert len(a.skips) == 2
    a.set_skips(slot, skip=False)
    assert a.skips == []


def test_graceful_unplug_waits_for_the_supplier():
    from src import automation as auto_mod
    from src.automation import automation_loop
    every = list(range(7))

    def run(ready_time, in_session, supplier_stops):
        a, clock = _automation(start=datetime.datetime(2026, 10, 6, 6, 59, tzinfo=TZ))
        a.set_schedule(enabled=True, ready_time=ready_time, entries=[
            {"time": "23:30", "action": "plug", "days": every}, {"time": "07:00", "action": "unplug", "days": every}])
        state = SharedState(plugged_in=True, transaction_id=1 if in_session else None)
        calls = []

        async def plug(source=None):
            calls.append("plug")

        async def unplug(source=None):
            calls.append(("unplug", round(clock.epoch() - start)))
            state.plugged_in = False

        start = 0

        async def scenario():
            nonlocal start
            task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01))
            await asyncio.sleep(0.03)
            clock.advance(minutes=1, seconds=1)  # 07:00:01: unplug due
            start = clock.epoch()
            await asyncio.sleep(0.05)
            waiting = a.unplug_at is not None
            if supplier_stops:
                clock.advance(seconds=20)
                state.transaction_id = None  # RemoteStop
            else:
                clock.advance(seconds=61)
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return waiting

        old = auto_mod.PENDING_TICK_S
        auto_mod.PENDING_TICK_S = 0.01
        try:
            waiting = _run(scenario())
        finally:
            auto_mod.PENDING_TICK_S = old
        return calls, waiting

    assert run(False, True, False) == ([("unplug", 0)], False)  # ready time off: straight away
    assert run(True, False, False) == ([("unplug", 0)], False)  # no session: straight away
    assert run(True, True, True) == ([("unplug", 20)], True)  # supplier stopped it after 20 s
    assert run(True, True, False) == ([("unplug", 61)], True)  # gave it a minute


def test_unplug_wait_setting(tmp_path):
    a, _ = _automation(tmp_path)
    assert a.unplug_wait_s == 60 and a.snapshot()["schedule"]["unplug_wait_s"] == 60
    a.set_schedule(unplug_wait_s=120)
    assert _automation(tmp_path)[0].unplug_wait_s == 120  # saved
    for bad in (-1, 601, "x"):
        try:
            a.set_schedule(unplug_wait_s=bad)
            raise AssertionError("accepted %r" % (bad,))
        except ValueError:
            pass
    assert a.unplug_wait_s == 120


def test_unplug_wait_zero_unplugs_straight_away():
    every = list(range(7))
    a, clock = _automation(start=datetime.datetime(2026, 10, 6, 6, 59, tzinfo=TZ))
    a.set_schedule(enabled=True, ready_time=True, unplug_wait_s=0, entries=[
        {"time": "23:30", "action": "plug", "days": every}, {"time": "07:00", "action": "unplug", "days": every}])
    state = SharedState(plugged_in=True, transaction_id=1)
    calls = []

    async def plug(source=None):
        calls.append("plug")

    async def unplug(source=None):
        calls.append("unplug")
        state.plugged_in = False

    async def scenario():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01))
        await asyncio.sleep(0.03)
        clock.advance(minutes=1, seconds=1)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
    assert calls == ["unplug"] and a.unplug_at is None



def test_slot_running_with_no_session_doesnt_re_plug():
    """2.49.3: slots from the supplier (planned or running) are left to it."""
    from src.shared_state import SharedState, display_status
    a, clock = _automation(start=datetime.datetime(2026, 10, 3, 22, 14, 0, tzinfo=TZ))
    a.set_replug(enabled=True, after_min=5, attempts=2)
    state = SharedState(state="Preparing", plugged_in=True, connected_to_server=True)
    calls = []
    seen = {}

    async def plug(source=None):
        calls.append("plug")

    async def unplug(source=None):
        calls.append("unplug")

    async def run():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01,
                                                     scheduled=lambda: False, slot_now=lambda: True))
        await asyncio.sleep(0.05)
        seen["status"] = display_status(state)
        seen["replug"] = a.replug_status()
        clock.advance(minutes=30)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(run())
    assert seen["status"] == "Waiting for supplier"
    assert seen["replug"]["status"] == "scheduled" and seen["replug"]["next_replug_at"] is None
    assert calls == []


def test_replug_wait_is_the_set_minutes_from_plug_in():
    # No slots from the supplier: 22:14 + 5 min, not rounded to a half hour
    a, clock = _automation(start=datetime.datetime(2026, 10, 3, 22, 14, 0, tzinfo=TZ))
    a.set_replug(enabled=True, after_min=5, attempts=2)
    assert not a.replug_due(True, False, True, scheduled=False, slot_now=False)
    assert a.replug_status()["next_replug_at"] == "2026-10-03T22:19:00Z"
    clock.advance(minutes=4)
    assert not a.replug_due(True, False, True, scheduled=False, slot_now=False)
    clock.advance(minutes=1)
    assert a.replug_due(True, False, True, scheduled=False, slot_now=False)
    # Changing it while waiting moves the re-plug
    a2, clock2 = _automation(start=datetime.datetime(2026, 10, 3, 22, 14, 0, tzinfo=TZ))
    a2.set_replug(after_min=20)
    a2.replug_due(True, False, True, scheduled=None)
    clock2.advance(minutes=6)
    assert not a2.replug_due(True, False, True, scheduled=None)
    a2.set_replug(after_min=5)
    assert a2.replug_due(True, False, True, scheduled=None)


def test_planned_slot_still_holds_off_re_plug():
    a, clock = _automation()
    a.set_replug(enabled=True, after_min=10, attempts=2)
    assert not a.replug_due(True, False, True, scheduled=True, slot_now=False)
    clock.advance(minutes=30)
    assert not a.replug_due(True, False, True, scheduled=True, slot_now=False)
    assert a.replug_status()["status"] == "scheduled"


# --- auto plug-in charge (plan_auto_plug) -------------------------------------


def _sched_18_22(start, enabled=True):
    a, clock = _automation(start=start)
    a.set_schedule(enabled=enabled, entries=[{"time": "18:00", "action": "plug", "days": list(range(7))},
                                             {"time": "22:00", "action": "unplug", "days": list(range(7))}])
    return a, clock


def _today_windows(a, clock):
    day = clock.now().date()
    return [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.windows() if x.date() == day]


def _at(h, m=0):
    return datetime.datetime(2026, 10, 3, h, m, tzinfo=TZ)  # Saturday


def test_auto_plug_adds_a_one_off_slot():
    a, clock = _sched_18_22(_at(12, 10))
    out = a.plan_auto_plug(90, cap_min=360)  # 13:40 -> next half hour 14:00
    assert out["ready_for"].startswith("2026-10-03T14:00") and not out["combined"] and out["moved"] is None
    autos = [(e["time"], e["action"]) for e in a.entries if e.get("source") == "auto_plug"]
    assert autos == [("12:10", "plug"), ("14:00", "unplug")]
    assert _today_windows(a, clock) == [("12:10", "14:00"), ("18:00", "22:00")]
    assert a.next_unplug(clock.now()).strftime("%H:%M") == "14:00"


def test_auto_plug_joins_an_overlapping_slot():
    a, clock = _sched_18_22(_at(16, 30))
    out = a.plan_auto_plug(120, cap_min=360)  # to 18:30, overlaps 18:00-22:00: 5.5 h in all
    assert out["combined"] and out["end"].startswith("2026-10-03T22:00")
    assert _today_windows(a, clock) == [("16:30", "22:00")]
    assert a.next_unplug(clock.now()).strftime("%H:%M") == "22:00"  # the ready time is set for 22:00


def _sat_slots(*pairs, start=None):
    """Saturday-only slots (so the previous day has none), now 12:00 Saturday."""
    a, clock = _automation(start=start or _at(12))
    entries = []
    for p, u in pairs:
        entries += [{"time": p, "action": "plug", "days": [5]}, {"time": u, "action": "unplug", "days": [5]}]
    a.set_schedule(enabled=True, entries=entries)
    return a, clock


def test_auto_plug_longer_than_the_slot_it_joins_keeps_its_end():
    """Plugged in at 18:30 for a charge to 23:00 over a 19:00-21:00 slot: one slot to
    23:00. The 19:00 plug-in mustn't run (it would set the ready time back to 21:00),
    nor the 21:00 unplug (it would end the charge two hours early)."""
    from src.automation import auto_plug_charge
    a, clock = _sat_slots(("19:00", "21:00"), start=_at(18, 30))
    asked = []

    async def set_ready(unplug):
        asked.append(unplug.strftime("%H:%M"))
        return {"ready": unplug.strftime("%H:%M"), "unplug": unplug.isoformat()}

    out = _run(auto_plug_charge(a, 4.5, set_ready))
    assert out["combined"] and out["end"].startswith("2026-10-03T23:00")
    assert _today_windows(a, clock) == [("18:30", "23:00")]
    assert asked == ["23:00"]
    a.due()
    for minutes, want in ((31, []), (151, []), (271, ["unplug"])):
        clock.dt = _at(18, 30) + datetime.timedelta(minutes=minutes)
        assert [e["action"] for e in a.due()] == want  # nothing at 19:00 or 21:00; unplugged at 23:00
    # A charge that reaches one slot, which meets the next, joins both
    b, clock_b = _sat_slots(("19:00", "20:00"), ("20:30", "22:00"), start=_at(18, 30))
    assert b.plan_auto_plug(120)["end"].startswith("2026-10-03T22:00")  # to 20:30, then on to 22:00
    assert _today_windows(b, clock_b) == [("18:30", "22:00")]
    # One that ends inside a slot still runs to the slot's end, as before
    c, clock_c = _sat_slots(("19:00", "21:00"), start=_at(18, 30))
    assert c.plan_auto_plug(60)["end"].startswith("2026-10-03T21:00")
    assert _today_windows(c, clock_c) == [("18:30", "21:00")]


def test_auto_plug_over_the_cap_trims_just_enough():
    a, clock = _sat_slots(("18:00", "22:00"))
    out = a.plan_auto_plug(180, cap_min=360)  # 12-15 + 18-22 = 7 h: the slot loses 1 h
    assert not out["combined"] and "19:00" in out["moved"]
    assert out["over"] == {"before": 420, "after": 360, "added": 60, "left": 0}
    assert _today_windows(a, clock) == [("12:00", "15:00"), ("19:00", "22:00")]
    # Without a cap (EDF, E.ON) nothing moves
    b, clock_b = _sat_slots(("18:00", "22:00"))
    assert b.plan_auto_plug(180)["over"] is None
    assert _today_windows(b, clock_b) == [("12:00", "15:00"), ("18:00", "22:00")]


def test_auto_plug_over_the_cap_trims_a_daily_slot():
    a, clock = _sched_18_22(_at(12))
    out = a.plan_auto_plug(180, cap_min=360)  # tonight's slot loses 1 h
    assert "19:00" in out["moved"]
    assert _today_windows(a, clock) == [("12:00", "15:00"), ("19:00", "22:00")]
    # yesterday's 18-22 and this charge are 7 h in the 24 hours from yesterday 18:00: nothing to trim for that
    assert out["over"] == {"before": 420, "after": 420, "added": 60, "left": 60}


def test_auto_plug_over_the_cap_can_skip_the_next_slot():
    a, clock = _sat_slots(("18:00", "22:00"))
    out = a.plan_auto_plug(360, cap_min=360)  # 12-18 and 18-22: the slot has to go
    assert "skipped" in out["moved"] and out["over"]["after"] == 360
    assert _today_windows(a, clock) == [("12:00", "18:00")]
    nxt = clock.now().date() + datetime.timedelta(days=7)
    assert any(x.date() == nxt for x, _ in a.windows())  # only this week's is skipped


def test_auto_plug_counts_the_previous_24_hours():
    a, clock = _sat_slots(("02:00", "06:00"), ("20:00", "22:00"))
    out = a.plan_auto_plug(180, cap_min=360)  # 02-06 + 12-15 + 20-22 = 9 h in the 24 from 02:00
    assert out["over"] == {"before": 540, "after": 420, "added": 180, "left": 60}  # skipping 20-22 helps, but 7 h is left
    assert "skipped" in out["moved"]


def test_charge_isnt_blamed_for_the_schedules_own_hours():
    # 23:30-07:00 is already 7.5 h: a charge that doesn't add to any 24 hours with it trims nothing
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 8, 0, tzinfo=TZ))  # Monday 08:00
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug", "days": [0, 1, 2, 3, 4]},
                                          {"time": "07:00", "action": "unplug", "days": list(range(7))}])
    out = a.plan_auto_plug(60, cap_min=360, dry_run=True)  # 08:00-09:00: 8.5 h in the 24 from 08:00
    assert out["over"] == {"before": 510, "after": 450, "added": 60, "left": 0}
    assert "00:30" in out["moved"]  # only the hour the charge adds comes off, not the schedule's own extra 1.5


def test_a_daily_unplug_doesnt_cut_the_charge_short():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 5, 30, tzinfo=TZ))  # Monday 05:30
    a.set_schedule(enabled=True, entries=[{"time": "07:00", "action": "unplug", "days": list(range(7))}])
    a.plan_auto_plug(180)
    day = clock.now().date()
    assert [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.windows() if x.date() == day] == [("05:30", "08:30")]


def test_auto_plug_dry_run_changes_nothing():
    a, clock = _sat_slots(("18:00", "22:00"))
    entries, skips = list(a.entries), list(a.skips)
    out = a.plan_auto_plug(180, cap_min=360, dry_run=True)
    assert "19:00" in out["moved"] and a.entries == entries and a.skips == skips and a.auto_plug_last is None


def test_an_already_over_schedule_can_still_be_changed():
    a, clock = _sat_slots(("18:00", "22:00"))
    a.set_schedule(ready_time=True, daily_cap_min=360)
    a.plan_auto_plug(360, cap_min=None)  # e.g. before the cap applied: 12-18 + 18-22 = 10 h
    weekly = [e for e in a.entries if not e.get("date")]
    shorter = [dict(e, time="21:00") if e["action"] == "plug" and not e.get("date") else e for e in a.entries]
    a.set_schedule(entries=shorter, daily_cap_min=360)  # still over, but no worse: saved
    longer = [dict(e, time="23:00") if e["action"] == "unplug" and not e.get("date") else e for e in a.entries]
    try:
        a.set_schedule(entries=longer, daily_cap_min=360)
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "6 hours" in str(err)
    assert weekly


def test_auto_plug_ready_time_rounds_to_what_the_supplier_accepts():
    from src.ready_time import DEFAULT_TIMES
    a, clock = _sched_18_22(_at(14), enabled=False)
    out = a.plan_auto_plug(120, ready_times=DEFAULT_TIMES)  # EDF: 04:00-11:00 only
    assert out["ready_for"].startswith("2026-10-04T04:00")


def test_auto_plug_slot_runs_with_the_schedule_off():
    a, clock = _sched_18_22(_at(12), enabled=False)
    a.plan_auto_plug(60)
    assert [x.strftime("%H:%M") for x, _ in a.windows()] == ["12:00"]  # the schedule's own are off
    a.due()
    clock.advance(minutes=61)
    assert [e["action"] for e in a.due()] == ["unplug"]
    assert a.snapshot()["schedule"]["auto_plug_last"]["end"].startswith("2026-10-03T13:00")


def test_a_joined_charge_still_unplugs_when_the_schedule_is_turned_off(tmp_path):
    """2.50.1: a charge that joins a scheduled slot ends with the slot's unplug. Turned off, the
    schedule's unplug doesn't run, so the charge keeps its own for then."""
    from src.automation import CHARGE_NOW_SOURCE

    def unplugs(a, clock, hours=6):
        a.due()
        out = []
        for _ in range(hours * 12):
            clock.advance(minutes=5)
            out += [clock.now().strftime("%H:%M") for e in a.due() if e["action"] == "unplug"]
        return out

    # 17:00 for 2 hours meets the 18:00-22:00 slot: one slot to 22:00
    a, clock = _sched_18_22(_at(17))
    out = a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    assert out["combined"] and out["end"].startswith("2026-10-03T22:00")
    assert unplugs(a, clock) == ["22:00"]  # schedule on: the slot's unplug, as before
    a, clock = _sched_18_22(_at(17))
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    a.set_schedule(enabled=False)
    assert unplugs(a, clock) == ["19:00"]  # off: the charge's own end
    # Inside the slot, kept through a restart
    a, clock = _sched_18_22(_at(20))
    a.data_dir = str(tmp_path)
    a.plan_auto_plug(60, source=CHARGE_NOW_SOURCE)
    assert [e["time"] for e in a.entries if e.get("only_off")] == ["21:00"]
    assert not [y for x, y in a.windows() if y.strftime("%H:%M") == "21:00"]  # with the schedule on, it doesn't end a slot
    a.set_schedule(enabled=False)
    assert unplugs(a, clock, 3) == ["21:00"]
    # Deleting the charge takes its own unplug too
    a, clock = _sched_18_22(_at(17))
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    plug = next(e for e in a.entries if e.get("source") and e["action"] == "plug")
    a.set_schedule(entries=[e for e in a.entries if e["id"] != plug["id"]])
    assert not [e for e in a.entries if e.get("only_off")]


def test_a_charge_planned_with_the_schedule_off_joins_its_slot_when_turned_on():
    """2.50.2: turned on, a charge (planned while it was off) becomes one with the slots it meets."""
    from src.automation import CHARGE_NOW_SOURCE

    def unplugs(a, clock, hours=7):
        a.due()
        out = []
        for _ in range(hours * 12):
            clock.advance(minutes=5)
            out += [clock.now().strftime("%H:%M") for e in a.due() if e["action"] == "unplug"]
        return out

    # 17:00-19:00, the schedule's 18:00-22:00: one stretch to 22:00 (not unplugged at 19:00)
    a, clock = _sched_18_22(_at(17), enabled=False)
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    a.set_schedule(enabled=True)
    assert unplugs(a, clock) == ["22:00"]
    # 17:00-23:00 over the slot: not unplugged at 22:00
    a, clock = _sched_18_22(_at(17), enabled=False)
    a.plan_auto_plug(360, source=CHARGE_NOW_SOURCE)
    a.set_schedule(enabled=True)
    assert unplugs(a, clock, 8) == ["23:00"]
    # Off again: the charge's own end (as it would have been)
    a, clock = _sched_18_22(_at(17), enabled=False)
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    a.set_schedule(enabled=True)
    a.set_schedule(enabled=False)
    assert unplugs(a, clock) == ["19:00"]
    # Deleting the charge puts the slot back as it was
    a, clock = _sched_18_22(_at(17), enabled=False)
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    a.set_schedule(enabled=True)
    plug = next(e for e in a.entries if e.get("source") and e["action"] == "plug")
    a.set_schedule(entries=[e for e in a.entries if e["id"] != plug["id"] and not e.get("source")])
    assert a.skips == []


def test_turning_on_asks_first_if_a_joined_charge_goes_over_the_limit():
    """2.50.3: a charge planned with the schedule off only counted the other charges. Turned on,
    joined with the slots, a day over the supplier's limit is refused, unless asked anyway."""
    from src.automation import CHARGE_NOW_SOURCE
    # A charge planned while the schedule was off by an older version (not joined then)
    a, clock = _sched_18_22(_at(14), enabled=False)
    a.set_schedule(entries=a.entries + [
        {"time": "14:00", "action": "plug", "date": "2026-10-03", "source": CHARGE_NOW_SOURCE},
        {"time": "18:00", "action": "unplug", "date": "2026-10-03", "source": CHARGE_NOW_SOURCE}])
    a.limit_reset = "12:00"
    a.set_schedule(ready_time=True, daily_cap_min=360, allow_over=True)
    entries, skips = [dict(e) for e in a.entries], list(a.skips)
    try:
        a.set_schedule(enabled=True, daily_cap_min=360, provider="Octopus Energy")  # with 18-22: 8 h
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "6 hours" in str(err) and "8h" in str(err)
    assert not a.schedule_enabled and a.entries == entries and a.skips == skips  # nothing changed
    a.set_schedule(enabled=True, daily_cap_min=360, allow_over=True)
    assert a.schedule_enabled
    # Within the limit, or without Force schedule on supplier: no question
    b, _ = _sched_18_22(_at(16), enabled=False)
    b.set_schedule(ready_time=True, daily_cap_min=360)
    b.plan_auto_plug(60, source=CHARGE_NOW_SOURCE, cap_min=360)  # 16:00-17:00 + 18-22: 5 h
    b.set_schedule(enabled=True, daily_cap_min=360)
    c, _ = _sched_18_22(_at(14), enabled=False)
    c.plan_auto_plug(240, source=CHARGE_NOW_SOURCE)
    c.set_schedule(enabled=True, daily_cap_min=360)  # ready time off: no limit
    assert b.schedule_enabled and c.schedule_enabled


def test_a_car_slot_alters_your_slots_with_the_schedule_off_too():
    """2.50.5: with the schedule off, a car slot stays separate from your slots (it joins them when
    the schedule's turned on), but trims or skips them for the daily limit, counted as if joined."""
    from src.automation import CHARGE_NOW_SOURCE

    def unplugs(a, clock, hours=7):
        a.due()
        out = []
        for _ in range(hours * 12):
            clock.advance(minutes=5)
            out += [clock.now().strftime("%H:%M") for e in a.due() if e["action"] == "unplug"]
        return out

    # 17:00 for 2 hours meets 18:00-22:00: separate with the schedule off (your slot untouched)
    a, clock = _sched_18_22(_at(17), enabled=False)
    out = a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    assert not out["combined"] and out["end"].startswith("2026-10-03T19:00") and a.skips == []
    assert unplugs(a, clock) == ["19:00"]  # off: the charge's own end
    a, clock = _sched_18_22(_at(17), enabled=False)
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    a.set_schedule(enabled=True)
    assert unplugs(a, clock) == ["22:00"]  # turned on: joined, one slot to 22:00
    # 4 hours from 14:00 with Force schedule on supplier: separate, but the 18:00 slot is trimmed for the limit
    b, _ = _sched_18_22(_at(14), enabled=False)
    b.set_schedule(ready_time=True, daily_cap_min=360)
    out = b.plan_auto_plug(240, source=CHARGE_NOW_SOURCE, cap_min=360)
    assert "the 22:00 slot now starts at 20:00" in out["notes"] and not out["combined"]
    b.set_schedule(enabled=True, daily_cap_min=360)  # (already joined and trimmed: no question)
    assert b.schedule_enabled


def test_turning_the_schedule_on_inside_a_slot_plugs_in():
    from src.automation import automation_loop
    from src.shared_state import SharedState
    a, clock = _sched_18_22(_at(19, 30), enabled=False)
    state = SharedState(plugged_in=False)
    calls, asked = [], []

    async def plug(source=None):
        calls.append(("plug", source))
        state.plugged_in = True

    async def unplug(source=None):
        calls.append(("unplug", source))
        state.plugged_in = False

    async def set_ready(unplug_at):
        asked.append(unplug_at.strftime("%H:%M"))
        return {"ready": unplug_at.strftime("%H:%M")}

    async def run():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
        await asyncio.sleep(0.05)
        seen = list(calls)
        a.set_schedule(enabled=True, ready_time=True)  # 19:30, inside 18:00-22:00
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return seen

    seen = _run(run())
    assert seen == []  # off: nothing
    assert calls == [("plug", "schedule")] and asked == ["22:00"]  # on: plugged in to the slot's end
    # Outside a slot: nothing
    b, _ = _sched_18_22(_at(12), enabled=False)
    b.set_schedule(enabled=True)
    assert b.turned_on and b.in_plug_window() is None


def test_turning_the_schedule_off_unplugs_unless_its_a_car_slot():
    """2.50.2: off during a scheduled slot: unplugged. Off during a car slot (a charge, even one
    joined with a slot): it carries on to the charge's own end. Not plugged in by the schedule
    (e.g. by hand, outside a slot): left alone."""
    from src.automation import CHARGE_NOW_SOURCE, automation_loop
    from src.shared_state import SharedState

    def turn_off(a, plugged=True):
        state = SharedState(plugged_in=plugged)
        calls, asked = [], []

        async def plug(source=None):
            calls.append(("plug", source))

        async def unplug(source=None):
            calls.append(("unplug", source))
            state.plugged_in = False

        async def set_ready(unplug_at):
            asked.append(unplug_at.strftime("%H:%M"))
            return {"ready": unplug_at.strftime("%H:%M")}

        async def run():
            task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
            await asyncio.sleep(0.05)
            a.set_schedule(enabled=False)
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        _run(run())
        return calls, asked

    # 19:30, in the 18:00-22:00 slot: unplugged
    a, clock = _sched_18_22(_at(19, 30))
    assert turn_off(a) == ([("unplug", "schedule off")], [])
    # 17:30, in a Charge now 17:00-19:00 joined with the slot: carries on, ready time back to 19:00
    a, clock = _sched_18_22(_at(17))
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    a.set_schedule(ready_time=True)
    clock.advance(minutes=30)
    calls, asked = turn_off(a)
    assert calls == [] and asked[-1] == "19:00"  # (after the one at start-up)
    # 20:00, the joined charge's own end passed: the slot's part: unplugged
    a, clock = _sched_18_22(_at(17))
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    clock.advance(hours=3)
    assert turn_off(a)[0] == [("unplug", "schedule off")]
    # 12:00, plugged in by hand outside any slot: left alone
    a, clock = _sched_18_22(_at(12))
    assert turn_off(a) == ([], [])


def test_auto_plug_charge_sets_the_ready_time():
    from src.automation import auto_plug_charge
    a, clock = _sched_18_22(_at(16, 30))
    asked = []

    async def set_ready(unplug):
        asked.append(unplug.strftime("%H:%M"))
        return {"ready": unplug.strftime("%H:%M"), "unplug": unplug.isoformat()}

    _run(auto_plug_charge(a, 2, set_ready, cap_min=360))
    assert asked == ["22:00"]  # joined with the 18:00-22:00 slot


def test_one_offs_kept_14_days_and_listed_as_runs():
    a, clock = _sched_18_22(_at(12))
    a.plan_auto_plug(60)  # 12:00-13:00, auto plug-in
    a.set_schedule(entries=a.entries + [{"time": "08:00", "action": "plug", "date": "2026-10-04"},
                                         {"time": "09:00", "action": "unplug", "date": "2026-10-04"}])
    clock.advance(days=2)
    runs = a.one_off_runs()
    assert [(r["start"][:16], r["source"]) for r in runs] == [
        ("2026-10-04T08:00", "one_off"), ("2026-10-03T12:00", "auto_plug")]
    clock.advance(days=10)
    a.set_schedule(enabled=True)  # prunes
    assert len([e for e in a.entries if e.get("date")]) == 4  # 12 days on: still kept
    clock.advance(days=3)
    a.set_schedule(enabled=True)
    assert [e for e in a.entries if e.get("date")] == []  # over 14 days: gone
    assert a.snapshot()["schedule"]["one_off_runs"] == []


def test_six_hour_limit_only_with_force_schedule_on():
    long_day = [{"time": "12:00", "action": "plug", "days": list(range(7))},
                {"time": "22:00", "action": "unplug", "days": list(range(7))}]  # 10 h a day
    a, clock = _automation()
    a.set_schedule(enabled=True, ready_time=False, entries=long_day, daily_cap_min=360)  # off: fine
    assert len(a.entries) == 2
    try:
        a.set_schedule(ready_time=True, daily_cap_min=360)
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "6 hours" in str(err)
    assert a.ready_time is False
    # Auto plug-in with no cap (Force off) adds to it without moving anything
    b, clock_b = _sched_18_22(_at(12))
    b.plan_auto_plug(300, cap_min=None)  # 12-17 + 18-22 = 9 h
    assert _today_windows(b, clock_b) == [("12:00", "17:00"), ("18:00", "22:00")]


def test_charge_now_is_planned_like_auto_plug_in():
    from src.automation import CHARGE_NOW_SOURCE
    a, clock = _sched_18_22(_at(12), enabled=False)
    out = a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)
    assert out["source"] == "charge_now"
    assert {e["source"] for e in a.entries if e.get("date")} == {"charge_now"}
    assert [x.strftime("%H:%M") for x, _ in a.windows()] == ["12:00"]  # runs with the schedule off
    a.due()
    clock.advance(minutes=121)
    assert [e["action"] for e in a.due()] == ["unplug"]
    assert a.one_off_runs()[0]["source"] == "charge_now"


def test_charge_capped_at_six_hours_for_octopus():
    a, clock = _sched_18_22(_at(8, 10), enabled=False)
    out = a.plan_auto_plug(480, cap_min=360, provider="Octopus Energy")  # 8 h asked
    assert out["ready_for"].startswith("2026-10-03T14:00")  # 08:10 + 6 h = 14:10: not rounded up past 6 h
    assert "shortened to 6 hours" in out["notes"][0]
    b, clock_b = _sched_18_22(_at(8, 10), enabled=False)
    assert b.plan_auto_plug(480)["ready_for"].startswith("2026-10-03T16:30")  # no cap: 8 h, rounded up
    c, clock_c = _sched_18_22(_at(8), enabled=False)
    assert c.plan_auto_plug(360, cap_min=360)["ready_for"].startswith("2026-10-03T14:00")  # exactly 6 h



def test_unskip_cant_go_over_six_hours():
    def sunday_slot():
        a, clock = _automation(start=_at(12))  # Saturday 3 Oct
        a.set_schedule(enabled=True, ready_time=True, daily_cap_min=360, entries=[
            {"time": "18:00", "action": "plug", "days": [6]}, {"time": "22:00", "action": "unplug", "days": [6]}])
        plug = next(e for e in a.entries if e["action"] == "plug")
        unplug = next(e for e in a.entries if e["action"] == "unplug")
        return a, plug, unplug

    a, plug, unplug = sunday_slot()
    slot = [{"entry_id": plug["id"], "date": "2026-10-04"}, {"entry_id": unplug["id"], "date": "2026-10-04"}]
    a.set_skips(slot, True, daily_cap_min=360)  # skip Sunday's 18-22
    # While it's skipped, a 13:00-16:00 one-off on Sunday fits (3 h)
    a.set_schedule(entries=a.entries + [{"time": "13:00", "action": "plug", "date": "2026-10-04"},
                                         {"time": "16:00", "action": "unplug", "date": "2026-10-04"}], daily_cap_min=360)
    try:
        a.set_skips(slot, False, daily_cap_min=360, provider="Octopus Energy")  # back on: 7 h
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "6 hours" in str(err)
    assert {"entry_id": plug["id"], "date": "2026-10-04"} in a.skips  # nothing changed
    # With Force schedule on supplier off, there's no limit
    a.set_schedule(ready_time=False)
    a.set_skips(slot, False, daily_cap_min=360)
    assert a.skips == []

    # Skipping only the unplug would leave it plugged in until next Sunday: refused
    b, plug, unplug = sunday_slot()
    try:
        b.set_skips([{"entry_id": unplug["id"], "date": "2026-10-04"}], True, daily_cap_min=360)
        raise AssertionError("should have refused")
    except ValueError:
        pass
    b.set_skips([{"entry_id": plug["id"], "date": "2026-10-04"}, {"entry_id": unplug["id"], "date": "2026-10-04"}],
                True, daily_cap_min=360)  # the whole slot: fine


def test_override_guards_lifts_the_six_hours():
    every = list(range(7))
    seven = [{"time": "23:30", "action": "plug", "days": every}, {"time": "06:30", "action": "unplug", "days": every}]
    six = [{"time": "23:30", "action": "plug", "days": every}, {"time": "05:30", "action": "unplug", "days": every}]
    a, clock = _automation()
    a.set_schedule(entries=six, ready_time=True, daily_cap_min=360)
    a.set_schedule(override_guards=True, daily_cap_min=360)
    a.set_schedule(entries=seven, daily_cap_min=360)  # over 6 hours, allowed
    assert a.override_guards and a.snapshot()["schedule"]["override_guards"]
    try:
        a.set_schedule(override_guards=False, daily_cap_min=360, provider="Octopus Energy")  # back on while over
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "6 hours" in str(err)
    assert a.override_guards
    a.set_schedule(entries=six, override_guards=False, daily_cap_min=360)
    assert not a.override_guards



def test_over_six_hours_can_be_saved_anyway():
    """2.50.0: each refusal can be gone ahead with (allow_over); the page shows the hours over in red."""
    every = list(range(7))
    six = [{"time": "23:30", "action": "plug", "days": every}, {"time": "05:30", "action": "unplug", "days": every}]
    seven = [{"time": "23:30", "action": "plug", "days": every}, {"time": "06:30", "action": "unplug", "days": every}]
    a, clock = _automation()
    a.set_schedule(entries=six, ready_time=True, daily_cap_min=360)
    a.set_schedule(entries=seven, daily_cap_min=360, allow_over=True)  # saving a slot
    assert [e["time"] for e in a.entries] == ["23:30", "06:30"]
    a.set_schedule(entries=six + [{"time": "12:00", "action": "plug", "date": "2026-10-06"},
                                  {"time": "13:00", "action": "unplug", "date": "2026-10-06"}],
                   daily_cap_min=360, allow_over=True)  # and another over it, still fine
    # Turning Force schedule on supplier on while over
    b, _ = _automation()
    b.set_schedule(entries=seven, daily_cap_min=360)
    b.set_schedule(ready_time=True, daily_cap_min=360, allow_over=True)
    assert b.ready_time
    # The unplug times are still checked
    try:
        b.set_schedule(entries=[{"time": "23:30", "action": "plug", "days": every}, {"time": "06:40", "action": "unplug", "days": every}],
                       ready_times=["06:30", "07:00"], daily_cap_min=360, allow_over=True)
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "06:40" in str(err)
    # The guards back on while over
    c, _ = _automation()
    c.set_schedule(entries=six, ready_time=True, daily_cap_min=360)
    c.set_schedule(override_guards=True, daily_cap_min=360)
    c.set_schedule(entries=seven, daily_cap_min=360)
    c.set_schedule(override_guards=False, daily_cap_min=360, allow_over=True)
    assert not c.override_guards


def test_unskip_over_six_hours_anyway():
    a, clock = _automation(start=_at(12))  # Saturday 3 Oct
    a.set_schedule(enabled=True, ready_time=True, daily_cap_min=360, entries=[
        {"time": "18:00", "action": "plug", "days": [6]}, {"time": "22:00", "action": "unplug", "days": [6]}])
    plug = next(e for e in a.entries if e["action"] == "plug")
    unplug = next(e for e in a.entries if e["action"] == "unplug")
    slot = [{"entry_id": plug["id"], "date": "2026-10-04"}, {"entry_id": unplug["id"], "date": "2026-10-04"}]
    a.set_skips(slot, True, daily_cap_min=360)
    a.set_schedule(entries=a.entries + [{"time": "13:00", "action": "plug", "date": "2026-10-04"},
                                         {"time": "16:00", "action": "unplug", "date": "2026-10-04"}], daily_cap_min=360)
    a.set_skips(slot, False, daily_cap_min=360, allow_over=True)  # 7 h, as asked
    assert a.skips == []


NIGHT = {"start": "23:30", "end": "05:30", "days": list(range(7))}


def _blocked(start, entries=()):
    a, clock = _automation(start=start)
    a.set_schedule(enabled=True, entries=list(entries), blocks=[NIGHT])
    return a, clock


def _spans(out):
    return [(x["start"][11:16], x["end"][11:16]) for x in out["segments"]]


def test_slots_cant_overlap_a_no_charging_time():
    a, clock = _blocked(_at(12))
    for slot in (("22:00", "00:00"), ("05:00", "07:00")):
        try:
            a.set_schedule(entries=[{"time": slot[0], "action": "plug"}, {"time": slot[1], "action": "unplug"}])
            raise AssertionError("should have refused %s" % (slot,))
        except ValueError as err:
            assert "off-peak time" in str(err)
    a.set_schedule(entries=[{"time": "05:30", "action": "plug"}, {"time": "07:00", "action": "unplug"}])  # just after: fine
    try:
        a.set_schedule(blocks=[{"start": "05:00", "end": "06:00", "days": list(range(7))}])  # over that slot
        raise AssertionError("should have refused")
    except ValueError:
        pass
    assert a.blocks[0]["start"] == "23:30"


def test_a_charge_is_cut_around_a_no_charging_time():
    a, clock = _blocked(_at(18))
    out = a.plan_auto_plug(12 * 60)  # 18:00 for 12 h, no limit: 18:00-23:30, then 05:30-06:00
    assert _spans(out) == [("18:00", "23:30"), ("05:30", "06:00")] and out["plug_now"]
    day = clock.now().date()
    wins = [(x.strftime("%a %H:%M"), y.strftime("%a %H:%M")) for x, y in a.windows() if x.date() >= day]
    assert wins[:2] == [("Sat 18:00", "Sat 23:30"), ("Sun 05:30", "Sun 06:00")]


def test_only_the_daily_limit_is_scheduled():
    a, clock = _blocked(_at(18))
    out = a.plan_auto_plug(12 * 60, cap_min=360)  # 5.5 h before the night, 0.5 h after it
    assert _spans(out) == [("18:00", "23:30"), ("05:30", "06:00")] and out["minutes"] == 360
    b, _ = _blocked(_at(12))
    out = b.plan_auto_plug(12 * 60, cap_min=360)  # all 6 before the night
    assert _spans(out) == [("12:00", "18:00")] and "shortened to 6 hours" in out["notes"][0]


def test_a_charge_during_a_no_charging_time_waits_for_its_end():
    a, clock = _blocked(datetime.datetime(2026, 10, 3, 1, 0, tzinfo=TZ))
    out = a.plan_auto_plug(6 * 60)  # 01:00 for 6 h would end 07:00: 05:30-07:00, same end
    assert not out["plug_now"] and _spans(out) == [("05:30", "07:00")]
    b, _ = _blocked(datetime.datetime(2026, 10, 3, 1, 0, tzinfo=TZ))
    out = b.plan_auto_plug(3 * 60)  # would end at 04:00, inside it: nothing
    assert out["nothing"] and not out["plug_now"] and b.entries == []
    now = b._now()
    assert any(start <= now < end for start, end in b.block_windows(now.date(), 1))  # inside it


def test_deleting_a_charge_puts_back_the_slot_it_trimmed():
    a, clock = _sat_slots(("18:00", "22:00"))
    a.plan_auto_plug(180, cap_min=360, source="charge_now")  # tonight's slot now starts at 19:00
    later = next(e for e in a.entries if e["time"] == "19:00")
    assert later.get("source") is None  # still a plain slot, not a Charge now one
    assert _today_windows(a, clock) == [("12:00", "15:00"), ("19:00", "22:00")]
    charge = [e for e in a.entries if e.get("source") == "charge_now"]
    a.set_schedule(entries=[e for e in a.entries if e not in charge], daily_cap_min=360)
    assert _today_windows(a, clock) == [("18:00", "22:00")] and a.skips == []
    assert not any(e["time"] == "19:00" for e in a.entries)


def test_daily_limit_that_resets_at_midday():
    # Octopus's limit resets at 12:00: 6 hours before it and 6 after are fine
    sat = [{"time": "06:00", "action": "plug", "days": [5]}, {"time": "18:00", "action": "unplug", "days": [5]}]
    a, clock = _automation(start=_at(1))
    try:
        a.set_schedule(entries=sat, ready_time=True, daily_cap_min=360)  # any 24 hours: 12 h
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "12h 00m" in str(err)
    a.limit_reset = "12:00"
    a.set_schedule(entries=sat, ready_time=True, daily_cap_min=360)  # 06-12 and 12-18: 6 h each
    assert a.entries[1]["time"] == "18:00"
    try:
        a.set_schedule(entries=[{"time": "11:00", "action": "plug", "days": [5]},
                                {"time": "19:00", "action": "unplug", "days": [5]}], daily_cap_min=360)
        raise AssertionError("should have refused")
    except ValueError as err:  # 12-19: 7 h after 12:00
        assert "7h 00m" in str(err) and "resets at 12:00" in str(err) and "Sat 12:00" in str(err)


def test_charge_across_the_limit_reset():
    a, clock = _automation(start=_at(8))  # Saturday 08:00, nothing scheduled
    a.limit_reset = "12:00"
    out = a.plan_auto_plug(720, cap_min=360, provider="Octopus Energy")  # 08-12 (4 h) + 12-18 (6 h, the limit)
    assert out["ready_for"].startswith("2026-10-03T18:00") and "shortened to 10 hours" in out["notes"][0]
    assert out["over"]["left"] == 0
    b, clock_b = _automation(start=_at(6))
    b.limit_reset = "12:00"
    out = b.plan_auto_plug(720, cap_min=360)  # 06-12 and 12-18: 6 h each, all of it
    assert out["ready_for"].startswith("2026-10-03T18:00") and not any("shortened" in n for n in out["notes"])
    c, clock_c = _automation(start=_at(8))  # any 24 hours: 6 h
    assert c.plan_auto_plug(720, cap_min=360)["ready_for"].startswith("2026-10-03T14:00")


def test_limit_reset_trims_only_within_the_limit_day():
    # 10:00-12:00 charge with an 18:00-22:00 slot: rolling, 6 h in 24 (fine);
    # a 3 h charge from 10:00 is 1 h before 12:00 and 2 h after: with 18-22 that's 6 h
    a, clock = _sat_slots(("18:00", "22:00"), start=_at(10))
    a.limit_reset = "12:00"
    out = a.plan_auto_plug(180, cap_min=360)
    assert out["moved"] is None and _today_windows(a, clock) == [("10:00", "13:00"), ("18:00", "22:00")]
    b, clock_b = _sat_slots(("18:00", "22:00"), start=_at(10))  # rolling: 7 h, the slot loses 1 h
    assert "19:00" in b.plan_auto_plug(180, cap_min=360)["moved"]


def test_daily_limit_over_any_48_hours():
    # 4 h on Saturday and 4 h on Sunday: fine in any 24 hours, 8 h in 48
    two = [{"time": "18:00", "action": "plug", "days": [5, 6]}, {"time": "22:00", "action": "unplug", "days": [5, 6]}]
    a, clock = _automation(start=_at(1))
    a.set_schedule(entries=two, ready_time=True, daily_cap_min=360)
    a.set_schedule(entries=[], daily_cap_min=360)
    a.limit_reset = "48h"
    try:
        a.set_schedule(entries=two, daily_cap_min=360)
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "8h 00m" in str(err) and "48 hours" in str(err)
    # A charge counts the 48 hours around it: 12-15 with tonight's 18-22 (Saturday only)
    b, clock_b = _sat_slots(("18:00", "22:00"))
    b.limit_reset = "48h"
    out = b.plan_auto_plug(180, cap_min=360)  # 7 h in 48: the slot loses 1 h
    assert "19:00" in out["moved"] and out["over"]["left"] == 0
    c, clock_c = _automation(start=_at(8))
    c.limit_reset = "48h"
    assert c.plan_auto_plug(720, cap_min=360)["ready_for"].startswith("2026-10-03T14:00")  # 6 h, then it stops


def test_daily_limit_over_any_rolling_period():
    # A 12-hour rolling period: 18-22 Saturday and 08-11 Sunday are 14 h apart, fine
    two = [{"time": "18:00", "action": "plug", "days": [5]}, {"time": "22:00", "action": "unplug", "days": [5]},
           {"time": "08:00", "action": "plug", "days": [6]}, {"time": "11:00", "action": "unplug", "days": [6]}]
    a, clock = _automation(start=_at(1))
    a.limit_reset = "12h"
    a.set_schedule(entries=two, ready_time=True, daily_cap_min=360)
    a.limit_reset = None  # any 24 hours: 7 h
    try:
        a.check_daily_cap(a.entries, 360, "Octopus Energy")
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "7h 00m" in str(err) and "24 hours" in str(err)
    a.limit_reset = "72h"
    try:
        a.check_daily_cap(a.entries, 360, "Octopus Energy")
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "the 72 hours from" in str(err) and "in any 72 hours" in str(err)


def test_back_to_back_slots_are_one_stretch():
    thu = "2026-10-08"
    first = [{"time": "06:00", "action": "plug", "date": thu}, {"time": "12:00", "action": "unplug", "date": thu}]
    second = [{"time": "12:00", "action": "plug", "date": thu}, {"time": "23:00", "action": "unplug", "date": thu}]
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 14, 0, tzinfo=TZ))  # Monday
    a.set_schedule(enabled=True, entries=first + second)
    # plugged in for both (it used to unplug at 12:00 and stay unplugged)
    assert [(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.windows()] == [("06:00", "12:00"), ("12:00", "23:00")]
    assert a.in_plug_window(datetime.datetime(2026, 10, 8, 15, 0, tzinfo=TZ)) is not None
    # One charge: the ready time is the second's end, from the first's start
    assert a.next_unplug(datetime.datetime(2026, 10, 8, 6, 0, tzinfo=TZ)).strftime("%H:%M") == "23:00"
    assert a.next_unplug(datetime.datetime(2026, 10, 8, 12, 1, tzinfo=TZ)).strftime("%H:%M") == "23:00"
    # At 12:00 it carries on plugged in
    clock.dt = datetime.datetime(2026, 10, 8, 11, 59, tzinfo=TZ)
    a.due()
    clock.dt = datetime.datetime(2026, 10, 8, 12, 0, 30, tzinfo=TZ)
    due = a.due()
    assert [(e["action"], bool(e.get("carry_on"))) for e in due] == [("unplug", True), ("plug", True)]
    # Where the daily limit resets (Octopus: 12:00) it unplugs and plugs back in: two
    # charges, each with its own ready time
    a.limit_reset = "12:00"
    assert a.next_unplug(datetime.datetime(2026, 10, 8, 6, 0, tzinfo=TZ)).strftime("%H:%M") == "12:00"
    clock.dt = datetime.datetime(2026, 10, 8, 11, 59, tzinfo=TZ)
    a.due()
    clock.dt = datetime.datetime(2026, 10, 8, 12, 0, 30, tzinfo=TZ)
    assert [(e["action"], bool(e.get("back_to_back"))) for e in a.due()] == [("unplug", True), ("plug", False)]
    # Octopus, resetting at 12:00: 12:00-23:00 is 11 hours in the day from 12:00
    b, clock_b = _automation(start=datetime.datetime(2026, 10, 5, 14, 0, tzinfo=TZ))
    b.limit_reset = "12:00"
    b.set_schedule(enabled=True, ready_time=True, entries=first, daily_cap_min=360)
    try:
        b.set_schedule(entries=b.entries + second, daily_cap_min=360, provider="Octopus Energy")
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "11h 00m" in str(err) and "Thu 12:00" in str(err)


def _over_cap(fn):
    try:
        fn()
    except ValueError as err:
        assert "6 hours" in str(err)
        return True
    return False


def _fresh_cap(reset=None):
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 14, 0, tzinfo=TZ))  # Monday 14:00
    a.limit_reset = reset
    a.set_schedule(enabled=True, ready_time=True, daily_cap_min=360)
    return a


def _slot(p, u, **k):
    return [{"time": p, "action": "plug", **k}, {"time": u, "action": "unplug", **k}]


def test_already_over_cant_add_another_day_over():
    a = _fresh_cap()
    a.override_guards = True  # e.g. Charge anyway: Tuesday is 7 h
    a.set_schedule(entries=_slot("10:00", "17:00", date="2026-10-06"), daily_cap_min=360)
    a.override_guards = False
    assert _over_cap(lambda: a.set_schedule(entries=a.entries + _slot("10:00", "17:00", date="2026-10-08"), daily_cap_min=360))
    # Shortening the one that's over is fine
    a.set_schedule(entries=_slot("10:00", "16:00", date="2026-10-06"), daily_cap_min=360)


def test_one_offs_weeks_ahead_are_checked():
    a = _fresh_cap()
    assert _over_cap(lambda: a.set_schedule(entries=_slot("06:00", "18:00", date="2026-10-26"), daily_cap_min=360))
    a.set_schedule(entries=_slot("06:00", "12:00", date="2026-10-26"), daily_cap_min=360)  # 6 h: fine


def test_plug_in_that_never_unplugs_counts():
    a = _fresh_cap()
    assert _over_cap(lambda: a.set_schedule(entries=[{"time": "06:00", "action": "plug", "days": [2]}], daily_cap_min=360))


def test_unskipping_into_a_no_charging_time_is_refused():
    a = _fresh_cap()
    a.set_schedule(entries=[{"time": "23:30", "action": "plug", "date": "2026-10-07"},
                            {"time": "03:30", "action": "unplug", "date": "2026-10-08"}], daily_cap_min=360)
    p = next(e for e in a.entries if e["action"] == "plug")
    u = next(e for e in a.entries if e["action"] == "unplug")
    keys = [{"entry_id": p["id"], "date": "2026-10-07"}, {"entry_id": u["id"], "date": "2026-10-08"}]
    a.set_skips(keys, True, daily_cap_min=360)
    a.set_schedule(blocks=[{"start": "23:00", "end": "05:00", "days": [2]}])  # fine while it's skipped
    try:
        a.set_skips(keys, False, daily_cap_min=360)
        raise AssertionError("should have refused")
    except ValueError as err:
        assert "off-peak time" in str(err)
    assert keys[0] in a.skips


def test_clock_change_nights_count_real_hours():
    """Octopus counts the night the clocks go back as 25 hours: 23:30-05:30 is
    7 hours then (5 when they go forward). Limit days are 12:00 to 12:00 by the clock."""
    import os
    import time as _time
    from src.automation import Automation, ReplugOptions, limit_end, period_start
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/London"
    _time.tzset()
    try:
        now = datetime.datetime(2026, 10, 23, 12, 0).astimezone()
        a = Automation(None, ReplugOptions(), now=lambda: now, clock=_time.time)  # the add-on's own clock handling
        night = [{"time": "23:30", "action": "plug", "date": "2026-10-24"},
                 {"time": "05:30", "action": "unplug", "date": "2026-10-25"}]
        a.set_schedule(enabled=True, ready_time=True, daily_cap_min=360)
        for reset in (None, "12:00"):
            a.limit_reset = reset
            try:
                a.set_schedule(entries=night, daily_cap_min=360)
                raise AssertionError("should have refused")
            except ValueError as err:
                assert "7h 00m" in str(err)
        # 23:30-04:30 is the 6 hours that night
        a.set_schedule(entries=[dict(night[0]), dict(night[1], time="04:30")], daily_cap_min=360)
        # The limit day from Sat 12:00 ends Sun 12:00 by the clock: 25 hours
        sat = datetime.datetime(2026, 10, 24, 15, 0).astimezone()
        start = period_start(sat, "12:00")
        assert start.hour == 12 and start.day == 24
        end = limit_end(start, "12:00")
        assert end.hour == 12 and end.day == 25 and (end - start) == datetime.timedelta(hours=25)
        # After the change: Sun 13:00 is in the day from Sun 12:00
        sun = datetime.datetime(2026, 10, 25, 13, 0).astimezone()
        assert period_start(sun, "12:00").day == 25
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        _time.tzset()


def test_auto_plug_can_take_its_time_from_the_end_of_the_next_slot():
    # 14:00 + 3 h is 2 h over: from the end, the 01:00-05:00 slot ends at 03:00 instead (21-22 still goes first)
    b = _fresh_cap()
    b.trim_from = "end"
    b.set_schedule(entries=_slot("21:00", "22:00", days=[0]) + [{"time": "01:00", "action": "plug", "days": [1]},
                   {"time": "05:00", "action": "unplug", "days": [1]}], daily_cap_min=360)
    out = b.plan_auto_plug(180, cap_min=360)
    assert out["moved"] == "the 21:00–22:00 slot is skipped; the 01:00 slot now ends at 04:00"
    assert out["over"]["after"] == 360 and out["over"]["left"] == 0
    assert b._longest_day(b.entries)[0] == 360
    # Just one later slot, 1 h over: its last hour goes, it still starts when it did
    c = _fresh_cap()
    c.trim_from = "end"
    c.set_schedule(entries=[{"time": "01:00", "action": "plug", "days": [1]},
                   {"time": "05:00", "action": "unplug", "days": [1]}], daily_cap_min=360)
    out = c.plan_auto_plug(180, cap_min=360)
    assert out["moved"] == "the 01:00 slot now ends at 04:00" and out["over"]["left"] == 0
    # Deleting the charge puts it back
    weekly = [e for e in c.entries if not e.get("date")]
    c.set_schedule(entries=weekly)
    assert c.skips == [] and c._longest_day(c.entries)[0] == 240
    # Octopus (resets at 12:00), 18:00-23:00 tonight, 3 h from 14:00: it ends at 21:00 (from the start: starts at 20:00)
    for trim, moved in (("end", "the 18:00 slot now ends at 21:00"), ("start", "the 23:00 slot now starts at 20:00")):
        d = _fresh_cap("12:00")
        d.trim_from = trim
        d.set_schedule(entries=_slot("18:00", "23:00", days=[0]), daily_cap_min=360)
        out = d.plan_auto_plug(180, cap_min=360)
        assert out["moved"] == moved and out["over"] == {"before": 480, "after": 360, "added": 120, "left": 0}


def test_auto_plug_trims_every_later_slot_it_needs_to():
    # 14:00 + 6 h with 21:00-22:00 and 01:00-05:00 after it: both go (6 h in 24 already)
    a = _fresh_cap()
    a.set_schedule(entries=_slot("21:00", "22:00", days=[0]) + [{"time": "01:00", "action": "plug", "days": [1]},
                   {"time": "05:00", "action": "unplug", "days": [1]}], daily_cap_min=360)
    out = a.plan_auto_plug(360, cap_min=360)
    assert out["over"]["left"] == 0 and out["over"]["after"] == 360
    assert "21:00–22:00 slot is skipped" in out["moved"] and "01:00–05:00 slot is skipped" in out["moved"]
    assert a._longest_day(a.entries)[0] == 360
    # 14:00 + 3 h is 2 h over: the next slot first (21-22 skipped), then 01:00-05:00 starts at 02:00
    b = _fresh_cap()
    b.set_schedule(entries=_slot("21:00", "22:00", days=[0]) + [{"time": "01:00", "action": "plug", "days": [1]},
                   {"time": "05:00", "action": "unplug", "days": [1]}], daily_cap_min=360)
    out = b.plan_auto_plug(180, cap_min=360)
    assert out["moved"] == "the 21:00–22:00 slot is skipped; the 05:00 slot now starts at 02:00"
    assert out["over"] == {"before": 480, "after": 360, "added": 120, "left": 0}
    # Deleting the charge puts both back
    c = _fresh_cap()
    c.set_schedule(entries=_slot("21:00", "22:00", days=[0]) + [{"time": "01:00", "action": "plug", "days": [1]},
                   {"time": "05:00", "action": "unplug", "days": [1]}], daily_cap_min=360)
    weekly = [e for e in c.entries if not e.get("date")]
    c.plan_auto_plug(360, cap_min=360)
    assert c.skips
    c.set_schedule(entries=weekly)
    assert c.skips == [] and all(not e.get("date") for e in c.entries)


def test_replug_unplugged_for_is_a_setting():
    a, clock = _automation()
    assert a.replug["off_s"] == 30
    a.set_replug(off_s=60)
    assert a.replug["off_s"] == 60
    for bad in (2, 301, "x"):
        try:
            a.set_replug(off_s=bad)
            raise AssertionError("should have refused")
        except ValueError:
            pass
    waits = []

    async def unplug(source):
        pass

    async def plug(source):
        pass

    async def fake_sleep(s):
        waits.append(s)

    import src.automation as mod
    real = mod.asyncio.sleep
    mod.asyncio.sleep = fake_sleep
    try:
        _run(a.run_replug(unplug, plug))
    finally:
        mod.asyncio.sleep = real
    assert waits == [60]


def test_slots_that_meet_carry_on_plugged_in():
    """14:00-15:30 then 15:30-18:00 (no reset there): at 15:30 nothing's unplugged
    and the ready time stays 18:00, set at 14:00."""
    from src.shared_state import SharedState
    thu = "2026-10-08"
    a, clock = _automation(start=datetime.datetime(2026, 10, 8, 13, 59, 50, tzinfo=TZ))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, ready_time=True, entries=[
        {"time": "14:00", "action": "plug", "date": thu}, {"time": "15:30", "action": "unplug", "date": thu},
        {"time": "15:30", "action": "plug", "date": thu}, {"time": "18:00", "action": "unplug", "date": thu}])
    state = SharedState(plugged_in=False)
    calls, asked = [], []

    async def plug(source=None):
        calls.append("plug")
        state.plugged_in = True

    async def unplug(source=None):
        calls.append("unplug")
        state.plugged_in = False

    async def set_ready(unplug_at):
        asked.append(unplug_at.strftime("%H:%M"))
        return {"ready": unplug_at.strftime("%H:%M")}

    async def ticks():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
        await asyncio.sleep(0.03)
        clock.dt = datetime.datetime(2026, 10, 8, 14, 0, 10, tzinfo=TZ)
        await asyncio.sleep(0.05)
        clock.dt = datetime.datetime(2026, 10, 8, 15, 30, 10, tzinfo=TZ)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(ticks())
    assert calls == ["plug"] and state.plugged_in
    assert asked and set(asked) == {"18:00"}
    assert a.next_action()["time"].endswith("18:00:00+00:00") and a.next_action()["action"] == "unplug"


def test_back_to_back_slots_replug_at_the_join():
    """06:00-12:00 then 12:00-18:00 (Octopus resets at 12:00): at 12:00 it
    unplugs straight away (even mid-session with Force schedule on) and plugs
    back in after the re-plug time, then sets the ready time to 18:00."""
    from src.shared_state import SharedState
    thu = "2026-10-08"
    a, clock = _automation(start=datetime.datetime(2026, 10, 8, 11, 59, 50, tzinfo=TZ))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, ready_time=True, entries=[
        {"time": "06:00", "action": "plug", "date": thu}, {"time": "12:00", "action": "unplug", "date": thu},
        {"time": "12:00", "action": "plug", "date": thu}, {"time": "18:00", "action": "unplug", "date": thu}])
    a.set_replug(off_s=5)
    state = SharedState(plugged_in=True)
    state.transaction_id = 7  # a session is going
    calls, asked, slept = [], [], []

    async def plug(source=None):
        calls.append("plug")
        state.plugged_in = True

    async def unplug(source=None):
        calls.append("unplug")
        state.plugged_in = False

    async def set_ready(unplug_at):
        asked.append(unplug_at.strftime("%H:%M"))
        return {"ready": unplug_at.strftime("%H:%M")}

    import src.automation as mod
    real_sleep = mod.asyncio.sleep

    async def fake_sleep(s):
        if s == 5:
            slept.append(s)
        await real_sleep(0)

    async def ticks():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
        await real_sleep(0.03)
        clock.dt = datetime.datetime(2026, 10, 8, 12, 0, 10, tzinfo=TZ)
        await real_sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    mod.asyncio.sleep = fake_sleep
    try:
        _run(ticks())
    finally:
        mod.asyncio.sleep = real_sleep
    assert calls == ["unplug", "plug"] and slept == [5]
    assert asked and asked[-1] == "18:00"


def _pairs(a, day):
    return sorted((x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in a.windows() if x.date() == day)


def test_charge_across_the_reset_is_split_there():
    a, clock = _automation(start=_at(8))  # Saturday 08:00
    a.limit_reset = "12:00"
    out = a.plan_auto_plug(720, cap_min=360)  # 08-12 and 12-18
    day = clock.now().date()
    assert _pairs(a, day) == [("08:00", "12:00"), ("12:00", "18:00")]
    assert any("fresh charge from 12:00" in n for n in out["notes"])
    assert a.next_unplug(clock.now()).strftime("%H:%M") == "12:00"  # the first ready time
    # Deleting the charge takes the split with it
    a.set_schedule(entries=[])
    assert a.entries == []
    # Not with a rolling limit, nor a charge within the limit
    b, clock_b = _automation(start=_at(8))
    b.plan_auto_plug(720, cap_min=360)
    assert _pairs(b, day) == [("08:00", "14:00")]
    c, clock_c = _automation(start=_at(10))
    c.limit_reset = "12:00"
    c.plan_auto_plug(180, cap_min=360)  # 10-13: 3 h
    assert _pairs(c, day) == [("10:00", "13:00")]


def test_charge_joining_a_slot_across_the_reset_is_split():
    a, clock = _sat_slots(("12:00", "18:00"), start=_at(8))
    a.limit_reset = "12:00"
    out = a.plan_auto_plug(240, cap_min=360)  # 08-12 meets 12-18: 10 h as one
    assert out["combined"]
    assert _pairs(a, clock.now().date()) == [("08:00", "12:00"), ("12:00", "18:00")]
    assert a.next_unplug(clock.now()).strftime("%H:%M") == "12:00"


def test_saved_slots_across_the_reset_are_split():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 7, 0, tzinfo=TZ))  # Monday 07:00
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, ready_time=True, daily_cap_min=360, entries=[
        {"time": "09:00", "action": "plug", "days": [1, 3]}, {"time": "17:00", "action": "unplug", "days": [1, 3]},
        {"time": "08:00", "action": "plug", "date": "2026-10-10"}, {"time": "18:00", "action": "unplug", "date": "2026-10-10"},
        {"time": "13:00", "action": "plug", "days": [0]}, {"time": "15:00", "action": "unplug", "days": [0]}])
    assert not any(e["time"] == "12:00" and 0 in e["days"] for e in a.entries)  # 13-15 isn't across 12:00
    weekly = sorted((e["time"], e["action"], tuple(e["days"])) for e in a.entries if not e.get("date"))
    assert ("12:00", "plug", (1, 3)) in weekly and ("12:00", "unplug", (1, 3)) in weekly
    once = sorted((e["time"], e["action"], e["date"]) for e in a.entries if e.get("date"))
    assert once == [("08:00", "plug", "2026-10-10"), ("12:00", "plug", "2026-10-10"),
                    ("12:00", "unplug", "2026-10-10"), ("18:00", "unplug", "2026-10-10")]
    tue = datetime.date(2026, 10, 6)
    assert _pairs(a, tue) == [("09:00", "12:00"), ("12:00", "17:00")]
    n = len(a.entries)
    a.set_schedule(entries=a.entries, daily_cap_min=360)  # saving again adds nothing
    assert len(a.entries) == n
    # Force schedule off: left as it is
    b, clock_b = _automation(start=datetime.datetime(2026, 10, 5, 7, 0, tzinfo=TZ))
    b.limit_reset = "12:00"
    b.set_schedule(enabled=True, entries=[{"time": "09:00", "action": "plug", "days": [1]},
                                          {"time": "17:00", "action": "unplug", "days": [1]}], daily_cap_min=360)
    assert len(b.entries) == 2


# --- an unplug is never missed (re-plug, a busy loop, a restart) ------------------------


def _night(a):
    every = list(range(7))
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug", "days": every},
                                          {"time": "07:00", "action": "unplug", "days": every}])


def _timed_loop(a, clock, state, plug, unplug, run_s, monkeypatch):
    """Run the loop on the fake clock: every sleep moves the clock on that far."""
    import types
    from src import automation as auto_mod
    real_sleep = asyncio.sleep

    async def sleep(s):
        clock.advance(seconds=s)
        await real_sleep(0)
    monkeypatch.setattr(auto_mod, "asyncio", types.SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError))

    async def go():
        end = clock.epoch() + run_s
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=15))
        while clock.epoch() < end:
            await real_sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    _run(go())


def _recorder(state, clock):
    calls = []

    async def plug(source=None):
        calls.append((clock.now().strftime("%H:%M:%S"), "plug", source))
        state.plugged_in, state.state = True, "Preparing"

    async def unplug(source=None):
        calls.append((clock.now().strftime("%H:%M:%S"), "unplug", source))
        state.plugged_in, state.state, state.transaction_id = False, "Available", None
    return calls, plug, unplug


def test_no_replug_just_before_a_scheduled_unplug(monkeypatch):
    # No session since 06:49:40: a re-plug would be due at 06:59:40, 20 s before the 07:00 unplug
    a, clock = _automation(options=ReplugOptions(True, 10, 3, 30), start=datetime.datetime(2026, 10, 6, 6, 49, 40, tzinfo=TZ))
    _night(a)
    state = SharedState(plugged_in=True, state="Preparing", connected_to_server=True)
    calls, plug, unplug = _recorder(state, clock)
    _timed_loop(a, clock, state, plug, unplug, 15 * 60, monkeypatch)
    assert calls == [("07:00:10", "unplug", "schedule")]
    assert not state.plugged_in


def test_a_replug_running_past_the_unplug_doesnt_plug_back_in(monkeypatch):
    # The schedule's unplug changed to 07:00 while a 5-minute re-plug was under way from 06:56
    a, clock = _automation(options=ReplugOptions(True, 10, 3, 300), start=datetime.datetime(2026, 10, 6, 6, 56, tzinfo=TZ))
    every = list(range(7))
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug", "days": every},
                                          {"time": "08:00", "action": "unplug", "days": every}])
    state = SharedState(plugged_in=True, state="Preparing", connected_to_server=True)
    calls, plug, unplug = _recorder(state, clock)

    async def unplug_and_reschedule(source=None):
        await unplug(source)
        a.set_schedule(entries=[{"time": "23:30", "action": "plug", "days": every},
                                {"time": "07:00", "action": "unplug", "days": every}])
    import types
    from src import automation as auto_mod
    real_sleep = asyncio.sleep

    async def sleep(s):
        clock.advance(seconds=s)
        await real_sleep(0)
    monkeypatch.setattr(auto_mod, "asyncio", types.SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError))
    _run(a.run_replug(unplug_and_reschedule, plug))
    assert calls == [("06:56:00", "unplug", "auto re-plug")]  # 07:01: past the unplug, so not plugged back in
    assert not state.plugged_in


def test_a_late_unplug_still_runs_if_nothing_came_after_it():
    a, clock = _automation(start=datetime.datetime(2026, 10, 6, 6, 55, tzinfo=TZ))
    _night(a)
    a.due()
    clock.advance(minutes=11)  # the loop was busy (e.g. re-plugging) through 07:00
    assert [e["action"] for e in a.due()] == ["unplug"]
    a, clock = _automation(start=datetime.datetime(2026, 10, 6, 23, 25, tzinfo=TZ))
    _night(a)
    a.due()
    clock.advance(minutes=16)  # (more than CATCH_UP_S, 10 minutes)
    assert a.due() == []  # a late plug-in isn't (as before)


def test_an_unplug_missed_while_stopped_runs_at_start_up(tmp_path):
    a, clock = _automation(tmp_path, start=datetime.datetime(2026, 10, 6, 6, 58, tzinfo=TZ))
    _night(a)
    a.due()
    a.save_runtime()
    # Restarted (or updated): back at 07:04, the car still plugged in (maybe with its session continued)
    b, clock2 = _automation(tmp_path, start=datetime.datetime(2026, 10, 6, 7, 4, tzinfo=TZ))
    assert [e["action"] for e in b.due()] == ["unplug"]
    assert b.due() == []  # once
    # A plug-in missed while stopped is left to the start-up check
    c, _ = _automation(tmp_path, start=datetime.datetime(2026, 10, 6, 23, 29, tzinfo=TZ))
    c.due()
    c.save_runtime()
    d, _ = _automation(tmp_path, start=datetime.datetime(2026, 10, 6, 23, 33, tzinfo=TZ))
    assert d.due() == []


def test_without_a_record_of_the_last_check_nothing_is_caught_up(tmp_path):
    a, _ = _automation(tmp_path, start=datetime.datetime(2026, 10, 6, 7, 4, tzinfo=TZ))
    _night(a)
    assert a.due() == []


def test_a_waiting_unplug_survives_a_restart(tmp_path):
    a, clock = _automation(tmp_path, start=datetime.datetime(2026, 10, 6, 7, 0, 5, tzinfo=TZ))
    a.due()
    a.unplug_at = clock.epoch() + 60
    a.save_runtime()
    b, _ = _automation(tmp_path, start=datetime.datetime(2026, 10, 6, 7, 0, 30, tzinfo=TZ))
    assert b.unplug_at == a.unplug_at


# --- random start delay ------------------------------------------------------


def _delay_loop(a, clock, steps, state=None):
    """Run the loop, moving the clock to each of `steps` (datetimes) in turn; the plug/unplug calls."""
    state = state or SharedState(connected_to_server=True)
    calls = []

    async def plug(source=None):
        calls.append(("plug", clock.dt.strftime("%H:%M:%S")))
        state.plugged_in = True
        state.state = "Preparing"

    async def unplug(source=None):
        calls.append(("unplug", clock.dt.strftime("%H:%M:%S")))
        state.plugged_in = False

    async def scenario():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01))
        await asyncio.sleep(0.03)
        for t in steps:
            clock.dt = t
            await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
    return calls, state


def test_random_start_delay_plugs_in_later(tmp_path):
    a, clock = _automation(tmp_path, start=datetime.datetime(2026, 10, 5, 23, 29, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug"}, {"time": "07:00", "action": "unplug"}],
                   start_delay={"enabled": True, "max_min": 10})
    a._random = lambda low, high: 7 * 60  # 7 minutes
    at = lambda hh, mm, ss=0: datetime.datetime(2026, 10, 5, hh, mm, ss, tzinfo=TZ)
    calls, state = _delay_loop(a, clock, [at(23, 30, 5), at(23, 36), at(23, 37, 10), at(23, 40)])
    assert calls == [("plug", "23:37:10")]  # (7 minutes after it was due, at 23:30:05)
    assert a.plug_at is None and state.plug_pending is None
    # Saved with the schedule
    again = Automation(str(tmp_path), now=clock.now, clock=clock.epoch)
    assert again.start_delay == {"enabled": True, "max_min": 10}


def test_random_start_delay_shows_while_waiting():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 23, 29, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug"}, {"time": "07:00", "action": "unplug"}],
                   start_delay={"enabled": True, "max_min": 10})
    a._random = lambda low, high: 5 * 60
    calls, state = _delay_loop(a, clock, [datetime.datetime(2026, 10, 5, 23, 30, 5, tzinfo=TZ)])
    assert calls == [] and state.plug_pending and a.snapshot()["schedule"]["plug_pending"]


def test_random_start_delay_not_if_the_schedule_is_turned_off_meanwhile():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 23, 29, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug"}, {"time": "07:00", "action": "unplug"}],
                   start_delay={"enabled": True, "max_min": 10})
    a._random = lambda low, high: 5 * 60
    at = lambda hh, mm, ss=0: datetime.datetime(2026, 10, 5, hh, mm, ss, tzinfo=TZ)
    state = SharedState(connected_to_server=True)
    calls = []

    async def plug(source=None):
        calls.append("plug")

    async def unplug(source=None):
        calls.append("unplug")

    async def scenario():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01))
        await asyncio.sleep(0.03)
        clock.dt = at(23, 30, 5)
        await asyncio.sleep(0.05)
        a.set_schedule(enabled=False)
        clock.dt = at(23, 36)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
    assert calls == [] and a.plug_at is None


def test_random_start_delay_is_only_for_your_own_slots():
    """Auto plug-in, Charge now, the car plugged in sensor and the charge before a saving
    session plug in straight away; the delay is at most half the slot."""
    from src.automation import AUTO_SOURCES
    a, clock = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug"}, {"time": "23:40", "action": "unplug"}],
                   start_delay={"enabled": True, "max_min": 30})
    a._random = lambda low, high: high
    at = datetime.datetime(2026, 10, 5, 23, 30, tzinfo=TZ)
    mine = next(e for e in a.entries if e["action"] == "plug")
    assert a.start_delay_s(mine, at) == 300  # half the 10-minute slot, not 30 minutes
    for source in AUTO_SOURCES:
        assert a.start_delay_s(dict(mine, source=source), at) == 0
    a.set_schedule(start_delay={"enabled": False})
    assert a.start_delay_s(mine, at) == 0 and a.start_delay["max_min"] == 30
    a.set_schedule(start_delay={"max_min": 20})  # changed with it off: kept for when it's on
    assert a.start_delay == {"enabled": False, "max_min": 20}


def test_a_charge_now_plugs_in_straight_away_with_the_delay_on():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 23, 29, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, entries=[{"time": "23:30", "action": "plug", "date": "2026-10-05", "source": "charge_now"},
                                          {"time": "23:59", "action": "unplug", "date": "2026-10-05", "source": "charge_now"}],
                   start_delay={"enabled": True, "max_min": 10})
    a._random = lambda low, high: 5 * 60
    calls, _ = _delay_loop(a, clock, [datetime.datetime(2026, 10, 5, 23, 30, 5, tzinfo=TZ)])
    assert calls == [("plug", "23:30:05")]


def test_random_start_delay_settings_are_checked():
    a, _ = _automation()
    for bad in ({"max_min": 0}, {"max_min": 61}, {"max_min": "x"}, "on"):
        with pytest.raises(ValueError):
            a.set_schedule(start_delay=bad)
    assert a.start_delay == {"enabled": False, "max_min": 10}


# --- 2.52.0: from the review ---------------------------------------------------


def _loop(a, clock, steps, state, set_ready=None):
    """Run the loop through `steps` (datetimes); returns the plug/unplug calls."""
    calls = []

    async def plug(source=None):
        calls.append(("plug", clock.dt.strftime("%H:%M")))
        state.plugged_in = True

    async def unplug(source=None):
        calls.append(("unplug", clock.dt.strftime("%H:%M")))
        state.plugged_in = False

    async def scenario():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
        await asyncio.sleep(0.03)
        for t in steps:
            clock.dt = t
            await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
    return calls


def test_turning_the_delay_off_lets_a_waiting_plug_in_go_ahead():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 9, 59, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, entries=[{"time": "10:00", "action": "plug"}, {"time": "14:00", "action": "unplug"}],
                   start_delay={"enabled": True, "max_min": 30})
    a._random = lambda low, high: high
    state = SharedState(connected_to_server=True)
    at = lambda hh, mm, ss=0: datetime.datetime(2026, 10, 5, hh, mm, ss, tzinfo=TZ)

    calls = []

    async def plug(source=None):
        calls.append(clock.dt.strftime("%H:%M"))
        state.plugged_in = True

    async def unplug(source=None):
        state.plugged_in = False

    async def scenario():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01))
        await asyncio.sleep(0.03)
        clock.dt = at(10, 0, 5)
        await asyncio.sleep(0.05)
        clock.dt = at(10, 5)
        await asyncio.sleep(0.05)
        assert calls == [] and a.plug_at is not None  # waiting until 10:30
        a.set_schedule(start_delay={"enabled": False})
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
    assert calls == ["10:05"] and a.plug_at is None


def test_already_plugged_in_at_a_slot_start_sets_the_ready_time_with_the_delay_on():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 21, 59, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, ready_time=True, entries=[{"time": "22:00", "action": "plug"}, {"time": "04:00", "action": "unplug"}],
                   start_delay={"enabled": True, "max_min": 30})
    a._random = lambda low, high: high
    asked = []

    async def set_ready(unplug_at):
        asked.append(unplug_at.strftime("%H:%M"))
        return {"ready": unplug_at.strftime("%H:%M")}

    state = SharedState(connected_to_server=True, plugged_in=True)
    calls = _loop(a, clock, [datetime.datetime(2026, 10, 5, 22, 0, 5, tzinfo=TZ)], state, set_ready)
    assert asked == ["04:00"] and a.plug_at is None


def test_a_waiting_plug_in_survives_a_restart(tmp_path):
    a, clock = _automation(tmp_path, start=datetime.datetime(2026, 10, 5, 9, 59, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, entries=[{"time": "10:00", "action": "plug"}, {"time": "14:00", "action": "unplug"}],
                   start_delay={"enabled": True, "max_min": 30})
    a._random = lambda low, high: 20 * 60
    state = SharedState(connected_to_server=True)
    _loop(a, clock, [datetime.datetime(2026, 10, 5, 10, 0, 5, tzinfo=TZ)], state)
    assert a.plug_at is not None
    # Restarted at 10:05: it still waits for 10:20
    b = Automation(str(tmp_path), now=clock.now, clock=clock.epoch, localize=lambda naive: naive.replace(tzinfo=TZ))
    assert b.plug_at == a.plug_at
    state = SharedState(connected_to_server=True)
    calls = _loop(b, clock, [datetime.datetime(2026, 10, 5, 10, 5, tzinfo=TZ), datetime.datetime(2026, 10, 5, 10, 20, 10, tzinfo=TZ)], state)
    assert calls == [("plug", "10:20")]


def test_a_restart_where_two_slots_meet_leaves_it_plugged_in(tmp_path):
    thu = "2026-10-08"
    a, clock = _automation(tmp_path, start=datetime.datetime(2026, 10, 8, 11, 59, 40, tzinfo=TZ))
    a.set_schedule(enabled=True, entries=[
        {"time": "06:00", "action": "plug", "date": thu}, {"time": "12:00", "action": "unplug", "date": thu},
        {"time": "12:00", "action": "plug", "date": thu}, {"time": "18:00", "action": "unplug", "date": thu}])
    a.due()
    a.save_runtime()
    clock.dt = datetime.datetime(2026, 10, 8, 12, 2, tzinfo=TZ)  # stopped 11:59:40-12:02
    b = Automation(str(tmp_path), now=clock.now, clock=clock.epoch, localize=lambda naive: naive.replace(tzinfo=TZ))
    state = SharedState(connected_to_server=True, plugged_in=True)
    calls = _loop(b, clock, [datetime.datetime(2026, 10, 8, 13, 0, tzinfo=TZ)], state)
    assert calls == [] and state.plugged_in


def test_a_car_slot_is_plugged_in_at_start_up_with_the_schedule_off():
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 19, 2, tzinfo=TZ))
    a.set_schedule(entries=[{"time": "19:00", "action": "plug", "date": "2026-10-05", "source": "charge_now"},
                            {"time": "22:00", "action": "unplug", "date": "2026-10-05", "source": "charge_now"}])
    assert not a.schedule_enabled
    state = SharedState(connected_to_server=True)
    calls = _loop(a, clock, [datetime.datetime(2026, 10, 5, 19, 3, tzinfo=TZ)], state)
    assert calls == [("plug", "19:02")]


def test_clocks_going_forward():
    """A time that doesn't exist (01:30 on the UK's spring-forward night) is 02:00; a slot
    that's all in the missing hour (01:00-02:00) doesn't run at all."""
    from zoneinfo import ZoneInfo
    LON = ZoneInfo("Europe/London")
    a, clock = _automation(start=datetime.datetime(2027, 3, 26, 12, 0, tzinfo=LON))
    a._localize = lambda naive: naive.replace(tzinfo=LON)
    a.set_schedule(enabled=True, entries=[{"time": "01:30", "action": "plug"}, {"time": "02:30", "action": "unplug"}])
    windows = [(x.isoformat(), y.isoformat()) for x, y in a.windows()]
    assert ("2027-03-28T02:00:00+01:00", "2027-03-28T02:30:00+01:00") in windows
    assert all(datetime.datetime.fromisoformat(y) - datetime.datetime.fromisoformat(x) < datetime.timedelta(hours=2)
               for x, y in windows)
    a.set_schedule(entries=[{"time": "01:00", "action": "plug"}, {"time": "02:00", "action": "unplug"}])
    on_the_day = [occ for occ, e, _ in a.timeline(datetime.date(2027, 3, 28), 1)]
    assert on_the_day == []


def test_settings_only_take_real_booleans():
    a, _ = _automation()
    for bad in (dict(enabled="false"), dict(ready_time="no"), dict(override_guards=1),
                dict(start_delay={"enabled": "true"}), dict(entries=[{"time": "10:00", "action": "plug", "enabled": "false"}])):
        with pytest.raises(ValueError):
            a.set_schedule(**bad)
    assert not a.schedule_enabled
    with pytest.raises(ValueError):
        a.set_skips(["x"])  # not {entry_id, date}: refused, not a crash


def test_one_offs_can_be_at_most_two_months_ahead():
    a, _ = _automation()  # Mon 5 Oct 2026
    a.set_schedule(entries=[{"time": "10:00", "action": "plug", "date": "2026-12-01"}])
    with pytest.raises(ValueError):
        a.set_schedule(entries=[{"time": "10:00", "action": "plug", "date": "2036-01-01"}])


def test_a_refused_turn_on_puts_everything_back():
    """Turning the schedule on with other changes in the same request: if it's refused, none apply."""
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 13, 0, tzinfo=TZ))
    a.set_schedule(ready_time=True, entries=[{"time": "14:00", "action": "plug"}, {"time": "18:00", "action": "unplug"}])
    entries, blocks = json.dumps(a.entries, sort_keys=True), json.dumps(a.blocks)

    def refuse(*args, **kw):
        raise ValueError("over the limit")
    a._join_charges, a._makes_worse, a.check_daily_cap = (lambda: 1), (lambda *x: True), refuse
    with pytest.raises(ValueError):
        a.set_schedule(enabled=True, daily_cap_min=360, blocks=[{"start": "02:00", "end": "03:00"}],
                       entries=[{"time": "14:00", "action": "plug"}, {"time": "19:00", "action": "unplug"}])
    assert not a.schedule_enabled
    assert json.dumps(a.blocks) == blocks and [e["time"] for e in a.entries] == ["14:00", "18:00"]


def test_cancelling_a_charge_now_puts_the_schedule_back():
    from src.automation import CHARGE_NOW_SOURCE
    a, clock = _sched_18_22(_at(17))
    before = [(x.isoformat(), y.isoformat()) for x, y in a.windows()]
    a.plan_auto_plug(120, source=CHARGE_NOW_SOURCE)  # 17:00-19:00, joins the 18:00-22:00 slot
    assert [(x.isoformat(), y.isoformat()) for x, y in a.windows()] != before
    clock.advance(minutes=30)
    assert a.cancel_charge() > 0
    assert [(x.isoformat(), y.isoformat()) for x, y in a.windows()] == before
    assert not [e for e in a.entries if e.get("source") == CHARGE_NOW_SOURCE]
    assert a.cancel_charge() == 0  # nothing left to cancel


def test_cancelling_a_charge_now_with_no_schedule():
    from src.automation import CHARGE_NOW_SOURCE
    a, clock = _automation(start=datetime.datetime(2026, 10, 5, 10, 0, tzinfo=TZ))
    a.plan_auto_plug(180, source=CHARGE_NOW_SOURCE)
    clock.advance(minutes=30)
    assert a.in_charge_window() is not None
    assert a.cancel_charge() == 2 and a.entries == [] and a.in_charge_window() is None
    assert a.next_action() is None


def test_slots_can_be_changed_with_lots_of_past_one_offs_and_charges():
    """2.53.2: the 50-entry limit counted one-offs that had run (kept 14 days) and the charges
    the add-on adds, so with a fortnight of auto plug-ins no slot could be changed."""
    a, clock = _automation(start=datetime.datetime(2026, 10, 26, 12, 0, tzinfo=TZ))
    past = []
    for d in range(1, 14):
        day = (datetime.date(2026, 10, 26) - datetime.timedelta(days=d)).isoformat()
        past += [{"time": "06:30", "action": "plug", "date": day, "source": "auto_plug"},
                 {"time": "08:30", "action": "unplug", "date": day, "source": "auto_plug"},
                 {"time": "12:00", "action": "plug", "date": day}, {"time": "13:00", "action": "unplug", "date": day}]
    mine = [{"time": "18:30", "action": "plug"}, {"time": "21:30", "action": "unplug"}]
    a.set_schedule(enabled=True, entries=past + mine)
    assert len(a.entries) > 50
    # turning the slot off, and moving it 30 minutes
    entries = [dict(e, enabled=False) if e.get("time") == "18:30" and not e.get("date") else e for e in a.entries]
    a.set_schedule(entries=entries)
    entries = [dict(e, time="19:00") if e.get("time") == "18:30" and not e.get("date") else e for e in a.entries]
    a.set_schedule(entries=entries)
    assert [e["time"] for e in a.entries if not e.get("date")] == ["19:00", "21:30"]
    # your own are still limited
    with pytest.raises(ValueError, match="of your own"):
        a.set_schedule(entries=[{"time": f"{h:02d}:{m:02d}", "action": "plug"} for h in range(24) for m in (0, 10, 20, 30, 40)])


def test_ready_time_tried_again_when_it_fails():
    """Octopus's integration failing ("Device status could not be fetched"): the
    ready time is tried again a minute later, while it's still plugged in."""
    from src.shared_state import SharedState
    thu = "2026-10-08"
    a, clock = _automation(start=datetime.datetime(2026, 10, 8, 19, 59, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, ready_time=True, entries=[
        {"time": "20:00", "action": "plug", "date": thu}, {"time": "23:30", "action": "unplug", "date": thu}])
    state = SharedState(plugged_in=False)
    asked = []

    async def plug(source=None):
        state.plugged_in = True

    async def unplug(source=None):
        state.plugged_in = False

    async def set_ready(unplug_at):
        asked.append(unplug_at.strftime("%H:%M"))
        if len(asked) == 1:
            raise RuntimeError("Failed - Device status could not be fetched")
        return {"ready": unplug_at.strftime("%H:%M"), "unplug": unplug_at.isoformat()}

    async def ticks():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
        await asyncio.sleep(0.03)
        clock.dt = datetime.datetime(2026, 10, 8, 20, 0, 10, tzinfo=TZ)
        await asyncio.sleep(0.05)
        assert a.ready_status["error"] and a.ready_status["retry_at"] and len(asked) == 1
        clock.dt = datetime.datetime(2026, 10, 8, 20, 1, 20, tzinfo=TZ)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(ticks())
    assert asked == ["23:30", "23:30"] and not a.ready_status.get("error") and a.ready_retry is None


def test_ready_time_follows_a_schedule_change_while_plugged_in():
    """Plugged in for 20:00-23:30 (ready time 23:30), then the slot's end is
    changed to 22:00: the ready time is set again, to 22:00."""
    from src.shared_state import SharedState
    thu = "2026-10-08"
    a, clock = _automation(start=datetime.datetime(2026, 10, 8, 19, 59, 50, tzinfo=TZ))
    a.set_schedule(enabled=True, ready_time=True, entries=[
        {"id": "p1", "time": "20:00", "action": "plug", "date": thu}, {"id": "u1", "time": "23:30", "action": "unplug", "date": thu}])
    state = SharedState(plugged_in=False)
    asked = []

    async def plug(source=None):
        state.plugged_in = True

    async def unplug(source=None):
        state.plugged_in = False

    async def set_ready(unplug_at):
        asked.append(unplug_at.strftime("%H:%M"))
        return {"ready": unplug_at.strftime("%H:%M"), "unplug": unplug_at.isoformat(timespec="minutes")}

    async def ticks():
        task = asyncio.ensure_future(automation_loop(a, state, plug, unplug, tick_s=0.01, set_ready_time=set_ready))
        await asyncio.sleep(0.03)
        clock.dt = datetime.datetime(2026, 10, 8, 20, 0, 10, tzinfo=TZ)
        await asyncio.sleep(0.05)
        clock.dt = datetime.datetime(2026, 10, 8, 20, 30, tzinfo=TZ)
        a.set_schedule(entries=[{"id": "p1", "time": "20:00", "action": "plug", "date": thu},
                                {"id": "u1", "time": "22:00", "action": "unplug", "date": thu}])
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(ticks())
    assert asked == ["23:30", "22:00"]


def test_saving_with_times_to_skip():
    """A new weekly slot saved skipped where it overlaps a one-off (the page's choice)."""
    a, _ = _automation(start=datetime.datetime(2026, 11, 2, 15, 0, tzinfo=TZ))  # Monday
    one_off = [{"id": "o1", "time": "18:00", "action": "plug", "date": "2026-11-02", "enabled": True},
               {"id": "o2", "time": "21:30", "action": "unplug", "date": "2026-11-02", "enabled": True}]
    weekly = [{"id": "w1", "time": "21:00", "action": "plug", "days": list(range(7)), "enabled": True},
              {"id": "w2", "time": "22:00", "action": "unplug", "days": list(range(7)), "enabled": True}]
    skips = [{"entry_id": "w1", "date": "2026-11-02"}, {"entry_id": "w2", "date": "2026-11-02"}]
    a.set_schedule(enabled=True, entries=one_off + weekly, add_skips=skips, daily_cap_min=360)
    assert all(k in a.skips for k in skips)
    assert [w for w in a.windows() if w[0].date() == datetime.date(2026, 11, 2)] == [
        (datetime.datetime(2026, 11, 2, 18, 0, tzinfo=TZ), datetime.datetime(2026, 11, 2, 21, 30, tzinfo=TZ))]
    with pytest.raises(ValueError, match="doesn't run"):
        a.set_schedule(entries=one_off + weekly, add_skips=[{"entry_id": "o1", "date": "2026-11-03"}])
    with pytest.raises(ValueError, match="No such"):
        a.set_schedule(entries=one_off + weekly, add_skips=[{"entry_id": "zz", "date": "2026-11-03"}])
