import httpx
import pytest
import respx

from app.drivers.falcon import parse_strings_xml
from app.drivers.fpp import parse_pixel_outputs

from .conftest import load
from .test_api import client  # noqa: F401  (fixture)


def test_parse_bbb_strings_with_port_status():
    groups = parse_pixel_outputs(load("fpp_bbbstrings.json"), load("fpp_ports_k8pro.json"))
    assert len(groups) == 1
    g = groups[0]
    assert g.name == "K8-Pro pixel strings" and g.ports[0].number == 1
    assert g.ports[0].strings[0].start_channel == 1          # FPP stores 0-based
    assert g.ports[0].protocol == "ws2811"
    assert g.ports[0].enabled is False                        # from fppd/ports


def test_parse_pi_pixel_strings():
    g = parse_pixel_outputs(load("fpp_pixelstrings.json"), None)[0]
    assert g.ports[0].strings[0].description == "GE Rosa Grande 1-str-1"
    assert g.ports[0].strings[0].start_channel == 31150
    assert g.ports[0].pixels == 300 and g.ports[0].current_ma is None


def test_parse_falcon_strings():
    ports = parse_strings_xml(load("falcon_strings.xml"))
    assert len(ports) == 48 and ports[0].number == 1
    assert ports[0].strings[0].description == "GE Fuzion-str-1" and ports[0].pixels == 498


@pytest.fixture
async def fpp_host(client):  # noqa: F811
    with respx.mock(assert_all_called=False) as m:
        m.get("http://10.0.0.1/api/system/info").respond(json=load("fpp_info_remote.json"))
        m.get("http://10.0.0.1/api/fppd/status").respond(json=load("fpp_status_remote.json"))
        r = await client.post("/api/hosts", json={"address": "10.0.0.1"})
    return client, r.json()["id"]


async def test_proxy_forwards_method_query_body(fpp_host):
    client, hid = fpp_host
    with respx.mock(assert_all_called=True) as m:
        g = m.get("http://10.0.0.1/api/file/logs/fppd.log", params={"tail": "50"}).respond(
            text="line1\nline2", headers={"content-type": "text/plain"})
        p = m.post("http://10.0.0.1/api/playlist/x/start").respond(json={"status": "OK"})
        r = await client.get(f"/api/hosts/{hid}/fpp/file/logs/fppd.log?tail=50",
                             headers={"content-type": "application/json"})
        assert "content-type" not in g.calls[0].request.headers   # dropped on bodyless requests
        assert r.status_code == 200 and r.text == "line1\nline2" and r.headers["content-type"] == "text/plain"
        assert r.headers["x-upstream-url"] == "http://10.0.0.1/api/file/logs/fppd.log?tail=50"
        r = await client.post(f"/api/hosts/{hid}/fpp/playlist/x/start", json={"a": 1})
        assert r.json() == {"status": "OK"}
        assert p.calls[0].request.content == b'{"a":1}'
        assert p.calls[0].request.headers["content-type"] == "application/json"
        assert g.called


async def test_proxy_rejects_dotdot_and_reports_upstream_errors(fpp_host):
    client, hid = fpp_host
    assert (await client.get(f"/api/hosts/{hid}/fpp/../etc/passwd")).status_code in (400, 404)
    assert (await client.get(f"/api/hosts/{hid}/fpp/a/%2e%2e/b")).status_code == 400
    with respx.mock() as m:
        m.get("http://10.0.0.1/api/fppd/status").mock(side_effect=httpx.ConnectError("down"))
        assert (await client.get(f"/api/hosts/{hid}/fpp/fppd/status")).status_code == 502
        m.get("http://10.0.0.1/api/slow").mock(side_effect=httpx.ReadTimeout("slow"))
        assert (await client.get(f"/api/hosts/{hid}/fpp/slow")).status_code == 504
    assert (await client.get("/api/hosts/999/fpp/fppd/status")).status_code == 404


async def test_fpp_proxy_only_for_fpp_hosts(client):  # noqa: F811
    with respx.mock(assert_all_called=False) as m:
        m.get("http://10.0.0.2/status.xml").respond(text=load("falcon_status.xml"))
        r = await client.post("/api/hosts", json={"address": "10.0.0.2", "kind": "falcon", "name": "F48"})
        hid = r.json()["id"]
        assert (await client.get(f"/api/hosts/{hid}/fpp/system/info")).status_code == 409
        r = await client.get(f"/api/hosts/{hid}/http/status.xml")
        assert r.status_code == 200 and "<fv>2.59</fv>" in r.text


async def test_actions(fpp_host):
    client, hid = fpp_host
    with respx.mock() as m:
        rs = m.post("http://10.0.0.1/api/system/fppd/restart").respond(text="OK")
        rb = m.post("http://10.0.0.1/api/system/reboot").mock(side_effect=httpx.ReadTimeout("going down"))
        r = await client.post(f"/api/hosts/{hid}/actions/restart_fppd")
        assert r.status_code == 200 and r.json()["ok"] and rs.called
        r = await client.post(f"/api/hosts/{hid}/actions/reboot")
        assert r.status_code == 200 and "sent" in r.json()["message"] and rb.called
    assert (await client.post(f"/api/hosts/{hid}/actions/format_disk")).status_code == 404


async def test_detail_and_catalog(fpp_host):
    client, hid = fpp_host
    with respx.mock(assert_all_called=False) as m:
        m.get("http://10.0.0.1/api/system/info").respond(json=load("fpp_info_remote.json"))
        m.get("http://10.0.0.1/api/channel/output/co-pixelStrings").respond(json=load("fpp_pixelstrings.json"))
        m.get("http://10.0.0.1/api/channel/output/co-bbbStrings").respond(404)
        m.get("http://10.0.0.1/api/channel/output/co-other").respond(json={"channelOutputs": []})
        m.get("http://10.0.0.1/api/fppd/ports").respond(json=[])
        m.get("http://10.0.0.1/api/network/interface").respond(json=[])
        m.get("http://10.0.0.1/api/fppd/warnings").mock(side_effect=httpx.ConnectError("x"))
        d = (await client.get(f"/api/hosts/{hid}/detail")).json()
    assert d["info"]["Cape"] == "K8-Pi v2.0" and d["has_logs"]
    assert d["outputs"][0]["ports"][0]["strings"][0]["pixels"] == 300
    assert d["errors"] == ["fppd/warnings: ConnectError"]
    assert {a["id"] for a in d["actions"]} >= {"restart_fppd", "reboot", "shutdown"}
    cat = (await client.get("/api/fpp/endpoints")).json()
    assert {"method": "POST", "path": "system/reboot", "handler": "RebootDevice"} in cat["endpoints"]
