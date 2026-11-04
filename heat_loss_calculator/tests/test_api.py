"""The web page's API (needs aiohttp and pytest-aiohttp: requirements-dev.txt)."""

import pytest
import pytest_asyncio

from src.api import create_api_app
from src.app_settings import AppSettings
from src.calc_settings import CalcSettings
from src.calculator import Calculator
from src.debug_tools import DebugTools
from src.ha_link import HaLink
from src.health import Health
from src.shared_state import SharedState


@pytest.fixture
def state():
    return SharedState(status="Working")


@pytest_asyncio.fixture
async def client(aiohttp_client, state, tmp_path):
    settings = AppSettings(str(tmp_path))
    calc = Calculator(str(tmp_path), CalcSettings(str(tmp_path)))
    calc.days = {"2026-11-02": {"date": "2026-11-02", "complete": True, "dt": 12, "heat_kwh": 50, "hh": [{"gas": 1}]}}
    app = create_api_app(state, ha_link=HaLink(str(tmp_path), state, token=""), health=Health("1.2.3"),
                         debug=DebugTools(settings=settings), app_settings=settings, calc=calc)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_page_and_state(client):
    resp = await client.get("/")
    assert resp.status == 200 and "Heat Loss Calculator" in await resp.text()
    data = await (await client.get("/api/state")).json()
    assert data["status"] == "Working"


@pytest.mark.asyncio
async def test_health(client):
    data = await (await client.get("/api/health")).json()
    assert data["version"] == "1.2.3" and data["home_assistant"]["available"] is False


@pytest.mark.asyncio
async def test_entity_settings(client):
    resp = await client.post("/api/sensors", json={"outside": "sensor.temp"})
    assert resp.status == 200 and (await resp.json())["settings"]["outside"] == "sensor.temp"
    resp = await client.post("/api/sensors", json={"outside": "automation.x"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_your_home_and_the_days(client):
    resp = await client.post("/api/calc", json={"floors": [{"name": "Ground", "entity": "climate.hall", "area": 60}]})
    data = await resp.json()
    assert resp.status == 200 and data["area_m2"] == 60 and data["settings"]["floors"][0]["entity"] == "climate.hall"
    assert (await client.post("/api/calc", json={"design_in": 5, "design_out": 10})).status == 400
    day = await (await client.get("/api/day/2026-11-02")).json()
    assert day["hh"] == [{"gas": 1}]
    assert (await client.get("/api/day/2026-01-01")).status == 404
    resp = await client.post("/api/days/exclude", json={"date": "2026-11-02", "excluded": True})
    assert (await resp.json())["settings"]["excluded"] == ["2026-11-02"]
    resp = await client.post("/api/days/exclude", json={"date": "2026-11-02", "excluded": False})
    assert (await resp.json())["settings"]["excluded"] == []
    assert (await client.post("/api/recalculate")).status == 200


@pytest.mark.asyncio
async def test_settings_and_log_level(client):
    resp = await client.post("/api/settings", json={"log_level": "warning"})
    assert resp.status == 200 and (await resp.json())["log_level"] == "warning"
    assert (await client.post("/api/settings", json={"log_level": "loud"})).status == 400
    resp = await client.post("/api/debug/log_level", json={"level": "info"})
    assert resp.status == 200
    data = await (await client.get("/api/debug/info?lines=5")).json()
    assert data["log_level"] == "info"
