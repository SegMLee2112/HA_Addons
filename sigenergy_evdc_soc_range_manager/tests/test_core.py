"""The add-on's building blocks, without Home Assistant or a web server."""

import asyncio
import json
import logging

import pytest

from src.app_settings import AppSettings
from src.config import Config, load_config
from src.debug_tools import DebugTools, LogBuffer, redacted_config
from src.ha_entities import SENSORS, SensorPublisher, entity_info
from src.ha_link import HaLink, entity_list, validate_settings
from src.log_filters import DemoteWebPageRequests
from src.shared_state import SharedState


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_options_are_read_with_defaults(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"something_old": 1}))
    assert load_config(str(path)) == Config()
    assert load_config(str(tmp_path / "missing.json")) == Config()


def test_app_settings_are_checked_and_saved(tmp_path):
    seen = []
    s = AppSettings(str(tmp_path), on_change=seen.append)
    assert s.log_level == "info"
    s.update({"log_level": "debug"})
    assert seen == [{"log_level": "debug"}]
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG  # applied straight away
    s.update({"log_level": "info"})
    assert AppSettings(str(tmp_path)).log_level == "info"  # kept after a restart
    for bad in ({"log_level": "loud"}, {}, "x"):
        with pytest.raises(ValueError):
            s.update(bad)


def test_entity_settings_are_checked():
    out = validate_settings({"charge_signal": " binary_sensor.predbat_charging "})
    assert out["charge_signal"] == "binary_sensor.predbat_charging"
    assert out["start_button"] == "button.sigen_inverter_dc_charger_start_charging"  # the defaults
    assert validate_settings({"export_power": ""})["export_power"] == ""  # not used
    for bad in ({"start_button": "sensor.x"}, {"soc_high": 3}, {"nope": 1}, []):
        with pytest.raises(ValueError):
            validate_settings(bad)


def test_link_follows_changes_and_saves(tmp_path):
    seen = []

    async def changed(entity_id, st):
        seen.append((entity_id, (st or {}).get("state")))

    link = HaLink(str(tmp_path), SharedState(), token="", on_entity_changed=changed)
    assert not link.available and "sensor.sigen_plant_battery_power" in link.watched
    _run(link.update_settings({"charge_signal": "binary_sensor.predbat_charging"}))
    assert "binary_sensor.predbat_charging" in link.watched
    rs = "sensor.sigen_inverter_dc_charger_running_state"
    _run(link.handle_entities_event({"a": {rs: {"s": "Occupied", "a": {"friendly_name": "Run"}, "lu": 100.0}}}))
    _run(link.handle_entities_event({"c": {rs: {"+": {"s": "Charging", "lu": 200.0}}}}))
    _run(link.handle_entities_event({"c": {rs: {"+": {"a": {"x": 1}, "lu": 300.0}}}}))  # attributes only
    assert seen[-3:] == [(rs, "Occupied"), (rs, "Charging"), (rs, "Charging")]
    assert link.states[rs]["last_changed"] == 200.0  # only a new state counts as a change
    snap = link.snapshot()
    assert snap["entities"]["running_state"]["state"] == "Charging"
    saved = HaLink(str(tmp_path), SharedState(), token="").settings
    assert saved["charge_signal"] == "binary_sensor.predbat_charging"


def test_entity_list_for_the_pickers():
    out = entity_list([
        {"entity_id": "sensor.b", "state": "1", "attributes": {"friendly_name": "B", "unit_of_measurement": "W"}},
        {"entity_id": "automation.x", "state": "on", "attributes": {}},
        {"entity_id": "input_number.a", "state": "5", "attributes": {"friendly_name": "A"}},
    ])
    assert [e["entity_id"] for e in out] == ["input_number.a", "sensor.b"]


def test_own_sensors_are_posted_when_they_change():
    pub, state = SensorPublisher(), SharedState(status="Idle", inputs={"soc": 55.0, "plugged_in": True, "active": False})
    first = pub.due(state, 0)
    assert {e for e, _, _ in first} == {e for e, _ in SENSORS.values()}
    posted = {e: (v, a) for e, v, a in first}
    assert posted["sensor.sigen_dc_charger_vehicle_soc"][0] == "55.0"
    assert posted["binary_sensor.sigen_dc_charger_plugged_in"][0] == "on"
    assert posted["sensor.sigen_dc_charger_status"][1]["friendly_name"] == "SigEnergy DC Charger Status"
    assert pub.due(state, 10) == []  # nothing changed
    state.reason = "Something new"
    assert [e for e, _, _ in pub.due(state, 20)] == ["sensor.sigen_dc_charger_status"]  # an attribute changed
    state.last_action = {"action": "stop", "rule": "low_limit", "observe_only": True}
    assert [v for e, v, _ in pub.due(state, 30) if e.endswith("last_action")] == ["Would stop"]
    assert len(pub.due(state, 10_000)) == len(SENSORS)  # posted again now and then
    assert all(v == "unavailable" for _, v, _ in pub.unavailable())
    assert entity_info({}, "") is None


