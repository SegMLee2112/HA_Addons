# Predbat Dashboard

A clean, easy-to-read dashboard for
[Predbat](https://springfall2008.github.io/batpred/), made of cards you
choose. Predbat publishes a lot; this shows just what you pick.

## Using it

1. Have Predbat running (as an add-on or under AppDaemon).
2. Install this add-on, start it and open it from the sidebar. It finds
   Predbat's entities itself (`predbat.status`, `select.predbat_mode`...).
   If you changed Predbat's `prefix` in apps.yaml, set the same prefix on
   the **Settings** tab.
3. A starter set of cards is on the **Dashboard**. Press **Edit dashboard**
   to change it.

## Arranging the dashboard

In **Edit dashboard**:

- **Add a card:** drag it from the list onto the dashboard, where you want
  it (a blue line shows where it'll land), or tap it to add it at the end.
- **Move a card:** drag it by its ⠿ handle (or anywhere on it), or use ◀ ▶.
- **Rename a card:** type a title in the box at its top (empty: its own
  name).
- **Remove a card:** ✕.
- **Reset to the default:** puts back the starter set (press twice).

Press **Done** when you've finished. Changes are saved as you make them, in
the add-on, so every browser and phone shows the same dashboard. Chips take
one cell (Energy flow two); charts take half the width on a computer (the whole width on a
phone), the plan table the whole width.

## The cards

| Card | Shows |
| --- | --- |
| Battery | Its charge (%, and a battery that fills up), kWh, charging or discharging, and how long it lasts |
| Energy flow | Solar, the grid, the battery and the house, with the power moving between them (two cells wide) |
| Rates now | The import and export rate now, and the next 12 hours' import rates |
| Predbat status | What Predbat is doing, its mode and version (red on an error) |
| Grid carbon | The grid's carbon intensity now (low under 100 g/kWh, high over 200), and today's total |
| Next charge / Next export | When Predbat next charges or exports the battery (and how long until then), and to what |
| Next cheap rate / Next high export rate | The next low import / high export rate slot; all four show where it falls in the next 24 hours |
| Battery at the end of the plan | The battery's charge when the plan ends |
| Car charging | The car's next planned charge |
| Cost today / Yesterday / Savings | The cost so far today (import, export, per kWh), yesterday's and what Predbat saved |
| Energy today | Imported, exported, house and solar kWh |
| Solar forecast | Today (and what's still to come), tomorrow, and the next 12 hours |
| Battery plan (chart) | The battery's charge over the plan; charging shaded, exporting hatched |
| Rates (chart) | Import (bars) and export (dashed line) rates per half hour |
| Solar and house (chart) | Forecast solar and house use per half hour |
| Plan (table) | Predbat's plan, slots doing the same thing grouped |
| Predbat mode | Switch Predbat's mode |
| Force charge / Force export / Freeze charge / Freeze export | Predbat's manual overrides (below) |
| Any entity | Any Home Assistant entity's state (pick it in Edit dashboard) |

Each kind of figure keeps one colour: the grid and importing blue,
exporting orange, the battery green-blue, solar yellow, the house violet and
money green (a red battery is nearly empty).

Hover over (or tap) a chart for each half hour's figures. The **Plan** tab
has the battery plan and the plan table, grouped or every half hour.

## Controls

- **Predbat mode:** Monitor, Control SoC only, Control charge or Control
  charge & discharge (Predbat's `select.predbat_mode`).
- **Force charge / export, freeze charge / export:** Predbat's manual
  override selects (`select.predbat_manual_charge` and so on). **Now**
  turns it on for the half hour now and **Stop** takes that slot off;
  **+ Slot…** adds any slot in the next day; ✕ on a slot takes it off;
  **Clear all** turns them all off. Predbat plans around them on its next
  run (within a few minutes).

## How it works

The add-on follows Predbat's entities live (and the entities of any
"Any entity" cards), and looks for new ones every 5 minutes. The plan comes
from `predbat.plan_html`'s `raw` attribute. Money is shown in pounds (or
Predbat's currency symbols), rates in pence. Power: Predbat's battery power
is positive when discharging, its grid power positive when exporting.

## Sensors

| Sensor | What |
| --- | --- |
| `sensor.pb_dashboard_status` | What the add-on is doing |

## Where things are

The dashboard's cards are in `/data/layout.json` and the settings in
`/data/settings.json` (kept when the add-on is updated). **Diagnostics**
has the health, the log and a diagnostics bundle.

| What | Where |
| --- | --- |
| Reading Predbat's entities | `src/predbat.py` |
| The kinds of card and the layout | `src/layout.py` |
| Following Predbat, the controls | `src/runner.py` |
| Sensors posted to Home Assistant | `src/ha_entities.py` |
| Web page (the cards are drawn here) and API | `src/static/index.html`, `src/api.py` |
| Tests | `tests/` (`python -m pytest tests/`) |

Add a card: an entry in `CARDS` (`src/layout.py`) and a renderer in `R`
(chips) or `WIDE` (charts) in `src/static/index.html`, reading what it needs
from `data()` in `src/predbat.py`.

## Releasing a change

1. Make the change, and bump `version` in `config.yaml`.
2. Add an entry at the top of `CHANGELOG.md`: `## X.Y.Z — Title`, then
   bullets starting **Feature:**, **Improvement:**, **Fix:**, **Change:** or
   **Docs:**.
3. Commit, tag it `predbat_dashboard-X.Y.Z` (the add-on's folder name and
   the version, as other add-ons share this repository), and push the
   branch and the tag. The Builder workflow builds and publishes the image;
   Home Assistant then offers the update.
