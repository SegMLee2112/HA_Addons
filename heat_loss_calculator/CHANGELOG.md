# Changelog

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
