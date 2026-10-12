# Matt's Home Assistant add-ons

[![Open your Home Assistant instance and show the add add-on repository dialog with this repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fthewhale21%2FHA_Addons)

To install, add this repository in Home Assistant (**Settings › Add-ons ›
Add-on store › ⋮ › Repositories**) with the URL
`https://github.com/thewhale21/HA_Addons`, then pick an add-on from the
store.

## Add-ons

| Add-on | What it does |
| --- | --- |
| [OCPP Charge Proxy](ocpp_charge_proxy/) | A virtual OCPP 1.6 chargepoint for smart tariff suppliers (Octopus, EDF, E.ON Next), with a schedule, auto plug-in and more. |
| [SigEnergy EVDC SoC Range Manager](sigenergy_evdc_soc_range_manager/) | Keeps an EV's state of charge between a high and a low limit in V2X mode by starting and stopping a Sigenergy DC charger. |
| [Stellantis EV Stats](stellantis_ev_stats/) | Real-world range, efficiency by temperature and battery health for a Stellantis EV, from the Stellantis Vehicles integration. |
| [Predbat Dashboard](predbat_dashboard/) | A clean, colourful dashboard for Predbat that you arrange yourself: drag the cards you want onto it, with a few controls. |
| [Add-on Template](addon_template/) | A working add-on to start new ones from: web page, Home Assistant link, its own sensors, settings and diagnostics. |

## Starting a new add-on

```
python scripts/new_addon.py my_addon "My Add-on" "What it does"
```

This copies `addon_template/` to `my_addon/` with everything renamed
(slug, name, entity IDs, image, service) at version 0.1.0. Then replace its
icons, add it to the table above, and put its work in `my_addon/src/` (its
`DOCS.md` says where everything lives).

## Layout and releases

- One folder per add-on, each with its own `config.yaml` (version),
  `CHANGELOG.md`, `DOCS.md` and `tests/`.
- `.github/workflows/`: **Builder** builds and publishes the image of any
  add-on whose files changed (on a push to `main`; pull requests are only
  test builds), **Lint** checks every add-on's configuration, **Tests**
  runs each add-on's tests.
- To release: bump the add-on's `version`, add a changelog entry
  (`## X.Y.Z — Title` and **Feature:** / **Improvement:** / **Fix:** /
  **Change:** / **Docs:** bullets), commit, tag `<folder>-X.Y.Z` (e.g.
  `ocpp_charge_proxy-2.39.2`) and push the branch and the tag.
