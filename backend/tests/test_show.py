import copy
import json

import httpx
import pytest
import respx

from .conftest import load
from .test_api import client  # noqa: F401  (fixture)
from .test_backups import FPP_BACKUP


def player_status():
    s = copy.deepcopy(load("fpp_status_remote.json"))
    s.update(mode_name="player", host_name="FPPMain", uuid="M1-player")
    return s


@pytest.fixture
async def fleet(client, tmp_path, monkeypatch):  # noqa: F811
    """Player at 10.0.0.1, remote at 10.0.0.2 (offline later in some tests), Falcon at 10.0.0.3."""
    from app.main import app
    monkeypatch.setattr(app.state.backups, "root", tmp_path)
    info_p = dict(load("fpp_info_remote.json"), HostName="FPPMain", Mode="player", uuid="M1-player")
    with respx.mock(assert_all_called=False) as m:
        m.get("http://10.0.0.1/api/system/info").respond(json=info_p)
        m.get("http://10.0.0.1/api/fppd/status").respond(json=player_status())
        m.get("http://10.0.0.2/api/system/info").respond(json=load("fpp_info_remote.json"))
        m.get("http://10.0.0.2/api/fppd/status").respond(json=load("fpp_status_remote.json"))
        m.get("http://10.0.0.3/status.xml").respond(text=load("falcon_status.xml"))
        ids = {}
        for addr in ("10.0.0.1", "10.0.0.2"):
            ids[addr] = (await client.post("/api/hosts", json={"address": addr})).json()["id"]
        ids["10.0.0.3"] = (await client.post("/api/hosts", json={"address": "10.0.0.3", "name": "F48"})).json()["id"]
    return client, ids


async def test_status_is_flat(fleet):
    client, ids = fleet
    s = (await client.get("/api/show/status")).json()
    assert s["player"] == "FPPMain" and s["player_online"] and s["status"] == "idle" and s["playing"] is False
    assert s["controllers_total"] == 3 and s["controllers_online"] == 3


async def test_start_stop_transport_volume(fleet):
    client, ids = fleet
    with respx.mock() as m:
        cmd = m.post("http://10.0.0.1/api/command").respond(text="Playlist Started")
        r = await client.post("/api/show/start", json={"name": "Christmas_2026", "repeat": True})
        assert r.status_code == 200 and r.json()["host"] == "FPPMain"
        assert json.loads(cmd.calls[0].request.content) == {
            "command": "Start Playlist", "args": ["Christmas_2026", "true", "false", "false"]}

        for mode, path in (("graceful", "stopgracefully"), ("now", "stop"), ("after_loop", "stopgracefullyafterloop")):
            route = m.post(f"http://10.0.0.1/api/playlists/{path}").respond(json={"status": "OK"})
            assert (await client.post("/api/show/stop", json={"mode": mode})).status_code == 200
            assert route.called
        assert (await client.post("/api/show/stop", json={"mode": "explode"})).status_code == 422

        # explicit routes: /volume and /command must not be swallowed by the transport routes
        pause = m.post("http://10.0.0.1/api/playlists/pause").respond(json={})
        assert (await client.post("/api/show/pause")).status_code == 200 and pause.called
        assert (await client.post("/api/show/next", json={})).status_code == 200
        assert json.loads(cmd.calls[-1].request.content)["command"] == "Next Playlist Item"
        vol = m.post("http://10.0.0.1/api/system/volume").respond(json={"status": "OK"})
        r = await client.post("/api/show/volume", json={"volume": 65})
        assert r.json()["volume"] == 65 and json.loads(vol.calls[0].request.content) == {"volume": 65}
        assert (await client.post("/api/show/volume", json={"volume": 120})).status_code == 422

        # a specific host, and a non-FPP host is refused
        rem = m.post("http://10.0.0.2/api/command").respond(text="ok")
        assert (await client.post("/api/show/command", json={"command": "All Lights Off", "host_id": ids["10.0.0.2"]})).status_code == 200
        assert rem.called
        assert (await client.post("/api/show/command", json={"command": "x", "host_id": ids["10.0.0.3"]})).status_code == 409


async def test_command_errors_and_multisync(fleet):
    client, ids = fleet
    with respx.mock() as m:
        cmd = m.post("http://10.0.0.1/api/command").mock(side_effect=[
            httpx.Response(500, text="Unknown command 'Nope'"),
            httpx.Response(200, text="OK"),
        ])
        r = await client.post("/api/show/command", json={"command": "Nope"})
        assert r.status_code == 422 and "Unknown command" in r.json()["detail"]
        r = await client.post("/api/show/command", json={"command": "Volume Set", "args": [50, True], "multisync": True})
        assert r.status_code == 200
        assert json.loads(cmd.calls[1].request.content) == {
            "command": "Volume Set", "args": ["50", "true"], "multisyncCommand": True, "multisyncHosts": ""}
        m.post("http://10.0.0.1/api/command").mock(side_effect=httpx.ConnectError("down"))
        assert (await client.post("/api/show/command", json={"command": "x"})).status_code == 502


