"""Phase 5: content overview/push, fleet versions/updates, settings compare, jobs."""
import copy
import json

import httpx
import pytest
import respx

from app.updates import image_date, is_failure_line, os_date, recommended_os

from .conftest import load
from .test_api import client  # noqa: F401  (fixture)
from .test_backups import FPP_BACKUP
from .test_show import fleet  # noqa: F401  (fixture)

P, R, R2 = "http://10.0.0.1", "http://10.0.0.2", "http://10.0.0.4"
SEQ = {"A.fseq": b"a" * 1000, "B.fseq": b"b" * 5000, "C.fseq": b"c" * 300_000}


def files(names_sizes):
    return {"status": "ok", "files": [{"name": n, "mtime": "11/01/26  08:00 PM", "sizeBytes": s, "sizeHuman": ""}
                                      for n, s in names_sizes.items()]}


def mock_content(m, remote_has=("A.fseq",), remote2_has=()):
    m.get(f"{P}/api/files/sequences").respond(json=files({n: len(b) for n, b in SEQ.items()}))
    m.get(f"{P}/api/files/music").respond(json=files({"a.mp3": 10, "c.mp3": 10}))
    m.get(f"{P}/api/files/videos").respond(json=files({}))
    m.get(f"{P}/api/playlists").respond(json=["Show", "Old", "Wrapper"])
    m.get(f"{P}/api/playlist/Show").respond(json={"mainPlaylist": [
        {"type": "both", "sequenceName": "A.fseq", "mediaName": "a.mp3", "enabled": 1},
        {"type": "both", "sequenceName": "C.fseq", "mediaName": "c.mp3", "enabled": 1},
        {"type": "both", "sequenceName": "Gone.fseq", "mediaName": "gone.mp3", "enabled": 1},
        {"type": "sequence", "sequenceName": "Disabled.fseq", "enabled": 0}]})
    m.get(f"{P}/api/playlist/Old").respond(json={"mainPlaylist": [{"type": "sequence", "sequenceName": "B.fseq"}]})
    m.get(f"{P}/api/playlist/Wrapper").respond(json={"leadIn": [{"type": "playlist", "name": "Old"},
                                                                {"type": "playlist", "name": "Nope"}]})
    m.get(f"{P}/api/schedule").respond(json=[{"enabled": 1, "playlist": "Show", "endDate": "2099-12-31"},
                                             {"enabled": 0, "playlist": "Old", "endDate": "2099-12-31"}])
    m.get(f"{R}/api/files/sequences").respond(json=files({n: 10 for n in remote_has}))   # trimmed copies
    m.get(f"{R2}/api/files/sequences").respond(json=files({n: 10 for n in remote2_has}))


@pytest.fixture
async def fleet5(fleet):  # noqa: F811
    """fleet (player .1, remote .2, Falcon .3) plus a second remote .4 that has no sequences."""
    client, ids = fleet
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{R2}/api/system/info").respond(json=dict(load("fpp_info_remote.json"), HostName="FPP-PB-1", uuid="M1-pb1"))
        m.get(f"{R2}/api/fppd/status").respond(json=load("fpp_status_remote.json"))
        ids["10.0.0.4"] = (await client.post("/api/hosts", json={"address": "10.0.0.4"})).json()["id"]
    return client, ids


async def test_content_overview(fleet5):
    client, ids = fleet5
    with respx.mock(assert_all_called=False) as m:
        mock_content(m)
        d = (await client.get("/api/content")).json()
    assert d["player"]["name"] == "FPPMain" and d["scheduled_playlists"] == ["Show"]
    remotes = {r["id"]: r for r in d["remotes"]}
    r1, r2 = remotes[ids["10.0.0.2"]], remotes[ids["10.0.0.4"]]
    assert r1["uses_sequences"] and r1["missing"] == ["B.fseq", "C.fseq"]
    assert not r2["uses_sequences"] and r2["missing"] == []        # fed over the network: not a problem
    seqs = {s["name"]: s for s in d["sequences"]}
    assert seqs["B.fseq"]["playlists"] == ["Old", "Wrapper"]       # via the sub-playlist
    msgs = {(p["severity"], p["host"], p["file"]) for p in d["problems"]}
    assert ("critical", "FPPMain", "Gone.fseq") in msgs and ("critical", "FPPMain", "gone.mp3") in msgs
    assert (("critical", r1["name"], "C.fseq") in msgs) and (("warning", r1["name"], "B.fseq") in msgs)
    assert ("warning", "FPPMain", "Nope") in msgs
    assert not any(p["file"] == "Disabled.fseq" for p in d["problems"])
    assert d["problems"][0]["severity"] == "critical"


