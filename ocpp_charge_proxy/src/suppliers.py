"""Each supplier's defaults, from suppliers.json next to this file, and your
own changes to them from an override file.

Everything the add-on knows about a supplier is here, not in the code:
its daily limit and when it resets, the ready times its app accepts, how its
smart charging sensor and ready time entity are found (entity names, the
attributes and fields of its planned slots), its saving sessions calendar,
its colour on the page, and its Home Assistant integration (to notice when
that's updated). See suppliers.json's "_about" for each field.

The override: /config/suppliers.json (the add-on's own folder in Home
Assistant, /addon_configs/<this add-on>/ over Samba or the File editor). It
has the same layout but only needs what's different: a supplier with the same
name has those fields changed, a new name is added. It's read again when it
changes (within a few minutes), so a supplier's change can be fixed the same
day, without a new version of the add-on. Anything set on the web page
(Settings › Your supplier) still wins. If a file is missing or broken, the
one before it is used and the log (and Diagnostics › Health) says why.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from typing import Optional

from src import supplier_types
from src.supplier_types import kraken

logger = logging.getLogger(__name__)

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "suppliers.json")
OVERRIDE_PATH = os.environ.get("SUPPLIERS_OVERRIDE", "/config/suppliers.json")
ALL_DAY = [f"{h:02}:{m:02}" for h in range(24) for m in (0, 30)]
CHECK_EVERY_S = 60  # the override is looked at (its modified time) at most this often

# The Kraken integrations' (Octopus Energy, EDF Energy) names for things
# (src/supplier_types/kraken.py): also used for a supplier of an unknown kind
KRAKEN = kraken.DEFAULTS
SAVING_DEFAULTS = {
    # Words in a calendar's ID or name that mean saving sessions (lower case, "_" for spaces)
    "words": ["power_down", "saving_session", "savings_session", "saver_session", "saver",
              "demand_flex", "flexibility", "turn_down", "peak_save", "beat_the_peak"],
    "not_words": ["power_up", "free_electricity", "free_power", "free_session", "boost"],
    "prefer": ["power_down"],  # picked first, when several are found
    # Saving sessions from sensors rather than a calendar: each source's next event is
    # three sensors (its start and end times, and its type). Found by the end of their
    # entity IDs; only events of the given types are saving sessions (Axle Energy's
    # "import" events are the opposite: use more)
    "sensors": [
        {"name": "Axle Energy", "start": "axle_energy_event_start", "end": "axle_energy_event_end",
         "type": "axle_energy_event_type", "types": ["export"]},
    ],
}

BUILT_IN = {
    "suppliers": [
        {"name": "Octopus Energy", "integration": "octopus_energy", "colour": "#8e24aa",
         "dispatch_prefix": "binary_sensor.octopus_energy_", "calendar_words": ["octopus"],
         "ready_times": {"from": "00:00", "to": "23:30"}, "daily_limit_h": 6, "limit_reset": "12:00"},
        {"name": "EDF Energy", "integration": "edf_energy", "colour": "#f9a825",
         "dispatch_prefix": "binary_sensor.edf_energy_", "calendar_words": ["edf"],
         "ready_times": {"from": "04:00", "to": "11:00"}, "daily_limit_h": None, "limit_reset": "rolling"},
        {"name": "E.ON Next", "integration": "eon_next", "colour": "#e53935",
         "schedule_sensor_suffix": "smart_charging_schedule", "schedule_attribute": "schedule",
         "slot_fields": {"start": "start", "end": "end", "kwh": "energy_added_kwh", "source": "type"},
         "calendar_words": ["eon", "e_on"],
         "ready_times": None, "daily_limit_h": None, "limit_reset": "rolling"},
    ],
    "other": {"name": "Your supplier", "ready_times": {"from": "04:00", "to": "11:00"},
              "daily_limit_h": None, "limit_reset": "rolling"},
}


def _text(entry: dict, key: str, where: str, pattern: Optional[str] = None) -> Optional[str]:
    value = entry.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{where}: {key} must be text")
    value = value.strip()
    if pattern and not re.fullmatch(pattern, value):
        raise ValueError(f"{where}: {key} {value!r} isn't valid")
    return value


def _words(entry: dict, key: str, where: str) -> Optional[list]:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(w, str) and w.strip() for w in value):
        raise ValueError(f"{where}: {key} must be a list of words")
    return [w.strip().lower() for w in value]


def _check(entry: dict, where: str) -> dict:
    """One supplier's entry, checked: raises ValueError saying what's wrong."""
    if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
        raise ValueError(f"{where}: needs a name")
    out = {"name": str(entry["name"]).strip()}
    for key, pattern in (("dispatch_prefix", r"[a-z_]+\.[a-z0-9_]*"), ("schedule_sensor_suffix", r"[a-z0-9_]+"),
                         ("integration", r"[a-z0-9_]+"), ("colour", r"#[0-9a-fA-F]{6}"),
                         ("dispatch_suffix", r"[a-z0-9_]+"), ("ready_entity_suffix", r"[a-z0-9_]+"),
                         ("planned_attribute", r"[A-Za-z0-9_]+"), ("completed_attribute", r"[A-Za-z0-9_]+"),
                         ("schedule_attribute", r"[A-Za-z0-9_]+"),
                         # its rates, for the automatic schedule (src/auto_schedule.py)
                         ("rates_prefix", r"[a-z_]+\.[a-z0-9_]*"), ("rates_adjusted_field", r"[A-Za-z0-9_]+")):
        value = _text(entry, key, where, pattern)
        if value:
            out[key] = value
    # Its kind (src/supplier_types): given, or worked out from what it has; the kind's
    # names fill in any it doesn't give
    if entry.get("type") is not None and entry.get("type") not in supplier_types.TYPES:
        raise ValueError(f"{where}: type must be one of {', '.join(supplier_types.TYPES)}")
    kind = supplier_types.kind_of({**out, "type": entry.get("type")})
    if entry.get("type") and kind and not all(out.get(k) for k in kind.REQUIRED):
        raise ValueError(f"{where}: a {kind.NAME} supplier needs {', '.join(kind.REQUIRED)}")
    if kind:
        out["type"] = kind.NAME
        for key, value in kind.DEFAULTS.items():
            if key != "slot_fields":
                out.setdefault(key, value)
    fields = entry.get("slot_fields")
    if fields is not None and (not isinstance(fields, dict) or not all(isinstance(v, str) and v for v in fields.values())):
        raise ValueError(f"{where}: slot_fields must name each field, e.g. {{\"start\": \"start\", \"end\": \"end\"}}")
    out["slot_fields"] = {**(kind.DEFAULTS if kind else KRAKEN)["slot_fields"], **(fields or {})}
    words = _words(entry, "calendar_words", where)
    if words:
        out["calendar_words"] = words
    times = entry.get("ready_times")
    if times is None:
        out["ready_times"] = None
    else:
        lo, hi = (times or {}).get("from"), (times or {}).get("to")
        if lo not in ALL_DAY or hi not in ALL_DAY or lo > hi:
            raise ValueError(f"{where}: ready_times needs from / to on the hour or half hour, from before to")
        out["ready_times"] = {"from": lo, "to": hi}
    hours = entry.get("daily_limit_h")
    if hours is not None:
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            raise ValueError(f"{where}: daily_limit_h must be a number of hours or null") from None
        if not 0 < hours <= 24 or hours * 2 != int(hours * 2):
            raise ValueError(f"{where}: daily_limit_h must be 0.5 to 24, in half hours (null: no limit)")
    out["daily_limit_h"] = hours
    reset = str(entry.get("limit_reset") or "rolling").strip()
    if reset != "rolling" and reset not in ALL_DAY:
        raise ValueError(f"{where}: limit_reset must be \"rolling\" or a time on the hour or half hour")
    out["limit_reset"] = reset
    return out


def _saving_sensors(value) -> list:
    """saving_sessions.sensors, checked (raises ValueError)."""
    if not isinstance(value, list):
        raise ValueError("saving_sessions: sensors must be a list")
    out = []
    for i, e in enumerate(value):
        where = f"saving_sessions sensor source {i + 1}"
        if not isinstance(e, dict) or not str(e.get("name") or "").strip():
            raise ValueError(f"{where}: needs a name")
        entry = {"name": str(e["name"]).strip()}
        for key in ("start", "end", "type"):
            v = e.get(key)
            if v is None and key == "type":
                continue
            if not isinstance(v, str) or not re.fullmatch(r"[a-z0-9_]+", v):
                raise ValueError(f"{where} ({entry['name']}): {key} must be the end of an entity ID (a-z, 0-9, _)")
            entry[key] = v
        types = _words(e, "types", where)
        if types is not None:
            entry["types"] = types
        out.append(entry)
    return out


def parse(data: dict) -> dict:
    """The file's contents, checked (raises ValueError)."""
    if not isinstance(data, dict) or not isinstance(data.get("suppliers"), list):
        raise ValueError("needs a \"suppliers\" list")
    suppliers = [_check(e, f"supplier {i + 1} ({(e or {}).get('name', '?') if isinstance(e, dict) else '?'})")
                 for i, e in enumerate(data["suppliers"])]
    other = _check(dict({"name": "Your supplier"}, **(data.get("other") or {})), "other")
    saving = dict(SAVING_DEFAULTS)
    raw = data.get("saving_sessions") or {}
    if not isinstance(raw, dict):
        raise ValueError("saving_sessions must be an object")
    for key in ("words", "not_words", "prefer"):
        words = _words(raw, key, "saving_sessions")
        if words is not None:
            saving[key] = words
    if raw.get("sensors") is not None:
        saving["sensors"] = _saving_sensors(raw["sensors"])
    return {"suppliers": suppliers, "other": other, "saving_sessions": saving}


