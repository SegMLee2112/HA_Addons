# Stellantis EV Stats

Real-world range, efficiency by temperature and battery health for a
Stellantis electric car, from the
[Stellantis Vehicles](https://github.com/andreadegiovine/homeassistant-stellantis-vehicles)
integration (Peugeot, Vauxhall/Opel, Citroën, DS, Fiat, Jeep…).

## Using it

1. Have the Stellantis Vehicles integration set up for the car.
2. Install the add-on, start it and open it from the sidebar. It finds the
   car's sensors itself (from its **Last trip** sensor); the **Settings**
   tab shows which, and you can pick others.
3. It reads the last 30 days of Home Assistant's history once (Home
   Assistant keeps 10 days unless you've changed it), then records as you
   drive. The more trips at different temperatures, the better the figures.

The integration fetches a trip when the car stops, with its distance and
the energy used. Some cars stop sending updates a few minutes after being
switched off: the integration's wake-up automations help.

## What it shows

- **Overview:** the real range on a full battery at today's temperature,
  and what's left at the current SoC; the efficiency over the last 30 days;
  the car's battery health; and the real range against the car's own
  estimate, by temperature.
- **Temperature:** each trip's efficiency (mi/kWh) against the outside
  temperature, and a table by 5 °C band: trips, miles, mi/kWh, the real
  range at 100% and the car's estimate.
- **Temperature** also has the efficiency by average speed, a table of
  temperature against speed, and the drain while parked.
- **Commute** (Overview): the charge to have for your next commute.
- **Trips:** the latest 300 trips: distance, time, average speed, kWh,
  mi/kWh, temperature and SoC.
- **Battery:** the car's battery health figures over time (capacity and
  resistance) and the usable capacity measured each week.

## How it's worked out

- **Efficiency:** miles ÷ kWh over trips of at least the **shortest trip
  counted** (2 miles; short trips are mostly warming up). Each band adds up
  its trips' miles and kWh, so long trips count for more.
- **Temperature of a trip:** the average of the car's outside temperature
  readings during it (or the last one before it ended). Pick another
  outdoor sensor on the Settings tab if you prefer: the car's can read high
  after it's been parked in the sun or a garage.
- **Usable capacity:** Battery residual (kWh in the battery) ÷ SoC, from
  readings at 20% or more (the median of the latest 30), so it follows the
  battery as it ages. Until there are some, the car's Battery capacity
  sensor; or set it yourself on the Settings tab.
- **Real range at 100%:** efficiency × usable capacity, for each band.
  **From the SoC** is a second figure that doesn't need the kWh: miles per
  1% used × 100, from trips that used 5% or more.
- **The car's estimate at 100%:** its range ÷ SoC, from readings at 30% or
  more, the median for each band.
- **Real range left:** the real range at 100% for today's temperature (the
  nearest band with trips) × the SoC.
- **The temperature now:** the weather entity's (Settings, Commute), else
  the car's outside temperature.
- **Efficiency now:** overall, your trips in the band for the temperature
  now; for each kind of driving, its trips in that band, or the nearest band
  that has some (the Overview says when it's another band).
- **Speed:** each trip's average speed puts it in a band: town (under 20
  mph), mixed (20–35), faster roads (35–50) and motorway (50+). Colder
  weather often comes with different driving, so compare a column of the
  temperature and speed table rather than the temperature bands alone.
- **Drain while parked:** for each spell of 6 hours or more parked between
  trips, the SoC from the first reading after the trip to the start of the
  next (the integration only sends a change, so an unchanged SoC is no
  drain). Spells where the car was plugged in (the Battery plugged sensor)
  or the SoC rose are left out. Per day, overall and by the temperature
  during the spell. It needs the car to report now and then while parked
  (the integration's wake-up automations), and takes a few weeks to settle
  as the SoC is only whole numbers.

## Commute planner

Set the commute on the Settings tab: the distance (one way), the time you
leave, the days, and the SoC to arrive with (your margin). For the next
commute it takes the forecast temperature when you leave (from the weather
entity, hourly if it has it, else the day's low before 11:00 or high after;
the temperature now if there's no forecast) and the efficiency to expect:
from your commute-length trips (within 15% of the distance) at that
temperature when there are 3 or more, otherwise from all trips at it.

Charge to = the kWh it needs ÷ the usable capacity + the SoC to arrive
with, rounded up. `sensor.stellantis_ev_stats_commute_charge` has it (and
the details as attributes), e.g. for your charge limit the night before.

## Sensors

The add-on posts these to Home Assistant (unavailable while it's stopped):

| Sensor | What |
| --- | --- |
| `sensor.stellantis_ev_stats_real_range_full` | Real range at 100% at today's temperature (mi) |
| `sensor.stellantis_ev_stats_real_range_left` | Real range left now (mi) |
| `sensor.stellantis_ev_stats_car_range_full` | The car's estimate at 100% now (mi) |
| `sensor.stellantis_ev_stats_efficiency` | Efficiency over the last 30 days (mi/kWh) |
| `sensor.stellantis_ev_stats_efficiency_now` | Efficiency to expect at the temperature now (mi/kWh) |
| `sensor.stellantis_ev_stats_efficiency_now_town` (`_mixed`, `_faster_roads`, `_motorway`) | ...for each kind of driving (mi/kWh; the range on a full battery as an attribute) |
| `sensor.stellantis_ev_stats_range_left_town` (`_mixed`, `_faster_roads`, `_motorway`) | The range left now for each kind of driving (mi) |
| `sensor.stellantis_ev_stats_usable_capacity` | Usable capacity (kWh) |
| `sensor.stellantis_ev_stats_battery_soh` | The car's battery health, capacity (%) |
| `sensor.stellantis_ev_stats_commute_charge` | The charge to have for the next commute (%) |
| `sensor.stellantis_ev_stats_drain` | Drain while parked unplugged (% a day) |
| `sensor.stellantis_ev_stats_trips` | Trips recorded |
| `sensor.stellantis_ev_stats_status` | What the add-on is doing |

## Where things are

Everything recorded is in `/data/evstats.json` (kept when the add-on is
updated), settings in `/data/settings.json` and the sensors picked in
`/data/sensors.json`. **Diagnostics** has the health, the log and a
diagnostics bundle.

| What | Where |
| --- | --- |
| Recording and the figures | `src/evstats.py` |
| Feeding it the readings (live and history) | `src/runner.py` |
| The car's sensors and finding them | `src/ha_link.py` (`ENTITY_KEYS`, `SUFFIXES`) |
| Sensors posted to Home Assistant | `src/ha_entities.py` |
| Web page and API | `src/static/index.html`, `src/api.py` |
| Tests | `tests/` (`python -m pytest tests/`) |

## Releasing a change

1. Make the change, and bump `version` in `config.yaml`.
2. Add an entry at the top of `CHANGELOG.md`: `## X.Y.Z — Title`, then
   bullets starting **Feature:**, **Improvement:**, **Fix:**, **Change:** or
   **Docs:**.
3. Commit, tag it `stellantis_ev_stats-X.Y.Z` (the add-on's folder name and
   the version, as other add-ons share this repository), and push the
   branch and the tag. The Builder workflow builds and publishes the image;
   Home Assistant then offers the update.
