import asyncio
import datetime

import pytest

from src.ha_link import HaLink, power_kw, soc_value, validate_settings
from src.shared_state import SharedState


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class Fake:
    def __init__(self):
        self.state = SharedState()
        self.power = []
        self.soc = []
        self.plugs = 0

    def set_power(self, kw):
        self.power.append(kw)

    async def set_soc(self, soc):
        self.soc.append(soc)

    async def plug(self, source=None):
        self.plugs += 1
        self.state.plugged_in = True


def _link(tmp_path=None, **settings):
    fake = Fake()
    link = HaLink(str(tmp_path) if tmp_path else None, fake.state, fake.set_power, fake.set_soc, fake.plug, token="t")
    if settings:
        _run(link.update_settings(settings))
    fake.power.clear()
    fake.soc.clear()
    return link, fake


def _added(**states):
    return {"a": {k: {"s": v, "a": {}} for k, v in states.items()}}


def _changed(entity_id, state):
    return {"c": {entity_id: {"+": {"s": state}}}}


# --- values ------------------------------------------------------------------


def test_power_units():
    assert power_kw({"state": "1370", "attributes": {"unit_of_measurement": "W"}}) == 1.37
    assert power_kw({"state": "1.37", "attributes": {"unit_of_measurement": "kW"}}) == 1.37
    assert power_kw({"state": "1370", "attributes": {}}) == 1.37  # W assumed
    assert power_kw({"state": "unavailable", "attributes": {}}) is None
    assert soc_value({"state": "64"}) == 64 and soc_value({"state": "140"}) is None


def test_trim_from_is_start_or_end():
    assert validate_settings({})["trim_from"] == "start"
    assert validate_settings({"trim_from": "end"})["trim_from"] == "end"
    for bad in ("middle", True, None):
        with pytest.raises(ValueError):
            validate_settings({"trim_from": bad})


def test_validate_settings():
    s = validate_settings({"power_entity": "sensor.p", "auto_plug_soc": "25"})
    assert s["power_entity"] == "sensor.p" and s["auto_plug_soc"] == 25 and s["soc_entity"] == ""
    for bad in ({"plug_entity": "sensor.x"}, {"soc_entity": "binary_sensor.x"},
                {"auto_plug_soc": 0}, {"auto_plug_soc": "x"}, {"power_entity": 5}):
        with pytest.raises(ValueError):
            validate_settings(bad)


# --- following entities -------------------------------------------------------


def test_power_and_soc_sent_on_change():
    link, fake = _link(power_entity="sensor.p", soc_entity="sensor.soc")
    _run(link.handle_entities_event({"a": {
        "sensor.p": {"s": "2000", "a": {"unit_of_measurement": "W"}},
        "sensor.soc": {"s": "64", "a": {}},
    }}))
    assert fake.power == [2.0] and fake.soc == [64.0]
    _run(link.handle_entities_event({"c": {"sensor.p": {"+": {"s": "1.5", "a": {"unit_of_measurement": "kW"}}}}}))
    assert fake.power[-1] == 1.5
    _run(link.handle_entities_event(_changed("sensor.soc", "unavailable")))
    assert fake.soc[-1] is None


def test_unset_sensors_cleared():
    link, fake = _link(power_entity="sensor.p", soc_entity="sensor.soc")
    _run(link.update_settings({"power_entity": "", "soc_entity": ""}))
    assert fake.power == [None] and fake.soc == [None]
    assert link.watched == []


def test_car_connected_plugs_in_on_off_to_on_only():
    link, fake = _link(plug_entity="binary_sensor.cable")
    _run(link.handle_entities_event(_added(**{"binary_sensor.cable": "on"})))
    assert fake.plugs == 0  # first reading only
    _run(link.handle_entities_event(_changed("binary_sensor.cable", "off")))
    _run(link.handle_entities_event(_changed("binary_sensor.cable", "on")))
    assert fake.plugs == 1
    fake.state.plugged_in = True
    _run(link.handle_entities_event(_changed("binary_sensor.cable", "off")))
    assert fake.plugs == 1  # never unplugs


def test_auto_plug_on_soc_drop_watching_reporting_sensor():
    link, fake = _link(soc_entity="sensor.soc", auto_plug=True, auto_plug_soc=30)
    assert link.watched == ["sensor.soc"]
    _run(link.handle_entities_event(_added(**{"sensor.soc": "50"})))
    _run(link.handle_entities_event(_changed("sensor.soc", "29")))
    assert fake.plugs == 1
    snap = link.snapshot()["monitored_soc"]
    assert snap["soc"] == 29 and snap["source"] == "reporting SoC sensor" and snap["auto_plug"] is True


