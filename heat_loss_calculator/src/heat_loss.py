"""The heat loss calculation: Home Assistant history in, your home's heat loss out.

Pure (no I/O) so it can be tested on its own; src/__main__.py fetches the
history and stores the days.

1. Each day (local midnight to midnight) is summarised from the history of
   the gas sensor, the outside temperature, the flow temperature (or a fixed
   one) and the inside thermostats:
   - gas: the change between readings of an accumulating sensor (Octopus
     Energy's current accumulative consumption, which resets each midnight:
     a drop is a reset, so the new reading is all new use);
   - heat: each half hour's gas times the boiler's efficiency at that half
     hour's flow temperature;
   - temperatures: time-weighted means (a reading holds until the next);
     inside is the floors' means weighted by their floor area.
2. The fit: a straight line through each day's heat against inside minus
   outside (ΔT). Its slope ÷ 24 h is the heat loss (W/K). Hot water and
   cooking are about the same every day, so on a combi they end up in where
   the line crosses ΔT 0 (with the free heat from the sun, people and
   appliances), not in the slope.
"""
from __future__ import annotations

import datetime
import math
from typing import Iterable, Optional

HH = 1800  # a half hour, s
MAX_GAS_GAP_S = 3 * 3600  # a gap between gas readings longer than this: the day's incomplete
EDGE_S = 90 * 60  # ...and the first and last reading must be this close to midnight
MIN_TEMP_COVER = 0.8  # temperatures must cover this much of the day
MIN_DAYS = 5  # the fewest days for a fit
MIN_SPREAD = 3.0  # ...and their ΔT must span at least this (°C)


# --- readings -------------------------------------------------------------------


def parse_ts(text) -> Optional[float]:
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def value_of(st: dict, attribute: Optional[str] = None) -> Optional[float]:
    """A state's number: its state, or an attribute (a climate entity's current_temperature)."""
    if not st:
        return None
    raw = (st.get("attributes") or {}).get(attribute) if attribute else st.get("state")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def temperature_attribute(entity_id: str) -> Optional[str]:
    """Where an entity keeps its temperature: climate in current_temperature, weather in
    temperature, sensors in their state."""
    domain = (entity_id or "").split(".", 1)[0]
    return {"climate": "current_temperature", "weather": "temperature"}.get(domain)


def series(rows: Iterable[dict], attribute: Optional[str] = None, gaps: bool = False) -> list:
    """History rows (state, attributes, last_changed / last_updated) to [(ts, value)], oldest
    first. With an attribute, last_updated (attribute changes don't move last_changed). With
    gaps, unavailable / unknown states stay in as None (so the reading before doesn't hold
    through them); without, they're left out."""
    out = []
    for row in rows or []:
        ts = parse_ts(row.get("last_updated") if attribute else row.get("last_changed")) \
            or parse_ts(row.get("last_changed")) or parse_ts(row.get("last_updated"))
        value = value_of(row, attribute)
        if ts is not None and (value is not None or gaps):
            out.append((ts, value))
    out.sort(key=lambda p: p[0])
    return out


