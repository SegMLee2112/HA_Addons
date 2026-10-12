"""The dashboard's cards: which, in what order, saved in /data/layout.json and
shared by everyone who opens the page.

A card is {"id", "type"} plus, for some types, options ("entity" for an
entity card, "title" to rename any card). CARDS lists the types: a chip is
one figure (one grid cell), a double one takes two cells, a wide card is a
chart or table (half the width; the whole width on a phone). The page draws them (src/static/index.html).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# type -> (name, size, group, what it shows)
CARDS = {
    # Now
    "battery": ("Battery", "chip", "Now", "The home battery's charge, and how long it lasts"),
    "power": ("Energy flow", "double", "Now", "Power flowing now between solar, the grid, the battery and the house"),
    "rates_now": ("Rates now", "chip", "Now", "The import and export rate now"),
    "status": ("Predbat status", "chip", "Now", "What Predbat is doing, and any error"),
    "carbon": ("Grid carbon", "chip", "Now", "The grid's carbon intensity now"),
    # Plan
    "next_charge": ("Next charge", "chip", "Plan", "When Predbat next charges the battery, and to what"),
    "next_export": ("Next export", "chip", "Plan", "When Predbat next exports from the battery"),
    "next_cheap": ("Next cheap rate", "chip", "Plan", "The next low import rate slot"),
    "next_export_rate": ("Next high export rate", "chip", "Plan", "The next high export rate slot"),
    "end_soc": ("Battery at the end of the plan", "chip", "Plan", "The battery's charge when the plan ends"),
    "car": ("Car charging", "chip", "Plan", "The car's next planned charge"),
    # Money and energy
    "cost_today": ("Cost today", "chip", "Money", "What today has cost so far, and per kWh"),
    "cost_yesterday": ("Yesterday", "chip", "Money", "Yesterday's cost and what Predbat saved"),
    "savings": ("Savings", "chip", "Money", "Predbat's savings so far"),
    "energy_today": ("Energy today", "chip", "Money", "Imported, exported, used and generated today"),
    "pv": ("Solar forecast", "chip", "Money", "Solar today (and what's left) and tomorrow"),
    # Charts
    "soc_chart": ("Battery plan", "wide", "Charts", "The battery's charge over the plan, with charging and exporting marked"),
    "rates_chart": ("Rates", "wide", "Charts", "Import and export rates over the plan"),
    "pv_load_chart": ("Solar and house", "wide", "Charts", "Forecast solar and house use per half hour"),
    "plan_table": ("Plan", "wide", "Charts", "Predbat's plan, slot by slot, without the clutter"),
    # Controls
    "mode": ("Predbat mode", "chip", "Controls", "Switch Predbat's mode"),
    "manual_charge": ("Force charge", "chip", "Controls", "Charge the battery this half hour (Predbat's manual charge)"),
    "manual_export": ("Force export", "chip", "Controls", "Export from the battery this half hour"),
    "manual_freeze_charge": ("Freeze charge", "chip", "Controls", "Hold the battery (no discharge) this half hour"),
    "manual_freeze_export": ("Freeze export", "chip", "Controls", "Export solar, but not the battery, this half hour"),
    # Anything else
    "entity": ("Any entity", "chip", "Other", "Any Home Assistant entity's state"),
}
# Six chips, two charts, then the energy flow (two cells) and four chips: rows that fill on a computer's screen
DEFAULT = ["status", "battery", "rates_now", "cost_today", "next_charge", "next_cheap",
           "soc_chart", "rates_chart",
           "power", "mode", "manual_charge", "pv", "savings"]
MAX_CARDS = 60
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def validate_card(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("type") not in CARDS:
        raise ValueError(f"Unknown card: {raw.get('type') if isinstance(raw, dict) else raw}")
    card = {"id": str(raw.get("id") or uuid.uuid4().hex[:8])[:16], "type": raw["type"]}
    title = str(raw.get("title") or "").strip()[:40]
    if title:
        card["title"] = title
    if raw["type"] == "entity":
        entity = str(raw.get("entity") or "").strip()
        if entity and not _ENTITY.match(entity):
            raise ValueError("The entity looks like sensor.living_room_temperature")
        card["entity"] = entity
    return card


def validate_layout(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("Send a list of cards")
    if len(raw) > MAX_CARDS:
        raise ValueError(f"At most {MAX_CARDS} cards")
    cards, seen = [], set()
    for item in raw:
        card = validate_card(item)
        while card["id"] in seen:
            card["id"] = uuid.uuid4().hex[:8]
        seen.add(card["id"])
        cards.append(card)
    return cards


def catalog() -> list[dict]:
    return [{"type": t, "name": n, "size": s, "group": g, "about": a} for t, (n, s, g, a) in CARDS.items()]


class Layout:
    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._path = os.path.join(data_dir, "layout.json") if data_dir else None
        self.cards = validate_layout([{"type": t} for t in DEFAULT])
        self._load()

    def _load(self) -> None:
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            self.cards = validate_layout([c for c in raw if isinstance(c, dict) and c.get("type") in CARDS])
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("The layout %s is unreadable: using the default", self._path)

    def save(self, raw) -> list[dict]:
        self.cards = validate_layout(raw)
        if self._path:
            try:
                tmp = self._path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.cards, f, indent=1)
                os.replace(tmp, self._path)
            except Exception:
                logger.warning("Could not save the layout", exc_info=True)
        return self.cards

    def reset(self) -> list[dict]:
        return self.save([{"type": t} for t in DEFAULT])

    def entities(self) -> list[str]:
        """The entities the entity cards show (to follow them)."""
        return sorted({c["entity"] for c in self.cards if c["type"] == "entity" and c.get("entity")})
