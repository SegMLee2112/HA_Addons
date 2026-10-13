"""What the add-on is doing right now, shown on the web page (Overview),
pushed to it as it changes (/api/events) and posted as sensors
(src/ha_entities.py). src/__main__.py fills it from the manager after each
look at the charger."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class SharedState:
    status: str = "Starting"  # a few words, e.g. "Discharging" or "Held at the high limit"
    reason: str = ""  # one sentence: why
    rule: Optional[str] = None  # the controller's key for it (src/controller.py)
    inputs: dict = field(default_factory=dict)  # what it decided from (src/controller.py Inputs)
    timers: dict = field(default_factory=dict)  # seconds the charger has been off, export held
    soc_at: Optional[str] = None  # when the SoC was last read (ISO 8601, UTC)
    soc_before_plug_in: bool = False  # the SoC shown is from before the car was plugged in
    active_since: Optional[str] = None
    presses_today: int = 0
    last_action: Optional[dict] = None
    log: list = field(default_factory=list)  # recent starts and stops, newest first
    energy: dict = field(default_factory=dict)  # kWh above the low limit, kWh between the limits, %
    rates: dict = field(default_factory=dict)  # the V2X battery rates, kW (src/rates.py)
    flow: dict = field(default_factory=dict)  # the power flow diagram (src/flow.py)
    stats: dict = field(default_factory=dict)  # the statistics sensors' values (src/stats.py; the page uses /api/stats)
    readings: dict = field(default_factory=dict)
    observe_only: bool = False
    setup_note: Optional[str] = None  # a problem making the helpers, for the page
    card: dict = field(default_factory=dict)  # the dashboard card's install: {"ok", "note"} (src/card.py)
    updated: Optional[str] = None

    def apply(self, snap: dict, observe_only: bool) -> None:
        inputs = dict(snap.get("inputs") or {})
        self.timers = {k: inputs.pop(k, None) for k in ("inactive_for", "export_held_s")}
        self.inputs = inputs
        for key in ("status", "reason", "rule", "soc_at", "soc_before_plug_in", "active_since", "presses_today",
                    "last_action", "log", "energy", "rates", "flow", "readings"):
            setattr(self, key, snap.get(key))
        self.observe_only = observe_only

    def to_dict(self) -> dict:
        return asdict(self)
