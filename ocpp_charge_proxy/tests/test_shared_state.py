from src.shared_state import SharedState


def test_default_state():
    state = SharedState()
    assert state.connected_to_server is False
    assert state.state == "Available"
    assert state.plugged_in is False
    assert state.power_kw == 0.0
    assert state.energy_kwh == 0.0
    assert state.transaction_id is None
    assert state.current_amps_setting == 32
    assert state.power_source == "simulated"


def test_to_dict():
    state = SharedState()
    d = state.to_dict()
    assert isinstance(d, dict)
    assert "state" in d
    assert "connected_to_server" in d
    assert "server_config" in d
    assert d["connected_to_server"] is False


def test_to_dict_includes_all_fields():
    state = SharedState()
    d = state.to_dict()
    expected_keys = {
        "state", "plugged_in", "power_kw", "voltage", "current_a",
        "frequency_hz", "power_offered_kw", "energy_kwh",
        "current_amps_setting", "transaction_id", "connected_to_server",
        "meter_interval", "power_source", "power_entity_value",
        "server_config", "last_command_received", "last_command_sent",
        "soc_percent", "held_messages", "tag_refused",
        "current_amps_effective", "current_amps_provider_limit",
        "last_heartbeat",
        "schedule_enabled", "schedule_next", "replug", "scheduled", "slot_now", "unplug_pending", "plug_pending", "plan_check",
        "start_delay_s", "ramp_up_s", "next_slots",
    }
    assert set(d.keys()) == expected_keys
