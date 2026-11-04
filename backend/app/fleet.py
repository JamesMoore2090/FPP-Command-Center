"""Builds the dashboard snapshot: per-host view, sync relative to the player, show banner and live alerts."""
from collections import Counter
from datetime import datetime, timezone

from pydantic import BaseModel

from .config import Settings
from .drivers import HostStatus

SYNC_TOLERANCE_S = 2.0   # polls aren't simultaneous, so allow some slack on seconds_played


class HostInfo(BaseModel):
    """DB fields the collector keeps in memory."""
    id: int
    name: str
    address: str
    kind: str
    hostname: str | None = None
    uuid: str | None = None
    role: str | None = None
    model: str | None = None
    source: str = "manual"
    enabled: bool = True
    notes: str | None = None
    last_seen: datetime | None = None

    model_config = {"from_attributes": True}


class Alert(BaseModel):
    severity: str          # critical / warning / info
    code: str              # offline, temp_high, cpu_high, disk_low, version_mismatch, fppd_stopped, no_player
    message: str
    host_id: int | None = None
    host_name: str | None = None


class HostView(HostInfo):
    status: HostStatus
    sync: str | None = None      # in_sync / drift / out_of_sync / idle / unknown; None if not a remote
    disk_pct: float | None = None
    temp_max: float | None = None


class ShowBanner(BaseModel):
    player_id: int | None = None
    player_name: str | None = None
    player_online: bool = False
    status: str | None = None
    playlist: str | None = None
    sequence: str | None = None
    seconds_played: float | None = None
    seconds_remaining: float | None = None


class Summary(BaseModel):
    total: int
    online: int
    offline: int
    playing: int
    version_majority: str | None = None


class Snapshot(BaseModel):
    ts: datetime
    banner: ShowBanner
    summary: Summary
    alerts: list[Alert]
    hosts: list[HostView]


def compute_sync(remote: HostStatus, player: HostStatus | None) -> str:
    if player is None or not player.online:
        return "unknown"
    if not remote.online:
        return "unknown"
    p_playing = player.status == "playing"
    r_playing = remote.status == "playing"
    if not p_playing:
        return "out_of_sync" if r_playing else "idle"
    if not r_playing or (remote.current_sequence or "") != (player.current_sequence or ""):
        return "out_of_sync"
    if remote.seconds_played is not None and player.seconds_played is not None:
        if abs(remote.seconds_played - player.seconds_played) > SYNC_TOLERANCE_S:
            return "drift"
    return "in_sync"


def find_player(hosts: list[HostInfo], states: dict[int, HostStatus]) -> HostInfo | None:
    players = [h for h in hosts if h.kind == "fpp" and (states.get(h.id) and states[h.id].mode == "player" or h.role == "player")]
    players.sort(key=lambda h: not (states.get(h.id) and states[h.id].online))
    return players[0] if players else None


def build_snapshot(hosts: list[HostInfo], states: dict[int, HostStatus], settings: Settings,
                   extra_alerts: list[Alert] | None = None) -> Snapshot:
    hosts = [h for h in hosts if h.enabled]
    player = find_player(hosts, states)
    pstate = states.get(player.id) if player else None
    alerts: list[Alert] = []

    fpp_versions = [states[h.id].version for h in hosts if h.kind == "fpp" and h.id in states and states[h.id].version]
    reference_version = (pstate.version if pstate and pstate.version else None) or (
        Counter(fpp_versions).most_common(1)[0][0] if fpp_versions else None
    )

    views = []
    for h in sorted(hosts, key=lambda h: (h is not player, h.kind != "fpp", h.name.lower())):
        st = states.get(h.id) or HostStatus(online=False, error="not polled yet")
        sync = compute_sync(st, pstate) if (h.kind == "fpp" and h is not player and (st.mode or h.role) == "remote") else None
        v = HostView(**h.model_dump(), status=st, sync=sync, disk_pct=st.disk_pct, temp_max=st.temp_max)
        views.append(v)
        alerts.extend(host_alerts(v, reference_version, settings))

    if player is None:
        alerts.insert(0, Alert(severity="warning", code="no_player", message="No FPP player found in the fleet"))

    banner = ShowBanner()
    if player:
        banner = ShowBanner(
            player_id=player.id, player_name=player.name, player_online=bool(pstate and pstate.online),
            status=pstate.status if pstate else None,
            playlist=pstate.current_playlist if pstate else None,
            sequence=pstate.current_sequence if pstate else None,
            seconds_played=pstate.seconds_played if pstate else None,
            seconds_remaining=pstate.seconds_remaining if pstate else None,
        )

    online = sum(v.status.online for v in views)
    summary = Summary(
        total=len(views), online=online, offline=len(views) - online,
        playing=sum(v.status.status == "playing" for v in views),
        version_majority=reference_version,
    )
    alerts.extend(extra_alerts or [])
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: sev_rank.get(a.severity, 9))
    return Snapshot(ts=datetime.now(timezone.utc), banner=banner, summary=summary, alerts=alerts, hosts=views)


def host_alerts(v: HostView, reference_version: str | None, s: Settings) -> list[Alert]:
    st, out = v.status, []

    def add(sev, code, msg):
        out.append(Alert(severity=sev, code=code, message=msg, host_id=v.id, host_name=v.name))

    if not st.online:
        if st.error != "not polled yet":
            add("critical", "offline", f"{v.name} is offline" + (f" ({st.error})" if st.error else ""))
        return out
    if st.temp_max is not None and st.temp_max >= s.temp_warn_c:
        add("warning", "temp_high", f"{v.name} temperature {st.temp_max:.0f}°C")
    if st.cpu_pct is not None and st.cpu_pct >= s.cpu_warn_pct:
        add("warning", "cpu_high", f"{v.name} CPU {st.cpu_pct:.0f}%")
    if st.disk_pct is not None and st.disk_pct >= s.disk_warn_pct:
        add("warning", "disk_low", f"{v.name} disk {st.disk_pct:.0f}% full")
    if v.kind == "fpp":
        if st.fppd and st.fppd != "running":
            add("critical", "fppd_stopped", f"{v.name} fppd is {st.fppd}")
        if reference_version and st.version and st.version != reference_version:
            add("warning", "version_mismatch", f"{v.name} runs FPP {st.version}, fleet is on {reference_version}")
    if v.sync in ("out_of_sync", "drift"):
        add("warning", "sync", f"{v.name} is {v.sync.replace('_', ' ')} with the player")
    return out
