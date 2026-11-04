"""The decisions (src/controller.py) and the manager that feeds and acts on
them (src/manager.py), with plain dictionaries for Home Assistant."""

import asyncio
import json
from dataclasses import replace

import pytest

from src.app_settings import AppSettings
from src.controller import Inputs, decide
from src.ha_link import default_settings
from src.manager import Manager, power_kw


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# A car plugged in, in V2X mode, between the limits, charger off for a while, house quiet
BASE = Inputs(plugged_in=True, mode_on=True, running_state="Occupied", active=False, discharging=False,
              inactive_for=600, soc=60, high=80, low=40, battery_kw=0.0, grid_kw=0.0, export_kw=0.0,
              export_held_s=0, ems_mode="Maximum Self Consumption", charge_signal=None)


def d(**changes):
    return decide(replace(BASE, **changes))


# --- what makes it do nothing --------------------------------------------------------


def test_left_alone_unless_plugged_in_v2x_with_a_soc_and_limits():
    assert d(plugged_in=False).rule == "not_plugged_in"
    assert d(mode_on=False).rule == "not_v2x" and d(mode_on=None).rule == "mode_unknown"
    assert d(soc=None).rule == "no_soc"
    assert d(high=None).rule == "no_limits"
    assert d(low=80).rule == "bad_limits" and d(low=80).action is None


# --- starts --------------------------------------------------------------------------


def test_between_the_limits_it_starts_once_the_restart_wait_is_over():
    assert d().action == "start" and d().rule == "in_range"
    waiting = d(inactive_for=100)
    assert waiting.action is None and waiting.rule == "restart_wait" and "80 s" in waiting.reason
    assert waiting.status == "Reconnecting car in 80 s"
    # It wouldn't start once the wait's over (at the high limit, the house quiet): no countdown
    held = d(soc=85, inactive_for=100)
    assert held.action is None and held.rule == "held_high"
    assert d(inactive_for=180).action == "start"
    assert decide(replace(BASE, inactive_for=100), {"restart_wait_s": 60}).action == "start"


def test_house_needing_power_starts_it_above_the_low_limit_even_above_high():
    for power in ({"battery_kw": -2.0}, {"grid_kw": 0.5}):
        assert d(soc=85, **power).rule == "house_needs_power"
        assert d(soc=41, **power).rule == "house_needs_power"
        assert d(soc=40, **power).action is None  # at the low limit: not for discharging
    assert d(soc=85, battery_kw=-0.05).action is None  # below the level: not "needs power"
    assert d(soc=85, battery_kw=None, grid_kw=None).rule == "held_high"  # unknown power doesn't start it


def test_spare_power_starts_it_below_the_high_limit_even_below_low():
    exporting = {"export_kw": 1.2, "export_held_s": 15}
    assert d(soc=30, **exporting).rule == "charge_opportunity"
    assert d(soc=30, export_kw=1.2, export_held_s=0).rule == "charge_opportunity"  # exporting now is enough
    assert d(soc=30, export_kw=0.4, export_held_s=60).action is None  # below the export level
    assert d(soc=30, ems_blocked=True, **exporting).action is None  # the EMS is force-discharging
    assert d(soc=30, charge_signal=True).rule == "charge_opportunity"  # e.g. Predbat charging
    assert "charge signal" in d(soc=30, charge_signal=True).reason
    assert d(soc=80, charge_signal=True).action is None  # at the high limit: no room


def test_nothing_starts_while_it_is_running_or_resting():
    running = {"active": True, "inactive_for": None, "running_state": "Charging"}
    assert d(battery_kw=-3.0, **running).action is None and d(**running).status == "Charging"
    assert d(charge_signal=True, inactive_for=10).action is None


# --- stops ---------------------------------------------------------------------------


def test_stops_at_the_high_limit_once_the_house_is_quiet_and_it_isnt_discharging():
    preparing = {"active": True, "inactive_for": None, "running_state": "Preparing Comm"}
    assert d(soc=80, **preparing).rule == "high_limit" and d(soc=80, **preparing).action == "stop"
    assert d(soc=80, battery_kw=-1.0, **preparing).action is None  # the house needs power: leave it
    assert d(soc=80, battery_kw=None, **preparing).action is None  # can't tell: leave it
    charging = {"active": True, "inactive_for": None, "running_state": "Charging"}
    assert d(soc=80, **charging).rule == "charging_above_high" and d(soc=80, **charging).action == "stop"
    discharging = {**charging, "running_state": "Discharging", "discharging": True}
    assert d(soc=85, **discharging).action is None  # discharging above high is what's wanted
    assert d(soc=80).action is None  # already off: no button press


def test_charging_above_the_high_limit_is_stopped_even_while_importing():
    # Seen: 95% with the high limit at 50%, charging at 7 kW overnight, the grid
    # importing 12 kW (the car and the home battery charging): it was left charging
    charging = {"active": True, "inactive_for": None, "running_state": "Charging"}
    night = {"grid_kw": 12.3, "battery_kw": 4.2, "high": 50, "low": 40}
    stop = d(soc=95, **night, **charging)
    assert stop.action == "stop" and stop.rule == "charging_above_high" and "charging" in stop.reason
    assert d(soc=95, grid_kw=2.0, battery_kw=-1.0, high=50, low=40, **charging).action == "stop"
    assert d(soc=95, charge_signal=True, high=50, low=40, **charging).action == "stop"
    assert d(soc=49, **night, **charging).action is None  # below the high limit: charging's fine
    # ...and isn't started again for the house while the home battery charges from the grid
    held = d(soc=95, grid_kw=4.5, battery_kw=4.2, high=50, low=40)
    assert held.action is None and held.rule == "held_high"
    assert d(soc=95, grid_kw=0.5, charge_signal=True, high=50, low=40).action is None
    assert d(soc=95, grid_kw=0.5, high=50, low=40).rule == "house_needs_power"  # a real need still starts it