def test_auto_plug_monitor_sensor_not_reported():
    link, fake = _link(auto_plug=True, auto_plug_entity="sensor.away_soc", auto_plug_soc=30)
    _run(link.handle_entities_event(_added(**{"sensor.away_soc": "40"})))
    _run(link.handle_entities_event(_changed("sensor.away_soc", "20")))
    assert fake.plugs == 1 and fake.soc == []
    assert link.snapshot()["monitored_soc"]["source"] == "monitor sensor"


def test_auto_plug_off_without_a_sensor():
    link, _ = _link(auto_plug=True)
    assert link.auto_plug.enabled is False and link.watched == []


def test_settings_saved_and_configured_flag(tmp_path):
    link, _ = _link(tmp_path)
    assert link.configured is False
    _run(link.update_settings({"soc_entity": "sensor.soc"}))
    again, _ = _link(tmp_path)
    assert again.configured is True and again.settings["soc_entity"] == "sensor.soc"


def test_no_supervisor_means_link_off():
    fake = Fake()
    link = HaLink(None, fake.state, fake.set_power, fake.set_soc, fake.plug, token="")
    _run(link.run())  # returns at once
    assert link.available is False and "Supervisor" in link.error


def test_supplier_settings_are_checked():
    s = validate_settings({"supplier_entity": "binary_sensor.x_intelligent_dispatching", "ready_entity": "select.x_target_time",
                           "daily_limit_h": "5.5", "ready_from": "04:00", "ready_to": "11:00"})
    assert s["daily_limit_h"] == 5.5 and s["ready_from"] == "04:00"
    assert validate_settings({"daily_limit_h": ""})["daily_limit_h"] is None  # back to automatic
    for bad in ({"supplier_entity": "switch.x"}, {"ready_entity": "sensor.x"}, {"daily_limit_h": 25},
                {"daily_limit_h": 1.2}, {"ready_from": "04:15"}, {"ready_from": "11:00", "ready_to": "04:00"},
                {"limit_reset": "12:15"}, {"limit_reset": "daily"}, {"limit_hours": 0}, {"limit_hours": 1.5},
                {"limit_hours": 200}, {"limit_hours": 4}, {"limit_hours": 8, "daily_limit_h": 10}):
        with pytest.raises(ValueError):
            validate_settings(bad)


def test_supplier_daily_limit_and_ready_times(tmp_path):
    link, fake = _link(tmp_path)
    assert link.daily_limit_min("Octopus Energy") == 360 and link.daily_limit_min("EDF Energy") is None
    _run(link.update_settings({"daily_limit_h": 5}))
    assert link.daily_limit_min("Octopus Energy") == 300 and link.daily_limit_min("EDF Energy") == 300
    _run(link.update_settings({"daily_limit_h": 0}))
    assert link.daily_limit_min("Octopus Energy") is None  # 0: no limit
    # Octopus's limit resets at 12:00; the others' over any 24 hours
    assert link.limit_reset("Octopus Energy") == "12:00" and link.limit_reset("EDF Energy") is None
    _run(link.update_settings({"limit_reset": "rolling"}))
    assert link.limit_reset("Octopus Energy") is None
    _run(link.update_settings({"limit_reset": "48h"}))  # 2.31.0's: rolling, 48 hours
    assert link.limit_reset("Octopus Energy") == "48h" and link.settings["limit_reset"] == "rolling"
    _run(link.update_settings({"limit_hours": 36}))
    assert link.limit_reset("Octopus Energy") == "36h"
    _run(link.update_settings({"limit_hours": None}))
    assert link.limit_reset("Octopus Energy") is None
    _run(link.update_settings({"limit_reset": "00:00"}))
    assert link.limit_reset("Octopus Energy") == "00:00" and link.limit_reset("EDF Energy") == "00:00"
    _run(link.update_settings({"limit_reset": ""}))
    assert link.limit_reset("Octopus Energy") == "12:00"
    octopus = {"entity_id": "time.octopus_energy_x_intelligent_target_time", "state": "07:00:00", "attributes": {}}
    assert len(link._allowed_times(octopus)) == 48  # any half hour
    _run(link.update_settings({"ready_from": "05:00", "ready_to": "09:30"}))
    assert link._allowed_times(octopus) == ["05:00", "05:30", "06:00", "06:30", "07:00", "07:30", "08:00",
                                            "08:30", "09:00", "09:30"]
    states = [octopus, {"entity_id": "select.mine_target_time", "state": "07:00"}]
    assert link._ready_entity_id(states, "binary_sensor.octopus_energy_x_intelligent_dispatching") == octopus["entity_id"]
    _run(link.update_settings({"ready_entity": "select.mine_target_time"}))
    assert link._ready_entity_id(states, "binary_sensor.octopus_energy_x_intelligent_dispatching") == "select.mine_target_time"


