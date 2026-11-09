"""Random scenarios against the schedule (2.96.0): your slots, the automatic schedule, saving
sessions, auto plug-in and Charge now turned on and off, slots and blocks moved, skipped and
deleted as the page does, and time passing, with what must always hold checked after each step
(tests/test_schedule_scenarios.py). Run by hand for more: python3 tests/schedule_scenarios.py 0 300"""
import sys, copy, random, datetime, traceback, logging
logging.disable(logging.CRITICAL)
from tests.test_auto_schedule import _auto, at, user_exp, iog_import, WEEK, D
from src.automation import (AUTO_SCHED_SOURCE, AUTO_SOURCE, CHARGE_NOW_SOURCE, SAVING_SOURCE, longest_day, _parse_date)

HH = datetime.timedelta(minutes=30)
SCHEDULES = [
    [("06:30", "08:00"), ("15:00", "17:00"), ("19:00", "20:00"), ("21:00", "22:30")],  # the user's
    [("15:30", "21:30")],
    [("01:00", "05:00")],
    [("22:00", "02:00")],  # overnight
    [("11:00", "13:00"), ("13:00", "14:00")],  # back to back across the reset
    [("17:00", "18:00"), ("18:00", "19:30")],
    [],
]

def hm(t): return t.strftime("%H:%M")

