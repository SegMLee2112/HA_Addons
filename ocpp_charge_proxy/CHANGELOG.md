# Changelog

## 2.68.0 — Add a weekly slot where it doesn't overlap

- **Feature:** a new or changed weekly slot that overlaps another slot on
  some days (e.g. a one-off you already have today) can be saved anyway,
  skipped on those days: **Save, skipping it today** (or on the days it
  overlaps). It runs on all its other days, and **Undo skip** puts a day back.
  The skips are saved with the slot, so the daily limit is checked with them.

## 2.67.3 — Next slots: just listed in Diagnostics

- **Change:** Diagnostics › Home Assistant entities lists the next slots
  sensors with the others, without their readings or the Predbat set-up (the
  `apps.yaml` lines are in the documentation).

## 2.67.2 — Predbat: the next slots always on

- **Change:** the next slots sensors are always published, and the Settings
  tab's Predbat card is gone. They're shown in **Diagnostics › Home Assistant
  entities**, with **Set up Predbat** (the `apps.yaml` lines to copy).

## 2.67.1 — Predbat: the rate is set in Predbat

- **Change:** the slot rate setting and `sensor.ocpp_charge_proxy_slot_rate`
  are gone: put your off-peak rate in Predbat's `apps.yaml` instead
  (`rate: 7`, say). The **Set up Predbat** lines show where.

## 2.67.0 — Tell Predbat the next slots

