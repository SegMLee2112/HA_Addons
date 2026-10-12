"""Reading Predbat's entities (src/predbat.py), the dashboard's layout (src/layout.py)
and the controls (src/runner.py), without Home Assistant."""

import asyncio
import datetime

import pytest

from src.app_settings import AppSettings
from src.layout import CARDS, DEFAULT, Layout, validate_layout
from src.predbat import data, entity_ids, is_predbat, manual_options, num, plan_rows, slot_option
from src.runner import Runner
from src.shared_state import SharedState

TZ = datetime.timezone(datetime.timedelta(hours=1))
NOW = datetime.datetime(2026, 10, 12, 21, 10, tzinfo=TZ)  # a Monday


def _st(state, **attrs):
    return {"state": state, "attributes": attrs}


def _plan():
    rows = []
    for i in range(6):
        t = datetime.datetime(2026, 10, 12, 20, 30, tzinfo=TZ) + datetime.timedelta(minutes=30 * i)
        rows.append({"time": t.strftime("%Y-%m-%dT%H:%M:%S%z"), "state": ["", "Chrg", "Chrg", "Exp", "FrzChrg", ""][i],
                     "state_text": "Chrg&#8599;", "soc_percent": 40 + i * 10, "import_rate": 7.5 + i,
                     "export_rate": 15.0, "pv_forecast": 0, "load_forecast": 0.4, "total_cost": 1.23 + i})
    return {"rows": rows, "soc_max": 10.0, "currency_symbols": ["£", "p"]}


def _states():
    return {
        "predbat.status": _st("Demand", version="v8.30", error=False, last_updated="2026-10-12 21:05:00"),
        "select.predbat_mode": _st("Control charge & discharge", options=[
            "Monitor", "Control SoC only", "Control charge", "Control charge & discharge"]),
        "predbat.soc_kw_h0": _st("5.0"),
        "sensor.predbat_inverter_config": _st("on", soc_max=10.0, reserve=0.4),
        "predbat.best_battery_hours_left": _st("6.25"),
        "predbat.battery_power": _st("-1.5"),
        "predbat.cost_today": _st("123.4"),
        "predbat.rates": _st("24.5", min=7.5, max=30.1),
        "predbat.rates_export": _st("15"),
        "predbat.low_rate_start": _st("2026-10-12T23:30:00+0100"),
        "predbat.low_rate_end": _st("2026-10-13T05:30:00+0100"),
        "predbat.low_rate_cost": _st("7.5"),
        "predbat.best_charge_start": _st("2026-10-12T21:00:00+0100"),
        "predbat.best_charge_end": _st("2026-10-12T22:00:00+0100"),
        "predbat.best_charge_limit": _st("60"),
        "predbat.best_export_start": _st("undefined"),
        "sensor.predbat_pv_today": _st("12.3", total=12.0, remaining=0.0),
        "predbat.plan_html": _st("", raw=_plan()),
        "binary_sensor.predbat_charging": _st("on"),
        "select.predbat_manual_charge": _st("+Mon 22:00", options=[
            "off", "Mon 21:00", "Mon 21:30", "[Mon 22:00]", "Mon 22:30", "+Mon 22:00"]),
        "sensor.living_room": _st("21.5", friendly_name="Living room", unit_of_measurement="°C"),
    }


def test_numbers_and_names():
    assert num("12.5 kWh") == 12.5 and num(3) == 3.0 and num("x") is None and num(True) is None
    assert entity_ids("pb", "mode")[:4] == ["pb.mode", "sensor.pb_mode", "binary_sensor.pb_mode", "select.pb_mode"]
    assert is_predbat("predbat.status", "predbat") and is_predbat("select.predbat_mode", "predbat")
    assert not is_predbat("sensor.solar", "predbat") and not is_predbat("automation.predbat_x", "predbat")


def test_manual_options_follow_predbat():
    m = manual_options(_states()["select.predbat_manual_charge"])
    assert m["chosen"] == ["Mon 22:00"] and m["slots"] == ["Mon 21:00", "Mon 21:30", "Mon 22:00", "Mon 22:30"]
    assert slot_option(["off", "[Mon 21:00]", "Mon 21:30", "+Mon 21:00"]) == "[Mon 21:00]"
    assert slot_option(["off"]) is None
    assert manual_options(None)["available"] is False


def test_plan_rows_start_now_in_plain_words():
    rows = plan_rows(_plan(), NOW)
    assert rows[0]["t"].startswith("2026-10-12T21:00")  # from the slot now on
    assert [r["kind"] for r in rows] == ["charge", "charge", "export", "hold", ""]
    assert rows[0]["label"] == "Charge" and rows[3]["label"] == "Freeze (battery held)" and rows[4]["label"] == "Normal"
    later = plan_rows(_plan(), NOW + datetime.timedelta(hours=2))
    assert len(later) == 1
    assert plan_rows(None, NOW) == [] and plan_rows({"rows": [{"time": "bad"}]}, NOW) == []