def gas_deltas(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """kWh used between readings of an accumulating sensor, at each reading's time. A drop is a
    reset (midnight): the new reading is all new use."""
    out, prev = [], None
    for ts, value in points:
        if prev is not None:
            delta = value - prev if value >= prev - 0.001 else value
            out.append((ts, max(0.0, delta)))
        prev = value
    return out


def step_mean(points: list[tuple[float, float]], start: float, end: float) -> tuple[Optional[float], float]:
    """The time-weighted mean of a step series over [start, end) (each reading holds until the
    next), and how much of the time it covers (0..1): before the first reading, and while
    it's unavailable (None), isn't covered."""
    if end <= start:
        return None, 0.0
    total = weight = 0.0
    for n, (ts, value) in enumerate(points):
        if value is None:
            continue
        nxt = points[n + 1][0] if n + 1 < len(points) else end
        a, b = max(ts, start), min(nxt, end)
        if b > a:
            total += value * (b - a)
            weight += b - a
    return (total / weight if weight else None), weight / (end - start)


# --- the boiler -------------------------------------------------------------------


def efficiency(flow_c: Optional[float], fixed: Optional[float] = None) -> float:
    """A condensing boiler's efficiency at a flow temperature: about 97% at 35 °C, falling to
    about 87% at 70 °C (the return is cooler, so less condenses). A fixed one wins."""
    if fixed:
        return fixed
    if flow_c is None:
        return 0.9
    return max(0.80, min(0.97, 0.97 - (flow_c - 35) * 0.003))


# --- a day ----------------------------------------------------------------------------


def day_bounds(day: datetime.date, tz) -> tuple[float, float]:
    start = datetime.datetime.combine(day, datetime.time(), tz)
    end = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(), tz)
    return start.timestamp(), end.timestamp()


def summarise_day(day: datetime.date, tz, *, gas: list, outside: list, floors: list,
                  flow: Optional[list] = None, flow_fixed: Optional[float] = None,
                  efficiency_fixed: Optional[float] = None) -> dict:
    """One day's summary. gas/outside/flow: [(ts, value)] (gas: the accumulating readings, from
    a little before the day); floors: [(points, area, name)]. Returns the day's gas, heat, mean
    temperatures, ΔT and half hours, with complete False and why when it can't be used."""
    start, end = day_bounds(day, tz)
    edges = [start + n * HH for n in range(int(round((end - start) / HH)) + 1)]
    deltas = gas_deltas(gas)
    in_day = [(ts, kwh) for ts, kwh in deltas if start <= ts < end]
    # Each reading's use goes in the half hour it arrived in
    hh = []
    for a, b in zip(edges, edges[1:]):
        g = sum(kwh for ts, kwh in in_day if a <= ts < b)
        out_t, _ = step_mean(outside, a, b)
        floor_t = [step_mean(points, a, b)[0] for points, _, _ in floors]
        flow_t = flow_fixed if flow is None else step_mean(flow, a, b)[0]
        hh.append({"gas": round(g, 3), "out": _r(out_t), "in": _r(_weighted(floor_t, [f[1] for f in floors])),
                   "flow": _r(flow_t)})
    gas_kwh = sum(kwh for _, kwh in in_day)
    heat = sum(h["gas"] * efficiency(h["flow"] if h["flow"] is not None else flow_fixed, efficiency_fixed) for h in hh)
    out_mean, out_cover = step_mean(outside, start, end)
    floor_means = [step_mean(points, start, end) for points, _, _ in floors]
    in_mean = _weighted([m for m, _ in floor_means], [f[1] for f in floors])
    in_cover = min((c for m, c in floor_means), default=0.0)
    if flow is None:
        flow_mean = flow_fixed
    else:  # while the boiler's burning: what its efficiency follows
        burning = [(h["flow"], h["gas"]) for h in hh if h["gas"] > 0 and h["flow"] is not None]
        flow_mean = (sum(f * g for f, g in burning) / sum(g for _, g in burning)) if burning else step_mean(flow, start, end)[0]
    why = _incomplete(gas, start, end, out_cover, in_cover, floors)
    d_t = None if in_mean is None or out_mean is None else in_mean - out_mean
    return {
        "date": day.isoformat(),
        "gas_kwh": round(gas_kwh, 2), "heat_kwh": round(heat, 2),
        "efficiency": round(heat / gas_kwh, 3) if gas_kwh else round(efficiency(flow_mean, efficiency_fixed), 3),
        "outside": _r(out_mean), "inside": _r(in_mean), "dt": _r(d_t), "flow": _r(flow_mean),
        "floors": [{"name": name, "temp": _r(m)} for (_, _, name), (m, _) in zip(floors, floor_means)],
        "complete": why is None, "why": why,
        "hh": hh,
    }


