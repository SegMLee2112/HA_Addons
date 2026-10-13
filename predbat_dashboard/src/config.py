"""The add-on's options (Settings › Add-ons › this add-on › Configuration).

Home Assistant writes them to /data/options.json; the schema is in
config.yaml and the labels in translations/en.yaml. Add an option in all
three places and here. Settings made on the web page live elsewhere (see
src/app_settings.py and src/ha_link.py)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields

OPTIONS_PATH = os.environ.get("ADDON_OPTIONS", "/data/options.json")


@dataclass(frozen=True)
class Config:
    """No options yet: everything is set on the web page."""


def load_config(path: str = OPTIONS_PATH) -> Config:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, ValueError):
        raw = {}  # running outside Home Assistant (development): the defaults
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in (raw or {}).items() if k in known})
