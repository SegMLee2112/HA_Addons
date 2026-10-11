# Changelog

## 0.3.1 — Range left for each kind of driving

- **Feature:** the Efficiency now card shows the range left at the SoC now,
  overall and for town, mixed, faster roads and motorway driving, from the
  efficiency expected at the temperature now. Sensors for each:
  `sensor.stellantis_ev_stats_range_left_town`, `..._mixed`,
  `..._faster_roads` and `..._motorway` (miles).

## 0.3.0 — Efficiency now, from the weather

- **Feature:** an "Efficiency now" card on the Overview: the efficiency to
  expect at the temperature now, overall and for each kind of driving
  (town, mixed, faster roads, motorway), with the range on a full battery
  for each. Each comes from your trips at that temperature, or the nearest
  temperature with trips of that kind (shown when it is).
- **Feature:** sensors for them: `sensor.stellantis_ev_stats_efficiency_now`
  (overall) and `..._efficiency_now_town`, `..._mixed`, `..._faster_roads`
  and `..._motorway`.
- **Change:** the figures for now (real range, range left and the
  efficiency now) use the weather entity's temperature when there is one,
  rather than the car's (which reads high in a garage or after sitting in
  the sun); the car's is used without one.

## 0.2.3 — Chart hover

- **Fix:** on the range and speed charts, a bar lit up (and its details
  showed) when the pointer was in the empty space above it, and went out
  when it was on the bar. Now only the bar (and the car's estimate on it)
  does. The dots on the other charts no longer flicker either.

## 0.2.2 — Settings fix

- **Fix:** changing a setting gave "HTTP 500", and Home Assistant's history
  was never read, the commute forecast never fetched and the figures only
  updated when the car sent something: the web server took over the name
  of the part that does the statistics when the add-on started. Live
  readings and trips were still recorded.

## 0.2.1 — Lint fix

- **Fix:** removed the empty `options` and `schema` from `config.yaml`
  (the add-on has no options; the linter rejects defaults).

## 0.2.0 — Commute, speed and drain

- **Feature:** commute planner. Set the distance, the time you leave, the
  days and the SoC to arrive with (Settings), and the Overview shows the
  charge to have for the next one, from the forecast temperature when you
  leave (a weather entity; Home Assistant's own is found by itself) and your
  efficiency then: from commute-length trips at that temperature when
  there are 3 or more, otherwise from all trips at it. Also
  `sensor.stellantis_ev_stats_commute_charge`, e.g. for a charge limit.
- **Feature:** efficiency by average speed (town, mixed, faster roads,
  motorway), and a table of temperature against speed, so the temperature's
  effect can be seen at the same kind of driving (Temperature tab).
- **Feature:** drain while parked: the SoC lost per day while parked
  unplugged between trips (6 hours or more), overall and by temperature, in
  %, kWh and miles (Temperature tab, the Overview, and
  `sensor.stellantis_ev_stats_drain`). A Battery plugged picker (found by
  itself) keeps time on a charger or V2X out of it.

## 0.1.0 — First version

- **Feature:** records every trip from the Stellantis Vehicles
  integration's Last trip sensor (distance, energy, time, speed) with the
  outside temperature during it and the SoC before and after, the car's
  range estimate with the SoC and temperature, its battery health (SoH
  capacity and resistance) when it changes, and the usable capacity
  (Battery residual ÷ SoC). Its sensors are found by themselves.
- **Feature:** Overview: the real range at 100% at today's temperature (and
  what's left now), the efficiency over the last 30 days, battery health,
  and the real range against the car's estimate by temperature.
- **Feature:** Temperature tab: every trip's efficiency against the
  temperature, and a table by 5 °C band: trips, miles, mi/kWh, the real
  range (from the energy and from the SoC) and the car's estimate.
- **Feature:** Trips tab, and a Battery tab with the battery health and the
  measured usable capacity over time.
- **Feature:** the last 30 days of Home Assistant's history are read once,
  so there's something to see straight away.
- **Feature:** sensors: real range at 100%, real range left, the car's
  range at 100%, efficiency (30 days and at this temperature), usable
  capacity, battery health and trips recorded.
- **Feature:** settings for the shortest trip counted (2 miles) and the
  usable capacity (measured by default).