def test_data_for_the_cards():
    d = data(_states(), "predbat", NOW)
    assert d["found"] and d["status"]["text"] == "Demand" and not d["status"]["error"]
    assert d["battery"]["pct"] == 50 and d["battery"]["max_kwh"] == 10 and d["battery"]["charging"] is True
    assert d["cost"]["today"] == 1.23  # pence to pounds
    assert d["rates"]["import"] == 24.5 and d["rates"]["import_min"] == 7.5
    assert d["next_cheap"]["rate"] == 7.5 and d["next_cheap"]["start"].startswith("2026-10-12T23:30")
    assert d["next_charge"]["limit"] == 60 and d["next_export"] is None  # "undefined": none planned
    assert d["pv"]["today"] == 12.3 and d["pv"]["tomorrow"] is None and d["battery"]["kw"] == -1.5
    assert d["mode"]["value"] == "Control charge & discharge" and len(d["mode"]["options"]) == 4
    charge = d["manual"]["charge"]
    assert charge["now"] == "Mon 21:00" and not charge["now_on"] and charge["chosen"] == ["Mon 22:00"]
    assert d["manual"]["export"]["available"] is False
    assert d["end_soc"] == d["plan"][-1]["soc"]
    nothing = data({}, "predbat", NOW)
    assert not nothing["found"] and nothing["battery"]["pct"] is None and nothing["plan"] == []


def test_layout_is_checked_and_saved(tmp_path):
    lay = Layout(str(tmp_path))
    assert [c["type"] for c in lay.cards] == DEFAULT
    lay.save([{"type": "battery", "title": " Home battery "}, {"type": "entity", "entity": "sensor.living_room"},
              {"type": "battery", "id": "x"}, {"type": "battery", "id": "x"}])
    again = Layout(str(tmp_path)).cards
    assert again[0]["title"] == "Home battery" and len({c["id"] for c in again}) == 4  # ids made unique
    assert Layout(str(tmp_path)).entities() == ["sensor.living_room"]
    for bad in ([{"type": "nope"}], "x", [{"type": "entity", "entity": "not an entity"}], [{"type": "battery"}] * 61):
        with pytest.raises(ValueError):
            validate_layout(bad)
    assert [c["type"] for c in lay.reset()] == DEFAULT
    assert set(DEFAULT) <= set(CARDS)
    (tmp_path / "layout.json").write_text("{bad")
    assert [c["type"] for c in Layout(str(tmp_path)).cards] == DEFAULT


class FakeLink:
    available = connected = True

    def __init__(self, states):
        self.states, self.extra, self.calls = states, set(), []

    def set_extra(self, ids):
        changed = set(ids) != self.extra
        self.extra = set(ids)
        return changed

    async def all_states(self, fresh=False):
        return [{"entity_id": e, **st} for e, st in self.states.items()]

    async def call_service(self, domain, service, data=None, target=None):
        self.calls.append((domain, service, data, target))


def _runner(tmp_path):
    link, lay = FakeLink(_states()), Layout(str(tmp_path))
    lay.save([{"type": "battery"}, {"type": "entity", "entity": "sensor.living_room"}])
    state = SharedState()
    return Runner(link, lay, AppSettings(str(tmp_path)), state), link, state


def test_runner_follows_predbat_and_the_cards(tmp_path):
    runner, link, state = _runner(tmp_path)
    asyncio.run(runner.discover())
    assert "select.predbat_mode" in link.extra and "sensor.living_room" in link.extra
    assert runner.found == len(_states()) - 1
    runner.refresh(NOW)
    assert state.status == "Running" and state.data["battery"]["pct"] == 50
    assert state.entities["sensor.living_room"]["state"] == "21.5"
    assert [c["type"] for c in state.cards] == ["battery", "entity"]
    runner.settings.update({"plan_hours": 12})
    runner.refresh(NOW)
    assert len(state.data["plan"]) <= 24
    link.states = {}
    runner.refresh(NOW)
    assert state.status == "Waiting" and "prefix" in state.reason


def test_controls(tmp_path):
    runner, link, _ = _runner(tmp_path)
    run = lambda body: asyncio.run(runner.control(body))  # noqa: E731
    run({"control": "mode", "value": "Monitor"})
    assert link.calls[-1] == ("select", "select_option", {"option": "Monitor"}, {"entity_id": "select.predbat_mode"})
    run({"control": "charge", "op": "now"})
    assert link.calls[-1][2] == {"option": "Mon 21:00"}
    run({"control": "charge", "op": "remove", "option": "Mon 22:00"})
    assert link.calls[-1][2] == {"option": "[Mon 22:00]"}  # Predbat takes a [chosen] slot off
    n = len(link.calls)
    assert "already" in run({"control": "charge", "op": "add", "option": "Mon 22:00"})["message"]
    assert len(link.calls) == n
    run({"control": "charge", "op": "clear"})
    assert link.calls[-1][2] == {"option": "off"}
    for bad in ({"control": "mode", "value": "Turbo"}, {"control": "nope"}, {"control": "export", "op": "now"},
                {"control": "charge", "op": "add", "option": "Fri 01:00"}, {"control": "charge", "op": "x"}, []):
        with pytest.raises(ValueError):
            run(bad)
