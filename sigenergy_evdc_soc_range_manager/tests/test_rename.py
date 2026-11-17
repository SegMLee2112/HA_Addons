"""0.40.0: the sensors renamed from sensor.evdc_soc_range_... to sensor.sigen_dc_charger_..."""
from src.ha_entities import SENSORS, SensorPublisher
from src.shared_state import SharedState


def _state():
    s = SharedState()
    s.inputs = {"plugged_in": True, "running_state": "Charging", "active": True, "discharging": False}
    s.readings = {"mode": "V2X", "mode_options": ["V2X", "Fast Charging"], "mode_entity": "input_select.m"}
    return s


def test_new_names():
    assert all(e.split(".", 1)[1].startswith("sigen_dc_charger_") for e, _ in SENSORS.values())
    assert SENSORS["status"][1]["friendly_name"] == "SigEnergy DC Charger Status"


def test_the_old_names_are_posted_unavailable():
    """0.40.0: renamed from sensor.evdc_soc_range_...: the old ones posted as unavailable, saying what they're now."""
    old = {e: a for e, v, a in SensorPublisher().legacy(_state()) if v == "unavailable"}
    assert old["sensor.evdc_soc_range_virtual_battery_soc"]["renamed_to"] == "sensor.sigen_dc_charger_virtual_battery_soc"
    assert old["sensor.evdc_soc_range_virtual_battery_soc"]["friendly_name"] == "EVDC SoC Range Virtual Battery SoC (renamed)"
    assert old["binary_sensor.evdc_soc_range_plugged_in"]["renamed_to"] == "binary_sensor.sigen_dc_charger_plugged_in"
    assert old["binary_sensor.evdc_soc_range_fast_charging_charging"]["renamed_to"] == "binary_sensor.sigen_dc_charger_fast_charging_charging"
    assert not any(e.startswith(("sensor.sigen_dc", "binary_sensor.sigen_dc")) for e in old)