async def test_schedule_read_and_save(fleet):
    client, ids = fleet
    entries = load("fpp_schedule.json")
    for e in entries:
        e["endDate"] = "2099-12-31"          # keep the "not finished yet" check independent of today's date
    with respx.mock() as m:
        m.get("http://10.0.0.1/api/schedule").respond(json=entries)
        m.get("http://10.0.0.1/api/fppd/schedule").respond(json=load("fpp_fppd_schedule.json"))
        m.get("http://10.0.0.1/api/playlists").respond(json=["Christmas_2026"])      # "November" is missing
        m.get("http://10.0.0.1/api/playlist/Christmas_2026").respond(
            json={"name": "Christmas_2026", "empty": True, "leadIn": [], "mainPlaylist": [], "leadOut": []})
        s = (await client.get("/api/show/schedule")).json()
        assert s["entries"][0]["warning"] == "Playlist 'November' does not exist on the player"
        assert s["entries"][1]["warning"] == "Playlist 'Christmas_2026' is empty"
        assert s["entries"][2]["warning"] is None
        assert [e["kind"] for e in s["entries"]] == ["playlist", "playlist", "command", "command"]
        assert s["entries"][0]["day_str"] == "Everyday" and s["entries"][0]["stop_str"] == "Graceful"
        assert s["upcoming"][0]["command"] == "Start Playlist" and s["upcoming"][0]["start_str"]

        new = copy.deepcopy(entries)
        new[0]["enabled"] = 0
        assert (await client.put("/api/show/schedule", json={"entries": new})).status_code == 422   # no confirm
        bad = [dict(new[0], playlist="", command=None)]
        assert (await client.put("/api/show/schedule", json={"entries": bad, "confirm": True})).status_code == 422

        m.post("http://10.0.0.1/backup.php").respond(json=FPP_BACKUP)
        save = m.post("http://10.0.0.1/api/schedule").respond(json=new)
        reload_ = m.post("http://10.0.0.1/api/schedule/reload").respond(json={"status": "OK"})
        r = await client.put("/api/show/schedule", json={"entries": new, "confirm": True})
        assert r.status_code == 200, r.text
        assert json.loads(save.calls[0].request.content) == new        # unknown keys round-trip untouched
        assert reload_.called
        pre = (await client.get(f"/api/backups/{r.json()['pre_change_backup_id']}")).json()
        assert pre["trigger"] == "pre-change" and pre["status"] == "ok"


async def test_test_mode_fills_per_host_defaults(fleet):
    client, ids = fleet
    spec = {"args": [{"name": "ChannelRange", "contents": ["1-150"]}, {"name": "ChaseType", "contents": ["R-G-B"]}]}
    with respx.mock() as m:
        m.get(url__regex=r"http://10\.0\.0\.1/api/fppd/testing/tests/RGB%20Chase").respond(json=spec)
        spec2 = {"args": [{"name": "ChannelRange", "contents": ["513-19072"]}, {"name": "ChaseType", "contents": ["R-G-B"]}]}
        m.get(url__regex=r"http://10\.0\.0\.2/api/fppd/testing/tests/RGB%20Chase").respond(json=spec2)
        c1 = m.post("http://10.0.0.1/api/command").respond(text="OK")
        c2 = m.post("http://10.0.0.2/api/command").mock(side_effect=httpx.ConnectError("x"))
        r = await client.post("/api/show/test/start", json={"pattern": "RGB Chase", "interval_ms": 500, "args": ["", "R-G-B-W"]})
        res = {x["host"]: x for x in r.json()}
        assert res["FPPMain"]["ok"] and not res["FPP-PI-1"]["ok"] and "F48" not in res
        assert json.loads(c1.calls[0].request.content)["args"] == ["500", "RGB Chase", "1-150", "R-G-B-W"]
        assert c2.called
    assert (await client.post("/api/show/test/start", json={"host_ids": [ids["10.0.0.3"]]})).status_code == 422


async def test_fleet_actions(fleet):
    client, ids = fleet
    st = (await client.get("/api/fleet/status")).json()
    assert {h["name"] for h in st} == {"FPPMain", "FPP-PI-1", "F48"} and all("cpu" in h for h in st)
    assert (await client.post("/api/fleet/actions/restart_fppd", json={})).status_code == 422     # confirm needed
    with respx.mock() as m:
        r1 = m.post("http://10.0.0.1/api/system/fppd/restart").respond(text="OK")
        r2 = m.post("http://10.0.0.2/api/system/fppd/restart").mock(side_effect=httpx.ConnectError("x"))
        r = await client.post("/api/fleet/actions/restart_fppd", json={"confirm": True})
        res = {x["host"]: x["ok"] for x in r.json()}
        assert res == {"FPPMain": True, "FPP-PI-1": False} and r1.called and r2.called
    r = await client.post("/api/fleet/actions/restart_fppd", json={"host_ids": [ids["10.0.0.3"]], "confirm": True})
    assert r.status_code == 422
    assert (await client.post("/api/fleet/actions/launch_rockets", json={"confirm": True})).status_code == 404
