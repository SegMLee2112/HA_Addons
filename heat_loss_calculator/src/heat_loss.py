"""The heat loss calculation: Home Assistant history in, your home's heat loss out.

Pure (no I/O) so it can be tested on its own; src/__main__.py fetches the
history and stores the days.

1. Each day (local midnight to midnight) is summarised from the history of
   the gas sensor, the outside temperature, the flow temperature (or a fixed
   one) and the inside thermostats:
   - gas: each half hour's use from the sensor's charges attribute (Octopus
     Energy's accumulative consumption sensors list every half hour, with
     its start, whenever the data arrives, which can be hours later); for
     other sensors, the change between readings (a drop is a reset at
     midnight, so the new reading is all new use);
   - heat: each half hour's gas times the boiler's efficiency at that half
     hour's flow temperature;
   - temperatures: time-weighted means (a reading holds until the next);
     inside is the floors' means weighted by their floor area.
   - hot water: on a combi, gas burnt while no floor's thermostat is calling
     for heat is hot water (showers, taps), not heat for the house, and is
     taken out; or a set amount a day. Calling: its hvac_action, or (most
     thermostats don't have one) on heat and under its set temperature.
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


GAS_MAX_KW = 60.0  # no home boiler burns faster: a bigger jump in the readings is a glitch
ELECTRIC_MAX_KW = 15.0  # ...nor does an air con unit or heater use more


def gas_deltas(points: list[tuple[float, float]], max_kw: Optional[float] = None) -> list[tuple[float, float]]:
    """kWh used between readings of an accumulating sensor, at each reading's time. A drop is a
    reset (midnight): the new reading is all new use. With max_kw, a jump faster than that is
    a glitch, not use: e.g. a lifetime total that drops to 0 for a moment (a restart) and comes
    back, which would otherwise count the whole total as used."""
    out, prev, prev_ts = [], None, None
    for ts, value in points:
        if prev is not None:
            delta = max(0.0, value - prev if value >= prev - 0.001 else value)
            hours = max((ts - prev_ts) / 3600, 1 / 60)
            if max_kw is not None and delta > max_kw * hours + 0.05:
                delta = 0.0
            out.append((ts, delta))
        prev, prev_ts = value, ts
    return out


def energy_series(rows: Iterable[dict]) -> list[tuple[float, float]]:
    """An energy sensor's history to [(ts, kWh)]: in kWh whatever it reports in (Wh, kWh, MWh)."""
    scale = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}
    out = []
    for row in rows or []:
        unit = str((row.get("attributes") or {}).get("unit_of_measurement") or "kWh").strip().lower()
        for ts, value in series([row]):
            out.append((ts, value * scale.get(unit, 1.0)))
    out.sort(key=lambda p: p[0])
    return out


def gas_charges(rows: Iterable[dict]) -> dict[int, float]:
    """Each half hour's gas (kWh) by its start (s since the epoch), from the charges attribute
    of an Octopus Energy accumulative consumption sensor's history: [{start, end, consumption
    or consumption_kwh}]. A later state's figure for the same half hour wins."""
    out: dict[int, float] = {}
    for row in rows or []:
        for charge in (row.get("attributes") or {}).get("charges") or []:
            if not isinstance(charge, dict):
                continue
            start = parse_ts(charge.get("start"))
            kwh = charge.get("consumption_kwh", charge.get("consumption"))
            try:
                kwh = float(kwh)
            except (TypeError, ValueError):
                continue
            if start is not None and math.isfinite(kwh):
                out[int(round(start))] = max(0.0, kwh)
    return out


CALL_BELOW = 0.3  # a thermostat without hvac_action calls for heat until this close to its set temperature (°C)


