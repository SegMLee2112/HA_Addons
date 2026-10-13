# Changelog

## 0.20.0 — The power flow as a dashboard card

- **Feature:** the Overview's power flow diagram as a card for any
  dashboard: **EVDC Power Flow** in the card picker (or `type:
  custom:evdc-power-flow-card`). The add-on installs it itself (into Home
  Assistant's www folder and the dashboards' resources); Diagnostics ›
  Health says whether that worked.
- **Feature:** `sensor.evdc_soc_range_power_flow`: the house's use, with
  every reading and flow as attributes (posted at most every 5 seconds, not
  kept in the long-term statistics). The card draws it.
- **Change:** the add-on now has write access to Home Assistant's config
  folder (to copy the card into its www folder).

## 0.19.0 — Skip a scheduled slot

- **Feature:** an every-week schedule entry can be skipped once, e.g. this
  Thursday's high of 50% when you're not going in: **Skip next** (or **Skip
  this one** while it's on) in the list, **Skip** in the week view's editor
  for the day you clicked, or the ✕ on its chip on the Overview. The
  default limits are used instead for that one; every other week stays.
  Skipped ones show struck through in the week view, with **undo** in the
  list, and are forgotten once they're over.

## 0.18.0 — Conversion loss only where it happens

- **Fix:** "Is it paying?" applied the inverter's AC ↔ DC efficiency to
  every kWh into and out of the car. The DC charger is on the inverter's DC
  side, so charging from solar or the home battery, and discharging into the
  home battery, have no conversion. Now only the share that crossed between
  AC and DC (charging from the grid, discharging to the house or the grid)
  has it, worked out every few seconds from the inverter's AC power (or the
  grid import and the home battery without it). The shares are shown under
  Charging and Discharging. Days recorded before this are counted as before
  (all crossing).
