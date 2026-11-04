"""What the add-on knows right now, shown on the web page and pushed to it as it changes
(/api/events): the result, the days (without their half hours: /api/day/<date> has those) and
the live readings."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class SharedState:
    status: str = "Starting"  # a word or two: Starting, Working, Needs setting up, Collecting days...
    status_note: str = ""  # ...and a sentence about it
    missing: list = field(default_factory=list)  # what still needs picking on the Settings tab
    result: dict = field(default_factory=dict)  # src.heat_loss.results()
    days: list = field(default_factory=list)  # the days, newest first (src/calculator.py day_list)
    live: dict = field(default_factory=dict)  # now: outside, inside, floors, flow, gas_today
    last_run: Optional[str] = None  # when the days were last brought up to date (ISO, UTC)
    error: Optional[str] = None  # why the last update failed

    def to_dict(self) -> dict:
        return asdict(self)
