"""Automatic schedule (Automations tab): plug in before the best export prices.

From your import and export rates (each half hour's price, from Home Assistant):

- Off-peak times: the cheapest import half hours (an hour or more together, not
  ones made cheap by a smart charging slot), e.g. 23:30-05:30 on Intelligent
  Octopus Go. Nothing automatic plugs in then: it's cheap anyway.
- Charging blocks: the `blocks` half hours with the best export prices are picked
  (over all the rates known, not in an off-peak time), and each run of them (back to
  back half hours) gets as many half hours of charging straight before it, earlier
  where that's taken: e.g. exports 16:00-17:30 and 18:00-19:00 with 5 blocks charge
  14:00-16:00 and 17:30-18:00. One that can't be charged for before it (too soon) is
  passed over, as is one that would take a day over your supplier's daily limit.

Rates are read from (found automatically, or picked):
- Octopus Energy / EDF Energy (BottlecapDave's integration and the one based on it):
  event.<..>_electricity_<serial>_<mpan>[_export]_current_day_rates / _next_day_rates,
  attribute rates: [{start, end, value_inc_vat (£/kWh), is_intelligent_adjusted}].
- Predbat: predbat.rates / predbat.rates_export, attribute results: {time: p/kWh}
  (any supplier, e.g. E.ON Next, which has no rates in Home Assistant).
- Anything else with a rates list ({start, end, value / price / rate}) or Nord Pool's
  raw_today / raw_tomorrow.

Kept free of I/O so it can be tested on its own.
"""

from __future__ import annotations

import datetime
from typing import Callable, Optional

HH = datetime.timedelta(minutes=30)
OFFPEAK_MIN = datetime.timedelta(hours=1)  # cheapest half hours together at least this long
CHEAP_WITHIN_P = 0.5  # p/kWh above the cheapest that still counts as off-peak
MAX_BLOCKS = 48

_DAYS = ("_current_day_rates", "_next_day_rates")


def _with_rates() -> list:
    """The suppliers whose integrations publish rates (rates_prefix in src/suppliers.json)."""
    from src import suppliers
    return [s for s in suppliers.all_suppliers() if s.get("rates_prefix")]


def _when(value) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        t = value
    else:
        try:
            t = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return t if t.tzinfo else None


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_rates(state: Optional[dict]) -> list:
    """An entity's rates as half hours: [(start, end, pence per kWh, adjusted)], in
    time order. adjusted: made cheap by a smart charging slot (Octopus Intelligent)."""
    attrs = (state or {}).get("attributes") or {}
    unit = str(attrs.get("unit_of_measurement") or attrs.get("unit") or "").lower()
    items = []  # (start, end, value, in pounds?, adjusted)
    if isinstance(attrs.get("rates"), list):
        adjusted_fields = {s["rates_adjusted_field"] for s in _with_rates() if s.get("rates_adjusted_field")}
        for r in attrs["rates"]:
            if not isinstance(r, dict):
                continue
            if "value_inc_vat" in r:
                v, pounds = _num(r["value_inc_vat"]), True  # (BottlecapDave: £/kWh)
            else:
                v = _num(next((r[k] for k in ("value", "price", "rate", "value_inc_vat") if k in r), None))
                pounds = None
            adjusted = any(r.get(f) for f in adjusted_fields)
            items.append((_when(r.get("start") or r.get("from")), _when(r.get("end") or r.get("to")), v, pounds, adjusted))
    elif isinstance(attrs.get("results"), dict):  # Predbat: {time: p/kWh}
        starts = sorted((t, _num(v)) for t, v in ((_when(k), v) for k, v in attrs["results"].items()) if t)
        for n, (t, v) in enumerate(starts):
            end = starts[n + 1][0] if n + 1 < len(starts) else t + HH
            items.append((t, min(end, t + datetime.timedelta(hours=2)), v, False, False))
    else:  # Nord Pool
        for key in ("raw_today", "raw_tomorrow"):
            for r in attrs.get(key) or []:
                if isinstance(r, dict):
                    items.append((_when(r.get("start")), _when(r.get("end")), _num(r.get("value")),
                                  None if attrs.get("price_in_cents") is None else not attrs.get("price_in_cents"), False))
    items = [x for x in items if x[0] and x[1] and x[2] is not None and x[1] > x[0]]
    if not items:
        return []
    # In pounds unless it says otherwise: a £ unit, or every price under £2 (no p/kWh rate is)
    guess = "gbp" in unit or "£" in unit or (("p/" not in unit and "pence" not in unit)
                                             and max(abs(v) for _, _, v, _, _ in items) < 2)
    out = {}
    for a, b, v, pounds, adj in items:
        p = v * 100 if (guess if pounds is None else pounds) else v
        t = a
        while t < b:  # (in half hours)
            out[t] = (t, min(t + HH, b), round(p, 3), adj)
            t += HH
    return [out[k] for k in sorted(out)]


def merge(*lists) -> list:
    """Several entities' rates as one list (a later one wins for the same half hour)."""
    out = {}
    for rates in lists:
        for r in rates:
            out[r[0]] = r
    return [out[k] for k in sorted(out)]


def is_rates_entity(state: dict) -> bool:
    return len(parse_rates(state)) >= 2