- **Docs:** the round trip is all DC (the charger's counters), with no
  conversion.

## 0.17.1 — Tidy up

- **Change:** removed code nothing used any more: the capacity chart's
  drawing code and styles, an unused entity helper and styles on the web
  page, and two figures the Statistics tab never showed.

## 0.17.0 — Round trip from short sessions, battery size

- **Fix:** the round trip never came out because it only used charges and
  discharges that moved the SoC 10% or more, and discharges cut short by
  dropouts never do. Now it adds up the SoC moved (and its kWh, measured
  between the SoC's tick-overs) over the latest sessions, short ones
  included, until there's 10% each way (up to 60%, last 30 days). The
  first time it connects, sessions already recorded get their figures from
  Home Assistant's history.
- **Feature:** a Battery size chip on the Statistics tab: 100% of the car's
  battery itself, between the kWh to fill it and the kWh back out of it.
- **Change:** the usable capacity chart is gone.

## 0.16.0 — Exporting is enough

- **Change:** spare power from exporting only needs the export above the
  export level (0.5 kW) when it decides, as in the automation's
  condition; it no longer has to have been for 10 s. The "Spare power:
  for" setting is gone (a saved value is ignored).

## 0.15.3 — Checked against the automation

- **Fix:** the minute between button presses only applies to pressing the
  same button again. Before, a stop at the low limit within a minute of a
  start (or at the high limit within a minute of a start) waited out the
  minute, so the car could discharge below the low limit for up to a
  minute; the "V2X SOC Range Manager" automation stops it straight away.
- **Docs:** the decisions were checked against the automation, rule by
  rule; the remaining differences are listed in DOCS.md.

## 0.15.2 — Alarms on the SoC chart

- **Fix:** alarms recorded before 0.13.0 showed on the Car SoC chart as
  unplugged. The next time it connects, the add-on checks those against
  Home Assistant's history (once) and marks them as alarms, shaded red.
- **Change:** Plugged in (Settings › Advanced) no longer lists `#Alarm`,
  which the Sigenergy integration never reports (it says `Alarm`). During
  an alarm `binary_sensor.evdc_soc_range_plugged_in` reads off, as a
  plugged-in template that treats an alarm as unplugged does.

## 0.15.1 — Clearer status after a stop and a start

- **Change:** during the restart wait the status reads "Reconnecting car
  in 80 s" (counting down), with why it will start in the reason. If the
  car won't be started once the wait's over (e.g. it's held at a limit),
  the status says that instead.
- **Fix:** for the few seconds after it presses Start (or Stop), while
  the charger responds, the status stays "Starting" (or "Stopping")
  rather than flicking to "Waiting to retry". "Waiting to retry" is now
  only shown when a press failed or something else needs doing.

## 0.15.0 — Virtual battery sensors

- **Change:** the energy sensors are now named for the virtual battery:
  `sensor.evdc_soc_range_virtual_battery_soc` (%),
  `..._virtual_battery_usable_energy` (kWh) and
  `..._virtual_battery_max_energy` (kWh), with the limits, the car's
  capacity and whether it's in use as attributes. They replace
  `..._window_percent`, `..._available_energy` and `..._window_energy`
  (which go when Home Assistant next restarts): update anything that
  used those.

## 0.14.0 — Tidier Settings

- **Feature:** the Settings tab hides the entities the add-on set up
  itself (the Sigenergy integration's, the helpers it made or found, the
  Octopus rate sensors) while they're working. Any with a problem, or
  that you picked yourself, stay shown. **Show the ones set up
  automatically** shows them all (remembered in this browser).

## 0.13.0 — Charger alarms

- **Feature:** when the charger's running state is an alarm (`#Alarm`,
  i.e. an error with the charger), the add-on leaves it alone (no button
  presses) until it clears, and says so: the status, the health dot, a
  red chip, a History entry when it starts and when it clears (with how
  long it lasted), a notification through your notify service, and
  `binary_sensor.evdc_soc_range_charger_alarm`.
- **Feature:** the Car SoC chart shades alarms red, with a ! where each
  one began.
- **Change:** the History's "Dropouts and failures" filter is now
  "Problems" and includes alarms. Which running states count as an alarm
  is under Settings › Advanced.

## 0.12.0 — More chart periods

- **Feature:** the Car SoC chart shows the last 1, 3, 6, 12, 24 or 48
  hours, with the time axis spaced to suit (every 10 minutes for 1 hour,
  up to every 12 hours for 48).

## 0.11.2 — Crossing limits from two entries

- **Fix:** when two schedule entries that are on at once cross (e.g. one
  sets the high to 50% and a later one the low to 60%), the one that
  started later now stays and the other moves out of its way, as the
  schedule says. Before, the high always won.

## 0.11.1 — Car arrow

- **Fix:** the power flow's car shows ↓ while discharging (down into the
  house below it) and ↑ while charging.

## 0.11.0 — Who started it, and Quick hold on the Schedule tab

- **Feature:** when the charger starts or stops without this add-on
  (another automation, the Sigenergy app, Predbat or the charger itself),
  the history and the Overview say so ("Started elsewhere" / "Stopped
  elsewhere"), and the SoC chart marks it. While watching only, a restart
  by your own automation shows up this way, so you can see why there was
  no "Would start".
- **Fix:** while watching only, a stop by something else just after a
  "Would stop" (e.g. your automation at the low limit) is no longer
  counted as a dropout.
- **Change:** the restart wait counts from when Home Assistant saw the
  charger stop, not from when the add-on next looked.
- **Change:** Quick hold is on the Schedule tab, with the holds that are
  on (✕ to end one early). The Overview's Car card still shows them.
- **Change:** the Car SoC chart shades the whole height behind the line
  for charging (green), discharging (blue) and unplugged (hatched), shows
  the limits as dashed lines, and the SoC line breaks while the car is
  unplugged.

## 0.10.0 — Quick holds, SoC chart and limit bounds

- **Feature:** Quick hold on the Overview's Car card: No discharging (the
  low limit at the SoC now), Hold here (stays where it is) or Keep at
  least a level, for 1–8 hours or until a time. Each is a one-off
  schedule entry that ends by itself; end it early with the ✕ on its chip.
- **Feature:** a Car SoC chart on the Overview for the last 24 or 48
  hours: the SoC with the limits band behind it (scheduled changes
  included), a strip for charging, discharging and unplugged, and markers
  for starts, stops (hollow while watching only) and dropouts. Hover for
  the details. The first time it connects it fills in from Home
  Assistant's history.
- **Feature:** a schedule entry sets the High limit, the Low limit or
  Both, picked in its editor.
- **Change:** the limits can't go below 20% or above 99%, whatever the
  helpers, the schedule or a hold say; the add-on's new limit helpers are
  made with those bounds.

## 0.9.0 — History on the Schedule tab

- **Feature:** a History card on the Schedule tab: every start and stop,
  what it would have done while Watch only was on, dropouts, failures and
  each time scheduled limits came on or went back to the defaults, by
  day, with the limits in use and the car's SoC at the time. Filter to
  starts and stops, watch only, limits, or dropouts and failures. The
  last 500 are kept (up from 100).

## 0.8.0 — Schedule week view, power flow fixes

- **Feature:** a week view on the Schedule tab, like the OCPP Charge
  Proxy's: today and the next 6 days, high limits in the top half of each
  day and low limits in the bottom half. Click an entry to change, pause
  or delete it; click or drag across a day (or + Add) to add one. The list
  of entries is below it.
- **Fix:** the power flow diagram reads the way the Power Flow Card Plus
  does: the grid shows ← sent to the grid and → taken from it, the home
  battery and the car ↓ in and ↑ out (both always shown for the battery),
  with the SoC above the icon.
- **Fix:** the moving dots go the right way in every browser (a flow
  against a line's drawn direction now follows a reversed copy of it).

## 0.7.0 — Power flow

- **Feature:** the Overview's Power and Virtual battery cards are one
  Power card: a power flow diagram (solar, grid, home battery, house and
  car, with moving dots along each flow, faster for more power) beside
  the virtual battery and what it's doing.
- **Feature:** a house power picker
  (`sensor.sigen_plant_consumed_power` by default); without it the house
  is worked out from the others.

## 0.6.0 — Schedule on its own tab, with default limits

- **Change:** the limit schedule has its own Schedule tab, with the
  default limits (the limit helpers) at the top: used whenever the
  schedule doesn't set a limit, and whenever nothing's on. A Now card
  shows the limits in use and where each comes from.
- **Change:** the Overview's Car card no longer has the limit boxes; it
  shows any scheduled limit and links to the Schedule tab.

## 0.5.0 — Limit schedule, round trip, costs and dropouts

- **Feature:** a limit schedule on the Overview: change the high and/or
  low limit for a while, every week (e.g. Tue and Thu 23:00–08:00 high
  50%) or once (e.g. tomorrow 06:00–10:00 low 95%). The everyday limits
  (the helpers) come back afterwards; one-off entries tidy themselves
  away. Pause, resume or delete entries.
- **Feature:** the round trip from the SoC: the kWh it takes to put 1%
  into the car against the kWh back for 1% out, from each charge and
  discharge (the median of the last 10 of each). The Statistics tab's
  losses now come from this, with the inverter's measured figures beside
  it when its sensors are there.
- **Feature:** Is it paying? Import and export rate pickers (the Octopus
  Energy integration's current rate sensors are found by themselves); what
  charging cost and discharging was worth over 30 days, the margin on each
  kWh out after the losses, the break-even price and what the losses cost.
  An inverter AC ↔ DC efficiency setting (96%) covers the charger counting
  DC.
- **Feature:** discharge dropouts: each time the car stops discharging by
  itself (not a stop from here, not unplugging) is logged on the Overview
  and counted on the Statistics tab, with a sensor and an optional
  notification after so many in a day.
- **Fix:** the loss chart's axis showed 0.0 for small values.

## 0.4.0 — Virtual battery on the Overview

- **Feature:** a Virtual battery card on the Overview: the car between its
  limits as one battery (as Predbat sees it), with how full it is, kWh
  stored of the window, what it's doing now and roughly when it'll be
  full or empty, its maximum rate (and the home battery's and plant's),
  today's kWh in and out, and the car's battery capacity and health. It
  replaces the Energy in the car card.

## 0.3.0 — Battery statistics

- **Feature:** a Statistics tab for the car's battery:
  - **Usable capacity:** each charge or discharge that moves the SoC 10% or
    more gives an estimate (kWh at the charger ÷ SoC moved, measured
    between the SoC's tick-overs). The latest is the median of the last
    10, compared with the capacity helper, with a trend (kWh and % a year)
    once there are 6 estimates over 2 months.
  - **Conversion losses:** the inverter's DC side (solar, home battery,
    car) against its AC side, every few seconds; the car gets its share of
    the loss by its share of the DC flow. The last 30 days by day, and the
    charging (AC → car) and discharging (car → AC) efficiency from clean
    sessions.
  - **Sessions:** every charge and discharge with its SoC, energy,
    capacity estimate, efficiency and loss.
- **Feature:** the last 30 days of Home Assistant's history are read once,
  so the capacity estimates and sessions start with something in them
  (losses are recorded from now on).
- **Feature:** sensors for the capacity estimate, battery health, charge
  and discharge efficiency and today's conversion loss.
- **Feature:** pickers for the charger's power and total charged and
  discharged energy, solar power and the inverter's AC power (Sigenergy's
  by default), and settings for the capacity span, what counts as a clean
  session, and flipping the inverter power's sign.

## 0.2.0 — Charging mode select and battery rate sensors

- **Feature:** battery rate sensors, as the "V2X Battery Rate", "Rate Car"
  and "Rate House" template sensors did:
  `sensor.evdc_soc_range_battery_rate`, `..._battery_rate_car` and
  `..._battery_rate_house`. Their fixed figures (8 and 12.5 kW, 95%, the
  4.5 kW home battery and 8 kW car top rates) are settings, and they read
  the Sigenergy plant's available power, battery SoC and rated capacity.
- **Change:** without a charging mode select, it now makes
  `input_select.sigenergy_evdc_charging_mode` (V2X, Solar Surplus, Fast
  Charging) rather than an on/off switch. It manages the charger only in
  V2X.
- **Change:** the energy sensors also count Solar Surplus mode, as the
  V2X SoC template sensors did.

## 0.1.0 — First version

- **Feature:** keeps the car's SoC between a low and a high limit in V2X
  mode by starting and stopping a Sigenergy DC charger, following the
  "V2X SOC Range Manager" automation's rules: start when the house needs
  power (above the low limit), when there's spare power (below the high
  limit) or when between the limits; stop at the high limit when not
  discharging and at the low limit when discharging.
- **Feature:** a restart wait (3 minutes) after the charger stops for any
  reason, and at least a minute between button presses.
- **Feature:** works out "plugged in", "running" and "discharging" from
  the charger's running state, and keeps the car's last known SoC itself
  (also after a restart).
- **Feature:** makes its high and low limit, battery capacity and V2X mode
  helpers when they aren't set, starting from existing V2X helpers'
  values; uses `input_select.sigenergy_evdc_charging_mode` as the V2X mode
  if it's there.
- **Feature:** an optional charge signal (e.g. Predbat charging), an
  optional notify service, a margin to stop it bouncing at a limit, and
  Watch only (decides and logs, presses nothing).
- **Feature:** sensors for its status, last action, the car's SoC,
  plugged in, charger running, the energy above the low limit and between
  the limits, and button presses today.
- **Feature:** a web page: status and reason, the SoC against the limits
  (set them there), power readings, energy, recent starts and stops,
  Start/Stop buttons, the entity pickers and settings, and diagnostics.
