from app.drivers.falcon import _uptime_seconds, parse_status_xml
from app.drivers.fpp import parse_fpp

from .conftest import load


def test_parse_fpp_remote(fpp_status, fpp_info):
    s = parse_fpp(fpp_status, fpp_info)
    assert s.online and s.hostname == "FPP-PI-1" and s.mode == "remote" and s.version == "10.3"
    assert s.model == "Raspberry Pi 3 Model B Plus Rev 1.3 / K8-Pi"
    assert s.status == "idle" and s.fppd == "running"
    assert [t.label for t in s.temps] == ["CPU", "Temp"]
    assert s.temp_max == 46.2
    assert s.disk_pct == 33.2
    assert s.uuid == "M1-00000000b45cd612"


def test_parse_fpp_without_info(fpp_status):
    s = parse_fpp(fpp_status, None)
    assert s.online and s.cpu_pct is None and s.mode == "remote"


def test_parse_falcon():
    s = parse_status_xml(load("falcon_status.xml"))
    assert s.version == "2.59" and s.hostname == "F48" and s.model == "Falcon F48"
    assert [t.label for t in s.temps] == ["Board"]   # t2=-3, t3=1 are unconnected probes
    assert s.uptime_s == 2 * 86400 + 2 * 3600 + 18 * 60 + 52


def test_uptime_without_days():
    assert _uptime_seconds("05:00:01") == 18001