def test_car_plugged_in_can_be_switched_off_and_keeps_its_sensor():
    link, fake = _link(plug_entity="binary_sensor.cable", plug_auto=False, plug_ready=True)
    calls = []

    async def planned(hours, source, plug_source):
        calls.append(hours)

    link.on_auto_plug = planned
    assert link.settings["plug_entity"] == "binary_sensor.cable" and link.snapshot()["plug"]["on"] is False
    _run(link.handle_entities_event(_added(**{"binary_sensor.cable": "off"})))
    _run(link.handle_entities_event(_changed("binary_sensor.cable", "on")))
    assert fake.plugs == 0 and calls == []  # off: nothing plugged in or added
    # Back on: the next time it's connected plugs in (not the cable already connected)
    _run(link.update_settings({"plug_auto": True, "plug_ready": False}))
    assert fake.plugs == 0
    _run(link.handle_entities_event(_changed("binary_sensor.cable", "off")))
    _run(link.handle_entities_event(_changed("binary_sensor.cable", "on")))
    assert fake.plugs == 1
    assert _link()[0].settings["plug_auto"] is True  # on unless switched off, as before


def test_car_plugged_in_sensor_can_add_a_charge():
    link, fake = _link(plug_entity="binary_sensor.cable", plug_ready=True, plug_hours=8)
    calls = []

    async def planned(hours, source, plug_source):
        calls.append((hours, source, plug_source))

    link.on_auto_plug = planned
    _run(link.handle_entities_event(_added(**{"binary_sensor.cable": "off"})))
    _run(link.handle_entities_event(_changed("binary_sensor.cable", "on")))
    assert calls == [(8, "car_plugged", "car plugged in sensor")] and fake.plugs == 0  # it plugs in, not us


def test_supply_voltage_from_a_sensor_or_set():
    from src.charger_sim import ChargerSimulator
    link, fake = _link()
    assert link.voltage() is None  # 230 V
    _run(link.update_settings({"voltage_v": 240}))
    assert link.voltage() == 240
    _run(link.update_settings({"voltage_entity": "sensor.mains_voltage"}))
    assert "sensor.mains_voltage" in link.watched
    link.states["sensor.mains_voltage"] = {"state": "243.6", "attributes": {}}
    assert link.voltage() == 243.6
    link.states["sensor.mains_voltage"] = {"state": "unavailable", "attributes": {}}
    assert link.voltage() == 240  # falls back to the set value
    for bad in ({"voltage_v": 50}, {"voltage_v": "x"}, {"voltage_entity": "switch.x"}):
        with pytest.raises(ValueError):
            validate_settings(bad)
    sim = ChargerSimulator(current_amps=32)
    assert sim.rated_power_kw == 7.4  # 230 V
    sim.voltage_source = lambda: 250.0
    assert sim.rated_power_kw == 8.0 and 248 <= sim.sample_full().voltage <= 252
    sim.voltage_source = lambda: 5.0  # nonsense: ignored
    assert sim.rated_power_kw == 7.4


def test_saving_session_settings_are_checked():
    s = validate_settings({"saving_on": True, "saving_entity": "calendar.x", "saving_plug_hours": 1.5})
    assert s["saving_on"] and s["saving_entity"] == "calendar.x" and s["saving_plug_hours"] == 1.5
    assert s["saving_block"] is True and s["saving_plug"] is False  # the defaults
    for bad in ({"saving_entity": "sensor.x"}, {"saving_plug_hours": 0.2}, {"saving_plug_hours": 1.25}):
        with pytest.raises(ValueError):
            validate_settings(bad)


def test_notify_settings_are_checked_and_sent():
    s = validate_settings({"notify_on": True, "notify_service": "notify.mobile_app_pixel"})
    assert s["notify_on"] and s["notify_service"] == "notify.mobile_app_pixel"
    for bad in ("light.kitchen", "notify.", "notify.Bad Name"):
        with pytest.raises(ValueError):
            validate_settings({"notify_service": bad})
    link, _ = _link()
    sent = []

    async def query(payload, timeout=30):
        sent.append(payload)
        if payload["type"] == "get_services":
            return {"notify": {"mobile_app_pixel": {}, "send_message": {}, "persistent_notification": {}}, "light": {}}
        return {}

    link.query = query
    assert _run(link.notify_services()) == ["notify.mobile_app_pixel", "notify.persistent_notification"]
    assert _run(link.notify("t", "m")) is None and len(sent) == 1  # off: nothing sent
    link.settings.update(notify_on=True, notify_service="notify.mobile_app_pixel")
    assert _run(link.notify("Charging schedule changed", "Today: …")) is None
    assert sent[-1] == {"type": "call_service", "domain": "notify", "service": "mobile_app_pixel",
                        "service_data": {"title": "Charging schedule changed", "message": "Today: …"}}


