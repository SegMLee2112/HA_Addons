"""The web page's API (needs aiohttp and pytest-aiohttp: requirements-dev.txt)."""

import pytest
import pytest_asyncio

from src.api import create_api_app
from src.app_settings import AppSettings
from src.debug_tools import DebugTools
from src.ha_link import HaLink
from src.health import Health
from src.layout import Layout
from src.runner import Runner
from src.shared_state import SharedState


@pytest.fixture
def state():
    return SharedState(status="Running", reason="Demand")


@pytest_asyncio.fixture
async def client(aiohttp_client, state, tmp_path):
    settings = AppSettings(str(tmp_path))
    link, lay = HaLink(str(tmp_path), state, token=""), Layout(str(tmp_path))
    app = create_api_app(state, ha_link=link, health=Health("1.2.3"), debug=DebugTools(settings=settings),
                         app_settings=settings, runner=Runner(link, lay, settings, state), layout=lay)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_page_and_state(client):
    resp = await client.get("/")
    assert resp.status == 200 and "Predbat Dashboard" in await resp.text()
    data = await (await client.get("/api/state")).json()
    assert data["status"] == "Running" and len(data["cards"]) > 0


@pytest.mark.asyncio
async def test_health(client):
    data = await (await client.get("/api/health")).json()
    assert data["version"] == "1.2.3" and data["home_assistant"]["available"] is False


@pytest.mark.asyncio
async def test_layout(client):
    data = await (await client.get("/api/layout")).json()
    assert data["cards"] and any(c["type"] == "soc_chart" for c in data["catalog"])
    resp = await client.post("/api/layout", json={"cards": [{"type": "battery"}, {"type": "entity", "entity": "sensor.x"}]})
    assert resp.status == 200 and [c["type"] for c in (await resp.json())["cards"]] == ["battery", "entity"]
    assert [c["type"] for c in (await (await client.get("/api/state")).json())["cards"]] == ["battery", "entity"]
    assert (await client.post("/api/layout", json={"cards": [{"type": "nope"}]})).status == 400
    resp = await client.post("/api/layout/reset")
    assert resp.status == 200 and len((await resp.json())["cards"]) > 2


@pytest.mark.asyncio
async def test_control_without_predbat(client):
    resp = await client.post("/api/control", json={"control": "mode", "value": "Monitor"})
    assert resp.status == 400 and "select.predbat_mode" in (await resp.json())["message"]


@pytest.mark.asyncio
async def test_settings_and_log_level(client):
    resp = await client.post("/api/settings", json={"log_level": "warning"})
    assert resp.status == 200 and (await resp.json())["log_level"] == "warning"
    assert (await client.post("/api/settings", json={"log_level": "loud"})).status == 400
    resp = await client.post("/api/debug/log_level", json={"level": "info"})
    assert resp.status == 200
    data = await (await client.get("/api/debug/info?lines=5")).json()
    assert data["log_level"] == "info"
