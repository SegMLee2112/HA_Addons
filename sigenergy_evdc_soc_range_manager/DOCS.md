# SigEnergy EVDC SoC Range Manager

Keeps your car's state of charge (SoC) between a low and a high limit while
a Sigenergy DC charger (EVDC) is in V2X mode. It does this by pressing the
charger's **Start** and **Stop** buttons. Once the charger is started,
Sigenergy decides whether the car charges or discharges; this add-on only
decides when it may run.

## What it does

It only acts while the car is plugged in, the V2X mode entity says V2X (or
is on) and the car's SoC is known. Then, in this order:

| | When | It |
| --- | --- | --- |
| 1 | The house needs power (home battery discharging, or importing from the grid) and the car is above the low limit | starts the charger, so the car can discharge |
| 2 | There's spare power (exporting, or a charge signal such as Predbat charging) and the car is below the high limit | starts the charger, so the car can charge |
| 3 | The car is at or above the high limit, isn't discharging and the house doesn't need power | stops the charger |
| 4 | The car is at or below the low limit and is discharging (and no charge signal is on) | stops the charger |
| 5 | The car is between the limits | starts the charger |

So between the limits the charger always runs. At the high limit it only
runs to discharge, and at the low limit only to charge.

After the charger stops, for whatever reason, it isn't started again for
the **restart wait** (3 minutes). Some cars stop discharging now and then;
this stops the add-on restarting it over and over. While it waits, the
status shows "Reconnecting car in … s" if it will start again once the wait
is over. It also never presses the same button again within a minute (the other
button goes straight away, e.g. Stop at the low limit just after a Start).

It looks whenever one of your entities changes, and every few seconds anyway.

### Compared with the "V2X SOC Range Manager" automation

The rules are the automation's, in its order, with the same thresholds.
The differences:

- It doesn't press Start while the charger is already running, or Stop
  while it's already stopped (the automation's rules 2 and 3 can; the
  press does nothing).