def test_saving_sessions_read_from_the_found_calendar():
    link, _ = _link()
    cal = "calendar.octopus_energy_a_1_octoplus_power_down"
    asked = []

    async def states():
        return [{"entity_id": cal, "state": "off", "attributes": {"friendly_name": "Power Down"}},
                {"entity_id": "calendar.home", "state": "off", "attributes": {}}]

    async def query(payload, timeout=30):
        asked.append(payload)
        return {"response": {cal: {"events": [
            {"start": "2099-01-05T17:00:00+00:00", "end": "2099-01-05T18:00:00+00:00", "summary": "Power Down"},
            {"start": "2020-01-05T17:00:00+00:00", "end": "2020-01-05T18:00:00+00:00", "summary": "Saving Session"}]}}}

    link.all_states, link.query = states, query
    sessions = _run(link.read_saving_sessions())
    assert len(sessions) == 1 and sessions[0]["start"].hour == 17
    # the ones that are over, for the Sessions tab
    assert link.saving_past == [{"start": "2020-01-05T17:00:00+00:00", "end": "2020-01-05T18:00:00+00:00",
                                 "summary": "Saving Session"}]
    assert asked[0]["service"] == "get_events" and asked[0]["target"] == {"entity_id": cal} and asked[0]["return_response"]
    assert link.saving_info["entity"] == cal and link.saving_info["auto"] and link.saving_info["supplier"] == "Octopus Energy"
    # Axle Energy's sensors weren't there: looked for, not found (the page says so)
    assert link.saving_info["sensors"] == []
    assert link.saving_info["sensors_known"] == [{"name": "Axle Energy", "entity": "sensor.…axle_energy_event_start"}]

    async def no_calendar():
        return [{"entity_id": "calendar.home", "state": "off", "attributes": {}}]

    link.all_states = no_calendar
    assert _run(link.read_saving_sessions()) is None and "No saving sessions calendar" in link.saving_info["error"]


def test_saving_sessions_read_from_axle_energy_without_a_calendar():
    link, _ = _link()
    asked = []

    start = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).replace(
        hour=19, minute=0, second=0, microsecond=0)

    async def states():
        return [{"entity_id": "sensor.axle_energy_event_start", "state": start.isoformat()},
                {"entity_id": "sensor.axle_energy_event_end", "state": (start + datetime.timedelta(hours=1)).isoformat()},
                {"entity_id": "sensor.axle_energy_event_type", "state": "export"}]

    async def query(payload, timeout=30):
        asked.append(payload)
        return {}

    link.all_states, link.query = states, query
    sessions = _run(link.read_saving_sessions())
    assert [(s["start"].hour, s["end"].hour) for s in sessions] == [(19, 20)] and not asked  # no calendar asked
    assert link.saving_info["entity"] is None and link.saving_info["error"] is None
    assert link.saving_info["sensors"] == [{"name": "Axle Energy", "entity": "sensor.axle_energy_event_start"}]

    async def unavailable():
        return [dict(st, state="unavailable") for st in await states()]

    link.all_states = unavailable
    assert _run(link.read_saving_sessions()) is None and "unavailable" in link.saving_info["error"]


def test_read_only_keeps_the_next_slot_sensors_as_they_were(tmp_path):
    """2.95.3: Read only on, the next slot sensors keep what they were then (also after a restart),
    so Predbat plans on what was going to happen; off, they follow the schedule again."""
    from src.ha_entities import slot_sensor_id
    link, fake = _link(tmp_path)
    slot = lambda d: {"date": "2026-11-09", "start": d, "end": "18:00:00"}  # noqa: E731
    fake.state.next_slots = [slot("17:00:00")]
    _run(link.update_settings({"read_only": True}))
    fake.state.next_slots = [slot("19:00:00")]  # (the schedule changed while read only)
    frozen = link.frozen_slots if link.settings.get("read_only") else None
    posted = {e: v for e, v, _ in link.publisher.due(fake.state, 1000.0, frozen)}
    assert posted[slot_sensor_id(1, "start")] == "17:00:00"
    again, _ = _link(tmp_path)  # (restarted)
    assert again.settings["read_only"] and again.frozen_slots == [slot("17:00:00")]
    _run(link.update_settings({"read_only": False}))
    assert link.frozen_slots is None
    posted = {e: v for e, v, _ in link.publisher.due(fake.state, 2000.0, None)}
    assert posted[slot_sensor_id(1, "start")] == "19:00:00"
