# Changelog

## 0.33.0 — Grid voltage on the power flow

- **Feature:** the grid circle on the power flow diagram (the Overview and
  the dashboard card) shows the grid's phase A voltage above its icon,
  e.g. 241 V. It starts with the Sigenergy inverter's
  `sensor.sigen_inverter_phase_a_voltage`; **Settings › Power flow card ›
  Grid voltage (phase A)** picks another, or None. The power flow sensor
  has it as a `grid_v` attribute.

## 0.32.2 — Settings and Diagnostics as lists

- **Improvement:** the Settings and Diagnostics tabs are now lists, as in
  OCPP Charge Proxy: each card is a row with an icon, its title and a
  one-line summary kept up to date (e.g. "Rate 8 kW · Top rate 12.5 kW ·
  Nearly full 95%", or "Working · 4 entities"). Click a row to open it in
  place. Which rows are open is remembered on each device.
- **Change:** a group of Home Assistant entities all set up automatically
  and working now keeps its row (rather than being hidden), and says so
  when opened; its summary says if one has a problem.

## 0.32.1 — SoC chart: pinch properly, and further back

- **Fix:** pinching the car SoC chart only zoomed a little (the chart redrew
  under the fingers and lost the gesture after the first step); it now
  follows the fingers all the way
- **Change:** zoomed in (by touch, dragging or scrolling) the chart can go
  anywhere in the last 48 hours, not only the period picked: moving or
  zooming out past it fetches the rest. Double tap or **Reset zoom** goes
  back to the period picked
- **Fix:** **Reset zoom** now sits over the chart's top right corner instead
  of in its heading, where (on a phone) showing it pushed the chart down

## 0.32.0 — SoC chart on a phone

- **Feature:** the Overview's car SoC chart works by touch: move a finger across it
  to go back or forward in time, pinch to zoom in and out, double tap to go
  back to the whole period, and tap for the reading at that time. A finger
  moving up or down still scrolls the page

## 0.31.0 — Drag to change

- **Feature:** drag either end of an entry in the schedule's week view to
  change when it starts or ends (in half hours), saved straight away. An
  every-week entry changes every week; one that runs past midnight has its
  end on the next day

## 0.30.1 — No car when it's unplugged

- **Change:** the power flow (the Overview and the dashboard card) leaves the
  car off while it isn't plugged into the EVDC; it comes back when it's
  plugged in. The power flow sensor has a new `car_plugged` attribute

## 0.30.0 — Discharge only

- **New:** a **Discharge only** quick hold (Schedule tab › Limits now): the
  high limit at the car's SoC now, for 1 to 8 hours or until a time, so the
  car can discharge (down to the low limit) but isn't charged. Like the other
  holds it's a one-off schedule entry, so it ends by itself.

## 0.29.0 — The house's whole use, if you like

- **New:** Settings › Power flow card › **Take them off the house**: on
  (the default), the house on the power flow shows what's left after its
  devices ("4.2 kW of 12.6 kW"); off, its whole use, the devices included.

## 0.28.3 — Each tab from the top

- **Change:** switching tab goes back to the top of the page, instead of
  staying as far down as you'd scrolled on the last one.

## 0.28.2 — Bigger on a phone

- **Change:** with the car and one device (e.g. a Hypervolt), the power
  flow stays in the usual square, the car above the house and the device
  below it, so it isn't squeezed narrow and small on a phone. With more
  devices they go in a column to the right as before.

## 0.28.1 — Every circle the same size

- **Change:** on the power flow, the car and your devices off the house
  are the same size as solar, the grid, the house and the battery. Up to 3
  sit in a column; with more they zigzag between two. The car's SoC is in
  its circle, above its icon, with ← while it discharges into the house and
  → while it charges.

## 0.28.0 — The car off the house like the rest

- **Change:** on the power flow (the Overview and the dashboard card) the
  car on the DC charger is drawn like your devices off the house: first in
  the column to the right of the house, in blue, with its power, its SoC
  and whether it's charging or discharging (the dots run into the house
  while it discharges).
- **New:** Settings › Power flow card can give the drawing its own
  **house** and **car** sensors too, alongside solar, the grid and the home
  battery; only the drawing changes.
- **Change:** the first device's colour is pink, so it isn't mistaken for
  the car.

## 0.27.0 — Devices off the house on the power flow

