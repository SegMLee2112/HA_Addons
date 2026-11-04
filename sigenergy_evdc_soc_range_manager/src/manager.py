"""Reads Home Assistant's entities, asks src/controller.py what to do and
presses the charger's Start or Stop button.

Kept apart from the Home Assistant link so it can be tested with plain
dictionaries: `states` is entity_id -> {"state", "attributes",
"last_changed"}, `entities` is the Settings tab's picks (key -> entity_id)
and `press` / `notify` are coroutines supplied by src/__main__.py.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
from collections import deque
from dataclasses import replace
from typing import Awaitable, Callable, Optional

from src.controller import RULES, Decision, Inputs, bound_limits, decide, house_needs_power, soc_place
from src.flow import flows, house_load
from src.rates import battery_rates
from src.schedule import describe
from src.stats import Sample

logger = logging.getLogger(__name__)

LOG_SIZE = 500  # the history on the Schedule tab
UNANSWERED_LIMIT = 3  # presses of the same button in a row without a response: then only every unanswered_wait_s
UNKNOWN = ("unknown", "unavailable", "none", "")
SOC_TREND_S = 1800  # the SoC's direction is worked out over this long (logging only)
SOC_TREND_MIN_S = 300  # ...and needs readings at least this far apart
SOC_SAMPLES_KEEP_S = 7200
QUIET_RULES = ("press_gap",)  # decisions not logged when they change (they're about a press just made)

# Lists the Settings tab can change (Advanced); matched without regard to case
LIST_DEFAULTS = {
    # The charger's running states (sensor.sigen_inverter_dc_charger_running_state)
    # (not Alarm or Fault: those are an alarm, whether or not a car's plugged in)
    "plugged_states": ["Occupied", "Ended", "Preparing Comm", "Preparing Insulation",
                       "Charging", "Discharging"],
    "active_states": ["Charging", "Discharging", "Preparing Insulation", "Preparing Comm"],
    "discharging_states": ["Discharging"],
    # ...and that mean the charger has a fault
    "alarm_states": ["Alarm", "#Alarm", "Fault"],
    # The V2X mode entity's state(s) that mean "manage the charger"
    "mode_states": ["V2X", "on"],
    # ...that count for the energy and battery rate sensors (as the V2X template sensors did)
    "energy_mode_states": ["V2X", "Solar Surplus", "on"],
    # ...that mean fast charging (the battery rate sensor's top rate)
    "fast_mode_states": ["Fast Charging"],
    # EMS modes in which exporting isn't spare power (the plant is being told to discharge)
    "blocked_ems_modes": ["Command Discharging (PV First)", "Command Discharging (ESS First)"],
}


def _float(st: Optional[dict]) -> Optional[float]:
    if not st:
        return None
    try:
        value = float(st.get("state"))
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # not NaN


def power_kw(st: Optional[dict]) -> Optional[float]:
    """A power sensor's reading in kW (W and MW converted)."""
    value = _float(st)
    if value is None:
        return None
    unit = ((st.get("attributes") or {}).get("unit_of_measurement") or "kW").strip()
    return value / 1000 if unit == "W" else value * 1000 if unit == "MW" else value


def energy_kwh(st: Optional[dict], unit: Optional[str] = None) -> Optional[float]:
    """An energy sensor's reading in kWh (Wh and MWh converted)."""
    value = _float(st)
    if value is None:
        return None
    unit = (unit or ((st or {}).get("attributes") or {}).get("unit_of_measurement") or "kWh").strip()
    return value / 1000 if unit == "Wh" else value * 1000 if unit == "MWh" else value


def price_gbp(st: Optional[dict]) -> Optional[float]:
    """A rate sensor's price in £/kWh (p/kWh converted)."""
    value = _float(st)
    if value is None:
        return None
    unit = (((st or {}).get("attributes") or {}).get("unit_of_measurement") or "").lower()
    return value / 100 if unit.startswith("p") or "pence" in unit else value


def _text(st: Optional[dict]) -> Optional[str]:
    value = (st or {}).get("state")
    return None if value is None or str(value).lower() in UNKNOWN else str(value)


def _in(value: Optional[str], options) -> bool:
    return value is not None and value.strip().lower() in {o.strip().lower() for o in options}


# A device's "plugged in" entity counts as plugged in with one of these states (or a number above 0)
PLUGGED_WORDS = ("on", "true", "yes", "home", "plugged", "plugged in", "plugged_in", "connected", "charging",
                 "occupied", "running", "heating")


