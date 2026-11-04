# Changelog

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
