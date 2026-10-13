# Changelog

## 0.3.0 — Card sizes, sections and a house for the energy flow

- **Feature:** make any card bigger or smaller (− and + in Edit
  dashboard): S one cell, M two, L three or XL the whole width. Figures,
  batteries and the small charts grow with the card; charts get taller.
- **Feature:** sections: a heading that groups the cards below it. Move a
  section (with its cards) with ▲ ▼ or by dragging it, and click its
  heading to fold its cards away. The starter dashboard has three: Now,
  The plan and Home.
- **Feature:** the Energy flow card is now a house: solar panels on the
  roof with the sun shining on them, the grid's pylon and wire, the battery
  inside, and the car on the drive when there is one (with its charge),
  with moving dots for where the power is going.
- **Feature:** a car charger power setting (Settings tab), for the car on
  the energy flow: Predbat doesn't publish the car's charging power.

## 0.2.0 — More graphical and colourful

- **Feature:** an Energy flow card (two cells wide) in place of Power now:
  solar, the grid, the battery and the house, with the power moving
  between them shown as moving dots.
- **Improvement:** each kind of figure has its own colour, used for its icon,
  its card's tint and its marks everywhere: the grid and importing blue,
  exporting orange, the battery green-blue, solar yellow, the house violet,
  money green.
- **Improvement:** small graphics on the chips: a battery that fills up
  (red when nearly empty, a bolt while charging), the next 12 hours' import
  rates and solar forecast, where the next charge / export / cheap rate
  falls in the next 24 hours (and how long until it starts), import against
  export for today's cost, today's energy as bars, and a low / moderate /
  high gauge for grid carbon.
- **Improvement:** the charts: the battery plan as a filled area, solar as a
  filled area, and the plan table with a battery bar on each row and
  coloured dots for what Predbat is doing.

## 0.1.0 — First release

- **Feature:** a dashboard of cards for Predbat that you arrange yourself:
  **Edit dashboard**, then drag cards from the list onto it (or tap one to
  add it at the end), drag them around (or use ◀ ▶), rename or remove them.
  Saved in the add-on, so every browser shows the same dashboard.
- **Feature:** chips for the battery, power now, rates now, Predbat's
  status, grid carbon, the next charge and export, the next cheap import
  and high export rates, the battery at the end of the plan, car charging,
  cost today and yesterday, savings, energy today and the solar forecast.
- **Feature:** charts of the battery plan, the rates and solar against the
  house, and the plan as a table (grouped, or every half hour), also on the
  Plan tab.
- **Feature:** controls: Predbat's mode, and force charge, force export,
  freeze charge and freeze export now or for any slot, and clearing them.
- **Feature:** an "Any entity" chip for any other Home Assistant entity.
