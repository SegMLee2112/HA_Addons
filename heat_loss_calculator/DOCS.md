# Heat Loss Calculator

Works out your home's heat loss from what your gas boiler actually uses:
how many watts it takes to keep your home each degree warmer than outside
(**W/K**), and so the heat loss at your **design temperatures** (kW), the
figure a heat pump survey quotes. Measured from your own home, it's a
check on the surveys, which work room by room from standard figures and
usually come out high.

## How it works

1. **Each day** (midnight to midnight), once it's over, is read from Home
   Assistant's history:
   - **Gas:** the changes in your gas sensor. It can start again at
     midnight, as Octopus Energy's *current accumulative consumption* does:
     a drop is taken as a new day starting.
   - **Temperatures:** the day's average outside, inside and flow
     temperatures, each reading counting for as long as it held. **Inside**
     is your floors' thermostats averaged by floor area, so a bigger floor
     counts for more.
2. **Gas to heat:** each half hour's gas times the boiler's efficiency at
   that flow temperature. A condensing boiler is about 97% efficient at
   35 °C flow, falling to about 87% at 70 °C (its return water is warmer,
   so less of its exhaust condenses). Or set an efficiency you know.
3. **The fit:** a straight line through each day's heat against how much
   warmer it was inside than out (ΔT). Its slope ÷ 24 hours is the heat
   loss in W/K.
4. **Hot water** (on a combi, or a cylinder heated by the boiler) is about
   the same every day, so it doesn't change the slope: it's where the line
   crosses ΔT 0 (shown as *hot water and cooking*), less the free heat from
   the sun, people and appliances.
5. **Design heat loss:** the heat loss × (design inside − design outside),
   e.g. × (21 − (−3)) = × 24 K.

**±** is how sure the fit is (95%). It narrows with more days, and with
days at more different temperatures: a cold snap helps most.

Each day is read once and kept by the add-on (in its own storage), so it
isn't limited to the 10 days of history Home Assistant keeps by default.
The half hours of each day are kept for 45 days (for the day chart), then
just its totals.

## Setting it up

Everything is on the **Settings** tab and saved as you change it.

| Setting | What to put |
| --- | --- |
| **Gas** | An accumulating gas sensor in kWh. Octopus Energy's *current accumulative consumption (kWh)* is found for you. The *previous accumulative consumption* sensor works too, a day later |
| **Outside temperature** | A temperature sensor outside (out of the sun), or a weather entity |
| **Flow temperature** | **I set it:** the flow temperature you set on the boiler (37.5 °C to start). **A sensor:** the boiler's flow temperature sensor; each half hour then uses its own |
| **Floors** | For each floor: a name, its thermostat (a climate entity's current temperature, or a temperature sensor) and its floor area (m²) |
| **Design temperatures** | Inside (21 °C) and outside (−3 °C): use your surveys' figures, so they compare. **Floor area:** empty adds up the floors' |
| **Your boiler** | Efficiency from the flow temperature, or a fixed one |
| **Your heat pump surveys** | Each survey's heat loss (kW at the design temperatures), shown next to the measured one |
| **Smallest ΔT** | Milder days than this are left out (5 °C): the heating's barely on, so they say little |
| **Days to use** | The most recent days the fit uses (60) |

Changing the gas, outside or flow sensor, the floors or the efficiency
reads again the days Home Assistant still has; older days keep what they
were read with. **Days › Read the days again** does it by hand.

## The tabs

- **Overview:** the heat loss and design heat loss with how sure they
  are, the hot water baseline, the boiler's efficiency and how well the
  line fits; the temperatures now (each floor's too); heat against ΔT for
  each day with the line through them (click a dot to see that day); your
  surveys next to the measured heat loss; and a day half hour by half hour
  (the gas as bars, outside, inside and flow as lines).
- **Days:** each day's temperatures (each floor's too), gas, efficiency
  and heat, and whether it's used. **Click a day to leave it out** (you
  were away, a party, the oven on all day), and again to put it back.
- **Settings:** above.
- **Diagnostics:** health, the log, and a diagnostics bundle to download.

## Its Home Assistant entities

The add-on posts these itself; they're unavailable while it's stopped.

| Entity | |
| --- | --- |
| `sensor.heat_loss_calculator_heat_loss` | Heat loss (W/K). Attributes: days used, R², ±, the hot water baseline, ΔT range, W/K per m² |
| `sensor.heat_loss_calculator_design_heat_loss` | At the design temperatures (kW). Attributes: ±, the design temperatures, W/m², floor area |
| `sensor.heat_loss_calculator_heat_yesterday` | The heat the boiler put in on the latest whole day (kWh), with its gas and temperatures |
| `sensor.heat_loss_calculator_status` | Working, Collecting days or Needs setting up |

They read *unknown* until there are enough days: at least 5 whole days
over the smallest ΔT, spanning at least 3 °C.

## Good to know

- **A day is left out** when its readings have a gap of over 3 hours,
  start or stop more than 90 minutes from midnight, or a temperature is
  missing for more than a fifth of the day. The Days tab says which.
- **Other heat** (a wood burner, an electric heater, lots of sun) isn't
  counted, so it makes the house look better insulated than it is. Leave
  those days out.
- **The house warming up or cooling down** (e.g. a setback overnight) evens
  out over a day, but the first day after you change the thermostats can
  be off: leave it out.
- **Gas in m³:** use a sensor in kWh (the Octopus Energy integration gives
  both).