- If the charge signal (e.g. Predbat) is unavailable, the low-limit stop
  still happens (the automation's needs it to be "off").
- A charger alarm leaves the charger alone, and the limits stay between
  20% and 99%.
- It checks every few seconds as well as on changes, so it can act a
  little sooner than the automation's triggers would.

## Getting started

1. Install the add-on and start it, then open it from the sidebar.
2. **Settings** shows the entities it uses. They start as the Sigenergy
   integration's own, so most need no change. The ones it set up itself
   are hidden while they're working (any with a problem stay shown);
   turn on **Show the ones set up automatically** to see or change them.
3. The first time it connects to Home Assistant it makes helpers for
   anything left empty (**Settings › Devices & services › Helpers**):
   - **EVDC SoC High Limit** and **EVDC SoC Low Limit** (input numbers, %).
     If you already have `input_number.v2x_cut_off_threshold_high` and
     `input_number.v2x_cut_off_threshold`, they start at the same values.
   - **EVDC Vehicle Battery Capacity** (kWh), for the energy sensors. It
     starts at your `input_number.vehicle_max_capacity` if you have one,
     otherwise 0 (set it to see the energy sensors).
   - **SigEnergy EVDC Charging Mode** (`input_select.sigenergy_evdc_charging_mode`:
     V2X, Solar Surplus, Fast Charging), unless you already have it. It
     only manages the charger in V2X.

   You can point any of these at a helper you already have instead.
4. Optional: a **charge signal**, e.g. `binary_sensor.predbat_charging`.
   While it's on, the car is charged if there's room, and isn't stopped at
   the low limit.
5. If you've been doing this with an automation, turn **Watch only** on
   (Settings) at first: the add-on logs what it would do without pressing
   anything, so you can compare. Then turn your automation off and Watch
   only off.

The **Overview** shows what it's doing and why, the car's SoC against the
limits (set on the Schedule tab), the power readings it decides from, the
virtual battery and the recent starts and stops.

The **Power** card's diagram shows where the power is going: solar,
the grid, the home battery, the house and the car, with dots moving along
each flow (faster for more power). As in the Power Flow Card Plus, the
grid shows ← sent to the grid and → taken from it, the home battery ↓ in
and ↑ out, and the car (above the house) ↓ discharging into the house and
↑ charging. Solar is shared out to the house
first, then the home battery, then the grid; the car hangs off the house.
The house's use comes from `sensor.sigen_plant_consumed_power` (or is
worked out from the others if that isn't set).

**Quick hold** (on the Schedule tab) holds the car for a while, from now:
**No discharging** puts the low limit at its SoC now, **Hold here** keeps
it where it is, and **Keep at least** sets a low limit you choose, for 1
to 8 hours or until a time. Each is a one-off schedule entry, so it ends
by itself; the ✕ on its chip ends it early.

The **Car SoC** chart shows the last 1, 3, 6, 12, 24 or 48 hours: the SoC (with a
break while the car was unplugged), the limits as dashed lines (with
scheduled changes), the background shaded for charging, discharging and
unplugged, and markers for starts, stops (hollow while watching only),
dropouts and starts or stops by something else. Hover over it for the
details.

The limits always stay between **20% and 99%**, whatever the helpers, the
schedule or a hold say.

The **virtual battery** is the car between its limits seen as one
battery, the way Predbat sees it through the available energy, window and
battery rate sensors: how full it is, kWh stored of the window, what it's
doing now and roughly when it'll be full or empty at that rate, its
maximum rate, today's kWh in and out, and the car's capacity and health. **Start charger** and
**Stop charger** press the buttons by hand.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| Restart wait | 180 s | After the charger stops, how long before it may be started again |
| House needs power: battery | 0.1 kW | Home battery discharging faster than this |
| House needs power: grid | 0.1 kW | Importing more than this |
| Spare power: export | 0.5 kW | Exporting more than this… |
| Margin | 0% | Starts need the SoC this far inside the limits (stops are at the limits). 1 or 2 stops it bouncing at a limit |
| Between button presses | 60 s | At least this long before pressing the same button again |
| Watch only | off | Decide and log, but don't press anything |
| Notify service | none | e.g. `notify.mobile_app_your_phone`: told on every start and stop |
| Battery rates | 8 and 12.5 kW, 95%, 4.5 kW home, 8 kW car | See [Battery rates](#battery-rates) |

**Advanced** has the words the charger uses for its running states (which
mean plugged in, running and discharging), the V2X mode states, and the EMS
modes in which exporting isn't spare power (Command Discharging). Only
change these if your set-up reports different words.

Power sensors can be in W or kW.

## Its own entities

| Entity | |
| --- | --- |
| `sensor.evdc_soc_range_status` | What it's doing, with the reason as an attribute |
| `sensor.evdc_soc_range_last_action` | Started, Stopped, Would start (watch only) or Failed to…, with when, why and the SoC |
| `sensor.evdc_soc_range_vehicle_soc` | The car's last known SoC (kept while the charger reports 0 or nothing, and after a restart) |
| `binary_sensor.evdc_soc_range_plugged_in` | Car plugged in |
| `binary_sensor.evdc_soc_range_charger_running` | Charger running (charging, discharging or preparing) |
| `sensor.evdc_soc_range_virtual_battery_soc` | The virtual battery's SoC: how full the window between the limits is (%) |
| `sensor.evdc_soc_range_virtual_battery_usable_energy` | Its usable energy: kWh the car can give before the low limit |
| `sensor.evdc_soc_range_virtual_battery_max_energy` | Its size: kWh between the low and high limits |
| `sensor.evdc_soc_range_battery_rate` | The plant's charge/discharge rate with the car plugged in (kW) |
| `sensor.evdc_soc_range_battery_rate_car` | The car's share of it (kW) |
| `sensor.evdc_soc_range_battery_rate_house` | The home battery's share of it (kW) |
| `sensor.evdc_soc_range_presses_today` | Button presses today |

The virtual battery sensors read 0.01 kWh (and 0%) when the car isn't
plugged in or isn't in V2X or Solar Surplus mode, as the V2X SoC template
sensors did; their attributes give the limits, the car's capacity and
whether it's in use. All of them show as unavailable while the add-on is
stopped.

### Battery rates

For Predbat or similar. With the car plugged in and in V2X or Solar
Surplus mode, the rate is the plant's available power, capped at
**Rate** (8 kW), or **Top rate** (12.5 kW) once the home battery is
**Nearly full** (95%). In Fast Charging it's the top rate; otherwise 0.

That's shared between the home battery and the car in proportion to their
sizes (the home battery's rated capacity, and the car's kWh between its
limits), each capped at its top rate (4.5 kW home, 8 kW car), and scaled
down together if that's more than the plant can do. Without the car it's
all the home battery's (up to its top rate). All the figures are on the
Settings tab, and the plant entities default to Sigenergy's
(`sensor.sigen_plant_available_max_active_power`,
`sensor.sigen_plant_battery_state_of_charge`,
`sensor.sigen_inverter_rated_battery_capacity`).

## Limit schedule

The **Schedule** tab sets the limits. **Default limits** at the top are
used whenever the schedule doesn't set a limit, and whenever nothing's
on; they're the limit helpers, so a dashboard or automation can change
them too. **Now** shows the limits in use and where each comes from.
The schedule below changes the high and/or low limit for a while, every
week or once. Each entry sets the high limit, the low limit or both. Its week view shows today and the next 6 days, with high
limits in the top half of each day and low limits in the bottom half:
click an entry to change, pause or delete it, or click or drag across a
day (or **+ Add**) to add one.

- Every week: e.g. Tue and Thu 23:00–08:00, high 50% (an entry can run
  past midnight).
- Once: e.g. tomorrow 06:00–10:00, low 95% (the car isn't discharged
  below 95% that morning). One-off entries are removed once they've
  finished.
- Skip once: an every-week entry can be left out for one day only (this
  Thursday, say), with **Skip next** (or **Skip this one** while it's on)
  in the list, **Skip** in the editor when you click that day's slot in the
  week view, or the ✕ on its chip on the Overview. The defaults are used
  instead for that one; it shows struck through in the week view, and
  **undo** in the list puts it back.

The defaults aren't changed by the schedule; while an
entry is on, its limits are used instead (the Car card says so). Where
entries overlap, the one that started last wins. If a scheduled limit
crosses the other one, the other moves out of its way (between two
entries, the one that started later stays) (a scheduled low of
95% with an default high of 80% makes the high 96%). The energy and rate
sensors use the scheduled limits too, so Predbat sees the change.

**History** (at the bottom of the Schedule tab) lists every start and
stop, what it would have done while Watch only was on, dropouts,
failures, and each time scheduled limits came on or went back to the
defaults, with the limits in use and the car's SoC at the time. When the
charger starts or stops without this add-on (another automation, the
Sigenergy app, Predbat or the charger itself) it says so: "Started
elsewhere" or "Stopped elsewhere". It keeps the last 500.

**Charger alarms.** When the charger's running state is an alarm
(`Alarm` or `Fault`: an error with the charger), the add-on leaves the charger alone
until it clears. The status and the health dot say so, the History notes
when it started and cleared, your notify service (if set) is told, the
Car SoC chart shades it red, and `binary_sensor.evdc_soc_range_charger_alarm`
turns on (and `binary_sensor.evdc_soc_range_plugged_in` reads off). Which
states count is under Settings › Advanced.

## Statistics

The **Statistics** tab is about the car's battery.

**Usable capacity.** Each charge or discharge that moves the SoC at least
10% gives an estimate: the kWh at the charger divided by the SoC moved.
It's measured between the moments the SoC ticks over, since it's only a
whole number. The figure shown is the median of the last 10, against your
battery capacity helper, and after 6 estimates over 2 months there's a
trend in kWh (and %) a year. It's a guess at degradation: the energy is
measured at the charger, so it includes the car's own charging losses,
and the car's SoC reading has its own quirks. Watch the trend rather than
any one figure. Fast charges from low to high give the best estimates.

**Battery size.** 100% of the battery itself: between the kWh it takes to
fill it (with the charging losses on top) and the kWh you get back out of
it, from the round trip below, taking the losses as about even each way.

**Round trip.** The kWh per 1% of SoC each way: say 0.60 kWh to put 1% in,
but only 0.54 kWh back for 1% out, a 90% round trip. Every charge and
discharge counts, short ones too (a discharge cut short by a dropout after
3%): the SoC moved and its kWh, measured between the SoC's tick-overs, are
added up over the latest sessions until there's at least 10% each way (up
to 60%, over the last 30 days). The last 30 days' losses are the kWh out of the car × (1 ÷ round trip
− 1). This covers the charger and the car; the inverter's own AC ↔ DC
loss is on top.

**Is it paying?** With your import and export rate sensors set (the
Octopus Energy integration's current rate sensors are picked up by
themselves), every kWh into the car is priced at the import rate when
you're importing, otherwise at the export rate you gave up; every kWh out
at the import rate it saves, or the export rate when exporting. Over 30
days it shows what charging cost, what discharging was worth, the margin
on each kWh out after the losses, the break-even price a kWh out must be
worth, and what the losses cost. The charger counts DC energy and sits on
the inverter's DC side with the solar and the home battery, so only the
energy that crossed between AC and DC has the **Inverter AC ↔ DC
efficiency** (96%, each way): charging from the grid, and discharging to
the house or the grid. Charging from solar or the home battery, and
discharging into the home battery, stay DC. The share is worked out every
few seconds from the inverter's AC power (or, without it, the grid import
and the home battery) and shown under Charging and Discharging. The round
trip itself is all DC (the charger's counters), with no conversion. Prices
are recorded from when they're set.

**Discharge dropouts.** Each time the car stops discharging by itself
(not a stop from here, and not unplugging) is logged on the Overview and
counted by day. **Dropout alert** (Settings) sends a notification after
so many in a day.

**Inverter losses.** Every few seconds it also compares the inverter's DC
side (solar in, home battery in or out, car in or out) with its AC side;
what goes in and doesn't come out is lost. The car is given its share of
that by its share of the DC flow at the time. Sessions with little solar
or home battery going at the same time ("clean" ones) give a straight
efficiency for charging (AC → car) and discharging (car → AC). If the
losses read large and negative, turn on **Flip the inverter's power**
(Settings): your inverter's power sensor reads the other way round.

It reads `sensor.sigen_inverter_dc_charger_total_charging_capacity` and
`..._total_discharging_capacity` (the car's energy),
`sensor.sigen_inverter_dc_charger_output_power`, `sensor.sigen_plant_pv_power`,
`sensor.sigen_inverter_active_power` and the home battery power. Change
any of them on the Settings tab. The first time it connects it reads the
last 30 days of these from Home Assistant's history for the sessions and
capacity estimates; the losses are recorded from then on.

| Entity | |
| --- | --- |
| `sensor.evdc_soc_range_capacity_estimate` | The car's usable capacity estimate (kWh) |
| `sensor.evdc_soc_range_battery_health` | ...as a % of the capacity helper |
| `sensor.evdc_soc_range_charge_efficiency` | AC → car (%) |
| `sensor.evdc_soc_range_discharge_efficiency` | Car → AC (%) |
| `sensor.evdc_soc_range_conversion_loss_today` | The car's share of today's inverter loss (kWh) |
| `sensor.evdc_soc_range_round_trip_efficiency` | kWh out per 1% ÷ kWh in per 1% (%) |
| `sensor.evdc_soc_range_dropouts_today` | Times the car stopped discharging by itself today |

## Diagnostics

**Diagnostics › Health** shows the link to Home Assistant and anything
needing attention (also the coloured dot by the title). **Diagnostics ›
Debug** has the log level, recent log lines, a diagnostics bundle to
download and a restart button.

## For developers

Started from the repository's add-on template. The decision is in
`src/controller.py` (no I/O, fully tested), the reading of Home Assistant
and the button presses in `src/manager.py`, the helpers in `src/helpers.py`
and the sensors in `src/ha_entities.py`. Tests: `python -m pytest tests/`.

To release a change: bump `version` in `config.yaml`, add a `CHANGELOG.md`
entry, commit, tag `sigenergy_evdc_soc_range_manager-X.Y.Z` and push the
branch and the tag.