def merge(base: dict, override: dict) -> dict:
    """The override on top of the base (both as in the files, unchecked): a supplier
    with the same name (any case) has the fields given changed, a new one is added;
    "other" and "saving_sessions" have the fields given changed."""
    if not isinstance(override, dict):
        raise ValueError("must be an object")
    out = copy.deepcopy(base)
    listed = {str(s.get("name", "")).strip().lower(): s for s in out.get("suppliers", []) if isinstance(s, dict)}
    for s in override.get("suppliers") or []:
        if not isinstance(s, dict) or not str(s.get("name") or "").strip():
            raise ValueError("each supplier needs a name")
        key = str(s["name"]).strip().lower()
        if key in listed:
            listed[key].update({k: v for k, v in s.items() if k != "name"})
        else:
            out.setdefault("suppliers", []).append(dict(s))
    for section in ("other", "saving_sessions"):
        if isinstance(override.get(section), dict):
            out[section] = {**(out.get(section) or {}), **override[section]}
    return out


def _read(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# What was loaded, for Diagnostics › Health: the override's path, when it was
# read, and any problem with either file
INFO: dict = {"override": None, "override_error": None, "error": None, "loaded_at": None}
_override_seen: tuple = (None, 0.0)  # (its modified time, when looked)


def load(path: str = PATH, override: Optional[str] = None) -> dict:
    """The defaults: `path` (else the built-in ones), with `override` on top if it exists."""
    INFO.update(override=None, override_error=None, error=None, loaded_at=time.time())
    try:
        base = _read(path)
        parse(base)
    except FileNotFoundError:
        logger.warning("Supplier defaults: %s not found, using the built-in ones", path)
        INFO["error"] = f"{path} not found"
        base = BUILT_IN
    except (ValueError, OSError) as err:  # json.JSONDecodeError is a ValueError
        logger.warning("Supplier defaults: %s can't be used (%s), using the built-in ones", path, err)
        INFO["error"] = f"{os.path.basename(path)}: {err}"
        base = BUILT_IN
    if override and os.path.exists(override):
        try:
            data = parse(merge(base, _read(override)))
            INFO["override"] = override
            logger.info("Supplier defaults: your changes in %s are used", override)
            return data
        except (ValueError, OSError) as err:
            logger.warning("Supplier defaults: your changes in %s can't be used (%s), so they're left out", override, err)
            INFO["override_error"] = f"{override}: {err}"
    return parse(base)


def _override_mtime() -> Optional[float]:
    try:
        return os.stat(OVERRIDE_PATH).st_mtime
    except OSError:
        return None


DATA = load(override=OVERRIDE_PATH)
_override_seen = (_override_mtime(), time.monotonic())


def refresh(force: bool = False) -> bool:
    """Read the files again if the override was added, changed or removed (looked at
    at most every CHECK_EVERY_S). True if they were read again."""
    global DATA, _override_seen
    seen, looked = _override_seen
    if not force and time.monotonic() - looked < CHECK_EVERY_S:
        return False
    mtime = _override_mtime()
    _override_seen = (mtime, time.monotonic())
    if not force and mtime == seen:
        return False
    DATA = load(override=OVERRIDE_PATH)
    return True


def write_example(folder: str = os.path.dirname(OVERRIDE_PATH)) -> Optional[str]:
    """Put the add-on's own defaults in your folder as suppliers.example.json (each
    start, so it's current), to copy from. Returns its path, or None if there's no folder."""
    if not folder or not os.path.isdir(folder):
        return None
    path = os.path.join(folder, "suppliers.example.json")
    try:
        with open(PATH, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    except OSError as err:
        logger.debug("Couldn't write %s: %s", path, err)
        return None


def all_suppliers() -> list[dict]:
    return DATA["suppliers"]


def other() -> dict:
    return DATA["other"]


def saving_words() -> dict:
    """{"words", "not_words", "prefer"} for finding the saving sessions calendar, and
    "sensors": the sources whose saving sessions are read from sensors (Axle Energy)."""
    return DATA["saving_sessions"]


def colours() -> dict:
    """Each supplier's colour on the page: {name: "#rrggbb"}."""
    return {s["name"]: s["colour"] for s in DATA["suppliers"] if s.get("colour")}


def by_name(name: Optional[str]) -> dict:
    """A supplier's defaults by the name shown on the page (other: the defaults for any other)."""
    return next((s for s in DATA["suppliers"] if s["name"] == name), DATA["other"])


def for_entity(entity_id: Optional[str]) -> dict:
    """The supplier an entity belongs to (its dispatching sensor, or the ready
    time entity next to it), else the defaults for any other supplier."""
    entity_id = str(entity_id or "")
    for s in DATA["suppliers"]:
        kind = supplier_types.kind_of(s)
        if kind and kind.matches_entity(entity_id, s):
            return s
    return DATA["other"]


def ready_times(supplier: dict) -> list[str]:
    """The ready times a supplier accepts ("HH:MM"), or [] if it has none."""
    t = supplier.get("ready_times")
    if not t:
        return []
    return [x for x in ALL_DAY if t["from"] <= x <= t["to"]]


def daily_limit_min(name: Optional[str]) -> Optional[int]:
    hours = by_name(name).get("daily_limit_h")
    return int(round(hours * 60)) if hours else None


def limit_reset(name: Optional[str]) -> str:
    """"HH:MM" or "rolling"."""
    return by_name(name).get("limit_reset") or "rolling"