def plugged_now(st: Optional[dict]) -> bool:
    text = _text(st)
    if text is None:
        return False
    if _in(text, PLUGGED_WORDS):
        return True
    try:
        return float(text) > 0
    except ValueError:
        return False


def flow_devices(devices, states: dict) -> list:
    """The power flow card's devices to show now: with a power sensor, and plugged in (when
    there's an entity for it). Each {n, name, icon, kw}: n is its place in the settings, for its colour."""
    out = []
    for n, d in enumerate(devices or []):
        if not d.get("power") or (d.get("plugged") and not plugged_now(states.get(d["plugged"]))):
            continue
        kw = power_kw(states.get(d["power"]))
        out.append({"n": n, "name": d.get("name") or f"Device {n + 1}", "icon": d.get("icon") or "plug",
                    "kw": None if kw is None else round(max(0.0, kw), 3)})
    return out


def _pct_text(v: float) -> str:
    return f"{v:g}%"


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Manager:
    def __init__(self, data_dir: Optional[str] = None, *,
                 press: Optional[Callable[[str], Awaitable[None]]] = None,
                 notify: Optional[Callable[[str, str], Awaitable[None]]] = None) -> None:
        self._path = os.path.join(data_dir, "state.json") if data_dir else None
        self.press = press  # (entity_id) -> presses that button
        self.notify = notify  # (title, message) -> sends a notification
        self.schedule = None  # src.schedule.Schedule: limits changed for a while
        self.was_discharging = False  # what the charger was doing while it last ran
        self.dropouts: list[float] = []  # times the car stopped discharging by itself, not yet collected
        self._scheduled: Optional[tuple] = None  # the schedule entries on at the last look
        self.alarm_since: Optional[float] = None  # the charger has reported an alarm since then
        self.alarms: list[str] = []  # alarm messages not yet sent as notifications
        self.soc: Optional[float] = None  # the car's last known SoC
        self.soc_at: Optional[float] = None  # ...when it was read
        self.soc_before_plug_in = False  # ...and it's from before the car was last plugged in
        self.active: Optional[bool] = None
        self.active_since: Optional[float] = None
        self.plugged: Optional[bool] = None
        self.export_since: Optional[float] = None
        self.charging_since: Optional[float] = None  # the charger has been charging since then
        self.charge_stopped_at: Optional[float] = None  # stopped for charging above the high limit then
        self.unanswered = 0  # presses of the same button in a row that the charger didn't respond to
        self.last_press_at: Optional[float] = None
        self.last_press: Optional[dict] = None  # its log entry: what was pressed, and whether it worked
        self.presses_today = 0
        self.day: Optional[str] = None
        self.log: deque = deque(maxlen=LOG_SIZE)
        self.soc_samples: deque = deque()  # (time, SoC) readings, for the direction it's going in the log
        self._logged_rule: Optional[str] = None  # the last decision written to the log
        self.inputs: Optional[Inputs] = None
        self.decision: Optional[Decision] = None
        self.readings: dict = {}
        self._load()

    # --- saved between restarts ------------------------------------------------

    def _load(self) -> None:
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self.soc, self.soc_at = data.get("soc"), data.get("soc_at")
            self.soc_before_plug_in = bool(data.get("soc_before_plug_in"))
            self.log.extend(data.get("log") or [])
            self.presses_today, self.day = int(data.get("presses_today") or 0), data.get("day")
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Saved state %s is unreadable: starting afresh", self._path)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"soc": self.soc, "soc_at": self.soc_at, "soc_before_plug_in": self.soc_before_plug_in,
                           "presses_today": self.presses_today, "day": self.day, "log": list(self.log)}, f)
            os.replace(tmp, self._path)
        except Exception:
            logger.warning("Could not save the state", exc_info=True)

    # --- reading Home Assistant ---------------------------------------------------

    def observe(self, states: dict, entities: dict, settings: dict, now: float) -> Inputs:
        """Turn the entities' states into the controller's inputs, keeping track of
        what needs remembering (last known SoC, when the charger stopped...)."""
        lists = {k: settings.get(k) or v for k, v in LIST_DEFAULTS.items()}
        get = lambda key: states.get(entities.get(key) or "")  # noqa: E731

        running_st = get("running_state")
        running = _text(running_st)
        plugged = _in(running, lists["plugged_states"])
        active = _in(running, lists["active_states"])
        discharging = _in(running, lists["discharging_states"])
        alarm = _in(running, lists["alarm_states"])
        if alarm != bool(self.alarm_since) and self.active is not None:
            self._alarm_changed(alarm, running, now)
        elif alarm and not self.alarm_since:
            self.alarm_since = now

        if self.active is None:  # first look: when did the running state last change?
            changed = (running_st or {}).get("last_changed")
            self.active_since = changed if isinstance(changed, (int, float)) else now
        elif active != self.active:
            self.unanswered = 0  # it responded (or something else changed it)
            # When it changed, by Home Assistant's clock (we may only look a few seconds later)
            changed = (running_st or {}).get("last_changed")
            self.active_since = changed if isinstance(changed, (int, float)) and now - 120 <= changed <= now else now
            logger.info("Charger %s (%s)", "running" if active else "stopped", running or "unknown")
            if active and plugged and not self._ours("start", now):
                self._elsewhere("start", now)
            elif not active and plugged and not alarm and not self._ours("stop", now):  # an alarm is noted as one
                if self.was_discharging:
                    self._dropout(now)
                else:
                    self._elsewhere("stop", now)
        self.active = active
        if active:
            self.was_discharging = discharging
        if active and not discharging and (running or "").strip().lower() == "charging":
            self.charging_since = self.charging_since or now
        else:
            self.charging_since = None

        if plugged and self.plugged is False:
            self.soc_before_plug_in = self.soc is not None  # until a new reading arrives
            logger.info("Car plugged in")
        elif not plugged and self.plugged:
            logger.info("Car unplugged")
        self.plugged = plugged

        soc = _float(get("vehicle_soc"))
        if soc is not None and 0 < soc <= 100:
            if soc != self.soc or self.soc_before_plug_in:
                previous = None if self.soc_before_plug_in else self.soc
                self.soc, self.soc_at, self.soc_before_plug_in = soc, now, False
                self._save()
                self._soc_sample(now, soc)
                if previous is not None:
                    logger.info("Car SoC %s → %s (%s; charger %s)", _pct_text(previous), _pct_text(soc),
                                self.soc_trend_text(now), (running or "unknown").lower())
            else:
                self.soc_at = now
                self._soc_sample(now, soc)

        base_high, base_low = _float(get("soc_high")), _float(get("soc_low"))
        high, low, scheduled = base_high, base_low, []
        if self.schedule is not None:
            local = datetime.datetime.fromtimestamp(now)
            self.schedule.tidy(local)
            high, low, scheduled = self.schedule.apply(base_high, base_low, local)
        high, low = bound_limits(high, low)  # never below 20% or above 99%
        self._limits_changed(scheduled, high, low, now)

        mode_st = get("v2x_mode")
        mode = _text(mode_st)
        mode_on = None if mode is None else _in(mode, lists["mode_states"])

        export = power_kw(get("export_power"))
        level = float(settings.get("export_kw", 0.5))
        if export is not None and export > level:
            self.export_since = self.export_since or now
        else:
            self.export_since = None

        signal_st = get("charge_signal")
        signal = None if not entities.get("charge_signal") else _in(_text(signal_st), ("on", "true", "charging"))
        ems = _text(get("ems_mode"))

        self.inputs = Inputs(
            plugged_in=plugged, mode_on=mode_on, running_state=running, active=active, discharging=discharging,
            alarm=alarm,
            inactive_for=None if active else max(0.0, now - (now if self.active_since is None else self.active_since)),
            soc=self.soc, high=high, low=low,
            battery_kw=power_kw(get("battery_power")), grid_kw=power_kw(get("grid_power")),
            export_kw=export, export_held_s=0.0 if self.export_since is None else now - self.export_since,
            ems_mode=ems, charge_signal=signal, ems_blocked=_in(ems, lists["blocked_ems_modes"]),
            charging_for=None if self.charging_since is None else now - self.charging_since,
            charge_stopped_ago=None if self.charge_stopped_at is None else now - self.charge_stopped_at,
        )
        # The house stopped needing power (e.g. exporting): the next time it does, the car may be
        # started for it again, even above the high limit
        if self.charge_stopped_at is not None and not house_needs_power(self.inputs, settings):
            self.charge_stopped_at = None
            self.inputs = replace(self.inputs, charge_stopped_ago=None)
        self.readings = {
            "base_high": base_high, "base_low": base_low,
            "scheduled": [{"id": e["id"], "kind": e["kind"], "high": e["high"], "low": e["low"], "until": e["until"],
                           "label": e.get("label") or ""} for e in scheduled],
            "capacity_kwh": _float(get("capacity")), "capacity_entity": entities.get("capacity") or None, "mode": mode,
            "energy_mode": plugged and _in(mode, lists["energy_mode_states"]),
            "fast_mode": plugged and _in(mode, lists["fast_mode_states"]),
            "available_kw": power_kw(get("available_power")),
            "car_kw": power_kw(get("charger_power")),
            "pv_kw": power_kw(get("pv_power")),
            "home_measured_kw": power_kw(get("home_power")),
            "grid_v": _float(get("grid_voltage")),  # the power flow card's grid circle (phase A)
            "home_soc": _float(get("home_battery_soc")), "home_kwh": _float(get("home_battery_capacity")),
            "flow_devices": flow_devices(entities.get("flow_devices"), states),
            "flow_subtract": entities.get("flow_subtract", True) is not False,
        }
        # The power flow card's own sensors for solar, the grid and the home battery, when picked
        for key, entity_key in (("flow_pv_kw", "flow_pv_power"), ("flow_grid_kw", "flow_grid_power"),
                                ("flow_battery_kw", "flow_battery_power"), ("flow_home_kw", "flow_home_power"),
                                ("flow_car_kw", "flow_car_power")):
            if entities.get(entity_key):
                self.readings[key] = power_kw(get(entity_key))
        self._settings = settings
        return self.inputs

    # --- logging what it sees ------------------------------------------------------

    def _soc_sample(self, now: float, soc: float) -> None:
        """Keep a reading for the SoC's direction: when it changes, and every few minutes anyway."""
        last = self.soc_samples[-1] if self.soc_samples else None
        if last is None or last[1] != soc or now - last[0] >= SOC_TREND_MIN_S:
            self.soc_samples.append((now, soc))
        while self.soc_samples and self.soc_samples[0][0] < now - SOC_SAMPLES_KEEP_S:
            self.soc_samples.popleft()

    def soc_trend(self, now: float) -> Optional[float]:
        """How fast the car's SoC is changing, %/h, over the last SOC_TREND_S (None: too few readings)."""
        if not self.soc_samples:
            return None
        start = None
        for ts, soc in self.soc_samples:  # the reading in force when the window starts, else the oldest
            if ts <= now - SOC_TREND_S or start is None:
                start = (ts, soc)
            else:
                break
        end = self.soc_samples[-1]
        span = now - start[0]
        if span < SOC_TREND_MIN_S:
            return None
        return (end[1] - start[1]) / (span / 3600)

    def soc_trend_text(self, now: float) -> str:
        rate = self.soc_trend(now)
        if rate is None:
            return "direction not known yet"
        if rate >= 0.5:
            return f"rising {rate:+.1f}%/h"
        if rate <= -0.5:
            return f"falling {rate:+.1f}%/h"
        return "steady"

    def context(self, now: float) -> str:
        """What it's seeing, on one line for the log: the car, the charger, the house and the grid."""
        i = self.inputs
        if i is None:
            return "no readings yet"
        parts = []
        if i.soc is not None:
            car = f"car {_pct_text(i.soc)} ({self.soc_trend_text(now)})"
            if i.high is not None and i.low is not None:
                car += f", {soc_place(i.soc, i.high, i.low)}"
            parts.append(car)
        parts.append(f"charger {(i.running_state or 'unknown').lower()}")
        if i.battery_kw is not None:
            way = "charging" if i.battery_kw > 0 else "discharging" if i.battery_kw < 0 else "idle"
            soc_home = self.readings.get("home_soc")
            parts.append(f"home battery {way} {abs(i.battery_kw):.1f} kW"
                         + (f" at {_pct_text(soc_home)}" if soc_home is not None else ""))
        if i.grid_kw is not None:
            way = "importing" if i.grid_kw > 0 else "exporting" if i.grid_kw < 0 else "idle"
            parts.append(f"grid {way} {abs(i.grid_kw):.1f} kW")
        if i.export_kw is not None:
            parts.append(f"export {i.export_kw:.1f} kW")
        if i.charge_signal is not None:
            parts.append(f"charge signal {'on' if i.charge_signal else 'off'}")
        if i.ems_mode:
            parts.append(f"EMS {i.ems_mode}")
        return "; ".join(parts)

    # --- acting -----------------------------------------------------------------

    async def step(self, states: dict, entities: dict, settings: dict, now: Optional[float] = None) -> Decision:
        """Look, decide and (unless only watching) press a button. Returns the decision."""
        now = time.time() if now is None else now
        self._new_day(now)
        inputs = self.observe(states, entities, settings, now)
        decision = decide(inputs, settings)
        # Log each new decision (not the presses: act() logs those), with what it was based on
        if decision.rule != self._logged_rule and not decision.action and decision.rule not in QUIET_RULES:
            logger.info("Decision: %s - %s [%s]", decision.status, decision.reason, self.context(now))
        if decision.rule not in QUIET_RULES:
            self._logged_rule = decision.rule
        if decision.action:
            gap = float(settings.get("press_gap_s", 60))
            last = self.last_press or {}
            if self.unanswered >= UNANSWERED_LIMIT and last.get("action") == decision.action:
                gap = max(gap, float(settings.get("unanswered_wait_s", 900)))
            # The gap only stops the same button being pressed again too soon. The other one goes
            # straight away, as the automation would (e.g. a stop at the low limit just after a start);
            # a start straight after a stop can't happen anyway (the restart wait).
            same = last.get("action", decision.action) == decision.action
            if same and self.last_press_at is not None and now - self.last_press_at < gap:
                left = round(gap - (now - self.last_press_at))
                ago = round(now - self.last_press_at)
                if last.get("pressed"):
                    # Just pressed this: the charger takes a few seconds to respond, so not a retry yet
                    decision = Decision(None, "press_gap", "Starting" if decision.action == "start" else "Stopping",
                                        f"{'Start' if decision.action == 'start' else 'Stop'} was pressed {ago} s ago; "
                                        f"waiting for the charger (pressed again in {left} s if it hasn't "
                                        f"{'started' if decision.action == 'start' else 'stopped'}).")
                else:
                    decision = Decision(None, "press_gap", "Waiting to retry",
                                        f"{RULES.get(decision.rule, decision.rule)}, but "
                                        f"{'that was tried' if last.get('error') else 'that was decided'} "
                                        f"{ago} s ago; trying again in {left} s.")
            else:
                await self.act(decision.action, decision.rule, decision.reason, entities, settings, now)
        self.decision = decision
        return decision

    async def act(self, action: str, rule: str, reason: str, entities: dict, settings: dict,
                  now: Optional[float] = None) -> dict:
        """Press Start or Stop (rule "manual" from the web page). Returns the log entry."""
        now = time.time() if now is None else now
        button = entities.get("start_button" if action == "start" else "stop_button")
        watching = bool(settings.get("observe_only")) and rule != "manual"
        context = self.context(now)
        entry = {"at": _iso(now), "action": action, "rule": rule, "reason": reason, "context": context,
                 "soc": self.soc, "pressed": False, "observe_only": watching, "error": None, **self._limits_now(),
                 **({"soc_from_before_plug_in": True} if self.soc_before_plug_in else {})}
        self.last_press_at = now
        if not button:
            entry["error"] = f"No {action} button set on the Settings tab"
        elif watching:
            logger.info("Would %s the charger (watching only): %s [%s]", action, reason, context)
        elif self.press is None:
            entry["error"] = "Not connected to Home Assistant"
        else:
            try:
                await self.press(button)
                entry["pressed"] = True
                self.presses_today += 1
                same = (self.last_press or {}).get("action") == action and (self.last_press or {}).get("pressed")
                self.unanswered = self.unanswered + 1 if same else 1
                if self.unanswered >= UNANSWERED_LIMIT:
                    wait = round(float(settings.get("unanswered_wait_s", 900)) / 60)
                    entry["error"] = (f"The charger hasn't responded to {self.unanswered} {action} presses in a row: "
                                      f"trying again every {wait} min until it does")
                if rule == "charging_above_high":
                    self.charge_stopped_at = now
                logger.info("%s the charger: %s [%s]", "Started" if action == "start" else "Stopped", reason, context)
            except Exception as err:
                entry["error"] = str(err) or type(err).__name__
        if entry["error"]:
            logger.warning("Couldn't %s the charger: %s", action, entry["error"])
        self.log.append(entry)
        self.last_press = entry
        self._save()
        if entry["pressed"] and settings.get("notify_service") and self.notify is not None:
            title = "V2X: charger " + ("started" if action == "start" else "stopped")
            try:
                await self.notify(title, f"{RULES.get(rule, 'Pressed on the add-on page')}. {reason}")
            except Exception as err:
                logger.warning("Couldn't send the notification: %s", err)
        return entry

    def _ours(self, action: str, now: float) -> bool:
        """Did this add-on start (or stop) the charger just before it started (or stopped)?
        A watch-only "would" counts: something else did what it would have done."""
        for entry in reversed(self.log):
            try:
                at = datetime.datetime.fromisoformat(entry["at"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                return False
            if now - at > 180:
                return False
            # (a press that went through counts even if it's noted as unanswered)
            if entry.get("action") == action and (entry.get("pressed")
                                                  or (entry.get("observe_only") and not entry.get("error"))):
                return True
        return False

    def _elsewhere(self, action: str, now: float) -> None:
        """The charger started or stopped without this add-on: e.g. another automation, the
        Sigenergy app or Predbat. Noted so the history says who did what."""
        self.log.append({"at": _iso(now), "action": f"elsewhere_{action}", "rule": f"elsewhere_{action}",
                         "reason": f"The charger {'started' if action == 'start' else 'stopped'} without this add-on "
                                   "(another automation, the Sigenergy app or the charger itself).",
                         "soc": self.soc, "pressed": False, "observe_only": False, "error": None, **self._limits_now(),
                         **({"soc_from_before_plug_in": True} if self.soc_before_plug_in else {})})
        self._save()
        logger.info("The charger %s without this add-on", "started" if action == "start" else "stopped")

    def _limits_now(self) -> dict:
        """The limits in use, for a history entry."""
        i, r = self.inputs, self.readings
        return {"high": i.high if i else None, "low": i.low if i else None,
                "scheduled": [e.get("label") or "scheduled" for e in r.get("scheduled") or []]}

    def _limits_changed(self, scheduled: list, high, low, now: float) -> None:
        """A schedule entry came on or went off: note it in the history."""
        ids = tuple(sorted(e["id"] for e in scheduled))
        if self._scheduled is None or ids == self._scheduled:
            self._scheduled = ids  # the first look after starting isn't a change
            return
        self._scheduled = ids
        fmt = lambda v: "—" if v is None else f"{v:g}%"  # noqa: E731
        if scheduled:
            names = ", ".join(e.get("label") or describe(e) for e in scheduled)
            reason = f"Scheduled limits on ({names}): high {fmt(high)}, low {fmt(low)}."
        else:
            reason = f"Back to the default limits: high {fmt(high)}, low {fmt(low)}."
        self.log.append({"at": _iso(now), "action": "limits", "rule": "schedule", "reason": reason, "soc": self.soc,
                         "pressed": False, "observe_only": False, "error": None, "high": high, "low": low,
                         "scheduled": [e.get("label") or "scheduled" for e in scheduled]})
        self._save()
        logger.info(reason)

    def _alarm_changed(self, alarm: bool, running: Optional[str], now: float) -> None:
        """The charger went into, or came out of, its alarm state."""
        if alarm:
            self.alarm_since = now
            reason = f"The charger reports an alarm ({running}): an error with the charger. Check it in the Sigenergy app."
            self.log.append({"at": _iso(now), "action": "alarm", "rule": "alarm", "reason": reason, "soc": self.soc,
                             "pressed": False, "observe_only": False, "error": None, **self._limits_now()})
            self.alarms.append(reason)
            logger.warning(reason)
        else:
            lasted = round((now - (self.alarm_since or now)) / 60)
            self.alarm_since = None
            reason = f"The charger's alarm cleared after {lasted} min (now {running or 'unknown'})."
            self.log.append({"at": _iso(now), "action": "alarm_cleared", "rule": "alarm", "reason": reason,
                             "soc": self.soc, "pressed": False, "observe_only": False, "error": None,
                             **self._limits_now()})
            logger.info(reason)
        self._save()

    def _dropout(self, now: float) -> None:
        """The car stopped discharging without being told to."""
        self.dropouts.append(now)
        self.log.append({"at": _iso(now), "action": "dropout", "rule": "dropout",
                         "reason": "The car stopped discharging by itself.", "soc": self.soc,
                         "pressed": False, "observe_only": False, "error": None, **self._limits_now()})
        self._save()
        logger.info("The car stopped discharging by itself (at %s%%)", self.soc)

    def _new_day(self, now: float) -> None:
        day = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if day != self.day:
            self.day, self.presses_today = day, 0

    # --- for the web page and the sensors ------------------------------------------

    def energy(self) -> dict:
        """kWh the car can give before the low limit, kWh between the limits, and how
        full that window is (%): like the V2X SoC template sensors, 0.01 when not in use."""
        i, cap = self.inputs, self.readings.get("capacity_kwh")
        out = {"available_kwh": 0.01, "window_kwh": 0.01, "window_pct": 0.0}
        if not i or not self.readings.get("energy_mode") or not cap or i.low is None or i.high is None:
            return out
        if i.soc is not None:
            out["available_kwh"] = round(max(cap * (i.soc - i.low) / 100, 0.01), 2)
        out["window_kwh"] = round(max(cap * (i.high - i.low) / 100, 0.01), 2)
        if out["window_kwh"] > 0.01:
            out["window_pct"] = round(min(100.0, out["available_kwh"] / out["window_kwh"] * 100), 1)
        return out

    def rates(self) -> dict:
        """The V2X battery rate sensors (src/rates.py)."""
        i, r = self.inputs, self.readings
        if i is None:
            return {}
        cap = r.get("capacity_kwh") or 0.0
        window = cap * (i.high - i.low) / 100 if i.high is not None and i.low is not None else 0.0
        return battery_rates(plugged_in=i.plugged_in, v2x=bool(r.get("energy_mode")), fast=bool(r.get("fast_mode")),
                             available_kw=r.get("available_kw"), home_soc=r.get("home_soc"), home_kwh=r.get("home_kwh"),
                             car_window_kwh=window, settings=getattr(self, "_settings", None))

    def power_flow(self) -> dict:
        """The Overview's power flow diagram (src/flow.py)."""
        i, r = self.inputs, self.readings
        if i is None:
            return {}
        car_kw = r["flow_car_kw"] if "flow_car_kw" in r else r.get("car_kw")  # the card's own car sensor, if picked
        car = abs(car_kw or 0.0) if i.active else 0.0
        car = -car if i.discharging else car
        # The card's own solar, grid and battery sensors when picked, else the ones it decides from
        pv = r["flow_pv_kw"] if "flow_pv_kw" in r else r.get("pv_kw")
        grid = r["flow_grid_kw"] if "flow_grid_kw" in r else i.grid_kw
        batt = r["flow_battery_kw"] if "flow_battery_kw" in r else i.battery_kw
        house = house_load(pv, grid, batt, car, r["flow_home_kw"] if "flow_home_kw" in r else r.get("home_measured_kw"))
        # Devices off the house (a car charger, a heat pump...): drawn coming off it, the house
        # showing what's left
        devices = r.get("flow_devices") or []
        used = sum(d["kw"] or 0.0 for d in devices)
        rest = None if house is None else round(max(0.0, house - used), 3)
        # (or, with "Take them off the house" off, its whole use: home_kw None)
        return {"pv_kw": pv, "grid_kw": grid, "battery_kw": batt, "car_kw": car,
                "car_plugged": bool(i.plugged_in),  # (the car's left off the diagram while it's unplugged)
                "house_kw": None if house is None else round(house, 3),
                "home_kw": rest if devices and r.get("flow_subtract", True) else None,
                "home_soc": r.get("home_soc"), "devices": devices, "grid_v": r.get("grid_v"),
                "lines": flows(pv, grid, batt, car, house)}

    def stats_sample(self, states: dict, entities: dict, now: float) -> Optional[Sample]:
        """This look's readings for the statistics (src/stats.py)."""
        i = self.inputs
        if i is None:
            return None
        get = lambda key: states.get(entities.get(key) or "")  # noqa: E731
        return Sample(
            now=now, direction=_direction(i.active, i.discharging, i.running_state),
            soc=_float(get("vehicle_soc")), e_in=energy_kwh(get("charged_energy")),
            e_out=energy_kwh(get("discharged_energy")), car_kw=power_kw(get("charger_power")),
            pv_kw=power_kw(get("pv_power")), batt_kw=i.battery_kw, ac_kw=power_kw(get("inverter_power")),
            grid_kw=i.grid_kw, import_price=price_gbp(get("import_rate")), export_price=price_gbp(get("export_rate")),
        )

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        i, d = self.inputs, self.decision
        return {
            "status": d.status if d else "Starting",
            "rule": d.rule if d else None,
            "reason": d.reason if d else "",
            "inputs": None if i is None else {**i.__dict__},
            "soc_at": _iso(self.soc_at),
            "soc_age_s": None if self.soc_at is None else round(now - self.soc_at),
            "soc_before_plug_in": self.soc_before_plug_in,
            "active_since": _iso(self.active_since),
            "presses_today": self.presses_today,
            "last_action": next((x for x in reversed(self.log) if x.get("action") in ("start", "stop")), None),
            "log": [x for x in self.log if x.get("action") != "limits"][-30:][::-1],
            "energy": self.energy(),
            "rates": self.rates(),
            "flow": self.power_flow(),
            "readings": dict(self.readings),
        }


def _direction(active: bool, discharging: bool, running_state: Optional[str]) -> Optional[str]:
    if not active:
        return None
    if discharging:
        return "discharge"
    return "charge" if (running_state or "").strip().lower() == "charging" else "other"


def _when(item: dict) -> Optional[float]:
    for key in ("lu", "lc"):
        if isinstance(item.get(key), (int, float)):
            return float(item[key])
    for key in ("last_updated", "last_changed"):
        if item.get(key):
            try:
                return datetime.datetime.fromisoformat(str(item[key]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return None


HISTORY_KEYS = ("running_state", "vehicle_soc", "charged_energy", "discharged_energy")


def samples_from_history(history: dict, entities: dict, settings: dict, units: Optional[dict] = None) -> list:
    """Home Assistant's history (history/history_during_period: entity_id -> states)
    as statistics samples, one per change. `units`: key -> the sensor's unit now."""
    lists = {k: settings.get(k) or v for k, v in LIST_DEFAULTS.items()}
    units = units or {}
    events = []
    for key in HISTORY_KEYS:
        for item in (history or {}).get(entities.get(key) or "", []) or []:
            when = _when(item)
            if when is not None:
                events.append((when, key, item.get("s", item.get("state"))))
    events.sort(key=lambda e: e[0])
    now: dict = {}
    out = []
    for when, key, state in events:
        now[key] = state
        running = _text({"state": now.get("running_state")})
        active = _in(running, lists["active_states"])
        discharging = _in(running, lists["discharging_states"])
        out.append(Sample(
            now=when, direction=_direction(active, discharging, running),
            soc=_float({"state": now.get("vehicle_soc")}),
            e_in=energy_kwh({"state": now.get("charged_energy")}, units.get("charged_energy")),
            e_out=energy_kwh({"state": now.get("discharged_energy")}, units.get("discharged_energy")),
        ))
    return out


def soc_state(active: bool, discharging: bool, running: Optional[str], plugged: bool, alarm: bool = False) -> str:
    """What the charger is doing, for the SoC chart."""
    if alarm:
        return "alarm"
    if not plugged:
        return "unplugged"
    d = _direction(active, discharging, running)
    return "charge" if d == "charge" else "discharge" if d == "discharge" else "idle"


def soc_points_from_history(history: dict, entities: dict, settings: dict) -> list:
    """Home Assistant's history of the SoC and the running state as SoC chart points
    (no limits: they weren't recorded then)."""
    lists = {k: settings.get(k) or v for k, v in LIST_DEFAULTS.items()}
    events = []
    for key in ("running_state", "vehicle_soc"):
        for item in (history or {}).get(entities.get(key) or "", []) or []:
            when = _when(item)
            if when is not None:
                events.append((when, key, item.get("s", item.get("state"))))
    events.sort(key=lambda e: e[0])
    soc, running, out = None, None, []
    for when, key, state in events:
        if key == "vehicle_soc":
            value = _float({"state": state})
            if value is None or not 0 < value <= 100:
                continue  # 0 or nothing while the charger's off: keep the last known
            soc = value
        else:
            running = _text({"state": state})
        if soc is None:
            continue
        state_now = soc_state(_in(running, lists["active_states"]), _in(running, lists["discharging_states"]),
                              running, _in(running, lists["plugged_states"]), _in(running, lists["alarm_states"]))
        point = [round(when, 1), soc, None, None, state_now]
        if out and out[-1][1:] == point[1:]:
            continue
        out.append(point)
    return out
