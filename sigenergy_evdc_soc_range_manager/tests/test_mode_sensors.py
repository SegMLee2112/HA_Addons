"""0.39.0: two binary sensors for each of the charging mode's options (src/ha_entities.py mode_sensors)."""
from src.ha_entities import SensorPublisher, mode_sensor_id, mode_sensors
from src.shared_state import SharedState

OPTIONS = ["V2X", "Solar Surplus", "Fast Charging"]


def _state(plugged=True, mode="Fast Charging", running="Charging", active=True, discharging=False, options=OPTIONS):
    s = SharedState()
    s.inputs = {"plugged_in": plugged, "running_state": running, "active": active, "discharging": discharging}
    s.readings = {"mode": mode, "mode_options": list(options), "mode_entity": "input_select.sigenergy_evdc_charging_mode"}
    return s


def _on(state):
    return {e for e, v, _ in mode_sensors(state) if v == "on"}


def test_sensor_ids_from_the_options():
    assert mode_sensor_id("Fast Charging") == "binary_sensor.evdc_soc_range_fast_charging_plugged_in"
    assert mode_sensor_id("Solar Surplus", "charging") == "binary_sensor.evdc_soc_range_solar_surplus_charging"
    assert mode_sensor_id("V2X") == "binary_sensor.evdc_soc_range_v2x_plugged_in"
    assert mode_sensor_id(" -- ") is None


def test_on_only_for_the_selected_mode_while_plugged_in_and_charging():
    out = {e: (v, a) for e, v, a in mode_sensors(_state())}
    assert len(out) == 6
    assert out["binary_sensor.evdc_soc_range_fast_charging_plugged_in"][1]["friendly_name"] == "EVDC SoC Range Fast Charging Plugged In"
    assert out["binary_sensor.evdc_soc_range_fast_charging_charging"][1]["friendly_name"] == "EVDC SoC Range Fast Charging Charging"
    assert _on(_state()) == {"binary_sensor.evdc_soc_range_fast_charging_plugged_in",
                             "binary_sensor.evdc_soc_range_fast_charging_charging"}
    # Plugged in, not charging (stopped, or discharging in V2X): only the plugged in one
    assert _on(_state(mode="V2X", running="Occupied", active=False)) == {"binary_sensor.evdc_soc_range_v2x_plugged_in"}
    assert _on(_state(mode="V2X", running="Discharging", discharging=True)) == {"binary_sensor.evdc_soc_range_v2x_plugged_in"}
    # Not plugged in: all off
    assert _on(_state(plugged=False, running="Idle", active=False)) == set()
    # A mode entity without options (e.g. a switch): none
    assert mode_sensors(_state(options=[])) == []


def test_they_follow_the_options():
    pub = SensorPublisher()
    first = {e: v for e, v, _ in pub.due(_state(), 1000.0)}
    assert first["binary_sensor.evdc_soc_range_v2x_plugged_in"] == "off"
    assert len(pub.unavailable()) == len(first)  # (all of them, the mode ones too)
    # A mode added: its two sensors; one taken away: unavailable, then not posted again
    opts = ["V2X", "Fast Charging", "Eco"]
    second = {e: v for e, v, _ in pub.due(_state(options=opts), 1001.0)}
    assert second["binary_sensor.evdc_soc_range_eco_plugged_in"] == "off"
    assert second["binary_sensor.evdc_soc_range_solar_surplus_plugged_in"] == "unavailable"
    assert second["binary_sensor.evdc_soc_range_solar_surplus_charging"] == "unavailable"
    third = {e for e, _, _ in pub.due(_state(options=opts), 1002.0)}
    assert not any("solar_surplus" in e for e in third)
    assert "binary_sensor.evdc_soc_range_eco_charging" in pub.mode_ids
