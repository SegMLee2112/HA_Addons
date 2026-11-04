# Home Assistant Add-on: OCPP Charge Proxy

## How to use

This add-on acts as a virtual OCPP 1.6 chargepoint and connects to any
OCPP 1.6J server. When the server sends charge scheduling commands, the add-on
updates its state so Home Assistant automations can respond.

## Prerequisites

- An OCPP 1.6J compatible supplier account (or any CSMS that accepts OCPP 1.6
  chargepoint connections)
- OCPP connection credentials (server hostname, chargepoint ID, password)
  from your supplier

## Configuration

### Required options

| Option | Description |
|--------|-------------|
| `server_hostname` | Your supplier's OCPP server hostname |
| `chargepoint_id` | Your chargepoint ID for authentication |
| `password` | Your OCPP password |

### Optional options

| Option | Default | Description |
|--------|---------|-------------|
| `charger_model` | `PLP2-0-2-2` | Charger model reported in BootNotification |
| `charger_vendor` | `Wall Box Chargers` | Charger vendor reported in BootNotification |
| `charger_serial` | Auto-generated | Charger serial number (generated on first boot if empty) |
| `firmware_version` | `6.11.16` | Firmware version reported in BootNotification |
| `initial_energy_wh` | `0` | Seed the energy register (Wh). Set to your old charger's meter reading when migrating. Only applied if higher than the stored register (the meter never goes backwards); cleared after first boot. |

