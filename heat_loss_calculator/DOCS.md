# Heat Loss Calculator

Works out your home's heat loss from what your gas boiler actually uses:
how many watts it takes to keep your home each degree warmer than outside
(**W/K**), and so the heat loss at your **design temperatures** (kW), the
figure a heat pump survey quotes. Measured from your own home, it's a
check on the surveys, which work room by room from standard figures and
usually come out high.

## How it works

1. **Each day** (midnight to midnight) is read from Home Assistant's
   history once it's over, and **today so far** is read on every refresh
   (every 30 minutes):
   - **Gas:** each half hour's gas. Octopus Energy's accumulative
     consumption sensors list every half hour, which the add-on takes
     whenever it arrives (Octopus's data comes in batches, often hours
     later), and the *previous accumulative consumption* sensor fills in
     any half hours the current one missed. A day is used once all its
     half hours are in. With another gas sensor, the changes in its
     reading are used; it can start again at midnight (a drop is taken as
     a new day starting).
   - **Temperatures:** the day's average outside, inside and flow
     temperatures, each reading counting for as long as it held. **Inside**
     is your floors' thermostats averaged by floor area, so a bigger floor
     counts for more.
2. **Gas to heat:** each half hour's gas times the boiler's efficiency at
   that flow temperature. A condensing boiler is about 97% efficient at
   35 °C flow, falling to about 87% at 70 °C (its return water is warmer,
   so less of its exhaust condenses). Or set an efficiency you know.
3. **Other heating** (**Settings › Other heating**), e.g. air con units or
   electric heaters: the electricity each uses that day (from its energy
   sensor, kWh, which may start again at midnight) × its COP (the heat out
   for each kWh in: about 3 to 4 for air con heating, 1 for a plain
   electric heater) is added to the boiler's heat. Leave it out and the
   heat loss reads low.
4. **The fit:** a straight line through each day's heat against how much
   warmer it was inside than out (ΔT). Its slope ÷ 24 hours is the heat
   loss in W/K.
   **Today so far** joins the fit: its heat is scaled up to a whole day
   (the early hours are treated like the rest of the day) and it pulls on
   the line by the part of the day that has passed — half a day counts as
   half a day — so a cold morning starts helping straight away without a
   few hours counting as a whole day. It joins once at least 10% of the
   day has passed and its data is sound; its ΔT is the average over the
   part of the day read. A whole day replaces it once it's over.
