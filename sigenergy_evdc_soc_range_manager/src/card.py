"""The power flow card for your own dashboards: copies src/static/evdc-power-flow-card.js
into Home Assistant's www folder (served at /local/) and adds it to the dashboards'
resources, so "EVDC Power Flow" shows in the card picker. It draws
sensor.evdc_soc_range_power_flow (src/ha_entities.py).

Needs the add-on's access to Home Assistant's config folder (config.yaml map:
homeassistant_config, mounted at /homeassistant). Done each time it connects; the
resource's URL carries the version, so browsers fetch the new card after an update.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CARD_FILE = "evdc-power-flow-card.js"
SOURCE = Path(__file__).parent / "static" / CARD_FILE
HA_CONFIG = os.environ.get("HA_CONFIG_DIR", "/homeassistant")
LOCAL_URL = f"/local/{CARD_FILE}"


def copy_card(config_dir: str = HA_CONFIG, source: Path = SOURCE) -> dict:
    """Write the card into <config>/www if it isn't there or has changed.
    Returns {"written": bool, "new_www": bool} (new_www: Home Assistant must restart
    once before it serves /local/)."""
    if not os.path.isdir(config_dir):
        raise FileNotFoundError(f"Home Assistant's config folder isn't mapped ({config_dir})")
    www = os.path.join(config_dir, "www")
    new_www = not os.path.isdir(www)
    os.makedirs(www, exist_ok=True)
    dest = os.path.join(www, CARD_FILE)
    body = source.read_bytes()
    try:
        with open(dest, "rb") as f:
            if f.read() == body:
                return {"written": False, "new_www": new_www}
    except FileNotFoundError:
        pass
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, dest)
    return {"written": True, "new_www": new_www}


async def register(link, version: str) -> str:
    """Add (or update) the dashboards' resource. Returns what was done."""
    url = f"{LOCAL_URL}?v={version}"
    resources = await link.query({"type": "lovelace/resources"}) or []
    mine = [r for r in resources if str(r.get("url", "")).split("?")[0] == LOCAL_URL]
    if not mine:
        await link.query({"type": "lovelace/resources/create", "res_type": "module", "url": url})
        return "added"
    if mine[0].get("url") != url:
        await link.query({"type": "lovelace/resources/update", "resource_id": mine[0]["id"],
                          "res_type": "module", "url": url})
        return "updated"
    return "already there"


async def install(link, version: str, config_dir: str = HA_CONFIG) -> dict:
    """Copy and register the card. Returns {"ok", "note"} for the Diagnostics page."""
    note: Optional[str] = None
    try:
        copied = copy_card(config_dir)
    except Exception as err:
        return {"ok": False, "note": f"Couldn't copy the card into Home Assistant's www folder: {err}"}
    try:
        done = await register(link, version)
        logger.info("Power flow card: %s (%s)", done, LOCAL_URL)
    except Exception as err:
        note = (f"Couldn't add it to the dashboards' resources ({err}). Add {LOCAL_URL} yourself "
                "(Settings › Dashboards › ⋮ › Resources, JavaScript module).")
        return {"ok": False, "note": note}
    if copied["new_www"]:
        note = "Home Assistant's www folder was new: restart Home Assistant once so it serves the card."
    return {"ok": True, "note": note}
