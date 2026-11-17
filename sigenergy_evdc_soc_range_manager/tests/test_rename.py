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



def test_the_old_names_are_no_longer_posted():
    """0.41.1: the old names (sensor.evdc_soc_range_...) aren't posted as unavailable any more."""
    assert not hasattr(SensorPublisher(), "legacy")
    out = SensorPublisher().due(_state(), 1000.0) + SensorPublisher().unavailable()
    assert not any("evdc_soc_range" in e for e, _, _ in out)