- **New:** Settings › **Power flow card**: devices off the house, e.g. a
  car charger and a heat pump (there from the start; **+ Add a device** for
  more, up to 6). Pick a device's power sensor and it's drawn coming off
  the house, in its own colour with its name and icon, and the house shows
  what's left of its use. Pick a **plugged in** entity and it's only drawn
  while that's on. Nothing picked: nothing drawn.
- **New:** the same card can give the power flow its own **solar**,
  **grid** and **home battery** sensors; what the add-on decides from
  doesn't change.
- Both the Overview's diagram and the dashboard card (EVDC Power Flow)
  draw them, the same way.

## 0.26.0 — Zoom into the car's SoC

- **New:** the Overview's **Car SoC** chart zooms: drag across a stretch
  of it, or scroll over it (in and out around the pointer, down to 10
  minutes). **Reset zoom** or a double-click goes back to the whole period;
  picking another period does too. The time labels follow what's shown.

## 0.25.0 — The page redesigned

- **Improvement:** each tab opens with a banner that answers its main
  question first, the detail below:
  - **Overview:** **Right now**: what it's doing and why, the car's SoC
    against the limits, and Start/Stop. Blue normally, green while
    charging, grey when unplugged, red on a charger alarm.
  - **Schedule:** **Limits now** (the limits in force and where they come
    from) with **Quick hold** beside them; the default limits as three
    fields side by side.
  - **Statistics:** the four headline numbers (usable capacity, round-trip
    losses, is it paying, dropouts), with the cards below for the detail.
  - **Diagnostics:** the health: green **All good**, orange or red when
    something needs attention.
- **Improvement:** shorter pages: **Recent starts and stops** shows the
  latest 6 (**Show all** for the rest) and **History** 15 at a time; cards
  side by side no longer leave gaps under the shorter one.
- **Improvement:** Settings puts **How it runs** first and the entities
  after it, with both "show" switches together; each entity's reading
  stays on its label's line. Schedule entries' Skip, Pause and Delete are
  quieter, Delete in red. Long log lines wrap on the Debug tab.
- **Change:** Spare power on the Overview reads **None (not exporting)**
  instead of 0.00 kW.
- **New:** Settings › **Appearance**: **Colourful** (the banners, the
  default) or **Basic** (plain cards). Saved in each browser.

## 0.24.0 — The schedule redesigned

- **Improvement:** the Schedule tab's week view is easier to read: taller
  rows with the day and date, each split into a tinted high-limit lane on
  top and low-limit lane below, gridlines and times every 3 hours, entries
  as rounded blocks labelled with the limit (and the entry's label when it
  fits), the past of today dimmed, a marker for now, and the picked entry
  outlined.
- **Improvement:** All entries is a row per entry instead of a table: its
  times (and "next day"), label, the days it runs as Mo–Su dots (or the
  date, for a one-off), its limits with their colours, a status label (On
  now, Next…, Paused) with any skipped days, and its buttons. A coloured
  edge shows which limits it sets. Click a row to open it in the week, as
  before.

## 0.23.1 — Chart markers on the SoC line

- **Fix:** on the SoC chart, a start or stop just after plugging in was
  drawn at the SoC from before the car was unplugged (e.g. at the top of the
  chart) instead of on the line. Markers now sit on the SoC line where it
  is at that time, and where the line starts again after plugging in.

## 0.23.0 — Helpers named like the sensors

- **Change:** the helpers the add-on makes are named like its sensors:
  **EVDC SoC Range High Limit** (`input_number.evdc_soc_range_high_limit`),
  **EVDC SoC Range Low Limit** (`input_number.evdc_soc_range_low_limit`) and
  **EVDC SoC Range Vehicle Battery Capacity**
  (`input_number.evdc_soc_range_vehicle_battery_capacity`). The charging
  mode select keeps its name.