async def _wait_job(client, job_id):
    from app.main import app
    await app.state.jobs.wait(app.state.jobs.get(job_id))
    return (await client.get(f"/api/jobs/{job_id}")).json()


async def test_push_missing_sequences(fleet5):
    client, ids = fleet5
    uploads = {}

    async def upload(request):
        name = request.url.path.rsplit("/", 1)[-1]
        body = await request.aread()
        uploads[name] = (body, request.headers.get("content-length"))
        return httpx.Response(200, json={"status": "OK", "written": len(body), "size": len(body)})

    with respx.mock(assert_all_called=False) as m:
        mock_content(m)
        for n, b in SEQ.items():
            m.get(f"{P}/api/file/sequences/{n}").respond(content=b)
        m.get(f"{R}/api/system/info").respond(json=load("fpp_info_remote.json"))
        m.post(url__regex=rf"{R}/api/file/uploads/.*").mock(side_effect=upload)
        move = m.post(url__regex=rf"{R}/api/file/move/.*").respond(json={"status": "OK"})

        r = await client.post("/api/content/push", json={})
        assert r.status_code == 422 and "confirm" in r.json()["detail"]
        r = await client.post("/api/content/push", json={"confirm": True})
        assert r.status_code == 202
        job = await _wait_job(client, r.json()["job"]["id"])
    assert job["status"] == "ok" and len(job["steps"]) == 1          # remote .4 has no sequences: skipped
    assert job["steps"][0]["host_id"] == ids["10.0.0.2"] and "copied 2 files" in job["steps"][0]["message"]
    assert uploads == {"B.fseq": (SEQ["B.fseq"], "5000"), "C.fseq": (SEQ["C.fseq"], "300000")}
    assert [c.request.url.path for c in move.calls] == ["/api/file/move/B.fseq", "/api/file/move/C.fseq"]


async def test_push_short_write_cleans_up(fleet5):
    client, ids = fleet5
    with respx.mock(assert_all_called=False) as m:
        mock_content(m)
        m.get(f"{P}/api/file/sequences/B.fseq").respond(content=SEQ["B.fseq"])
        m.get(f"{R}/api/system/info").respond(json=load("fpp_info_remote.json"))
        m.post(f"{R}/api/file/uploads/B.fseq").respond(json={"status": "OK", "written": 4096})   # disk full
        cleanup = m.delete(f"{R}/api/file/uploads/B.fseq").respond(json={"status": "OK"})
        move = m.post(url__regex=rf"{R}/api/file/move/.*").respond(json={"status": "OK"})
        r = await client.post("/api/content/push", json={"confirm": True, "files": ["B.fseq"],
                                                          "host_ids": [ids["10.0.0.2"]]})
        job = await _wait_job(client, r.json()["job"]["id"])
    assert job["status"] == "failed" and "4096 of 5000" in job["steps"][0]["message"]
    assert cleanup.called and not move.called


async def test_push_guards(fleet5):
    from app.main import app
    client, ids = fleet5
    with respx.mock(assert_all_called=False) as m:
        mock_content(m, remote_has=("A.fseq", "B.fseq", "C.fseq"))
        r = await client.post("/api/content/push", json={"confirm": True})
        assert r.status_code == 202 and r.json()["job"] is None      # nothing missing
        r = await client.post("/api/content/push", json={"confirm": True, "files": ["Nope.fseq"]})
        assert r.status_code == 404
        r = await client.post("/api/content/push", json={"confirm": True, "host_ids": [ids["10.0.0.3"]]})
        assert r.status_code == 404                                     # the Falcon is not an FPP remote
        app.state.collector.states[ids["10.0.0.1"]].status = "playing"
        r = await client.post("/api/content/push", json={"confirm": True, "overwrite": True})
        assert r.status_code == 409 and "playing" in r.json()["detail"]


