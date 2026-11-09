"""Plug-in schedule and automatic re-plug.

- Schedule: switch Plugged In on or off at set times, on chosen days, as
  many times a day as you like. Times are local (the add-on's TZ, which Home
  Assistant sets to your configured time zone). A plug-in time missed while
  the add-on was stopped isn't caught up; an unplug is (see due()).
- Re-plug: if the car is plugged in but the supplier hasn't started a
  session after `after_min` minutes, unplug, wait REPLUG_WAIT_S seconds and
  plug back in, up to `attempts` times. The count resets when a session
  starts or when the car is unplugged by anything else. If your supplier's
  smart charging plan is available (src/smart_charging.py), a slot planned
  for later counts as an answer: no re-plug while one is planned, only when
  nothing has been scheduled `after_min` minutes after plugging in. A slot
  running now doesn't: the supplier should be charging. But it may only
  start a session at the next half hour (Octopus does, e.g. after the
  add-on restarted mid-slot), so the wait is counted from then: with no
  session `after_min` minutes after the next :00 or :30, it re-plugs.

- One-off times: an entry with a `date` runs once, on that day, and is
  removed after it has run. Skips: any upcoming slot (a plug-in and its
  unplug, weekly or one-off) can be skipped once; skipped times don't
  run, and the skips are forgotten a day after.
- Auto plug-in charge: when auto plug-in (low SoC) switches Plugged In on
  and its "set my supplier's ready time" option is on, plan_auto_plug adds
  a one-off slot to the schedule: plug in now, unplug at the first ready
  time your supplier accepts at least the chosen hours away. If that puts
  more than Octopus's 6 hours in 24 (only checked with Force schedule on
  supplier on, as for the schedule), the next scheduled slot starts that
  much later (its first hours move to now). If it then meets or overlaps
  the next scheduled slot, the two become one slot. The ready time is set
  for the end of the slot. Its entries carry "source": "auto_plug" and run
  even with the schedule off.
- At start-up: if the schedule is on and the add-on starts inside a
  plugged-in stretch (the last schedule time before now was a plug-in) with
  Plugged In off, it plugs in, as if it had been running at that time.
- Graceful unplug: with the ready time on (Force schedule on supplier), a
  scheduled unplug during a session waits up to `unplug_wait_s` (default
  GRACEFUL_UNPLUG_S, set on the page; 0 = don't wait) for the supplier to
  stop the session itself (RemoteStop), then unplugs.
- Plan check: with the ready time on, while the schedule has the car
  plugged in, the supplier's planned slots up to the ready time are checked
  against the schedule (src/plan_check.py); a mismatch is shown on the page.
- Saving sessions (src/saving_sessions.py): with "off-peak time" on, each
  upcoming session from the calendar is an off-peak time (as the weekly
  ones: auto plug-in, Charge now... unplug for it), and a scheduled slot that
  runs into it keeps its end and its length but isn't plugged in during the
  session, so it starts that much earlier (15:30–21:30 with an 18:00–19:00
  session: 14:30–18:00 and 19:00–21:30), as one-offs (the weekly entries are
  skipped that day). What a session changed is put back if it's cancelled.
  With "plug in before" on, a charge of the chosen hours before it is added
  to the schedule as soon as the session is known, for the time the schedule
  doesn't already cover (sync_saving_sessions, _plan_precharge).
- Off-peak times: at the start of each, it unplugs if plugged in (by hand,
  Home Assistant...), as nothing should charge then (block_starts_due).
- Random start delay: optionally, each of your slots' plug-ins waits a random
  time (up to the minutes set, and at most half the slot) before plugging in,
  so it isn't the same time every day. Not car slots (auto plug-in, Charge
  now...), which start straight away, nor where two slots meet. If the slot
  ends, is skipped or the schedule is turned off meanwhile, it doesn't plug in.
- Ready time: optionally, when the schedule plugs in, set your supplier's
  smart charging ready-by time to the schedule's next unplug
  (src/ready_time.py). Only at a scheduled plug-in.

Settings are saved in /data/automation.json and set on the web page's
Automations tab. Until they're saved, re-plug uses the defaults.
"""

from __future__ import annotations

import asyncio
import copy
import datetime
import logging
import math
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from src import jsonfile
from src.plan_check import SETTLE_S, check_plan
from src.ready_time import ALL_DAY_TIMES, check_unplug_times
from src.saving_sessions import session_id

logger = logging.getLogger(__name__)

REPLUG_WAIT_S = 30
STARTUP_READY_S = 180  # keep trying to set the ready time this long after start-up
READY_RETRY_MIN = (1, 2, 5, 10, 10, 15, 15, 30)  # setting the ready time failed: tried again after these
GRACEFUL_UNPLUG_S = 60  # with Force schedule on supplier: wait this long for it to stop a session (default)
MAX_UNPLUG_WAIT_S = 600
PENDING_TICK_S = 2  # check this often while waiting
TICK_S = 15
NEXT_SLOTS = 6  # the next slots published for Predbat's rate overrides (src/ha_entities.py)
SLOT_LEAD_MIN = 5  # ...each dropped this long before it starts (Settings tab), so a planner can replan
CATCH_UP_S = 600  # a scheduled time is still run if noticed within this (or if nothing's come after it): more
# than the longest re-plug wait (off_s, 300 s), during which the loop doesn't look
MAX_ONE_OFF_DAYS = 62  # a one-off slot can be at most this far ahead
# How many schedule times can be saved: your own (weekly, and one-offs still to come) and
# everything (with the charges the add-on adds, and one-offs kept for 14 days after they ran)
MAX_YOUR_ENTRIES = 100
MAX_ENTRIES = 1000
MISSED_MAX_S = 86400  # after a restart, times missed while stopped are looked for this far back
REPLUG_UNPLUG_MARGIN_S = 60  # no re-plug if a scheduled unplug comes before it'd be plugged back in + this
ACTIONS = ("plug", "unplug")
AUTO_SOURCE = "auto_plug"  # entries added by an auto plug-in charge
CHARGE_NOW_SOURCE = "charge_now"  # ... or by the Charge now button
CAR_PLUGGED_SOURCE = "car_plugged"  # ... or by the car plugged in sensor
SAVING_SOURCE = "saving_session"  # ... or before a saving session
AUTO_SCHED_SOURCE = "auto_schedule"  # ... or by the automatic schedule (src/auto_schedule.py)
AUTO_SOURCES = (AUTO_SOURCE, CHARGE_NOW_SOURCE, CAR_PLUGGED_SOURCE, SAVING_SOURCE, AUTO_SCHED_SOURCE)
# Charges that come before the automatic schedule: it plans around them, never takes time from them
FIRST_SOURCES = (AUTO_SOURCE, CHARGE_NOW_SOURCE, CAR_PLUGGED_SOURCE, SAVING_SOURCE)
# The automatic schedule's rules: a plan made by older ones is made again (e.g. after an update)
AUTO_PLAN_VERSION = 8
SOURCE_NAMES = {AUTO_SOURCE: "Auto plug-in charge", CHARGE_NOW_SOURCE: "Charge now",
                CAR_PLUGGED_SOURCE: "Car plugged in charge", SAVING_SOURCE: "Saving session charge"}
SAVING_KEEP = datetime.timedelta(days=8)  # a saving session is remembered this long after it ends (the
# Sessions tab shows the last week's)
MIN_PRECHARGE_MIN = 15  # less than this left before a session: no charge before it
ONE_OFF_KEEP = datetime.timedelta(days=14)  # one-off times (and skips) are kept this long after they ran
MAX_START_DELAY_MIN = 60  # the random start delay is at most this
DEFAULT_START_DELAY = {"enabled": False, "max_min": 10}


@dataclass(frozen=True)
class ReplugOptions:
    """Re-plug defaults (the web page's settings are saved over them)."""

    enabled: bool = True
    after_min: int = 10
    attempts: int = 3
    off_s: int = REPLUG_WAIT_S  # left unplugged this long before plugging back in

    def as_dict(self) -> dict:
        return {"enabled": self.enabled, "after_min": self.after_min, "attempts": self.attempts, "off_s": self.off_s}


def _next_half_hour(t: datetime.datetime) -> datetime.datetime:
    """`t` if it's on the hour or half hour, else the next one (14:01 -> 14:30)."""
    t = t.replace(second=0, microsecond=0)
    return t if t.minute in (0, 30) else t + datetime.timedelta(minutes=30 - t.minute % 30)


