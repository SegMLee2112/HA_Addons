"""The web page's API (needs aiohttp and pytest-aiohttp: requirements-dev.txt)."""

import pytest
import pytest_asyncio

from src.api import create_api_app
from src.app_settings import AppSettings
from src.debug_tools import DebugTools
from src.ha_link import HaLink
from src.health import Health
from src.shared_state import SharedState


@pytest.fixture
def state():
    return SharedState(status="Discharging", inputs={"high": 80.0, "low": 40.0})


@pytest_asyncio.fixture
async def client(aiohttp_client, state, tmp_path):
    settings = AppSettings(str(tmp_path))
    app = create_api_app(state, ha_link=HaLink(str(tmp_path), state, token=""), health=Health("1.2.3"),
                         debug=DebugTools(settings=settings), app_settings=settings)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_page_and_state(client):
    resp = await client.get("/")
    assert resp.status == 200 and "SigEnergy DC Charger" in await resp.text()
    data = await (await client.get("/api/state")).json()
    assert data["status"] == "Discharging" and data["inputs"]["high"] == 80


@pytest.mark.asyncio
async def test_health(client):
    data = await (await client.get("/api/health")).json()
    assert data["version"] == "1.2.3" and data["home_assistant"]["available"] is False


@pytest.mark.asyncio
async def test_entity_settings(client):
    data = await (await client.get("/api/sensors")).json()
    assert data["settings"]["running_state"] == "sensor.sigen_inverter_dc_charger_running_state"  # the default
    resp = await client.post("/api/sensors", json={"charge_signal": "binary_sensor.predbat_charging"})
    assert resp.status == 200 and (await resp.json())["settings"]["charge_signal"] == "binary_sensor.predbat_charging"
    resp = await client.post("/api/sensors", json={"start_button": "sensor.x"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_limits_and_charger_need_home_assistant(client):
    assert (await client.post("/api/limits", json={"high": 30, "low": 50})).status == 400  # low above high
    assert (await client.post("/api/limits", json={"high": 130})).status == 400
    resp = await client.post("/api/limits", json={"high": 85})
    assert resp.status == 400  # no helper picked yet (they're made when Home Assistant connects)
    assert (await client.post("/api/charger", json={"action": "start"})).status == 501  # no runner here
    assert (await client.get("/api/stats")).status == 501
    assert (await client.post("/api/settings", json={"margin_pct": 2, "observe_only": True})).status == 200
    assert (await client.post("/api/settings", json={"margin_pct": 50})).status == 400


@pytest.mark.asyncio
async def test_settings_and_log_level(client):
    resp = await client.post("/api/settings", json={"log_level": "warning"})
    assert resp.status == 200 and (await resp.json())["log_level"] == "warning"
    assert (await client.post("/api/settings", json={"log_level": "loud"})).status == 400
    resp = await client.post("/api/debug/log_level", json={"level": "info"})
    assert resp.status == 200
    data = await (await client.get("/api/debug/info?lines=5")).json()
    assert data["log_level"] == "info"


@pytest.mark.asyncio
async def test_battery_capacity_is_set_from_the_schedule_page(client):
    assert (await client.post("/api/capacity", json={"capacity_kwh": 500})).status == 400  # out of range
    assert (await client.post("/api/capacity", json={"capacity_kwh": "lots"})).status == 400
    resp = await client.post("/api/capacity", json={"capacity_kwh": 42.5})
    assert resp.status == 400  # nothing picked and no Home Assistant to make the helper in