# ---------------------------------------------------------------- updates
UPD = {"status": "OK", "branchUpgradeAvailable": False, "commitUpdateAvailable": True, "remoteCommit": "abcdef1234",
       "currentBranch": "v10.3", "localCommit": "da997bd61", "isMajorVersionUpgrade": False, "checked": True}
IDLE = {"inProgress": False, "op": "", "status": "OK"}


def mock_versions(m, base, upd=UPD):
    m.get(f"{base}/api/system/updateStatus").respond(json=upd)
    m.get(f"{base}/api/system/updateActivity").respond(json=IDLE)
    m.get(f"{base}/api/plugin/updateStatus").respond(json={"plugins": [], "installed": 1})


async def test_versions(fleet5):
    client, ids = fleet5
    with respx.mock(assert_all_called=False) as m:
        for base in (P, R, R2):
            m.get(f"{base}/api/system/info").respond(json=dict(load("fpp_info_remote.json"), OSVersion="v2026-09 (32bit)"))
            mock_versions(m, base)
        m.get(f"{P}/api/git/releases/os/0").respond(json={"downloaded": [], "files": [
            {"tag": "nightly", "filename": "Pi-nightly_2026-11.fppos", "prerelease": True, "url": "u0", "size": 1},
            {"tag": "10.3", "filename": "Pi-10.3_2026-10b.fppos", "prerelease": False, "url": "u1", "size": 1}]})
        m.route(url__regex=r"http://10\.0\.0\.[24]/api/git/releases/os/0").respond(404)
        d = (await client.get("/api/fleet/versions?os=true")).json()
    rows = {r["name"]: r for r in d["hosts"]}
    assert rows["FPPMain"]["is_player"] and rows["FPPMain"]["update_available"]
    assert d["summary"]["updates_available"] == 3
    # every Pi shares the cached image list: the player's lookup answers for the others too
    assert any(r.get("os_update", {}) and r["os_update"]["filename"] == "Pi-10.3_2026-10b.fppos" for r in rows.values())
    assert rows["F48"]["updatable"] is False


def test_version_helpers():
    assert os_date("v2026-10b (32bit)") == "2026-10b" and image_date("BBB-10.3_2026-10b.fppos") == "2026-10b"
    imgs = [{"tag": "10.3", "filename": "Pi-10.3_2026-10b.fppos", "prerelease": False}]
    assert recommended_os(imgs, "10.3", "v2026-10b (32bit)") is None
    assert recommended_os(imgs, "10.3", "v2026-09 (32bit)")["filename"] == "Pi-10.3_2026-10b.fppos"
    assert is_failure_line("===== Upgrade Failed =====") and is_failure_line("Upgrade FAILED.")
    assert is_failure_line("Can not access github, unable to pull git updates")
    assert not is_failure_line("===== Upgrade Complete =====")


async def test_update_rolls_remotes_first_and_stops_on_failure(fleet5, tmp_path):
    client, ids = fleet5
    order = []

    def page(text):
        def handler(request):
            order.append(request.url.host)
            return httpx.Response(200, text=text)
        return handler

    with respx.mock(assert_all_called=False) as m:
        for base in (P, R, R2):
            m.get(f"{base}/api/system/info").respond(json=load("fpp_info_remote.json"))
            mock_versions(m, base)
            m.post(f"{base}/backup.php").respond(json=copy.deepcopy(FPP_BACKUP))
            m.get(f"{base}/api/fppd/status").respond(json=load("fpp_status_remote.json"))
        m.post(f"{R}/manualUpdate.php").mock(side_effect=page("Pulling\n===== Restarting FPP =====\n===== Upgrade Complete =====\n"))
        m.post(f"{R2}/manualUpdate.php").mock(side_effect=page("Pulling\nerror: build broke\nUpgrade FAILED.\n"))
        m.post(f"{P}/manualUpdate.php").mock(side_effect=page("===== Upgrade Complete =====\n"))

        body = {"op": "update", "host_ids": [ids["10.0.0.1"], ids["10.0.0.2"], ids["10.0.0.4"]]}
        assert (await client.post("/api/fleet/update", json=body)).status_code == 422          # needs confirm
        r = await client.post("/api/fleet/update", json={**body, "confirm": True})
        assert r.status_code == 202
        assert (await client.post("/api/fleet/update", json={**body, "confirm": True})).status_code == 409
        job = await _wait_job(client, r.json()["job"]["id"])
    steps = [(s["host"], s["status"]) for s in job["steps"]]
    assert steps[-1][0] == "FPPMain"                                     # the player goes last
    assert job["status"] == "partial" and steps[-1][1] == "skipped"      # stopped after the failure
    assert order == ["10.0.0.2", "10.0.0.4"]
    failed = next(s for s in job["steps"] if s["status"] == "failed")
    assert "Upgrade FAILED." in failed["log"] and "reported the update failed" in failed["message"]
    backups = (await client.get("/api/backups")).json()
    assert {b["trigger"] for b in backups} == {"pre-change"} and all(b["note"] == "Before FPP update" for b in backups)