class Sim:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.a, self.clock = _auto(at(12, 25))
        a = self.a
        a.limit_reset = "12:00"
        a.ready_time = True
        sched = self.r.choice(SCHEDULES)
        ents = []
        for p, u in sched:
            ents += [{"time": p, "action": "plug", "days": WEEK}, {"time": u, "action": "unplug", "days": WEEK}]
        a.set_schedule(enabled=True, ready_time=True, entries=ents, daily_cap_min=360, allow_over=True)
        self.auto_on = False
        self.mode = self.r.choice(["cheapest", "before"])
        self.blocks = self.r.choice([4, 8, 12])
        self.save_on = False
        self.plug_hours = self.r.choice([None, 1.0, 2.0])
        s0 = self.r.choice([(17, 18), (18, 19), (16, 17, 30)])
        day = self.r.choice([0, 1])
        self.sessions = [{"start": at(s0[0], 0, day), "end": at(s0[1], s0[2] if len(s0) > 2 else 0, day), "summary": "Saving session"}]
        self.imp, self.exp = iog_import(3), user_exp(2)
        self.over = False  # (saved over the limit on purpose)
        self.log = []
        self.user_skips = []

    def now(self): return self.a._now()

    def tick(self):
        a = self.a
        if self.save_on:
            a.sync_saving_sessions(self.sessions, True, None, 360, "Octopus Energy", self.plug_hours)
        else:
            a.clear_saving_sessions()
        if self.mode == "before":
            a.set_export_rates(self.exp, {}, 4)
        if self.auto_on:
            a.sync_auto_schedule(self.imp, self.exp, self.blocks, cap_min=360, mode=self.mode)
        else:
            a.clear_auto_schedule()

    def yours(self):
        return [e for e in self.a.entries if not e.get("source") and not e.get("date")]

    def op(self):
        a, r = self.a, self.r
        kind = r.choice(["auto", "auto", "save", "save", "charge", "cancel", "move_yours", "move_yours", "edit_block",
                         "edit_block", "delete_oneoff", "skip", "advance", "advance", "advance", "enable"])
        try:
            if kind == "auto":
                self.auto_on = not self.auto_on
                self.log.append(f"auto {'on' if self.auto_on else 'off'}")
            elif kind == "save":
                self.save_on = not self.save_on
                self.log.append(f"saving {'on' if self.save_on else 'off'}")
            elif kind == "charge":
                src = r.choice([AUTO_SOURCE, CHARGE_NOW_SOURCE])
                mins = r.choice([60, 120, 180, 360])
                a.plan_auto_plug(mins, cap_min=360, source=src)
                self.log.append(f"charge {src} {mins}")
            elif kind == "cancel":
                src = r.choice([AUTO_SOURCE, CHARGE_NOW_SOURCE])
                a.cancel_charge(src)
                self.log.append(f"cancel {src}")
            elif kind == "move_yours":
                ys = self.yours()
                plugs = [e for e in ys if e["action"] == "plug"]
                if not plugs:
                    return
                p = r.choice(plugs)
                d = r.choice([-60, -30, 30, 60])
                ents = copy.deepcopy(a.entries)
                # its unplug: the next weekly unplug of yours after it
                def mins(t): h, m = map(int, t.split(":")); return h * 60 + m
                us = [e for e in ys if e["action"] == "unplug"]
                us = [e for e in us if (mins(e["time"]) - mins(p["time"])) % 1440]
                u = min(us, key=lambda e: (mins(e["time"]) - mins(p["time"])) % 1440) if us else None
                if u and (mins(u["time"]) - mins(p["time"])) % 1440 <= abs(d) and d < 0:
                    pass
                if any(not 0 <= mins(x["time"]) + d < 1440 for x in (p, u) if x):
                    return  # (across midnight the page moves its days too: not done here)
                for e in ents:
                    if e["id"] in (p["id"], u and u["id"]):
                        t = (mins(e["time"]) + d) % 1440
                        e["time"] = f"{t // 60:02d}:{t % 60:02d}"
                a.set_schedule(entries=ents, daily_cap_min=360, allow_over=True)
                self.over = True
                self.log.append(f"move yours {p['time']} by {d}")
            elif kind == "edit_block":
                # (as the page allows: a block's start earlier, or its end later; never to nothing)
                blocks = [e for e in a.entries if e.get("source") == AUTO_SCHED_SOURCE and e.get("date")]
                if not blocks:
                    return
                b = r.choice(blocks)
                d = r.choice([-60, -30]) if b["action"] == "plug" else r.choice([30, 60])
                ents = copy.deepcopy(a.entries)
                for e in ents:
                    if e["id"] == b["id"]:
                        t = datetime.datetime.fromisoformat(e["date"] + "T" + e["time"]) + datetime.timedelta(minutes=d)
                        e["time"], e["date"] = hm(t), t.date().isoformat()
                a.set_schedule(entries=ents, daily_cap_min=360, allow_over=True)
                self.over = True
                self.log.append(f"edit block {b['action']} {b['date']} {b['time']} by {d}")
            elif kind == "delete_oneoff":
                ones = [e for e in a.entries if e.get("date") and e["action"] == "plug"]
                if not ones:
                    return
                p = r.choice(ones)
                skipped = {(k["entry_id"], k["date"]) for k in a.skips}
                ends = sorted((e for e in a.entries if e["action"] == "unplug" and e.get("date")
                               and (e["date"], e["time"]) > (p["date"], p["time"]) and (e["id"], e["date"]) not in skipped),
                              key=lambda e: (e["date"], e["time"]))
                gone = {p["id"]} | ({ends[0]["id"]} if ends else set())
                a.set_schedule(entries=[e for e in a.entries if e["id"] not in gone], daily_cap_min=360, allow_over=True)
                self.log.append(f"delete one-off {p.get('source')} {p['date']} {p['time']}")
            elif kind == "skip":
                ys = [e for e in self.yours() if e["action"] == "plug"]
                if not ys:
                    return
                p = r.choice(ys)
                day = self.now().date() + datetime.timedelta(days=r.choice([0, 1]))
                if day.weekday() in p["days"]:
                    # (as the page's Skip this one: the plug-in and the unplug that ends its run)
                    t0 = next(a._occurrences(p, day, 1))
                    if t0 not in {x for x, _ in a.windows(None, self.now())} or \
                            {"entry_id": p["id"], "date": day.isoformat()} in a.skips:
                        return  # (the page offers Skip on a slot that runs)
                    # (the page pairs a weekly plug-in with the nearest weekly unplug after it, skipped or not)
                    end = next(((occ, e) for occ, e, sk in a.timeline(day, 3, [x for x in a.entries if not x.get("date")], [])
                                if occ > t0 and e["action"] == "unplug"), None)
                    ks = [{"entry_id": p["id"], "date": day.isoformat()}]
                    if end:
                        ks.append({"entry_id": end[1]["id"], "date": end[1].get("date") or end[0].date().isoformat()})
                    a.set_skips(ks, True, allow_over=True)
                    self.user_skips += ks
                    self.log.append(f"skip {p['time']} {day}")
            elif kind == "advance":
                step = datetime.timedelta(minutes=r.choice([10, 20, 30, 45, 60, 90, 120]))
                if self.now() + step < at(20, 0, 1):
                    self.clock.dt = self.now() + step
                    self.log.append(f"time {self.now().strftime('%a %H:%M')}")
            elif kind == "enable":
                on = not a.schedule_enabled
                a.set_schedule(enabled=on, daily_cap_min=360, allow_over=True)
                self.log.append(f"schedule {'on' if on else 'off'}")
        except ValueError as err:
            self.log.append(f"{kind} refused: {err}")

    def check(self, tag):
        a, now = self.a, self.now()
        probs = []
        for x, y in a.windows(None, now):
            if y > now and (y - x) > datetime.timedelta(hours=14):
                probs.append(f"plugged in {x:%a %H:%M}–{y:%a %H:%M} ({(y - x).total_seconds() / 3600:.1f} h)")
        # orphan one-off unplugs the add-on made (not ending anything, still to come)
        ends = {b for _, b in a.windows(None, now)}
        for e in a.entries:
            if e["action"] == "unplug" and e.get("date") and e.get("source") and not e.get("only_off"):
                t = next(a._occurrences(e, _parse_date(e["date"]), 1), None)
                skipped = {"entry_id": e["id"], "date": e["date"]} in a.skips
                if t and t > now and not skipped and t not in ends:
                    probs.append(f"lone unplug {e['source']} {e['date']} {e['time']}")
        return probs

    def all_off_check(self):
        """Everything off: only your slots, none of the add-on's (still to come), no skips but yours."""
        a = self.a
        self.auto_on = self.save_on = False
        a.cancel_charge(AUTO_SOURCE); a.cancel_charge(CHARGE_NOW_SOURCE)
        self.tick()
        now = self.now()
        probs = []
        for e in a.entries:
            if e.get("source") and e.get("date"):
                t = next(a._occurrences(e, _parse_date(e["date"]), 1), None)
                if t and t > now:
                    probs.append(f"left behind: {e['source']} {e['action']} {e['date']} {e['time']}")
        for k in a.skips:
            if k not in self.user_skips:
                e = next((x for x in a.entries if x["id"] == k["entry_id"]), None)
                t = e and next(a._occurrences(e, _parse_date(k["date"]), 1), None)
                if e and t and t > now and not e.get("source"):
                    probs.append(f"skip left behind: {e['action']} {e['time']} {k['date']}")
        return probs


