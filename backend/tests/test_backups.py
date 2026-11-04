import copy
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.backups.devices import parse_falcon_network, parse_restore_page
from app.backups.diff import MASK, diff_backups
from app.backups.retention import Item, select_to_delete

from .conftest import load
from .test_api import client  # noqa: F401  (fixture)

FPP_BACKUP = {
    "channelOutputs": {"pixel_strings": [{"channelOutputs": [{"enabled": 1, "outputs": [{"portNumber": 0}]}]}]},
    "show_setup": {"playlist": [], "schedule": [[""]]},
    "settings": {"system_settings": [{"HostName": "FPP-PI-1", "volume": "50", "MQTTPassword": "hunter2",
                                      "passwordEnable": "1"}]},
    "network": {"wifi_network": [['INTERFACE="wlan0"', 'ADDRESS="10.0.0.1"', 'PSK="supersecret"', ""]]},
    "fpp_backup_version": "8.0", "backup_taken": 1, "backup_comment": "x", "backup_trigger_source": None,
    "platform": "Raspberry Pi", "protected": False,
}

RESTORE_OK_PAGE = """<html><div id="rebootFlag" class="callout callout-warning">Backup Restored</div>
<div id="restoreSuccessFlag" class="callout callout-primary">What was restored: <br>
<span class='callout callout-success' style='x'>Channel Outputs - <b>Success</b></span><br/>
<span class='callout callout-danger' style='x'>Show Setup - <b>Failed</b></span><br/></div></html>"""
RESTORE_ERR_PAGE = """<div id="rebootFlag">Backup failed: <br>
<ul><li>doRestore: The backup x data could not be decoded properly.</li></ul></div>"""


# ------------------------------------------------------------------ pure functions
def test_parse_restore_page():
    r = parse_restore_page(RESTORE_OK_PAGE)
    assert r.restored and r.areas == [{"area": "Channel Outputs", "ok": True}, {"area": "Show Setup", "ok": False}]
    r = parse_restore_page(RESTORE_ERR_PAGE)
    assert not r.restored and r.errors == ["doRestore: The backup x data could not be decoded properly."]


def test_parse_falcon_network():
    page = ('<input type="text" name="ip" id="ip" value="10.55.1.102"><input name="host" value="F48   ">'
            '<input type="checkbox" name="dhcp" value="1"><input type="checkbox" name="x" value="1" checked>')
    assert parse_falcon_network(page) == {"ip": "10.55.1.102", "host": "F48", "x": "1"}


def test_diff_masks_secrets_and_ignores_metadata():
    b = copy.deepcopy(FPP_BACKUP)
    b["backup_taken"] = 2
    b["settings"]["system_settings"][0].update(volume="70", MQTTPassword="changed")
    b["network"]["wifi_network"][0][2] = 'PSK="other"'
    d = diff_backups(FPP_BACKUP, b)
    by_path = {c["path"]: c for c in d["changes"]}
    assert set(by_path) == {"settings/system_settings/volume", "settings/system_settings/MQTTPassword",
                            "network/wifi_network"}
    assert by_path["settings/system_settings/volume"]["a"] == "50"
    assert by_path["settings/system_settings/MQTTPassword"] == {   # changed, but neither value shown
        "area": "settings", "path": "settings/system_settings/MQTTPassword", "type": "changed",
        "a": MASK, "b": MASK, "secret": True}
    assert by_path["network/wifi_network"]["secret"] is True
    assert "supersecret" not in json.dumps(d) and "other" not in json.dumps(d) and "hunter2" not in json.dumps(d)
    same = diff_backups(FPP_BACKUP, FPP_BACKUP)
    assert same["changes"] == []


def test_retention_gfs():
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    # two backups a day for 400 days, plus a pinned old one and an old failure
    items, n = [], 0
    for d in range(400):
        for h in (8, 20):
            n += 1
            items.append(Item(n, now - timedelta(days=d, hours=h), "ok", False))
    items.append(Item(9998, now - timedelta(days=900), "ok", True))
    items.append(Item(9999, now - timedelta(days=40), "failed", False))
    doomed = select_to_delete(items, tz, daily=14, weekly=8, monthly=12, now=now, recent_hours=0)
    kept = [i for i in items if i.id not in doomed]
    assert 9998 not in doomed and 9999 in doomed
    assert items[0].id not in doomed                       # newest always kept
    good_kept = [i for i in kept if not i.pinned]
    assert 14 <= len(good_kept) <= 14 + 8 + 12              # periods overlap
    assert max(now - i.created_at for i in good_kept) > timedelta(days=300)   # monthly reaches back ~a year
    assert all(i.status == "ok" or i.pinned for i in kept)
    # everything from the last 48 hours survives by default
    doomed48 = select_to_delete(items, tz, daily=14, weekly=8, monthly=12, now=now)
    assert all(i.id not in doomed48 for i in items if now - i.created_at < timedelta(hours=48))