async def test_update_guards(fleet5):
    from app.main import app
    client, ids = fleet5
    with respx.mock(assert_all_called=False) as m:
        for base in (P, R):
            m.get(f"{base}/api/system/info").respond(json=load("fpp_info_remote.json"))
            mock_versions(m, base, dict(UPD, branchUpgradeAvailable=True, branchUpgradeVersion="11.0",
                                        isMajorVersionUpgrade=True))
        r = await client.post("/api/fleet/update", json={"op": "update", "host_ids": [ids["10.0.0.3"]], "confirm": True})
        assert r.status_code == 422 and "F48" in r.json()["detail"]
        r = await client.post("/api/fleet/update", json={"op": "upgrade", "version": "11.0",
                                                          "host_ids": [ids["10.0.0.2"]], "confirm": True})
        assert r.status_code == 422 and "OS upgrade" in r.json()["detail"]
        r = await client.post("/api/fleet/update", json={"op": "os", "host_ids": [ids["10.0.0.2"]], "confirm": True})
        assert r.status_code == 422
        app.state.collector.states[ids["10.0.0.1"]].status = "playing"
        r = await client.post("/api/fleet/update", json={"op": "update", "host_ids": [ids["10.0.0.2"]], "confirm": True})
        assert r.status_code == 409 and "playing" in r.json()["detail"]


async def test_settings_compare(fleet5):
    client, ids = fleet5
    other = copy.deepcopy(FPP_BACKUP)
    other["settings"]["system_settings"][0].update(HostName="FPP-PI-2", volume="70", MQTTPassword="different",
                                                     TimeZone="America/Chicago")
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{P}/backup.php").respond(json=copy.deepcopy(FPP_BACKUP))
        m.post(f"{R}/backup.php").respond(json=other)
        m.get(f"{P}/api/plugin").respond(json=["fpp-HomeAssistant", "remote-falcon"])
        m.get(f"{R}/api/plugin").respond(json=["fpp-HomeAssistant"])
        m.get(f"{R2}/api/plugin").respond(json=[])
        for addr in ("10.0.0.1", "10.0.0.2"):
            assert (await client.post("/api/backups/run", json={"host_ids": [ids[addr]]})).status_code == 200
        r = await client.get(f"/api/fleet/settings?host_ids={ids['10.0.0.1']},{ids['10.0.0.2']},{ids['10.0.0.4']}")
    d = r.json()
    have_backup = {h["id"]: h["backup_id"] is not None for h in d["hosts"]}
    assert have_backup == {ids["10.0.0.1"]: True, ids["10.0.0.2"]: True, ids["10.0.0.4"]: False}
    rows = {x["key"]: x for x in d["rows"]}
    p, rm = str(ids["10.0.0.1"]), str(ids["10.0.0.2"])
    assert rows["settings/volume"]["values"] == {p: "50", rm: "70"} and not rows["settings/volume"]["expected"]
    assert rows["settings/HostName"]["expected"] and rows["settings/TimeZone"]["values"][p] is None
    assert rows["settings/MQTTPassword"]["secret"] and set(rows["settings/MQTTPassword"]["values"].values()) == {"••••••"}
    assert "hunter2" not in json.dumps(d) and "different" not in json.dumps(d)
    assert rows["plugins/remote-falcon"]["values"] == {p: "installed", rm: "not installed"}
    assert "settings/passwordEnable" not in rows                      # same everywhere


async def test_jobs_api(fleet5):
    client, _ = fleet5
    assert (await client.get("/api/jobs")).json() == []
    assert (await client.get("/api/jobs/nope")).status_code == 404
