from app.config import Settings
from app.discovery import Found, address_rank, merge
from app.drivers import HostStatus, Reading
from app.fleet import HostInfo, build_snapshot, compute_sync

S = Settings()


def st(**kw):
    return HostStatus(online=True, **kw)


def test_sync_states():
    p = st(status="playing", current_sequence="a.fseq", seconds_played=10)
    assert compute_sync(st(status="playing", current_sequence="a.fseq", seconds_played=11), p) == "in_sync"
    assert compute_sync(st(status="playing", current_sequence="a.fseq", seconds_played=20), p) == "drift"
    assert compute_sync(st(status="playing", current_sequence="b.fseq"), p) == "out_of_sync"
    assert compute_sync(st(status="idle"), p) == "out_of_sync"
    assert compute_sync(st(status="idle"), st(status="idle")) == "idle"
    assert compute_sync(st(status="idle"), HostStatus(online=False)) == "unknown"


def test_snapshot_alerts_and_banner():
    hosts = [
        HostInfo(id=1, name="FPPMain", address="a", kind="fpp", role="player"),
        HostInfo(id=2, name="Hot", address="b", kind="fpp", role="remote"),
        HostInfo(id=3, name="Gone", address="c", kind="fpp", role="remote"),
        HostInfo(id=4, name="Old", address="d", kind="fpp", role="remote"),
        HostInfo(id=5, name="Off", address="e", kind="fpp", enabled=False),
    ]
    states = {
        1: st(mode="player", version="10.3", status="playing", current_sequence="x.fseq", seconds_played=5),
        2: st(mode="remote", version="10.3", status="playing", current_sequence="x.fseq", seconds_played=5,
              temps=[Reading(label="CPU", value=80)], cpu_pct=95),
        3: HostStatus(online=False, error="timeout"),
        4: st(mode="remote", version="9.5", status="idle"),
    }
    snap = build_snapshot(hosts, states, S)
    assert snap.banner.player_name == "FPPMain" and snap.banner.sequence == "x.fseq"
    assert [h.name for h in snap.hosts][0] == "FPPMain" and "Off" not in [h.name for h in snap.hosts]
    assert snap.summary.total == 4 and snap.summary.offline == 1 and snap.summary.playing == 2
    codes = {(a.host_name, a.code) for a in snap.alerts}
    assert {("Gone", "offline"), ("Hot", "temp_high"), ("Hot", "cpu_high"),
            ("Old", "version_mismatch"), ("Old", "sync")} <= codes
    assert snap.alerts[0].severity == "critical"
    assert next(h for h in snap.hosts if h.name == "Hot").sync == "in_sync"


def test_no_player_alert():
    snap = build_snapshot([HostInfo(id=1, name="r", address="a", kind="fpp", role="remote")],
                          {1: st(mode="remote")}, S)
    assert snap.alerts[0].code == "no_player"


def test_address_rank_and_merge():
    subnets = ["10.100.100.0/24", "10.55.1.0/24"]
    assert address_rank("10.100.100.5", subnets) == 0
    assert address_rank("10.55.1.5", subnets) == 1
    assert address_rank("10.50.50.22", subnets) == 500
    assert address_rank("fe80::1%wlan0", subnets) >= 1000
    found = [
        Found(address="fe80::1%wlan0", kind="fpp", uuid="U1", via="multisync", model="Pi 4"),
        Found(address="10.55.1.101", kind="fpp", uuid="U1", via="multisync"),
        Found(address="10.100.100.50", kind="fpp", uuid="U1", via="probe", hostname="FPPMain"),
        Found(address="fe80::2%wlan0", kind="fpp", uuid="U2", via="multisync"),
        Found(address="10.55.1.102", kind="falcon", via="probe"),
    ]
    out = merge(found, subnets)
    assert {f.address for f in out} == {"10.100.100.50", "10.55.1.102"}
    main = next(f for f in out if f.uuid == "U1")
    assert main.model == "Pi 4" and main.hostname == "FPPMain"


def test_multisync_fixture_dedupes():
    from .conftest import load
    systems = load("fpp_multisync.json")["systems"]
    found = [Found(address=s["address"], kind="fpp", uuid=s["uuid"], hostname=s["hostname"], via="multisync")
             for s in systems]
    out = merge(found, ["10.100.100.0/24", "10.55.1.0/24"])
    assert sorted(f.address for f in out) == [f"10.100.100.{n}" for n in (50, 51, 52, 53, 54, 56, 57)]


def test_multisync_falcon_and_address_dedupe():
    from app.discovery import kind_from_type_id
    assert kind_from_type_id(13) == "fpp" and kind_from_type_id(135) == "falcon" and kind_from_type_id(0xC2) is None
    found = [
        Found(address="10.55.1.102", kind="falcon", uuid="MAC:5410EC54A162", via="multisync", model="Falcon F48"),
        Found(address="10.55.1.102", kind="falcon", via="probe", hostname="F48"),
    ]
    out = merge(found, ["10.55.1.0/24"])
    assert len(out) == 1 and out[0].via == "probe" and out[0].uuid == "MAC:5410EC54A162"
