"""The power flow diagram's sums (src/flow.py)."""

from src.flow import flows, house_load


def test_solar_feeds_the_house_then_the_battery_then_the_grid():
    f = flows(pv=6.0, grid=-1.0, batt=2.0, car=0.0, house=3.0)
    assert f == {"solar_house": 3.0, "solar_battery": 2.0, "solar_grid": 1.0}


def test_the_car_discharging_covers_the_house_and_exports_the_rest():
    f = flows(pv=0.0, grid=-1.5, batt=0.0, car=-3.0, house=1.5)
    assert f == {"house_grid": 1.5}  # the house side had 1.5 kW spare


def test_night_import_charges_the_car_and_the_battery():
    f = flows(pv=0.0, grid=10.0, batt=3.0, car=6.5, house=0.5)
    assert f == {"grid_house": 7.0, "grid_battery": 3.0}


def test_house_load_from_the_balance_when_not_measured():
    assert house_load(5.0, -1.0, 2.0, 0.0) == 2.0
    assert house_load(0.0, 0.2, -1.0, -3.0) == 4.2  # battery and car discharging into the house
    assert house_load(1.0, 0.0, 0.0, 0.0, measured=0.4) == 0.4
    assert house_load(None, 0.0, 0.0, 0.0) is None


def test_devices_off_the_house_need_a_power_sensor_and_to_be_plugged_in():
    from src.manager import flow_devices
    states = {"sensor.hv": {"state": "7100", "attributes": {"unit_of_measurement": "W"}},
              "binary_sensor.hv_plugged": {"state": "on"}, "sensor.hp": {"state": "1.4"},
              "binary_sensor.off": {"state": "off"}, "sensor.tub": {"state": "0.5"}}
    devices = [{"name": "Hypervolt", "icon": "car", "power": "sensor.hv", "plugged": "binary_sensor.hv_plugged"},
               {"name": "Heat pump", "icon": "heatpump", "power": "sensor.hp", "plugged": ""},
               {"name": "Spare", "icon": "plug", "power": "", "plugged": ""},
               {"name": "Hot tub", "icon": "plug", "power": "sensor.tub", "plugged": "binary_sensor.off"}]
    assert flow_devices(devices, states) == [{"n": 0, "name": "Hypervolt", "icon": "car", "kw": 7.1, "entity": "sensor.hv"},
                                             {"n": 1, "name": "Heat pump", "icon": "heatpump", "kw": 1.4, "entity": "sensor.hp"}]


def test_flow_devices_are_checked():
    import pytest
    from src.ha_link import default_settings, validate_settings
    s = default_settings()
    assert [d["name"] for d in s["flow_devices"]] == ["Car charger", "Heat pump"]
    out = validate_settings({"flow_devices": [{"name": " Hypervolt ", "icon": "car", "power": "sensor.hv"}],
                             "flow_grid_power": "sensor.meter"}, s)
    assert out["flow_devices"] == [{"name": "Hypervolt", "icon": "car", "power": "sensor.hv", "plugged": ""}]
    assert out["flow_grid_power"] == "sensor.meter"
    with pytest.raises(ValueError):
        validate_settings({"flow_devices": [{"icon": "rocket"}]}, s)
    with pytest.raises(ValueError):
        validate_settings({"flow_devices": [{"power": "switch.x"}]}, s)
    with pytest.raises(ValueError):
        validate_settings({"flow_devices": [{}] * 7}, s)