- **Feature:** **Settings › Predbat** can publish the schedule's next three
  slots as sensors (date, start and end each, as Octopus Charge Pack's), plus
  the rate to give them, for Predbat's `rates_import_override`. Predbat can
  then plan around a slot's cheap rate before your supplier has planned it.
  A slot drops off when it starts, or when the add-on plugs in for it, so
  from then on Predbat goes by your supplier's own slots. It doesn't depend
  on any supplier. Off by default.
- **Docs:** the Predbat section, with the `apps.yaml` to add (also on the
  card to copy).

## 2.66.7 — Schedule fixes from testing

- **Fix:** skipping a one-off slot (for example part of a saving session)
  no longer leaves a lone "unplug" marker on top of it, so **Undo skip** can
  be reached again.
- **Fix:** short pieces of a slot (such as 23:30–24:00 before midnight) can
  be tapped to open them; the resize handles used to cover them.
- **Fix:** a slot that overlaps another is refused with an explanation. The
  page used to draw the two as one long slot, but the car unplugs at the
  first slot's end, ending both. Slots that meet end to start are still one
  charge.
- **Fix:** putting a skipped slot back when another day is already over the
  daily limit now offers **Put it back anyway**, instead of a message that
  disappeared.

## 2.66.6 — Deleting a slot from a series

- **Fix:** deleting a slot that's part of a series now asks: **Every
  <weekday>** (just that day of the series), **All its days**, or **Just this
  one**. Before, with that day already skipped (e.g. by a saving session),
  the only choice was "Every week", which deleted the series on every day
- **Fix:** a one-off slot (moved for a saving session, Charge now...) that
  ended with a series' every-day unplug lost its end when the series was
  deleted, a day unticked or its time changed, so it stayed plugged in for
  days (a long "filler" slot, and hours over the daily limit). It now gets
  its own unplug at the same time
- **Fix:** "Just this one" always skips it (it shared a button with Skip this
  one, which puts back one that's already skipped)
- **Improvement:** Today, Tomorrow and Yesterday show their weekday too (the
  slot editor's days are Mo-Su)

## 2.66.5 — Changing a moved slot's end

- **Fix:** a one-off slot that ends with an every-day unplug (a slot moved
  for a saving session, or by Charge now or auto plug-in, e.g. 16:00 to your
  21:30): dragging its end (or changing it in the slot editor) added an
  unplug at the new time but left that day's 21:30, so it still unplugged at
  21:30 and the new time showed as an unplug on its own. That day's 21:30 is
  now skipped (only that day, and not if another slot that day ends with it).
  Deleting such a slot skips it too, so it doesn't leave an unplug behind

## 2.66.4 — Charge before a morning session, within the right day

- **Fix:** with a daily limit that resets at a time (Octopus: 12:00), a
  charge before a saving session that took a day over the limit was taken
  off the next slot after the session, even when that slot was in the next
  limit day (a 06:00 session: the afternoon's slot started later, though the
  charge counted in the day before). Now it comes off a slot in the same
  limit day: first the end of the one before the session (the evening
  before ends earlier, if that's still to come), then the start of the one
  after it, if that's still in the day. If there's nothing left in that day
  to take it off, the charge isn't added, and the session says why (in
  orange, Automations › Saving sessions, and in the log). A limit over any
  24 hours works as before

## 2.66.3 — Every supplier checked

- **Fix:** with E.ON Next (it has no ready time), with no supplier found, or
  when your supplier accepts no ready time before the unplug, setting the
  ready time is no longer tried again 8 times (with a warning each time) at
  every plug-in: that can't help. A failure that may pass (Home Assistant
  busy, an entity not there yet, no supplier looked for yet) is still tried
  again
- **Fix:** the page said the add-on's version from when it was first opened
  (2.66.1's page caching): it follows the version again (the build's
  test_the_page_and_the_api_say_the_version)
- **Improvement:** new tests run the same scenarios with Octopus Energy, EDF
  Energy, E.ON Next, an unlisted supplier and none: how each is found, its
  daily limit and reset (and one set on the Settings tab), the ready times it
  accepts, the schedule's checks, slots that meet, an 8 hour charge and
  setting the ready time

## 2.66.2 — Old upgrade handling removed

- **Change:** removed handling for versions long gone (every install is past
  them, including the ocpp_charge_proxy repository's 2.54.3):
  - saving sessions moved by versions before 2.43.7 are no longer moved again
  - chart files left by versions before 2.3.0 are no longer looked for and
    deleted
  - the old companion integration (0.x / 1.x) is no longer checked for
- Kept: a daily-limit reset saved as "48h" by 2.31.0 still reads as rolling,
  and a car plugged in with no plugged-in record still gets one

## 2.66.1 — Tidy up

- **Improvement:** the code is compiled when the image is built, so the first
  start after an update is quicker
- **Improvement:** the web page is read from disk once (again only if it
  changes) rather than every time it's opened
- **Improvement:** each message from your supplier is checked against the
  ones the add-on answers with a list worked out once, and replies to the
  add-on's own messages aren't read twice
- **Change:** removed page styles nothing used any more, and a leftover
  function; the icon and logo are stored smaller (same pixels)
- **Docs:** versions before 2.40.0 moved to CHANGELOG-older.md, so the
  changelog Home Assistant shows when updating opens quicker
- **Change:** tests and caches are left out of the image build

## 2.66.0 — Ready for changes on your supplier's side

- **Feature:** **New from your supplier** (Diagnostics › Health › Supplier):
  anything your supplier sends that the add-on doesn't know (new settings,
  messages it has no answer for, settings a Wallbox doesn't have), things it
  had to refuse and why, errors your supplier sends back, and a new
  connection password, each shown once with when it was first and last seen,
  kept across restarts. A notification for each, if you turn it on (Settings
  › Notifications › When your supplier sends something new)
- **Feature:** your supplier can change the connection password
  (`AuthorizationKey`): it's kept (never logged or shown: hidden in the log
  and message log too) and used from the next connection; if your supplier
  then refuses it, the add-on's own password is tried again. Reports
  `SecurityProfile` 2 (TLS with a password)
- **Feature:** charging profiles work as on a charger: a session's own
  (`TxProfile`) overrides the default for every session (`TxDefaultProfile`),
  the overall cap (`ChargePointMaxProfile`) always applies, and the lowest
  caps the current; Absolute, Relative and Recurring schedules, `validFrom` /
  `validTo` and durations; the default and the cap stay after a session and
  across restarts (before, every profile was cleared when a session ended,
  and the highest stack level won whatever its purpose)
- **Feature:** `GetCompositeSchedule`: what the charger will allow from now,
  all profiles combined (before, it wasn't answered)
- **Improvement:** `SetChargingProfile` is checked as a charger does (and
  refused with the reason in New from your supplier); `ClearChargingProfile`
  answers `Unknown` when nothing matched; Diagnostics shows what each profile
  is for, whether it applies now, and its validity

## 2.65.1 — Reset zoom over the chart

- **Fix:** **Reset zoom** now sits over the History chart's top right corner
  instead of in its heading, where (on a phone) showing it pushed the chart
  down
- **Improvement:** touch gestures follow the finger even when the chart
  redraws under it

## 2.65.0 — Charts on a phone

- **Feature:** the Overview's history chart (power, current, SoC) works by touch: move a finger across it
  to go back or forward in time, pinch to zoom in and out, double tap to go
  back to the whole period, and tap for the reading at that time. A finger
  moving up or down still scrolls the page

## 2.64.2 — Auto plug-in wording

- **Docs:** Automations › Auto plug-in now reads "Plugs in when the monitored
  device's charge drops below a level" (it needn't be a car)

## 2.64.1 — Looks more like a real charger

- **Change:** the connection to your supplier no longer sends a User-Agent
  header (it was the library's own, "Python/3.x websockets/x"); a charger
  usually sends none
- **Improvement:** simulated power is to the watt (0.001 kW) rather than whole
  tens of watts, as a real meter reads (e.g. 1447 W, not 1450 W)

## 2.64.0 — ID tags and settings, as a real charger

- **Feature:** an authorisation cache, as chargers have
  (`AuthorizationCacheEnabled`, on): your supplier's answer for each ID tag is
  remembered, `ClearCache` now empties it, and it's kept across restarts with
  the local list (both shown in Diagnostics › Health › Supplier)
- **Feature:** `LocalPreAuthorize` now works: on, a tag already accepted in
  the local list or cache starts without waiting for `Authorize`
- **Feature:** Wallbox's `AuthEnabled`: off, a remote start goes ahead without
  checking the tag at all
- **Change:** offline, a start now needs the tag to be accepted in the local
  list or cache (or `AllowOfflineTxForUnknownId` on, new, off by default), as
  well as `LocalAuthorizeOffline` on and `AuthEnabledOffline` not off. With
  Octopus nothing changes: it sets `AuthEnabledOffline` off, so an offline
  start doesn't happen, and online starts work as before
- **Feature:** more of the settings a charger reports: `MinimumStatusDuration`,
  the `…MaxLength` limits for the measurand lists, and `AuthEnabledOffline`
  (true until your supplier sets it)
- **Improvement:** setting values are checked as a charger does (true/false,
  whole numbers, measurand lists within their limit) and refused otherwise
- **Fix:** `SendLocalList` follows the rules: a differential update must be a
  newer version, an entry without `idTagInfo` removes that tag, and a list
  over 20 tags is refused

## 2.63.10 — AuthEnabledOffline honoured

- **Change:** Wallbox's `AuthEnabledOffline` setting now does what it does on
  a real Wallbox: set to False (as Octopus does after every connect), a
  remote start whose ID tag can't be checked because the connection is down
  (or drops while checking) isn't started, and shows as a red chip on the
  Overview. Online starts aren't affected

## 2.63.9 — Off-peak in What happens next

- **Feature:** **What happens next** shows your off-peak times (Automations ›
  Schedule): hatched grey on the strip, a box when one starts (with when it
  ends), and in Now's box while you're in one
- **Fix:** a box's text could wrap a word early (its width was rounded down)

## 2.63.8 — What happens next, earlier further out

- **Fix:** in **What happens next**, when things are close together the
  earlier ones go further out (higher above the strip, lower below it) and
  the later ones nearer it, so a line no longer runs through another box
- **Fix:** a box placed below the strip could stay hidden after the card
  redrew (e.g. 20:00 missing until the page was reloaded)

## 2.63.7 — What happens next, above and below

- **Change:** **What happens next** is back to the clearer boxes (big time,
  what happens, details), each at its time on the strip: above it, or below
  it where it would overlap the one before (then a row further out), so
  lines never run through a box. Replaces the one-line labels from 2.63.5

## 2.63.6 — What happens next, soonest at the top

- **Fix:** in **What happens next**, the soonest label now sits on the top row
  and later ones step down towards the strip, so a later event's line no
  longer runs through an earlier label

## 2.63.5 — What happens next, compact

- **Improvement:** **What happens next** is about half the height: what's
  happening now (and what's next, and how long until) is one line, and each
  thing to come is a one-line label at its time just above the strip (e.g.
  "14:30 Plug in → 19:00", "19:00 Unplug · Saving session → 20:00"; hover for
  more), in its colour, going up a row only where labels would overlap

## 2.63.4 — What happens next, on the timeline

- **Improvement:** **What happens next** puts each thing at its time on the
  strip below it: its line meets the strip at that time (14:30 at 14:30),
  with things close together on another row instead of overlapping. On a
  phone it's at least an hour to 60 px and scrolls sideways, rather than
  the boxes stacking on top of each other

## 2.63.3 — The ready time follows the schedule

- **Fix:** changing the schedule while plugged in for a slot (editing or
  dragging its end, a saving session moving it, removing the hours over...)
  didn't change your supplier's ready time: it stayed at the old unplug until
  the next plug-in. It's now set again as soon as the unplug it's plugged in
  for moves

## 2.63.2 — Ready time after a restart

- **Fix:** restarted while plugged in for a slot (e.g. an update), the ready
  time wasn't set again, so one that had failed before the restart (and its
  retries, which aren't kept) stayed unset. It's now set again at start-up
  whenever it's plugged in for a slot with Force schedule on

## 2.63.1 — Ready time, again

- **Fix:** when setting your supplier's ready time failed at a plug-in (e.g.
  Octopus's integration: "Device status could not be fetched"), it wasn't
  tried again, so the app kept the old time. It's now tried again after 1,
  2, 5, 10... minutes (8 times, about 1½ hours) while it's still plugged in
  for the same unplug; the Automations tab says when

## 2.63.0 — Which saving session

- **Improvement:** saving sessions that overlap (e.g. Octopus's 18:00–19:00
  and Axle Energy's 18:30–20:00) are still one for the schedule, but the
  timelines (Sessions, Automations, What happens next) show each as it runs,
  in two lanes where they overlap, with the one going now stronger ("Now:"
  on hover). The Saving sessions list shows each under the time they cover
  together, with **Now** by the one running

## 2.62.0 — Like a wallbox

- **Feature:** the add-on reports the rest of the settings a charger must
  have (`LocalAuthorizeOffline`, `LocalPreAuthorize`, `ConnectionTimeOut`,
  `ResetRetries`, `TransactionMessageAttempts`,
  `TransactionMessageRetryInterval`), at the values a wallbox typically has.
  Before, your supplier got "unknown key" for them
- **Change:** a start, stop or meter reading your supplier fails to process
  is sent again, as a wallbox does: up to `TransactionMessageAttempts` (3)
  tries, waiting `TransactionMessageRetryInterval` (60 s) times the tries so
  far, in order. Before, it was dropped straight away. With
  `LocalAuthorizeOffline` turned off by your supplier, a start that can't be
  checked (offline) doesn't go ahead

## 2.61.1 — Last week's saving sessions

- **Fix:** the Sessions tab's last 7 days could miss a saving session the
  add-on hadn't kept (e.g. one found before an update, when they were kept
  for a day). The ones that are over are now read from the calendar too, a
  week back, and shown alongside the ones the add-on kept

## 2.61.0 — The ID tag, checked

- **Feature:** with `AuthorizeRemoteTxRequests` on (the default, as the
  add-on has always reported), a start your supplier asks for checks the ID
  tag with it first (`Authorize`), as a real charger does, and only starts if
  it's accepted. Offline, or if the check fails, it starts anyway. Your
  supplier can turn the setting off
- **Feature:** your supplier's answer to `StartTransaction` is followed: if it
  says the ID tag isn't valid (Invalid, Blocked, Expired, ConcurrentTx), with
  `StopTransactionOnInvalidId` off (the default) the session stays open but
  no energy is delivered (SuspendedEVSE) until your supplier stops it; with
  it on, the session is stopped (DeAuthorized). Before, the answer was
  ignored and it carried on charging. A red chip on the Overview says so

## 2.60.0 — Amps and volts

- **Feature:** meter values include the current the car draws
  (`Current.Import`, 0 A when it isn't charging) and the supply voltage
  (`Voltage`: your voltage sensor or set value, as on Settings › Voltage)
  when your supplier asks for them, as a real charger does. Before, they were
  left out

## 2.59.0 — Remove the hours over

- **Feature:** a slot with hours over your supplier's daily limit (in red)
  has a button in its editor that takes them off: **Remove the 30m over**
  makes it end (or start) that much sooner, at a ready time your supplier
  accepts (a weekly slot: every week). A slot that's all over has **Remove
  it** instead, which deletes it (a weekly one asks: every week, or just
  this one)

## 2.58.5 — Not past off-peak

- **Fix:** a slot that ends later for a saving session (it had started, or
  starting earlier would be in the past) carried on around your off-peak
  hours, e.g. 20:00–23:30 and then 05:30–06:00 the next morning. It now stops
  at the start of an off-peak time, since the car can charge then anyway;
  the note says so. Sessions already found aren't moved again: turn **No
  charging during a session** off and on to redo them

## 2.58.4 — Cut short, drawn short

- **Fix:** a slot that was going when a saving session cut it short (e.g.
  15:30–21:30 with an 18:00–20:00 session found at 16:20: 15:30–18:00, then
  20:00–23:30) was drawn on the Automations tab as one slot to 23:30, with
  the 18:00 unplug as a tick. It runs as it should; it's now drawn that way
  too, 15:30–18:00 and 20:00–23:30

## 2.58.3 — Over which days

- **Fix:** on the Automations tab, a slot over your supplier's daily limit
  was labelled only up to where the limit ran out (a 15:30–22:00 slot read
  15:30–21:30, with the last half hour in red), so it looked as if the red
  had nothing to do with the slot. Slots now show their whole time, and the
  warning lists every day that's over, not just the worst
- **Improvement:** the Saving sessions card says when Axle Energy's sensors
  aren't found (and what it looked for), as well as when they are

## 2.58.2 — What saving sessions do

- **Improvement:** the Saving sessions card says what it reads the sessions
  from, that the two switches decide what's done about them, and, under it,
  what it does now: e.g. "1h of charging is added before each session, but
  charging during a session isn't changed (turn on No charging during a
  session to move it out)"

## 2.58.1 — Delete anyway

- **Fix:** deleting a slot that the add-on warned takes a day over your
  supplier's daily limit: **Save anyway** saved the slot again instead of
  deleting it, so nothing happened. The button is now **Delete anyway**, and
  deletes it

## 2.58.0 — A week of sessions

- **Change:** the Sessions tab keeps the last 7 days of sessions and
  plug-ins that never got a session (today and the 6 days before, as its
  timeline shows), rather than the last 20 of each. Older ones are dropped

## 2.57.1 — Together

- **Improvement:** **What happens next** lists things at the same time in
  one box (e.g. an unplug at 18:00 and a saving session from 18:00), and a
  saving session that's going already in Now's

## 2.57.0 — What happens next, and notifications

- **Feature:** **What happens next** on the Overview: now, then the next few
  things in the coming 16 hours (the schedule plugging in and unplugging
  with the ready time, your supplier's planned slots, saving sessions), and
  a strip of those hours
- **Feature:** notifications when the schedule changes (Settings ›
  Notifications): pick a Home Assistant notify service (e.g. your phone's)
  and turn it on. Whatever changes it, each day that changed, before and
  after, and why where the schedule says, once it's settled for a minute.
  **Send a test** to check
- **Feature:** drag either end of a slot on the Automations tab to change
  its times (in half hours), saved straight away
- **Feature:** slots that meet are drawn joined, with a link where they meet
  (hover: the ready time for the whole charge), and a gap where they're split
  at your supplier's daily limit reset
- **Feature:** an open page says when the add-on has been updated, with a
  **Reload** button
- **Improvement:** every session on the Sessions tab says what started it
  (**Schedule**, **Charge now**, **By hand**...), not only one-offs
- **Improvement:** a **Check** chip on the Overview when something's wrong
  with your supplier's integration (hover for what, click for
  Diagnostics › Health)

## 2.56.0 — Slots that meet are one charge

- **Change:** where one slot ends as the next starts (your slots, a slot
  moved for a saving session, Charge now, auto plug-in or the car plugged in
  sensor running into a slot), it now carries on plugged in, as one charge:
  no unplug and plug back in, and the ready time is set to the last one's
  end when it plugs in. They're still shown and changed separately. Where
  your supplier's daily limit resets (Octopus: 12:00) it still unplugs and
  plugs back in, so the supplier sees a charge either side

## 2.55.2 — As soon as it can

- **Change:** when a saving session is found too late for a slot to start
  earlier by the time it takes out (its start would be in the past), the
  slot now starts at the next half hour and ends later only by what's left,
  rather than keeping its start and ending later by the whole session. A
  15:30–21:30 slot with an 18:00–20:00 session found at 13:45: 14:00–18:00
  and 20:00–22:00 (was 15:30–18:00 and 20:00–23:30). Found within half an
  hour of the slot's start, it keeps its start as before. Sessions already
  found aren't moved again

## 2.55.1 — Saving session adjusted

- **Change:** on the Sessions tab, a charge from one of your slots that was
  moved for a saving session is tagged **Saving session adjusted** (was
  **One-off**), for as long as the saving session is kept (a week)

## 2.55.0 — Axle Energy events, and today's plan

- **Feature:** saving sessions from Axle Energy: its export events
  (`sensor.axle_energy_event_start`, `_end` and `_type`, found automatically)
  are saving sessions, as from a calendar, with or without one. Its import
  events (use more) are left out. The sensors are listed on the Saving
  sessions card, and the sources are in `suppliers.json`
  (`saving_sessions.sensors`), so another one like it can be added there
- **Feature:** the Sessions tab's Today row shows today's plan: your
  supplier's planned charge slots (dashed, in its colour, with their kWh)
  and when the schedule plugs in (a dashed band)

## 2.54.3 — The last week, by the clock

- **Fix:** after the clocks went back, the Sessions tab's last 7 days drew the
  days before the change an hour early (a 15:31 charge showed from about
  14:30; its times on hover were right). Each day is now drawn by the clock,
  so 15:30 is under 15:00 on either side of a change. The Automations tab's
  past days, charges and your supplier's planned slots had the same problem
  across a change, and are fixed too
- **Feature:** the Sessions tab's last 7 days show the saving sessions
  (hatched; hover for the time and name). Saving sessions are now kept for
  a week after they end (was a day), so the week has them all

## 2.54.2 — Not during a session

- **Change:** **Move to another install**'s Export and Import wait while a
  charging session is going (the page says so and greys them out), so the
  session's meter readings and the energy register aren't split between the
  two installs. Unplug, or wait for it to finish

## 2.54.1 — The serial number, moved

- **Fix:** moving to another install where the old one had its serial number
  only on the Configuration tab left the new install's own made-up serial in
  its serial file (the Configuration tab's, moved, was still the one used):
  the file now gets the moved serial too

## 2.54.0 — Move to another install

- **Feature:** **Diagnostics › Move to another install**: **Export** downloads
  one file with this install's settings (the Configuration tab, with or
  without the password; the schedule and the Automations tab; your sensors,
  supplier settings and supplier changes; the page's settings, max current
  and simulated car; the charger's serial and lifetime energy register) and,
  if you like, its history (sessions and OCPP messages). **Import** on the
  other install shows what's in the file, warns if the old add-on is still
  running, then sets the Configuration tab, puts the rest in place and
  restarts. For moving from the ocpp_charge_proxy repository's add-on to the
  HA_Addons one without entering anything again. The energy register never
  goes backwards

## 2.53.2 — Slots can be changed again

- **Fix:** changing, turning off or deleting a slot could fail with "At most
  50 schedule entries": the limit counted one-off times that had already run
  (kept for 14 days, for the Sessions tab) and the charges the add-on adds
  (auto plug-in, Charge now, the car plugged in sensor...), so after a
  couple of weeks of auto plug-ins no slot could be changed. Now only your
  own count (weekly times and one-offs still to come: up to 100, i.e. 50
  slots); Cancel this charge was affected too

## 2.53.1 — Tidier code

- **Change:** a tidy of the code, with no change to what the add-on does:
  every JSON file in `/data` is now read and written by one helper
  (`src/jsonfile.py`), so they're all written the safe way (atomically,
  keeping a good `.bak` copy that a broken file never replaces) and fall
  back to the copy the same way. The web API's errors come from one helper,
  the page's times from one formatter, and unused code is gone

## 2.53.0 — Following your supplier's changes

- **Feature:** your own changes to the supplier defaults, in `suppliers.json`
  in the add-on's folder in Home Assistant (`/addon_configs/...`): only
  what's different, read again within a minute or so of changing, so a
  supplier's change can be followed the same day without an update.
  `suppliers.example.json` there has the add-on's own, to copy from
- **Feature:** the add-on notices when your supplier's integration looks
  changed and says so on the Overview's Smart charging card, Diagnostics ›
  Health and in the log: its sensor without the slots, slots in another
  format, its integration there but no smart charging sensor, no ready time
  entity, or the sensor unavailable. Health shows each supplier
  integration's version, and for a week after one's updated, from what to
  what
- **Change:** everything the add-on knows about a supplier is now in the
  supplier defaults, not the code: as well as its limit and ready times, the
  names of its sensor, the attributes and fields of its slots, its ready
  time entity, its saving sessions calendar's words, its colour and its
  integration. Each kind of supplier integration (Kraken: Octopus Energy and
  EDF Energy; a schedule sensor: E.ON Next) is one file in
  `src/supplier_types/`
- **Change:** the add-on now maps its own folder in Home Assistant (for
  `suppliers.json`)
- **Docs:** *When your supplier changes something*: how it's noticed, and
  how to follow it with `suppliers.json`, with every field

## 2.52.0 — Fixes from a review, and clearer pages

- **Fix:** turning the random start delay off while a plug-in was waiting
  out the delay cancelled it: it now plugs in straight away
- **Fix:** with the random start delay on, a slot that started while the car
  was already plugged in didn't set your supplier's ready time; there's no
  delay then now, just the ready time
- **Fix:** the night the clocks go forward, a slot with a time that doesn't
  exist (e.g. 01:30–02:30 in the UK) stayed plugged in for about 24 hours and
  every change that week was refused as over the daily limit. A missing time
  now runs at 02:00, and a slot that's all in the missing hour doesn't run
- **Fix:** restarted where two slots meet (e.g. 06:00–12:00 and 12:00–18:00),
  it unplugged and stayed unplugged until 18:00; it now stays plugged in,
  with the ready time for the new slot. A plug-in waiting out its random
  delay still waits after a restart
- **Fix:** a Charge now, auto plug-in or car plugged in charge that started
  while the add-on was stopped wasn't plugged in at start-up with the
  schedule off
- **Fix:** a scheduled plug-in due while auto re-plug had the car unplugged
  (its Unplugged for time can be up to 5 minutes) could be dropped as late,
  with no ready time set
- **Fix:** the extra unplug and plug-in a saving session adds at the daily
  limit's reset time were recorded against the wrong session, so cancelling
  the right one left them behind
- **Fix:** settings only take real true/false values (the text "false"
  turned the schedule on), bad requests get a clear error instead of a
  server error, one-off slots can be at most 62 days ahead (one years away
  slowed everything down), and a refused change no longer leaves part of it
  applied
- **Feature:** **Stop for today** on a slot that's going now: it unplugs and
  runs again next time. Deleting a weekly slot asks: every week, or just
  this one? The slot editor shows the date, labels its on/off switch
  (**Active**) and warns about **No unplug**
- **Feature:** **Cancel this charge** after Charge now: it comes out of the
  schedule, what it changed is put back, and it unplugs unless a slot has
  the car plugged in
- **Feature:** a **Plug in** / **Unplug** button on the Overview
- **Improvement:** while a plug-in waits out its random start delay, the
  Overview and the Automations banner say when it'll plug in, and the slot
  is hatched until then
- **Improvement:** Charge now says your supplier's daily limit in plain words
  (Octopus: "at most 6 hours of smart charging from 12:00 one day to 12:00
  the next"), from your supplier's settings: none is shown for a supplier
  without one. The lengths over it are marked
- **Improvement:** **+ Add a slot** starts at the next free 2 hours
- **Improvement:** the add-on's Plugged In is called **Plugged In (virtual)**
  on the page, so it isn't confused with your car's real cable; the
  simulated car's start delay is now **Pause before drawing power**, and its
  card is greyed out while your power sensor is used
- **Improvement:** on a phone, the schedule and session timelines show fewer
  hour labels and only the slot and charge labels that fit; the random start
  delay's switch sits with its title
- **Improvement:** older OCPP messages on the Overview show their date;
  sessions outside your supplier's slots say so; Charge now and car plugged
  in colours are easier to see in dark mode
- **Docs:** switch your charger from the Status sensor (the Power sensor
  reads 0 until the charger's on, if you use your own power sensor); a
  section on your supplier's daily limit, with an example; the slot colours,
  the random start delay and the Automations cards brought up to date

## 2.51.1 — Set the start delay before turning it on

- **Improvement:** the random start delay's minutes can be changed with its
  switch off, so it's ready before you turn it on

## 2.51.0 — Random start delay

- **Feature:** a **Random start delay** for your schedule (Automations tab,
  under the schedule; off by default): each of your slots plugs in a random
  time after it starts, up to the minutes you set (1 to 60) and at most half
  the slot, so it isn't the same time every day. Only your own slots: auto
  plug-in, the car plugged in sensor, Charge now and the charge before a
  saving session still plug in straight away, as does a slot that starts
  where another ends. The Overview shows "Plugging in at …" while it waits

## 2.50.5 — Car slots stay separate with the schedule off

- With the schedule off, a car slot (low-SoC auto plug-in, the car plugged in
  sensor, Charge now, or the charge before a saving session) stays separate
  from your slots: it doesn't join them (2.50.4 joined them). It still trims
  or skips your later slots to keep within your supplier's daily limit,
  counted as if the schedule were on and joined, so it all fits when the
  schedule's turned back on; it joins your slots then
- The charge before a saving session now does the same: with the schedule
  off it counts your slots for the daily limit and trims your next one

## 2.50.4 — Car slots with the schedule off

- With the schedule off, the switch only pauses your own slots: they're greyed
  out in the week, while car slots (auto plug-in, Charge now, the car plugged
  in sensor) stay in colour, as they still run. The banner shows the next car
  slot time (and so does the Overview's chip) instead of just "Paused"
- A car slot planned with the schedule off now fits around your slots as it
  would with it on: it joins the ones it meets, and trims or skips later ones
  for your supplier's daily limit. With the schedule off it still ends at its
  own end; turned back on, it's already one with your slots
- The daily limit counts the schedule as it runs with it on, also while it's off

## 2.50.3 — Turning the schedule on checks the daily limit

- Turning the schedule on now checks your supplier's daily limit (Octopus: 6
  hours, with Force schedule on supplier on). A charge planned while it was off
  only counted the other charges, so joined with the schedule's slots a day
  could go over. If it would, it asks first, with **Turn on anyway** (the hours
  over then show in red). Not if the slots alone were already over
- A charge that ends just as a scheduled slot starts (or starts as one ends)
  now joins it too when the schedule's turned on

## 2.50.2 — Turning the schedule on and off

- **Turned on during a slot:** it plugs in until the slot's end (as at
  start-up), with the ready time set to it if Force schedule on supplier is on
- **Turned on during a car slot** (a charge from auto plug-in, the car plugged
  in sensor or Charge now, planned while the schedule was off): the charge
  joins the slots it meets, as if planned with the schedule on, so it's
  plugged in until whichever ends last. Before, the charge's own end cut the
  slot short (or the slot's end cut the charge short)
- **Turned off during a slot:** it unplugs. During a car slot it doesn't: the
  charge separates from the slot it joined and carries on to its own end (the
  ready time set back to it). Not plugged in by the schedule (e.g. by hand,
  outside a slot): left alone

## 2.50.1 — Unplugged after the charge, with the schedule off

- Auto plug-in, Charge now and the car plugged in sensor still unplug at the
  end of their charge after the schedule's turned off. A charge that met a
  scheduled slot became one with it and ended with the slot's unplug, which
  doesn't run with the schedule off, so the car stayed plugged in. It now
  keeps its own unplug as well, used only while the schedule is off (not
  shown in the week, or counted, while it's on)

## 2.50.0 — Over the daily limit, if you choose

- Everywhere the schedule is refused for going over your supplier's daily
  limit (Octopus: 6 hours), you can now go ahead anyway: **Save anyway**
  (saving a slot), **Put it back anyway** (undoing a skip), **Turn on
  anyway** (Force schedule on supplier) and **Turn back on anyway**
  (Diagnostics › Scheduling guards). Charge now already had **Charge anyway**
- The schedule shows just the hours past the limit as a red block of their
  own on the slot (it used to colour the whole slot red). E.g. 4 hours last
  night and a 3-hour slot this morning, the same day from 12:00: its last
  hour is red, as your supplier may not charge then. Also with the guards
  overridden
- The unplug times your supplier accepts are still checked

## 2.49.5 — Clearing the session history clears it all

- Diagnostics › Clear data › Session history now also clears the plugged-in
  times on the Sessions tab's 7-day chart, and the backup copy of the
  history (it used to keep the old one). The energy totals on the Sessions
  tab start again from the clear. The page redraws straight away
- A session in progress, and a plug-in now, carry on. The Overview chart's
  power readings are Home Assistant's own history, so they stay

## 2.49.4 — Time drop-downs stay open

- Schedule: a slot's time drop-down no longer closes by itself while you
  scroll through it. The page's refresh every few seconds redrew the week,
  editor included; it now waits until you've finished with the editor

## 2.49.3 — Re-plug after means what it says

- Auto re-plug: the wait is now the minutes set, from plugging in. It used
  to start at the next hour or half hour while your supplier's slot was
  running, so 5 minutes at 22:14 re-plugged at 22:35
- Your supplier's slots, planned or running now, are left to it: no
  re-plug. With no slots, or no supplier found, it re-plugs after the wait

## 2.49.2 — Each tab from the top

- **Change:** switching tab goes back to the top of the page, instead of
  staying as far down as you'd scrolled on the last one.

## 2.49.1 — Sessions in your supplier's colour

- **Change:** on the Sessions tab, a session charged in your supplier's
  dispatch slots is drawn in its colour (Octopus purple, E.ON red, EDF
  amber), on the 7-day timeline and in the list's energy bar. Others stay
  green; the key says which is which.

## 2.49.0 — The page tidied up

- **Improvement:** the Overview's banner holds the charger's state, the
  connection and transaction, and Power, Current, Car SoC and the energy
  meter as tiles; the last messages received and sent are one **OCPP
  messages** card, with a link to the full log.
- **Improvement:** cards side by side no longer leave gaps under the
  shorter one (the Automations tab's Auto plug-in, Car plugged in, Saving
  sessions and Auto re-plug; the Settings sections; the Debug tools). The
  daily limit is said once, in the Charge now banner; the schedule's key is
  smaller; Max current fits on one line.
- **Improvement:** Settings' **Show all sensors in the lists** is at the
  top of the sensors it affects.
- **Improvement:** Diagnostics › Debug puts the log first and the tools
  beside each other below it; replies sit quietly under their message, and
  Drop, Restart and Clear are in red. On Health, held messages sit with the
  other cards and the configuration keys fold away (**Show the keys**).
- **Fix:** Diagnostics › Health's Charging profiles card was empty (and the
  configuration table not drawn) when a profile was in amps.

## 2.48.0 — Off-peak times

- **Change:** no-charging times are now called **off-peak times**, as
  that's what they're for: your off-peak hours, when the car may well
  charge anyway.
- **Change (undoes part of 2.43.8):** auto plug-in, the car plugged in
  sensor and Charge now only carry on later after a **saving session**.
  Round an off-peak time they unplug at its start and plug back in at its
  end, but still end when they would have (12 hours from 21:30 with
  23:30–05:30 off peak: 21:30–23:30, then 05:30–09:30). One that starts
  during an off-peak time plugs in at its end only if the charge would
  still be running.

## 2.47.0 — Charge now in the schedule, and a colourful theme

- **Improvement:** on the Automations tab, **Charge now** and the
  **Schedule** are one card. Its banner shows what the schedule does next
  (e.g. **Unplug at 21:30**, with how long until and what it did last; grey
  and **Paused** when the schedule is off), the schedule's On switch, and
  the Charge now buttons.
- **New:** a coloured banner at the top of each tab, from blue to your
  supplier's colour (Octopus purple, E.ON red, EDF amber): the charger state
  on Overview, the totals on Sessions, and on Diagnostics the overall health
  (green when all is well, orange or red when something needs attention).
- **New:** Settings › **Appearance**: choose **Colourful** (the banners) or
  **Basic** (plain cards). Saved in each browser.

## 2.46.0 — Recent sessions with a 7-day timeline

- **Improvement:** the Sessions tab's **Recent sessions** card opens with a
  timeline of the last 7 days: when the car was plugged in (coloured by what
  plugged it in: the schedule, auto plug-in, Charge now, ...), when it
  actually charged (with the kWh), and each day's energy and time charging.
  Click a day to show only its sessions in the list below (again to show
  all), or click a charge to open that session's details.
- **Change:** the **Per day** chart is gone (the timeline replaces it).

## 2.45.1 — The schedule without the past days

- **Change:** the Automations tab's schedule shows today and the next 6
  days only. The last 3 days (when the car was actually plugged in, which
  could look out of line with the schedule, e.g. an unplug that waited for
  your supplier) are gone from it; past sessions are on the Sessions tab.

## 2.45.0 — The schedule redesigned

- **Improvement:** the Automations tab's schedule is easier to read (the
  way it works is unchanged): "As it happened" and "Coming up" sections,
  each day with its name (Yesterday, Today, Tomorrow...) and date, taller
  rows with gridlines and times every 3 hours, slots as rounded blocks
  labelled with their times, what was charged as a green line under them,
  your supplier's planned slots outlined, the past of today dimmed and a
  marker for now. The next action is shown as a pill above, the slot editor
  opens as a card under its day, and the legend is clearer.

## 2.44.0 — Recent sessions redesigned

- **Improvement:** the Sessions tab's Recent sessions are now a row each
  instead of a table: the day and times, how it started (Charge now, Auto
  plug-in, your supplier's slot...), the energy with a bar against the
  biggest session shown, its length and peak, and how it ended as a
  coloured label (charging now, stopped by your supplier, unplugged, an
  add-on restart...). Plug-ins that never got a session are greyed. Click a
  row for its details and power curve, as before.

## 2.43.8 — Charges keep their hours around no-charging times

- **Change:** auto plug-in, the car plugged in sensor and Charge now carry
  on after a no-charging time or saving session for the time it took out,
  instead of ending when they would have (3 hours from 17:55 with an
  18:00–19:00 session: 17:55–18:00, then 19:00–22:00, not 21:00). One that
  starts during one plugs in at its end for its full hours. The end is a
  ready time your supplier accepts, and the daily limit still applies.

## 2.43.7 — Unplug at the start of a no-charging time

- **Feature:** at the start of each no-charging time it unplugs if the car
  is plugged in, e.g. by hand or from Home Assistant, as nothing should
  charge then.
- **Change:** saving sessions no longer add an unplug at their start
  (2.43.6); that was meant for no-charging times. One already added is
  taken out.

## 2.43.6 — Saving sessions: unplug at the start

- **Feature:** with No charging during a session on, each session gets an
  unplug at its start (a one-off on the schedule), so the car stops even if
  it was plugged in by hand or from Home Assistant. Not added if the
  schedule already unplugs then (e.g. a charge before the session ends
  there); taken out with the rest if the session is cancelled or the switch
  is turned off.

## 2.43.5 — Schedule view: slots whose unplug is skipped

- **Fix:** the schedule view drew a slot to its own unplug even when that
  was skipped, so a charge before a saving session that joined it looked
  like nothing was added (16:30–17:30 plus a stray unplug at 18:00 instead
  of 16:30–18:00), or like it stopped at the joined slot's end (16:00–17:30
  instead of 16:00–18:00). A slot now runs to the next unplug that does
  run, and that unplug isn't shown on its own. The schedule itself was
  right.

## 2.43.4 — Saving sessions on the schedule

- **Fix:** the schedule view showed a slot moved for a saving session
  under the weekly slot it replaces that day, as one slot (and paired the
  moved part with the weekly unplug it skips). The weekly slot isn't drawn
  that day now, and the moved times show in purple as "Moved for a saving
  session".
- **Change:** Plug in before a session adds its charge to the schedule as
  soon as the session is known (shown in purple), not only when its time
  comes, for the time the schedule doesn't already cover; over the daily
  limit, the next slot after the session starts later. Turning it off or
  changing the hours takes it out or redoes it.

## 2.43.3 — Saving sessions: no start in the past

- **Fix:** if starting a slot earlier for a saving session would put its
  start in the past, it keeps its start and ends later instead (15:30–21:30
  with an 18:00–19:00 session found at 14:40: 15:30–18:00 and 19:00–22:30),
  not in a no-charging time and joining the next slot if it meets it. A
  slot already running carries on after the session the same way, instead
  of being cut short.

## 2.43.2 — Saving sessions: charge before only when needed

- **Fix:** Plug in before a session adds nothing if the schedule already
  has the car plugged in for those hours (e.g. a slot moved to end as the
  session starts), and only the missing time if it covers part of them. As
  before, the next slot starts later if it'd take a day over the daily limit.

## 2.43.1 — Saving sessions: slots start earlier

- **Fix:** a slot that runs into a saving session now keeps its end time
  and its hours and starts earlier by the time the session takes out of it
  (15:30–21:30 with an 18:00–19:00 session: 14:30–18:00 and 19:00–21:30).
  It was carried on later instead (19:00–22:30), past its ready time.
  Slots moved by 2.43.0 are moved again.
- **Improvement:** a slot moved earlier that meets the slot before joins it.

## 2.43.0 — Saving sessions

- **Feature:** a Saving sessions card on the Automations tab (off by
  default). It reads your saving sessions from a Home Assistant calendar,
  found automatically for Octopus Energy (Power Down / Saving Sessions) and
  by its name for E.ON Next and EDF, or picked.
- **Feature:** No charging during a session (its own switch): each session
  is a no-charging time, and a scheduled slot that runs into it carries on
  the session's length later. Put back if the session is cancelled.
- **Feature:** Plug in before a session (its own switch): a charge of the
  chosen hours, ending as the session starts, added as Charge now adds one,
  within the daily limit.
- **Improvement:** sessions are shown hatched on the schedule, and the
  card lists the ones coming up with what was changed for each.

## 2.42.0 — Charge now buttons in Home Assistant

- **Feature:** the Charge now buttons are Home Assistant entities:
  `input_button.ocpp_charge_proxy_charge_now_1h`, `_2h`, `_3h`, `_4h`,
  `_6h`, `_8h` and `_12h`, created by the add-on. Press one, or call
  `input_button.press` from an automation, to charge now for that long,
  as the web page's button does. From Home Assistant it goes ahead even
  over the supplier's daily limit, as auto plug-in does.
- **Improvement:** Diagnostics › Health lists the Charge now buttons with the
  add-on's other entities.

## 2.41.2 — Unused code removed

- **Change:** removed code nothing used any more: the old idle and
  charging meter-value builders (meter values come from one builder), an
  unused ID-masking helper, the charging profiles' text summary, and two
  one-line automation helpers only the tests called. No change to how the
  add-on behaves.

## 2.41.1 — A how-to README

- **Docs:** a new README (the add-on's Info page in Home Assistant): what
  it does and how it works, what you need, installing, the add-on options,
  setting it up tab by tab, an automation to switch your charger, its
  entities and troubleshooting.

## 2.41.0 — A switch for Car plugged in

- **Feature:** the Car plugged in card (Automations tab) has an on/off
  switch, as Auto plug-in has: off, the sensor stays set but its turning
  on doesn't plug in or add a charge. It's on unless you switch it off, so
  nothing changes until you do.

## 2.40.1 — A charge that runs past a scheduled slot keeps its end

- **Fix:** an auto plug-in (car plugged in, low SoC) or Charge now that
  overlapped a scheduled slot but went on past it was cut short at the
  slot's unplug. E.g. plugged in at 18:30 for a charge to 23:00, with a
  19:00–21:00 slot: it became one slot to 21:00, and the ready time was set
  for 21:00, not 23:00. Now the joined slot runs to whichever ends last
  (here 23:00), and the scheduled slot's own plug-in and unplug inside it
  are skipped that once. A charge that reaches one slot that meets the next
  joins both.

## Older versions

Before 2.40.0: [CHANGELOG-older.md](CHANGELOG-older.md).