def test_a_moments_charging_above_the_high_limit_isnt_stopped():
    # e.g. "Charging" for a few seconds as it starts, before it discharges
    charging = {"active": True, "inactive_for": None, "running_state": "Charging", "high": 50, "low": 40,
                "grid_kw": 9.0, "battery_kw": 2.0}  # importing (the car charging): only the new rule stops it
    assert d(soc=95, charging_for=10, **charging).action is None
    assert d(soc=95, charging_for=60, **charging).rule == "charging_above_high"
    assert decide(replace(BASE, soc=95, charging_for=10, **charging), {"charge_stop_s": 0}).action == "stop"


def test_after_stopping_for_charging_the_house_waits_before_starting_it_above_high():
    house = {"grid_kw": 0.5, "high": 50, "low": 40}
    held = d(soc=95, charge_stopped_ago=300, **house)
    assert held.action is None and held.rule == "held_high" and "25 min" in held.reason
    assert d(soc=95, charge_stopped_ago=1800, **house).rule == "house_needs_power"  # the wait's over
    assert d(soc=45, charge_stopped_ago=300, **house).rule == "house_needs_power"  # below high: as before


def test_stops_at_the_low_limit_when_discharging_unless_told_to_charge():
    discharging = {"active": True, "inactive_for": None, "running_state": "Discharging", "discharging": True}
    assert d(soc=40, **discharging).rule == "low_limit"
    assert d(soc=38, charge_signal=False, **discharging).action == "stop"
    assert d(soc=38, charge_signal=True, **discharging).action is None
    charging = {**discharging, "running_state": "Charging", "discharging": False}
    assert d(soc=38, **charging).action is None  # charging at the low limit is fine


def test_a_margin_keeps_starts_away_from_the_limits():
    t = {"margin_pct": 2}
    assert decide(replace(BASE, soc=79), t).rule == "held_high"
    assert decide(replace(BASE, soc=41), t).rule == "held_low"
    assert decide(replace(BASE, soc=60), t).rule == "in_range"
    on = {"active": True, "inactive_for": None, "running_state": "Charging"}
    assert decide(replace(BASE, soc=80, **on), t).action == "stop"  # stops are still at the limit


# --- the manager: reading Home Assistant and pressing buttons -----------------------------


RS = "sensor.sigen_inverter_dc_charger_running_state"


def _states(running="Occupied", soc="60", battery="0", grid="0", export="0", mode="V2X", lc=0.0,
            high="80", low="40", battery_unit="kW"):
    return {
        RS: {"state": running, "attributes": {}, "last_changed": lc},
        "sensor.sigen_inverter_dc_charger_vehicle_soc": {"state": soc, "attributes": {}},
        "sensor.sigen_plant_battery_power": {"state": battery, "attributes": {"unit_of_measurement": battery_unit}},
        "sensor.sigen_plant_grid_active_power": {"state": grid, "attributes": {"unit_of_measurement": "kW"}},
        "sensor.sigen_plant_grid_export_power": {"state": export, "attributes": {"unit_of_measurement": "kW"}},
        "input_select.sigenergy_evdc_charging_mode": {"state": mode, "attributes": {}},
        "input_number.high": {"state": high, "attributes": {}},
        "input_number.low": {"state": low, "attributes": {}},
        "input_number.cap": {"state": "64", "attributes": {}},
    }


def _entities(**extra):
    return {**default_settings(), "v2x_mode": "input_select.sigenergy_evdc_charging_mode",
            "soc_high": "input_number.high", "soc_low": "input_number.low", "capacity": "input_number.cap", **extra}


def _settings(**changes):
    return {**AppSettings().data, **changes}


class Presses:
    def __init__(self):
        self.pressed, self.notes = [], []

    async def press(self, entity_id):
        self.pressed.append(entity_id)

    async def notify(self, title, message):
        self.notes.append(title)


def test_power_units_are_converted_to_kw():
    assert power_kw({"state": "1500", "attributes": {"unit_of_measurement": "W"}}) == 1.5
    assert power_kw({"state": "-0.2", "attributes": {"unit_of_measurement": "kW"}}) == -0.2
    assert power_kw({"state": "unavailable", "attributes": {}}) is None and power_kw(None) is None


def test_manager_starts_in_range_after_the_restart_wait_and_presses_start(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press, notify=p.notify)
    ent, st = _entities(), _settings()
    first = _run(m.step(_states(lc=1000.0), ent, st, now=1100.0))  # stopped 100 s ago
    assert first.rule == "restart_wait" and p.pressed == []
    second = _run(m.step(_states(lc=1000.0), ent, st, now=1200.0))
    assert second.rule == "in_range" and p.pressed == ["button.sigen_inverter_dc_charger_start_charging"]
    assert m.presses_today == 1 and m.log[-1]["pressed"] and m.log[-1]["soc"] == 60
    # The charger takes a moment to start: no second press within the press gap
    third = _run(m.step(_states(lc=1000.0), ent, st, now=1230.0))
    assert third.rule == "press_gap" and len(p.pressed) == 1
    assert third.status == "Starting" and "waiting for the charger" in third.reason  # not "Waiting to retry"
    assert p.notes == []  # no notify service set