def test_debug_tools_hide_secrets_and_set_the_log_level(tmp_path):
    class Opts:
        def __init__(self):
            self.name, self.password, self.api_token = "x", "hunter2", "abc"

    assert redacted_config(Opts()) == {"name": "x", "password": "***", "api_token": "***"}
    settings = AppSettings(str(tmp_path), on_change=lambda c: settings.apply_log_level())
    buf = LogBuffer(5)
    buf.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("t").addHandler(buf)
    logging.getLogger("t").warning("hello")
    debug = DebugTools(config=Config(), log_buffer=buf, settings=settings)
    assert debug.info()["logs"] == ["hello"] and debug.info()["config"] == {}
    debug.set_log_level("debug")
    assert settings.log_level == "debug" and logging.getLogger().getEffectiveLevel() == logging.DEBUG
    with pytest.raises(ValueError):
        debug.set_log_level("loud")
    settings.update({"log_level": "info"})
    logging.getLogger("t").removeHandler(buf)


def test_page_polling_is_kept_out_of_the_log():
    f = DemoteWebPageRequests()

    def rec(msg):
        return logging.LogRecord("aiohttp.access", logging.INFO, "", 0, msg, None, None)

    logging.getLogger().setLevel(logging.INFO)
    assert not f.filter(rec('1.2.3.4 "GET /api/state HTTP/1.1" 200 12'))
    assert f.filter(rec('1.2.3.4 "POST /api/sensors HTTP/1.1" 200 12'))
    assert f.filter(rec('1.2.3.4 "GET /api/state HTTP/1.1" 500 12'))


def test_battery_capacity_is_set_on_its_helper_or_a_new_one():
    from src.helpers import capacity_target
    assert capacity_target("input_number.vehicle_max_capacity", "42.5") == ("input_number", 42.5)
    assert capacity_target("number.car_capacity", 60) == ("number", 60.0)
    # From a sensor or nothing: the add-on's own helper is made instead
    assert capacity_target("sensor.sigen_dc_charger_capacity_estimate", 42.5) == (None, 42.5)
    assert capacity_target("", 42.5) == (None, 42.5)
    for value, says in [(0.5, "between 1 and 200"), (None, "in kWh")]:
        try:
            capacity_target("input_number.vehicle_max_capacity", value)
            raise AssertionError("should have refused")
        except ValueError as err:
            assert says in str(err)


def test_the_capacity_helper_is_made_or_found_and_picked(tmp_path):
    from src.helpers import make_capacity_helper

    class Link:
        def __init__(self, have):
            self.have, self.queries, self.settings = have, [], {}

        async def all_states(self, fresh=False):
            return [{"entity_id": e} for e in self.have]

        async def query(self, msg):
            self.queries.append(msg)
            return {"id": "sigen_dc_charger_vehicle_battery_capacity"} if msg["type"].endswith("/create") else None

        async def update_settings(self, changes):
            self.settings.update(changes)

    new = Link([])
    assert asyncio.run(make_capacity_helper(new, 42.5)) == "input_number.sigen_dc_charger_vehicle_battery_capacity"
    assert new.queries[0]["type"] == "input_number/create" and new.queries[0]["initial"] == 42.5
    assert new.queries[1]["type"] == "input_number/update" and new.queries[1]["name"] == "SigEnergy DC Charger Vehicle Battery Capacity"
    assert new.settings == {"capacity": "input_number.sigen_dc_charger_vehicle_battery_capacity"}
    found = Link(["input_number.sigen_dc_charger_vehicle_battery_capacity"])
    assert asyncio.run(make_capacity_helper(found, 42.5)) == "input_number.sigen_dc_charger_vehicle_battery_capacity"
    assert found.queries == [] and found.settings["capacity"] == "input_number.sigen_dc_charger_vehicle_battery_capacity"
