"""The schedule's next slots, published for Predbat's rates_import_override."""

import datetime
import enum
import sys
import types

from src.automation import Automation, ReplugOptions
from src.ha_entities import SLOT_NONE, SLOT_SENSORS, SensorPublisher, slot_sensor_id, slot_states
from src.shared_state import SharedState

try:
    import aiohttp  # noqa: F401
except ImportError:  # local runs without aiohttp: a minimal stand-in
    aiohttp = types.ModuleType("aiohttp")

    class WSMsgType(enum.Enum):
        TEXT = 1
        CLOSED = 2
        ERROR = 3
    aiohttp.WSMsgType = WSMsgType
    aiohttp.ClientTimeout = lambda **kw: None
    sys.modules["aiohttp"] = aiohttp

from src.ha_link import default_settings, validate_settings  # noqa: E402

TZ = datetime.timezone.utc
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


class Clock:
    def __init__(self, start):
        self.dt = start

    def now(self):
        return self.dt

    def epoch(self):
        return self.dt.timestamp()


def _automation(start=datetime.datetime(2026, 10, 5, 11, 0, tzinfo=TZ)):  # Monday 11:00
    clock = Clock(start)
    a = Automation(None, ReplugOptions(), now=clock.now, clock=clock.epoch,
                   localize=lambda naive: naive.replace(tzinfo=TZ))
    return a, clock


def _slot(plug, unplug, days=EVERY_DAY, date=None):
    extra = {"date": date} if date else {"days": days}
    return [{"time": plug, "action": "plug", **extra}, {"time": unplug, "action": "unplug", **extra}]


def _brief(slots):
    return [(s["date"], s["start"][:5], s["end"][:5]) for s in slots]


def test_the_next_three_slots():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=_slot("16:00", "21:30"))
    assert _brief(a.next_slots()) == [("2026-10-05", "16:00", "21:30"), ("2026-10-06", "16:00", "21:30"),
                                      ("2026-10-07", "16:00", "21:30")]
    assert a.next_slots()[0] == {"date": "2026-10-05", "start": "16:00:00", "end": "21:30:00"}


def test_a_slot_drops_off_when_it_starts():
    a, clock = _automation()
    a.set_schedule(enabled=True, entries=_slot("16:00", "21:30"))
    clock.dt = clock.dt.replace(hour=16)  # starting now
    assert _brief(a.next_slots())[0] == ("2026-10-06", "16:00", "21:30")
    clock.dt = clock.dt.replace(hour=15, minute=59)
    assert _brief(a.next_slots())[0] == ("2026-10-05", "16:00", "21:30")


def test_plugged_in_moves_on_to_the_slot_after():
    a, clock = _automation()
    a.set_schedule(enabled=True, entries=_slot("16:00", "21:30"))
    clock.dt = clock.dt.replace(hour=15, minute=50)  # plugged in (e.g. by hand) just before the slot
    assert _brief(a.next_slots(plugged_in=True))[0] == ("2026-10-06", "16:00", "21:30")
    assert _brief(a.next_slots(plugged_in=False))[0] == ("2026-10-05", "16:00", "21:30")


def test_overnight_slot_ends_the_next_day():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=_slot("22:00", "02:00"))
    first = a.next_slots()[0]
    assert (first["date"], first["start"], first["end"]) == ("2026-10-05", "22:00:00", "02:00:00")
    a.set_schedule(enabled=True, entries=_slot("18:00", "00:00"))
    assert a.next_slots()[0]["end"] == "00:00:00"  # midnight, which Predbat takes as the next day


def test_slots_that_meet_are_one():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=_slot("13:00", "15:00") + _slot("15:00", "17:00"))
    assert _brief(a.next_slots())[0] == ("2026-10-05", "13:00", "17:00")


def test_slots_that_meet_where_the_limit_resets_stay_two():
    a, _ = _automation(datetime.datetime(2026, 10, 5, 5, 0, tzinfo=TZ))
    a.limit_reset = "12:00"
    a.set_schedule(enabled=True, entries=_slot("09:00", "12:00") + _slot("12:00", "14:00"))
    assert _brief(a.next_slots())[:2] == [("2026-10-05", "09:00", "12:00"), ("2026-10-05", "12:00", "14:00")]


