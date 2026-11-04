"""The app's own REST API for the show: player-centric, stable and simple enough for Home Assistant and
scripts. Every endpoint acts on the player unless host_id is given."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import show as fpp
from ..fleet import HostInfo, find_player
from .hosts import collector

log = logging.getLogger("fppcc.show")
router = APIRouter(prefix="/api/show")


def target(c, host_id: int | None) -> HostInfo:
    if host_id is not None:
        h = c.hosts.get(host_id)
        if h is None:
            raise HTTPException(404, "host not found")
    else:
        h = find_player([x for x in c.hosts.values() if x.enabled], c.states)
        if h is None:
            raise HTTPException(409, "no FPP player in the fleet")
    if h.kind != "fpp":
        raise HTTPException(409, f"{h.name} is not an FPP controller")
    return h


async def call(coro):
    try:
        return await coro
    except fpp.FppError as e:
        raise HTTPException(e.status, str(e))


def backups(request: Request):
    return request.app.state.backups


# ---------------------------------------------------------------- status
@router.get("/status")
async def show_status(c=Depends(collector)):
    """Flat show status for Home Assistant: player state, what's playing, fleet health."""
    snap = c.snapshot()
    b = snap.banner
    return {
        "player": b.player_name, "player_online": b.player_online, "status": b.status or "unknown",
        "playing": b.status == "playing", "playlist": b.playlist, "sequence": b.sequence,
        "seconds_played": b.seconds_played, "seconds_remaining": b.seconds_remaining,
        "controllers_total": snap.summary.total, "controllers_online": snap.summary.online,
        "controllers_offline": snap.summary.offline,
        "out_of_sync": [h.name for h in snap.hosts if h.sync in ("out_of_sync", "drift")],
        "alerts": len(snap.alerts), "critical_alerts": sum(a.severity == "critical" for a in snap.alerts),
        "version": snap.summary.version_majority,
    }


# ---------------------------------------------------------------- playlists & transport
@router.get("/playlists")
async def get_playlists(host_id: int | None = None, c=Depends(collector)):
    h = target(c, host_id)
    return {"host_id": h.id, **await call(fpp.playlists(c.client, h.address))}


@router.get("/playlists/{name}")
async def get_playlist(name: str, host_id: int | None = None, c=Depends(collector)):
    h = target(c, host_id)
    return await call(fpp.playlist_detail(c.client, h.address, name))


class StartRequest(BaseModel):
    name: str = Field(min_length=1, description="Playlist name, or a sequence file such as 'Song.fseq'")
    repeat: bool = False
    if_not_running: bool = False
    host_id: int | None = None


@router.post("/start")
async def start(body: StartRequest, c=Depends(collector)):
    h = target(c, body.host_id)
    log.info("start '%s' on %s (repeat=%s)", body.name, h.name, body.repeat)
    out = await call(fpp.run_command(c.client, h.address, "Start Playlist",
                                     [body.name, str(body.repeat).lower(), str(body.if_not_running).lower(), "false"]))
    c.wake()
    return {"ok": True, "host": h.name, "message": out}


class StopRequest(BaseModel):
    mode: str = Field("graceful", pattern="^(graceful|now|after_loop)$")
    host_id: int | None = None


@router.post("/stop")
async def stop(body: StopRequest, c=Depends(collector)):
    h = target(c, body.host_id)
    path = {"graceful": "playlists/stopgracefully", "now": "playlists/stop",
            "after_loop": "playlists/stopgracefullyafterloop"}[body.mode]
    log.info("stop (%s) on %s", body.mode, h.name)
    await call(fpp.post(c.client, h.address, path))
    c.wake()
    return {"ok": True, "host": h.name}


class HostOnly(BaseModel):
    host_id: int | None = None


async def _transport(action: str, body: HostOnly | None, c) -> dict:
    h = target(c, body.host_id if body else None)
    if action in ("pause", "resume"):
        await call(fpp.post(c.client, h.address, f"playlists/{action}"))
    else:
        await call(fpp.run_command(c.client, h.address, "Next Playlist Item" if action == "next" else "Prev Playlist Item", []))
    c.wake()
    return {"ok": True, "host": h.name}


@router.post("/pause")
async def pause(body: HostOnly | None = None, c=Depends(collector)):
    return await _transport("pause", body, c)


@router.post("/resume")
async def resume(body: HostOnly | None = None, c=Depends(collector)):
    return await _transport("resume", body, c)


@router.post("/next")
async def next_item(body: HostOnly | None = None, c=Depends(collector)):
    return await _transport("next", body, c)


@router.post("/prev")
async def prev_item(body: HostOnly | None = None, c=Depends(collector)):
    return await _transport("prev", body, c)


# ---------------------------------------------------------------- volume
@router.get("/volume")
async def get_volume(host_id: int | None = None, c=Depends(collector)):
    h = target(c, host_id)
    d = await call(fpp.get_json(c.client, h.address, "system/volume"))
    return {"host": h.name, "volume": d.get("volume")}


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)
    host_id: int | None = None


@router.post("/volume")
async def set_volume(body: VolumeRequest, c=Depends(collector)):
    h = target(c, body.host_id)
    await call(fpp.post(c.client, h.address, "system/volume", json={"volume": body.volume}))
    c.wake()
    return {"ok": True, "host": h.name, "volume": body.volume}


# ---------------------------------------------------------------- schedule
@router.get("/schedule")
async def get_schedule(host_id: int | None = None, c=Depends(collector)):
    h = target(c, host_id)
    return {"host_id": h.id, "host": h.name, **await call(fpp.schedule(c.client, h.address))}


