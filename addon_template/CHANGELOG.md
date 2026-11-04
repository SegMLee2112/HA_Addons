# Changelog

## 0.1.7 — Settings and Diagnostics as lists

- **Improvement:** the Settings and Diagnostics tabs are lists, as in OCPP
  Charge Proxy and the EVDC SoC Range Manager: each card is a row with an
  icon, its title and a one-line summary kept up to date, opened in place
  (which are open is remembered on each device). New add-ons start with
  it: add a card to a list in setupRows() in src/static/index.html.

## 0.1.6 — Each tab from the top

- **Change:** switching tab goes back to the top of the page, instead of
  staying as far down as you'd scrolled on the last one.

## 0.1.5 — The banner theme

- **New:** the page has the same look as the other add-ons: a coloured
  banner at the top of each tab (Overview: the status with the example
  values as tiles; Settings; Diagnostics › Health: **All good**, or orange
  or red when something needs attention), fading from `--primary` to
  `--hero2`.
- **New:** Settings › **Appearance**: **Colourful** (the banners) or
  **Basic** (plain cards), saved in each browser.
- **For new add-ons:** the banner classes (`.bnr`, `.top`, `.ttl`,
  `.main`, `.lead`, `.ring`, `.hb`, `.side`, `.stats`/`.stat`) and
  `.masonry` (cards side by side without gaps) are ready to reuse.

## 0.1.4 — Issue triage with any AI provider

- **Docs:** GitHub Models was retired on 30 July 2026, so issue triage now
  works with any OpenAI-compatible API: set the `TRIAGE_API_KEY` secret and
  the `TRIAGE_API_URL` and `TRIAGE_MODEL` variables. Until they're set it
  skips, without failing.

## 0.1.3 — README as a how-to, and issue triage

- **Docs:** the README is laid out as a how-to for users (what you need,
  install, set it up, troubleshooting), ready to fill in for a new add-on:
  the issue triage reads it.
- **Docs:** DOCS.md says how issues are triaged (the repository's
  `.github/workflows/issue-triage.yaml`, which covers every add-on in the
  repository, a new one too) and how to take it to another repository.

## 0.1.2 — Passes the add-on linter

- **Fix:** `panel_icon` is no longer set to its default (`mdi:puzzle`),
  which the add-on linter rejects. It's left as a comment in `config.yaml`
  for setting your own add-on's sidebar icon.

## 0.1.1 — Log level applies straight away

- **Fix:** a log level set through the API's settings (or anything other
  than the page's log level buttons) was saved but not used until a
  restart; it now applies at once.

## 0.1.0 — First version

- **Feature:** a working add-on to start from: a web page in the Home
  Assistant sidebar (Overview, Settings, Diagnostics), a live link to
  Home Assistant with an entity picker, its own sensors posted to Home
  Assistant, saved settings, a log level, recent log lines, a diagnostics
  bundle and a restart button.
