"""Automatic schedule (Automations tab): charge where exporting pays least.

From your import and export rates (each half hour's price, from Home Assistant):

- Off-peak times: the cheapest import half hours (an hour or more together, not
  ones made cheap by a smart charging slot), e.g. 23:30-05:30 on Intelligent
  Octopus Go. Nothing automatic plugs in then: it's cheap anyway.
- Charging: each day (your supplier's limit day, e.g. 12:00 to 12:00, else midnight to
  midnight) that the export rates are known for gets its `blocks` half hours of charging
  in the free half hours still to come with the lowest export prices (not an off-peak
  time, a saving session or a charge that comes first). With Predbat's rates, days that
  are a copy of the day before (its guess at rates not published yet) aren't used; a
  supplier's own rates (e.g. Octopus Energy's next day rates) are used even when they're
  the same as the day before (e.g. a tariff with the same times each day).

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


FLAT_P = 1.0  # a day whose export prices are all within this (p/kWh) has no best times


def known_only(rates: list, copies: bool = True) -> list:
    """The export rates without the days that can't say when's best: a copy of the day before
    (Predbat fills the days not published yet, e.g. tomorrow's before Octopus's 16:00, with the
    last day's; copies False: rates that are only ever published, e.g. Octopus Energy's next
    day rates, where the same as the day before is real), or all one price (e.g. Octopus Energy's integration, before tomorrow's Agile
    rates are out, has the account's fixed export rate for it): from the first such day on,
    they're left out (a flat day already under way stays: it's just the rest of today)."""
    if not rates:
        return rates
    tz = rates[0][0].tzinfo
    by = {r[0]: r[2] for r in rates}
    days = sorted({r[0].astimezone(tz).date() for r in rates})
    for d in days[1:]:
        prices = [p for t, p in by.items() if t.astimezone(tz).date() == d]
        if len(prices) >= 8 and max(prices) - min(prices) < FLAT_P:
            return [r for r in rates if r[0].astimezone(tz).date() < d]
        mine = [(t, p) for t, p in by.items() if t.astimezone(tz).date() == d
                and t - datetime.timedelta(days=1) in by]
        if copies and len(mine) >= 8 and all(by[t - datetime.timedelta(days=1)] == p for t, p in mine):
            return [r for r in rates if r[0].astimezone(tz).date() < d]
    return rates


def _windows(halves) -> list:
    out = []
    for c in sorted(halves):
        if out and out[-1][1] == c:
            out[-1] = (out[-1][0], c + HH)
        else:
            out.append((c, c + HH))
    return out


def _plan_before(price, future, days, day_of, blocks, cap_min, used, own, fits, nope, highs, off, notes, hits) -> dict:
    """plan(mode="before"): a charge straight before each run of high export half hours."""
    earliest = future[0] if future else None
    highs = set(highs)
    need = {}
    for d in days:
        want = blocks * 30 - (own(d) if own else 0)
        if cap_min:
            want = min(want, cap_min - (used(d) if used else 0))
        need[d] = max(0, want // 30)
    runs = _windows(highs)
    charges = set()
    short = 0
    # (the dearest runs first, so they get their charge if a day hasn't room for all)
    order = sorted(range(len(runs)), key=lambda i: -max((price.get(t, 0) for t in highs if runs[i][0] <= t < runs[i][1]), default=0))
    for i in order:
        a, b = runs[i]
        if b <= (earliest or b):  # (gone)
            continue
        want = int((b - a) / HH)
        floor = max([earliest] + [y for x, y in runs if y <= a])
        t, got = a - HH, 0
        while got < want and t >= floor:
            d = day_of(t)
            if t in price and t not in highs and t not in charges and not hits(t, nope) and need.get(d, 0) > 0 \
                    and (not fits or fits(charges | {t})):
                charges.add(t)
                need[d] -= 1
                got += 1
            t -= HH
        short += want - got
    if not highs:
        notes.append("Nothing planned: no high export half hours marked (set High export half hours a day)")
    elif short:
        notes.append(f"{short * 30} min of charging before the high export times didn't fit (no room before them, "
                     "the day's charging half hours or its limit)")
    return {"charge": _windows(charges), "offpeak": off, "notes": notes, "known": [(x, x + HH) for x in future]}


def _halves(win):
    t, out = win[0], []
    while t < win[1]:
        out.append(t)
        t += HH
    return out


def plan(imp: list, exp: list, now: datetime.datetime, blocks: int, cuts: list = (),
         cap_min: Optional[int] = None, day_of: Optional[Callable[[datetime.datetime], object]] = None,
         used: Optional[Callable[[object], int]] = None, busy: list = (),
         fits: Optional[Callable[[set], bool]] = None, sessions: list = (), pre: list = (),
         plugged: list = (), own: Optional[Callable[[object], int]] = None,
         seen: Optional[dict] = None, copies: bool = True, mode: str = "cheapest", highs=()) -> dict:
    """The automatic schedule: {"charge": [(a, b)], "offpeak": [(a, b)], "notes": [...], "known": [(a, b)]}.

    mode "before": the charging goes straight before each run of the high export half hours
    (`highs`: their starts, e.g. each day's highest marked), as long as the run, as far as there's
    room (back to the run before, or now; free half hours only), the dearest runs first, up to the
    day's charging half hours. Otherwise ("cheapest"):

    Each day (day_of: a time -> its day, e.g. the daily limit's 12:00 to 12:00; else midnight to
    midnight) whose export rates are known gets `blocks` half hours of charging in the half hours
    still to come that exporting pays least in, still free (not in `cuts` (off-peak times, saving
    sessions), an off-peak time from the import rates, a charge that comes first (`busy`) or the
    time already plugged in, `plugged`). own: a day -> the minutes of its own charging it's had
    already: they count in its `blocks`. used: a day -> the minutes it's plugged in for already
    (what's been, charges that come first, your slots kept): with cap_min, no day gets more than
    cap_min (so they count only near the limit). fits: (the charging half hours) -> whether
    they're within the limit (a rolling one: any 24 or 48 hours). (sessions, pre and seen: no
    longer used, kept for callers.)"""
    blocks = max(0, min(int(blocks), MAX_BLOCKS))
    day_of = day_of or (lambda t: t.date())
    off = offpeak(imp, now)
    nope = list(cuts) + off + list(busy) + list(plugged)  # no charging then
    earliest = _ceil_hh(now)

    def hits(t, wins):
        return any(a < t + HH and b > t for a, b in wins)

    price = {r[0]: r[2] for r in known_only(exp, copies)}
    notes = []
    future = sorted(t for t in price if t >= earliest)
    days = sorted({day_of(t) for t in future})
    charges = set()
    if mode == "before":
        return _plan_before(price, future, days, day_of, blocks, cap_min, used, own, fits, nope, highs, off, notes, hits)
    for d in days:
        # How much charging the day still needs: its `blocks` (less what it's had already), within the limit
        want = blocks * 30 - (own(d) if own else 0)
        if cap_min:
            want = min(want, cap_min - (used(d) if used else 0))
        n = max(0, want // 30)
        if not n:
            continue
        got = 0
        for t in sorted((t for t in future if day_of(t) == d and not hits(t, nope)), key=lambda t: (price[t], t)):
            if got >= n:
                break
            if fits and not fits(charges | {t}):
                continue
            charges.add(t)
            got += 1
        if got < n:
            notes.append(f"The day from {d.strftime('%a %H:%M') if isinstance(d, datetime.datetime) else d}: "
                         f"{got * 30} of {n * 30} min of charging placed (no more free half hours with known rates"
                         + (" within the daily limit" if fits else "") + ")")
    if blocks and not future:
        notes.append("Nothing planned: " + ("no export rates known" if not price else "no export half hours still to come"))
    windows = []
    for c in sorted(charges):
        if windows and windows[-1][1] == c:
            windows[-1] = (windows[-1][0], c + HH)
        else:
            windows.append((c, c + HH))
    return {"charge": windows, "offpeak": off, "notes": notes, "known": [(a, a + HH) for a in future]}
