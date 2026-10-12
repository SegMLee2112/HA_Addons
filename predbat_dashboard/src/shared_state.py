"""What the add-on is showing right now, pushed to the web page as it changes
(/api/events): Predbat's figures (src/predbat.py data()), the dashboard's
cards (src/layout.py) and the entity cards' states."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SharedState:
    status: str = "Starting"  # a word or two for the header
    reason: str = ""  # ...and why
    data: dict = field(default_factory=dict)  # Predbat's figures for the cards
    cards: list = field(default_factory=list)  # the dashboard's cards, in order
    entities: dict = field(default_factory=dict)  # entity_id -> {name, state, unit} for the entity cards

    def to_dict(self) -> dict:
        return asdict(self)