class ScheduleSave(BaseModel):
    entries: list[dict]                 # FPP schedule.json entries, unknown keys are kept as sent
    host_id: int | None = None
    confirm: bool = False


@router.put("/schedule")
async def put_schedule(body: ScheduleSave, c=Depends(collector), svc=Depends(backups)):
    if not body.confirm:
        raise HTTPException(422, "saving replaces the player's schedule; send confirm: true")
    h = target(c, body.host_id)
    for i, e in enumerate(body.entries):
        if not isinstance(e.get("day"), int) or not e.get("startTime"):
            raise HTTPException(422, f"entry {i + 1} needs a day and a start time")
        if not e.get("command") and not e.get("playlist"):
            raise HTTPException(422, f"entry {i + 1} needs a playlist or a command")
    pre = await svc.backup_host(h, "pre-change", note="Taken automatically before a schedule change")
    if pre.status != "ok":
        raise HTTPException(409, f"could not back up {h.name} before changing its schedule ({pre.error})")
    log.info("saving schedule on %s (%d entries)", h.name, len(body.entries))
    await call(fpp.save_schedule(c.client, h.address, body.entries))
    return {"ok": True, "pre_change_backup_id": pre.id, **await call(fpp.schedule(c.client, h.address))}


# ---------------------------------------------------------------- commands
@router.get("/commands")
async def get_commands(host_id: int | None = None, c=Depends(collector)):
    h = target(c, host_id)
    cmds, presets = await asyncio.gather(fpp.get_json(c.client, h.address, "commands"),
                                         fpp.get_json(c.client, h.address, "configfile/commandPresets.json"),
                                         return_exceptions=True)
    if isinstance(cmds, Exception):
        raise HTTPException(502, str(cmds))
    preset_list = presets.get("commands", []) if isinstance(presets, dict) else []
    return {"host_id": h.id, "commands": cmds, "presets": preset_list}


class CommandRequest(BaseModel):
    command: str = Field(min_length=1)
    args: list[str | int | float | bool] = []
    host_id: int | None = None
    multisync: bool = False           # also run it on the other FPP systems in the MultiSync group


@router.post("/command")
async def command(body: CommandRequest, c=Depends(collector)):
    h = target(c, body.host_id)
    args = [str(a).lower() if isinstance(a, bool) else str(a) for a in body.args]
    log.info("command '%s' %s on %s (multisync=%s)", body.command, args, h.name, body.multisync)
    out = await call(fpp.run_command(c.client, h.address, body.command, args, multisync=body.multisync))
    c.wake()
    return {"ok": True, "host": h.name, "message": out}


# ---------------------------------------------------------------- test mode
def fpp_hosts(c, host_ids: list[int] | None) -> list[HostInfo]:
    if host_ids is None:
        hosts = [h for h in c.hosts.values() if h.enabled and h.kind == "fpp"
                 and (c.states.get(h.id) is None or c.states[h.id].online)]
    else:
        missing = [i for i in host_ids if i not in c.hosts]
        if missing:
            raise HTTPException(404, f"unknown host ids {missing}")
        hosts = [c.hosts[i] for i in host_ids]
        bad = [h.name for h in hosts if h.kind != "fpp"]
        if bad:
            raise HTTPException(422, f"not FPP controllers: {', '.join(bad)}")
    if not hosts:
        raise HTTPException(422, "no controllers selected")
    return hosts


async def per_host(hosts: list[HostInfo], fn) -> list[dict]:
    async def one(h):
        try:
            return {"host_id": h.id, "host": h.name, "ok": True, "message": await fn(h)}
        except fpp.FppError as e:
            return {"host_id": h.id, "host": h.name, "ok": False, "message": str(e)}
    return list(await asyncio.gather(*(one(h) for h in hosts)))


@router.get("/test")
async def test_status(c=Depends(collector)):
    hosts = [h for h in c.hosts.values() if h.enabled and h.kind == "fpp" and c.states.get(h.id) and c.states[h.id].online]

    async def st(h):
        d = await fpp.test_state(c.client, h.address)
        return "on" if d.get("enabled") else "off"
    return await per_host(hosts, st)


@router.get("/test/patterns")
async def test_patterns(host_id: int | None = None, c=Depends(collector)):
    h = target(c, host_id)
    names = await call(fpp.get_json(c.client, h.address, "fppd/testing/tests/"))
    return {"patterns": names}


@router.get("/test/patterns/{pattern}")
async def test_pattern(pattern: str, host_id: int | None = None, c=Depends(collector)):
    h = target(c, host_id)
    return {"args": await call(fpp.test_pattern_args(c.client, h.address, pattern))}


class TestStart(BaseModel):
    pattern: str = "RGB Chase"
    interval_ms: int = Field(1000, ge=100, le=10000)
    args: list[str] = []                 # blank entries use each controller's own default (its channel range)
    host_ids: list[int] | None = None    # omit for every online FPP controller


@router.post("/test/start")
async def test_start(body: TestStart, c=Depends(collector)):
    hosts = fpp_hosts(c, body.host_ids)
    log.info("test mode '%s' on %s", body.pattern, [h.name for h in hosts])
    return await per_host(hosts, lambda h: fpp.test_start(c.client, h.address, body.pattern, body.interval_ms, body.args))


class HostIds(BaseModel):
    host_ids: list[int] | None = None


@router.post("/test/stop")
async def test_stop(body: HostIds, c=Depends(collector)):
    hosts = fpp_hosts(c, body.host_ids)
    return await per_host(hosts, lambda h: fpp.run_command(c.client, h.address, "Test Stop", []))