def find_rate_entities(states: list) -> dict:
    """The rates entities found: {"import": [ids], "export": [ids], "source": name}.
    Octopus Energy / EDF Energy (today's and tomorrow's), else Predbat's."""
    by_id = {st.get("entity_id"): st for st in states or []}
    for supplier in _with_rates():
        imp, exp = [], []
        for eid in sorted(by_id):
            if not eid.startswith(supplier["rates_prefix"]) or not eid.endswith(_DAYS[0]):
                continue
            if not isinstance((by_id[eid].get("attributes") or {}).get("rates"), list):
                continue
            base = eid[: -len(_DAYS[0])]
            ids = [base + d for d in _DAYS if base + d in by_id]
            (exp if base.endswith("_export") else imp).append(ids)
        if imp or exp:
            return {"import": imp[0] if imp else [], "export": exp[0] if exp else [], "source": supplier["name"]}
    imp = ["predbat.rates"] if "predbat.rates" in by_id else []
    exp = ["predbat.rates_export"] if "predbat.rates_export" in by_id else []
    return {"import": imp, "export": exp, "source": "Predbat" if imp or exp else None}


def entities_for(chosen: str, states: list) -> list:
    """A picked entity, with tomorrow's rates too where it's today's (Octopus, EDF)."""
    ids = {st.get("entity_id") for st in states or []}
    if chosen.endswith(_DAYS[0]) and chosen[: -len(_DAYS[0])] + _DAYS[1] in ids:
        return [chosen, chosen[: -len(_DAYS[0])] + _DAYS[1]]
    return [chosen]


def offpeak(imp: list, after: datetime.datetime) -> list:
    """The cheapest import half hours, an hour or more together, still to end: [(a, b)].
    None with a flat rate (no cheaper times)."""
    base = [r for r in imp if not r[3]]
    if not base:
        return []
    low, high = min(r[2] for r in base), max(r[2] for r in base)
    if high - low < 1:
        return []
    runs = []
    for a, b, p, _ in base:
        if p > low + CHEAP_WITHIN_P:
            continue
        if runs and runs[-1][1] == a:
            runs[-1] = (runs[-1][0], b)
        else:
            runs.append((a, b))
    return [(a, b) for a, b in runs if b - a >= OFFPEAK_MIN and b > after]


def _ceil_hh(t: datetime.datetime) -> datetime.datetime:
    """The next half hour boundary at or after t."""
    m = t.replace(minute=0, second=0, microsecond=0)
    while m < t:
        m += HH
    return m


def _runs(halves: list) -> list:
    """Half hour starts in time order -> runs [[start, ...]] of back to back ones."""
    runs = []
    for t in sorted(halves):
        if runs and runs[-1][-1] + HH == t:
            runs[-1].append(t)
        else:
            runs.append([t])
    return runs


def _place(exports: list, earliest: datetime.datetime, blocked: Callable[[datetime.datetime], bool]) -> Optional[list]:
    """A charging half hour for each export half hour, straight before its run
    (earlier where that's taken). None if one can't be (too soon)."""
    taken, charges = set(exports), set()
    for run in _runs(exports):
        need, t = len(run), run[0] - HH
        while need:
            if t < earliest:
                return None
            if t not in taken and t not in charges and not blocked(t):
                charges.add(t)
                need -= 1
            t -= HH
    return sorted(charges)


def plan(imp: list, exp: list, now: datetime.datetime, blocks: int, cuts: list = (),
         cap_min: Optional[int] = None, day_of: Optional[Callable[[datetime.datetime], object]] = None,
         used: Optional[Callable[[object], int]] = None) -> dict:
    """The automatic schedule: {"charge": [(a, b)], "exports": [(a, b, p)], "offpeak": [(a, b)],
    "notes": [...]}. cuts: times nothing may plug in (off-peak times, saving sessions);
    cap_min with day_of (a time -> its limit day) and used (a limit day -> minutes other
    charges already have in it): no limit day gets more than cap_min."""
    blocks = max(0, min(int(blocks), MAX_BLOCKS))
    off = offpeak(imp, now)
    nope = list(cuts) + off
    earliest = _ceil_hh(now)

    def blocked(t):
        return any(a < t + HH and b > t for a, b in nope)

    price = {r[0]: r[2] for r in exp}
    cands = sorted((t for t in price if t >= earliest and not blocked(t)), key=lambda t: (-price[t], t))
    chosen, charges, notes = [], [], []
    for t in cands:
        if len(chosen) >= blocks:
            break
        trial = _place(chosen + [t], earliest, blocked)
        if trial is None:
            continue
        if cap_min and day_of:
            per = {}
            for c in trial:
                per[day_of(c)] = per.get(day_of(c), 0) + 30
            if any(m + (used(d) if used else 0) > cap_min for d, m in per.items()):
                continue
        chosen.append(t)
        charges = trial
    if blocks and len(chosen) < blocks:
        notes.append(f"{len(chosen)} of {blocks} half hours planned: "
                     + ("no export rates known" if not price else
                        "not enough export half hours with time to charge before them, within the daily limit"))
    windows = []
    for c in charges:
        if windows and windows[-1][1] == c:
            windows[-1] = (windows[-1][0], c + HH)
        else:
            windows.append((c, c + HH))
    return {"charge": windows, "exports": [(t, t + HH, price[t]) for t in sorted(chosen)], "offpeak": off,
            "notes": notes}