5. **Hot water** (showers, taps) isn't heat for the house
   (**Settings › Hot water**):
   - **Leave it in** (the default, and best for most): it's about the same
     every day, so it doesn't change the heat loss (the slope), only where
     the line crosses ΔT 0, shown as *hot water and cooking*.
   - **When the heating's off** (on a combi, only if your thermostats
     follow the boiler closely): gas burnt in a half hour when
     none of your thermostats was calling for heat is hot water. Calling:
     its `hvac_action` is *heating*, or, for a thermostat without one (most
     don't have it), it's on heat and more than 0.3 °C under what it's set
     to. Showers while the heating's on can't be
     told apart; they're about the same every day, so they don't change
     the slope, only where the line crosses ΔT 0. If your thermostats
     report neither, it's left in.
   - **A set amount** a day (kWh of gas), e.g. about 1.5 kWh a shower.
6. **Design heat loss:** the heat loss × (design inside − design outside),
   e.g. × (21 − (−3)) = × 24 K.

**From one day:** until there are 5 days at different temperatures for
the line, the heat loss is an **estimate**: the heat over the ΔT of the
days there are (a line through 0, as if hot water and the free heat cancel
out). It's labelled as one, with ± once there are two days, and becomes
the full fit by itself.

**R²** (the Fit tile) is how closely the days sit on the line, from 0 (no
pattern) to 1 (every day exactly on it): over 0.85 the heat loss is well
measured. Days well off the line usually had something else going on
(other heat, away, a big change to the thermostats): leave them out on the
Days tab.

**±** is how sure the fit is (95%). It narrows with more days, and with
days at more different temperatures: a cold snap helps most.

Each day is read once and kept by the add-on (in its own storage), so it
isn't limited to the 10 days of history Home Assistant keeps by default;
today's totals are refreshed each time. The half hours of each day are
kept for 45 days (for the day chart), then just its totals.

## Setting it up

Everything is on the **Settings** tab and saved as you change it.

| Setting | What to put |
| --- | --- |
| **Gas** | An accumulating gas sensor in kWh. Octopus Energy's *current accumulative consumption (kWh)* is found for you. The *previous accumulative consumption* sensor works too, a day later |
| **Outside temperature** | A temperature sensor outside (out of the sun), or a weather entity |
| **Flow temperature** | **I set it:** the flow temperature you set on the boiler (37.5 °C to start). **A sensor:** the boiler's flow temperature sensor; each half hour then uses its own |
| **Floors** | For each floor: a name, its thermostat (a climate entity's current temperature, or a temperature sensor) and its floor area (m²) |
| **Design temperatures** | Inside (21 °C) and outside (−3 °C): use your surveys' figures, so they compare. **Floor area:** empty adds up the floors' |
| **Other heating** | Each air con unit or heater: a name, its energy sensor (kWh) and its COP (3.5 to start) |
| **Your boiler** | Efficiency from the flow temperature, or a fixed one |
| **Weather compensation** | Two points on your weather compensation curve: the outside temperature and flow temperature for a **colder** day and a **milder** day. The add-on works out the flow temperature along the line between them, shows the target flow at the **current outside temperature** and at **12 °C outside** (Octopus's low point), and draws the curve. Leave all four empty to hide it |
| **Your heat pump surveys** | Each survey's heat loss (kW at the design temperatures), shown next to the measured one |
| **Smallest ΔT** | Milder days than this are left out (5 °C): the heating's barely on, so they say little |
| **Hot water** | Left in (the default), a set amount a day, or when the heating's off (from your thermostats) |

Changing the gas, outside or flow sensor, the floors or the efficiency
reads again the days Home Assistant still has; older days keep what they
were read with. **Days › Read the days again** does it by hand.

## The tabs

- **Overview:** **Days used** (the last 7, 14, 30 or 60 days, all of
  them, or between two dates); the heat loss and design heat loss with how sure they
  are, the hot water baseline, the boiler's efficiency and how well the
  line fits; the temperatures now (each floor's too, and the **weather
  compensation** target flow at the current outside temperature and at
  12 °C when set); heat against ΔT for
  each day with the line through them (click a dot to see that day); your
  surveys next to the measured heat loss, with the assumptions behind it
  (design temperatures, the days and temperatures it's measured over,
  efficiency, hot water, floor area); and a day half hour by half hour
  (the gas as bars, outside, inside and flow as lines).
- **Days:** each day's temperatures (each floor's too), gas, efficiency
  and heat, and whether it's used. Today shows **Used so far**: the heat
  measured up to now, and the whole-day figure the fit scales it to.
  **Click a day to leave it out** (you
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

They read *unknown* until there's a whole day over the smallest ΔT; until
there are 5, spanning at least 3 °C, they're an estimate (the heat loss
sensor's `method` attribute: `estimate` or `fit`).

## Good to know

- **A day is left out** when any of its half hours of gas are missing
  (it's tried again for 3 days, as Octopus's data can come late), or, with
  a gas sensor that has no half hours, its readings have a gap of over 3 hours,
  start or stop more than 90 minutes from midnight, or a temperature is
  missing for more than a fifth of the day. The Days tab says which. A
  half hour missing from Octopus's list can be filled from the gas
  readings when two nearby readings bracket it; otherwise it's named.
- **Today so far** is still in the fit unless its data has a problem or
  less than 10% of the day has passed; it never makes a day incomplete
  for the days around it.
- **Other heat** not added under Other heating (a wood burner, lots of
  sun) isn't counted, so it makes the house look better insulated than it
  is. Leave those days out.
- **The house warming up or cooling down** (e.g. a setback overnight) evens
  out over a day, but the first day after you change the thermostats can
  be off: leave it out.
- **Gas in m³:** use a sensor in kWh (the Octopus Energy integration gives
  both).

## Fit quality and validation

**Overview › Heat pump sizing** helps you check the result against your surveys:

- **Fit quality:** labelled **Strong**, **OK**, **Weak** or **Estimate**, with R², how many days were used, the measured ΔT range, and whether the **design ΔT** is **extrapolated** beyond your coldest measured days (a common reason measured and survey figures differ). Warnings point out what to improve (more cold days, high scatter, large extrapolation). Days are counted by how much of each is covered, so today so far counts as the part of the day that has passed.
- **Measured range and surveys:** the measured heat loss is shown as a value and a **± range** (from the fit). Each survey is marked **within the measured range** or how far above/below it sits.
- **Measured validation (per day):** open **Measured validation (per day)** to see, for each fitted day, the **implied** heat loss from that day’s space heat and ΔT (W/K and kW at the design temperatures), the range across days, and the **residual** (actual heat − the fit’s prediction). This shows whether the house consistently runs above/below the fitted line and how the day-by-day implied values compare to surveys. A **so far** day uses the whole-day heat the fit uses (the actual heat measured so far is in its tooltip). DHW handling and any **Other heating** affect these figures, so they are shown per day.

For the most representative inside temperature (and a fair survey comparison), add all your temperature points as **Floors** (Settings › Floors) with their areas — for example a ground-floor thermostat and first/second-floor temperature sensors — so the inside temperature is area-weighted across the whole home.