- **Change:** if a helper with an old name is picked on the Settings tab,
  it's moved to the new name at its value, and the old one is removed
  (unless it's from YAML). Update any dashboard or automation that used the
  old entity IDs.

## 0.22.2 — Limit helpers made again straight away

- **Fix:** deleting the add-on's **EVDC SoC High Limit** or **Low Limit**
  helper in Home Assistant now makes it again within a few seconds, not
  only when the add-on next restarts or reconnects.

## 0.22.1 — Says why a limit helper isn't made

- **Improvement:** when a limit picker on the Settings tab names a helper
  of your own (e.g. `input_number.v2x_cut_off_threshold_high`), the log says
  so each time it connects, and that clearing the picker and restarting
  the add-on makes the add-on's own **EVDC SoC High Limit** / **Low Limit**
  helper instead.

## 0.22.0 — The add-on's own limit helpers

- **Feature:** the high and low limits are the add-on's own Home Assistant
  helpers (**EVDC SoC High Limit** and **EVDC SoC Low Limit**), which you
  can change from any dashboard or automation. Each time it connects it
  checks for them, and makes any that have gone again, even if the
  Settings tab still names them (at your `v2x_cut_off_threshold` helpers'
  values, if you have them).
- **Improvement:** if a limit helper with the add-on's name is defined in
  YAML, it's still used, and the Overview says to remove it from your YAML
  and restart Home Assistant so the add-on can make it as its own.
- **Improvement:** **Diagnostics › Health** lists the helpers in use (the
  limits, battery capacity and charging mode) above the sensors, and whether
  each is the add-on's own or yours.
- **Change:** limit helpers you've picked yourself on the Settings tab, and
  the charging mode select (e.g. kept in your YAML), are left alone, as
  before.

## 0.21.0 — Battery capacity on the Schedule tab

- **Feature:** the car's battery capacity (usable kWh, for the virtual
  battery sensors) can be set under **Default limits** on the Schedule tab,
  next to the limits. It shows which entity it comes from.
- **Feature:** if the capacity comes from a sensor (e.g. the capacity
  estimate), or nothing's picked, entering a value makes the **EVDC Vehicle
  Battery Capacity** helper (or finds it), picks it on the Settings tab and
  sets it, so the virtual battery's size is one fixed number you control.
- **Docs:** the Schedule tab's battery capacity.

## 0.20.7 — Clearer logging

- **Improvement:** each start and stop in the log ends with what the add-on
  saw: the car's SoC, which way it's going (rising or falling, %/h over the
  last 30 minutes), where it is against the high and low limits, the
  charger's state, the home battery and grid power, export, the charge
  signal and the EMS mode. The same is saved with each start and stop in
  the add-on's history.
- **Improvement:** a start for the house now says what showed the house
  needing power and where the car is against both limits (e.g. "at or above
  the high limit (90%)"), not only that it's above the low limit.
- **Improvement:** each new decision is logged once at Info (e.g. "Held at
  the high limit"), with the same details, and each change in the car's SoC
  is logged with its direction.
- **Docs:** Diagnostics explains the new log lines. Logging only: no change
  to when the charger is started or stopped.

## 0.20.6 — A fresh try for each spell of importing

- **Improvement:** after a stop for charging above the high limit, the car
  can be started for the house again as soon as the house has stopped
  needing power (e.g. exported) and needs it again, not only after the
  30-minute house retry wait. For plants that swing between importing and
  exporting, each spell of importing can still start the car to discharge.

## 0.20.5 — No start-stop cycles

- **Improvement:** charging at or above the high limit is stopped after a
  minute of charging, not at once, so a moment's "Charging" as the charger
  starts (before it discharges) isn't stopped.
- **Fix:** after a stop for charging above the high limit, it isn't started
  for the house above the high limit again for 30 minutes (the **House
  retry wait**), so it doesn't start, charge and stop every few minutes.
- **Fix:** if the charger doesn't respond to 3 presses of the same button
  in a row (e.g. the car won't start), it's only pressed every 15 minutes
  until it does, instead of every minute, and it's shown under Problems.
- **Change:** the two new timings are on the Settings tab.

## 0.20.4 — Stops charging above the high limit

- **Fix:** a car charging at or above the high limit wasn't stopped while
  the grid was importing, because the import (much of it the car's own
  charging, and the home battery charging overnight) counted as the house
  needing power. Charging at or above the high limit is now stopped
  straight away.
- **Fix:** importing while the home battery charges, or while a charge
  signal is on, no longer counts as the house needing power, so the
  charger isn't started again above the high limit during a grid charge.

## 0.20.3 — A how-to README

- **Docs:** a new README (the add-on's Info page in Home Assistant): what
  it does, what you need, installing, setting it up (the entities, the
  helpers it makes, Watch only), how it decides, everyday use, Predbat, the
  power flow card and troubleshooting.

## 0.20.2 — The SoC chart starts at the new reading

- **Fix:** plugging a car in drew a jump on the Car SoC chart: from the last
  SoC the add-on knew (the car's, or another car's, from before it was
  unplugged) down or up to the new reading. Now the line starts at the
  first reading after plugging in, and a start or stop before that reading
  is marked there too.

## 0.20.1 — A live power flow card

- **Improvement:** the power flow card follows the readings live: its
  sensor is posted about a second after your sensors change (at most once a
  second), not every 5 seconds.

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
