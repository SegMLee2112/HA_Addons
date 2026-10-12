"""Keeps the dashboard's figures up to date from Predbat's entities and makes
the few changes the dashboard's controls can (Predbat's mode, and its manual
force charge / export / freeze selects)."""
from __future__ import annotations

import datetime
import logging
import time
from typing import Optional

from src.ha_entities import ALL_SENSORS, entity_info
from src.predbat import MANUAL, Reader, data, is_predbat, slot_option

logger = logging.getLogger(__name__)

DISCOVER_S = 300  # look for new Predbat entities this often


class Runner:
    def __init__(self, link, layout, settings, state) -> None:
        self.link, self.layout, self.settings, self.state = link, layout, settings, state
        self.dirty = True  # something changed: refresh() soon
        self.found = 0  # how many Predbat entities there are
        self._discovered_at: Optional[float] = None
        self.rediscover = False  # look again straight away (the prefix changed)
        self.state.cards = list(layout.cards)

    @property
    def prefix(self) -> str:
        return self.settings.data.get("prefix") or "predbat"

    async def entity_changed(self, entity_id: str, st: Optional[dict]) -> None:
        """A followed entity changed (src/ha_link.py): refreshed within a second (a burst of
        changes, as when it subscribes, is one refresh)."""
        self.dirty = True

    async def discover(self, force: bool = False) -> None:
        """Follow Predbat's entities (found by their prefix) and the entity cards'."""
        if not (force or self.rediscover or self._discovered_at is None
                or time.monotonic() - self._discovered_at >= DISCOVER_S):
            return
        self.rediscover = False
        states = await self.link.all_states(fresh=True)
        ids = {st.get("entity_id") for st in states or []
               if is_predbat(st.get("entity_id") or "", self.prefix) and st.get("entity_id") not in ALL_SENSORS}
        self.found = len(ids)
        self._discovered_at = time.monotonic()
        if self.link.set_extra(ids | set(self.layout.entities())):
            logger.info("Following %d Predbat entities (prefix %s)", self.found, self.prefix)
        self.dirty = True

    def follow_cards(self) -> None:
        """The layout changed: follow any new entity card's entity."""
        self.state.cards = list(self.layout.cards)
        predbat = {e for e in self.link.extra if is_predbat(e, self.prefix)}
        self.link.set_extra(predbat | set(self.layout.entities()))
        self.dirty = True

    def refresh(self, now: Optional[datetime.datetime] = None) -> None:
        self.dirty = False
        d = data(self.link.states, self.prefix, now)
        d["plan"] = d["plan"][: int(self.settings.data.get("plan_hours") or 24) * 2]
        d["end_soc"] = d["plan"][-1]["soc"] if d["plan"] else None
        d["entities_found"] = self.found
        self.state.data = d
        self.state.entities = {e: entity_info(self.link.states, e) for e in self.layout.entities()}
        if not self.link.available:
            self.state.status, self.state.reason = "Offline", "Not running under Home Assistant"
        elif not self.link.connected:
            self.state.status, self.state.reason = "Offline", "Not connected to Home Assistant"
        elif not d["found"]:
            self.state.status = "Waiting"
            self.state.reason = (f"No Predbat entities found (looking for {self.prefix}.status, "
                                 f"select.{self.prefix}_mode...). Is Predbat running? Check the prefix on the Settings tab.")
        else:
            self.state.status = "Error" if d["status"]["error"] else "Running"
            self.state.reason = d["status"]["text"] or ""

    # --- controls ---------------------------------------------------------------

    def _select(self, entity: str) -> tuple[str, list[str]]:
        r = Reader(self.link.states, self.prefix)
        st = r.st(entity)
        eid = r.eid(entity)
        if st is None or not eid or not eid.startswith(("select.", "input_select.")):
            raise ValueError(f"Predbat's {entity} select isn't there (select.{self.prefix}_{entity})")
        return eid, [str(o) for o in (st.get("attributes") or {}).get("options") or []]

    async def control(self, body: dict) -> dict:
        """{"control": "mode", "value": "Control charge"}, or
        {"control": "charge"|"export"|"freeze_charge"|"freeze_export"|"demand",
         "op": "now" | "add" | "remove" | "clear", "option": "Mon 22:30" (add, remove)}."""
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object")
        control = body.get("control")
        if control == "mode":
            eid, options = self._select("mode")
            value = str(body.get("value") or "")
            if value not in options:
                raise ValueError("The mode is one of: " + ", ".join(options))
            return await self._choose(eid, value, f"Predbat mode: {value}")
        if control not in MANUAL:
            raise ValueError("Unknown control: " + ", ".join(["mode", *MANUAL]))
        suffix, name = MANUAL[control]
        eid, options = self._select(suffix)
        op = body.get("op")
        if op == "clear":
            return await self._choose(eid, "off", f"{name}: cleared")
        if op == "now":
            option = slot_option(options)
            if option is None:
                raise ValueError(f"{name} has no slot for now")
            if option.startswith("["):
                return {"status": "ok", "message": f"{name} is already on for this half hour"}
            return await self._choose(eid, option, f"{name}: {option}")
        if op in ("add", "remove"):
            want = str(body.get("option") or "").strip("[] ")
            match = [o for o in options if o.strip("[]") == want]
            if not want or not match:
                raise ValueError(f"{name} has no slot {want or '(none given)'}")
            option = match[0]
            if (op == "add") == option.startswith("["):  # already as asked
                return {"status": "ok", "message": f"{name} {want}: already {'on' if op == 'add' else 'off'}"}
            return await self._choose(eid, option, f"{name}: {'+' if op == 'add' else '-'}{want}")
        raise ValueError("op is now, add, remove or clear")

    async def _choose(self, entity_id: str, option: str, what: str) -> dict:
        domain = entity_id.split(".", 1)[0]
        await self.link.call_service(domain, "select_option", {"option": option}, {"entity_id": entity_id})
        logger.info("%s", what)
        return {"status": "ok", "message": what}
