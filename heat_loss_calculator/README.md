# Home Assistant Add-on: Heat Loss Calculator

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

Works out your home's heat loss from what your gas boiler actually uses,
for sizing a heat pump: the watts it takes to keep your home each degree
warmer than outside (W/K), and the heat loss at your design temperatures
(kW, as a heat pump survey quotes it), side by side with your surveys.

[Full documentation][docs] (also the add-on's **Documentation** tab).

---

## What you need

- Home Assistant OS or Supervised (add-ons need the Supervisor).
- A gas sensor in kWh that adds up through the day, e.g. the
  [Octopus Energy integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy)'s
  *current accumulative consumption* (with an Octopus Home Mini).
- An outside temperature (a sensor or a weather entity).
- A thermostat or temperature sensor on each floor.
- The boiler's flow temperature: a sensor, or the setting you use.

## Install

1. Add this repository to Home Assistant:
   **Settings › Add-ons › Add-on store › ⋮ › Repositories**, and add
   `https://github.com/thewhale21/HA_Addons`.
2. Find **Heat Loss Calculator** in the store and click **Install**.
3. **Start** it, turn on **Show in sidebar**, and open it.

## Set it up

All on the add-on's **Settings** tab, saved as you go (it has no options):

1. **Gas:** found for you if you have the Octopus Energy integration.
2. **Outside temperature.**
3. **Flow temperature:** the one you set on the boiler, or a sensor.
4. **Floors:** each floor's thermostat and floor area.
5. **Your heat pump surveys** (optional): each one's heat loss in kW.

It reads the days Home Assistant still has (about 10), so the first result
comes within a minute or so, and firms up as colder days come in.

## Its Home Assistant entities

| Entity | |
| --- | --- |
| `sensor.heat_loss_calculator_heat_loss` | Heat loss (W/K), with how sure it is as attributes |
| `sensor.heat_loss_calculator_design_heat_loss` | At the design temperatures (kW) |
| `sensor.heat_loss_calculator_heat_yesterday` | The heat the boiler put in yesterday (kWh) |
| `sensor.heat_loss_calculator_status` | What it's doing |

## Troubleshooting

- **The dot by the title is amber or red:** **Diagnostics › Health** says
  why.
- **Asking for help:** **Diagnostics › Debug** has the log and a
  diagnostics bundle to download. Attach it to a
  [GitHub issue](https://github.com/thewhale21/HA_Addons/issues).

[docs]: https://github.com/thewhale21/HA_Addons/blob/main/heat_loss_calculator/DOCS.md
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
