# Home Assistant Add-on: SigEnergy DC Charger

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

Use your car as a **home battery, within limits you set**. With a
Sigenergy DC charger (EVDC) in V2X mode, this add-on keeps the car's state
of charge between a **low** and a **high** limit by pressing the charger's
Start and Stop buttons at the right moments, so the car powers the house
when it needs it, soaks up spare solar, and never drops below the charge
you want to keep for driving.

- Charges from spare power and discharges into the house, between your
  limits; stops at the low limit, and at the high limit unless the house
  needs power.
- A **schedule** for the limits (e.g. keep 80% on Tuesday and Thursday
  mornings), **quick holds**, and **skip once** for a day you're not going
  in.
- Copes with cars that stop discharging now and then, and leaves the
  charger alone while it shows an alarm.
- Sensors for **Predbat**: the car between its limits as a virtual battery,
  with its charge and discharge rates.
- A web page in the sidebar: what it's doing and why, a live **power flow**
  diagram (also as a card for your dashboards), the car's SoC chart,
  history, and battery statistics (usable capacity, round-trip losses,
  whether discharging is paying).
- **Watch only** mode, to see what it would do before you let it act.

[Full documentation][docs] (also the add-on's **Documentation** tab).

---

## What you need

- Home Assistant OS or Supervised (add-ons need the Supervisor).
- A **Sigenergy** inverter with the **DC charger (EVDC)**, and a Sigenergy
  integration giving the `sensor.sigen_…` entities, including the charger's
  **running state**, **vehicle SoC**, and **Start charging** / **Stop
  charging** buttons.
- A car that supports V2X discharging with the EVDC.
- Optional: **Predbat** (or another planner), for the charge signal and the
  battery sensors.

## Install

1. Add this repository to Home Assistant:
   **Settings › Add-ons › Add-on store › ⋮ › Repositories**, and add
   `https://github.com/thewhale21/HA_Addons`.
2. Find **SigEnergy DC Charger** in the store and click
   **Install**. There are no add-on options to fill in.
3. **Start** it, turn on **Show in sidebar**, and open it.

## Set it up

1. **Settings tab: check the entities.** They start as the Sigenergy
   integration's own (`sensor.sigen_inverter_dc_charger_running_state`,
   `button.sigen_inverter_dc_charger_start_charging` and so on), so most
   need no change. Ones that work are tucked away; any with a problem stay
   shown (**Show the ones set up automatically** shows the rest). Pick yours
   if your entity IDs are different.
2. **Helpers are made for you** the first time it connects (find them under
   **Settings › Devices & services › Helpers**):

   | Helper | What to do |
   | --- | --- |
   | **SigEnergy DC Charger High Limit** / **SigEnergy DC Charger Low Limit** | Your default limits, e.g. 80% and 40%. Copied from the V2X automation's thresholds if you have them |
   | **SigEnergy DC Charger Vehicle Battery Capacity** | Your car's battery size (kWh): needed for the energy sensors and the virtual battery |
   | **SigEnergy DC Charger Charging Mode** | V2X, Solar Surplus or Fast Charging. The add-on only runs the charger in **V2X** |

   The two limits are made again if they go missing, and can be changed from
   any dashboard or automation. Already have helpers for
   these? Pick them on the Settings tab instead.
3. **Turn on Watch only** (Settings) for a day or two. It logs what it
   *would* do, marked hollow on the SoC chart and in the History, without
   pressing anything.
4. **Turn off any automation** that did this before (such as the "V2X SOC
   Range Manager" automation), then turn **Watch only** off.
5. Set the **charging mode** to **V2X** and plug the car in. The Overview
   says what it's doing and why.

## How it decides

While the car is plugged in, in V2X mode, with its SoC known, it checks
every few seconds and whenever a reading changes:

| The car is… | It… |
| --- | --- |
| Between the limits | runs the charger: Sigenergy charges or discharges the car as the house needs |
| At the high limit | stops it if it's been charging for a minute; otherwise stops it unless the house needs power (then the car may discharge) |
| At the low limit | stops it, unless there's spare power to charge from, or the charge signal is on |

After any stop it waits 3 minutes before starting again (the **restart
wait**), so a car that stops discharging by itself isn't restarted over and
over. The limits always stay between 20% and 99%.

## Everyday use

- **Change the limits:** the **Schedule** tab. **Default limits** are used
  most of the time, and the car's battery capacity is set there too. Add schedule entries to change them for a while, every
  week (e.g. Tue and Thu 23:00–08:00, high 80% so the car's full for the
  commute) or once. **Skip next** leaves out one week's entry.
- **Quick hold** (Schedule tab): **No discharging**, **Discharge only**, **Hold here**, or
  **Keep at least** a %, for a few hours or until a time.
- **Start / Stop by hand:** the Overview's Virtual battery card.
- **Get told:** set a **Notify service** (Settings), e.g.
  `notify.mobile_app_your_phone`, to hear about every start and stop, and
  about charger alarms.

## With Predbat

- **Charge signal** (Settings): pick e.g. `binary_sensor.predbat_charging`.
  While it's on, the car charges if there's room, and isn't stopped at the
  low limit.
- **The car as a battery:** the add-on posts the car between its limits as
  a virtual battery, `sensor.sigen_dc_charger_virtual_battery_soc`,
  `..._usable_energy` and `..._max_energy`, and the charge / discharge
  rates `sensor.sigen_dc_charger_battery_rate` (with the car's and the home
  battery's shares). They follow the scheduled limits, so Predbat sees a
  change straight away.

## Power flow card for your dashboards

The Overview's power flow diagram is also a dashboard card. The add-on
installs it for you: edit a dashboard, **Add card**, and pick **EVDC Power
Flow**, or in YAML:

```yaml
type: custom:evdc-power-flow-card
```

If the card isn't in the list, check **Diagnostics › Health**: Home
Assistant may need restarting once, or (for dashboards in YAML mode) the
resource `/local/evdc-power-flow-card.js` adding by hand.

## Troubleshooting

- **It doesn't do anything:** the Overview's status says why: e.g. not
  plugged in, not in V2X mode, no SoC from the charger yet, waiting out the
  restart wait, Watch only on, or a charger alarm.
- **An entity is flagged on the Settings tab:** it doesn't exist or isn't
  reporting: pick the right one.
- **The car's SoC shows "From before it was plugged in":** the charger
  only reports the SoC once it's running; it updates when it does.
- **Asking for help:** **Diagnostics › Debug** has the log and a
  diagnostics bundle to download. Attach it to a
  [GitHub issue](https://github.com/thewhale21/HA_Addons/issues).

[docs]: https://github.com/thewhale21/HA_Addons/blob/main/sigenergy_evdc_soc_range_manager/DOCS.md
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