def test_manager_stops_charging_above_high_after_a_minute_then_waits_for_the_house(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    over = dict(soc="95", grid="12", battery="4")
    _run(m.step(_states(running="Charging", **over), ent, st, now=1000.0))
    early = _run(m.step(_states(running="Charging", **over), ent, st, now=1030.0))
    assert early.action is None and p.pressed == []
    stop = _run(m.step(_states(running="Charging", **over), ent, st, now=1061.0))
    assert stop.rule == "charging_above_high" and p.pressed == ["button.sigen_inverter_dc_charger_stop_charging"]
    # Stopped; the house imports for real (battery idle), but it charged last time: held for a while
    held = _run(m.step(_states(running="Occupied", soc="95", grid="0.5", battery="0"), ent, st, now=1061.0 + 600))
    assert held.rule == "held_high" and len(p.pressed) == 1
    later = _run(m.step(_states(running="Occupied", soc="95", grid="0.5", battery="0"), ent, st, now=1061.0 + 1800))
    assert later.rule == "house_needs_power" and len(p.pressed) == 2


def test_after_a_charging_stop_a_new_spell_of_importing_gets_a_fresh_try(tmp_path):
    # The plant swings between importing and exporting: each import may start it to discharge
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    _run(m.step(_states(running="Charging", soc="95", grid="9", battery="0"), ent, st, now=1000.0))
    _run(m.step(_states(running="Charging", soc="95", grid="9", battery="0"), ent, st, now=1061.0))
    assert len(p.pressed) == 1  # stopped: it charged
    held = _run(m.step(_states(running="Occupied", soc="95", grid="0.5"), ent, st, now=1300.0))
    assert held.rule == "held_high" and len(p.pressed) == 1  # the same spell of importing: held
    _run(m.step(_states(running="Occupied", soc="95", grid="-3", export="3"), ent, st, now=1310.0))  # exporting
    again = _run(m.step(_states(running="Occupied", soc="95", grid="0.5"), ent, st, now=1490.0))  # after the restart wait
    assert again.rule == "house_needs_power" and len(p.pressed) == 2  # importing again: a fresh try


def test_manager_backs_off_when_the_charger_doesnt_respond(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    t = 10000.0
    for _ in range(3):  # in range, charger off: Start, no response, again after the press gap
        _run(m.step(_states(lc=0.0), ent, st, now=t))
        t += 61
    assert len(p.pressed) == 3 and "hasn't responded to 3" in (m.log[-1]["error"] or "")
    _run(m.step(_states(lc=0.0), ent, st, now=t + 300))
    assert len(p.pressed) == 3  # backing off
    _run(m.step(_states(lc=0.0), ent, st, now=t + 900))
    assert len(p.pressed) == 4
    # It responds: back to normal, and noted as this add-on's start
    _run(m.step(_states(running="Charging"), ent, st, now=t + 905))
    assert m.unanswered == 0 and m.log[-1]["action"] == "start"


def test_manager_follows_the_charger_stopping_on_its_own(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    _run(m.step(_states(running="Discharging", battery="-1"), ent, st, now=5000.0))
    assert m.active and p.pressed == []
    # The car stops discharging by itself (some cars do): wait before starting it again
    d1 = _run(m.step(_states(running="Occupied", battery="-1"), ent, st, now=5010.0))
    assert d1.rule == "restart_wait" and p.pressed == []
    d2 = _run(m.step(_states(running="Occupied", battery="-1"), ent, st, now=5010.0 + 180))
    assert d2.rule == "house_needs_power" and p.pressed == ["button.sigen_inverter_dc_charger_start_charging"]


def test_manager_stops_at_the_low_limit_and_watch_only_presses_nothing(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press, notify=p.notify)
    ent = _entities()
    watch = _run(m.step(_states(running="Discharging", soc="39"), ent, _settings(observe_only=True), now=100.0))
    assert watch.rule == "low_limit" and p.pressed == [] and m.log[-1]["observe_only"]
    real = _run(m.step(_states(running="Discharging", soc="39"), ent,
                       _settings(notify_service="notify.mobile_app_phone"), now=500.0))
    assert real.rule == "low_limit" and p.pressed == ["button.sigen_inverter_dc_charger_stop_charging"]
    assert p.notes == ["V2X: charger stopped"]


def test_manager_starts_as_soon_as_its_exporting(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), {**_settings(), "export_hold_s": 10}  # an old saved setting: ignored
    assert _run(m.step(_states(soc="30", export="2"), ent, st, now=1000.0)).rule == "charge_opportunity"
    assert p.pressed == ["button.sigen_inverter_dc_charger_start_charging"]


def test_last_known_soc_is_kept_and_saved(tmp_path):
    m = Manager(str(tmp_path))
    ent, st = _entities(), _settings()
    _run(m.step(_states(soc="62"), ent, st, now=10.0))
    _run(m.step(_states(soc="0"), ent, st, now=20.0))  # the charger reads 0 while it's off
    _run(m.step(_states(soc="unavailable"), ent, st, now=30.0))
    assert m.inputs.soc == 62
    again = Manager(str(tmp_path))
    assert again.soc == 62  # kept after a restart
    # Unplugged and plugged in again: the old SoC is still used, but marked as such
    _run(again.step(_states(running="Idle", soc="0"), ent, st, now=40.0))
    _run(again.step(_states(running="Occupied", soc="0"), ent, st, now=50.0))
    assert again.soc_before_plug_in and again.snapshot(60.0)["soc_before_plug_in"]
    _run(again.step(_states(running="Charging", soc="64"), ent, st, now=70.0))
    assert not again.soc_before_plug_in and again.soc == 64



def test_a_start_just_after_plugging_in_is_marked_as_the_old_soc(tmp_path):
    """The chart puts it at the first new reading, not the last car's SoC."""
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    _run(m.step(_states(running="Idle", soc="95"), ent, st, now=10.0))
    _run(m.step(_states(running="Idle", soc="0"), ent, st, now=20.0))
    _run(m.step(_states(running="Occupied", soc="0"), ent, st, now=30.0))  # plugged in: no reading yet
    entry = _run(m.act("start", "manual", "", ent, st))
    assert entry["soc"] == 95 and entry["soc_from_before_plug_in"]
    _run(m.step(_states(running="Charging", soc="37"), ent, st, now=40.0))
    assert "soc_from_before_plug_in" not in _run(m.act("stop", "manual", "", ent, st))


def test_v2x_mode_can_be_a_select_or_a_switch(tmp_path):
    m = Manager(str(tmp_path))
    st = _settings()
    assert _run(m.step(_states(mode="Fast Charging"), _entities(), st, now=1.0)).rule == "not_v2x"
    sw = {**_states(), "input_boolean.evdc_v2x_mode": {"state": "on", "attributes": {}}}
    ent = _entities(v2x_mode="input_boolean.evdc_v2x_mode")
    assert _run(m.step(sw, ent, st, now=2.0)).rule != "not_v2x"


def test_energy_sensors_like_the_v2x_templates(tmp_path):
    m = Manager(str(tmp_path))
    _run(m.step(_states(soc="60"), _entities(), _settings(), now=1.0))
    assert m.energy() == {"available_kwh": 12.8, "window_kwh": 25.6, "window_pct": 50.0}
    _run(m.step(_states(soc="30"), _entities(), _settings(), now=2.0))
    assert m.energy()["available_kwh"] == 0.01  # below the low limit
    _run(m.step(_states(mode="Fast Charging"), _entities(), _settings(), now=3.0))
    assert m.energy() == {"available_kwh": 0.01, "window_kwh": 0.01, "window_pct": 0.0}


def test_combined_battery_weights_by_size():
    from src.manager import combined_battery
    # Car window 25.6 kWh half full (12.8), home battery 10 kWh at 80% (8): 20.8 of 35.6
    c = combined_battery({"available_kwh": 12.8, "window_kwh": 25.6, "window_pct": 50.0}, 80, 10)
    assert c["pct"] == 58.4 and c["kwh"] == 20.8 and c["max_kwh"] == 35.6
    assert c["home_kwh"] == 8 and c["virtual_kwh"] == 12.8 and c["virtual_max_kwh"] == 25.6
    # The virtual battery not in use: just the home battery
    off = combined_battery({"available_kwh": 0.01, "window_kwh": 0.01, "window_pct": 0.0}, 80, 10)
    assert off["pct"] == 80.0 and off["virtual_max_kwh"] == 0
    # Without the home battery's SoC or size: unknown
    assert combined_battery({"window_kwh": 25.6, "available_kwh": 1}, None, 10)["pct"] is None
    assert combined_battery({"window_kwh": 25.6, "available_kwh": 1}, 50, None)["pct"] is None


def test_combined_battery_sensor(tmp_path):
    from src.ha_entities import values
    from src.shared_state import SharedState
    st = SharedState()
    st.energy = {"available_kwh": 12.8, "window_kwh": 25.6, "window_pct": 50.0}
    st.readings = {"home_soc": 80, "home_kwh": 10}
    pct, attrs = values(st)["combined_soc"]
    assert pct == 58.4 and attrs["max_kwh"] == 35.6 and attrs["home_battery_soc"] == 80 and attrs["virtual_battery_soc"] == 50.0


def test_tuning_settings_are_checked(tmp_path):
    s = AppSettings(str(tmp_path))
    s.update({"restart_wait_s": 240, "notify_service": "mobile_app_phone", "active_states": "Charging, Discharging"})
    again = AppSettings(str(tmp_path)).data
    assert again["restart_wait_s"] == 240 and again["notify_service"] == "notify.mobile_app_phone"
    assert again["active_states"] == ["Charging", "Discharging"]
    for bad in ({"restart_wait_s": -1}, {"observe_only": "yes"}, {"active_states": []},
                {"notify_service": "notify.bad name"}, {"nope": 1}):
        with pytest.raises(ValueError):
            s.update(bad)
    json.dumps(again)


def test_missing_helpers_are_found_or_made(tmp_path):
    from src.ha_link import HaLink
    from src.helpers import ensure_helpers
    from src.shared_state import SharedState

    class FakeLink(HaLink):
        def __init__(self, existing, folder, ui=(), ha_state="RUNNING"):
            folder.mkdir()
            super().__init__(str(folder), SharedState(), token="")
            self.existing, self.made, self.ui, self.ha_state = existing, [], list(ui), ha_state

        async def all_states(self, fresh=False):
            return [{"entity_id": e, "state": v} for e, v in self.existing.items()]

        async def query(self, payload, timeout=30):
            if payload["type"].endswith("/list"):
                return [{"id": i} for i in self.ui]
            if payload["type"] == "get_config":
                return {"state": self.ha_state}
            self.made.append(payload)
            made_id = payload["name"].lower().replace(" ", "_")
            self.existing[payload["type"].split("/")[0] + "." + made_id] = payload.get("initial")
            self.ui.append(made_id)
            return {"id": made_id}

    link = FakeLink({"input_select.sigenergy_evdc_charging_mode": "V2X", "input_number.v2x_cut_off_threshold": "35",
                     "input_number.evdc_soc_range_vehicle_battery_capacity": "64"}, tmp_path / "a",
                    ui=["sigenergy_evdc_charging_mode"])
    done = _run(ensure_helpers(link))
    assert link.settings["v2x_mode"] == "input_select.sigenergy_evdc_charging_mode"  # already there
    assert link.settings["capacity"] == "input_number.evdc_soc_range_vehicle_battery_capacity"  # already there
    assert link.settings["soc_high"] == "input_number.evdc_soc_range_high_limit"
    made = {p["name"]: p for p in link.made}
    assert set(made) == {"EVDC SoC Range High Limit", "EVDC SoC Range Low Limit"}
    assert made["EVDC SoC Range Low Limit"]["initial"] == 35.0  # from the automation's own helper
    assert made["EVDC SoC Range High Limit"]["initial"] == 80 and made["EVDC SoC Range High Limit"]["type"] == "input_number/create"
    assert len(done) == 4 and _run(ensure_helpers(link)) == []  # nothing left to do

    fresh = FakeLink({}, tmp_path / "b")
    _run(ensure_helpers(fresh))
    assert fresh.settings["v2x_mode"] == "input_select.sigenergy_evdc_charging_mode"
    mode = [p for p in fresh.made if p["type"] == "input_select/create"][0]
    assert mode["options"] == ["V2X", "Solar Surplus", "Fast Charging"] and mode["initial"] == "V2X"


def test_battery_rates_like_the_v2x_rate_templates():
    from src.rates import battery_rates
    common = dict(plugged_in=True, v2x=True, fast=False, available_kw=10.0, home_soc=50.0, home_kwh=9.0,
                  car_window_kwh=25.6)
    assert battery_rates(**common) == {"rate_kw": 8.0, "car_kw": 7.4, "house_kw": 2.6}  # scaled to 10 kW
    roomy = battery_rates(**{**common, "available_kw": 20.0})
    assert roomy == {"rate_kw": 8.0, "car_kw": 8.0, "house_kw": 2.81}  # in proportion to their sizes
    assert battery_rates(**{**common, "home_soc": 96.0})["rate_kw"] == 10.0  # nearly full: 12.5, capped at 10
    assert battery_rates(**{**common, "home_kwh": None})["house_kw"] == 2.6  # 9 kWh when it can't be read
    fast = battery_rates(**{**common, "v2x": False, "fast": True, "available_kw": 15.0})
    assert fast == {"rate_kw": 12.5, "car_kw": 0.0, "house_kw": 4.5}
    off = battery_rates(**{**common, "plugged_in": False})
    assert off == {"rate_kw": 0.0, "car_kw": 0.0, "house_kw": 4.5}
    assert battery_rates(**{**common, "car_window_kwh": 0})["house_kw"] == 4.5
    custom = battery_rates(**{**common, "available_kw": 30.0}, settings={"rate_kw": 6, "house_rate_kw": 3})
    assert custom["rate_kw"] == 6 and custom["house_kw"] == 2.81


def test_manager_reports_the_rates_and_counts_solar_surplus(tmp_path):
    m = Manager(str(tmp_path))
    states = {**_states(mode="Solar Surplus"),
              "sensor.sigen_plant_available_max_active_power": {"state": "10", "attributes": {"unit_of_measurement": "kW"}},
              "sensor.sigen_plant_battery_state_of_charge": {"state": "50", "attributes": {}},
              "sensor.sigen_inverter_rated_battery_capacity": {"state": "9", "attributes": {}}}
    d = _run(m.step(states, _entities(), _settings(), now=1.0))
    assert d.rule == "not_v2x"  # Solar Surplus isn't V2X: the charger is left alone...
    assert m.energy()["window_kwh"] == 25.6  # ...but the energy and rate sensors count it
    assert m.snapshot(2.0)["rates"] == {"rate_kw": 8.0, "car_kw": 7.4, "house_kw": 2.6}


def test_a_discharge_that_stops_by_itself_is_a_dropout(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    _run(m.step(_states(running="Discharging", battery="-1"), ent, st, now=1000.0))
    _run(m.step(_states(running="Occupied", battery="-1"), ent, st, now=1010.0))
    assert m.dropouts == [1010.0] and m.log[-1]["action"] == "dropout"
    assert m.snapshot(1011.0)["last_action"] is None  # a dropout isn't a start or stop
    # Stopped by us at the low limit: not a dropout
    m.dropouts.clear()
    _run(m.step(_states(running="Discharging", soc="45"), ent, st, now=2000.0))
    _run(m.step(_states(running="Discharging", soc="39"), ent, st, now=2100.0))
    assert m.log[-1]["action"] == "stop" and m.log[-1]["pressed"]
    _run(m.step(_states(running="Occupied", soc="39"), ent, st, now=2110.0))
    assert m.dropouts == []
    # Unplugged while discharging: not a dropout either
    _run(m.step(_states(running="Discharging", soc="60"), ent, st, now=3000.0))
    _run(m.step(_states(running="Idle", soc="0"), ent, st, now=3010.0))
    assert m.dropouts == []


def test_the_schedule_changes_the_limits_used(tmp_path):
    import datetime
    from src.schedule import Schedule

    m = Manager(str(tmp_path))
    m.schedule = Schedule(str(tmp_path))
    now = datetime.datetime(2026, 10, 6, 23, 30)  # Tuesday
    m.schedule.add({"kind": "weekly", "days": ["tue"], "start": "23:00", "end": "08:00", "high": 50})
    _run(m.step(_states(soc="60"), _entities(), _settings(), now=now.timestamp()))
    assert m.inputs.high == 50 and m.readings["base_high"] == 80
    assert m.readings["scheduled"][0]["high"] == 50 and m.decision.rule == "held_high"


def test_the_history_notes_the_limits_and_schedule_changes(tmp_path):
    import datetime
    from src.schedule import Schedule

    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    m.schedule = Schedule(str(tmp_path))
    m.schedule.add({"kind": "weekly", "days": ["tue"], "start": "23:00", "end": "08:00", "high": 50, "label": "Work"})
    tue = datetime.datetime(2026, 10, 6, 22, 0)
    ent, st = _entities(), _settings(observe_only=True)
    _run(m.step(_states(soc="60", lc=0.0), ent, st, now=tue.timestamp()))  # starts in range (watching)
    assert m.log[-1]["action"] == "start" and m.log[-1]["observe_only"] and m.log[-1]["high"] == 80
    _run(m.step(_states(soc="60", lc=0.0), ent, st, now=(tue + datetime.timedelta(hours=1, minutes=1)).timestamp()))
    limits = [x for x in m.log if x["action"] == "limits"]
    assert len(limits) == 1 and "Work" in limits[0]["reason"] and limits[0]["high"] == 50
    assert all(x["action"] != "limits" for x in m.snapshot()["log"])  # not on the Overview
    _run(m.step(_states(soc="60", lc=0.0), ent, st, now=(tue + datetime.timedelta(hours=10, minutes=1)).timestamp()))
    limits = [x for x in m.log if x["action"] == "limits"]
    assert len(limits) == 2 and "default" in limits[-1]["reason"] and limits[-1]["high"] == 80


def test_starts_and_stops_by_something_else_are_noted(tmp_path):
    m = Manager(str(tmp_path))
    ent, st = _entities(), _settings(observe_only=True)
    _run(m.step(_states(running="Discharging", battery="-1"), ent, st, now=1000.0))
    _run(m.step(_states(running="Occupied", battery="-1"), ent, st, now=1010.0))
    assert m.log[-1]["action"] == "dropout"
    # Another automation restarts it before our restart wait is up: noted, and no "would start" needed
    _run(m.step(_states(running="Discharging", battery="-1"), ent, st, now=1150.0))
    assert m.log[-1]["action"] == "elsewhere_start"
    # Watching only: we'd have stopped it at the low limit, and something else did: not a dropout
    _run(m.step(_states(running="Discharging", soc="39"), ent, st, now=1400.0))
    assert m.log[-1]["action"] == "stop" and m.log[-1]["observe_only"]
    _run(m.step(_states(running="Occupied", soc="39"), ent, st, now=1420.0))
    assert m.log[-1]["action"] == "stop" and len([x for x in m.log if x["action"] == "dropout"]) == 1


def test_the_restart_wait_counts_from_when_home_assistant_saw_the_stop(tmp_path):
    m = Manager(str(tmp_path))
    ent, st = _entities(), _settings()
    _run(m.step(_states(running="Discharging", battery="-1"), ent, st, now=1000.0))
    d = _run(m.step(_states(running="Occupied", battery="-1", lc=1050.0), ent, st, now=1060.0))  # seen 10 s late
    assert d.rule == "restart_wait" and "10 s ago" in d.reason


def test_a_charger_alarm_is_noted_and_left_alone(tmp_path):
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    _run(m.step(_states(running="Discharging", battery="-1"), ent, st, now=1000.0))
    d = _run(m.step(_states(running="Alarm", battery="-1"), ent, st, now=1010.0))
    assert d.rule == "alarm" and d.action is None and m.inputs.alarm
    assert m.log[-1]["action"] == "alarm" and m.alarms and not [x for x in m.log if x["action"] == "dropout"]
    _run(m.step(_states(running="Alarm", battery="-1"), ent, st, now=1500.0))
    assert p.pressed == []  # nothing pressed while it's in alarm
    _run(m.step(_states(running="Occupied", battery="-1"), ent, st, now=1610.0))
    cleared = [x for x in m.log if x["action"] == "alarm_cleared"]
    assert len(cleared) == 1 and "10 min" in cleared[0]["reason"]
    assert p.pressed == ["button.sigen_inverter_dc_charger_start_charging"]  # back to normal: started again


def test_the_chart_marks_alarms():
    from src.manager import soc_state
    assert soc_state(False, False, "Alarm", False, True) == "alarm"
    assert soc_state(True, True, "Discharging", True) == "discharge"


def test_a_stop_at_the_low_limit_isnt_held_up_by_a_start_just_before(tmp_path):
    # As the automation: the gap between presses is only for pressing the same button again
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    _run(m.step(_states(soc="41", battery="-1", lc=1000.0), ent, st, now=1200.0))  # house needs power: start
    assert p.pressed == ["button.sigen_inverter_dc_charger_start_charging"]
    d = _run(m.step(_states(running="Discharging", soc="40", battery="-1"), ent, st, now=1230.0))
    assert d.rule == "low_limit" and p.pressed[-1] == "button.sigen_inverter_dc_charger_stop_charging"


# --- logging: what each decision was based on (0.21.0) ---------------------------------


def test_a_start_for_the_house_says_where_the_car_is_against_both_limits():
    above = d(soc=99, battery_kw=-1.2)
    assert above.rule == "house_needs_power"
    assert "99%, at or above the high limit (80%)" in above.reason
    assert "the home battery is discharging 1.2 kW" in above.reason
    between = d(soc=60, grid_kw=0.8)
    assert "between the limits (40%–80%)" in between.reason and "importing 0.8 kW from the grid" in between.reason


def test_the_soc_direction_is_worked_out_from_readings_over_half_an_hour(tmp_path):
    m = Manager(str(tmp_path))
    assert m.soc_trend_text(1000.0) == "direction not known yet"
    ent, st = _entities(), _settings()
    _run(m.step(_states(running="Charging", soc="90"), ent, st, now=1000.0))
    _run(m.step(_states(running="Charging", soc="91"), ent, st, now=1000.0 + 900))
    _run(m.step(_states(running="Charging", soc="92"), ent, st, now=1000.0 + 1800))
    assert m.soc_trend(1000.0 + 1800) == pytest.approx(4.0)
    assert m.soc_trend_text(1000.0 + 1800) == "rising +4.0%/h"
    _run(m.step(_states(running="Discharging", soc="88"), ent, st, now=1000.0 + 3600))
    assert m.soc_trend_text(1000.0 + 3600).startswith("falling")


def test_presses_and_new_decisions_are_logged_with_what_was_seen(tmp_path, caplog):
    caplog.set_level("INFO", logger="src.manager")
    p = Presses()
    m = Manager(str(tmp_path), press=p.press)
    ent, st = _entities(), _settings()
    _run(m.step(_states(lc=1000.0), ent, st, now=1100.0))  # in the restart wait
    _run(m.step(_states(lc=1000.0), ent, st, now=1150.0))  # same decision: not logged again
    decisions = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Decision:")]
    assert len(decisions) == 1 and "[car 60%" in decisions[0] and "charger occupied" in decisions[0]
    _run(m.step(_states(lc=1000.0), ent, st, now=1200.0))
    started = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Started the charger")]
    assert len(started) == 1 and "between the limits (40%–80%)" in started[0] and "grid" in started[0]
    assert m.log[-1]["context"].startswith("car 60%")


def test_the_page_is_told_which_entity_the_battery_capacity_comes_from(tmp_path):
    m = Manager(str(tmp_path))
    _run(m.step(_states(), _entities(), _settings(), now=1000.0))
    assert m.readings["capacity_kwh"] == 64 and m.readings["capacity_entity"] == "input_number.cap"


def test_the_limits_are_the_add_ons_own_helpers_too(tmp_path):
    from src.ha_link import HaLink
    from src.helpers import ensure_helpers
    from src.shared_state import SharedState

    class FakeLink(HaLink):
        def __init__(self, existing, folder):
            folder.mkdir()
            super().__init__(str(folder), SharedState(), token="")
            self.existing, self.made = existing, []

        async def all_states(self, fresh=False):
            return [{"entity_id": e, "state": v} for e, v in self.existing.items()]

        async def query(self, payload, timeout=30):
            if payload["type"].endswith("/list"):
                return [{"id": "sigenergy_evdc_charging_mode"}]
            if payload["type"] == "get_config":
                return {"state": "RUNNING"}
            self.made.append(payload)
            return {"id": payload["name"].lower().replace(" ", "_")}

    # The limit helpers were picked but have gone: made again, at the V2X automation's values
    link = FakeLink({"input_select.sigenergy_evdc_charging_mode": "V2X", "input_number.v2x_cut_off_threshold": "40",
                     "input_number.v2x_cut_off_threshold_high": "99"}, tmp_path / "a")
    link.settings.update({"soc_high": "input_number.evdc_soc_range_high_limit", "soc_low": "input_number.evdc_soc_range_low_limit",
                          "v2x_mode": "input_select.sigenergy_evdc_charging_mode", "capacity": "input_number.c",
                          "import_rate": "sensor.r"})
    notes = []
    _run(ensure_helpers(link, notes))
    made = {p["name"]: p for p in link.made}
    assert set(made) == {"EVDC SoC Range High Limit", "EVDC SoC Range Low Limit"}  # not the charging mode (from YAML)
    assert made["EVDC SoC Range High Limit"]["initial"] == 99.0 and made["EVDC SoC Range Low Limit"]["initial"] == 40.0
    assert notes == []
    # A limit helper with the add-on's name in YAML: used, with a note to remove it there
    yaml = FakeLink({"input_number.evdc_soc_range_high_limit": "90", "input_number.evdc_soc_range_low_limit": "40"},
                    tmp_path / "b")
    yaml.settings.update({"soc_high": "input_number.evdc_soc_range_high_limit", "soc_low": "input_number.my_low",
                          "v2x_mode": "input_select.sigenergy_evdc_charging_mode", "capacity": "input_number.c",
                          "import_rate": "sensor.r"})
    notes = []
    _run(ensure_helpers(yaml, notes))
    assert yaml.made == [] and len(notes) == 1 and "evdc_soc_range_high_limit is defined in YAML" in notes[0]
    # Something else picked for both limits: left alone
    mine = FakeLink({}, tmp_path / "c")
    mine.settings.update({"soc_high": "input_number.my_high", "soc_low": "input_number.my_low",
                          "v2x_mode": "input_select.sigenergy_evdc_charging_mode", "capacity": "input_number.c",
                          "import_rate": "sensor.r"})
    import logging
    seen = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    grab = Grab()
    logging.getLogger("src.helpers").addHandler(grab)
    logging.getLogger("src.helpers").setLevel(logging.INFO)
    try:
        _run(ensure_helpers(mine))
    finally:
        logging.getLogger("src.helpers").removeHandler(grab)
    assert mine.made == []
    assert any("high limit uses input_number.my_high" in m and "evdc_soc_range_high_limit isn't made" in m for m in seen)


def test_helpers_with_the_old_names_are_moved_to_the_new_ones(tmp_path):
    from src.ha_link import HaLink
    from src.helpers import ensure_helpers
    from src.shared_state import SharedState

    class FakeLink(HaLink):
        def __init__(self, existing, folder, ui):
            folder.mkdir()
            super().__init__(str(folder), SharedState(), token="")
            self.existing, self.queries, self.ui = existing, [], list(ui)

        async def all_states(self, fresh=False):
            return [{"entity_id": e, "state": v} for e, v in self.existing.items()]

        async def query(self, payload, timeout=30):
            self.queries.append(payload)
            if payload["type"].endswith("/list"):
                return [{"id": i} for i in self.ui]
            if payload["type"] == "get_config":
                return {"state": "RUNNING"}
            if payload["type"].endswith("/create"):
                made = payload["name"].lower().replace(" ", "_")
                self.ui.append(made)
                return {"id": made}
            return None

    link = FakeLink({"input_number.evdc_soc_high_limit": "95", "input_number.evdc_soc_low_limit": "35",
                     "input_number.evdc_vehicle_battery_capacity": "42.5",
                     "input_select.sigenergy_evdc_charging_mode": "V2X"}, tmp_path / "a",
                    ui=["evdc_soc_high_limit", "evdc_soc_low_limit", "evdc_vehicle_battery_capacity"])
    link.settings.update({"soc_high": "input_number.evdc_soc_high_limit", "soc_low": "input_number.evdc_soc_low_limit",
                          "capacity": "input_number.evdc_vehicle_battery_capacity",
                          "v2x_mode": "input_select.sigenergy_evdc_charging_mode", "import_rate": "sensor.r"})
    done = _run(ensure_helpers(link))
    assert link.settings["soc_high"] == "input_number.evdc_soc_range_high_limit"
    assert link.settings["soc_low"] == "input_number.evdc_soc_range_low_limit"
    assert link.settings["capacity"] == "input_number.evdc_soc_range_vehicle_battery_capacity"
    made = {q["name"]: q["initial"] for q in link.queries if q["type"] == "input_number/create"}
    assert made == {"EVDC SoC Range High Limit": 95.0, "EVDC SoC Range Low Limit": 35.0,
                    "EVDC SoC Range Vehicle Battery Capacity": 42.5}  # at the old ones' values
    deleted = {q["input_number_id"] for q in link.queries if q["type"] == "input_number/delete"}
    assert deleted == {"evdc_soc_high_limit", "evdc_soc_low_limit", "evdc_vehicle_battery_capacity"}
    assert link.settings["v2x_mode"] == "input_select.sigenergy_evdc_charging_mode"  # not renamed
    assert any("in place of input_number.evdc_soc_high_limit" in line for line in done)

    # An old one from YAML is left in Home Assistant (only the add-on's own are removed)
    yaml = FakeLink({"input_number.evdc_soc_high_limit": "90"}, tmp_path / "b", ui=[])
    yaml.settings.update({"soc_high": "input_number.evdc_soc_high_limit", "soc_low": "input_number.mine",
                          "capacity": "input_number.c", "v2x_mode": "input_select.m", "import_rate": "sensor.r"})
    _run(ensure_helpers(yaml))
    assert yaml.settings["soc_high"] == "input_number.evdc_soc_range_high_limit"
    assert not [q for q in yaml.queries if q["type"].endswith("/delete")]


def test_power_flow_takes_devices_off_the_house_and_the_cards_own_sensors(tmp_path):
    m = Manager(str(tmp_path), press=Presses().press)
    st = _states(running="Available", grid="9", battery="0")
    st.update({"sensor.hv": {"state": "7000", "attributes": {"unit_of_measurement": "W"}},
               "sensor.house": {"state": "10", "attributes": {"unit_of_measurement": "kW"}},
               "sensor.meter": {"state": "8.5", "attributes": {"unit_of_measurement": "kW"}}})
    ent = _entities(home_power="sensor.house", pv_power="",
                    flow_devices=[{"name": "Hypervolt", "icon": "car", "power": "sensor.hv", "plugged": ""}])
    m.observe(st, ent, _settings(), 1000.0)
    f = m.power_flow()
    assert f["house_kw"] == 10 and f["home_kw"] == 3.0 and f["grid_kw"] == 9
    assert f["devices"] == [{"n": 0, "name": "Hypervolt", "icon": "car", "kw": 7.0, "entity": "sensor.hv"}]
    # Each reading's entity, to open it (the card's own sensor when picked)
    assert f["entities"]["grid"] == ent["grid_power"] and f["entities"]["home"] == "sensor.house"
    assert "pv" not in f["entities"]  # (none picked)
    assert f["car_plugged"] is False  # (Available: unplugged, so the diagram leaves the car off)
    m.observe(_states(running="Occupied", grid="9", battery="0"), ent, _settings(), 1005.0)
    assert m.power_flow()["car_plugged"] is True
    m.observe(st, {**ent, "flow_grid_power": "sensor.meter"}, _settings(), 1010.0)
    assert m.power_flow()["grid_kw"] == 8.5  # the card's own grid sensor; the controller still uses its own
    assert m.power_flow()["entities"]["grid"] == "sensor.meter"
    assert m.inputs.grid_kw == 9
    m.observe(st, {**ent, "flow_devices": []}, _settings(), 1020.0)
    assert m.power_flow()["home_kw"] is None and m.power_flow()["devices"] == []
    # The card's own house sensor
    st["sensor.house2"] = {"state": "12000", "attributes": {"unit_of_measurement": "W"}}
    m.observe(st, {**ent, "flow_home_power": "sensor.house2"}, _settings(), 1030.0)
    assert m.power_flow()["house_kw"] == 12 and m.power_flow()["home_kw"] == 5.0


def test_the_house_can_show_its_whole_use_with_devices(tmp_path):
    from src.ha_link import default_settings, validate_settings
    assert default_settings()["flow_subtract"] is True
    assert validate_settings({"flow_subtract": False}, default_settings())["flow_subtract"] is False
    m = Manager(str(tmp_path), press=Presses().press)
    st = _states(running="Available", grid="9")
    st.update({"sensor.hv": {"state": "7", "attributes": {"unit_of_measurement": "kW"}},
               "sensor.house": {"state": "10", "attributes": {"unit_of_measurement": "kW"}}})
    ent = _entities(home_power="sensor.house", flow_subtract=False,
                    flow_devices=[{"name": "Hypervolt", "icon": "car", "power": "sensor.hv", "plugged": ""}])
    m.observe(st, ent, _settings(), 1000.0)
    f = m.power_flow()
    assert f["house_kw"] == 10 and f["home_kw"] is None and f["devices"][0]["kw"] == 7