def _incomplete(gas, start, end, out_cover, in_cover, floors) -> Optional[str]:
    times = [ts for ts, _ in gas]
    inside = [ts for ts in times if start <= ts < end]
    if not inside:
        return "No gas readings"
    if not any(ts < start for ts in times) and inside[0] - start > EDGE_S:
        return "Gas readings start late"
    if end - inside[-1] > EDGE_S:
        return "Gas readings stop early"
    near = [ts for ts in times if start - EDGE_S <= ts <= end + EDGE_S]
    if any(b - a > MAX_GAS_GAP_S for a, b in zip(near, near[1:])):
        return "A gap in the gas readings"
    if out_cover < MIN_TEMP_COVER:
        return "Outside temperature missing"
    if not floors:
        return "No inside temperature"
    if in_cover < MIN_TEMP_COVER:
        return "Inside temperature missing"
    return None


def _weighted(values: list, weights: list) -> Optional[float]:
    pairs = [(v, w if w and w > 0 else 1.0) for v, w in zip(values, weights) if v is not None]
    total = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total if total else None


def _r(v, d: int = 2):
    return None if v is None else round(v, d)


# --- the fit -----------------------------------------------------------------------------


def fit(days: list[dict], min_dt: float = 5.0, excluded: Iterable[str] = ()) -> dict:
    """The straight line through heat per day against ΔT, from the complete days with ΔT at
    least min_dt that aren't excluded. heat_loss (W/K) is its slope ÷ 24 h; baseline (kWh/day)
    is where it crosses ΔT 0 (hot water and cooking, less the free heat)."""
    skip = set(excluded or ())
    used = [d for d in days if d.get("complete") and d.get("dt") is not None and d["dt"] >= min_dt
            and d["date"] not in skip]
    out = {"ok": False, "days": len(used), "used": [d["date"] for d in used]}
    if len(used) < MIN_DAYS:
        out["why"] = f"Needs {MIN_DAYS} days with ΔT of {min_dt:g} °C or more: {len(used)} so far"
        return out
    xs, ys = [d["dt"] for d in used], [d["heat_kwh"] for d in used]
    if max(xs) - min(xs) < MIN_SPREAD:
        out["why"] = f"Needs days at more different temperatures (ΔT spans {max(xs) - min(xs):.1f} °C so far)"
        return out
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    se = math.sqrt(resid / (n - 2) / sxx) if n > 2 else 0.0
    out.update({
        "ok": slope > 0,
        "slope_kwh_per_k_day": round(slope, 3), "baseline_kwh": round(intercept, 2),
        "heat_loss_w_k": round(slope * 1000 / 24, 1), "plus_minus_w_k": round(1.96 * se * 1000 / 24, 1),
        "r2": round(sxy * sxy / (sxx * syy), 3) if syy else 1.0,
        "dt_range": [round(min(xs), 1), round(max(xs), 1)],
    })
    if slope <= 0:
        out["why"] = "The days don't show more heat on colder days yet"
    return out


def results(days: list[dict], *, min_dt: float, excluded: Iterable[str], design_in: float, design_out: float,
            area_m2: Optional[float], efficiency_note: str = "") -> dict:
    """The fit, and what it means: the heat loss at the design temperatures (kW, as a heat pump
    survey quotes it) and per m² of floor."""
    f = fit(days, min_dt, excluded)
    if f["ok"]:
        design_w = f["heat_loss_w_k"] * (design_in - design_out)
        f["design_kw"] = round(design_w / 1000, 2)
        f["design_plus_minus_kw"] = round(f["plus_minus_w_k"] * (design_in - design_out) / 1000, 2)
        if area_m2:
            f["design_w_m2"] = round(design_w / area_m2, 1)
            f["heat_loss_w_k_m2"] = round(f["heat_loss_w_k"] / area_m2, 2)
    f["design_in"], f["design_out"], f["area_m2"] = design_in, design_out, area_m2
    return f
