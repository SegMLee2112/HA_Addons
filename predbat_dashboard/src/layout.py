"""The dashboard's cards: which, in what order, how big, saved in
/data/layout.json and shared by everyone who opens the page.

A card is {"id", "type"} plus options: "title" to rename it, "size" (s, m, l
or xl: one cell, two, three or the whole width; on a phone, two cells is the
whole width), and "entity" for an entity card. A "section" card is a heading:
the cards after it, up to the next one, are its group. CARDS lists the types
with their usual size; MIN_SIZE how small a chart can go. The page draws them
(src/static/index.html).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# type -> (name, usual size, group, what it shows)
CARDS = {
    # Now
    "battery": ("Battery", "s", "Now", "The home battery's charge, and how long it lasts"),
    "power": ("Energy flow", "m", "Now", "Power flowing now between solar, the grid, the battery and the house"),
    "rates_now": ("Rates now", "s", "Now", "The import and export rate now"),
    "status": ("Predbat status", "s", "Now", "What Predbat is doing, and any error"),
    "carbon": ("Grid carbon", "s", "Now", "The grid's carbon intensity now"),
    # Plan
    "next_charge": ("Next charge", "s", "Plan", "When Predbat next charges the battery, and to what"),
    "next_export": ("Next export", "s", "Plan", "When Predbat next exports from the battery"),
    "next_cheap": ("Next cheap rate", "s", "Plan", "The next low import rate slot"),
    "next_export_rate": ("Next high export rate", "s", "Plan", "The next high export rate slot"),
    "end_soc": ("Battery at the end of the plan", "s", "Plan", "The battery's charge when the plan ends"),
    "car": ("Car charging", "s", "Plan", "The car's next planned charge"),
    # Money and energy
    "cost_today": ("Cost today", "s", "Money", "What today has cost so far, and per kWh"),
    "cost_yesterday": ("Yesterday", "s", "Money", "Yesterday's cost and what Predbat saved"),
    "savings": ("Savings", "s", "Money", "Predbat's savings so far"),
    "energy_today": ("Energy today", "s", "Money", "Imported, exported, used and generated today"),
    "pv": ("Solar forecast", "s", "Money", "Solar today (and what's left) and tomorrow"),
    # Charts
    "soc_chart": ("Battery plan", "l", "Charts", "The battery's charge over the plan, with charging and exporting marked"),
    "rates_chart": ("Rates", "l", "Charts", "Import and export rates over the plan"),
    "pv_load_chart": ("Solar and house", "l", "Charts", "Forecast solar and house use per half hour"),
    "plan_table": ("Plan", "xl", "Charts", "Predbat's plan, slot by slot, without the clutter"),
    # Controls
    "mode": ("Predbat mode", "s", "Controls", "Switch Predbat's mode"),
    "manual_charge": ("Force charge", "s", "Controls", "Charge the battery this half hour (Predbat's manual charge)"),
    "manual_export": ("Force export", "s", "Controls", "Export from the battery this half hour"),
    "manual_freeze_charge": ("Freeze charge", "s", "Controls", "Hold the battery (no discharge) this half hour"),
    "manual_freeze_export": ("Freeze export", "s", "Controls", "Export solar, but not the battery, this half hour"),
    # Anything else
    "entity": ("Any entity", "s", "Other", "Any Home Assistant entity's state"),
    "section": ("Section", "xl", "Other", "A heading that groups the cards below it (and can fold them away)"),
}
SIZES = ("s", "m", "l", "xl")
MIN_SIZE = {"power": "m", "soc_chart": "m", "rates_chart": "m", "pv_load_chart": "m", "plan_table": "l", "section": "xl"}
# Three sections: the figures now, the plan's charts, then the home and its controls
DEFAULT = [{"type": "section", "title": "Now"}, {"type": "status"}, {"type": "battery"}, {"type": "rates_now"},
           {"type": "cost_today"}, {"type": "next_charge"}, {"type": "next_cheap"},
           {"type": "section", "title": "The plan"}, {"type": "soc_chart"}, {"type": "rates_chart"},
           {"type": "section", "title": "Home"}, {"type": "power"}, {"type": "mode"}, {"type": "manual_charge"},
           {"type": "pv"}, {"type": "savings"}]
MAX_CARDS = 60
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def validate_card(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("type") not in CARDS:
        raise ValueError(f"Unknown card: {raw.get('type') if isinstance(raw, dict) else raw}")
    card = {"id": str(raw.get("id") or uuid.uuid4().hex[:8])[:16], "type": raw["type"]}
    title = str(raw.get("title") or "").strip()[:40]
    if title:
        card["title"] = title
    size = raw.get("size")
    if size and raw["type"] != "section":
        if size not in SIZES:
            raise ValueError("The size is s, m, l or xl")
        if SIZES.index(size) < SIZES.index(MIN_SIZE.get(raw["type"], "s")):
            raise ValueError(f"{CARDS[raw['type']][0]} can't be smaller than {MIN_SIZE[raw['type']]}")
        if size != CARDS[raw["type"]][1]:
            card["size"] = size
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
    return [{"type": t, "name": n, "size": s, "min": MIN_SIZE.get(t, "s"), "group": g, "about": a}
            for t, (n, s, g, a) in CARDS.items()]


class Layout:
    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._path = os.path.join(data_dir, "layout.json") if data_dir else None
        self.cards = validate_layout(DEFAULT)
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
        return self.save(DEFAULT)

    def entities(self) -> list[str]:
        """The entities the entity cards show (to follow them)."""
        return sorted({c["entity"] for c in self.cards if c["type"] == "entity" and c.get("entity")})