# ------------------------------------------------------------------ API
@pytest.fixture
async def setup(client, tmp_path, monkeypatch):  # noqa: F811
    from app.main import app
    svc = app.state.backups
    monkeypatch.setattr(svc, "root", tmp_path)
    with respx.mock(assert_all_called=False) as m:
        m.get("http://10.0.0.1/api/system/info").respond(json=load("fpp_info_remote.json"))
        m.get("http://10.0.0.1/api/fppd/status").respond(json=load("fpp_status_remote.json"))
        m.get("http://10.0.0.2/status.xml").respond(text=load("falcon_status.xml"))
        h1 = (await client.post("/api/hosts", json={"address": "10.0.0.1"})).json()["id"]
        h2 = (await client.post("/api/hosts", json={"address": "10.0.0.2", "kind": "falcon", "name": "F48"})).json()["id"]
    return client, svc, h1, h2, tmp_path


def mock_fpp_page(m, address="10.0.0.1", backup=None, restore_page=RESTORE_OK_PAGE, calls=None):
    def handler(request):
        body = request.content
        if calls is not None:
            calls.append(body)
        if b"btnRestoreConfig" in body:
            return httpx.Response(200, text=restore_page)
        return httpx.Response(200, json=backup or FPP_BACKUP)
    return m.post(f"http://{address}/backup.php").mock(side_effect=handler)


def mock_falcon(m, address="10.0.0.2"):
    m.get(f"http://{address}/status.xml").respond(text=load("falcon_status.xml"))
    m.get(f"http://{address}/settings.xml").respond(text="<settings><universes/></settings>")
    m.get(f"http://{address}/strings.xml").respond(text=load("falcon_strings.xml"))
    m.get(f"http://{address}/serialsettings.xml").respond(text="<serial/>")
    m.get(f"http://{address}/config.htm").respond(text='<input name="ip" value="10.0.0.2">')


async def test_backup_run_list_download_diff(setup):
    client, svc, h1, h2, root = setup
    with respx.mock(assert_all_called=False) as m:
        mock_fpp_page(m)
        mock_falcon(m)
        r = await client.post("/api/backups/run", json={})
        assert r.status_code == 200, r.text
        res = {b["host_name"]: b for b in r.json()}
        assert res["FPP-PI-1"]["status"] == "ok" and res["FPP-PI-1"]["changed"] is True
        assert res["FPP-PI-1"]["platform"] == "Raspberry Pi"
        assert res["F48"]["status"] == "ok" and res["F48"]["version"] == "2.59"

        changed = copy.deepcopy(FPP_BACKUP)
        changed["settings"]["system_settings"][0]["volume"] = "80"
    with respx.mock(assert_all_called=False) as m:
        mock_fpp_page(m)                                  # identical content -> changed False
        mock_falcon(m)
        m.get("http://10.0.0.2/status.xml").respond(text=load("falcon_status.xml").replace("02:18:52", "03:00:00"))
        second, falcon2 = (await client.post("/api/backups/run", json={"host_ids": [h1, h2]})).json()
        assert second["changed"] is False
        assert falcon2["changed"] is False                # uptime in status.xml doesn't count as a change
    with respx.mock(assert_all_called=False) as m:
        mock_fpp_page(m, backup=changed)
        third = (await client.post("/api/backups/run", json={"host_ids": [h1]})).json()[0]
        assert third["changed"] is True

    files = list(root.rglob("*.json.gz"))
    assert len(files) == 5 and all(stat.S_IMODE(os.stat(f).st_mode) == 0o600 for f in files)

    r = await client.get(f"/api/backups/{third['id']}/download")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.json()["settings"]["system_settings"][0]["volume"] == "80"

    d = (await client.get(f"/api/backups/diff?a={res['FPP-PI-1']['id']}&b={third['id']}")).json()
    assert [c["path"] for c in d["changes"]] == ["settings/system_settings/volume"]
    assert (await client.get(f"/api/backups/diff?a={res['FPP-PI-1']['id']}&b={res['F48']['id']}")).status_code == 422

    st = (await client.get("/api/backups/status")).json()
    counts = {h["host_name"]: h["count"] for h in st["hosts"]}
    assert counts == {"F48": 2, "FPP-PI-1": 3} and st["retention"]["daily"] == 14

    areas = (await client.get(f"/api/backups/{third['id']}/areas")).json()["areas"]
    assert set(areas) == {"channelOutputs", "show_setup", "settings", "network"}