def heating_series(rows: Iterable[dict]) -> list:
    """A climate entity's history to [(ts, 1.0 calling for heat / 0.0 not / None unknown)]:
    from its hvac_action attribute, or, when it doesn't have one, worked out: on heat and more
    than CALL_BELOW under its set temperature (off: not calling)."""
    out = []
    for row in rows or []:
        ts = parse_ts(row.get("last_updated")) or parse_ts(row.get("last_changed"))
        if ts is not None:
            out.append((ts, _calling(row)))
    out.sort(key=lambda p: p[0])
    return out


def _calling(row: dict) -> Optional[float]:
    attrs = row.get("attributes") or {}
    action = attrs.get("hvac_action")
    if action not in (None, "", "unavailable", "unknown"):
        return 1.0 if action == "heating" else 0.0
    state = row.get("state")
    if state == "off":
        return 0.0
    if state not in ("heat", "auto", "heat_cool"):
        return None
    current, target = value_of(row, "current_temperature"), value_of(row, "temperature")
    if current is None or target is None:
        return None
    return 1.0 if current < target - CALL_BELOW else 0.0


def calling_from(rows: Iterable[dict]) -> Optional[str]:
    """How a thermostat's calling for heat is known: "reported" (hvac_action), "worked out"
    (its temperature against what it's set to), or None."""
    rows = list(rows or [])
    if any((r.get("attributes") or {}).get("hvac_action") not in (None, "", "unavailable", "unknown") for r in rows):
        return "reported"
    if any(_calling(r) is not None for r in rows):
        return "worked out"
    return None


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
                  efficiency_fixed: Optional[float] = None, charges: Optional[dict] = None,
                  hot_water: str = "none", calling: Optional[list] = None, hot_water_kwh: float = 0.0,
                  calling_from: Optional[str] = None, extra: Optional[list] = None) -> dict:
    """One day's summary. gas/outside/flow: [(ts, value)] (gas: the accumulating readings, from
    a little before the day); charges: each half hour's gas by its start (gas_charges), used
    instead of the readings when it has any of the day; floors: [(points, area, name)]. Returns
    the day's gas, heat, mean temperatures, ΔT and half hours, with complete False and why when
    it can't be used.

    extra: other heating, [(points, cop, name)]: an energy sensor's readings (kWh, adding up,
    and may start again at midnight) from a little before the day, and its COP (heat out for
    each kWh in: about 3.5 for air-to-air heat pumps, 1 for a plain electric heater). Its heat
    is added to the boiler's.

    hot_water: "auto" takes the gas of each half hour no floor called for heat in (calling:
    heating_series per floor) as hot water; "fixed" takes hot_water_kwh a day; "none" counts
    all the gas as heating. Hot water isn't heat for the house, so it's left out of heat_kwh."""
    start, end = day_bounds(day, tz)
    edges = [start + n * HH for n in range(int(round((end - start) / HH)) + 1)]
    starts = [int(round(a)) for a in edges[:-1]]
    if charges and any(t in charges for t in starts):  # each half hour's own figure, whenever it arrived
        gas_from = "half hours"
        in_day = [(t, charges[t]) for t in starts if t in charges]
        short = len(starts) - len(in_day)
        gas_why = f"Gas missing for {short} half hour{'' if short == 1 else 's'}" if short else None
    else:  # each reading's use goes in the half hour it arrived in
        gas_from = "readings"
        in_day = [(ts, kwh) for ts, kwh in gas_deltas(gas, GAS_MAX_KW) if start <= ts < end]
        gas_why = _gas_gaps(gas, start, end)
    extra = [(points, cop, name) for points, cop, name in (extra or [])]
    extra_in_day = [[(ts, kwh) for ts, kwh in gas_deltas(points, ELECTRIC_MAX_KW) if start <= ts < end]
                    for points, _, _ in extra]
    hh = []
    for a, b in zip(edges, edges[1:]):
        g = sum(kwh for ts, kwh in in_day if a <= ts < b)
        other = sum(kwh * cop for e_day, (_, cop, _) in zip(extra_in_day, extra) for ts, kwh in e_day if a <= ts < b)
        out_t, _ = step_mean(outside, a, b)
        floor_t = [step_mean(points, a, b)[0] for points, _, _ in floors]
        flow_t = flow_fixed if flow is None else step_mean(flow, a, b)[0]
        hh.append({"gas": round(g, 3), "out": _r(out_t), "in": _r(_weighted(floor_t, [f[1] for f in floors])),
                   "flow": _r(flow_t), "dhw": _hot_water(g, calling, a, b) if hot_water == "auto" else None,
                   "extra": round(other, 3) if extra else None})
    gas_kwh = sum(kwh for _, kwh in in_day)
    known = [h["dhw"] for h in hh if h["dhw"] is not None]
    if hot_water == "auto" and len(known) >= 0.8 * len(hh):  # the thermostats said when they were calling
        dhw_from, dhw = "heating off", sum(known)
    elif hot_water == "fixed":
        dhw_from, dhw = "set", min(hot_water_kwh or 0.0, gas_kwh)
    else:
        dhw_from, dhw = None, 0.0
    for h in hh:
        h["dhw"] = round(h["dhw"], 3) if dhw_from == "heating off" and h["dhw"] is not None else None
    eff = lambda h: efficiency(h["flow"] if h["flow"] is not None else flow_fixed, efficiency_fixed)  # noqa: E731
    if dhw_from == "heating off":
        heat = sum((h["gas"] - (h["dhw"] or 0.0)) * eff(h) for h in hh)
    else:  # a set amount: off the day's gas, at the day's efficiency
        all_heat = sum(h["gas"] * eff(h) for h in hh)
        heat = all_heat * (1 - dhw / gas_kwh) if gas_kwh else 0.0
    space_gas = gas_kwh - dhw
    boiler_heat = heat
    others = [{"name": name, "kwh": round(sum(k for _, k in e_day), 2), "cop": cop,
               "heat_kwh": round(sum(k for _, k in e_day) * cop, 2), "read": any(v is not None for _, v in points)}
              for e_day, (points, cop, name) in zip(extra_in_day, extra)]
    heat += sum(o["heat_kwh"] for o in others)
    out_mean, out_cover = step_mean(outside, start, end)
    floor_means = [step_mean(points, start, end) for points, _, _ in floors]
    in_mean = _weighted([m for m, _ in floor_means], [f[1] for f in floors])
    in_cover = min((c for m, c in floor_means), default=0.0)
    if flow is None:
        flow_mean = flow_fixed
    else:  # while the boiler's burning: what its efficiency follows
        burning = [(h["flow"], h["gas"]) for h in hh if h["gas"] > 0 and h["flow"] is not None]
        flow_mean = (sum(f * g for f, g in burning) / sum(g for _, g in burning)) if burning else step_mean(flow, start, end)[0]
    unread = [o["name"] for o in others if not o["read"]]
    why = gas_why or _temps_missing(out_cover, in_cover, floors) or (f"No readings from {unread[0]}" if unread else None)
    d_t = None if in_mean is None or out_mean is None else in_mean - out_mean
    return {
        "date": day.isoformat(),
        "gas_kwh": round(gas_kwh, 2), "heat_kwh": round(heat, 2), "boiler_heat_kwh": round(boiler_heat, 2),
        "other": [{k: v for k, v in o.items() if k != "read"} for o in others],
        "dhw_kwh": round(dhw, 2) if dhw_from else None, "dhw_from": dhw_from,
        "calling_from": calling_from if dhw_from == "heating off" else None,
        "efficiency": round(boiler_heat / space_gas, 3) if space_gas > 0.01 else round(efficiency(flow_mean, efficiency_fixed), 3),
        "outside": _r(out_mean), "inside": _r(in_mean), "dt": _r(d_t), "flow": _r(flow_mean),
        "floors": [{"name": name, "temp": _r(m)} for (_, _, name), (m, _) in zip(floors, floor_means)],
        "complete": why is None, "why": why, "gas_from": gas_from,
        "hh": hh,
    }