def test_skipped_and_one_off_slots():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=_slot("16:00", "21:30") + _slot("13:00", "14:00", date="2026-10-06"))
    plug = next(e for e in a.entries if e["time"] == "16:00")
    a.set_skips([{"entry_id": plug["id"], "date": "2026-10-05"}])
    assert _brief(a.next_slots()) == [("2026-10-06", "13:00", "14:00"), ("2026-10-06", "16:00", "21:30"),
                                      ("2026-10-07", "16:00", "21:30")]


def test_schedule_off_or_empty():
    a, _ = _automation()
    assert a.next_slots() == []
    a.set_schedule(enabled=True, entries=_slot("16:00", "21:30"))
    a.set_schedule(enabled=False)
    assert a.next_slots() == []  # (only Charge now / auto plug-in run with it off)


def test_over_a_day_is_split_at_midnight():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=[{"time": "20:00", "action": "plug", "date": "2026-10-05"},
                                          {"time": "06:00", "action": "unplug", "date": "2026-10-07"}])
    assert _brief(a.next_slots()) == [("2026-10-05", "20:00", "00:00"), ("2026-10-06", "00:00", "00:00"),
                                      ("2026-10-07", "00:00", "06:00")]


def test_publish_puts_them_in_the_shared_state():
    a, _ = _automation()
    a.set_schedule(enabled=True, entries=_slot("16:00", "21:30"))
    st = SharedState()
    a.publish(st)
    assert len(st.next_slots) == 3 and st.next_slots[0]["start"] == "16:00:00"


# --- the sensors ---------------------------------------------------------------


def test_sensor_names():
    assert slot_sensor_id(1, "date") == "sensor.ocpp_charge_proxy_next_slot_date"
    assert slot_sensor_id(2, "start") == "sensor.ocpp_charge_proxy_next_slot_2_start_time"
    assert slot_sensor_id(3, "end") == "sensor.ocpp_charge_proxy_next_slot_3_end_time"
    assert len(SLOT_SENSORS) == 9 and not any('rate' in e for e in SLOT_SENSORS)


def test_missing_slots_are_none():
    states = dict((e, v) for e, v, _ in slot_states([{"date": "2026-10-05", "start": "16:00:00", "end": "21:30:00"}]))
    assert states[slot_sensor_id(1, "date")] == "2026-10-05"
    assert states[slot_sensor_id(2, "date")] == SLOT_NONE["date"] == "1999-01-01"
    assert states[slot_sensor_id(3, "start")] == states[slot_sensor_id(3, "end")] == "00:00:00"


def _posted(pub, st, now):
    return {e: v for e, v, _ in pub.due(st, now) if e in SLOT_SENSORS}


def test_always_published_on_change():
    pub, st = SensorPublisher(), SharedState()
    first = _posted(pub, st, 1000.0)
    assert len(first) == 9 and set(first.values()) == {"1999-01-01", "00:00:00"}  # none yet
    st.next_slots = [{"date": "2026-10-05", "start": "16:00:00", "end": "21:30:00"}]
    assert _posted(pub, st, 1001.0) == {slot_sensor_id(1, "date"): "2026-10-05",
                                        slot_sensor_id(1, "start"): "16:00:00", slot_sensor_id(1, "end"): "21:30:00"}
    assert _posted(pub, st, 1002.0) == {}
    st.next_slots = [{"date": "2026-10-06", "start": "16:00:00", "end": "21:30:00"}]
    assert _posted(pub, st, 1003.0) == {slot_sensor_id(1, "date"): "2026-10-06"}
    assert len(_posted(pub, st, 1003.0 + 300)) == 9  # periodic refresh


def test_no_setting():
    assert "predbat_slots" not in default_settings()
    assert "predbat_slots" not in validate_settings({"predbat_slots": True}, default_settings())
