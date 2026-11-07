# Changelog

## 0.5.1 — Weather compensation docs and labels

- **Docs:** how to set the weather compensation curve, and where its
  target flow (now and at 12 °C outside) is shown.
- **Improvement:** the **Colder point** and **Milder point** labels in
  Settings › Weather compensation are bold.

## 0.5.0 — Weather compensation curve

- **Feature:** Settings › Weather compensation: set the flow temperature
  at a colder and a milder outside temperature (two points), and the
  add-on works out the flow temperature along that line. It shows the
  target flow for the **current outside temperature** (Overview › Now) and
  the flow at **12 °C outside** (Octopus's low point), with a small curve
  chart in Settings. Leave all four values empty to hide it.

## 0.4.3 — What each figure means

- **Docs:** each figure at the top of the Overview (the heat loss, at
  design temperature, hot water or free heat, and boiler efficiency, as
  well as the fit) has a **What's this?** link explaining it, opened in
  place. The free heat one says why it swings a lot until there are days
  at quite different temperatures.
- **Change:** the design temperature line reads "to keep 21 °C inside when
  it's -3 °C out" rather than "21 °C in, -3 °C out".

## 0.4.2 — Settings and Diagnostics as lists

- **Improvement:** the Settings and Diagnostics tabs are lists, as in OCPP
  Charge Proxy and the EVDC SoC Range Manager: each card is a row with an
  icon, its title and a one-line summary kept up to date (e.g. "3 floors:
  Ground floor, First floor, Second floor · 120 m²"). Click a row to open
  it in place; which are open is remembered on each device.

## 0.4.1 — Other heating from lifetime totals

- **Fix:** an energy sensor that's a lifetime total could make the heat
  loss absurd (tens of thousands of W/K): when it dropped to 0 for a
  moment (a restart) and came back, the drop was taken as a reset at
  midnight and the whole total counted as one day's use. A jump faster
  than an air con unit or heater can use (15 kW; 60 kW for gas) is now
  left out as a glitch. Energy sensors in Wh or MWh are read in kWh. Days
  already read are read again.

## 0.4.0 — Other heating

- **Feature:** Settings › Other heating: air con units, electric heaters or
  anything else that heats your home, each with its energy sensor (kWh)
  and its COP (heat out for each kWh in: about 3 to 4 for air con heating,
  1 for a plain electric heater). Their heat is added to the boiler's
  before the fit, so the heat loss isn't read low. It shows in the day
  chart (stacked on the gas), the Days tab (Other heat) and the
  assumptions. Days already read are read again.
- **Improvement:** each floor and each other heating source has its name on
  its own line, so long names aren't cut off.

## 0.3.0 — An estimate from one day

- **Feature:** a heat loss from just one day. Until there are 5 days at
  different temperatures for the full fit, it's an estimate: the heat over
  the ΔT of the days there are, as if hot water and the free heat cancel
  out (with ± once there are two). It's labelled as an estimate on the
  Overview, in the assumptions, and in the status (Estimating), and
  switches to the full fit by itself. The heat loss sensor's method
  attribute says which.

## 0.2.4 — What's R²?

- **Docs:** the Fit tile on the Overview has a **What's R²?** link: how
  closely the days sit on the line (0 to 1, over 0.85 is good), and what
  to do when it's low. "How it's worked out" explains it too.

## 0.2.3 — Free heat, not negative hot water

- **Fix:** with hot water left in, the Overview could show a negative
  "Hot water and cooking". That figure is where the line crosses ΔT 0:
  hot water and cooking less the free heat from the sun, people and
  appliances. When the free heat is the bigger, it now says so ("Free
  heat, 3.2 kWh a day more ... than hot water uses"), and the assumptions
  too. The heat loss isn't affected.

## 0.2.2 — Hot water left in by default

- **Change:** Settings › Hot water starts at **Leave it in**, now first and
  recommended. Hot water is about the same every day, so it doesn't change
  the heat loss (the slope), only where the line crosses ΔT 0; working it
  out from the thermostats only works when they follow the boiler closely,
  and half-hourly gas can't split a shower from heating in the same half
  hour.
- **Fix:** the Overview's boiler efficiency counted the gas taken out as
  hot water, so it read too low (e.g. 52%).

## 0.2.1 — Hot water without hvac_action

- **Improvement:** thermostats that don't report hvac_action (when they're
  calling for heat), as many don't, no longer leave hot water in: a
  thermostat on heat and more than 0.3 °C under what it's set to is taken
  as calling for heat, so gas burnt while none is calling is hot water.
  Settings › Hot water and the assumptions say which way it was worked
  out. Days already read are read again.

## 0.2.0 — Days used, hot water and the assumptions

- **Feature:** choose the days the result uses on the Overview: the last
  7, 14, 30 or 60 days, all of them, or between two dates (either end can
  be left open), e.g. a cold spell, or only since new windows went in.
  Replaces Settings › Days to use.
- **Feature:** hot water (showers, taps) is taken out before the fit. On a
  combi, gas burnt while none of your thermostats is calling for heat
  (their hvac_action) is hot water; or set an amount a day, or leave it
  in (Settings › Hot water). It shows in the day chart and the Days tab,
  and the Overview has the average a day. Days already read are read
  again.
- **Feature:** Heat pump sizing lists the assumptions behind the measured
  figure: the design temperatures, the sum, the days and the outside and
  ΔT range it's measured over (and how far the line is carried on to the
  design temperature), the inside temperature, boiler efficiency and
  flow temperature, hot water, the days left out, the floor area, and
  what isn't counted.

## 0.1.3 — Gas from each half hour

- **Fix:** with Octopus Energy's gas sensors, whose data arrives in
  batches a few times a day, nearly every day was left out ("A gap in the
  gas readings") and its gas was drawn where each batch arrived. Each half
  hour's gas now comes from the sensor's own list of half hours, whenever
  it arrives, and the previous accumulative consumption sensor fills in
  any the current one missed (e.g. the last ones before midnight). A day
  is used once all its half hours are in. Days already read are read
  again.
- **Fix:** a floor's thermostat name could run into the picker's arrow.

## 0.1.2 — Add-on linter fix

- **Fix:** the add-on's config left out the empty options and schema, as
  the add-on linter asks (they're the defaults).

## 0.1.1 — Its own icon

- **Improvement:** the add-on has its own icon and logo (a house with heat
  escaping and a thermometer inside), in place of the template's.

## 0.1.0 — First version

- **Feature:** works out your home's heat loss (W/K) and the heat loss at
  your design temperatures (kW) from your gas use, outside temperature,
  flow temperature and each floor's thermostat, read day by day from Home
  Assistant's history and kept.
- **Feature:** the Overview shows the result with how sure it is, the
  temperatures now, heat against ΔT for each day with the fitted line,
  your heat pump surveys next to the measured heat loss, and any day half
  hour by half hour.
- **Feature:** a Days tab, where you can leave days out of the fit.
- **Feature:** sensors in Home Assistant for the heat loss, the design
  heat loss, yesterday's heat and the status.