def _hot_water(gas: float, calling: Optional[list], a: float, b: float) -> Optional[float]:
    """A half hour's hot water: all its gas when no floor called for heat in it, none when one
    did, None when the thermostats didn't say."""
    if not calling:
        return None
    seen = False
    for series in calling:
        frac, cover = step_mean(series, a, b)
        if cover < 0.5:
            continue
        seen = True
        if frac and frac > 0.05:
            return 0.0
    return gas if seen else None


def _gas_gaps(gas, start, end) -> Optional[str]:
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
    return None


def _temps_missing(out_cover, in_cover, floors) -> Optional[str]:
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
    out = {"ok": False, "days": len(used), "used": [d["date"] for d in used], "method": None}
    if len(used) < MIN_DAYS:
        out["why"] = f"Needs {MIN_DAYS} days with ΔT of {min_dt:g} °C or more for the full fit: {len(used)} so far"
        return estimate(used, out)
    xs, ys = [d["dt"] for d in used], [d["heat_kwh"] for d in used]
    if max(xs) - min(xs) < MIN_SPREAD:
        out["why"] = f"Needs days at more different temperatures for the full fit (ΔT spans {max(xs) - min(xs):.1f} °C so far)"
        return estimate(used, out)
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
        "ok": slope > 0, "method": "fit",
        "slope_kwh_per_k_day": round(slope, 3), "baseline_kwh": round(intercept, 2),
        "heat_loss_w_k": round(slope * 1000 / 24, 1), "plus_minus_w_k": round(1.96 * se * 1000 / 24, 1),
        "r2": round(sxy * sxy / (sxx * syy), 3) if syy else 1.0,
        "dt_range": [round(min(xs), 1), round(max(xs), 1)],
    })
    if slope <= 0:
        out["why"] = "The days don't show more heat on colder days yet"
    return out