async def test_failed_backup_alerts(setup):
    client, svc, h1, h2, root = setup
    with respx.mock(assert_all_called=False) as m:
        m.post("http://10.0.0.1/backup.php").respond(text="<html>PHP error</html>")
        r = (await client.post("/api/backups/run", json={"host_ids": [h1]})).json()[0]
    assert r["status"] == "failed" and "JSON backup" in r["error"]
    snap = (await client.get("/api/snapshot")).json()
    assert any(a["code"] == "backup_failed" and a["host_id"] == h1 for a in snap["alerts"])


async def test_restore_flow(setup):
    client, svc, h1, h2, root = setup
    with respx.mock(assert_all_called=False) as m:
        mock_fpp_page(m)
        b = (await client.post("/api/backups/run", json={"host_ids": [h1]})).json()[0]

    # confirmation is required
    assert (await client.post(f"/api/backups/{b['id']}/restore", json={})).status_code == 422

    calls = []
    with respx.mock(assert_all_called=False) as m:
        mock_fpp_page(m, calls=calls)
        r = await client.post(f"/api/backups/{b['id']}/restore",
                              json={"confirm": True, "keep_network": True, "keep_mode": False})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["restored"] and out["reboot_recommended"] and out["areas"][1] == {"area": "Show Setup", "ok": False}
    assert len(calls) == 2                                    # pre-restore backup, then the restore
    restore_body = calls[1]
    assert b"btnRestoreConfig" in restore_body and b"keepExitingNetwork" in restore_body
    assert b"keepMasterSlave" not in restore_body and b'"fpp_backup_version"' in restore_body
    pre = (await client.get(f"/api/backups/{out['pre_restore_backup_id']}")).json()
    assert pre["trigger"] == "pre-restore" and pre["pinned"] is True

    # platform mismatch is refused unless forced
    bbb = dict(FPP_BACKUP, platform="BeagleBone 64")
    with respx.mock(assert_all_called=False) as m:
        mock_fpp_page(m, backup=bbb)
        r = await client.post(f"/api/backups/{b['id']}/restore", json={"confirm": True})
    assert r.status_code == 409 and "BeagleBone" in r.json()["detail"]

    # Falcon backups can't be restored automatically
    with respx.mock(assert_all_called=False) as m:
        mock_falcon(m)
        fb = (await client.post("/api/backups/run", json={"host_ids": [h2]})).json()[0]
    r = await client.post(f"/api/backups/{fb['id']}/restore", json={"confirm": True})
    assert r.status_code == 409


async def test_pin_delete(setup):
    client, svc, h1, h2, root = setup
    with respx.mock(assert_all_called=False) as m:
        mock_fpp_page(m)
        b = (await client.post("/api/backups/run", json={"host_ids": [h1]})).json()[0]
    r = await client.patch(f"/api/backups/{b['id']}", json={"pinned": True, "note": "before xmas"})
    assert r.json()["pinned"] and r.json()["note"] == "before xmas"
    assert (await client.delete(f"/api/backups/{b['id']}")).status_code == 204
    assert not list(root.rglob("*.json*"))
    assert (await client.get(f"/api/backups/{b['id']}")).status_code == 404


def test_next_run_in_show_timezone():
    from types import SimpleNamespace

    from app.backups.service import BackupService
    from app.config import Settings
    svc = BackupService(SimpleNamespace(settings=Settings(timezone="America/New_York", backup_time="03:00")))
    ny = ZoneInfo("America/New_York")
    # 01:30 New York -> 03:00 the same day
    now = datetime(2026, 11, 4, 1, 30, tzinfo=ny)
    assert svc._next_run_after(now) == datetime(2026, 11, 4, 3, 0, tzinfo=ny)
    # 03:00 exactly -> tomorrow
    assert svc._next_run_after(datetime(2026, 11, 4, 3, 0, tzinfo=ny)).day == 5
    # given in UTC (the VM's zone): 07:59 UTC = 02:59 EST -> 03:00 EST = 08:00 UTC
    nxt = svc._next_run_after(datetime(2026, 11, 4, 7, 59, tzinfo=timezone.utc))
    assert nxt.astimezone(timezone.utc) == datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
    # across the spring-forward DST change, still 03:00 local
    assert svc._next_run_after(datetime(2027, 3, 13, 12, 0, tzinfo=ny)).hour == 3
