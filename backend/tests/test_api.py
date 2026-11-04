import httpx
import pytest
import respx
from httpx import ASGITransport

from app.db import Base, engine
from app.main import app

from .conftest import load


@pytest.fixture
async def client():
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            yield ac


@pytest.fixture
def fleet_mock():
    with respx.mock(assert_all_called=False) as m:
        m.get("http://10.0.0.1/api/system/info").respond(json=load("fpp_info_remote.json"))
        m.get("http://10.0.0.1/api/fppd/status").respond(json=load("fpp_status_remote.json"))
        m.get("http://10.0.0.2/api/system/info").respond(404)
        m.get("http://10.0.0.2/status.xml").respond(text=load("falcon_status.xml"))
        m.get("http://10.0.0.9/api/system/info").mock(side_effect=httpx.ConnectError("nope"))
        m.get("http://10.0.0.9/api/fppd/status").mock(side_effect=httpx.ConnectError("nope"))
        yield m


async def test_host_crud_autodetect(client, fleet_mock):
    r = await client.post("/api/hosts", json={"address": "http://10.0.0.1/"})
    assert r.status_code == 201, r.text
    h = r.json()
    assert h["kind"] == "fpp" and h["name"] == "FPP-PI-1" and h["status"]["online"] is True
    assert h["address"] == "10.0.0.1"

    r = await client.post("/api/hosts", json={"address": "10.0.0.2", "name": "F48"})
    assert r.status_code == 201 and r.json()["kind"] == "falcon"

    assert (await client.post("/api/hosts", json={"address": "10.0.0.1"})).status_code == 409
    assert (await client.post("/api/hosts", json={"address": "10.0.0.9"})).status_code == 422

    r = await client.post("/api/hosts", json={"address": "10.0.0.9", "kind": "fpp", "name": "Mega Tree"})
    assert r.status_code == 201 and r.json()["status"]["online"] is False

    r = await client.patch(f"/api/hosts/{h['id']}", json={"name": "K8-Pi 1", "enabled": False})
    assert r.json()["name"] == "K8-Pi 1" and r.json()["enabled"] is False
    snap = (await client.get("/api/snapshot")).json()
    assert "K8-Pi 1" not in [x["name"] for x in snap["hosts"]]
    assert len((await client.get("/api/hosts")).json()) == 3

    assert (await client.delete(f"/api/hosts/{h['id']}")).status_code == 204
    assert (await client.get(f"/api/hosts/{h['id']}")).status_code == 404


async def test_health(client):
    assert (await client.get("/api/health")).json()["ok"] is True