def estimate(used: list[dict], out: dict) -> dict:
    """Too few days for a line: the heat loss from the heat over the ΔT of the days there are
    (a line through 0, as if the hot water and the free heat cancel out). From one day; ± from
    how much the days differ, when there are two or more."""
    days = [d for d in used if d["dt"] and d["dt"] > 0 and d["heat_kwh"] is not None]
    if not days:
        return out
    per_day = [d["heat_kwh"] / d["dt"] for d in days]  # kWh a day per °C
    slope = sum(d["heat_kwh"] for d in days) / sum(d["dt"] for d in days)
    n = len(days)
    pm = None
    if n >= 2:
        mean = sum(per_day) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in per_day) / (n - 1))
        pm = round(1.96 * sd / math.sqrt(n) * 1000 / 24, 1)
    xs = [d["dt"] for d in days]
    out.update({
        "ok": slope > 0, "method": "estimate",
        "slope_kwh_per_k_day": round(slope, 3), "baseline_kwh": 0.0,
        "heat_loss_w_k": round(slope * 1000 / 24, 1), "plus_minus_w_k": pm, "r2": None,
        "dt_range": [round(min(xs), 1), round(max(xs), 1)],
    })
    return out


def _fit_quality(f: dict, design_in: float, design_out: float) -> dict:
    """How much to trust the fit (feature 3): label, extrapolation and warnings."""
    if not f.get("ok"):
        return {"label": "Not enough data", "warnings": [f.get("why")] if f.get("why") else []}
    design_dt = design_in - design_out
    dt_range = f.get("dt_range") or [None, None]
    dt_max = dt_range[1]
    extrap = round(max(0.0, design_dt - dt_max), 1) if dt_max is not None else None
    n = f.get("days") or 0
    r2 = f.get("r2")
    warnings = []
    if extrap is not None and extrap > 2:
        warnings.append(f"Design ΔT is {extrap:g} K above your coldest measured day — the result is extrapolated")
    if n < 15:
        warnings.append(f"Only {n} day{'s' if n != 1 else ''} used — it firms up as more cold days come in")
    if r2 is not None and r2 < 0.6:
        warnings.append("High scatter (low R²) — check DHW/other heating, sensor readings, or excluded days")
    if f.get("method") == "estimate":
        label = "Estimate"
        if f.get("why"):
            warnings.insert(0, f["why"])
    else:
        strong = (r2 is not None and r2 >= 0.8 and n >= 15 and (extrap is None or extrap <= 2))
        ok = (r2 is not None and r2 >= 0.6 and n >= 7 and (extrap is None or extrap <= 5))
        label = "Strong" if strong else "OK" if ok else "Weak"
    return {"label": label, "n": n, "r2": r2, "dt_min": dt_range[0], "dt_max": dt_max,
            "design_dt": round(design_dt, 1), "extrapolation_k": extrap, "warnings": warnings,
            "method": f.get("method")}


