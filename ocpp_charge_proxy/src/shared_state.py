from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class SharedState:
    """State shared between the OCPP client and the REST API."""

    state: str = "Available"
    plugged_in: bool = False
    power_kw: float = 0.0
    voltage: float = 230.0
    current_a: float = 0.0
    frequency_hz: float = 50.0
    power_offered_kw: float = 0.0
    energy_kwh: float = 0.0
    current_amps_setting: int = 32  # HA max current (the select)
    current_amps_effective: int = 32  # what the charger actually uses
    current_amps_provider_limit: Optional[float] = None  # chargingALimitConn1
    transaction_id: Optional[int] = None
    connected_to_server: bool = False
    meter_interval: int = 60
    power_source: str = "simulated"
    power_entity_value: Optional[float] = None
    server_config: dict[str, str] = field(default_factory=dict)
    # Last OCPP command from the server, and last message we sent (Heartbeat
    # and MeterValues excluded): {action, timestamp, payload, status, response}
    last_command_received: Optional[dict] = None
    last_command_sent: Optional[dict] = None
    # Last answered Heartbeat: {timestamp, round_trip_ms, interval_s,
    # server_time, clock_offset_s}
    last_heartbeat: Optional[dict] = None
    # Car's state of charge (%) from the integration's SoC entity, None if unset
    soc_percent: Optional[float] = None
    # Transaction messages waiting to be sent (offline queue)
    held_messages: int = 0
    # The supplier refused the session's ID tag: its status ("Invalid", "Blocked"...),
    # with when and what was done, for the page; None when it was accepted
    tag_refused: Optional[dict] = None
    # Simulated car start-up (Settings tab)
    start_delay_s: float = 3.0
    ramp_up_s: float = 5.0
    # Plug-in schedule and auto re-plug (src/automation.py)
    schedule_enabled: bool = False
    schedule_next: Optional[dict] = None  # {time, action, entry_id}
    replug: Optional[dict] = None  # {enabled, after_min, attempts, status, ...}
    # Your supplier has a charge slot running or planned (None: no supplier
    # integration to tell, src/smart_charging.py)
    scheduled: Optional[bool] = None  # your supplier has a slot planned for later
    slot_now: Optional[bool] = None  # ... or one running now
    # A scheduled unplug waiting (until this time) for the supplier to stop the session
    unplug_pending: Optional[str] = None
    plug_pending: Optional[str] = None  # a scheduled plug-in waiting out its random delay, until
    # Force schedule on supplier: the supplier's plan checked against the schedule
    plan_check: Optional[dict] = None
    # The schedule's next slots, for Predbat's rate overrides: [{date, start, end}]
    next_slots: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def display_status(shared) -> str:
    """The add-on's status: the OCPP state ("Charging", not
    "ChargePointStatus.charging"); while plugged in with no session
    (Preparing), "Waiting for supplier" if your supplier's slot is running now
    (it should be charging), or "Scheduled" if one is planned for later."""
    state = str(getattr(shared.state, "value", shared.state))
    if state == "Preparing":
        if getattr(shared, "slot_now", None):
            return WAITING_FOR_SUPPLIER
        if shared.scheduled:
            return "Scheduled"
    return state


WAITING_FOR_SUPPLIER = "Waiting for supplier"
