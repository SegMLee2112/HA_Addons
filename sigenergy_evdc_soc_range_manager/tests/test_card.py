"""The power flow card for your own dashboards (src/card.py) and its sensor."""

import asyncio

from src.card import CARD_FILE, LOCAL_URL, SOURCE, copy_card, install
from src.ha_entities import SENSORS, SensorPublisher
from src.shared_state import SharedState


class FakeLink:
    def __init__(self, resources=None, fail=False):
        self.resources, self.fail, self.sent = list(resources or []), fail, []

    async def query(self, payload, timeout=30):
        self.sent.append(payload)
        if self.fail:
            raise RuntimeError("Unauthorized")
        if payload["type"] == "lovelace/resources":
            return self.resources
        return {}


def test_card_is_copied_into_www(tmp_path):
    first = copy_card(str(tmp_path))
    assert first == {"written": True, "new_www": True}
    assert (tmp_path / "www" / CARD_FILE).read_bytes() == SOURCE.read_bytes()
    assert copy_card(str(tmp_path)) == {"written": False, "new_www": False}  # unchanged: left alone


def test_card_is_added_to_the_resources_once(tmp_path):
    (tmp_path / "www").mkdir()
    link = FakeLink()
    out = asyncio.run(install(link, "0.20.0", str(tmp_path)))
    assert out == {"ok": True, "note": None}
    assert link.sent[-1] == {"type": "lovelace/resources/create", "res_type": "module", "url": f"{LOCAL_URL}?v=0.20.0"}
    link = FakeLink([{"id": "abc", "type": "module", "url": f"{LOCAL_URL}?v=0.19.9"}])
    asyncio.run(install(link, "0.20.0", str(tmp_path)))
    assert link.sent[-1]["type"] == "lovelace/resources/update" and link.sent[-1]["resource_id"] == "abc"
    link = FakeLink([{"id": "abc", "type": "module", "url": f"{LOCAL_URL}?v=0.20.0"}])
    asyncio.run(install(link, "0.20.0", str(tmp_path)))
    assert len(link.sent) == 1  # already there


def test_card_install_says_what_went_wrong(tmp_path):
    out = asyncio.run(install(FakeLink(fail=True), "0.20.0", str(tmp_path)))
    assert not out["ok"] and "Resources" in out["note"]
    out = asyncio.run(install(FakeLink(), "0.20.0", str(tmp_path / "missing")))
    assert not out["ok"] and "www" in out["note"]
    (tmp_path / "fresh").mkdir()
    out = asyncio.run(install(FakeLink(), "0.20.0", str(tmp_path / "fresh")))  # a new www folder: restart HA once
    assert out["ok"] and "restart" in out["note"]


def test_power_flow_sensor_is_posted_at_most_every_5_s():
    entity = SENSORS["power_flow"][0]
    state = SharedState(flow={"pv_kw": 1.234, "grid_kw": -0.5, "battery_kw": 0.2, "car_kw": 3.0, "house_kw": 0.734,
                              "home_soc": 55.0, "lines": {"solar_house": 0.734, "solar_grid": 0.5}},
                        inputs={"soc": 61})
    pub = SensorPublisher()
    first = {e: (v, a) for e, v, a in pub.due(state, 0)}
    value, attrs = first[entity]
    assert value == "0.73" and attrs["pv_kw"] == 1.23 and attrs["car_soc"] == 61 and attrs["lines"]["solar_grid"] == 0.5
    assert "state_class" not in attrs  # not kept in the long-term statistics
    state.flow = {**state.flow, "house_kw": 0.9}
    assert entity not in [e for e, _, _ in pub.due(state, 2)]  # changed, but only 2 s later
    assert entity in [e for e, _, _ in pub.due(state, 6)]
    assert SharedState().flow == {} and pub.due(SharedState(), 400)  # no flow yet: unknown