def _validation(days: list[dict], f: dict, design_in: float, design_out: float) -> Optional[dict]:
    """Per-day measured/implied heat loss for the fitted days (feature 2)."""
    if not f.get("ok"):
        return None
    used = set(f.get("used") or [])
    design_dt = design_in - design_out
    slope = f.get("slope_kwh_per_k_day")
    baseline = f.get("baseline_kwh") or 0.0
    if slope is None or design_dt <= 0:
        return None
    rows = []
    for d in days:
        if d.get("date") not in used or not d.get("dt") or d["dt"] <= 0 or d.get("heat_kwh") is None:
            continue
        implied_w_k = d["heat_kwh"] * 1000 / (24 * d["dt"])
        predicted = slope * d["dt"] + baseline
        rows.append({
            "date": d["date"], "dt": d["dt"], "heat_kwh": d["heat_kwh"],
            "outside": d.get("outside"), "inside": d.get("inside"),
            "implied_w_k": round(implied_w_k, 1),
            "implied_design_kw": round(implied_w_k * design_dt / 1000, 2),
            "predicted_kwh": round(predicted, 2),
            "residual_kwh": round(d["heat_kwh"] - predicted, 2),
            "dhw_kwh": d.get("dhw_kwh"), "dhw_from": d.get("dhw_from"),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    if not rows:
        return None
    vals = [r["implied_design_kw"] for r in rows]
    res = [r["residual_kwh"] for r in rows]
    summary = {
        "implied_design_kw_avg": round(sum(vals) / len(vals), 2),
        "implied_design_kw_min": round(min(vals), 2),
        "implied_design_kw_max": round(max(vals), 2),
        "residual_mean_kwh": round(sum(res) / len(res), 2),
        "n": len(rows),
    }
    return {"days": rows, "summary": summary}


def results(days: list[dict], *, min_dt: float, excluded: Iterable[str], design_in: float, design_out: float,
            area_m2: Optional[float], efficiency_note: str = "") -> dict:
    """The fit, and what it means: the heat loss at the design temperatures (kW, as a heat pump
    survey quotes it) and per m² of floor."""
    f = fit(days, min_dt, excluded)
    if f["ok"]:
        design_w = f["heat_loss_w_k"] * (design_in - design_out)
        f["design_kw"] = round(design_w / 1000, 2)
        f["design_plus_minus_kw"] = None if f["plus_minus_w_k"] is None else \
            round(f["plus_minus_w_k"] * (design_in - design_out) / 1000, 2)
        if area_m2:
            f["design_w_m2"] = round(design_w / area_m2, 1)
            f["heat_loss_w_k_m2"] = round(f["heat_loss_w_k"] / area_m2, 2)
    f["design_in"], f["design_out"], f["area_m2"] = design_in, design_out, area_m2
    f["quality"] = _fit_quality(f, design_in, design_out)
    validation = _validation(days, f, design_in, design_out)
    if validation is not None:
        f["validation"] = validation
    return f