def state(s):
    a = s.a
    name = lambda i: next((f"{x.get('date') or 'wk'} {x['time']} {x['action']} {x.get('source') or 'yours'}" for x in a.entries if x['id'] == i), "gone:" + i)
    out = [f"-- after: {s.log[-1] if s.log else '-'} (now {s.now():%a %H:%M}, schedule {'on' if a.schedule_enabled else 'off'})"]
    for e in sorted(a.entries, key=lambda e: (e.get('date') or '', e['time'])):
        tr = e.get('trims')
        out.append(f"   {e.get('date') or 'wk'+str(e.get('days'))} {e['time']} {e['action']} {e.get('source') or 'yours'}{' only_off' if e.get('only_off') else ''}"
                   + (f"  trims skips={[(name(k['entry_id']), k['date']) for k in tr.get('skips') or []]} added={[name(i) for i in tr.get('added') or []]}" if tr else ""))
    out.append(f"   skips {[(name(k['entry_id']), k['date']) for k in a.skips]}")
    return "\n".join(out)


def run(seed, steps=40, trace=False):
    s = Sim(seed)
    found = []
    for i in range(steps):
        s.op()
        try:
            s.tick()
        except Exception as err:
            found.append(("crash", f"{type(err).__name__}: {err}", list(s.log), traceback.format_exc()))
            return found
        if trace:
            print(state(s))
        for p in s.check(i):
            found.append(("invariant", p, list(s.log), None))
            return found
    try:
        for p in s.all_off_check():
            found.append(("all off", p, list(s.log), None))
    except Exception as err:
        found.append(("crash", f"{type(err).__name__}: {err}", list(s.log), traceback.format_exc()))
    return found


if __name__ == "__main__":
    n0, n1 = int(sys.argv[1]), int(sys.argv[2])
    kinds = {}
    for seed in range(n0, n1):
        for kind, p, log, tb in run(seed):
            key = (kind, p.split(" ")[0] + " " + p.split(" ")[1] if " " in p else p)
            kinds.setdefault((kind, p[:40]), []).append((seed, log, tb))
    for (kind, p), cases in sorted(kinds.items(), key=lambda x: (not x[0][1].startswith("plugged"), -len(x[1]))):
        seed, log, tb = min(cases, key=lambda c: len(c[1]))
        print(f"[{kind}] x{len(cases)} {p}  (shortest: seed {seed}, {len(log)} steps)")
