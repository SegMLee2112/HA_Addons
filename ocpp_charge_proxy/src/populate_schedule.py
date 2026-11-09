"""Best charging times from a power sensor's history (Automations › Populate schedule).

For each weekday, uses only that weekday's history over the previous N weeks to find
the X 30-minute slots with the highest average power. Adjacent slots are merged.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from src.ha_history import parse_history, parse_statistics

logger = logging.getLogger(__name__)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _power_scale(unit: str | None, samples: list[float]) -> tuple[float, bool]:
    """(factor to kW, unit was assumed from values) from the sensor's unit/values."""
    u = (unit or "").strip().lower()
    if u in ("kw", "kilowatt", "kilowatts"):
        return 1.0, False
    if u in ("w", "watt", "watts"):
        return 0.001, False
    if u in ("mw", "megawatt", "megawatts"):
        return 1000.0, False
    vals = [abs(v) for v in samples if v is not None]
    return (0.001 if vals and max(vals) > 100 else 1.0), bool(vals)  # likely W if > 100


def _bin_stats(rows: list[dict], duration_s: int, weeks: int, now: datetime.datetime) -> dict[tuple[int, int], list[float]]:
    """(weekday 0=Mon, 30-min bin 0=00:00) -> [weighted mean contribution, weight]."""
    cutoff = now - datetime.timedelta(weeks=weeks)
    acc: dict[tuple[int, int], list[float]] = {}
    for row in rows:
        start_ts = row.get("start")
        mean = row.get("mean")
        if start_ts is None or mean is None:
            continue
        try:
            mean = float(mean)
        except (TypeError, ValueError):
            continue
        start = datetime.datetime.fromtimestamp(float(start_ts), datetime.timezone.utc).astimezone(now.tzinfo)
        if start < cutoff:
            continue
        end = start + datetime.timedelta(seconds=duration_s)
        t = start.replace(minute=(start.minute // 30) * 30, second=0, microsecond=0)
        while t < end:
            bin_end = t + datetime.timedelta(minutes=30)
            overlap = (min(end, bin_end) - max(start, t)).total_seconds()
            if overlap > 0:
                key = (t.weekday(), t.hour * 2 + t.minute // 30)
                pair = acc.setdefault(key, [0.0, 0.0])
                pair[0] += mean * overlap
                pair[1] += overlap
            t = bin_end
    return acc


def _merge_bins(bins: list[int]) -> list[tuple[int, int]]:
    if not bins:
        return []
    bins = sorted(set(bins))
    out = []
    a = b = bins[0]
    for x in bins[1:]:
        if x == b + 1:
            b = x
        else:
            out.append((a, b + 1))
            a = b = x
    out.append((a, b + 1))
    return out


def _fmt_bin(b: int) -> str:
    return f"{b // 2:02d}:{(b % 2) * 30:02d}"


def build_plan(five: list[dict], hourly: list[dict], *, weeks: int, slots: int,
               scale: float = 1.0, now: datetime.datetime | None = None,
               extra_notes: list[str] | None = None) -> dict[str, Any]:
    """Build the per-weekday plan from HA statistics rows (parsed, with 'start'/'mean')."""
    now = now or datetime.datetime.now().astimezone()
    slots = max(1, min(48, int(slots)))
    weeks = max(1, min(104, int(weeks)))
    # 5-min stats where available, hourly only before first 5-min (like ha_history.long_samples)
    first5 = min((r["start"] for r in five), default=None)
    hourly_use = [r for r in hourly if first5 is None or r["start"] + 3600 <= first5]
    acc = _bin_stats(five, 300, weeks, now)
    hacc = _bin_stats(hourly_use, 3600, weeks, now)
    for k, (s, w) in hacc.items():
        pair = acc.setdefault(k, [0.0, 0.0])
        pair[0] += s
        pair[1] += w

    days: dict[str, list[dict]] = {}
    notes = list(extra_notes or [])
    for wd, name in enumerate(DAYS):
        scored = []
        for b in range(48):
            pair = acc.get((wd, b))
            if pair and pair[1] > 0:
                scored.append((b, (pair[0] / pair[1]) * scale))
        scored.sort(key=lambda x: (-x[1], x[0]))
        top = sorted([b for b, _ in scored[:slots]])
        windows = _merge_bins(top)
        avg_by_bin = {b: v for b, v in scored}
        day_windows = []
        for a, e in windows:
            vals = [avg_by_bin[b] for b in range(a, e) if b in avg_by_bin]
            day_windows.append({
                "start": _fmt_bin(a),
                "end": _fmt_bin(e),
                "avg_kw": round(sum(vals) / len(vals), 2) if vals else None,
            })
        days[name] = day_windows
        if not scored:
            notes.append(f"No {name} data")
        elif len(scored) < slots:
            notes.append(f"{name}: only {len(scored)} slot{'s' if len(scored) != 1 else ''} with data")
    if not acc:
        notes.append("No history found for that sensor in the selected period")
    return {"ok": True, "weeks": weeks, "slots": slots, "days": days, "notes": notes}


async def calculate(ha_link, entity_id: str, weeks: int, slots: int) -> dict[str, Any]:
    """Fetch HA statistics/history for entity_id and return the populate plan."""
    if not entity_id:
        return {"ok": False, "message": "Pick a power sensor"}
    if ha_link is None or not getattr(ha_link, "available", False):
        return {"ok": False, "message": "Not connected to Home Assistant"}
    # In Home Assistant's time zone, with its clock changes: a fixed offset (astimezone()) would put
    # the weeks from before the clocks changed an hour out
    tz = None
    try:
        tz = ZoneInfo(((await ha_link.query({"type": "get_config"}) or {}).get("time_zone")) or "")
    except Exception:
        logger.debug("Populate schedule: Home Assistant's time zone not found; using the add-on's")
    now = datetime.datetime.now(tz) if tz else datetime.datetime.now().astimezone()
    start = (now - datetime.timedelta(weeks=max(1, int(weeks)))).timestamp()
    end = now.timestamp()
    iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
    notes: list[str] = []

    async def stats(period: str) -> list[dict]:
        try:
            result = await ha_link.query({
                "type": "recorder/statistics_during_period",
                "start_time": iso(start), "end_time": iso(end),
                "statistic_ids": [entity_id], "period": period, "types": ["mean"],
            }, timeout=60) or {}
        except Exception as err:
            logger.debug("Populate schedule %s stats (%s): %s", entity_id, period, err)
            return []
        return parse_statistics(result, entity_id)

    # Detect unit/scale from recent history
    scale, assumed_unit = 1.0, False
    try:
        h = await ha_link.query({
            "type": "history/history_during_period",
            "start_time": iso(end - 86400), "end_time": iso(end),
            "entity_ids": [entity_id], "minimal_response": False, "no_attributes": False,
            "significant_changes_only": False,
        }, timeout=30) or {}
        rows = parse_history(h, entity_id)
        unit = (rows[-1][2] or {}).get("unit_of_measurement") if rows else None
        vals = []
        for _, st, _ in rows[-50:]:
            try:
                vals.append(float(st))
            except (TypeError, ValueError):
                pass
        scale, assumed_unit = _power_scale(unit, vals)
    except Exception as err:
        logger.debug("Populate schedule unit lookup for %s: %s", entity_id, err)
    if assumed_unit:
        notes.append("Power unit assumed from values")

    five = await stats("5minute")
    first5 = min((r["start"] for r in five), default=None)
    # Hourly only fills a leading gap before 5-min stats (kept longer than 5-min by HA)
    hourly = await stats("hour") if (not five) or (first5 is not None and first5 - start > 3600) else []
    if not five and not hourly:
        # last resort: raw history (expensive but thorough)
        try:
            h = await ha_link.query({
                "type": "history/history_during_period",
                "start_time": iso(start), "end_time": iso(end),
                "entity_ids": [entity_id], "minimal_response": True, "no_attributes": True,
                "significant_changes_only": False,
            }, timeout=90) or {}
            hrows = parse_history(h, entity_id)
            # convert state changes to 5-min bucket means (count-based approximation)
            bucket: dict[int, list[float]] = {}
            for ts, st, _ in hrows:
                try:
                    v = float(st) * scale
                except (TypeError, ValueError):
                    continue
                key = int(ts // 300) * 300
                b = bucket.setdefault(key, [0.0, 0])
                b[0] += v
                b[1] += 1
            five = [{"start": k, "mean": v / n} for k, (v, n) in sorted(bucket.items()) if n]
            scale = 1.0  # already scaled
            if five:
                notes.append("Estimated from raw history (less precise)")
        except Exception as err:
            logger.debug("Populate schedule history fallback for %s: %s", entity_id, err)
            return {"ok": False, "message": f"Couldn't read history for {entity_id}: {err}"}

    return build_plan(five, hourly, weeks=int(weeks), slots=int(slots), scale=scale, now=now, extra_notes=notes)