The max current and **Continue session after a restart** are set on the web
page's Settings tab (Controls), and the log level on Diagnostics › Debug.
They used to be add-on options; they're kept across restarts and updates.
OCPP messages are logged at `info`, except Heartbeats and periodic meter
readings (in a session or not), which only show at `debug`. Clock-aligned
readings stay at `info` during a session (outside one, they're `debug` too).

### Controlling your charger

Use the **Status** sensor (`sensor.ocpp_charge_proxy_status`) to switch your
charger or smart plug: turn it on when the status becomes `Charging` and off
when it stops being `Charging` (the README has the automation to paste).
Don't use the Power sensor for this if you've picked your own power sensor
(below): that reads 0 until the charger is switched on, so it would never
switch on.

### Power entity

You can optionally pick a Home Assistant power sensor (W or kW) on the
add-on's **Settings** tab. The proxy will report this real power value to your
supplier instead of simulating power delivery. The value is capped at what the
virtual chargepoint could physically deliver at its current setting.

If not configured, the proxy uses a realistic power simulation.

### SoC sensor for reporting

You can also pick a **SoC sensor** on the Settings tab, for example from
your car's own integration, reporting 0–100%. When set:

- The car's state of charge is sent to your supplier whenever it asks for
  `SoC` in its meter values (Octopus does), while a car is plugged in.
- **Car full:** when the sensor reads 100% during a session, the charger
  reports `SuspendedEV` and stops drawing power, as a real car does when its
  battery is full. The session stays open; if the SoC drops below 100%,
  charging resumes. A charging-profile pause (`SuspendedEVSE`) takes priority.

Leave it unset (or clear it) and no SoC is reported and car-full never
triggers. If the sensor becomes unavailable, SoC reporting pauses until it
comes back.

### Your supplier's daily limit

Some suppliers only schedule so many hours of smart charging a day. The add-on
keeps to your supplier's limit while **Force schedule on supplier** is on
(Automations tab), and the page shows it wherever it matters:

- **Octopus Energy:** 6 hours, counted from 12:00 one day to 12:00 the next.
  So 23:30–05:30 (6 hours) is fine, and so is 06:00–12:00 then 12:00–18:00
  (6 hours each side of 12:00); but 23:30–05:30 plus a 3-hour Charge now at
  18:00 the evening before is 9 hours in one 12:00-to-12:00 day, so the
  add-on asks first, and if you go ahead it starts that night's slot 3 hours
  later (02:30–05:30) to stay within 6.
- **Other suppliers** (EDF Energy, E.ON Next...) and **no supplier:** no limit.
- **Settings › Your supplier** can change it (a number of hours, 0 for none,
  or empty for automatic) and when it resets (a time of day, or rolling: any
  24 hours). If Octopus changes its hours, set them there, or in your own
  `suppliers.json` (see [When your supplier changes
  something](#when-your-supplier-changes-something)), until the add-on is
  updated.

The details:

- Hours are real hours, as suppliers count them: the night the clocks go
  back, 23:30–05:30 is 7 hours (5 the night they go forward).
- A slot or charge that would go over is shown with its extra hours in red
  (click it: **Remove the … over** shortens it to fit; a weekly one offers
  **just this one**, that day only, or **every week**; a slot that's all
  over has **Remove it** instead),
  and the add-on asks before saving it (**Save anyway**, **Charge anyway**...).
- A charge that would go over trims your next slot by whole half hours (or
  skips it, and trims the one after), for that day only.
- With Force schedule on supplier off, or the scheduling guards overridden
  (Diagnostics › Debug), there's no limit.

### Auto plug-in, the car plugged in sensor and Charge now

**Auto plug-in** (Automations tab) switches Plugged In on once when the SoC
drops below the level you set. With **Automatically adjust schedule to fit
auto plug-in** on, it also adds the charge to the schedule as a one-off,
for the hours you choose (**Charge for**, 0.5 to 12). The **Car plugged in**
sensor (Automations tab) can do the same when it plugs in: turn on **Add a
charge to the schedule** and set its own **Charge for**. The switch on its
card turns it off without forgetting the sensor (as the one on Auto
plug-in does): the sensor turning on then does nothing. **Charge now**
works the same way. In each case:

- The slot runs from plugging in to the first ready time your supplier
  accepts at least that long away (rounded up to the half hour; with EDF,
  the next time between 04:00 and 11:00). It unplugs then, after the
  supplier stops the session or the unplug wait (60 s by default).
- **Off-peak times** (below) are left out: it unplugs at the start of one
  and plugs back in at its end, and the charge still ends when it would
  have (the car may well charge in your off-peak hours anyway). If it
  happens during one, it isn't plugged in then, only at its end if the
  charge would still be running. For example, 12 hours from 21:30 with
  23:30–05:30 off peak: 21:30–23:30, then 05:30–09:30.
- **Saving sessions** (below, with No charging during a session on) are
  left out too, but the charge carries on that much later so it keeps its
  hours (ending at a ready time your supplier accepts); one that starts
  during a session plugs in at its end for its full hours. For example,
  3 hours from 17:55 with an 18:00–19:00 session: 17:55–18:00, then
  19:00–22:00.
- With a [daily limit](#your-suppliers-daily-limit) (Octopus: 6 hours) and
  Force schedule on supplier on: a charge longer than the limit is
  scheduled for the limit only, the earliest hours first (e.g. 12 hours from
  16:30 with 23:30–05:30 off peak: 16:30–22:30). If it takes a day over the
  limit, the next scheduled slot starts later, just enough to get back
  within it (for example, 4 hours scheduled 23:30–03:30 and a 3-hour auto
  plug-in at 18:00: that night's slot starts at 00:30). Without a limit,
  the whole charge is scheduled.
- If it meets or overlaps scheduled slots, they become one slot, ending at
  whichever ends last: the charge's end, or the slot's unplug if that's
  later. The slots' own plug-ins and unplugs inside it are skipped that
  once (so they don't set the ready time back, or end the charge early).
- The supplier's ready time is set to the end of the slot (E.ON Next has
  none to set).
- Deleting the charge's slot puts back what it changed: a slot it trimmed
  or joined goes back to how it was.

The entries show on the Automations tab as orange **Auto plug-in**, pink
**Charge now** or brown **Car plugged in** slots, and run even with the
schedule off. A slot that was trimmed keeps its own colour.

### Automatic schedule

**Automations › Automatic schedule** (off by default) plans charging from your
import and export rates. Set how many half hours of charging a day to plan
(Charging half hours a day)
(up to your supplier's daily limit, e.g. 12 for Octopus's 6 hours) and turn it
on. While it's off, its card shows what it would do if it were turned on: the
charging it would add and which of your slots would give way (worked out on
the schedule as it is, without changing it). The plan folds away under a
one-line summary; click it to see it all.

- **The charging:** each day (your supplier's limit day, e.g. 12:00 to
  12:00 for Octopus, else midnight to midnight) whose export rates are known
  gets as many half hours of charging as you asked for, in the free half
  hours still to come where exporting pays least (known prices only; not an
  off-peak time or a saving session). The card's Notes say when fewer could
  go in (no more free half hours with known rates). A day that can't say
  where exporting pays least isn't used, and is planned when its real rates
  come: with Predbat's rates, a copy of the day before (its guess at rates
  not published yet; a supplier's own next day rates, e.g. Octopus Energy's,
  are used even when they're the same as today's), or all one price (Octopus
  Energy's integration, before tomorrow's Agile rates are out at about
  16:00, has the fixed export rate for it).
- **Charge** (where exporting pays least, or before the high exports): with
  *Before the high exports*, each run of the high export half hours (below)
  gets a charge straight before it, as long as the run (e.g. high exports
  16:00–19:00: it charges 13:00–16:00), so the battery's full for them. It
  goes back as far as it needs to in free half hours, but not past the run
  before or now; the dearest runs get theirs first. Then, with what's left,
  a charge straight after each run, as long as it again (not past the next
  run). The rest of the day's charging half hours then go where exporting
  pays least (never in a high export half hour), so all of them are used,
  within the daily limit. E.g. an hour of high export (2 half hours) and 12
  of charging: an hour before it, a pause for it, an hour after it, and the
  other 8 half hours at the cheapest. Set High export half hours a day for it to have something to
  charge before; the Notes say when a charge didn't fit.
- *Charge time needed, Discharge time needed and High export half hours a day
  show only with Before the high exports (with Where exporting pays least,
  no high export half hours are marked).*
- **Charge time needed** (hours, in half hours; empty for none): with
  *Before the high exports*, the most each charge before, and after, a high
  export run is. E.g. a 2-hour run with an hour needed: an hour before it and
  an hour after it (the rest of the charging half hours at the cheapest); a
  run shorter than it gets charges as long as the run. Empty: as long as the
  run.
- **Discharge time needed** (hours; empty for none): how long a full
  battery exports for. With *Before the high exports*, a high export run
  longer than it gets a top-up charge inside it. How many half hours: the
  part of the run the battery can't cover, shared between charging and
  exporting as charge time : discharge time, rounded up (e.g. 2 hours each
  way and a 3-hour run: one half hour). Where: every placement is tried,
  following the battery's charge through the run (it can only charge when
  there's room, and only exports while it has some), and the one exporting
  the most is kept, so it's not simply the cheapest half hour; a joined-up
  top-up is preferred when it's as good. The battery starts the run as full
  as its charge before made it. Top-ups count towards the daily limit, the
  dearest runs first, after the charges before the runs and before those
  after them. On the schedule they're charging (no high export mark), and the
  Notes say e.g. "Top-up 17:00–17:30 inside the Tue 16:00–19:00 high export".
  Charge time needed is then how long a full charge takes (empty: the same
  as the discharge time).
- **High export half hours a day** (a number, 0 for none): each limit
  day's highest export rates, as many half hours as you set, marked in orange
  along the bottom of each day on the schedule (and listed in the plan). Only
  marked: the charging isn't kept out of them. The rates it's seen are
  remembered for 2 days, so in the morning the limit day from 12:00
  yesterday is judged on its whole known length; a day whose start it hasn't
  seen (e.g. just after a restart in the morning) isn't marked.
- **Show export rates on the schedule** (its own switch, on or off whether the
  automatic schedule is): each half hour's export rate as a coloured line
  along the top of each day on the schedule, green where it's lowest and red
  where it's highest (across all the rates known), with the range in the
  legend. Hover over one for its price.
- **Tomorrow, before its rates are out:** until the export rates for
  tomorrow are published (Octopus: about 16:00), tomorrow is planned on an
  estimate, the day before's rates, exactly as if known (your slots there give
  way too), so the limit day from 12:00 today can use tomorrow morning. It
  plans again on the real rates when they come. The Notes say when part of
  the plan is an estimate, and on the schedule the estimated export rates and
  high export marks are faded.
- **Your slots give way** where the rates are known or estimated (skipped,
  or shortened), and stay where they aren't (they count towards the daily
  limit). A slot joined to Charge
  now, auto plug-in or the car plugged in sensor stays as it is. Turning it
  off puts them back; the card's Your slots row says what changed. A slot it
  skipped that you un-skip is yours: it runs, and the plan works around it
  rather than skipping it again.
- **Across the limit's reset:** with Force schedule on supplier, a block of
  its (on its own, or joined to one of your slots) that's longer than the
  daily limit across its reset time (Octopus: 12:00) is split there, as your
  slots are: it unplugs and plugs straight back in, so the supplier sees a
  charge either side, each with its own ready time. One within the limit
  (e.g. 10:30–14:00) stays one charge.
- **What comes first:** auto plug-in, the car plugged in sensor, Charge now
  and saving sessions (the slots they move and the charge before them): it
  plans around them, and they take from its half hours only where the day
  would otherwise go over your supplier's daily limit. Its own charging
  earlier in the day does count towards them. When one of those charges is
  planned, its charging still to come is taken out and planned again around
  it (after a block that's going ends); what it's already done stays. Each time it's planned again,
  the saving sessions still to come go in again after your slots give way,
  so a session only moves what of yours is left (and adds its own charge
  before it if nothing is).
- **The daily limit always applies** to it, by your supplier's limit day
  (e.g. 12:00 to 12:00 for Octopus), whether or not Force schedule on supplier
  is on: no limit day gets more than its hours. With a rolling limit (any 24
  or 48 hours: EDF Energy, E.ON Next), a half hour of charging only goes
  where no 24 (or 48) hours it's in would go over.
- **Off-peak times from your import rates:** the cheapest import half hours,
  an hour or more together (e.g. 23:30–05:30 on Intelligent Octopus Go, not
  half hours made cheap by a smart charging slot), are off-peak times: hatched
  in the schedule, and nothing automatic (or Charge now) plugs in then.
- **In the schedule** its charging is one-offs (cyan), joined to a slot they
  meet.
- **Planned again** when the rates (e.g. tomorrow's, out at about 16:00), the
  number of half hours, the daily limit or the rest of the schedule change,
  but not while one of its blocks is charging (straight after it ends). What
  it's already done stays as it was and counts in the day. Turning it off
  takes its charging out and puts back what it changed.
- **Rates:** found automatically from Octopus Energy's or EDF Energy's
  integration (the `event.…_current_day_rates` and `…_next_day_rates`
  entities, import and `…_export_…`), else Predbat's `predbat.rates` and
  `predbat.rates_export` (e.g. for E.ON Next, which has no rates in Home
  Assistant). Or pick any entity with a list of rates (`rates`:
  `{start, end, value_inc_vat / value / price}`, Predbat's `results`, or Nord
  Pool's `raw_today` / `raw_tomorrow`). They're read every 2 minutes.

### Off-peak times

**Off-peak times** are times nothing is scheduled in, on the days you
pick, e.g. your off-peak hours (23:30–05:30) when the car may well charge
anyway. They're part of the schedule on the Automations tab: add one as you
would a slot (click or drag across a day, or **+ Add a slot**) and choose
**Off-peak time**; click one to change its times or days, or delete it. Slots can't be added in them (and one
can't be added over existing slots), and auto plug-in, the car plugged in
sensor and Charge now are planned around them (above). At the start of
each, it unplugs if the car is plugged in (by hand or from Home Assistant,
say), as nothing should charge then. They're shown hatched in blue-grey.

### Saving sessions

The **Saving sessions** card on the Automations tab (off by default) keeps
the car's charging out of your supplier's saving sessions, read from a Home
Assistant calendar: every upcoming event in it is a session. The calendar
is read every 5 minutes, up to 8 days ahead.

**Axle Energy** events are read from its sensors instead,
`sensor.axle_energy_event_start`, `sensor.axle_energy_event_end` and
`sensor.axle_energy_event_type`, found automatically (the card lists them).
Its **export** events are saving sessions; its **import** events (when it
wants you to use more) aren't. With a calendar too, sessions from either
count (overlapping ones are one session). Without a calendar, Axle's
sensors are enough. While the sensors are unavailable (e.g. as Home
Assistant starts) the sessions already found are kept, not cancelled.

- **Calendar:** found automatically: Octopus Energy's
  `calendar.octopus_energy_<account>_octoplus_power_down` (or the older
  `..._octoplus_saving_sessions`), from the Octopus Energy integration; or
  another calendar whose ID or name mentions saving or saver sessions, power
  down or demand flexibility (E.ON Next, EDF). Octopus's Power Up and free
  electricity calendars are left out. Pick yours if it isn't found.
- **No charging during a session** (its own switch, on by default): each
  session is an off-peak time for that day only. Auto plug-in, the car
  plugged in sensor and Charge now unplug for it and plug back in after, as
  for a weekly off-peak time. A slot in the schedule that runs into it
  keeps its end time (its ready time) and its hours, so it starts earlier by
  the time the session takes out of it. For example, a 15:30–21:30 slot with
  an 18:00–19:00 session becomes 14:30–18:00 and 19:00–21:30; a 17:30–18:30
  slot with a 17:00–19:00 session becomes 16:00–17:00. If that meets an
  earlier slot they join. If starting earlier would be in the past (e.g.
  the session is found at 14:40), the slot starts at the next half hour
  instead (if that's before its own start) and ends later by what's left:
  15:00–18:00 and 19:00–22:00 (found at 15:10, it keeps its 15:30 start:
  15:30–18:00 and 19:00–22:30), not on past the start of an off-peak time (the car can charge then anyway: what's left is dropped, not carried on to the next morning),
  unplugging at a ready time your supplier accepts (with Force schedule on
  supplier), and joining the next slot if it meets it. A slot already
  running when the session is found unplugs for it (straight away, if it's
  begun) and carries on after it the same way. The weekly entries are
  skipped that day and the new times are added as one-offs. Sessions are
  shown hatched on the schedule.
- **Plug in before a session** (off by default), with **Charge for** hours:
  as soon as a session is known, a charge for that long before it is added
  to the schedule as one-offs (purple), ending as the session starts. Only
  the time the schedule doesn't already have the car plugged in is added:
  nothing if a slot already covers it (e.g. a slot moved to end as the
  session starts), and a slot it meets is joined. Not in an off-peak time
  or the past (a session found late gets what's left, not under 15
  minutes). With Force schedule on supplier, if that takes a day over the
  daily limit:
  - a limit that resets at a time (Octopus: 12:00): it comes off a slot in
    that same limit day, as a slot in the next one wouldn't help: first the
    end of the one before the session (if that's still to come: e.g. for a
    06:00 session, the evening before ends earlier), then the start of the
    one after it, if that's still in the day. If there's nothing left in
    that day to take it off, the charge before it isn't added, and the
    session says why (in orange, Automations › Saving sessions)
  - over any 24 hours: the next slot after the session starts that much
    later, as for a car plugged in

  Turning it off, or changing the hours, takes it out or redoes it.
- A session taken out of the calendar before it starts puts back the slots
  it moved. Turning **No charging during a session** off puts them all back,
  and turning the card off forgets the sessions too.

### Schedule, auto re-plug and start-up

- **Schedule** (**Automations** tab): today and the next 6 days are
  shown as slots, each running from a plug-in to its unplug, coloured by
  type: weekly (blue), one-off (teal), auto plug-in (orange), Charge now
  (pink), the car plugged in sensor (brown), the charge before a saving
  session (striped purple), the automatic schedule (cyan), and the time a
  slot was moved to for a saving session (purple). The automatic schedule's
  charging and the charge before a saving session keep their colour where they
  overlap or join a slot of yours (drawn on top of it). With the automatic
  schedule on, your slots still to come up to the end of the last limit day it
  has planned (e.g. 12:00 tomorrow) are cyan too, as what's left of them is
  part of its plan (where it joins its charging on to one of your slots,
  it's one cyan block, and opening the slot says so; past that day, the
  part it added is a lighter cyan); Charge now,
  auto plug-in, the car plugged in sensor and
  saving sessions keep their own colours. **+ Add a
  slot** starts a new one at the next free 2 hours. A
  green line shows when it has charged today (hover for the session's
  energy, length and what ended it; click to open it on the Sessions tab),
  and your supplier's slots are outlined. Earlier days are on the Sessions
  tab. Under each date is the charging scheduled in your supplier's two limit
  days around it: the one ending at that day's reset / the one starting then
  (e.g. **5½h / 6h** under Tuesday for Octopus: Monday 12:00 to Tuesday
  12:00, then Tuesday 12:00 to Wednesday 12:00; without a reset time, just
  the day's), each in red when it's over the daily limit; hover for the
  times.
  Click or drag across a day to add a slot; drag either end of a slot
  to change when it plugs in or unplugs (in half hours; saved straight away,
  as from the editor: a weekly slot changes every week, and if the time can't
  be used, e.g. over the daily limit, the editor opens with why). Drag a
  slot by its middle to move it, to another time or day (in half hours, not
  into the past): a one-off moves; a weekly slot asks **Every week** (its
  times change, and moved to another day, that weekday's slot moves with it)
  or **Just this one** (skipped that day, with a one-off at the new time).
  The automatic schedule's own blocks can't be dragged. On a phone the week
  scrolls sideways (it opens at now; the days stay on the left): swipe to
  scroll, tap to open or add, and press and hold a slot, then drag, to move
  it. Slots that
  meet (one charge, see Back-to-back slots) are drawn joined, with a small
  link where they meet (hover: the ready time for the whole charge); at your
  supplier's daily limit reset, where it unplugs and plugs back in, a gap.
  Click a slot to change its times, the days it repeats on
  (**Every week**) or its date (**Once**), turn it off (**Active**), delete it
  (a weekly slot asks: every week, or just this one), or skip it once (its
  plug-in and unplug both don't run; it shows hatched, and **Undo skip** puts
  it back). Slots can't overlap: the car would unplug at the first one's
  end, ending both, so saving one that would is refused. A weekly slot that
  only overlaps on some days (e.g. a one-off already there today) offers
  **Save, skipping it today** (or on those days): it's saved for the rest,
  skipped where it overlaps. The same goes for the daily limit: a weekly slot
  that would take some days over it offers **Save, skipping it today** (or
  on those days) as well as **Save anyway**. A slot that's going now has **Stop for today**: it unplugs now
  and runs again next time. **No unplug** leaves it plugged in until you
  unplug it or another unplug time comes. Changes are saved straight away. An unplug
  time with no plug-in before it (e.g. "unplug at 07:00 every day", to
  unplug anything left plugged in) shows as a small tick, and can be
  changed or deleted the same way. Times are in your Home Assistant time
  zone. A plug-in time missed while the add-on was stopped isn't run later
  (if it starts inside a plugged-in stretch it plugs in, see below). An
  unplug time is never left behind: one missed while the add-on was stopped
  or restarting (within a day, and if nothing in the schedule has come since)
  unplugs as soon as it's back, also with **Continue session after a
  restart** on, and an unplug that was waiting for the supplier to stop the
  session carries on after a restart. Unplugging during a session ends the
  session.
- **One-off slots:** a **Once** slot runs on its date only. Once it has
  run it's kept for 14 days: sessions on the **Sessions** tab that ran in
  a one-off slot are tagged **One-off** (or **Auto plug-in** / **Charge
  now**, or **Saving session adjusted** for your slot moved for a saving
  session). A skipped unplug isn't used for the ready time.
- **Random start delay** (**Automations** tab, under the schedule, off by
  default): each of your slots plugs in a random time after it starts, up
  to the minutes set (1 to 60, 10 by default) and at most half the slot, so
  it isn't the same time every day, e.g. a 23:30 slot plugs in at 23:34 one
  night and 23:38 the next. Only your own slots: auto plug-in, the car
  plugged in sensor, Charge now and the charge before a saving session plug
  in straight away, as does a slot that starts where another ends or while
  it's already plugged in. The Overview and the Automations banner show
  "Plug in at … (random delay)" meanwhile. If the slot ends, is skipped or
  the schedule's turned off before then, it doesn't plug in; turning the
  delay off plugs in straight away. Plugged in by hand meanwhile, it's left
  as it is. The ready time (Force schedule on supplier) is set when it plugs
  in (or is plugged in). The minutes can be set with the delay off.
- **Restarts:** a plug-in waiting out its random start delay still waits
  after a restart. Restarted while plugged in for a slot, it stays plugged in
  and sets the ready time again (in case it failed before the restart, or
  it was restarted where two slots meet, e.g. stopped from 11:59 to 12:02
  with 06:00–12:00 and 12:00–18:00). A charge from auto plug-in, the car
  plugged in sensor or Charge now that started while the add-on was stopped
  is plugged in at start-up too, with the schedule off as well.
- **Clocks going forward:** a time that doesn't exist that night (01:00–01:59
  in the UK) runs at 02:00, and a slot that's all in that hour doesn't run.
- **Starting inside a scheduled stretch:** if the add-on starts (or
  restarts) while the schedule has the car plugged in, e.g. at 02:00 with
  plug in 23:30 and unplug 07:00, and Plugged In is off, it plugs in
  straight away (and with Force schedule on supplier, sets the ready time
  once Home Assistant is connected). It never unplugs at start-up.
- **Force schedule on supplier** (**Automations** tab, off by default):
  when the schedule plugs in, the add-on sets your supplier's smart
  charging ready-by time to the schedule's next unplug, for example plug in
  02:00 and unplug 04:00 sets 04:00; plug in 06:00 and unplug 08:00 sets
  08:00. At scheduled plug-ins, and again whenever that unplug moves while
  it's plugged in for it (you change or drag the slot, a saving session
  moves it, the hours over are removed...), after a restart, and (if your
  supplier fails to take it) a few times more. Ready times are in 30-minute steps:
  Octopus Energy accepts any time of day, EDF Energy 04:00 to 11:00. While
  it's on, unplug times in the schedule are limited to those (the slot
  editor only offers those). E.ON Next's integration
  has no ready time setting. While it's on, the Automations tab outlines
  the schedule's slots in grey, and the schedule keeps to your supplier's
  [daily limit](#your-suppliers-daily-limit) (Octopus: 6 hours from 12:00 to
  12:00; none for the others): slots that go over are marked in red, and a
  change that goes over is only saved if you say so (the same for putting
  a skipped slot back). A charge earlier in the day counts too; if that's
  already over (e.g. after **Charge anyway**), slots can still be changed
  as long as it doesn't get worse. If Force schedule on
  supplier can't be turned on because of this (or an unplug time your
  supplier doesn't accept), the reason stays in red under the switch, with
  the slots to change marked in red, until it's fixed. With Force schedule
  on supplier off (or **Scheduling guards** overridden, Diagnostics ›
  Debug), there's no limit.
  At a scheduled unplug during a charging session, it waits for your
  supplier to stop the session itself (so it ends cleanly), then unplugs;
  if the supplier hasn't stopped it in time, it unplugs anyway. The wait
  is set under the switch (**Wait for the supplier at unplug**, 60 s by
  default, 0 to unplug straight away). The Overview shows "Unplugging by …"
  meanwhile.
  **Supplier's plan**: while the schedule has the car plugged in,
  the add-on checks your supplier's planned slots up to the ready time it
  set against the schedule. With plug-ins 16:00–18:00 and 20:00–22:00, the
  first stretch is checked from 16:00 (slots up to 18:00) and the second
  from 20:00 (slots up to 22:00). It's flagged if a slot runs past the
  unplug, or if the supplier's ready time was changed (e.g. in its app)
  from the one the schedule set. The check starts 5 minutes after the
  plug-in, to give the supplier time to plan, and follows the plan as it
  changes. The result shows on the Overview's Smart charging card and the
  Automations tab; a mismatch also as a red chip on the Overview, and in the
  log. The supplier's planned slots are outlined in its colour on the
  Automations tab (whether or not this is on).
- **Back-to-back slots** (one ends as the next starts, e.g. 14:00–15:30 and
  15:30–18:00, or Charge now running into a slot): it carries on plugged
  in where they meet, as one charge, with the ready time set to the last
  one's end (18:00) when it plugs in. They're still shown (and can be
  changed) separately. The exception is where your supplier's daily limit
  resets (Octopus: 12:00): slots meeting there (06:00–12:00 and
  12:00–18:00) unplug straight away (without waiting for your supplier) and
  plug back in after the re-plug **Unplugged for** time, so each is its own
  charge with its own ready time (12:00, then 18:00): 6 hours each side.
  With Force schedule on supplier on and a reset time, a slot longer than
  the daily limit across the reset time (e.g. 08:00–18:00) is split there
  when it's saved, into two slots that work this way; so is a charge from
  auto plug-in, the car plugged in sensor or Charge now (including one
  joined with a scheduled slot).
- **Auto re-plug** (**Automations** tab): Octopus sometimes doesn't start a session after you plug
  in. When Plugged In has been on for the set minutes (default 10) with no
  session and the add-on is connected, it unplugs, waits the set
  **Unplugged for** seconds (default 30) and
  plugs back in, up to the set number of tries (default 3). It then gives up
  until the car is next unplugged or a session starts, and the wait starts
  again after each re-plug. If your supplier's smart charging plan is found
  (see the Overview tab), a planned slot counts too: it only re-plugs when
  nothing has been scheduled after the set minutes, not while it waits for
  a slot later on. A slot that's running now with no session is different:
  the status shows **Waiting for supplier** (amber) instead of Scheduled,
  and since Octopus may only start a session at the next half hour (e.g.
  after the add-on restarted mid-slot), the wait counts from the next :00
  or :30: with no session the set minutes after that, it re-plugs.
  It doesn't re-plug when the schedule unplugs before the car would be
  plugged back in (the **Unplugged for** time plus a minute), and it doesn't
  plug back in if a scheduled unplug came while it was unplugged.
- **Simulated car** (**Settings** tab): after StartTransaction (or resuming
  after a charging-profile pause) the simulated car pauses (**Pause before
  drawing power**, default 3 s) before drawing current, then ramps linearly
  to full power over the **Ramp-up** (default 5 s). 0 and 0 = full power
  straight away. Not used when power comes from your power sensor (the card
  is greyed out then). Not to be confused with the schedule's **Random start
  delay**, which is in minutes and delays the plug-in itself.

The schedule and auto re-plug are saved in `/data/automation.json` (and
when the schedule was last checked in `/data/automation_runtime.json`), auto
plug-in and your sensors in `/data/sensors.json`.

### Read only

**Settings › Read only** (off by default): nothing plugs in or unplugs by
itself, and your supplier's ready time isn't changed. The schedule, the
automatic schedule, saving sessions, auto plug-in and the rest are still
planned and shown, so you can see what it would do; each plug-in or unplug it
would have done is logged instead ("Read only: would plug in now (schedule),
but doesn't"). What you do by hand still works: Plug in / Unplug on the web
page, the Plugged In helper in Home Assistant, and Charge now. A **Read only**
chip shows at the top while it's on.

### Notifications

**Settings › Notifications** (off by default): a notification through a
Home Assistant notify service (your phone's is `notify.mobile_app_…`, from
the Home Assistant app) whenever the times the schedule plugs in change,
whatever changed them: you (on the page or in Home Assistant), a saving
session, Charge now, auto plug-in, the car plugged in sensor or the daily
limit. Each day that changed is a line, before and after, with what changed
it where the schedule says, e.g.

> Today: 15:30–21:30 → 14:00–18:00, 20:00–22:00 (saving session 18:00–20:00)

It's sent once the schedule has settled for a minute, so a few changes
together (a slot moved and a charge added before a saving session) are one
notification. **Send a test** checks it reaches you.

**When your supplier sends something new** (off by default) sends one too,
the first time each new thing shows up in Diagnostics › Health › Supplier ›
**New from your supplier** (below).

### Predbat

The add-on always publishes the schedule's next three slots as sensors, so
[Predbat](https://springfall2008.github.io/batpred/) can plan around their
cheap rate before your supplier has planned anything (it only does once the
add-on plugs in). It works the same with any supplier, or none. They're
listed with the add-on's other sensors in **Diagnostics › Home Assistant
entities**.

- Each slot is three sensors: `sensor.ocpp_charge_proxy_next_slot_date`
  (YYYY-MM-DD), `..._start_time` and `..._end_time` (HH:MM:SS), then
  `sensor.ocpp_charge_proxy_next_slot_2_...` and `..._next_slot_3_...`. With
  no slot they read `1999-01-01` and `00:00:00`, which Predbat ignores.
- A slot drops off, and the next moves up, 5 minutes before it starts (so
  Predbat can replan in case it doesn't go ahead; **Settings › Next slot
  sensors › Move on early** changes it, 0: when it starts), or when the
  add-on plugs in for it (e.g. plugged in by hand just before). From then on Predbat
  takes the rate from your supplier's own slots, as it does now, so a slot
  your supplier doesn't give isn't trusted once it's begun.
- They're the slots the schedule will run: skipped ones are left out,
  one-off and Charge now slots are in, and slots that meet are one (but not
  where the daily limit resets: those are two charges). An overnight slot
  ends the next day (e.g. 22:00:00 to 02:00:00), as Predbat reads it. One
  over a day long is split at midnight.
- While the add-on is stopped they read as no slot.

Add this to Predbat's `apps.yaml`, alongside any overrides you already have, with your off-peak rate
(p/kWh) for `rate`:

```yaml
rates_import_override:
  - date: sensor.ocpp_charge_proxy_next_slot_date
    start: sensor.ocpp_charge_proxy_next_slot_start_time
    end: sensor.ocpp_charge_proxy_next_slot_end_time
    rate: 7  # your off-peak rate, p/kWh
  - date: sensor.ocpp_charge_proxy_next_slot_2_date
    start: sensor.ocpp_charge_proxy_next_slot_2_start_time
    end: sensor.ocpp_charge_proxy_next_slot_2_end_time
    rate: 7  # your off-peak rate, p/kWh
  - date: sensor.ocpp_charge_proxy_next_slot_3_date
    start: sensor.ocpp_charge_proxy_next_slot_3_start_time
    end: sensor.ocpp_charge_proxy_next_slot_3_end_time
    rate: 7  # your off-peak rate, p/kWh
```

Predbat takes a slot as certain until it starts. If your supplier then
doesn't give one (e.g. it's over its daily limit), Predbat may already have
planned around it, for example by charging the house battery less overnight.

## Moving to another install

To move the add-on to another install of it without entering anything again,
e.g. from the `ocpp_charge_proxy` repository's add-on to the `HA_Addons` one
(or to a new Home Assistant):

1. On the old add-on (2.54.0 or later: update it first), **Diagnostics ›
   Move to another install › Export**, when no charging session is going
   (Export and Import wait for it to finish, or unplug first, so the
   session's meter readings stay in one place). It downloads one file with:
   - the Configuration tab (server, chargepoint ID, the password unless you
     untick **With the password**, and the charger's details);
   - the schedule and everything else on the Automations tab, your sensors
     and supplier settings, the page's settings, the max current and the
     simulated car;
   - the charger's serial number and its lifetime energy register (your
     supplier knows the charger by these);
   - your supplier changes (`suppliers.json`), if you have any;
   - with **With the history** (on by default), the session history and the
     OCPP message log.

   The file has your password in it unless you leave it out: keep it
   somewhere private.
2. **Stop the old add-on.** Both would connect to your supplier as the same
   chargepoint, and make the same Home Assistant entities.
3. Install and start the new one, open its page, and on **Diagnostics › Move
   to another install › Import** choose the file. It shows what's in it (and
   warns if the old add-on is still running) before anything changes.
4. **Import and restart.** Its Configuration tab is set from the file, the
   rest is put in place as it starts again, and the page reconnects.
5. Uninstall the old add-on.

The Overview's charts and the energy dashboard are Home Assistant's own
history of the add-on's sensors: with the same entity IDs they carry on by
themselves. Not moved: what's only for the moment (a session in progress,
messages held while offline, Plugged In). The energy register never goes
backwards: if the new install's is already higher, it's kept.

## When your supplier changes something

Everything the add-on knows about a supplier is in its **supplier defaults**,
not its code: the daily limit and when it resets, the ready times its app
accepts, the names of its smart charging sensor, the attributes and fields
its planned slots are in, its ready time entity, the words that pick out its
saving sessions calendar, its colour on the page, and its Home Assistant
integration. So when a supplier (or its integration) changes something, it
can usually be followed by changing a setting, without a new version of the
add-on.

**Noticing.** The add-on checks the supplier integration's data every 10
minutes, and the Overview's Smart charging card and **Diagnostics › Health**
say when something looks wrong (an amber **Check**). The log says it too.
For example:

- its smart charging sensor is there, but without the attribute the slots
  are in (`planned_dispatches` for Octopus Energy and EDF Energy);
- the slots are there, but not with the fields expected (`start`, `end`);
- its integration is in Home Assistant, but no smart charging sensor was
  found (fine if you're not on its smart charging tariff);
- its ready time entity wasn't found next to the sensor;
- the sensor is unavailable.

Health also shows each supplier integration's version, and for a week after
one is updated, from which version to which: the first thing to look at if
smart charging stops being found.

**Fixing it yourself.** The add-on reads `suppliers.json` from its own
folder in Home Assistant (`/addon_configs/<this add-on>/`, which you can
reach with the Samba share or the File editor add-on). It's read again
within a minute or so of changing, and Diagnostics › Health says whether
your changes are being used (or why not, if the file has a mistake: it's
left out then, and the add-on's own defaults are used). It only needs
what's different; `suppliers.example.json` in the same folder has the
add-on's own defaults, with what each field means, to copy from. For
example, if the Octopus Energy integration renamed `planned_dispatches` to
`planned_slots` and Octopus allowed 7 hours a day:

```json
{"suppliers": [
  {"name": "Octopus Energy", "planned_attribute": "planned_slots", "daily_limit_h": 7}
]}
```

A supplier with the same name has just those fields changed; a new name
adds a supplier. The fields:

| Field | What it is |
| --- | --- |
| `type` | How its integration shows the plan: `kraken` (a dispatching binary sensor and a ready time entity: Octopus Energy, EDF Energy) or `schedule_sensor` (a sensor with a schedule attribute: E.ON Next) |
| `integration` | Its Home Assistant integration's domain (e.g. `octopus_energy`), to notice when it's updated |
| `colour` | Its colour on the page, `#rrggbb` |
| `dispatch_prefix`, `dispatch_suffix` | `kraken`: the start and end of its dispatching sensor's entity ID |
| `planned_attribute`, `completed_attribute` | `kraken`: the sensor's attributes listing the planned and completed slots |
| `ready_entity_suffix` | `kraken`: what replaces `dispatch_suffix` for its ready time entity (`time.` or `select.`) |
| `schedule_sensor_suffix`, `schedule_attribute` | `schedule_sensor`: part of its sensor's entity ID, and the attribute with the slots |
| `slot_fields` | The names of each slot's `start`, `end`, `kwh` and `source` |
| `calendar_words` | Words in its saving sessions calendar's ID or name |
| `ready_times` | The ready times its app accepts, `{"from": "04:00", "to": "11:00"}`; `null`: none |
| `daily_limit_h`, `limit_reset` | Its daily limit in hours (`null`: none) and when it resets (`"12:00"`, or `"rolling"`) |

And `saving_sessions` (`words`, `not_words`, `prefer`) for finding the
saving sessions calendar, and `sensors`: saving sessions read from sensors
rather than a calendar (Axle Energy), each with a `name`, the end of its
`start`, `end` and `type` sensors' entity IDs, and `types`, the event types
that are saving sessions (a `sensors` list in your file replaces the whole
list). Anything set on Settings › Your supplier still
wins over all of these.

If a supplier's integration changes in a way these fields can't follow (a
new way of showing its plan), it needs a change to the add-on: each kind is
one file in `src/supplier_types/`, and a new kind is a new file there.

## How it works

1. The add-on connects to your supplier's OCPP server via WebSocket
2. It registers as a chargepoint (BootNotification)
3. It sends periodic heartbeats and meter values: whichever your supplier
   asks for of energy, power, the current the car draws (`Current.Import`),
   the supply voltage (`Voltage`, from Settings › Voltage), the current and
   power offered, frequency and SoC
4. When you "plug in" (the Plugged In helper or the web page), it reports
   `Preparing`
5. Your supplier creates a charge schedule and sends `RemoteStartTransaction`
   (with the ID tag the session is for: Octopus sends the same fixed tag to
   everyone, and numbers every session 1). With `AuthorizeRemoteTxRequests`
   on (the default) the add-on first checks the tag with your supplier
   (`Authorize`) and only starts if it's accepted, as a charger does:
   - each answer is remembered (the authorisation cache,
     `AuthorizationCacheEnabled`; `ClearCache` empties it), and with the
     local list (`SendLocalList`) is kept across restarts. Diagnostics ›
     Health › Supplier shows both
   - with `LocalPreAuthorize` on, a tag already accepted in the local list
     or cache starts without waiting for `Authorize`
   - with Wallbox's `AuthEnabled` off, it starts without checking at all
   - offline (or if the connection drops while checking), it starts only if
     `LocalAuthorizeOffline` is on, Wallbox's `AuthEnabledOffline` isn't off,
     and the tag is accepted in the local list or cache (or isn't known there
     and `AllowOfflineTxForUnknownId` is on). Octopus sets `AuthEnabledOffline`
     to False after each connect, so with Octopus an offline start doesn't
     happen (shown as a red chip on the Overview, and in the log)

   If the answer to `StartTransaction` says the tag isn't valid, then with
   `StopTransactionOnInvalidId` off (the default) the session stays open but
   no energy is delivered (`SuspendedEVSE`) until your supplier stops it; on,
   it's stopped (reason `DeAuthorized`). Either shows as a red chip on the
   Overview, and in the log. The other settings a charger reports are there
   too, at the values a wallbox typically has (`LocalAuthorizeOffline`
   true, `LocalPreAuthorize` false, `ConnectionTimeOut` 60, `ResetRetries` 1,
   `TransactionMessageAttempts` 3, `TransactionMessageRetryInterval` 60,
   `MinimumStatusDuration` 0, and the `…MaxLength` limits of 20). Values are
   checked as a charger does (true/false, whole numbers, measurand lists no
   longer than their limit; read-only keys can't be changed), and
   `SendLocalList` follows the rules (a differential update must be a newer
   version, an entry without `idTagInfo` removes that tag, at most 20 tags).
   A start, stop or reading your supplier fails to process is sent again, up to
   3 tries, waiting 60 s, then 120 s (nothing after it goes first)
6. The proxy sends `StartTransaction`, reports `Charging`, and sends meter
   values while the simulated car ramps up to full power
7. When the supplier ends the session, it sends `RemoteStopTransaction`
8. The proxy reports `Finishing`, sends `StopTransaction`, then returns to
   `Preparing`

If the connection drops, the add-on reconnects automatically, retrying after 5, 10
and 30 seconds, then every minute.

### Ready for changes on your supplier's side

- **New from your supplier** (Diagnostics › Health › Supplier): anything your
  supplier sends that the add-on doesn't know (a new setting, a message it
  has no answer for, a setting a Wallbox doesn't have), anything it had to
  refuse and why, an error your supplier sends back, and a new connection
  password. Each shows once with when it was first and last seen (kept across
  restarts); **Mark as seen** and **Clear**; and a notification if you turn
  it on (Settings › Notifications)
- **Connection password**: your supplier can set a new one
  (`AuthorizationKey`, OCPP 1.6's security extension). It's kept in the
  add-on's data, never logged or shown (hidden in the log and message log
  too), and used from the next connection. If your supplier then refuses it,
  the password in the add-on's options is tried again, and if that works the
  new one is dropped. If you change the password in the options, yours is
  used. `SecurityProfile` is 2 (TLS with a password); a change to another
  level is refused
- **Charging profiles**, as a charger works them out:
  - a session's own (`TxProfile`) overrides the default for every session
    (`TxDefaultProfile`); the overall cap (`ChargePointMaxProfile`) always
    applies; the lowest is the limit, and caps the current (a limit of 0
    pauses: `SuspendedEVSE`)
  - of each, the highest stack level with a limit at the time applies
  - Absolute, Relative (from the session's start) and Recurring (daily or
    weekly) schedules; `validFrom` / `validTo`; a schedule's duration; amps
    or watts
  - the session's own ends with it; the default and the cap stay, across
    restarts too
  - `SetChargingProfile` is checked as a charger does (a `TxProfile` only
    during a session, the cap only on connector 0, within the stack levels,
    periods and number of profiles reported); `ClearChargingProfile` answers
    `Unknown` when nothing matched
  - `GetCompositeSchedule` answers what the charger will allow from now, all
    profiles combined and never over what it can do (amps or watts)

### While offline

Like a real charger, a dropped connection doesn't stop the session. Charging,
the energy register and any charging profile carry on, and the transaction
stays open. StartTransaction, StopTransaction and the session's MeterValues are
held in order and sent once the server accepts the BootNotification on
reconnect. Status updates and heartbeats aren't held; the current status is
sent after reconnecting instead. Held messages are saved to
`/data/offline_queue.json`, so they also survive an add-on restart. Up to
1,000 messages are held, and the oldest meter readings are dropped first.

If the add-on is stopped without warning mid-charge (power cut, crash), the
open transaction is closed on the next start. A StopTransaction with reason
`PowerLoss` is sent, using the last meter reading saved before the
interruption.

When the add-on is restarted mid-charge (an update, a restart from Home
Assistant), it leaves the session open by default
(**Continue session after a restart**, Settings › Controls): no StopTransaction or Unavailable, just
the connection closing, as when a charger loses its connection. When it's
back (within 10 minutes, with Plugged In still on) it reports Charging and
carries on with the same transaction, energy and charging profile.
Otherwise, or with the option off, the session is stopped with reason
`Reboot`, and your supplier starts a new one (Octopus at the next hour or
half hour). If the supplier sends a new RemoteStart instead of carrying on,
the old session is stopped (`Reboot`) and the new one started straight
away.

### StopTransaction readings

StopTransaction can include `transactionData` with the session's readings.
Two configuration keys choose what's included. Both are empty by default, as
on a Wallbox Pulsar Plus, so nothing is added unless your supplier sets them
with ChangeConfiguration:

| Key | Default | Readings |
|-----|---------|----------|
| `StopTxnSampledData` | *(empty)* | At the start, every `MeterValueSampleInterval`, and at the stop |
| `StopTxnAlignedData` | *(empty)* | On each `ClockAlignedDataInterval` boundary |

Long sessions are thinned to at most 100 readings, and the first and last are
always kept.

## Home Assistant entities

The add-on creates its own entities (no integration needed):

| Entity | Type | Description |
|--------|------|-------------|
| `input_boolean.ocpp_charge_proxy_plugged_in` | Helper (toggle) | Plugged In: turn on/off to plug in or unplug. Kept in step with the add-on's own Plugged In |
| `input_button.ocpp_charge_proxy_charge_now_1h` … `_12h` | Helpers (buttons) | Charge now for 1, 2, 3, 4, 6, 8 or 12 hours, as the Automations tab's Charge now buttons |
| `sensor.ocpp_charge_proxy_power` | Sensor | Current power draw (kW) |
| `sensor.ocpp_charge_proxy_energy` | Sensor | Cumulative energy (kWh, works with the Energy dashboard) |
| `sensor.ocpp_charge_proxy_current` | Sensor | Current draw (A) |
| `sensor.ocpp_charge_proxy_status` | Sensor | OCPP state (Available, Preparing, Charging...), or Scheduled / Waiting for supplier while plugged in with a supplier slot planned / running |
| `sensor.ocpp_charge_proxy_current_limit` | Sensor | Current the charger uses (A), with your max (`max_amps`) and the supplier's limit (`provider_limit_amps`) as attributes |
| `sensor.ocpp_charge_proxy_next_slot_date`, `..._start_time`, `..._end_time` (and `_next_slot_2_...`, `_next_slot_3_...`) | Sensors | The schedule's next three slots, for Predbat (see [Predbat](#predbat)) |

- The sensors are posted by the add-on, so they can't be renamed in the UI and
  aren't grouped under a device. They show as unavailable while the add-on is
  stopped, and come back when it (or Home Assistant) restarts.
- The Plugged In helper is a normal helper. While the add-on is stopped,
  toggling it does nothing, and it's set back to the add-on's value when the
  add-on starts.
- The Charge now buttons are normal helpers too: press one, or call
  `input_button.press` on it from an automation. It's added to the schedule
  as a one-off, as the web page's button does. An automation can't answer
  the page's "over the daily limit?" question, so a press always goes ahead
  (as auto plug-in does); the log says what was planned. Presses while the
  add-on is stopped are ignored. A deleted button comes back when the
  add-on reconnects to Home Assistant.
- If the old OCPP Charge Proxy integration (1.x) is still installed, the
  add-on leaves the sensors alone until you remove it (Settings > Devices &
  services). `sensor.ocpp_charge_proxy_energy` keeps its entity ID, so its
  Energy dashboard history carries on.
- Your power and SoC sensors are set up on the web page's **Settings**
  tab, and auto plug-in and the car plugged in sensor on the **Automations** tab; the add-on
  reads them through Home Assistant's API.

## Web page

Open the add-on from the sidebar (or **Open Web UI**). It updates live and
follows your Home Assistant theme. Settings save as soon as you change them.

- **Header:** a health dot left of the title (green / amber / red) that
  opens Diagnostics › Health.
- **Header:** after the add-on is updated, an open page says so with a
  **Reload** button (the page itself is the old version until reloaded).
- **Overview:** state, power, current, SoC and energy (with a **Check**
  chip when something's wrong with your supplier's integration or the
  supplier defaults: hover for what, click for Diagnostics › Health);
  **What happens next**: now, then each thing coming in the next 16 hours
  (the schedule plugging in, with the ready time, and unplugging; your
  supplier's slots; saving sessions; your off-peak times) at its time on a
  strip of those hours (plugged in by the schedule, your supplier's slots,
  saving sessions, off-peak times hatched grey);
  things go above the strip, or below it where they'd overlap the one
  before, and on a phone it scrolls sideways;
  the current session;
  **smart charging** (the charge slots your supplier plans, found
  automatically from the Octopus Energy, EDF Energy or E.ON Next integration, also
  shown along the top of the chart);
  and a chart of power, current (with your max and the supplier limit) or
  SoC over the last 30 minutes to 24 hours, or 14 days. Drag across the
  chart or scroll over it to zoom (double-click or **Reset zoom** to go
  back). On a phone: tap for the reading, move a finger across it to go
  back or forward in time, pinch to zoom, double tap to go back (up and
  down still scrolls the page). The charts and the
  energy for the last 7 days are read from Home Assistant's history of the add-on's
  sensors (keep them recorded): 10-second detail for 24 hours, then
  5-minute points while HA keeps them (10 days by default, `purge_keep_days`)
  and hourly beyond that.
- **Sessions:** a timeline of the last 7 days (when the car was plugged in,
  coloured by what plugged it in, when it charged, the saving sessions
  (hatched), today's plan (your supplier's planned slots, dashed in its
  colour with their kWh, and when the schedule plugs in, a dashed band),
  and each day's energy and
  time charging; click a day to show only its sessions, or a charge to open
  it), and the charging sessions of those 7 days (energy, duration, peak power, what ended them; transaction ID
  and ID tag in the details; tagged with what started it: **Schedule**,
  **Charge now**, **Auto plug-in**, **Car plugged in**, **Before a saving
  session**, **Saving session adjusted**, **One-off**, **By hand** or
  **Home Assistant**; and with your supplier's name, e.g.
  **Octopus Energy slot**, when the whole session was in its dispatch
  slots, or **Partly Octopus Energy slot** when only some was), kept across restarts,
  and the plug-ins of those 7 days that never got a session: when, for how long, who
  plugged in, and whether auto re-plug gave up on it or it was unplugged
  first.
- **Automations:**
  - The **Schedule** card's banner: what the schedule does next (and how
    long until, and what it did last), its On switch, and **Charge now**.
  - **Charge now** (in the banner): click how long to plug in for (1, 2, 3, 4, 6, 8 or 12
    hours). It plugs in straight away and adjusts the schedule the same way
    auto plug-in does (see [Auto plug-in](#auto-plug-in-the-car-plugged-in-sensor-and-charge-now)): a one-off slot
    in the schedule below, the ready time set to its end, and it unplugs
    then. **Cancel this charge** (after it's started) takes it out of the
    schedule again, puts back anything it changed, and unplugs unless a
    slot has the car plugged in. With your supplier's [daily
    limit](#your-suppliers-daily-limit) (Octopus: 6 hours) and Force
    schedule on supplier on, the banner says what it is, the lengths over
    it are marked, and a charge that would go over asks first, saying what
    would change; **Charge anyway** then goes ahead.
  - **Over the daily limit** (with Force schedule on supplier on): saving a slot, putting back a skipped one, turning on
    Force schedule on supplier or turning the scheduling guards back on
    with the schedule over the limit says so, with a button to go ahead
    anyway (**Save anyway**, **Put it back anyway**, **Turn on anyway**,
    **Turn back on anyway**). The hours past the limit then show as a red
    block of their own on the slot, e.g. 4 hours last night and a 3-hour
    slot this morning (the same day from 12:00): its last hour is red. Your
    supplier may not charge in them.
  - The plug-in schedule (see below): today and the next 6 days of when it
    has the car plugged in, and under it the **Random start delay**.
    While a plug-in waits out the delay, the banner and the Overview say
    when it'll plug in, and the slot is hatched until then.
  - **Your supplier:** Force schedule on supplier, the unplug wait and the
    supplier's plan check.
  - **Auto plug-in**, **Car plugged in** (the sensor for your car's real
    cable), **Saving sessions** and **Auto re-plug**: one card each.
- **Settings:** **Appearance**: **Colourful** (a coloured banner at the top
  of each tab, from blue to your supplier's colour, the default) or
  **Basic** (plain cards), saved in each browser; controls for **Plugged In
  (virtual)** (what your supplier sees: the add-on's charger cable, not your
  car's real one; also a **Plug in** / **Unplug** button on the Overview),
  max current and **Continue session after a restart**; the simulated car's
  pause and ramp-up; your
  power and SoC sensors, with their live values; the **Voltage** card (a
  voltage sensor, or a set supply voltage; 230 V if neither) used to turn
  amps into kW for the simulated power and the ~kW figures; and **Your
  supplier**, for when what's found automatically isn't right, or your
  supplier's rules change:
  - **Smart charging sensor:** the dispatching sensor with its planned
    slots (Octopus Energy / EDF Energy), or E.ON Next's schedule sensor.
  - **Ready time:** the target time entity Force schedule on supplier sets,
    and the **Ready times** it accepts (automatic: what the entity offers,
    any half hour for Octopus, 04:00–11:00 for the others).
  - **Daily limit:** the most hours of smart charging your supplier
    schedules in a day (automatic: 6 for Octopus Energy, none for the
    others; 0: no limit). Used for the schedule, auto plug-in and Charge now
    while Force schedule on supplier is on. **Resets** says when it starts
    again: every day at a set time (automatic for Octopus Energy: 12:00, so
    6 hours before midday and another 6 from midday are fine), or rolling
    (automatic for the others): no more than the limit in any period of the
    **Reset frequency** (24 hours unless changed, 1–168 hours). If a change
    means the schedule no longer fits, the card offers to clear the
    schedule's charging slots (off-peak times stay), put the setting
    back, or keep both and shorten the slots yourself. With a reset time, "any 24 hours" below means each day from
    that time, and a charge across it can be longer: 08:00 for 12 hours is
    08:00–18:00 (4 hours before 12:00, then 6 after).
  Each shows what's in use, and whether it was found or picked.
  The automatic values come from the supplier defaults (`src/suppliers.json`,
  with your own changes on top: see [When your supplier changes
  something](#when-your-supplier-changes-something)); anything set on this
  tab still wins.
- **Diagnostics**, in two parts:
  - **Health:** version, uptime, reconnects and the last drop's reason,
    heartbeat and clock offset, the Home Assistant link and the add-on's
    Home Assistant entities, and any held messages. At the bottom,
    **Supplier**: what your supplier has set: charging limits, charging
    profiles drawn as a timeline, the local authorisation list and every
    configuration key.
  - **Debug:** tools for testing and bug reports:
    - Test values: a power override (reported instead of the simulation)
      and a test SoC.
    - **Send a message** now as the charger: a StatusNotification (any
      status; it doesn't change the charger's own state), MeterValues, a
      Heartbeat or a BootNotification, with the supplier's reply.
    - **Drop the connection** for a number of seconds (charging carries on
      and transaction messages are held, as on a real dropout), or
      **restart the add-on** (through the Supervisor; a session carries on
      after it with **Continue session after a restart** on).
    - **Scheduling guards** (off by default): override them to let the
      schedule, auto plug-in and Charge now plug in for more than Octopus's
      6 hours a day while Force schedule on supplier is on.
    - **Logs:** the log level (Debug, Info, Warning), kept across
      restarts, and the last 300 OCPP messages both ways, kept across
      restarts (with a marker where the add-on restarted), with a filter,
      full JSON on click and a Copy button for sharing.
    - **Diagnostics bundle:** one JSON file with the state, schedule,
      sessions, supplier's plan and settings, health, recent messages, log
      lines and options (password and most of the chargepoint ID hidden).
    - **Clear data:** the session history or the message log.

Times on the page are 24-hour. Longer explanations are behind **More**
links.

## Getting your OCPP credentials

Your supplier will give you three values needed to connect:

- **Server hostname** — the OCPP WebSocket endpoint
- **Chargepoint ID** — your unique chargepoint identifier
- **Password** — authentication password

You may also need to set `charger_model` and `charger_vendor` to match a
charger model your supplier supports. The defaults work for suppliers that
accept Wallbox chargepoints.

Check your supplier's app or documentation for how to obtain these credentials.