def _iso_local(dt: Optional[datetime.datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def _iso_from_epoch(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_hhmm(value) -> tuple[int, int]:
    try:
        hh, mm = str(value).strip().split(":")
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        raise ValueError(f"Time must be HH:MM, got {value!r}") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Time must be HH:MM, got {value!r}")
    return h, m


def _parse_iso(value) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _flag(value, name: str, default: bool) -> bool:
    """A true/false setting: None is the default; anything but a real boolean is refused
    (so the text "false" isn't taken as on)."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _parse_date(value) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"Date must be YYYY-MM-DD, got {value!r}") from None


def validate_block(raw: dict) -> dict:
    """An off-peak time: {id, start "HH:MM", end "HH:MM", days [0=Mon..6=Sun]};
    overnight when end <= start (it starts on the days given)."""
    if not isinstance(raw, dict):
        raise ValueError("Each off-peak time must be an object")
    h1, m1 = _parse_hhmm(raw.get("start"))
    h2, m2 = _parse_hhmm(raw.get("end"))
    if (h1, m1) == (h2, m2):
        raise ValueError("An off-peak time must start and end at different times")
    days = raw.get("days", list(range(7)))
    if not isinstance(days, list) or not days or any(
        not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days
    ):
        raise ValueError("Days must be a non-empty list of 0 (Mon) to 6 (Sun)")
    return {"id": str(raw.get("id") or uuid.uuid4().hex[:8]), "start": f"{h1:02d}:{m1:02d}",
            "end": f"{h2:02d}:{m2:02d}", "days": sorted(set(days))}


def minus(windows: list, cuts: list) -> list:
    """`windows` [(a, b)] with the `cuts` [(a, b)] taken out."""
    out = list(windows)
    for ca, cb in cuts:
        nxt = []
        for a, b in out:
            if cb <= a or ca >= b:
                nxt.append((a, b))
                continue
            if a < ca:
                nxt.append((a, ca))
            if cb < b:
                nxt.append((cb, b))
        out = nxt
    return out


def without_fallbacks(entries: list) -> list:
    """The entries but the unplugs kept only for when the schedule is off ("only_off")."""
    return [e for e in entries if not e.get("only_off")]


def validate_entry(raw: dict, today: Optional[datetime.date] = None) -> dict:
    """A schedule entry: {id, time "HH:MM", days [0=Mon..6=Sun], action, enabled},
    or a one-off: the same with "date": "YYYY-MM-DD" (days is then that day). With
    `today` (a change being saved), a one-off can't be more than MAX_ONE_OFF_DAYS ahead."""
    if not isinstance(raw, dict):
        raise ValueError("Each entry must be an object")
    h, m = _parse_hhmm(raw.get("time"))
    action = raw.get("action")
    if action not in ACTIONS:
        raise ValueError(f"Action must be one of {ACTIONS}, got {action!r}")
    if raw.get("date"):
        date = _parse_date(raw["date"])
        if today is not None and date > today + datetime.timedelta(days=MAX_ONE_OFF_DAYS):
            raise ValueError(f"A one-off slot can be at most {MAX_ONE_OFF_DAYS} days ahead")
        out = {
            "id": str(raw.get("id") or uuid.uuid4().hex[:8]),
            "time": f"{h:02d}:{m:02d}",
            "days": [date.weekday()],
            "date": date.isoformat(),
            "action": action,
            "enabled": _flag(raw.get("enabled"), "enabled", True),
        }
        if raw.get("source") in AUTO_SOURCES:
            out["source"] = raw["source"]
            if raw.get("only_off"):
                # A charge's own unplug where it joined a scheduled slot (the slot's unplug ends
                # it): it only runs while the schedule is off, so turning the schedule off
                # doesn't leave the car plugged in after the charge
                out["only_off"] = True
        trims = raw.get("trims")
        if isinstance(trims, dict):  # what this charge changed in the schedule (put back if it's deleted)
            out["trims"] = {
                "skips": [{"entry_id": str(k["entry_id"]), "date": str(k["date"])} for k in trims.get("skips") or []
                          if isinstance(k, dict) and k.get("entry_id") and k.get("date")],
                "added": [str(i) for i in trims.get("added") or [] if isinstance(i, (str, int))],
            }
        return out
    days = raw.get("days", list(range(7)))
    if not isinstance(days, list) or not days or any(
        not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days
    ):
        raise ValueError("Days must be a non-empty list of 0 (Mon) to 6 (Sun)")
    return {
        "id": str(raw.get("id") or uuid.uuid4().hex[:8]),
        "time": f"{h:02d}:{m:02d}",
        "days": sorted(set(days)),
        "action": action,
        "enabled": _flag(raw.get("enabled"), "enabled", True),
    }


def plugged_windows(events, open_until=None):
    """(time, is_plug) events in time order -> the stretches plugged in, as
    (start, end). A stretch still open at the last event is left out, or
    with `open_until`, ends then (a plug-in that's never unplugged)."""
    windows = []
    state, since = None, None
    for t, plug in events:
        if plug and state is not True:
            state, since = True, t
        elif not plug:
            if state is True:
                windows.append((since, t))
            state = False
    if open_until is not None and state is True and since < open_until:
        windows.append((since, open_until))
    return windows


def _hours(minutes: int) -> str:
    """360 -> "6", 330 -> "5.5"."""
    return f"{minutes / 60:g}"


def _rolling_hours(reset: Optional[str]) -> Optional[int]:
    """limit_reset "48h": no more than the limit in any 48 hours (rolling)."""
    if reset and reset.endswith("h") and reset[:-1].isdigit():
        return int(reset[:-1])
    return None


def limit_length(reset: Optional[str]) -> datetime.timedelta:
    """How long the daily limit is counted over: N hours for "Nh", else 24."""
    return datetime.timedelta(hours=_rolling_hours(reset) or 24)


def _fixed(reset: Optional[str]) -> Optional[str]:
    """The reset time ("HH:MM") if the limit resets at a set time every day."""
    return reset if reset and _rolling_hours(reset) is None else None


def limit_period_text(reset: Optional[str]) -> str:
    """"a day" / "in any 48 hours", for messages."""
    hours = _rolling_hours(reset)
    return f"in any {hours} hours" if hours and hours != 24 else "a day"


def window_minutes(windows, start, end=datetime.timedelta(days=1)) -> int:
    """Plugged-in minutes (real ones: the night the clocks go back has an
    extra hour) from `start` to `end` (or for `end`, a timedelta: 24 hours)."""
    if isinstance(end, datetime.timedelta):
        end = start + end
    return int(round(sum(max(0.0, (min(y, end) - max(x, start)).total_seconds()) for x, y in windows) / 60))


def _system_localize(naive: datetime.datetime) -> datetime.datetime:
    return naive.astimezone()


def period_start(t: datetime.datetime, reset: str, localize=_system_localize) -> datetime.datetime:
    """The start of the limit day `t` is in, for a limit that resets every day
    at `reset` ("HH:MM", e.g. Octopus: 12:00), by the clock."""
    h, m = _parse_hhmm(reset)
    wall = t.replace(tzinfo=None)  # local clock time
    start = wall.replace(hour=h, minute=m, second=0, microsecond=0)
    if start > wall:
        start -= datetime.timedelta(days=1)
    start = localize(start)
    if start > t:  # the clocks changed in between
        start = localize(start.replace(tzinfo=None) - datetime.timedelta(days=1))
    return start


def limit_end(start: datetime.datetime, reset: Optional[str], localize=_system_localize) -> datetime.datetime:
    """When the limit day (or rolling period) from `start` ends: the reset
    time the next day by the clock (23 or 25 hours when the clocks change),
    or 24 (N) hours on."""
    if _fixed(reset):
        return localize(start.replace(tzinfo=None) + datetime.timedelta(days=1))
    return start + limit_length(reset)


def limit_starts(windows, after, until, reset: Optional[str] = None, localize=_system_localize) -> list:
    """Where the 24 hours a daily limit is checked over start. reset None
    (rolling): any 24 hours, i.e. from each stretch's start between `after`
    and `until`; "Nh": the same over N hours (looking back N hours from a
    day after `after`, i.e. from now).
    reset "HH:MM": the limit resets then every day, so each limit day a
    stretch is in, from after `after` (i.e. not over yet when `after` is a
    day ago) to `until`."""
    if not _fixed(reset):
        if reset:
            after += datetime.timedelta(days=1) - limit_length(reset)
        # a stretch already going at `after` (e.g. one that's never unplugged) counts from then
        return sorted({max(a, after) for a, b in windows if b > after and a <= until})
    out = set()
    for a, b in windows:
        for t in (a, b - datetime.timedelta(microseconds=1)):
            s = period_start(t, reset, localize)
            if after < s <= until:
                out.add(s)
    return sorted(out)


def longest_day(windows, after, until, reset: Optional[str] = None, localize=_system_localize):
    """The most plugged-in minutes in any 24 hours a daily limit is checked
    over (see limit_starts): (minutes, start), or None."""
    worst = None
    for a in limit_starts(windows, after, until, reset, localize):
        total = window_minutes(windows, a, limit_end(a, reset, localize))
        if worst is None or total > worst[0]:
            worst = (total, a)
    return worst


def limit_day_text(start: datetime.datetime, reset: Optional[str]) -> str:
    """"the 24 hours from Sat 18:00" / "the day from Sat 12:00 (the limit resets at 12:00)"."""
    if not _fixed(reset):
        return f"the {_rolling_hours(reset) or 24} hours from {start.strftime('%a %H:%M')}"
    return f"the day from {start.strftime('%a %H:%M')} (the limit resets at {reset})"


def _local_now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def timezone_name() -> str:
    return os.environ.get("TZ") or _local_now().tzname() or "UTC"


class Automation:
    def __init__(
        self,
        data_dir: Optional[str],
        options: ReplugOptions = ReplugOptions(),
        now: Callable[[], datetime.datetime] = _local_now,
        clock: Callable[[], float] = time.time,
        localize: Callable[[datetime.datetime], datetime.datetime] = lambda naive: naive.astimezone(),
    ) -> None:
        self._localize = localize  # naive local time -> aware (local DST rules)
        self.slot_lead: Callable[[], int] = lambda: SLOT_LEAD_MIN  # (set from the Settings tab by __main__)
        self._path = os.path.join(data_dir, "automation.json") if data_dir else None
        # When the schedule was last checked and any unplug waiting for the supplier: kept across
        # a restart, so an unplug due while the add-on was stopped (or waiting) still happens
        self._runtime_path = os.path.join(data_dir, "automation_runtime.json") if data_dir else None
        self._runtime_saved: Optional[tuple] = None
        self._now = now
        self._clock = clock
        self.schedule_enabled = False
        self.turned_on = False  # the schedule was just turned on (automation_loop takes it)
        self.turned_off = False  # ... or off, while it had the car plugged in
        self.entries: list[dict] = []
        self.skips: list[dict] = []  # [{"entry_id", "date"}]: times skipped once
        self.ready_time = False  # set the supplier's ready time at scheduled plug-ins
        self.ready_status: Optional[dict] = None  # the last time it was set (or why not)
        self.ready_retry: Optional[dict] = None  # setting it failed: {"tries", "at" (clock), "unplug"}
        self.auto_plug_last: Optional[dict] = None  # the last auto plug-in charge planned
        # Populate schedule › Update automatically: the last time (src/__main__.py populate_loop)
        # {"date", "at", "ok", "text", "key" (the settings it was for)}
        self.populate_last: Optional[dict] = None
        self.unplug_wait_s = GRACEFUL_UNPLUG_S
        self.start_delay = dict(DEFAULT_START_DELAY)  # a random wait before your slots' plug-ins
        self._random = random.uniform
        self.blocks: list[dict] = []  # off-peak times: nothing is scheduled in them
        # Saving sessions from the calendar: [{"id", "start", "end" (ISO), "summary",
        # "changes": {"skips", "added"}, "precharged", "notes"}]; with saving_block
        # on they're off-peak times too (see sync_saving_sessions)
        self.saving_sessions: list[dict] = []
        # The automatic schedule (src/auto_schedule.py; Automations tab): what it planned and changed
        self.auto_plan: Optional[dict] = None
        self.auto_preview: Optional[dict] = None  # (while it's off: what it would do, for the page)
        self.estimate_ahead = True  # (the export rates not out yet estimated from the day before's: see estimate())
        self.export_rates: list = []  # the export rates known, [[start, end, p/kWh]] (for the page, on or off)
        self._rates_seen: list = []  # (the export rates seen, 2 days, for the high ones; saved)
        self._rates_seen_was: list = []  # (as last saved)
        self.high_rates: list = []  # each day's highest export half hours asked for, [[start, end, p/kWh]] (marked only)
        self._preview_key = None
        self.saving_past: list[dict] = []  # set from the calendar (src/__main__.py), for the page
        self.saving_block = False
        self._saving_args = None  # (what the saving sessions were last put in with)
        self.override_guards = False  # Diagnostics › Debug: no daily limit (Octopus: 6 hours)
        # A charge (auto plug-in, Charge now...) that takes a day over the daily limit takes the time
        # from the later slots: "start" (their first half hours) or "end" (their last)
        self.trim_from = "start"
        # When the daily limit resets: None = any 24 hours (rolling), "Nh" =
        # any N hours, "HH:MM" = every day then (Octopus: 12:00). From the
        # Settings tab, not saved here.
        self.limit_reset: Optional[str] = None
        self.plan_check: Optional[dict] = None  # the supplier's plan vs the schedule, while plugged in
        self._plan_problems: tuple = ()
        self.replug = options.as_dict()
        # Runtime (not saved)
        self.attempts_used = 0
        self.waiting_since: Optional[float] = None  # plugged in, no session, since
        self.replugging = False
        self.gave_up = False
        self.scheduled = False  # waiting, but your supplier has a slot planned
        self.slot_now = False  # waiting, though your supplier's slot is running
        self.last_replug: Optional[float] = None
        self.last_run: Optional[dict] = None  # last schedule entry run
        self.unplug_at: Optional[float] = None  # a scheduled unplug waiting for the supplier, until
        self.plug_at: Optional[float] = None  # a scheduled plug-in waiting out its random delay, until
        self._last_check: Optional[datetime.datetime] = None
        self._block_check: Optional[datetime.datetime] = None  # off-peak time starts looked at up to
        self._first_check = True
        self._load()
        self._load_runtime()

    # --- persistence ---------------------------------------------------------

    def _load(self) -> None:
        data: dict = {}
        if not jsonfile.load(self._path, "Automation settings", data.update):
            return
        schedule = data.get("schedule") or {}
        self.schedule_enabled = bool(schedule.get("enabled", False))
        self.ready_time = bool(schedule.get("ready_time", False))
        self.override_guards = bool(schedule.get("override_guards", False))
        blocks = []
        for raw in schedule.get("blocks") or []:
            try:
                blocks.append(validate_block(raw))
            except ValueError:
                logger.warning("Ignoring invalid off-peak time %r", raw)
        self.blocks = blocks
        try:
            self.unplug_wait_s = self._valid_wait(schedule.get("unplug_wait_s", GRACEFUL_UNPLUG_S))
        except ValueError:
            pass
        try:
            self.start_delay = self._valid_delay({**DEFAULT_START_DELAY, **(schedule.get("start_delay") or {})})
        except (ValueError, TypeError):
            pass
        entries = []
        for raw in schedule.get("entries") or []:
            try:
                entries.append(validate_entry(raw))
            except ValueError:
                logger.warning("Ignoring invalid schedule entry %r", raw)
        self.entries = entries
        self.skips = [k for k in schedule.get("skips") or []
                      if isinstance(k, dict) and k.get("entry_id") and k.get("date")]
        saving = data.get("saving") or {}
        self.saving_block = bool(saving.get("block", False))
        self.saving_sessions = [r for r in saving.get("sessions") or []
                                if isinstance(r, dict) and _parse_iso(r.get("start")) and _parse_iso(r.get("end"))]
        for r in self.saving_sessions:
            r.setdefault("changes", {"skips": [], "added": []})
            r.setdefault("notes", [])
        last = data.get("populate_last")
        self.populate_last = last if isinstance(last, dict) else None
        auto = data.get("auto")
        if isinstance(auto, dict) and isinstance(auto.get("changes"), dict):
            self.auto_plan = auto
        self._rates_seen = [(a, b, p) for a, b, p in ((_parse_iso(x[0]), _parse_iso(x[1]), x[2]) for x in data.get("rates_seen") or []
                                                       if isinstance(x, list) and len(x) == 3) if a and b and isinstance(p, (int, float))]
        self._rates_seen_was = self._rates_seen_saved()
        # Saved re-plug settings (set on the web page) win over the defaults
        replug = data.get("replug")
        if isinstance(replug, dict):
            try:
                self.replug = self._valid_replug({**self.replug, **replug})
            except ValueError:
                pass

    def _load_runtime(self) -> None:
        self._missed_since: Optional[float] = None  # the last check before a restart

        def use(data: dict) -> None:
            last = data.get("last_check")
            if isinstance(last, (int, float)) and 0 <= self._clock() - last <= MISSED_MAX_S:
                self._missed_since = float(last)
            if isinstance(data.get("unplug_at"), (int, float)):
                self.unplug_at = float(data["unplug_at"])
            if isinstance(data.get("plug_at"), (int, float)):
                self.plug_at = float(data["plug_at"])
        jsonfile.load(self._runtime_path, "The schedule's last check", use, backup=False)

    def save_runtime(self) -> None:
        """Save the last check (to the minute) and any waiting unplug, when they change."""
        if not self._runtime_path or self._last_check is None:
            return
        state = (int(self._last_check.timestamp() // 60), self.unplug_at, self.plug_at)
        if state == self._runtime_saved:
            return
        if jsonfile.save(self._runtime_path, {"last_check": self._last_check.timestamp(), "unplug_at": self.unplug_at,
                                              "plug_at": self.plug_at}, "the schedule's last check", backup=False, quiet=True):
            self._runtime_saved = state

    def _save(self) -> None:
        jsonfile.save(self._path, {
            "schedule": {"enabled": self.schedule_enabled, "entries": self.entries,
                         "ready_time": self.ready_time, "skips": self.skips,
                         "unplug_wait_s": self.unplug_wait_s, "start_delay": self.start_delay,
                         "override_guards": self.override_guards, "blocks": self.blocks},
            "replug": self.replug,
            "saving": {"block": self.saving_block, "sessions": self.saving_sessions},
            "auto": self.auto_plan,
            "rates_seen": self._rates_seen_saved(),
            "populate_last": self.populate_last,
        }, "automation settings", indent=1)
        self._rates_seen_was = self._rates_seen_saved()

    def _rates_seen_saved(self) -> list:
        return [[_iso_local(a), _iso_local(b), p] for a, b, p in self._rates_seen]

    # --- settings ------------------------------------------------------------

    def set_schedule(self, enabled: Optional[bool] = None, entries: Optional[list] = None,
                     ready_time: Optional[bool] = None, ready_times: Optional[list] = None,
                     provider: str = "your supplier", daily_cap_min: Optional[int] = None,
                     unplug_wait_s=None, override_guards: Optional[bool] = None,
                     blocks: Optional[list] = None, allow_over: bool = False,
                     start_delay: Optional[dict] = None, add_skips: Optional[list] = None) -> None:
        """override_guards: with it on, daily_cap_min isn't applied (turning it
        back off checks the schedule, as turning on the ready time does).
        ready_times: the times your supplier accepts as a ready time (None:
        unknown). While the schedule sets it, unplug times must be among them,
        and with daily_cap_min (Octopus: 360) it can't plug in for longer than
        that in any 24 hours. Checked when times are saved or the ready time
        is turned on, so the schedule can always be switched off.
        allow_over: save it anyway, over the daily limit (the page asks first,
        and shows the hours over in red); the unplug times are still checked.
        add_skips: times to skip along with the change ([{entry_id, date}], as set_skips),
        e.g. a new weekly slot where it would overlap a one-off: checked with them."""
        enabled = None if enabled is None else _flag(enabled, "enabled", False)
        ready_time = None if ready_time is None else _flag(ready_time, "ready_time", False)
        override_guards = None if override_guards is None else _flag(override_guards, "override_guards", False)
        # (put back as it was if turning the schedule on is refused, below)
        undo = (copy.deepcopy(self.entries), list(self.skips), copy.deepcopy(self.blocks))
        wait = None if unplug_wait_s is None else self._valid_wait(unplug_wait_s)
        if start_delay is not None:
            if not isinstance(start_delay, dict):
                raise ValueError("start_delay must be {enabled, max_min}")
            delay = self._valid_delay({**self.start_delay, **start_delay})
        new_entries = self.entries
        if entries is not None:
            if not isinstance(entries, list):
                raise ValueError("Entries must be a list")
            today = self._now().date()
            new_entries = [validate_entry(e, today=today) for e in entries]
            # Only your own count towards the limit: not the charges the add-on adds (auto
            # plug-in, Charge now...) nor one-offs that have run (kept 14 days, for the
            # Sessions tab), which would otherwise stop any slot being changed
            yours = [e for e in new_entries if not e.get("source") and not (e.get("date") and e["date"] < today.isoformat())]
            if len(yours) > MAX_YOUR_ENTRIES:
                raise ValueError(f"At most {MAX_YOUR_ENTRIES} schedule times of your own "
                                 f"({MAX_YOUR_ENTRIES // 2} slots): delete some first")
            if len(new_entries) > MAX_ENTRIES:
                raise ValueError(f"At most {MAX_ENTRIES} schedule times")
        new_skips = list(self.skips)
        pre_gone = []  # saving sessions whose charge before it was deleted
        if entries is not None:
            # A charge (auto plug-in, Charge now...) that's deleted puts back the
            # slot it trimmed or joined: its replacement plug-in goes, the skips go
            kept = {e["id"] for e in new_entries}
            for gone in (e for e in self.entries if e.get("trims") and e["id"] not in kept):
                added = set(gone["trims"].get("added") or [])
                new_entries = [e for e in new_entries if e["id"] not in added]
                others = self._skip_owners(gone["trims"])
                new_skips = [k for k in new_skips if k not in (gone["trims"].get("skips") or [])
                             or (k["entry_id"], k["date"]) in others]
                logger.info("Schedule: a charge was deleted, putting back what it changed")
            # The charge before a saving session deleted on the page: what it changed is put back
            # (the slot it joined or trimmed, its unplug at the session) and it isn't added again
            removed = {e["id"] for e in self.entries} - kept
            for r in self.saving_sessions:
                pre = r.get("pre") or {}
                ch = pre.get("changes") or {}
                if pre.get("deleted") or not removed & set(ch.get("added") or []):
                    continue
                added = set(ch.get("added") or [])
                new_entries = [e for e in new_entries if e["id"] not in added]
                others = self._skip_owners(ch)
                freed = [k for k in ch.get("skips") or [] if (k["entry_id"], k["date"]) not in others]
                new_skips = [k for k in new_skips if k not in freed]
                # (an unplug it skipped to join the slot before it: if that slot went with it, the
                # unplug would be left on its own, so it goes too, or stays skipped if it's weekly)
                ends = {b for _, b in self.windows(without_fallbacks(new_entries), self._now(), new_skips)}
                for k in freed:
                    e = next((x for x in new_entries if x["id"] == k["entry_id"]), None)
                    if e is None or e["action"] != "unplug":
                        continue
                    at = next(self._occurrences(e, _parse_date(k["date"]), 1), None)
                    if at is not None and at not in ends:
                        if e.get("date"):
                            new_entries = [x for x in new_entries if x["id"] != e["id"]]
                        else:
                            new_skips.append(k)
                pre_gone.append(r)
            # A slot moved for a saving session, deleted (or part of it): an unplug it added that
            # no longer ends anything goes too (it'd be left on its own)
            for r in self.saving_sessions:
                added = set((r.get("changes") or {}).get("added") or [])
                if not removed & added:
                    continue
                ends = {b for _, b in self.windows(without_fallbacks(new_entries), self._now(), new_skips)}
                for e in [e for e in new_entries if e["id"] in added and e["action"] == "unplug" and e.get("date")]:
                    at = next(self._occurrences(e, _parse_date(e["date"]), 1), None)
                    if at is not None and at not in ends:
                        new_entries = [x for x in new_entries if x["id"] != e["id"]]
        for t in add_skips or []:
            if not isinstance(t, dict):
                raise ValueError("Each time to skip must be {entry_id, date}")
            entry = next((e for e in new_entries if e["id"] == str(t.get("entry_id") or "")), None)
            if entry is None:
                raise ValueError("No such schedule time to skip")
            day = _parse_date(t.get("date"))
            if not any(True for _ in self._occurrences(entry, day, 1)):
                raise ValueError(f"That time doesn't run on {t.get('date')}")
            key = {"entry_id": entry["id"], "date": day.isoformat()}
            if key not in new_skips:
                new_skips.append(key)
        # (the checks below count the entries as they run with the schedule on, also while it's
        # off, as car slots are planned: not a joined charge's own unplug, kept for when it's off)
        run_new = without_fallbacks
        guards_off = self.override_guards if override_guards is None else bool(override_guards)
        if guards_off:
            daily_cap_min = None
        if daily_cap_min and (self.ready_time if ready_time is None else bool(ready_time)):
            # A slot across the limit's reset time (Octopus: 12:00) that's longer
            # than the limit is split there: two charges, each with its own ready time
            splits = self._reset_splits(run_new(new_entries), new_skips, daily_cap_min)
            if splits:
                new_entries = new_entries + splits
                logger.info("Schedule: split %s at %s, where %s's daily limit resets",
                            "a slot" if len(splits) == 2 else "slots", ", ".join(sorted({e["time"] for e in splits})),
                            provider)
        checking = (entries is not None or bool(ready_time)
                    or (override_guards is not None and not override_guards and self.override_guards))
        if checking and (self.ready_time if ready_time is None else bool(ready_time)):
            if ready_times:
                check_unplug_times([e for e in new_entries if not self._past_one_off(e)], ready_times, provider)
            if daily_cap_min and not allow_over:
                # Any 24 hours from the previous day on. Only saving times: one
                # that's already over (e.g. after a Charge now) can still be
                # changed, as long as it doesn't get worse.
                only_times = entries is not None and not ready_time and override_guards is None
                if not only_times or self._makes_worse(daily_cap_min, (run_new(self.entries), self.skips), (run_new(new_entries), new_skips)):
                    self.check_daily_cap(run_new(new_entries), daily_cap_min, provider, new_skips)
        new_blocks = self.blocks
        if blocks is not None:
            if not isinstance(blocks, list) or len(blocks) > 20:
                raise ValueError("Off-peak times must be a list (at most 20)")
            new_blocks = [validate_block(b) for b in blocks]
        if entries is not None or blocks is not None:
            self.check_blocks(run_new(new_entries), new_blocks, new_skips)
        self.blocks = new_blocks
        self.entries = new_entries
        for r in pre_gone:
            r["pre"] = {"hours": (r.get("pre") or {}).get("hours"), "changes": {"skips": [], "added": []}, "deleted": True}
            r["notes"] = [n for n in r.get("notes") or [] if not n.startswith("charge before")] + ["charge before it: deleted"]
            logger.info("Saving session %s: the charge before it was deleted, putting back what it changed",
                        _parse_iso(r["start"]).strftime("%a %H:%M"))
        ids = {e["id"] for e in self.entries}
        self.skips = [k for k in new_skips if k["entry_id"] in ids]
        self._prune()
        if enabled is not None:
            turned_on = bool(enabled) and not self.schedule_enabled
            # Turned off while the schedule has it plugged in: the loop unplugs, unless a car slot
            # (a charge) is going now; that carries on to its own end (see automation_loop)
            if not bool(enabled) and self.schedule_enabled and self.in_plug_window() is not None:
                self.turned_off = True
            self.schedule_enabled = bool(enabled)
            if turned_on:
                before = (copy.deepcopy(self.entries), list(self.skips))
                joins = self._join_charges()
                # A charge planned while the schedule was off only counted the other charges:
                # joined with the schedule's slots, a day can go over the supplier's limit. Refused
                # (as saving the schedule is) if the charges make it worse than the slots alone,
                # unless asked to go ahead anyway (the hours over then show in red)
                cap = daily_cap_min if (self.ready_time if ready_time is None else bool(ready_time)) else None  # (None with the guards overridden)
                if joins and cap and not allow_over:
                    slots_alone = [e for e in without_fallbacks(before[0]) if e.get("source") not in AUTO_SOURCES]
                    running = without_fallbacks(self.entries)
                    if self._makes_worse(cap, (slots_alone, before[1]), (running, self.skips)):
                        try:
                            self.check_daily_cap(running, cap, provider, self.skips)
                        except ValueError:
                            self.entries, self.skips, self.blocks = undo
                            self.schedule_enabled = False
                            raise
                self.turned_on = True  # the loop plugs in if it's inside a slot now (see automation_loop)
        if ready_time is not None:
            self.ready_time = bool(ready_time)
        if wait is not None:
            self.unplug_wait_s = wait
        if start_delay is not None and delay != self.start_delay:
            self.start_delay = delay
            # (turned off, a plug-in waiting now goes ahead at the next look: see automation_loop)
            logger.info("Random start delay %s", f"on, up to {delay['max_min']} min" if delay["enabled"] else "off")
        if allow_over and checking and daily_cap_min:
            worst = self._longest_day(new_entries, new_skips)
            if worst and worst[0] > daily_cap_min:
                logger.info("Schedule: saved over %s's daily limit (%dh %02dm in %s), as asked",
                            provider, worst[0] // 60, worst[0] % 60, limit_day_text(worst[1], self.limit_reset))
        if override_guards is not None and bool(override_guards) != self.override_guards:
            self.override_guards = bool(override_guards)
            logger.info("Scheduling guards %s", "overridden: no daily limit"
                        if self.override_guards else "back on")
        self._save()
        logger.info(
            "Schedule %s, %d entr%s", "on" if self.schedule_enabled else "off",
            len(self.entries), "y" if len(self.entries) == 1 else "ies",
        )

    @staticmethod
    def _valid_wait(value) -> int:
        try:
            wait = int(value)
        except (TypeError, ValueError):
            raise ValueError("The unplug wait must be a whole number of seconds") from None
        if not 0 <= wait <= MAX_UNPLUG_WAIT_S:
            raise ValueError(f"The unplug wait must be 0 to {MAX_UNPLUG_WAIT_S} seconds")
        return wait

    @staticmethod
    def _valid_delay(values: dict) -> dict:
        try:
            max_min = int(values["max_min"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("The start delay must be a whole number of minutes") from None
        if not 1 <= max_min <= MAX_START_DELAY_MIN:
            raise ValueError(f"The start delay must be 1 to {MAX_START_DELAY_MIN} minutes")
        return {"enabled": _flag(values.get("enabled"), "The start delay's enabled", False), "max_min": max_min}

    def start_delay_s(self, entry: dict, at: datetime.datetime) -> float:
        """The random wait before a plug-in at `at` (seconds; 0: none): up to the
        minutes set, and at most half the slot. Only your own slots' plug-ins."""
        if not self.start_delay["enabled"] or entry.get("source") in AUTO_SOURCES:
            return 0.0
        most = self.start_delay["max_min"] * 60
        unplug = self.next_unplug(at)
        if unplug is not None:
            most = min(most, (unplug - at).total_seconds() / 2)
        return max(0.0, self._random(0, most)) if most > 0 else 0.0

    @staticmethod
    def _valid_replug(values: dict) -> dict:
        try:
            after = int(values["after_min"])
            attempts = int(values["attempts"])
            off = int(values.get("off_s", REPLUG_WAIT_S))
        except (KeyError, TypeError, ValueError):
            raise ValueError("after_min, attempts and off_s must be whole numbers") from None
        if not 1 <= after <= 240:
            raise ValueError("after_min must be 1 to 240 minutes")
        if not 0 <= attempts <= 20:
            raise ValueError("attempts must be 0 to 20")
        if not 5 <= off <= 300:
            raise ValueError("off_s must be 5 to 300 seconds")
        return {"enabled": _flag(values.get("enabled"), "Re-plug's enabled", True), "after_min": after, "attempts": attempts, "off_s": off}

    def set_replug(self, **changes) -> None:
        allowed = {k: v for k, v in changes.items() if k in ("enabled", "after_min", "attempts", "off_s") and v is not None}
        self.replug = self._valid_replug({**self.replug, **allowed})
        if not self.replug["enabled"]:
            self.waiting_since = None
        self.gave_up = self.gave_up and self.attempts_used >= self.replug["attempts"]
        self._save()
        logger.info(
            "Auto re-plug %s (after %d min, up to %d tries)",
            "on" if self.replug["enabled"] else "off",
            self.replug["after_min"], self.replug["attempts"],
        )

    # --- schedule ------------------------------------------------------------

    def _local(self, naive: datetime.datetime) -> datetime.datetime:
        """A local wall time as an aware time. One that doesn't exist (the hour the clocks
        go forward, e.g. 01:30 in the UK in spring) is the first time after it that does
        (02:00), rather than an hour on (02:30)."""
        def real(n):  # (an aware time for a wall time that exists, else None)
            aware = self._localize(n)
            back = aware.astimezone(datetime.timezone.utc).astimezone(aware.tzinfo)  # (normalised)
            return aware if back.replace(tzinfo=None) == n else None

        found = real(naive)
        if found is not None:
            return found
        for minutes in range(1, 181):
            found = real(naive + datetime.timedelta(minutes=minutes))
            if found is not None:
                return found
        return self._localize(naive)

    def _occurrences(self, entry: dict, start: datetime.date, days: int):
        h, m = _parse_hhmm(entry["time"])
        once = entry.get("date")
        for i in range(days):
            day = start + datetime.timedelta(days=i)
            if (day.isoformat() == once) if once else (day.weekday() in entry["days"]):
                yield self._local(datetime.datetime(day.year, day.month, day.day, h, m))

    def timeline(self, start: datetime.date, days: int, entries: Optional[list] = None,
                 skips: Optional[list] = None) -> list:
        """Every enabled time from `start` for `days` days: (when, entry, skipped), in order."""
        skips = self.skips if skips is None else skips
        out = []
        for entry in self._active_entries() if entries is None else entries:
            if not entry["enabled"]:
                continue
            h, m = _parse_hhmm(entry["time"])
            for occ in self._occurrences(entry, start, days):
                day = occ.date() if entry.get("date") is None else _parse_date(entry["date"])
                wall = datetime.datetime(day.year, day.month, day.day, h, m)
                out.append((occ, entry, {"entry_id": entry["id"], "date": day.isoformat()} in skips, wall))
        # an unplug at the same minute as a plug comes before it: back-to-back
        # slots (06:00-12:00 and 12:00-18:00) unplug and plug straight back in,
        # so the supplier sees two charges, each with its own ready time. But where
        # the clocks going forward put a plug-in and its unplug at the same time
        # (01:00-02:00 in the UK), the slot is empty: neither runs.
        out.sort(key=lambda x: (x[0], x[1]["action"] == "plug"))
        drop = set()
        for i, (occ, e, _, wall) in enumerate(out):
            if e["action"] != "plug":
                continue
            for j, (occ2, e2, _, wall2) in enumerate(out):
                if occ2 == occ and e2["action"] == "unplug" and wall2 > wall and j not in drop:
                    drop.update((i, j))
                    break
        return [(occ, e, skipped) for i, (occ, e, skipped, _) in enumerate(out) if i not in drop]

    def _active_entries(self) -> list:
        """The entries that run: all of them with the schedule on (but a joined charge's
        own unplug, kept for when it's off), else only those an auto plug-in charge added."""
        if self.schedule_enabled:
            return without_fallbacks(self.entries)
        return [e for e in self.entries if e.get("source") in AUTO_SOURCES]

    def _horizon_days(self, entries: Optional[list] = None) -> int:
        """Days on from today the schedule is looked at: 8, or to the last one-off."""
        today = self._now().date()
        last = max((_parse_date(e["date"]) for e in (self._active_entries() if entries is None else entries)
                    if e.get("date")), default=today)
        return max(8, (last - today).days + 2)

    def windows(self, entries: Optional[list] = None, now: Optional[datetime.datetime] = None,
                skips: Optional[list] = None, open_ended: bool = False) -> list:
        """The stretches the schedule has the car plugged in, a week back to 8
        days on (or to the last one-off). open_ended: a plug-in with no unplug
        after it counts as plugged in to the end (for the daily limit)."""
        now = now or self._now()
        days = self._horizon_days(entries)
        tl = self.timeline(now.date() - datetime.timedelta(days=7), 8 + days, entries, skips)
        end = self._localize(datetime.datetime.combine(now.date() + datetime.timedelta(days=days), datetime.time()))
        return plugged_windows([(t, e["action"] == "plug") for t, e, skipped in tl if not skipped],
                               end if open_ended else None)

    def session_windows(self) -> list:
        """The saving sessions that are off-peak times (with saving_block on): [(a, b)]."""
        if not self.saving_block:
            return []
        return [(_parse_iso(r["start"]), _parse_iso(r["end"])) for r in self.saving_sessions]

    def block_windows(self, start: datetime.date, days: int, blocks: Optional[list] = None,
                      sessions: bool = True) -> list:
        """The off-peak times from `start` for `days` days: [(a, b)], with
        the saving sessions (unless `sessions` is False)."""
        out = []
        if sessions:
            lo = self._localize(datetime.datetime.combine(start - datetime.timedelta(days=1), datetime.time()))
            hi = self._localize(datetime.datetime.combine(start + datetime.timedelta(days=days), datetime.time()))
            out += [(a, b) for a, b in self.session_windows() + self.auto_offpeak() if b > lo and a < hi]
        for b in self.blocks if blocks is None else blocks:
            h1, m1 = _parse_hhmm(b["start"])
            h2, m2 = _parse_hhmm(b["end"])
            for i in range(-1, days):
                d = start + datetime.timedelta(days=i)
                if d.weekday() not in b["days"]:
                    continue
                a = self._localize(datetime.datetime(d.year, d.month, d.day, h1, m1))
                e = d + datetime.timedelta(days=1) if (h2, m2) <= (h1, m1) else d
                out.append((a, self._localize(datetime.datetime(e.year, e.month, e.day, h2, m2))))
        return sorted(out)

    def check_blocks(self, entries: list, blocks: list, skips: Optional[list] = None) -> None:
        """Nothing scheduled may plug in during an off-peak time (over the
        coming week; auto plug-in and Charge now are planned around them)."""
        now = self._now()
        # (not the saving sessions: a slot that runs into one is moved, see sync_saving_sessions)
        cuts = self.block_windows(now.date(), self._horizon_days(entries) + 1, blocks, sessions=False)
        for a, b in self.windows(entries, now, skips):
            if b <= now:
                continue
            hit = next(((ca, cb) for ca, cb in cuts if ca < b and cb > a), None)
            if hit:
                raise ValueError(
                    f"The {a.strftime('%a %H:%M')}–{b.strftime('%H:%M')} slot overlaps the off-peak time "
                    f"{hit[0].strftime('%H:%M')}–{hit[1].strftime('%H:%M')}: change one of them")

    def _longest_day(self, entries: list, skips: Optional[list] = None):
        now = self._now()
        return longest_day(self.windows(entries, now, skips, open_ended=True), now - datetime.timedelta(days=1),
                           now + datetime.timedelta(days=self._horizon_days(entries) - 1), self.limit_reset,
                           self._localize)

    def _as_if_on(self, skips: Optional[list] = None) -> tuple[list, list]:
        """The plugged-in windows as the schedule runs with it on, also while it's off, when car
        slots stay separate from your slots: theirs and yours, overlapping ones as one (as they'd
        join), and the stretches the same way. For the daily limit of a car slot planned while off."""
        now, skips = self._now(), self.skips if skips is None else skips
        running = without_fallbacks(self.entries)
        start, days = now.date() - datetime.timedelta(days=1), self._horizon_days(running) + 2
        if self.schedule_enabled:
            return (self.windows(running, now, skips, open_ended=True),
                    self._stretches(start, days, running, skips))
        cars = [e for e in running if e.get("source") in AUTO_SOURCES]
        mine = [e for e in running if e.get("source") not in AUTO_SOURCES]
        merged = []
        for a, b in sorted(self.windows(cars, now, skips, open_ended=True) + self.windows(mine, now, skips, open_ended=True)):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        stretches = sorted(self._stretches(start, days, cars, skips) + self._stretches(start, days, mine, skips),
                           key=lambda x: x["start"])
        return merged, stretches

    def _reset_splits(self, entries: list, skips: list, cap_min: int, one_off: bool = False,
                      until: Optional[datetime.datetime] = None) -> list:
        """Entries that split each coming slot longer than `cap_min` where the
        daily limit resets (it unplugs and plugs straight back in there: see
        due()), so the supplier sees a charge either side. Weekly for weekly
        slots, one-offs otherwise (or with `one_off`). Only with a reset time."""
        fixed, loc = _fixed(self.limit_reset), self._localize
        if not fixed or not cap_min:
            return []
        now = self._now()
        weekly: dict = {}
        out = []
        for s in self._stretches(now.date() - datetime.timedelta(days=1), self._horizon_days(entries) + 1,
                                 entries, skips):
            if s["end"] <= now or (until is not None and s["start"] > until):
                continue
            if (s["end"] - s["start"]).total_seconds() / 60 <= cap_min:
                continue
            r = limit_end(period_start(s["start"], fixed, loc), fixed, loc)
            while r < s["end"]:
                if r > now:
                    src = s["plug"].get("source")
                    if not one_off and not s["plug"].get("date") and not s["unplug"].get("date"):
                        weekly.setdefault(r.strftime("%H:%M"), set()).add(r.weekday())
                    else:
                        for action in ("unplug", "plug"):
                            out.append(validate_entry({"time": r.strftime("%H:%M"), "date": r.date().isoformat(),
                                                       "action": action, **({"source": src} if src else {})}))
                r = limit_end(r, fixed, loc)
        for t, days in sorted(weekly.items()):
            for action in ("unplug", "plug"):
                out.append(validate_entry({"time": t, "days": sorted(days), "action": action}))
        return out

    def _makes_worse(self, cap_min: int, old: tuple, new: tuple) -> bool:
        """(entries, skips) before and after a change: does any 24 hours (or
        limit day) go over the cap, or further over it than it already was?"""
        now, reset = self._now(), self.limit_reset
        before = self.windows(old[0], now, old[1], open_ended=True)
        after = self.windows(new[0], now, new[1], open_ended=True)
        for a in limit_starts(after, now - datetime.timedelta(days=1),
                              now + datetime.timedelta(days=self._horizon_days(new[0]) - 1), reset, self._localize):
            end = limit_end(a, reset, self._localize)
            if window_minutes(after, a, end) > max(cap_min, window_minutes(before, a, end)):
                return True
        return False

    def check_daily_cap(self, entries: list, cap_min: int, provider: str, skips: Optional[list] = None) -> None:
        """Octopus schedules at most 6 hours of smart charging a day: refuse a
        schedule that plugs in for longer in any 24 hours (or limit day, with a
        reset time) over the coming week."""
        worst = self._longest_day(entries, skips)
        if worst and worst[0] > cap_min:
            total, start = worst
            raise ValueError(
                f"{provider[:1].upper() + provider[1:]} schedules at most {_hours(cap_min)} hours of smart charging "
                f"{limit_period_text(self.limit_reset)}, but the schedule "
                f"plugs in for {total // 60}h {total % 60:02d}m in {limit_day_text(start, self.limit_reset)}: shorten it"
            )

    def _prune(self) -> bool:
        """Forget one-off times that have run and skips more than a day old."""
        now = self._now()
        keep = []
        for e in self.entries:
            if e.get("date"):
                h, m = _parse_hhmm(e["time"])
                d = _parse_date(e["date"])
                when = self._localize(datetime.datetime(d.year, d.month, d.day, h, m))
                if when < now - ONE_OFF_KEEP:  # kept a day, so its stretch stays known
                    continue
            keep.append(e)
        # (a skip is kept for a day after, e.g. last night's plug-in until it's over)
        skips = [k for k in self.skips if k["date"] >= (now.date() - ONE_OFF_KEEP).isoformat()]
        changed = len(keep) != len(self.entries) or len(skips) != len(self.skips)
        self.entries, self.skips = keep, skips
        return changed

    def _past_one_off(self, entry: dict) -> bool:
        """A one-off time that has already run (kept for the Sessions tab)."""
        if not entry.get("date"):
            return False
        h, m = _parse_hhmm(entry["time"])
        d = _parse_date(entry["date"])
        return self._localize(datetime.datetime(d.year, d.month, d.day, h, m)) < self._now()

    def one_off_runs(self) -> list:
        """The plugged-in stretches of the last 14 days that a one-off time
        started or ended, newest first: [{"start", "end", "source"}] ("auto_plug"
        if an auto plug-in charge added it, "saving_move" if it's your slot moved
        for a saving session (while the session is kept), else "one_off")."""
        now = self._now()
        days = ONE_OFF_KEEP.days + 1
        moved = {i for r in self.saving_sessions for i in (r.get("changes") or {}).get("added") or []}
        out = []
        for s in self._stretches(now.date() - datetime.timedelta(days=days - 1), days, self._active_entries(), self.skips):
            ones = [e for e in (s["plug"], s["unplug"]) if e.get("date")]
            if ones and s["start"] <= now:
                source = next((e["source"] for e in ones if e.get("source") in AUTO_SOURCES),
                              "saving_move" if any(e["id"] in moved for e in ones) else "one_off")
                out.append({"start": _iso_local(s["start"]), "end": _iso_local(s["end"]), "source": source})
        return out[::-1]

    def _join_charges(self) -> int:
        """The schedule was just turned on: each charge still to come or going now (auto
        plug-in, Charge now, the car plugged in sensor), planned while it was off, becomes one
        with the scheduled slots it meets or overlaps, as if planned with it on: plugged in
        until whichever ends last. Unplugs in between are skipped this once (the charge's own
        becomes "only_off", for if the schedule's turned off again) and plug-ins in between
        too (or the ready time would be set to an earlier unplug). Where an unplug and a plug-in
        meet on purpose (a split at the daily limit's reset), they're left. Returns how many."""
        now = self._now()
        start_day = now.date() - datetime.timedelta(days=1)
        days = self._horizon_days() + 1
        running = without_fallbacks(self.entries)
        auto = [e for e in running if e.get("source") in AUTO_SOURCES]
        own = [e for e in running if e.get("source") not in AUTO_SOURCES]
        slots = [x for x in self._stretches(start_day, days, own, self.skips) if x["end"] > now]
        joins = 0
        for c in [x for x in self._stretches(start_day, days, auto, self.skips) if x["end"] > now]:
            lo, hi, joined, grew = c["start"], c["end"], [], True
            while grew:
                grew = False
                for x in slots:
                    if x in joined or x["start"] > hi or x["end"] < lo:
                        continue
                    joined.append(x)
                    lo, hi, grew = min(lo, x["start"]), max(hi, x["end"]), True
            if not joined:
                continue
            timeline = self.timeline(start_day, days, running, self.skips)
            # An unplug and a plug-in at the same time from the same kind (both the charge's, or
            # both the schedule's) are a split on purpose (e.g. at the daily limit's reset): kept.
            # A charge ending as a slot starts (or the other way round) just meets it: joined
            kind = lambda e: e.get("source") in AUTO_SOURCES
            meet = {(t, kind(e), e["action"]) for t, e, skipped in timeline if not skipped}
            new_skips = []
            for t, e, skipped in timeline:
                other = "plug" if e["action"] == "unplug" else "unplug"
                if skipped or not lo < t < hi or (t, kind(e), other) in meet:
                    continue
                if e["action"] == "unplug":
                    if e["id"] == c["unplug"]["id"] and e.get("source") in AUTO_SOURCES:
                        e["only_off"] = True  # the charge's own: only when the schedule's off
                    else:
                        new_skips.append({"entry_id": e["id"], "date": t.date().isoformat()})
                elif t > now:
                    new_skips.append({"entry_id": e["id"], "date": t.date().isoformat()})
            # Put back if the charge is deleted (as for one planned with the schedule on)
            trims = c["plug"].setdefault("trims", {"skips": [], "added": []})
            trims["skips"] = trims.get("skips", []) + [k for k in self._claim_skips(new_skips, trims) if k not in trims.get("skips", [])]
            joins += 1
            logger.info("Schedule on: the %s from %s joins the scheduled slot, plugged in until %s",
                        SOURCE_NAMES.get(c["plug"].get("source"), "charge"), c["start"].strftime("%a %H:%M"),
                        hi.strftime("%a %H:%M"))
        return joins

    def same_as_yours(self, weekly: list) -> bool:
        """Your slots are already just these weekly times (the plan hasn't changed)."""
        yours = [e for e in self.entries if not e.get("source")]
        if any(e.get("date") or not e.get("enabled", True) for e in yours):
            return False
        key = lambda es: sorted((e["time"], e["action"], tuple(sorted(e["days"]))) for e in es)  # noqa: E731
        return key(yours) == key(weekly)

    def slot_running(self, now: Optional[datetime.datetime] = None) -> bool:
        """The schedule has the car plugged in now (a slot or a charge is going)."""
        now = now or self._now()
        return any(a <= now < b for a, b in self.windows(None, now))

    def populate_schedule(self, weekly: list, cap_min: Optional[int] = None, provider: str = "your supplier",
                          ready_times: Optional[list] = None) -> dict:
        """Automations › Populate schedule › Add to schedule: your slots (weekly and one-off) are
        replaced with `weekly` (src/populate_schedule.py plan_entries). The charges the add-on
        added (auto plug-in, Charge now, the car plugged in sensor) stay: each still to come or
        going now joins the new slots it meets and takes the time it needs from those after it,
        as when it was planned (from their start or end: trim_from). The automatic schedule plans
        again around them, and saving sessions are put in again. Returns {"added", "removed",
        "notes"}."""
        if not isinstance(weekly, list) or not weekly:
            raise ValueError("The plan has no times to add")
        today = self._now().date().isoformat()
        sessions_out = self._relayer_sessions(apply=False)  # (they're put in again on the new slots)
        self._undo_auto(save=False)  # (planned again at the next look)
        yours = [e for e in self.entries if not e.get("source") and not (e.get("date") and e["date"] < today)]
        gone = {e["id"] for e in yours}
        kept = [e for e in self.entries if e["id"] not in gone]
        for e in kept:  # what the charges changed was on the slots that go
            if e.get("trims"):
                e["trims"] = {"skips": [k for k in e["trims"].get("skips") or [] if k["entry_id"] not in gone],
                              "added": [i for i in e["trims"].get("added") or [] if i not in gone]}
        self.set_schedule(entries=kept + weekly, ready_times=ready_times, provider=provider,
                          daily_cap_min=cap_min, allow_over=True)  # (the charges fit in below)
        notes = []
        if self.schedule_enabled and self._join_charges():
            notes.append("a charge in the schedule joins the new slots it meets")
        notes += self._fit_charges_to_limit(cap_min) if cap_min and self.ready_time and not self.override_guards else []
        if sessions_out and self._saving_args:
            self._apply_sessions(*self._saving_args)
        self._save()
        added = len([e for e in weekly if e["action"] == "plug"])
        logger.info("Populate schedule: %d slot time%s added, %d of yours replaced%s", added, "" if added == 1 else "s",
                    len(gone) // 2, ("; " + "; ".join(notes)) if notes else "")
        return {"added": added, "removed": len(gone), "notes": notes}

    def _fit_charges_to_limit(self, cap_min: int) -> list:
        """Each charge still to come or going now (auto plug-in, Charge now, the car plugged in
        sensor) that takes a day over the daily limit takes the time from your slots after it, the
        next first: their first half hours (or last, with trim_from "end"), just enough (a slot all
        taken off is skipped). As _plan_auto_plug does for a new charge, and put back if the charge
        is deleted. Returns notes."""
        now = self._now().replace(second=0, microsecond=0)
        loc, reset = self._localize, self.limit_reset
        start_day, days = now.date() - datetime.timedelta(days=1), self._horizon_days() + 2
        charge_sources = (AUTO_SOURCE, CHARGE_NOW_SOURCE, CAR_PLUGGED_SOURCE)
        running = without_fallbacks(self.entries)
        charges = [c for c in self._stretches(start_day, days, [e for e in running if e.get("source") in charge_sources],
                                              self.skips) if c["end"] > now]
        notes = []
        for c in charges:
            trims = c["plug"].setdefault("trims", {"skips": [], "added": []})
            later_end = c["end"] + datetime.timedelta(days=3)
            for _ in range(20):  # (each pass fixes one limit day; a few at most)
                running = without_fallbacks(self.entries)
                wins = self.windows(running, now, self.skips, open_ended=True)
                over = None
                for a in limit_starts(wins, now - datetime.timedelta(days=1), later_end, reset, loc):
                    stop = limit_end(a, reset, loc)
                    if a < c["end"] and stop > c["start"] and window_minutes(wins, a, stop) > cap_min:
                        over = (a, stop)
                        break
                if over is None:
                    break
                a, stop = over
                slots = [s for s in self._stretches(start_day, days, [e for e in running if not e.get("source")], self.skips)
                         if s["start"] > now and s["start"] >= c["start"] and s["start"] < stop and s["end"] > a]
                if not slots:
                    break  # nothing of yours after it to take from (it's over by itself)
                s = slots[0]
                length = int((s["end"] - s["start"]).total_seconds() // 60)
                done = False
                for cut in range(30, int(math.ceil(length / 30)) * 30 + 1, 30):
                    added, skips, note = self._trim_slot(s, cut, now)
                    test = self.windows(running + added, now, self.skips + skips, open_ended=True)
                    if window_minutes(test, a, stop) <= cap_min or cut >= length:
                        self.entries = self.entries + added
                        self.skips = self.skips + [k for k in skips if k not in self.skips]
                        trims["added"] = trims.get("added", []) + [e["id"] for e in added]
                        trims["skips"] = trims.get("skips", []) + [k for k in skips if k not in trims.get("skips", [])]
                        notes.append(note)
                        done = True
                        break
                if not done:
                    break
        return notes

    def _trim_slot(self, s: dict, cut: int, now: datetime.datetime) -> tuple:
        """A slot of yours (a stretch) with `cut` minutes taken off its start (or end, with
        trim_from "end"), as a one-off: (entries to add, skips, a note)."""
        def one_off(t, action):
            return validate_entry({"time": t.strftime("%H:%M"), "date": t.date().isoformat(), "action": action})
        when = f" ({s['start'].strftime('%a')})" if s["start"].date() != now.date() else ""
        if self.trim_from == "end":
            new_end = s["end"] - datetime.timedelta(minutes=cut)
            skips = [{"entry_id": s["unplug"]["id"], "date": s["end"].date().isoformat()}]
            if new_end > s["start"]:
                return [one_off(new_end, "unplug")], skips, f"the {s['start'].strftime('%H:%M')} slot{when} now ends at {new_end.strftime('%H:%M')}"
        else:
            new_start = s["start"] + datetime.timedelta(minutes=cut)
            skips = [{"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()}]
            if new_start < s["end"]:
                return [one_off(new_start, "plug")], skips, f"the {s['end'].strftime('%H:%M')} slot{when} now starts at {new_start.strftime('%H:%M')}"
        skips = [{"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()},
                 {"entry_id": s["unplug"]["id"], "date": s["end"].date().isoformat()}]
        return [], skips, f"the {s['start'].strftime('%H:%M')}–{s['end'].strftime('%H:%M')} slot{when} is skipped"

    def _stretches(self, start: datetime.date, days: int, entries: list, skips: list) -> list:
        """Plugged-in stretches with the entries that make them:
        [{"start", "plug", "end", "unplug"}] (a stretch still open at the end is left out)."""
        out, state, cur = [], None, None
        for t, e, skipped in self.timeline(start, days, entries, skips):
            if skipped:
                continue
            if e["action"] == "plug" and state is not True:
                state, cur = True, {"start": t, "plug": e}
            elif e["action"] == "unplug":
                if state is True:
                    cur.update(end=t, unplug=e)
                    out.append(cur)
                state = False
        return out

    def first_ready_at(self, target: datetime.datetime, ready_times: Optional[list] = None):
        """The first ready time your supplier accepts at or after `target`."""
        best = None
        for d in range(3):
            day = target.date() + datetime.timedelta(days=d)
            for t in ready_times or ALL_DAY_TIMES:
                h, m = _parse_hhmm(t)
                dt = self._localize(datetime.datetime(day.year, day.month, day.day, h, m))
                if dt >= target and (best is None or dt < best):
                    best = dt
        return best

    def last_ready_by(self, limit: datetime.datetime, ready_times: Optional[list] = None):
        """The last ready time your supplier accepts at or before `limit` (that day or the one before)."""
        best = None
        for d in (0, -1):
            day = limit.date() + datetime.timedelta(days=d)
            for t in ready_times or ALL_DAY_TIMES:
                h, m = _parse_hhmm(t)
                dt = self._localize(datetime.datetime(day.year, day.month, day.day, h, m))
                if dt <= limit and (best is None or dt > best):
                    best = dt
        return best

    def plan_auto_plug(self, minutes: int, ready_times: Optional[list] = None,
                       cap_min: Optional[int] = None, provider: str = "your supplier",
                       source: str = AUTO_SOURCE, dry_run: bool = False) -> dict:
        """As _plan_auto_plug, but first the automatic schedule's blocks still to come
        are taken out: this charge comes first (they're planned again around it)."""
        if not (self.auto_plan and (self.auto_plan["changes"].get("added") or self.auto_plan["changes"].get("skips"))):
            return self._plan_auto_plug(minutes, ready_times, cap_min, provider, source, dry_run)
        if dry_run:
            undo = (copy.deepcopy(self.entries), list(self.skips), copy.deepcopy(self.auto_plan))
            self._undo_auto_still_to_come()
            try:
                return self._plan_auto_plug(minutes, ready_times, cap_min, provider, source, dry_run)
            finally:
                self.entries, self.skips, self.auto_plan = undo
        self._undo_auto_still_to_come()
        logger.info("Automatic schedule: its blocks are taken out for this charge, and planned again around it")
        return self._plan_auto_plug(minutes, ready_times, cap_min, provider, source, dry_run)

    def _plan_auto_plug(self, minutes: int, ready_times: Optional[list] = None,
                        cap_min: Optional[int] = None, provider: str = "your supplier",
                        source: str = AUTO_SOURCE, dry_run: bool = False) -> dict:
        """An auto plug-in (or Charge now) just plugged in: add a charge of
        `minutes` to the schedule as a one-off (see the module docstring).

        With cap_min (Octopus: 360), the charge is at most that long, and if
        it takes any 24 hours (the previous 24 included) over the cap, the
        scheduled slots after it lose their first half hours (or, with
        trim_from "end", their last), as a one-off, the next first: just
        enough to get back under it (a slot that's all taken off is skipped,
        and the next one is trimmed too, as long as it's in a day the charge
        takes over). dry_run: work it out without changing
        anything (Charge now asks first). Returns what was (or would be) done,
        with "over": the most minutes in any 24 hours before and after the
        slots are trimmed."""
        loc = self._localize
        now = self._now().replace(second=0, microsecond=0)
        end = self.first_ready_at(now + datetime.timedelta(minutes=minutes), ready_times)
        if end is None:
            raise ValueError("No ready time your supplier accepts in the next 2 days")
        # Plugged in from now for that long, but not in a saving session or an
        # off-peak time: it unplugs at the start of one and plugs back in at its
        # end. After a saving session it carries on that much later (to a ready
        # time your supplier accepts), so it keeps its hours; an off-peak time is
        # just left out (the car may well charge then anyway), so it still ends
        # when it would have.
        cuts = self.block_windows(now.date() - datetime.timedelta(days=1), 4)
        saving = set(self.session_windows())
        segments = self._place(now, end - now, [c for c in cuts if c in saving])
        if segments and segments[-1][1] > end:
            later = self.first_ready_at(segments[-1][1], ready_times) or segments[-1][1]
            segments[-1] = (segments[-1][0], later)
            end = later
        segments = minus(segments, [c for c in cuts if c not in saving])
        blocked_notes = [f"not plugged in {ca.strftime('%H:%M')}–{cb.strftime('%H:%M')} "
                         + (f"(saving session), so it carries on until {end.strftime('%H:%M')}" if (ca, cb) in saving and ca > now
                            else "(saving session)" if (ca, cb) in saving else "(off-peak time)")
                         for ca, cb in cuts if ca < end and cb > now]
        shortened = None
        reset = self.limit_reset
        fixed, span_len = _fixed(reset), limit_length(reset)
        if cap_min:
            # e.g. Octopus schedules at most 6 hours a day: the earliest that many
            # are scheduled, then it stops. With a reset time (Octopus: 12:00),
            # that many in each limit day: a charge across it can be longer.
            pieces = []
            for x, y in segments:
                while fixed:
                    nxt = limit_end(period_start(x, fixed, loc), fixed, loc)
                    if nxt >= y:
                        break
                    pieces.append((x, nxt))
                    x = nxt
                pieces.append((x, y))
            keep, used, stopped = [], {}, False
            for x, y in pieces:
                key = period_start(x, fixed, loc) if fixed else None
                left = cap_min - used.get(key, 0)
                length = (y - x).total_seconds() / 60
                if length <= left:
                    keep.append((x, y))
                    used[key] = used.get(key, 0) + length
                    continue
                if left > 0:
                    cut = x + datetime.timedelta(minutes=left)
                    earlier = self.last_ready_by(cut, ready_times)  # a ready time, if one fits
                    keep.append((x, earlier if earlier is not None and earlier > x else cut))
                stopped = True
                break
            if stopped:
                merged = []
                for x, y in keep:
                    if merged and merged[-1][1] == x:
                        merged[-1] = (merged[-1][0], y)
                    else:
                        merged.append((x, y))
                segments = merged
                kept = int(round(sum((y - x).total_seconds() for x, y in segments) / 60))
                shortened = (f"shortened to {_hours(cap_min)} hours, {provider}'s daily limit" if not fixed
                             else f"shortened to {_hours(kept)} hours, {provider}'s daily limit of "
                                  f"{_hours(cap_min)} hours (resets at {fixed})")
        if not segments:
            plan = {"at": _iso_local(now), "minutes": 0, "ready_for": None, "end": None, "combined": False,
                    "moved": None, "notes": blocked_notes or ["nothing to schedule"], "source": source,
                    "cap_min": cap_min, "over": None, "plug_now": False, "segments": [], "nothing": True}
            if not dry_run:
                self.auto_plug_last = plan
                logger.info("%s: nothing scheduled (%s)", SOURCE_NAMES.get(source, "Charge"), "; ".join(plan["notes"]))
            return plan
        end = segments[-1][1]
        minutes = int(round(sum((y - x).total_seconds() for x, y in segments) / 60))
        plug_now = segments[0][0] <= now

        def one_off(t, action, src=source):
            return validate_entry({"time": t.strftime("%H:%M"), "date": t.date().isoformat(),
                                   "action": action, "source": src})

        # With the schedule on, the charge joins the slots it meets. With it off it stays separate
        # from your slots (it joins only other car slots), but it still counts them for the daily
        # limit, as if joined, and trims or skips later ones of yours to keep within it, so it all
        # fits when the schedule's turned back on (it joins them then: see _join_charges)
        active = without_fallbacks(self.entries)
        car_slots = [e for e in active if e.get("source") in AUTO_SOURCES]
        seg_entries = []  # plug / unplug for each part but the last's unplug
        for i, (x, y) in enumerate(segments):
            seg_entries.append(one_off(x, "plug"))
            if i < len(segments) - 1:
                seg_entries.append(one_off(y, "unplug"))
        unplug = one_off(end, "unplug")
        start_day = now.date() - datetime.timedelta(days=1)
        # The scheduled slots still to come (the first may meet the charge),
        # up to 3 days on: the ones that can be trimmed
        later = [s for s in self._stretches(start_day, 5, active, self.skips)
                 if s["end"] > now and s["start"] < end + datetime.timedelta(days=3)]
        if later and later[0]["start"] <= now:
            later = later[1:]  # one going now isn't trimmed

        from_end = self.trim_from == "end"

        def build(cuts: dict, joining: Optional[list] = None):
            """The entries and skips for the charge, with the first `cuts[i]`
            minutes of later slot i taken off (from_end: the last; all of it: skipped). joining:
            the entries whose slots it joins (and whose unplugs during it are skipped): all of
            them by default."""
            joining = active if joining is None else joining
            added, skips, notes = list(seg_entries), list(self.skips), []
            for i, s in enumerate(later):
                cut = cuts.get(i)
                if not cut:
                    continue
                if from_end:  # its last half hours come off: it unplugs earlier
                    new_end = s["end"] - datetime.timedelta(minutes=cut)
                    skips.append({"entry_id": s["unplug"]["id"], "date": s["end"].date().isoformat()})
                    if new_end > s["start"]:
                        added.append(one_off(new_end, "unplug", s["unplug"].get("source")))  # still that slot's own kind
                        notes.append(f"the {s['start'].strftime('%H:%M')} slot now ends at {new_end.strftime('%H:%M')}")
                    else:
                        skips.append({"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()})
                        notes.append(f"the {s['start'].strftime('%H:%M')}–{s['end'].strftime('%H:%M')} slot is skipped"
                                     + (f" ({s['start'].strftime('%a')})" if s["start"].date() != now.date() else ""))
                    continue
                new_start = s["start"] + datetime.timedelta(minutes=cut)
                skips.append({"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()})
                if new_start < s["end"]:
                    plug_later = one_off(new_start, "plug", s["plug"].get("source"))  # still that slot's own kind
                    added.append(plug_later)
                    notes.append(f"the {s['end'].strftime('%H:%M')} slot now starts at {new_start.strftime('%H:%M')}")
                else:
                    skips.append({"entry_id": s["unplug"]["id"], "date": s["end"].date().isoformat()})
                    notes.append(f"the {s['start'].strftime('%H:%M')}–{s['end'].strftime('%H:%M')} slot is skipped"
                                 + (f" ({s['start'].strftime('%a')})" if s["start"].date() != now.date() else ""))
            note = "; ".join(notes) or None
            # The schedule's own slots (as trimmed) that the charge meets or
            # overlaps join it: one slot, plugged in until whichever ends last
            # (not cut short at a slot's unplug when the charge goes on after it)
            own = self._stretches(start_day, 5, joining + [e for e in added if e not in seg_entries], skips)
            slot_end, joined, grew = end, [], True
            while grew:
                grew = False
                for s in own:
                    if s in joined or s["end"] <= now or s["start"] > slot_end:
                        continue
                    joined.append(s)
                    slot_end = max(slot_end, s["end"])
                    grew = True
            combined = bool(joined)
            for s in joined:
                # Its plug-in comes while the charge has it plugged in already: left
                # out, or it'd set the ready time again (to its own, earlier unplug)
                if s["start"] > now:
                    if s["plug"] in added:
                        added.remove(s["plug"])
                    else:
                        skips.append({"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()})
            if not any(s["end"] == slot_end for s in joined):
                added.append(unplug)  # the charge ends last: its own unplug
            else:
                # It ends with a scheduled slot's unplug. Its own is kept too, used while the
                # schedule is off (that slot's unplug doesn't run then)
                added.append(dict(unplug, only_off=True))
            # A scheduled unplug during the charge (e.g. an "unplug at 07:00
            # every day", or a joined slot's that ends before it) would end it
            # early: skipped this once
            parts = segments[:-1] + [(segments[-1][0], slot_end)]
            for t, e, skipped in self.timeline(now.date() - datetime.timedelta(days=1), 4, joining, skips):
                if not skipped and e["action"] == "unplug" and any(x < t < y for x, y in parts):
                    skips.append({"entry_id": e["id"], "date": t.date().isoformat()})
            return added, skips, combined, slot_end, note

        span = (now - datetime.timedelta(days=1), (later[-1]["end"] if later else end) + span_len)
        base = self.windows(active, now, self.skips)  # the schedule without this charge

        def totals_for(cuts: dict) -> tuple:
            """(most minutes in any 24 hours; most minutes the charge puts any
            24 hours over the cap, beyond what the schedule already had; the
            same for the 24 hours that meet each later slot, which trimming it
            can fix: {i: minutes})."""
            added, skips, *_ = build(cuts)
            wins = self.windows(active + without_fallbacks(added), now, skips)
            worst = excess = 0
            near = {}
            for a in limit_starts(wins + base, span[0], span[1], reset, loc):
                stop = limit_end(a, reset, loc)
                with_it = window_minutes(wins, a, stop)
                worst = max(worst, with_it)
                ex = with_it - max(cap_min, window_minutes(base, a, stop))
                excess = max(excess, ex)
                for i, s in enumerate(later):
                    if a < s["end"] and stop > s["start"]:
                        near[i] = max(near.get(i, 0), ex)
            return worst, excess, near

        cuts, before, after, added_over, left_over = {}, None, None, 0, 0
        if cap_min:
            before, added_over, near = totals_for(cuts)
            after, left_over = before, added_over
            # Each later slot in turn, the next first: its first (or last) half hours
            # come off, just enough (or all of it, then the one after)
            for i, s in enumerate(later):
                if near.get(i, 0) <= 0:
                    continue
                length = (s["end"] - s["start"]).total_seconds() / 60
                best = None
                for c in range(30, int(math.ceil(length / 30)) * 30 + 1, 30):
                    total, ex, n = totals_for({**cuts, i: c})
                    if n.get(i, 0) < near[i] and (best is None or n.get(i, 0) < best[3].get(i, 0)):
                        best = (c, total, ex, n)
                    if n.get(i, 0) <= 0:
                        break
                if best:
                    cuts[i] = best[0]
                    after, left_over, near = best[1], best[2], best[3]
        # (the daily limit above is counted as if joined; with the schedule off, only car slots join)
        added, skips, combined, slot_end, moved = build(cuts, None if self.schedule_enabled else car_slots)
        # Longer than the limit across its reset time (e.g. 08:00-18:00 with
        # Octopus's 12:00): split there, a fresh charge with its own ready time
        splits = self._reset_splits(active + without_fallbacks(added), skips, cap_min, one_off=True, until=slot_end) if cap_min else []
        added += splits
        owned = self._skip_owners(None)  # (one something else made: this needs it too)
        changed = {"skips": [k for k in skips if k not in self.skips or (k["entry_id"], k["date"]) in owned],
                   "added": [e["id"] for e in added if e["id"] not in {x["id"] for x in seg_entries + [unplug]}
                             or e.get("only_off")]}  # (the fallback unplug goes if the charge is deleted)
        if changed["skips"] or changed["added"]:
            added[0] = dict(added[0], trims=changed)  # put back if this charge is deleted
        notes = ([shortened] if shortened else []) + blocked_notes
        if moved:
            notes.append(moved)
        if combined:
            notes.append(f"joined with the scheduled slot until {slot_end.strftime('%H:%M')}" if slot_end > end
                         else "joined with the scheduled slot (it ends within this charge)")
        if splits:
            notes.append("a fresh charge from " + ", ".join(sorted({e["time"] for e in splits}))
                         + f" ({provider}'s daily limit resets then)")
        plan = {
            "at": _iso_local(now), "minutes": minutes, "ready_for": _iso_local(end),
            "end": _iso_local(slot_end), "combined": combined, "moved": moved, "notes": notes,
            "source": source, "cap_min": cap_min,
            # before / after: the most minutes in any 24 hours; added / left: how
            # far this charge takes it over the cap (beyond the schedule's own),
            # before and after the next slot is trimmed
            "over": {"before": before, "after": after, "added": added_over, "left": left_over} if cap_min else None,
            # plugged in straight away (False: it starts after an off-peak time)
            "plug_now": plug_now,
            "segments": [{"start": _iso_local(x), "end": _iso_local(y)} for x, y in segments],
        }
        if dry_run:
            return plan
        self.entries = self.entries + added
        self.skips = [dict(k) for i, k in enumerate(skips) if k not in skips[:i]]
        self._save()
        self.auto_plug_last = plan
        logger.info("%s: plugged in until %s%s", SOURCE_NAMES.get(source, "Charge"),
                    slot_end.strftime("%a %H:%M"),
                    f" ({'; '.join(notes)})" if notes else "")
        if cap_min and left_over > 0:
            logger.warning("%s: the schedule plugs in for %dh %02dm in %s, over %s's %s hours",
                           SOURCE_NAMES.get(source, "Charge"), after // 60, after % 60,
                           f"a day (from {fixed})" if fixed else f"{int(span_len.total_seconds() // 3600)} hours",
                           provider, _hours(cap_min))
        return plan

    # --- saving sessions ----------------------------------------------------------

    def sync_saving_sessions(self, sessions: Optional[list], block: bool, ready_times: Optional[list] = None,
                             cap_min: Optional[int] = None, provider: str = "your supplier",
                             plug_hours: Optional[float] = None) -> bool:
        """The sessions read from the calendar ([{"start", "end", "summary"}];
        None: it couldn't be read, so nothing is forgotten). New ones are
        remembered, a cancelled one (gone from the calendar before it starts)
        has what it changed put back, and with `block` each is an off-peak
        time: a scheduled slot that runs into it is moved (see _move_for_session).
        With `plug_hours`, a charge for that long before each session is added
        to the schedule as soon as it's known (see _plan_precharge).
        Returns whether anything changed (it's saved then)."""
        now = self._now()
        changed = False
        new = None if sessions is None else {session_id(x["start"], x["end"]): x for x in sessions}
        keep = []
        for r in self.saving_sessions:
            start, end = _parse_iso(r["start"]), _parse_iso(r["end"])
            if end < now - SAVING_KEEP:
                changed = True
                continue
            if new is not None and r["id"] not in new and start > now:
                self._undo_session(r)
                self._undo_pre(r)
                logger.info("Saving session %s–%s is no longer in the calendar%s", start.strftime("%a %H:%M"),
                            end.strftime("%H:%M"), ": putting back what it changed" if r.get("notes") else "")
                changed = True
                continue
            keep.append(r)
        known = {r["id"] for r in keep}
        iso = lambda t: _iso_local(self._to_local(t))
        parts_of = lambda x: [{"start": iso(p["start"]), "end": iso(p["end"]), "summary": p["summary"]}
                              for p in x.get("parts") or []]
        for r in keep:  # (what it's made of can change while it's the same, e.g. a second source adds one)
            if new is not None and r["id"] in new and parts_of(new[r["id"]]) != (r.get("parts") or []):
                r["parts"] = parts_of(new[r["id"]])
                if not r["parts"]:
                    r.pop("parts")
                changed = True
        for sid, x in (new or {}).items():
            if sid in known:
                continue
            # in local time (the add-on's TZ), as the rest of the schedule
            keep.append({"id": sid, "start": iso(x["start"]), "end": iso(x["end"]),
                         "summary": x.get("summary") or "Saving session",
                         "changes": {"skips": [], "added": []}, "precharged": False, "notes": [],
                         **({"parts": parts_of(x)} if x.get("parts") else {})})
            logger.info("Saving session found: %s–%s", x["start"].astimezone().strftime("%a %d %b %H:%M"),
                        x["end"].astimezone().strftime("%H:%M"))
            changed = True
        keep.sort(key=lambda r: _parse_iso(r["start"]))
        self.saving_sessions = keep
        if bool(block) != self.saving_block:
            self.saving_block = bool(block)
            changed = True
        # (kept for the automatic schedule, which puts them in again under its own plan)
        self._saving_args = (ready_times, cap_min, provider, plug_hours)
        changed = self._apply_sessions(ready_times, cap_min, provider, plug_hours) or changed
        if changed and self.auto_plan:
            self.auto_plan["key"] = None  # (planned again around them, they come first)
        if changed:
            self._save()
        return changed

    def _apply_sessions(self, ready_times: Optional[list], cap_min: Optional[int], provider: str,
                        plug_hours: Optional[float]) -> bool:
        """What each saving session changes in the schedule: the slots that run into it moved
        (with it an off-peak time) and the charge before it. Returns whether anything changed."""
        now = self._now()
        keep, block, changed = self.saving_sessions, self.saving_block, False
        moved = False
        movers = []  # the sessions that moved a slot this time
        for r in keep:
            if not block:
                if r["changes"]["skips"] or r["changes"]["added"]:
                    self._undo_session(r)
                    self._undo_pre(r)  # (it may have used the session's unplug: redone below)
                    logger.info("Saving sessions aren't off-peak times now: the slots moved for %s are put back",
                                _parse_iso(r["start"]).strftime("%a %H:%M"))
                    changed = True
                continue
            if _parse_iso(r["end"]) > now and self._move_for_session(r, ready_times):
                moved = changed = True
                movers.append(r)
        if moved and cap_min and self.ready_time and not self.override_guards:
            # a moved slot that's now across the daily limit's reset time is split there, as for Charge now
            splits = self._reset_splits(self._active_entries(), self.skips, cap_min, one_off=True)
            if splits:
                self.entries = self.entries + splits
                for e in splits:
                    # (the session nearest the split, of those that moved a slot: put back with it)
                    at = next(self._occurrences(e, _parse_date(e["date"]), 1))
                    owner = min(movers or keep, key=lambda r: abs((_parse_iso(r["start"]) - at).total_seconds()))
                    owner["changes"]["added"].append(e["id"])
            worst = self._longest_day(self._active_entries(), self.skips)
            if worst and worst[0] > cap_min:
                logger.warning("Saving session: with the slots moved, the schedule plugs in for %dh %02dm in %s, "
                               "over %s's %s hours", worst[0] // 60, worst[0] % 60,
                               limit_day_text(worst[1], self.limit_reset), provider, _hours(cap_min))
        # The charge before each session still to come (taken out again if it's
        # turned off, or redone if its hours change)
        for r in keep:
            if _parse_iso(r["start"]) <= now:
                continue
            pre = r.get("pre")
            if pre is not None and pre.get("hours") != plug_hours:
                self._undo_pre(r)
                changed = True
            if plug_hours and r.get("pre") is None and not r.get("precharged"):
                self._plan_precharge(r, float(plug_hours), cap_min if self.ready_time and not self.override_guards
                                     else None, provider)
                changed = True
        return changed

    def clear_saving_sessions(self) -> bool:
        """Saving sessions turned off: put back everything they changed and forget them."""
        if not self.saving_sessions and not self.saving_block:
            return False
        for r in self.saving_sessions:
            self._undo_session(r)
            self._undo_pre(r)
        self.saving_sessions, self.saving_block = [], False
        self._save()
        logger.info("Saving sessions off: anything they changed in the schedule is put back")
        return True

    # A skip can be needed by more than one thing (a saving session, the charge before it, the
    # automatic schedule, a charge that trimmed a slot): each lists it as its own, and taking
    # one out leaves a skip another still needs. A skip you made yourself isn't listed by them.
    def _skip_owners(self, mine) -> set:
        owners = [(self.auto_plan or {}).get("changes") or {}]
        for r in self.saving_sessions:
            owners += [r.get("changes") or {}, (r.get("pre") or {}).get("changes") or {}]
        owners += [e["trims"] for e in self.entries if e.get("trims")]
        return {(k["entry_id"], k["date"]) for ch in owners if ch is not mine for k in ch.get("skips") or []}

    def _claim_skips(self, keys: list, mine) -> list:
        """Skips something (its changes: `mine`) needs: in if they're not, and the ones it then
        owns returned (not one you'd made yourself, which stays when it's undone)."""
        others, out = self._skip_owners(mine), []
        for k in keys:
            if k not in self.skips:
                self.skips = self.skips + [k]
                out.append(k)
            elif (k["entry_id"], k["date"]) in others and k not in out:
                out.append(k)
        return out

    def _release_skips(self, keys: list, mine) -> None:
        """Its skips taken out, but not one something else still needs."""
        others = self._skip_owners(mine)
        self.skips = [k for k in self.skips if k not in keys or (k["entry_id"], k["date"]) in others]

    def _undo_session(self, r: dict) -> None:
        added = set(r["changes"].get("added") or [])
        skips = r["changes"].get("skips") or []
        if added or skips:
            self.entries = [e for e in self.entries if e["id"] not in added]
            self._release_skips(skips, r["changes"])
        r["changes"] = {"skips": [], "added": []}
        r["notes"] = [n for n in r.get("notes") or [] if n.startswith("charge")]

    @staticmethod
    def _place(start: datetime.datetime, length: datetime.timedelta, cuts: list) -> list:
        """`length` of plugged-in time from `start`, around the `cuts`: [(a, b)]."""
        out, t, left = [], start, length
        cuts = sorted(cuts)
        for _ in range(50):
            if left <= datetime.timedelta(0):
                break
            inside = next((cb for ca, cb in cuts if ca <= t < cb), None)
            if inside is not None:
                t = inside
                continue
            nxt = min((ca for ca, _ in cuts if ca > t), default=None)
            y = t + left if nxt is None or t + left <= nxt else nxt
            out.append((t, y))
            left -= y - t
            t = y
        return out

    @staticmethod
    def _place_back(end: datetime.datetime, length: datetime.timedelta, cuts: list,
                    not_before: datetime.datetime) -> list:
        """`length` of plugged-in time ending at `end`, filled backwards around
        the `cuts` and not before `not_before`: [(a, b)] in time order (shorter
        if it runs into `not_before`)."""
        out, t, left = [], end, length
        cuts = sorted(cuts)
        for _ in range(50):
            if left <= datetime.timedelta(0) or t <= not_before:
                break
            inside = next((ca for ca, cb in cuts if ca < t <= cb), None)
            if inside is not None:
                t = inside
                continue
            prev = max((cb for _, cb in cuts if cb < t), default=None)
            x = max(t - left, not_before, prev if prev is not None else t - left)
            out.append((x, t))
            left -= t - x
            t = x
        return out[::-1]

    def _move_for_session(self, r: dict, ready_times: Optional[list]) -> bool:
        """Move the scheduled slots that run into saving session `r`: a slot
        keeps its end (its ready time) and its length, and is not plugged in
        during the session, so it starts that much earlier (e.g. 15:30–21:30
        with an 18:00–19:00 session: 14:30–18:00 and 19:00–21:30), around any
        other off-peak time. If that start would be in the past, it starts at
        the next half hour instead (if that's before its own start) and ends
        that much later (found at 14:40: 15:00–18:00, 19:00–22:00; found at
        15:10, its start kept: 15:30–18:00, 19:00–22:30),
        unplugging at a ready time your supplier accepts (with Force schedule
        on). A slot already running unplugs at the session (or now) and carries
        on after it the same way. Recorded in r["changes"], so it can be put back."""
        sa, sb = _parse_iso(r["start"]), _parse_iso(r["end"])
        moved = False
        for _ in range(10):
            now = self._now().replace(second=0, microsecond=0)
            soon = now + datetime.timedelta(minutes=1)
            cut_from = max(sa, soon)
            if cut_from >= sb:
                break
            active = self._active_entries()
            start_day = now.date() - datetime.timedelta(days=1)
            stretches = self._stretches(start_day, self._horizon_days() + 2, active, self.skips)
            hit = next((s for s in stretches if s["end"] > cut_from and s["start"] < sb), None)
            if hit is None:
                break
            src = hit["plug"].get("source")

            def one_off(t, action):
                return validate_entry({"time": t.strftime("%H:%M"), "date": t.date().isoformat(),
                                       "action": action, **({"source": src} if src else {})})

            def key(entry, t):
                return {"entry_id": entry["id"], "date": t.date().isoformat()}

            cuts = self.block_windows(hit["start"].date() - datetime.timedelta(days=1), 4)
            added, skips = [], []
            running = hit["start"] <= now
            stopped_at = None  # (ending later: cut short at an off-peak time)
            c = max(cut_from, hit["start"])
            needed = hit["end"] - (c if running else hit["start"])
            if running:
                # plugged in already: until the session (or now), then what's left of it after
                added.append(one_off(c, "unplug"))
            else:
                skips.append(key(hit["plug"], hit["start"]))
            skips.append(key(hit["unplug"], hit["end"]))
            # Keep its end, starting earlier; if that would start in the past (or
            # it's running), keep its start and carry on after its end instead
            segs = [] if running else self._place_back(hit["end"], needed, cuts, soon)
            later_end = sum((y - x for x, y in segs), datetime.timedelta(0)) < needed
            if later_end:
                # as soon as it can: the next half hour (or its own start, if sooner)
                first = c if running else max(min(hit["start"], _next_half_hour(soon)), soon)
                segs = self._place(first, needed, cuts)
                # ... but not past an off-peak time: the car can charge then anyway, so
                # what's left isn't carried on after it (e.g. to 05:30 the next morning)
                peak = [a for a, b in self.block_windows(hit["start"].date() - datetime.timedelta(days=1), 4,
                                                         sessions=False) if a >= first]
                stop = min(peak) if peak else None
                if stop is not None and any(y > stop for x, y in segs):
                    segs = [(x, min(y, stop)) for x, y in segs if x < stop]
                    stopped_at = stop
                if segs and self.ready_time and ready_times:
                    ready = self.first_ready_at(segs[-1][1], ready_times)
                    if ready is not None and ready > segs[-1][1] and (stop is None or ready <= stop):
                        segs[-1] = (segs[-1][0], ready)
            # A slot it now meets or runs into joins it: the one before (plugged in
            # from its start) or, ending later, the one after (until its end)
            earlier = None if later_end else next(
                (s for s in stretches if s is not hit and segs and s["end"] >= segs[0][0]
                 and s["start"] < segs[0][0] and s["end"] <= hit["start"]), None)
            after = None if not later_end or not segs else next(
                (s for s in stretches if s is not hit and s["start"] >= hit["end"]
                 and s["start"] <= segs[-1][1]), None)
            for n, (x, y) in enumerate(segs):
                if not (n == 0 and earlier is not None):
                    added.append(one_off(x, "plug"))
                if not (n == len(segs) - 1 and after is not None and after["end"] > y):
                    added.append(one_off(y, "unplug"))
            if earlier is not None:
                skips.append(key(earlier["unplug"], earlier["end"]))
            if after is not None:
                skips.append(key(after["plug"], after["start"]))
                if after["end"] <= segs[-1][1]:
                    skips.append(key(after["unplug"], after["end"]))
            self.entries = self.entries + added
            r["changes"]["added"] += [e["id"] for e in added]
            r["changes"]["skips"] += [k for k in self._claim_skips(skips, r["changes"]) if k not in r["changes"]["skips"]]
            # The time it's moved to, outside the slot's own (for the page: shown in purple)
            r["changes"]["parts"] = (r["changes"].get("parts") or []) + [
                [_iso_local(x), _iso_local(y)] for x, y in minus(segs, [(hit["start"], hit["end"])]) if y > x]
            parts = ([(hit["start"], c)] if running else []) + segs
            if parts and parts[0][1] == (parts[1][0] if len(parts) > 1 else None):
                parts = [(parts[0][0], parts[1][1])] + parts[2:]
            if earlier is not None and parts:
                parts[0] = (earlier["start"], parts[0][1])
            if after is not None and parts and after["end"] > parts[-1][1]:
                parts[-1] = (parts[-1][0], after["end"])
            span = ", ".join(f"{x.strftime('%H:%M')}–{y.strftime('%H:%M')}" for x, y in parts)
            note = (f"the {hit['start'].strftime('%H:%M')}–{hit['end'].strftime('%H:%M')} slot is now {span}"
                    + (" (joined with the slot before)" if earlier is not None else "")
                    + (" (joined with the next slot)" if after is not None else "")
                    + ((" (ends later: it had begun)" if running else
                        " (starts at the next half hour and ends later: starting earlier would be in the past)"
                        if segs and segs[0][0] < hit["start"] else
                        " (ends later: starting earlier would be in the past)")
                       if later_end else "")
                    + (f" (the rest isn't carried on past the off-peak time at {stopped_at.strftime('%H:%M')})"
                       if stopped_at is not None else ""))
            r["notes"] = (r.get("notes") or []) + [note]
            logger.info("Saving session %s–%s: %s", sa.strftime("%a %H:%M"), sb.strftime("%H:%M"), note)
            moved = True
        return moved

    def _unplug_at(self, t: datetime.datetime) -> bool:
        """Whether the schedule (as it runs) unplugs at `t`."""
        return any(occ == t and e["action"] == "unplug" and not skipped
                   for occ, e, skipped in self.timeline(t.date(), 1))

    def _undo_pre(self, r: dict) -> None:
        pre = r.pop("pre", None)
        if pre:
            added = set(pre["changes"].get("added") or [])
            self.entries = [e for e in self.entries if e["id"] not in added]
            self._release_skips(pre["changes"].get("skips") or [], pre["changes"])
        r["notes"] = [n for n in r.get("notes") or [] if not n.startswith("charge before")]

    def _plan_precharge(self, r: dict, hours: float, cap_min: Optional[int], provider: str) -> None:
        """Plug in for `hours` before session `r`, as one-offs: only for the
        time the schedule doesn't already have the car plugged in (joining a
        slot it meets), not in an off-peak time, not in the past. If that
        takes a day over the daily limit (`cap_min`), the next slot after the
        session starts later by that much, as for a car plugged in. Recorded in
        r["pre"], so it can be taken out again."""
        sa = _parse_iso(r["start"])
        now = self._now().replace(second=0, microsecond=0)
        soon = now + datetime.timedelta(minutes=1)
        r["pre"] = pre = {"hours": hours, "changes": {"skips": [], "added": []}}
        start = max(sa - datetime.timedelta(minutes=int(round(hours * 60))), soon)

        def note(text):
            r["notes"] = [n for n in r.get("notes") or [] if not n.startswith("charge before")] + [
                "charge before it: " + text]
            logger.info("Saving session %s: charge before it: %s", sa.strftime("%a %H:%M"), text)

        if (sa - start).total_seconds() < MIN_PRECHARGE_MIN * 60:
            note("too close to the session")
            return
        active = self._active_entries()
        stretches = self._stretches(now.date() - datetime.timedelta(days=1), self._horizon_days() + 2, active, self.skips)
        plugged = [(s["start"], s["end"]) for s in stretches]
        cuts = self.block_windows(start.date() - datetime.timedelta(days=1), 4)
        gaps = minus([(start, sa)], plugged + cuts)
        gaps = [(x, y) for x, y in gaps if (y - x).total_seconds() >= 60]
        if not gaps:
            note(f"the schedule already has the car plugged in from {start.strftime('%H:%M')}")
            return

        def one_off(t, action, src=SAVING_SOURCE):
            return validate_entry({"time": t.strftime("%H:%M"), "date": t.date().isoformat(), "action": action,
                                   **({"source": src} if src else {})})

        def key(entry, t):
            return {"entry_id": entry["id"], "date": t.date().isoformat()}

        added, skips, joined = [], [], []
        for x, y in gaps:
            before = next((s for s in stretches if s["end"] == x), None)
            after = next((s for s in stretches if s["start"] == y), None)
            if before is not None:
                skips.append(key(before["unplug"], x))
                joined.append(before)
            else:
                added.append(one_off(x, "plug"))
            if after is not None:
                skips.append(key(after["plug"], y))
                joined.append(after)
            elif not self._unplug_at(y):
                added.append(one_off(y, "unplug"))
        self.entries = self.entries + added
        pre["changes"]["added"] += [e["id"] for e in added]
        pre["changes"]["skips"] += [k for k in self._claim_skips(skips, pre["changes"]) if k not in pre["changes"]["skips"]]
        span = ", ".join(f"{x.strftime('%H:%M')}–{y.strftime('%H:%M')}" for x, y in gaps)
        text = span + (" (joined with the slot it meets)" if joined else "")
        if cap_min and _fixed(self.limit_reset):
            fixed_day = self._precharge_fixed_day(r, pre, gaps, cap_min, provider, soon, one_off, key)
            if fixed_day is None:
                return  # (not added: noted)
            text += fixed_day
        elif cap_min:
            # Over the daily limit: the next slot after the session starts later. Counted as the
            # schedule runs with it on, also while it's off (your slots too), and the next slot
            # can be yours, so it fits when the schedule's turned back on (as for a car plugged in)
            wins, now_st = self._as_if_on()
            worst = longest_day(wins, now - datetime.timedelta(days=1),
                                now + datetime.timedelta(days=self._horizon_days() - 1), self.limit_reset, self._localize)
            if worst and worst[0] > cap_min:
                over = int(math.ceil((worst[0] - cap_min) / 30)) * 30
                nxt = next((s for s in now_st if s["start"] >= _parse_iso(r["end"])), None)
                if nxt is not None:
                    k_plug = key(nxt["plug"], nxt["start"])
                    new_start = nxt["start"] + datetime.timedelta(minutes=over)
                    trims = [k_plug]
                    extra = []
                    if new_start < nxt["end"]:
                        extra.append(one_off(new_start, "plug", nxt["plug"].get("source")))
                        text += (f"; the {nxt['start'].strftime('%H:%M')}–{nxt['end'].strftime('%H:%M')} slot now "
                                 f"starts at {new_start.strftime('%H:%M')} ({provider}'s daily limit)")
                    else:
                        trims.append(key(nxt["unplug"], nxt["end"]))
                        text += (f"; the {nxt['start'].strftime('%H:%M')}–{nxt['end'].strftime('%H:%M')} slot is "
                                 f"skipped ({provider}'s daily limit)")
                    self.entries = self.entries + extra
                    pre["changes"]["added"] += [e["id"] for e in extra]
                    pre["changes"]["skips"] += [k for k in self._claim_skips(trims, pre["changes"]) if k not in pre["changes"]["skips"]]
        note(text)

    def _precharge_fixed_day(self, r, pre, gaps, cap_min, provider, soon, one_off, key) -> Optional[str]:
        """A limit that resets at a time each day (Octopus: 12:00): the charge before
        a session counts in the limit day it's in, so if that day goes over, the
        time comes off a slot in that same day (a slot in the next day doesn't
        help): first the end of the one before the session (if that's still to
        come), then the start of the one after it, if that's still in the day.
        If there isn't enough, the charge before it isn't added, and that's
        noted (and logged). Returns the text to add to the note, or None if not added."""
        loc, reset = self._localize, self.limit_reset
        wins, stretches = self._as_if_on()
        days = sorted({period_start(x, reset, loc) for x, _ in gaps} | {period_start(y - datetime.timedelta(seconds=1), reset, loc) for _, y in gaps})

        def over_by():
            w, _ = self._as_if_on()
            return max(window_minutes(w, d, limit_end(d, reset, loc)) - cap_min for d in days)

        over = over_by()
        if over <= 0:
            return ""
        over = int(math.ceil(over / 30)) * 30
        sa = _parse_iso(r["start"])
        day0, day1 = days[0], limit_end(days[-1], reset, loc)
        # (a slot the charge joined is one stretch with it now, ending at the session: not a "before")
        mine = [s for s in stretches if s["start"] < day1 and s["end"] > day0]
        before = sorted((s for s in mine if s["end"] <= gaps[0][0] and s["end"] > soon), key=lambda s: s["end"], reverse=True)
        after = sorted((s for s in mine if s["start"] >= sa and s["start"] >= soon), key=lambda s: s["start"])
        extra, trims, done = [], [], []
        left = over
        for s in before:  # its end comes earlier (not before now)
            if left <= 0:
                break
            room = int((s["end"] - max(s["start"], soon)).total_seconds() // 60)
            if room <= 0:
                continue
            take = min(left, room)
            new_end = s["end"] - datetime.timedelta(minutes=take)
            trims.append(key(s["unplug"], s["end"]))
            if new_end > s["start"]:
                extra.append(one_off(new_end, "unplug", s["unplug"].get("source")))
                done.append(f"the {s['start'].strftime('%a %H:%M')}–{s['end'].strftime('%H:%M')} slot now ends at "
                            f"{new_end.strftime('%H:%M')}")
            else:
                trims.append(key(s["plug"], s["start"]))
                done.append(f"the {s['start'].strftime('%a %H:%M')}–{s['end'].strftime('%H:%M')} slot is skipped")
            left -= take
        for s in after:  # it starts later, as long as that's still in the day
            if left <= 0:
                break
            room = int((min(s["end"], day1) - s["start"]).total_seconds() // 60)
            if room <= 0:
                continue
            take = min(left, room)
            new_start = s["start"] + datetime.timedelta(minutes=take)
            trims.append(key(s["plug"], s["start"]))
            if new_start < s["end"]:
                extra.append(one_off(new_start, "plug", s["plug"].get("source")))
                done.append(f"the {s['start'].strftime('%a %H:%M')}–{s['end'].strftime('%H:%M')} slot now starts at "
                            f"{new_start.strftime('%H:%M')}")
            else:
                trims.append(key(s["unplug"], s["end"]))
                done.append(f"the {s['start'].strftime('%a %H:%M')}–{s['end'].strftime('%H:%M')} slot is skipped")
            left -= take
        self.entries = self.entries + extra
        pre["changes"]["added"] += [e["id"] for e in extra]
        pre["changes"]["skips"] += [k for k in self._claim_skips(trims, pre["changes"]) if k not in pre["changes"]["skips"]]
        if left > 0 or over_by() > 0:
            # Not enough to take off in that day: the charge before the session isn't added
            ch = pre["changes"]
            added = set(ch["added"])
            self.entries = [e for e in self.entries if e["id"] not in added]
            self._release_skips(ch["skips"], ch)
            pre["changes"] = {"skips": [], "added": []}
            pre["error"] = True
            text = (f"not added: it would take {limit_day_text(days[0], reset)} over {provider}'s "
                    f"{_hours(cap_min)} hour daily limit, and there isn't a slot still to come in that day "
                    f"to take {over} minutes off")
            r["notes"] = [n for n in r.get("notes") or [] if not n.startswith("charge before")] + ["charge before it: " + text]
            logger.warning("Saving session %s: charge before it %s", _parse_iso(r["start"]).strftime("%a %H:%M"), text)
            return None
        return "; " + ", ".join(done) + f" ({provider}'s daily limit)"

    # --- the automatic schedule (src/auto_schedule.py) -------------------------

    def auto_offpeak(self) -> list:
        """The automatic schedule's off-peak times (from your import rates): [(a, b)]."""
        if not self.auto_plan:
            return []
        out = []
        for a, b in self.auto_plan.get("offpeak") or []:
            a, b = _parse_iso(a), _parse_iso(b)
            if a and b:
                out.append((a, b))
        return out

    def _skip_time(self, k: dict) -> Optional[datetime.datetime]:
        """When a skipped time would have run."""
        e = next((e for e in self.entries if e["id"] == k.get("entry_id")), None)
        try:
            h, m = _parse_hhmm(e["time"])
            d = datetime.date.fromisoformat(k["date"])
        except (TypeError, KeyError, ValueError):
            return None
        return self._localize(datetime.datetime(d.year, d.month, d.day, h, m))

    def _undo_auto_still_to_come(self) -> None:
        """Its changes still to come taken out (for a charge that comes first, planned around
        after): what it's done stays, and counts (its blocks that have been, the time it took
        off your slots), and so does a block that's going, to its end."""
        cur = self.auto_plan or {}
        now = self._now()
        wins = [(a, b) for a, b in ((_parse_iso(x), _parse_iso(y)) for x, y in cur.get("charge") or []) if a and b]
        until = max([now] + [b for a, b in wins if a <= now < b])
        ch = cur.get("changes") or {}
        mine = set(ch.get("added") or [])
        kept = [dict(e) for e in self.entries if e["id"] in mine and e.get("date")
                and (self._skip_time({"entry_id": e["id"], "date": e["date"]}) or until + datetime.timedelta(1)) <= until]
        skips = [k for k in ch.get("skips") or [] if (self._skip_time(k) or until + datetime.timedelta(1)) <= until]
        gave = ch.get("gave") or {}
        if until > now:
            # (a block's going: it isn't planned again until it ends, so your slots that gave way
            # stay as they are till then, or they'd come back on top of the day's limit)
            ids = set(gave.get("added") or [])
            kept += [dict(e) for e in self.entries if e["id"] in ids and e["id"] not in {x["id"] for x in kept}]
            skips += [k for k in gave.get("skips") or [] if k not in skips]
        self._undo_auto(save=False)
        have = {e["id"] for e in self.entries}
        self.entries = self.entries + [e for e in kept if e["id"] not in have]
        self.skips = self.skips + [k for k in skips if k not in self.skips]
        self.auto_plan["changes"] = {"added": [e["id"] for e in kept], "skips": skips}
        if until > now and gave:
            self.auto_plan["changes"]["gave"] = gave
        self.auto_plan["charge"] = [[_iso_local(a), _iso_local(b)] for a, b in wins if a < now]

    def _undo_auto(self, save: bool = True) -> None:
        """Take out what the automatic schedule added (its blocks, and the time taken off other slots)."""
        if not self.auto_plan:
            return
        ch = self.auto_plan.get("changes") or {}
        added = set(ch.get("added") or [])
        self.entries = [e for e in self.entries if e["id"] not in added]
        self._release_skips(ch.get("skips") or [], ch)
        self.auto_plan["changes"] = {"added": [], "skips": []}
        self.auto_plan["charge"] = []
        self.auto_plan["key"] = None  # (planned again at the next look)
        if save:
            self._save()

    def _relayer_sessions(self, apply: bool = True) -> bool:
        """The saving sessions still to come (and their charge before) put in again on your own
        schedule, as they come first: what they changed may have been made on the automatic
        schedule's plan, just taken out. Not while a charge that comes first is in the schedule
        (it may have changed theirs). apply False: only taken out (the caller puts them in again,
        with _apply_sessions). Returns whether they were taken out."""
        now = self._now()
        later = [r for r in self.saving_sessions if _parse_iso(r["start"]) > now
                 and (self._pre_window(r) is None or _parse_iso(self._pre_window(r)[0]) > now)]
        charging = any(e.get("source") in (AUTO_SOURCE, CHARGE_NOW_SOURCE, CAR_PLUGGED_SOURCE)
                       and e.get("date", "") >= now.date().isoformat() for e in self.entries)
        if later and self._saving_args and not charging:
            for r in later:
                self._undo_pre(r)
                self._undo_session(r)
            if apply:
                self._apply_sessions(*self._saving_args)
            return True
        return False

    def clear_auto_schedule(self) -> bool:
        """The automatic schedule turned off: put back what it changed."""
        if self.auto_plan is None:
            return False
        self._undo_auto(save=False)
        self.auto_plan = None
        self._relayer_sessions()  # (as they'd be without it: e.g. its off-peak times gone)
        self._save()
        logger.info("Automatic schedule off: its blocks are taken out of the schedule")
        return True

    def _schedule_key(self) -> str:
        """What the automatic schedule was planned around: the other slots and skips."""
        ch = (self.auto_plan or {}).get("changes") or {}
        mine = set(ch.get("added") or [])
        # (and what saving sessions changed: they're put in again with it, so not what it's planned around)
        theirs = [r.get("changes") or {} for r in self.saving_sessions] + \
            [(r.get("pre") or {}).get("changes") or {} for r in self.saving_sessions]
        mine |= {i for c in theirs for i in c.get("added") or []}
        their_skips = [k for c in theirs for k in c.get("skips") or []]
        ents = sorted((e["id"], e["time"], e["action"], e.get("date") or "", tuple(e.get("days") or []), e["enabled"])
                      for e in self.entries if e["id"] not in mine)
        sk = sorted((k["entry_id"], k["date"]) for k in self.skips if k not in (ch.get("skips") or []) and k not in their_skips)
        return repr((ents, sk, self.schedule_enabled, [(_iso_local(a), _iso_local(b)) for a, b in self.session_windows()]))

    def sync_auto_schedule(self, imp: list, exp: list, blocks: int, cap_min: Optional[int] = None,
                           provider: str = "your supplier", info: Optional[dict] = None, mode: str = "cheapest",
                           charge_cap: Optional[int] = None, discharge_cap: Optional[int] = None) -> bool:
        """Plan (or plan again) the automatic schedule from the rates (src/auto_schedule.py
        parse_rates) and put its blocks in the schedule, as one-offs, as a charge before a
        saving session is: joined to a slot they meet, and with `cap_min` (a daily limit that
        resets at a time each day), time taken off your own slots in a day it takes over
        (not off auto plug-in, the car plugged in sensor, Charge now or a charge before a
        saving session: those come first). Planned again when the rates, the number of
        blocks or the rest of the schedule change, but not while one of its blocks is going.
        Returns whether anything changed."""
        from src import auto_schedule as planner
        now = self._now()
        # (in local time, as the rest of the schedule: rates may come in UTC)
        imp, exp = self._rates_local(imp), self._rates_local(exp)
        copies = self._copies(info)
        known_exp = planner.known_only(exp, copies)
        # (tomorrow's not out yet: planned on today's as an estimate, and again when they are)
        est = planner.estimate(known_exp, self._estimate_until(now))
        plan_exp = known_exp + est
        future = {("e", a.isoformat()): p for a, b, p, _ in exp if b > now}
        future.update({("i", a.isoformat()): p for a, b, p, adj in imp if b > now and not adj})
        cur = self.auto_plan or {}
        known = {tuple(x[0].split("|", 1)): x[1] for x in cur.get("rates") or []}
        same_rates = cur.get("blocks") == blocks and all(known.get(k) == v for k, v in future.items()) and \
            not any(k not in future and _parse_iso(k[1]) and _parse_iso(k[1]) + planner.HH > now for k in known)
        limit = [self.limit_reset, cap_min]
        # (charging before the high export times: the ones marked, set_export_rates; the ones gone
        # don't change the plan)
        highs = [x[0] for x in self.high_rates if _parse_iso(x[1]) > now] if mode == "before" else []
        how = {"mode": mode, "copies": copies, "ready": self.ready_time,  # (what else it's planned with)
               "charge_cap": charge_cap if mode == "before" else None,
               "discharge_cap": discharge_cap if mode == "before" else None}
        if cur and cur.get("key") == self._schedule_key() and same_rates and cur.get("v") == AUTO_PLAN_VERSION \
                and cur.get("limit") == limit and all(cur.get(k) == v for k, v in how.items()) \
                and [h for h in cur.get("highs") or [] if _parse_iso(h) + planner.HH > now] == highs:
            return False
        for a, b in (cur.get("charge") or []) + ((cur.get("changes") or {}).get("moved") or []):
            if _parse_iso(a) <= now < _parse_iso(b):
                return False  # (one of its blocks, or time it moved, is going: not changed under it; planned again after)
        # (also one of its blocks going as the schedule has it: e.g. you moved its start earlier, to
        # before now; its unplug still to come would otherwise be taken out, leaving it plugged in)
        mine_now = set((cur.get("changes") or {}).get("added") or [])
        if mine_now:
            starts = {t for t, e, skipped in self.timeline(now.date() - datetime.timedelta(days=1), 2)
                      if not skipped and e["action"] == "plug" and e["id"] in mine_now and t <= now}
            if any(a in starts and a <= now < b for a, b in self.windows(None, now)):
                return False
        loc, fixed = self._localize, _fixed(self.limit_reset)
        # What it's done already stays as it was: its blocks that are over (counted in their limit
        # day, or a day could go over) and the changes it made to the schedule before now
        # (the days counted in: the limit's, or any 24/48 hours of a rolling one, or midnight's)
        lo = now - limit_length(self.limit_reset) if (cap_min and not fixed) else period_start(now, fixed or "00:00", loc)
        done = [(a, b) for a, b in ((_parse_iso(x), _parse_iso(y)) for x, y in cur.get("charge") or [])
                if a and b and b <= now and b > lo]
        past_skips = [k for k in (cur.get("changes") or {}).get("skips") or [] if k in self.skips
                      and (self._skip_time(k) or now + datetime.timedelta(1)) <= now]
        # (times you un-skipped that it had skipped: not skipped by it again, for a day after)
        yours = [k for k in cur.get("yours") or [] if (self._skip_time(k) or now) > now - datetime.timedelta(days=1)]
        mine = set((cur.get("changes") or {}).get("added") or [])
        timed = sorted(((self._skip_time({"entry_id": e["id"], "date": e["date"]}), e) for e in self.entries
                        if e["id"] in mine and e.get("date")), key=lambda x: (x[0] is None, x[0] or now))
        past_added = [dict(e) for t, e in timed if t and t <= now]

        def undo():
            self._undo_auto(save=False)
            self.skips = self.skips + [k for k in past_skips if k not in self.skips]
            have = {e["id"] for e in self.entries}
            self.entries = self.entries + [dict(e) for e in past_added if e["id"] not in have]
            # (the saving sessions still to come taken out, and put in again once your slots have
            # given way below: they come first, and move only what of yours is left)
            return self._relayer_sessions(apply=False)
        relayer = undo()
        # (the days it plans: the daily limit's, e.g. 12:00 to 12:00, else midnight to midnight)
        per = (lambda t: period_start(t, fixed, loc)) if fixed else (lambda t: period_start(t, "00:00", loc))
        day_end = (lambda d: limit_end(d, fixed, loc)) if fixed else \
            (lambda d: period_start(d + datetime.timedelta(days=1, hours=1), "00:00", loc))
        changes = {"added": [e["id"] for e in past_added], "skips": list(past_skips)}
        notes = []
        self.auto_plan = {"blocks": blocks, "charge": [], "offpeak": [],
                          "changes": changes, "notes": notes, "planned_at": _iso_local(now), "info": info or {},
                          "rates_until": _iso_local(max((b for _, b, _, _ in known_exp), default=None)) if known_exp else None,
                          "rates": [[k[0] + "|" + k[1], v] for k, v in future.items()], "key": None,
                          "v": AUTO_PLAN_VERSION, "limit": limit, "yours": yours, "gave_way": [], "highs": highs, **how}
        # Your slots give way where the export rates are known (still to come): that time is its to plan
        hh = planner.HH
        known = []
        for x, _, _, _ in plan_exp:
            if x >= planner._ceil_hh(now):
                if known and known[-1][1] == x:
                    known[-1] = (known[-1][0], x + hh)
                else:
                    known.append((x, x + hh))
        self._auto_replace_yours(known, changes, notes, yours)
        if relayer:
            self._apply_sessions(*self._saving_args)
        cuts = self.block_windows(now.date() - datetime.timedelta(days=1), self._horizon_days() + 3)  # (your off-peak times)
        first = self._as_if_on_sources(FIRST_SOURCES)
        # The time before each saving session it charges for (Automations › Saving sessions), however
        # it's covered (its own charge, or a slot): kept as it is, as a charge that comes first
        keep = []
        for r in self.saving_sessions:
            pre = r.get("pre") or {}
            if pre.get("hours") and not pre.get("error") and _parse_iso(r["end"]) > now:
                sa = _parse_iso(r["start"])
                keep.append((sa - datetime.timedelta(minutes=int(round(pre["hours"] * 60))), sa))
        first = sorted(first + [(x, y) for x, y in minus(keep, first)])
        plugged = self._as_if_on()[0]  # (what's left: what's been, charges that come first, your slots kept)
        used = lambda d: window_minutes(plugged, d, day_end(d))
        fits = None
        if cap_min and not fixed:
            # A rolling limit (any 24 or 48 hours): a half hour only goes in where no such
            # stretch it's in goes over the limit
            length = limit_length(self.limit_reset)

            def fits(halves):
                wins = sorted(plugged + [(t, t + hh) for t in halves])
                merged = []
                for x, y in wins:
                    if merged and x <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], y))
                    else:
                        merged.append((x, y))
                lo2, hi = min(halves) - length, max(halves) + hh
                # every stretch of `length` with some of them in it: from each start and end around
                starts = {x for x, _ in merged if lo2 <= x < hi} | {y - length for _, y in merged if lo2 < y <= hi + length}
                starts |= {t + hh - length for t in halves} | set(halves)
                for a0 in starts:
                    if not any(a0 < t + hh and t < a0 + length for t in halves):
                        continue
                    if window_minutes(merged, a0, a0 + length) > cap_min:
                        return False
                return True
        if est:
            notes.append(f"From {est[0][0].strftime('%a %H:%M')}, planned on an estimate: the export rates for then "
                         "aren't out yet (Octopus: about 16:00), so the day before's are used, and it plans again when they are")
        res = planner.plan(imp, plan_exp, now, blocks, cuts, cap_min if fixed else None, per, used, busy=first, fits=fits,
                           plugged=plugged, own=lambda d: window_minutes(done, d, day_end(d)), copies=False,
                           mode=mode, highs=[_parse_iso(x) for x in highs], charge_cap=how["charge_cap"], discharge_cap=how["discharge_cap"])
        notes += res["notes"]
        charge = [(a, b) for a, b in res["charge"] if b > now]
        self._apply_auto(charge, changes)
        if cap_min and fixed and self.ready_time:
            self._auto_reset_splits(cap_min, changes)
        self.auto_plan["offpeak"] = [[_iso_local(a), _iso_local(b)] for a, b in res["offpeak"]]
        self.auto_plan["top_ups"] = [_iso_local(t) for t in res.get("top_ups") or []]  # (charged in a high export: drawn as charging)
        self.auto_plan["known_until"] = _iso_local(max((b for _, b in res["known"]), default=None)) if res["known"] else None
        # (with what it charged earlier in the days counted, for the page)
        charge_all = sorted(done + charge)
        self.auto_plan["charge"] = [[_iso_local(a), _iso_local(b)] for a, b in charge_all]
        self.auto_plan["key"] = self._schedule_key()
        if not self._previewing:
            self._log_auto_plan(blocks, cap_min, provider, charge, res, notes, per, day_end, fixed)
        self._save()
        return True

    _previewing = False

    def _to_local(self, t: datetime.datetime) -> datetime.datetime:
        """An aware time in local time, with the offset local time has then: e.g. rates in UTC for
        the day after the clocks change, which now's offset (fixed) would put an hour out."""
        utc = datetime.timezone.utc
        at = t.astimezone(utc).replace(tzinfo=None)
        hour = datetime.timedelta(hours=1)
        base = self._now().utcoffset() or datetime.timedelta(0)
        for off in (base, base - hour, base + hour):  # (local time is now's offset, or an hour either side)
            wall = at + off
            a, b = self._localize(wall), self._localize(wall.replace(fold=1))
            if a.utcoffset() < b.utcoffset():
                continue  # (a time the clocks skip, going forward: not one that happens)
            for x in (a, b):
                if x.utcoffset() == off and x.replace(tzinfo=None, fold=0) == wall:
                    return wall.replace(tzinfo=datetime.timezone(off))
        return t.astimezone(self._now().tzinfo)

    def _estimate_until(self, now: datetime.datetime) -> datetime.datetime:
        """How far ahead the export rates are estimated while they aren't out: to the end of tomorrow
        (estimate_ahead False: not at all)."""
        if not self.estimate_ahead:
            return now
        return self._localize(datetime.datetime.combine(now.date() + datetime.timedelta(days=2), datetime.time()))

    def _rates_local(self, rates: list) -> list:
        return [(self._to_local(a), self._to_local(b), p, adj) for a, b, p, adj in rates]

    @staticmethod
    def _copies(info: Optional[dict]) -> bool:
        """Whether the export rates are Predbat's, which fills the days not published yet with a
        copy of the day before (such a day isn't used); a supplier's own next day rates are, even
        when they're the same as today's."""
        return any(str(e).startswith("predbat.") for e in (info or {}).get("export") or [])

    def set_export_rates(self, exp: list, info: Optional[dict] = None, high: int = 0) -> bool:
        """The export rates read (src/auto_schedule.py parse_rates), as the page shows them on the
        schedule (Automations › Automatic schedule › Show export rates): those known (not a
        Predbat copy of the day before, nor a day all at one price), from yesterday on. And the
        high export half hours (Automations › Automatic schedule › High export half hours a day):
        each day's `high` highest export rates (the limit's day, e.g. 12:00 to 12:00, else
        midnight to midnight; all of it, gone or to come), marked on the schedule only: charging
        isn't kept out of them. Returns whether either changed."""
        from src import auto_schedule as planner
        now = self._now()
        lo = now - datetime.timedelta(days=1)
        known4 = planner.known_only(self._rates_local(exp), self._copies(info))
        est = {a for a, _, _, _ in planner.estimate(known4, self._estimate_until(now))}
        known = [(a, b, p) for a, b, p, _ in known4]
        both = known + [(a, b, p) for a, b, p, _ in planner.estimate(known4, self._estimate_until(now))]
        # ([start, end, p/kWh] and, for an estimate (tomorrow's not out yet: today's), True)
        rates = [[_iso_local(a), _iso_local(b), p] + ([True] if a in est else []) for a, b, p in both if b > lo]
        # (the rates seen are remembered for 2 days: by the morning, Home Assistant has only today's,
        # and the limit day from 12:00 yesterday has its afternoon in them)
        seen = {a: (a, b, p) for a, b, p in self._rates_seen if b > now - datetime.timedelta(days=2)}
        seen.update({a: (a, b, p) for a, b, p in known})
        self._rates_seen = sorted(seen.values())
        if self._rates_seen_saved() != self._rates_seen_was:
            self._save()  # (kept over a restart: the limit day from 12:00 yesterday needs its afternoon)
        fixed = _fixed(self.limit_reset) or "00:00"
        days: dict = {}
        for a, b, p in self._rates_seen + [x for x in both if x[0] in est]:  # (an estimate's highs too)
            days.setdefault(period_start(a, fixed, self._localize), []).append((a, b, p))
        high = max(0, min(int(high or 0), planner.MAX_BLOCKS))
        # (a day whose start isn't known, e.g. just after a restart in the morning, has gone partly
        # unseen: its highest can't be told, so none of it is marked)
        picked = sorted(x for d, halves in days.items() if min(h[0] for h in halves) <= d
                        for x in sorted(halves, key=lambda x: (-x[2], x[0]))[:high])
        highs = [[_iso_local(a), _iso_local(b), p] + ([True] if a in est else []) for a, b, p in picked if b > lo]
        if rates == self.export_rates and highs == self.high_rates:
            return False
        self.export_rates, self.high_rates = rates, highs
        return True

    def preview_auto_schedule(self, imp: list, exp: list, blocks: int, cap_min: Optional[int] = None,
                              provider: str = "your supplier", info: Optional[dict] = None, mode: str = "cheapest",
                              charge_cap: Optional[int] = None, discharge_cap: Optional[int] = None) -> bool:
        """While the automatic schedule is off: what it would do if it were turned on (its
        charging, the off-peak times, and which of your slots would give way), worked out on
        the schedule as it is and then put back, for the page (auto_preview). Worked out again
        when the rates, the schedule or the half hour change. Returns whether it changed."""
        if self.auto_plan is not None:
            changed = self.auto_preview is not None
            self.auto_preview, self._preview_key = None, None
            return changed
        now = self._now()
        key = repr((blocks, cap_min, self.limit_reset, self._schedule_key(), self.override_guards, mode, self.high_rates,
                    self._copies(info), self.ready_time, charge_cap, discharge_cap,
                    [(a.isoformat(), p) for a, b, p, _ in exp if b > now],
                    [(a.isoformat(), p, adj) for a, b, p, adj in imp if b > now],
                    now.replace(minute=now.minute // 30 * 30, second=0, microsecond=0).isoformat()))
        if key == self._preview_key:
            return False
        plain = (list, dict, set, tuple, str, int, float, bool, type(None))
        saved = {k: copy.deepcopy(v) for k, v in vars(self).items() if isinstance(v, plain)}
        self._previewing = True
        self._save = lambda *a, **k: None
        was = logger.disabled
        logger.disabled = True
        try:
            self.auto_plan = None
            self.sync_auto_schedule(imp, exp, blocks, cap_min, provider, info, mode, charge_cap, discharge_cap)
            p = self.auto_plan or {}
            preview = {k: p.get(k) for k in ("blocks", "charge", "offpeak", "notes", "rates_until", "info", "top_ups")}
            preview |= {"gave_way": p.get("gave_way") or [], "planned_at": _iso_local(now)}
        finally:
            logger.disabled = was
            for k in [k for k, v in vars(self).items() if isinstance(v, plain) and k not in saved]:
                delattr(self, k)
            for k, v in saved.items():
                setattr(self, k, v)
            self.__dict__.pop("_save", None)
            self.__dict__.pop("_previewing", None)
        self.auto_preview, self._preview_key = preview, key
        return True

    def _log_auto_plan(self, blocks: int, cap_min: Optional[int], provider: str, charge: list, res: dict,
                       notes: list, per, day_end, fixed) -> None:
        """The plan, as one block in the log: its charging, the off-peak
        times, its notes (your slots that gave way...) and each day's charging against the limit."""
        def runs(wins):
            out = []
            for a, b in sorted(wins):
                if out and out[-1][1] == a:
                    out[-1] = (out[-1][0], b)
                else:
                    out.append((a, b))
            return ", ".join(f"{a.strftime('%a %H:%M')}–{b.strftime('%H:%M')}" for a, b in out) or "none"

        def dur(m):
            return f"{m // 60}h{f' {m % 60:02d}m' if m % 60 else ''}"

        now = self._now()
        lines = [f"Automatic schedule planned ({blocks} half hours a day"
                 + (", before the high export times" if (self.auto_plan or {}).get("mode") == "before" else ", where exporting pays least")
                 + (f", {provider}'s limit {dur(cap_min)}" + (f" from {fixed}" if fixed else "") if cap_min else "") + "):",
                 f"  Charging: {runs(charge)}",
                 f"  High exports (marked): {runs((_parse_iso(h[0]), _parse_iso(h[1])) for h in self.high_rates if _parse_iso(h[1]) > now)}",
                 f"  Off-peak (import): {runs(res['offpeak'])}"]
        lines += [f"  Note: {n}" for n in notes]
        days = sorted({per(a) for a, _ in res.get("known") or []} | {per(now)})
        plugged = self._as_if_on()[0]
        until = day_end(days[-1])
        clip = lambda wins: [(max(a, now), min(b, until)) for a, b in wins if b > now and a < until]
        lines.append(f"  Coming first (auto plug-in, Charge now, car plugged in, before a saving session): "
                     f"{runs(clip(self._as_if_on_sources(FIRST_SOURCES)))}")
        lines.append(f"  Plugged in from now: {runs(clip(plugged))}")
        totals = []
        for d in days:
            e = day_end(d)
            m = int(window_minutes(plugged, d, e))
            totals.append(f"{d.strftime('%a %H:%M')}–{e.strftime('%a %H:%M')}: {dur(m)}"
                          + (f" of {dur(cap_min)}" + (" (OVER)" if m > cap_min else "") if cap_min else ""))
        lines.append("  Plugged in per day: " + "; ".join(totals))
        logger.info("\n".join(lines))

    def _auto_replace_yours(self, known: list, changes: dict, notes: list, yours: list = ()) -> None:
        """Your slots' time in `known` (the half hours still to come whose export rates are
        known) taken out: a slot all in it is skipped, one partly in it starts later or ends
        earlier (or unplugs for it and plugs back in after). Not the time of the charge before a
        saving session, nor a slot Charge now, auto plug-in or the car plugged in sensor joins
        (it ends with it), nor one going now before the next half hour. (One a saving session
        shortened to keep within the daily limit gives way like the rest. Nor one you un-skipped
        after it had skipped it: `yours`.)"""
        if not known:
            return
        now = self._now()
        start = now.date() - datetime.timedelta(days=1)
        # (the charge before each session, as it's covered: kept)
        known = minus(known, [(_parse_iso(w[0]), _parse_iso(w[1])) for w in
                              (self._pre_window(r) for r in self.saving_sessions) if w])
        stretches = self._stretches(start, self._horizon_days() + 3, self._active_entries(), self.skips)
        firsts = [t for t, e, skipped in self.timeline(start, self._horizon_days() + 3)
                  if not skipped and e.get("source") in (AUTO_SOURCE, CHARGE_NOW_SOURCE, CAR_PLUGGED_SOURCE)]

        def one_off(t, action, src):
            return validate_entry({"time": t.strftime("%H:%M"), "date": t.date().isoformat(), "action": action,
                                   **({"source": src} if src else {})})

        adds, trims, done = [], [], []
        for s in stretches:
            if s["end"] <= now or any(s["start"] <= t <= s["end"] for t in firsts):
                continue
            if {"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()} in yours or \
                    {"entry_id": s["unplug"]["id"], "date": s["end"].date().isoformat()} in yours:
                continue  # (you un-skipped it: yours to run)
            pieces = sorted(minus([(s["start"], s["end"])], known))
            if pieces == [(s["start"], s["end"])]:
                continue
            cut_start = not pieces or pieces[0][0] != s["start"]
            cut_end = not pieces or pieces[-1][1] != s["end"]
            if (cut_start and s["plug"].get("source") in AUTO_SOURCES) or (cut_end and s["unplug"].get("source") in AUTO_SOURCES):
                continue  # (not a charge's own end)
            label = f"{s['start'].strftime('%a %H:%M')}–{s['end'].strftime('%H:%M')}"
            if not pieces:
                trims += [{"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()},
                          {"entry_id": s["unplug"]["id"], "date": s["end"].date().isoformat()}]
                done.append(f"{label} skipped")
                continue
            if cut_start:
                trims.append({"entry_id": s["plug"]["id"], "date": s["start"].date().isoformat()})
                adds.append(one_off(pieces[0][0], "plug", s["plug"].get("source")))
            if cut_end:
                trims.append({"entry_id": s["unplug"]["id"], "date": s["end"].date().isoformat()})
                adds.append(one_off(pieces[-1][1], "unplug", s["unplug"].get("source")))
            # A plug-in inside the stretch (e.g. your 15:00 in a 13:00-17:00 run of slots) in the time
            # taken out is skipped too, or it'd plug back in with nothing to end it
            for t, e, skipped in self.timeline(start, self._horizon_days() + 3):
                if e["action"] == "plug" and not skipped and s["start"] < t < s["end"] \
                        and not any(x <= t < y for x, y in pieces):
                    trims.append({"entry_id": e["id"], "date": t.date().isoformat()})
            for (_, y), (x, _) in zip(pieces, pieces[1:]):  # (it unplugs for the time between, and back in after)
                adds += [one_off(y, "unplug", AUTO_SCHED_SOURCE), one_off(x, "plug", AUTO_SCHED_SOURCE)]
            done.append(f"{label} now " + ", ".join(f"{x.strftime('%H:%M')}–{y.strftime('%H:%M')}" for x, y in pieces))
        self.entries = self.entries + adds
        changes["added"] += [e["id"] for e in adds]
        claimed = self._claim_skips(trims, changes)
        changes["skips"] += [k for k in claimed if k not in changes["skips"]]
        # (what of yours gave way, apart from its blocks: kept while one of them is going)
        changes["gave"] = {"added": [e["id"] for e in adds], "skips": list(claimed)}
        if self.auto_plan is not None:
            self.auto_plan["gave_way"] = list(done)
        if done:
            notes.append("Your slots, where the export rates are known: " + "; ".join(done))

    def _as_if_on_sources(self, sources) -> list:
        """The plugged-in windows of the charges from `sources` alone: [(a, b)]."""
        now = self._now()
        ents = [e for e in without_fallbacks(self.entries) if e.get("source") in sources]
        return self.windows(ents, now, open_ended=True)

    def _apply_auto(self, charge: list, changes: dict) -> None:
        """Put the automatic schedule's blocks in, as one-offs, for the time nothing has the
        car plugged in already, each joined to a slot it meets."""
        now = self._now()
        stretches = self._stretches(now.date() - datetime.timedelta(days=1), self._horizon_days() + 3,
                                    self._active_entries(), self.skips)

        def one_off(t, action, src=AUTO_SCHED_SOURCE):
            return validate_entry({"time": t.strftime("%H:%M"), "date": t.date().isoformat(), "action": action,
                                   **({"source": src} if src else {})})

        added, skips = [], []
        for x, y in minus(list(charge), [(s["start"], s["end"]) for s in stretches]):
            if (y - x).total_seconds() < 60:
                continue
            before = next((s for s in stretches if s["end"] == x), None)
            after = next((s for s in stretches if s["start"] == y), None)
            if before is not None:
                skips.append({"entry_id": before["unplug"]["id"], "date": x.date().isoformat()})
            else:
                added.append(one_off(x, "plug"))
            if after is not None:
                skips.append({"entry_id": after["plug"]["id"], "date": y.date().isoformat()})
            elif not self._unplug_at(y):
                added.append(one_off(y, "unplug"))
        self.entries = self.entries + added
        changes["added"] += [e["id"] for e in added]
        changes["skips"] += [k for k in self._claim_skips(skips, changes) if k not in changes["skips"]]
        # An unplug that would run during a block (e.g. an "unplug at 09:30" time on its own), or
        # as it starts (e.g. a second slot ending where it joins on), is skipped while it runs
        inside = []
        for occ, e, skipped in self.timeline(now.date() - datetime.timedelta(days=1), self._horizon_days() + 3):
            if e["action"] == "unplug" and not skipped and any(a <= occ < b for a, b in charge):
                inside.append({"entry_id": e["id"], "date": occ.date().isoformat() if not e.get("date") else e["date"]})
        changes["skips"] += [k for k in self._claim_skips(inside, changes) if k not in changes["skips"]]

    def _auto_reset_splits(self, cap_min: int, changes: dict) -> None:
        """Its blocks (on their own, or joined to a slot) longer than the daily limit across its
        reset time (e.g. Octopus's 12:00) are split there, as your slots are: it unplugs and plugs
        straight back in, so the supplier sees a charge either side, each with its own ready time."""
        now = self._now()
        ents = self._active_entries()
        mine = [(s["start"], s["end"]) for s in self._stretches(now.date() - datetime.timedelta(days=1),
                                                                 self._horizon_days() + 1, ents, self.skips)
                if AUTO_SCHED_SOURCE in (s["plug"].get("source"), s["unplug"].get("source"))]
        loc = self._localize
        splits = [e for e in self._reset_splits(ents, self.skips, cap_min, one_off=True)
                  if any(a < loc(datetime.datetime.combine(_parse_date(e["date"]),
                                                           datetime.time(*_parse_hhmm(e["time"])))) < b for a, b in mine)]
        splits = [dict(e, source=AUTO_SCHED_SOURCE) for e in splits]
        if splits:
            self.entries = self.entries + splits
            changes["added"] += [e["id"] for e in splits]

    def auto_snapshot(self) -> Optional[dict]:
        if not self.auto_plan:
            return None
        p = self.auto_plan
        return {k: p.get(k) for k in ("blocks", "charge", "offpeak", "notes", "planned_at", "rates_until", "info", "gave_way", "top_ups")} | {
            "added": list(p["changes"].get("added") or []), "until": self._auto_until()}

    def _auto_until(self) -> Optional[str]:
        """Up to when the automatic schedule has decided the schedule (for the page: drawn in
        its colour up to then): the end of the last limit day it's planned (its reset, e.g.
        12:00 tomorrow), or with a rolling limit or none, the end of the rates it planned on."""
        p = self.auto_plan or {}
        last = [_parse_iso(y) for _, y in p.get("charge") or []] + ([_parse_iso(p["known_until"])] if p.get("known_until") else [])
        last = [t for t in last if t]
        if not last:
            return None
        t = max(last) - datetime.timedelta(seconds=1)
        # (the reset it was planned with: just after a restart, your supplier's isn't known yet)
        reset = self.limit_reset if self.limit_reset is not None else (p.get("limit") or [None])[0]
        fixed = _fixed(reset)
        if fixed:
            return _iso_local(limit_end(period_start(t, fixed, self._localize), fixed, self._localize))
        return p.get("rates_until") or _iso_local(max(last))

    def saving_snapshot(self) -> list:
        """For the page: each session, what it changed (skips, and the one-offs it
        added: moved slots and the charge before it)."""
        out = []
        for r in self.saving_sessions:
            pre = (r.get("pre") or {}).get("changes") or {}
            out.append({"id": r["id"], "start": r["start"], "end": r["end"], "summary": r.get("summary"),
                        "parts": r.get("parts") or [],  # the overlapping ones it's made of, each as it runs
                        "notes": r.get("notes") or [],
                        "skips": (r["changes"].get("skips") or []) + (pre.get("skips") or []),
                        "moved": r["changes"].get("added") or [], "before": pre.get("added") or [],
                        "moved_parts": r["changes"].get("parts") or [],
                        # the time it charges for before it (however it's covered), for the page
                        "pre_window": self._pre_window(r)})
        return out

    @staticmethod
    def _pre_window(r: dict) -> Optional[list]:
        pre = r.get("pre") or {}
        if not pre.get("hours") or pre.get("error") or pre.get("deleted"):
            return None
        sa = _parse_iso(r["start"])
        return [_iso_local(sa - datetime.timedelta(minutes=int(round(pre["hours"] * 60)))), r["start"]]

    def set_skips(self, times: list, skip: bool = True, daily_cap_min: Optional[int] = None,
                  provider: str = "your supplier", allow_over: bool = False) -> None:
        """Skip (or un-skip) several times at once, e.g. a slot's plug-in and
        unplug: [{"entry_id", "date"}, ...]. All or nothing. With daily_cap_min
        (Octopus, while the schedule sets its ready time), a change that takes a
        day over it is refused, as when saving the schedule (unless allow_over)."""
        if not isinstance(times, list) or not times:
            raise ValueError("Say which times to skip")
        keys = []
        for t in times:
            if not isinstance(t, dict):
                raise ValueError("Each time to skip must be {entry_id, date}")
            entry_id, date = str(t.get("entry_id") or ""), t.get("date")
            entry = next((e for e in self.entries if e["id"] == entry_id), None)
            if entry is None:
                raise ValueError("No such schedule time (save the schedule first)")
            day = _parse_date(date)
            if not any(True for _ in self._occurrences(entry, day, 1)):
                raise ValueError(f"That time doesn't run on {date}")
            keys.append(({"entry_id": entry_id, "date": day.isoformat()}, entry))
        if daily_cap_min and self.ready_time and not self.override_guards and not allow_over:
            new = list(self.skips)
            for key, _ in keys:
                new = [k for k in new if k != key] + ([key] if skip else [])
            running = without_fallbacks(self.entries)
            if self._makes_worse(daily_cap_min, (running, self.skips), (running, new)):
                self.check_daily_cap(running, daily_cap_min, provider, new)  # raises, with the details
        if not skip:
            new = list(self.skips)
            for key, _ in keys:
                new = [k for k in new if k != key]
            self.check_blocks(self._active_entries() if self.schedule_enabled else self.entries, self.blocks, new)  # e.g. an off-peak time added while it was skipped
        if not skip:
            self._yours_unskipped([k for k, _ in keys])
        for key, entry in keys:
            self.skips = [k for k in self.skips if k != key] + ([key] if skip else [])
            logger.info("Schedule: %s the %s at %s on %s", "skipping" if skip else "no longer skipping",
                        "plug-in" if entry["action"] == "plug" else "unplug", entry["time"], key["date"])
        self._prune()
        self._save()

    def _yours_unskipped(self, keys: list) -> None:
        """Times you un-skipped yourself: no longer anything else's skip (or one taken out would
        come back, e.g. the automatic schedule keeping the skips it made before now), and one the
        automatic schedule had skipped is yours to run: it's not skipped by it again (planned
        again around it)."""
        owners = [(self.auto_plan or {}).get("changes") or {}]
        for r in self.saving_sessions:
            owners += [r.get("changes") or {}, (r.get("pre") or {}).get("changes") or {}]
        owners += [e["trims"] for e in self.entries if e.get("trims")]
        for ch in owners + [((self.auto_plan or {}).get("changes") or {}).get("gave") or {}]:
            if ch.get("skips"):
                ch["skips"] = [k for k in ch["skips"] if k not in keys]
        if self.auto_plan is not None:
            yours = self.auto_plan.setdefault("yours", [])
            yours += [dict(k) for k in keys if k not in yours]
            self.auto_plan["key"] = None  # (planned again)

    def cancel_charge(self, source: str = CHARGE_NOW_SOURCE) -> int:
        """Take a charge (Charge now, by default) that's going or still to come out of the
        schedule: its plug-in and unplug go, and what it trimmed or skipped is put back.
        Returns how many times were taken out (0: none to cancel)."""
        now = self._now()
        times = sorted(((next(self._occurrences(e, _parse_date(e["date"]), 1)), e) for e in self.entries
                        if e.get("source") == source and e.get("date")), key=lambda x: (x[0], x[1]["action"] == "plug"))
        cancel = set()
        for i, (occ, e) in enumerate(times):
            if occ > now:
                cancel.add(e["id"])
            elif e["action"] == "plug":
                # going now: until its unplug
                end = next(((o, x) for o, x in times[i + 1:] if x["action"] == "unplug"), None)
                if end is None or end[0] > now:
                    cancel.add(e["id"])
        if not cancel:
            return 0
        self.set_schedule(entries=[e for e in self.entries if e["id"] not in cancel])
        logger.info("Schedule: %s cancelled", SOURCE_NAMES.get(source, source))
        return len(cancel)

    def next_slots(self, plugged_in: bool = False, count: int = NEXT_SLOTS, lead_min: Optional[int] = None) -> list:
        """The next `count` slots the schedule plugs in for, for Predbat's rate
        overrides: [{"date", "start", "end"}] (YYYY-MM-DD, HH:MM:SS local; an end at
        or before the start is the next day). Slots that meet are one (as they're one
        charge), but not where the daily limit resets (two charges). Not one that
        starts within `lead_min` (default: slot_lead()) minutes, so whatever uses them
        can replan in case it doesn't go ahead, nor while plugged in one before the next
        unplug: this plug-in runs into it. One over a day long is split at midnight (an override is a day at most)."""
        now = self._now()
        timeline = self.timeline(now.date() - datetime.timedelta(days=1), self._horizon_days() + 2)
        through = self.carry_ons(timeline)
        stretches = plugged_windows([(t, e["action"] == "plug") for t, e, skipped in timeline
                                     if not skipped and t not in through])
        upto = (self.next_unplug(now) if plugged_in else None) or now
        lead = datetime.timedelta(minutes=self.slot_lead() if lead_min is None else lead_min)
        out = []
        for a, b in stretches:
            if a <= now + lead or a < upto:
                continue
            while b - a > datetime.timedelta(days=1):
                midnight = self._localize(datetime.datetime.combine(a.date() + datetime.timedelta(days=1),
                                                                    datetime.time()))
                out.append((a, midnight))
                a = midnight
            out.append((a, b))
        return [{"date": a.date().isoformat(), "start": a.strftime("%H:%M:%S"), "end": b.strftime("%H:%M:%S")}
                for a, b in out[:count]]

    def upcoming(self, days: int = 7) -> list:
        """The next times over `days` days, for the page."""
        now = self._now()
        out = []
        for occ, entry, skipped in self.timeline(now.date(), days + 1):
            if now < occ <= now + datetime.timedelta(days=days):
                out.append({"time": _iso_local(occ), "date": occ.date().isoformat(), "entry_id": entry["id"],
                            "action": entry["action"], "skipped": skipped, "one_off": bool(entry.get("date"))})
        return out[:30]

    def due(self) -> list[dict]:
        """Entries whose time has come since the last check. A time is run if
        it's noticed within CATCH_UP_S; an unplug also later, if nothing's come
        after it (e.g. the loop was busy re-plugging), so it's never left plugged in. The first check after a restart
        runs only an unplug missed while the add-on was stopped, if it's still
        the schedule's latest time (a plug-in is done at start-up: see
        automation_loop); with no record of the last check, nothing."""
        now = self._now()
        prev, self._last_check = self._last_check, now
        first, self._first_check = self._first_check, False
        if first and self._missed_since is not None:
            prev = datetime.datetime.fromtimestamp(self._missed_since, tz=now.tzinfo)
        if prev is None or now <= prev:
            return []  # (with the schedule off, only auto plug-in times run: see timeline)
        start = prev.date() - datetime.timedelta(days=1)
        span = (now.date() - start).days + 1
        due = []
        timeline = self.timeline(start, span)
        plugs_at = {occ for occ, e, skipped in timeline if e["action"] == "plug" and not skipped}
        through = self.carry_ons(timeline)
        latest = max(((occ, n) for n, (occ, e, skipped) in enumerate(timeline) if occ <= now and not skipped),
                     default=None)
        for n, (occ, entry, skipped) in enumerate(timeline):
            late = (now - occ).total_seconds() > CATCH_UP_S
            if late and (entry["action"] != "unplug" or skipped or latest is None or latest[1] != n):
                continue  # a late plug-in, or a late unplug something's come after: leave it
            if first and entry["action"] == "plug":
                continue  # after a restart, plugging in is the start-up check's job
            if first and entry["action"] == "unplug" and any(
                    occ <= o <= now and e["action"] == "plug" and not s for o, e, s in timeline):
                continue  # ... and a missed unplug a plug-in came after (or at) is too: it'd leave it unplugged
            if prev < occ <= now:
                if skipped:
                    logger.info("Schedule: %s at %s skipped",
                                "plug-in" if entry["action"] == "plug" else "unplug", entry["time"])
                elif occ in through:
                    # One slot ends as the next starts: it carries on plugged in, one charge
                    due.append(dict(entry, carry_on=True))
                elif entry["action"] == "unplug" and occ in plugs_at:
                    # One slot ends as the next starts where the daily limit resets:
                    # unplug straight away (no waiting for the supplier) and plug back
                    # in for the next, so it's two charges, each with its own ready
                    # time (e.g. Octopus: 6 hours before a 12:00 reset and 6 after)
                    due.append(dict(entry, back_to_back=True))
                else:
                    due.append(entry)
        if prev.date() != now.date() and self._prune():  # tidy up once a day
            self._save()
        return due

    def block_starts_due(self) -> list:
        """The weekly off-peak times that have started since the last look
        (within CATCH_UP_S): [start]. The first look after a restart finds none."""
        now = self._now()
        prev, self._block_check = self._block_check, now
        if prev is None or now <= prev:
            return []
        prev = max(prev, now - datetime.timedelta(seconds=CATCH_UP_S))
        return [a for a, _ in self.block_windows(prev.date(), 2, sessions=False) if prev < a <= now]

    def last_event(self, at: datetime.datetime) -> Optional[tuple[datetime.datetime, dict]]:
        """The schedule's most recent enabled time at or before `at` (within a week)."""
        best = None
        for occ, entry, skipped in self.timeline(at.date() - datetime.timedelta(days=7), 9):
            if occ <= at and not skipped:
                best = (occ, entry)  # in order, so the last one wins
        return best

    def in_plug_window(self, at: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
        """When the plugged-in stretch we're in started, if the schedule (on)
        has the car plugged in at `at` (default now); else None."""
        if not self.schedule_enabled:
            return None
        last = self.last_event(at or self._now())
        return last[0] if last and last[1]["action"] == "plug" else None

    def in_charge_window(self, at: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
        """When the car slot (a charge: auto plug-in, Charge now, the car plugged in sensor) we're
        in started, if one has the car plugged in at `at` (default now), on its own (as with the
        schedule off: its own end, not a slot it joined); else None."""
        at = at or self._now()
        charges = [e for e in self.entries if e.get("source") in AUTO_SOURCES]
        best = None
        for occ, entry, skipped in self.timeline(at.date() - datetime.timedelta(days=7), 9, charges):
            if occ <= at and not skipped:
                best = (occ, entry)
        return best[0] if best and best[1]["action"] == "plug" else None

    def carry_ons(self, timeline: list) -> set:
        """The times in `timeline` where one slot ends as the next starts and it
        just carries on plugged in, as one charge (the ready time is the last
        one's end): all of them, but where your supplier's daily limit resets
        (e.g. Octopus's 12:00), where it unplugs and plugs back in so the
        supplier sees a charge either side (see _reset_splits)."""
        plugs = {occ for occ, e, skipped in timeline if e["action"] == "plug" and not skipped}
        unplugs = {occ for occ, e, skipped in timeline if e["action"] == "unplug" and not skipped}
        split_at = _fixed(self.limit_reset)
        return {t for t in plugs & unplugs if t.strftime("%H:%M") != split_at}

    def next_unplug(self, after: datetime.datetime) -> Optional[datetime.datetime]:
        """The schedule's next unplug after `after` (within a week). Slots that
        meet are one charge, ending at the last one's end (see carry_ons)."""
        timeline = self.timeline(after.date(), 8)
        through = self.carry_ons(timeline)
        for occ, entry, skipped in timeline:
            if occ > after and entry["action"] == "unplug" and not skipped and occ not in through:
                return occ
        return None

    def next_action(self) -> Optional[dict]:
        """The next time that runs: the schedule's (on), or with it off a car slot's (auto
        plug-in, Charge now, the car plugged in sensor), which run either way. "car_slot": true
        for one of those."""
        now = self._now()
        if self.plug_at is not None and self.in_plug_window() is not None:
            # A plug-in waiting out its random start delay comes first
            at = datetime.datetime.fromtimestamp(self.plug_at, tz=now.tzinfo)
            return {"time": _iso_local(at), "action": "plug", "entry_id": None, "car_slot": False, "delayed": True}
        timeline = self.timeline(now.date(), self._horizon_days())
        through = self.carry_ons(timeline)
        for occ, entry, skipped in timeline:
            if occ > now and not skipped and occ not in through:
                return {"time": _iso_local(occ), "action": entry["action"], "entry_id": entry["id"],
                        "car_slot": entry.get("source") in AUTO_SOURCES}
        return None

    # --- the supplier's plan vs the schedule ------------------------------------

    def update_plan_check(self, plan: Optional[dict], plugged_in: bool) -> Optional[dict]:
        """plan: {"found", "provider", "slots", "supplier_ready"} from HaLink.supplier_plan.
        Checked only with Force schedule on supplier, while the schedule has the
        car plugged in, up to the ready time set for this stretch."""
        result = None
        since = self.in_plug_window() if self.ready_time and plugged_in else None
        end = self.next_unplug(self._now()) if since is not None else None
        if end is not None and plan and plan.get("found"):
            r = self.ready_status or {}
            ready_at = _parse_iso(r.get("ready_at"))
            set_at = _parse_iso(r.get("at"))
            # The ready time set for this stretch (at its plug-in)
            this_stretch = (not r.get("error") and set_at is not None and set_at >= since - datetime.timedelta(minutes=1))
            until = ready_at if this_stretch and ready_at is not None and since < ready_at <= end else end
            settle_from = max(since, set_at) if this_stretch else since
            check_at = settle_from + datetime.timedelta(seconds=SETTLE_S)
            if self._now() < check_at:
                result = {"status": "waiting", "start": _iso_local(since), "end": _iso_local(end),
                          "until": _iso_local(until), "check_at": _iso_local(check_at), "problems": []}
            else:
                result = check_plan(plan.get("slots") or [], since, end, until,
                                    ready_set=r.get("ready") if this_stretch else None,
                                    supplier_ready=plan.get("supplier_ready"),
                                    provider=plan.get("provider") or "Your supplier")
            result["provider"] = plan.get("provider")
        problems = tuple(result["problems"]) if result else ()
        if problems != self._plan_problems:
            for text in problems:
                if text not in self._plan_problems:
                    logger.warning("Supplier plan doesn't match the schedule: %s", text)
            if not problems and result and result["status"] != "waiting":
                logger.info("Supplier plan matches the schedule again")
            self._plan_problems = problems
        self.plan_check = result
        return result

    # --- re-plug -------------------------------------------------------------

    def replug_due(self, plugged_in: bool, in_session: bool, connected: bool,
                   scheduled: Optional[bool] = None, slot_now: Optional[bool] = None) -> bool:
        """Call regularly; True when it's time to re-plug.

        scheduled: your supplier has a charge slot planned or running (None:
        unknown, so only a session counts): no re-plug while it has. Otherwise
        the wait is the set minutes from plugging in. slot_now: one is running
        now (shown on the page only)."""
        now = self._clock()
        self.scheduled = bool(scheduled) and plugged_in and not in_session
        self.slot_now = bool(slot_now) and plugged_in and not in_session
        if self.replugging:
            return False
        if in_session or not plugged_in or scheduled:
            # A session started or was scheduled, or the car was unplugged:
            # start afresh (if a planned slot is dropped, the wait starts then)
            self.attempts_used = 0
            self.gave_up = False
            self.waiting_since = None
            return False
        if not connected or not self.replug["enabled"]:
            self.waiting_since = None  # the wait restarts once back online / on
            return False
        if self.waiting_since is None:
            self.waiting_since = now
            return False
        if now < self._replug_at():
            return False
        unplug = self.next_unplug(self._now())
        off = self.replug.get("off_s", REPLUG_WAIT_S)
        if unplug is not None and (unplug - self._now()).total_seconds() <= off + REPLUG_UNPLUG_MARGIN_S:
            return False  # the schedule unplugs before it'd be plugged back in: leave it to that
        if self.attempts_used >= self.replug["attempts"]:
            if not self.gave_up:
                self.gave_up = True
                logger.warning(
                    "Still no session %d min after plugging in, after %d re-plug(s): giving up "
                    "until the car is next unplugged or a session starts",
                    self.replug["after_min"], self.attempts_used,
                )
            return False
        return True

    def _replug_at(self) -> float:
        """When the wait for a session runs out (waiting_since must be set)."""
        return self.waiting_since + self.replug["after_min"] * 60

    async def run_replug(self, unplug: Callable[..., Awaitable[None]], plug: Callable[..., Awaitable[None]],
                         wait_s: Optional[float] = None) -> None:
        self.attempts_used += 1
        logger.warning(
            "No session from the supplier %d min after plugging in: re-plugging (try %d of %d)",
            self.replug["after_min"], self.attempts_used, self.replug["attempts"],
        )
        self.replugging = True
        began = self._now()
        try:
            await unplug(source="auto re-plug")
            await asyncio.sleep(self.replug.get("off_s", REPLUG_WAIT_S) if wait_s is None else wait_s)
            unplug_at = self.next_unplug(began)
            if unplug_at is not None and unplug_at <= self._now():
                # The schedule unplugged meanwhile (e.g. the off time ran past it): stay unplugged
                logger.info("Re-plug: the schedule's unplug at %s came while unplugged, so not plugging back in",
                            unplug_at.strftime("%H:%M"))
                return
            await plug(source="auto re-plug")
        finally:
            self.replugging = False
            self.last_replug = self._clock()
            self.waiting_since = self._clock()

    # --- status --------------------------------------------------------------

    def replug_status(self) -> dict:
        r = self.replug
        if not r["enabled"]:
            status = "off"
        elif self.replugging:
            status = "replugging"
        elif self.gave_up:
            status = "gave_up"
        elif self.scheduled:
            status = "scheduled"
        elif self.waiting_since is not None:
            status = "waiting"
        else:
            status = "idle"
        next_at = None
        if status == "waiting" and self.attempts_used < r["attempts"]:
            next_at = self._replug_at()
        return {
            **r,
            "status": status,
            "attempts_used": self.attempts_used,
            "waiting_since": _iso_from_epoch(self.waiting_since),
            "next_replug_at": _iso_from_epoch(next_at),
            "last_replug": _iso_from_epoch(self.last_replug),
        }

    def snapshot(self) -> dict:
        return {
            "timezone": timezone_name(),
            "now": _iso_local(self._now()),
            "schedule": {
                "enabled": self.schedule_enabled,
                "entries": self.entries,
                "next": self.next_action(),
                "last_run": self.last_run,
                "ready_time": self.ready_time,
                "ready_status": self.ready_status,
                "auto_plug_last": self.auto_plug_last,
                "populate_last": self.populate_last,
                "one_off_runs": self.one_off_runs(),
                "unplug_wait_s": self.unplug_wait_s,
                "start_delay": self.start_delay,
                "plug_pending": _iso_from_epoch(self.plug_at),
                "override_guards": self.override_guards,
                "blocks": self.blocks,
                "saving_sessions": self.saving_snapshot(),
                "saving_past": self.saving_past,  # (from the calendar: the Sessions tab's week)
                "saving_block": self.saving_block,
                "auto_plan": self.auto_snapshot(),
                "auto_preview": self.auto_preview if self.auto_plan is None else None,
                "export_rates": self.export_rates,
                "high_rates": self.high_rates,
                "plan_check": self.plan_check,
                "skips": self.skips,
                "upcoming": self.upcoming(),
            },
            "replug": self.replug_status(),
        }

    def publish(self, shared_state) -> None:
        shared_state.schedule_enabled = self.schedule_enabled
        shared_state.schedule_next = self.next_action()
        shared_state.replug = self.replug_status()
        shared_state.unplug_pending = _iso_from_epoch(self.unplug_at)
        shared_state.plug_pending = _iso_from_epoch(self.plug_at)
        shared_state.plan_check = self.plan_check
        shared_state.next_slots = self.next_slots(plugged_in=bool(getattr(shared_state, "plugged_in", False)))


async def apply_ready_time(automation: "Automation",
                           set_ready_time: Callable[[datetime.datetime], Awaitable[dict]],
                           log_errors: bool = True, retry: bool = False) -> dict:
    """At a scheduled plug-in: ready time = the schedule's next unplug. If your
    supplier (or its integration) fails to take it, it's tried again a few times
    while it's still plugged in for that unplug (READY_RETRY_MIN; `retry`: one of those),
    unless that can't help ("permanent": no supplier found, one with no ready time
    such as E.ON Next, or no ready time it accepts before the unplug)."""
    now = automation._now()
    unplug = automation.next_unplug(now)
    tries = (automation.ready_retry or {}).get("tries", 0) if retry else 0
    automation.ready_retry = None
    if unplug is None:
        result = {"error": "There's no unplug in the schedule after this plug-in"}
    else:
        try:
            result = await set_ready_time(unplug)
        except Exception as err:
            result = {"unplug": unplug.isoformat(timespec="minutes"), "error": str(err)}
    if result.get("error") and not result.get("permanent") and unplug is not None and tries < len(READY_RETRY_MIN):
        wait = READY_RETRY_MIN[tries]
        automation.ready_retry = {"tries": tries + 1, "at": automation._clock() + wait * 60, "unplug": unplug.isoformat()}
        result = {**result, "retry_at": _iso_from_epoch(automation.ready_retry["at"])}
        if log_errors:
            logger.warning("Ready time not set: %s (trying again in %d min)", result["error"], wait)
    elif result.get("error") and log_errors:
        logger.warning("Ready time not set: %s", result["error"])
    elif retry:
        logger.info("Ready time set on try %d: %s", tries + 1, result.get("ready") or unplug)
    automation.ready_status = {**result, "at": _iso_from_epoch(automation._clock())}
    return result


async def auto_plug_charge(automation: "Automation", hours: float,
                           set_ready_time: Optional[Callable[[datetime.datetime], Awaitable[dict]]] = None,
                           ready_times: Optional[list] = None, cap_min: Optional[int] = None,
                           provider: str = "your supplier", source: str = AUTO_SOURCE,
                           dry_run: bool = False) -> dict:
    """Auto plug-in (or Charge now) just plugged in: plan the charge and set
    the ready time. dry_run: only work out what would be done."""
    plan = automation.plan_auto_plug(int(round(hours * 60)), ready_times, cap_min, provider, source, dry_run)
    if dry_run:
        return plan
    if set_ready_time is not None:
        await apply_ready_time(automation, set_ready_time)
    return plan


async def automation_loop(
    automation: Automation,
    shared_state,
    plug: Callable[[], Awaitable[None]],
    unplug: Callable[[], Awaitable[None]],
    tick_s: float = TICK_S,
    scheduled: Optional[Callable[[], Optional[bool]]] = None,
    slot_now: Optional[Callable[[], Optional[bool]]] = None,
    set_ready_time: Optional[Callable[[datetime.datetime], Awaitable[dict]]] = None,
    supplier_plan: Optional[Callable[[], Optional[dict]]] = None,
) -> None:
    """Run the schedule and re-plug for the life of the add-on.

    scheduled: whether your supplier has a charge slot planned for later,
    slot_now: whether one is running now (None if its integration isn't
    there); both also shown as the add-on's status.
    supplier_plan: the supplier's slots and ready time, for the plan check."""
    started = automation._clock()
    ready_pending = False  # a start-up plug-in's ready time, until HA is there to take it
    first = True
    while True:
        try:
            shared_state.scheduled = scheduled() if scheduled is not None else None
            shared_state.slot_now = slot_now() if slot_now is not None else None
            if first:
                first = False
                automation.turned_on = automation.turned_off = False  # (start-up does the same check, just below)
                # Inside a slot of the schedule (on), or a car slot (which runs with it off too)
                since = automation.in_plug_window() or automation.in_charge_window()
                waiting = automation.plug_at is not None and automation._clock() < automation.plug_at
                if since is None:
                    automation.plug_at = None
                elif not shared_state.plugged_in and not waiting:
                    automation.plug_at = None
                    logger.info("Started inside a scheduled plug-in (since %s): plugging in", since.strftime("%a %H:%M"))
                    automation.last_run = {"entry_id": None, "action": "plug",
                                           "timestamp": _iso_from_epoch(automation._clock()), "startup": True}
                    await plug(source="schedule")
                    ready_pending = automation.ready_time and set_ready_time is not None
                elif shared_state.plugged_in:
                    automation.plug_at = None
                    # Still plugged in for it: the ready time is set again, as it may not have been
                    # (it failed before the restart, e.g. at its plug-in; the retries aren't kept), or
                    # it was restarted across the start of this slot (where two slots meet)
                    ready_pending = automation.ready_time and set_ready_time is not None
            if ready_pending:
                # HA (or the supplier's sensor) may not be there yet just after start-up
                last_try = automation._clock() - started >= STARTUP_READY_S
                result = await apply_ready_time(automation, set_ready_time, log_errors=last_try)
                ready_pending = bool(result.get("error")) and not last_try
            # The ready time failed to set: again, while it's plugged in for the same unplug
            rr = automation.ready_retry
            if rr and automation._clock() >= rr["at"]:
                nxt = automation.next_unplug(automation._now())
                if (automation.ready_time and set_ready_time is not None and shared_state.plugged_in
                        and nxt is not None and nxt.isoformat() == rr["unplug"]):
                    await apply_ready_time(automation, set_ready_time, retry=True)
                else:
                    automation.ready_retry = None
            # The schedule changed while plugged in for it (its unplug moved: a slot edited or
            # dragged, a saving session, the hours over removed...): the ready time follows
            rs = automation.ready_status
            if (automation.ready_time and set_ready_time is not None and shared_state.plugged_in
                    and not ready_pending and automation.ready_retry is None and rs and rs.get("unplug")
                    and (automation.in_plug_window() or automation.in_charge_window()) is not None):
                nxt = automation.next_unplug(automation._now())
                was = _parse_iso(rs["unplug"])
                if nxt is not None and was is not None and nxt.replace(second=0, microsecond=0) != was.replace(second=0, microsecond=0):
                    logger.info("Schedule: the unplug is now %s (was %s): setting the ready time again",
                                nxt.strftime("%a %H:%M"), was.strftime("%a %H:%M"))
                    await apply_ready_time(automation, set_ready_time)
            if automation.turned_on:
                # The schedule was just turned on: inside a slot (or a charge joined with one),
                # plugged in to its end, as at start-up; the ready time set to that end
                automation.turned_on = False
                since = automation.in_plug_window()
                if since is not None:
                    if not shared_state.plugged_in:
                        logger.info("Schedule turned on inside a plug-in (since %s): plugging in", since.strftime("%a %H:%M"))
                        await plug(source="schedule")
                    if automation.ready_time and set_ready_time is not None:
                        await apply_ready_time(automation, set_ready_time)
            if automation.turned_off:
                # The schedule was turned off while it had the car plugged in: unplugged, unless
                # a car slot is going now (separated from the slot it joined: on to its own end)
                automation.turned_off = False
                if shared_state.plugged_in:
                    since = automation.in_charge_window()
                    if since is None:
                        logger.info("Schedule turned off: unplugging (the schedule had it plugged in)")
                        automation.unplug_at = None
                        await unplug(source="schedule off")
                    else:
                        logger.info("Schedule turned off during a car slot (since %s): it carries on to its own end",
                                    since.strftime("%a %H:%M"))
                        if automation.ready_time and set_ready_time is not None:
                            await apply_ready_time(automation, set_ready_time)  # the charge's own end
            due = automation.due()
            # Where two slots meet it carries on: no random delay for the plug-in there
            meets = {e["time"] for e in due if e.get("back_to_back") or e.get("carry_on")}
            for entry in due:
                if entry.get("carry_on"):
                    # Where two slots meet: it carries on plugged in (the plug-in only
                    # if it isn't, e.g. unplugged by hand), the ready time the last one's end
                    automation.plug_at = None
                    if entry["action"] == "unplug":
                        logger.info("Schedule: one slot ends and the next starts at %s: carrying on plugged in",
                                    entry["time"])
                        continue
                    automation.unplug_at = None
                    automation.last_run = {"entry_id": entry["id"], "action": "plug",
                                           "timestamp": _iso_from_epoch(automation._clock())}
                    if not shared_state.plugged_in:
                        logger.info("Schedule: plugging in at %s", entry["time"])
                        await plug(source="schedule")
                    if automation.ready_time and set_ready_time is not None:
                        await apply_ready_time(automation, set_ready_time)
                    continue
                if entry["action"] == "unplug" or entry.get("back_to_back"):
                    automation.plug_at = None  # a plug-in still waiting out its delay is overtaken
                elif entry["time"] not in meets and not shared_state.plugged_in:
                    # (already plugged in, e.g. by hand: nothing to wait for, just the ready time)
                    delay = automation.start_delay_s(entry, automation._now())
                    if delay >= 1:
                        automation.plug_at = automation._clock() + delay
                        logger.info("Schedule: plug-in at %s, after a random delay of %d min %02d s (at %s)",
                                    entry["time"], delay // 60, delay % 60,
                                    datetime.datetime.fromtimestamp(automation.plug_at).strftime("%H:%M:%S"))
                        continue
                automation.last_run = {
                    "entry_id": entry["id"], "action": entry["action"],
                    "timestamp": _iso_from_epoch(automation._clock()),
                }
                if entry.get("back_to_back"):
                    # Where two slots meet: a fresh charge for the next one
                    automation.unplug_at = None
                    off = automation.replug.get("off_s", REPLUG_WAIT_S)
                    logger.info("Schedule: one slot ends and the next starts at %s: unplugging, "
                                "plugging back in after %d s", entry["time"], off)
                    await unplug(source="schedule")
                    await asyncio.sleep(off)
                elif entry["action"] == "plug":
                    automation.unplug_at = None  # a waiting unplug is overtaken
                    logger.info("Schedule: plugging in at %s", entry["time"])
                    await plug(source="schedule")
                    if automation.ready_time and set_ready_time is not None:
                        await apply_ready_time(automation, set_ready_time)
                elif ((automation.ready_time or entry.get("source") in AUTO_SOURCES)
                      and automation.unplug_wait_s and shared_state.transaction_id is not None):
                    # Give the supplier a chance to end the session itself
                    automation.unplug_at = automation._clock() + automation.unplug_wait_s
                    logger.info("Schedule: unplug at %s, waiting up to %d s for the supplier to stop the session",
                                entry["time"], automation.unplug_wait_s)
                else:
                    logger.info("Schedule: unplugging at %s", entry["time"])
                    await unplug(source="schedule")
            if automation.plug_at is not None:
                # A plug-in waiting out its random delay: not if the slot's over (or skipped, or
                # the schedule's off) or it's plugged in already; with the delay turned off, now
                if automation.in_plug_window() is None:
                    logger.info("Schedule: the slot ended during the random delay: not plugging in")
                    automation.plug_at = None
                elif shared_state.plugged_in:
                    # Plugged in meanwhile (by hand, Home Assistant...): the ready time is still this slot's
                    automation.plug_at = None
                    if automation.ready_time and set_ready_time is not None:
                        await apply_ready_time(automation, set_ready_time)
                elif automation._clock() >= automation.plug_at or not automation.start_delay["enabled"]:
                    automation.plug_at = None
                    automation.unplug_at = None
                    automation.last_run = {"entry_id": None, "action": "plug",
                                           "timestamp": _iso_from_epoch(automation._clock()), "delayed": True}
                    logger.info("Schedule: plugging in (after the random delay)")
                    await plug(source="schedule")
                    if automation.ready_time and set_ready_time is not None:
                        await apply_ready_time(automation, set_ready_time)
            for start in automation.block_starts_due():
                # In case it's plugged in by hand (or from Home Assistant): nothing
                # charges in an off-peak time
                if shared_state.plugged_in:
                    automation.unplug_at = None
                    logger.info("Off-peak time from %s: unplugging", start.strftime("%H:%M"))
                    await unplug(source="off-peak time")
            if automation.unplug_at is not None:
                if not shared_state.plugged_in:
                    automation.unplug_at = None  # unplugged meanwhile
                elif shared_state.transaction_id is None:
                    logger.info("Schedule: the supplier stopped the session, unplugging")
                    automation.unplug_at = None
                    await unplug(source="schedule")
                elif automation._clock() >= automation.unplug_at:
                    logger.info("Schedule: the supplier didn't stop the session within %d s, unplugging",
                                automation.unplug_wait_s)
                    automation.unplug_at = None
                    await unplug(source="schedule")
            automation.update_plan_check(supplier_plan() if supplier_plan is not None else None,
                                         plugged_in=bool(shared_state.plugged_in))
            if automation.replug_due(
                # Plugged In and waiting (Preparing); not e.g. Unavailable
                plugged_in=bool(shared_state.plugged_in) and shared_state.state == "Preparing",
                in_session=shared_state.transaction_id is not None,
                connected=bool(shared_state.connected_to_server),
                # Your supplier has slots, planned or running: leave it to them (no re-plug)
                scheduled=bool(shared_state.scheduled) or bool(shared_state.slot_now),
                slot_now=shared_state.slot_now,
            ):
                automation.publish(shared_state)
                await automation.run_replug(unplug, plug)
            automation.publish(shared_state)
            automation.save_runtime()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Automation cycle failed", exc_info=True)
        wait = tick_s
        if automation.unplug_at is not None:
            wait = min(tick_s, PENDING_TICK_S)
        if automation.plug_at is not None:
            wait = min(wait, max(PENDING_TICK_S, automation.plug_at - automation._clock()))
        await asyncio.sleep(wait)
