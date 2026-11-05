"""Fleet ops: versions and available updates, running FPP/OS updates, and comparing settings across controllers."""
import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from .. import updates
from ..backups.diff import MASK, _is_secret
from ..fleet import find_player
from ..models import Backup
from ..show import FppError
from .hosts import collector
from .jobs import jobs

log = logging.getLogger("fppcc.ops")
router = APIRouter(prefix="/api/fleet")
LOCK = "updates"


def _online(c, h) -> bool:
    st = c.states.get(h.id)
    return st is None or st.online


@router.get("/versions")
async def versions(os: bool = False, refresh: bool = False, c=Depends(collector), mgr=Depends(jobs)):
    """Per controller: FPP version, branch, OS image, commits, available updates and any update in progress.
    os=true also looks up the matching OS image for each platform (asks GitHub through a controller, cached 6 h)."""
    hosts = sorted((h for h in c.hosts.values() if h.enabled), key=lambda h: (h.kind != "fpp", h.name.lower()))
    player = find_player(hosts, c.states)
    rows = await asyncio.gather(*(updates.host_versions(c.client, h, _online(c, h)) for h in hosts))
    for r in rows:
        r["is_player"] = player is not None and r["id"] == player.id
        st = c.states.get(r["id"])
        if r["kind"] != "fpp" and st:
            r["version"] = st.version
            r["platform"] = st.model
    if os:
        for r in rows:
            if r["kind"] == "fpp" and r.get("platform") and r.get("version") and not r.get("error"):
                try:
                    imgs = await updates.os_images(c.client, r["address"], r["platform"], r.get("os_version"), refresh)
                    r["os_update"] = updates.recommended_os(imgs, r["version"], r.get("os_version"))
                    r["os_images"] = [i for i in imgs if not i["prerelease"]][:8]
                except FppError as e:
                    r["os_error"] = str(e)
    fpp = [r for r in rows if r["kind"] == "fpp" and r.get("version")]
    return {
        "hosts": rows,
        "summary": {
            "versions": sorted({r["version"] for r in fpp}),
            "os_versions": sorted({r["os_version"] for r in fpp if r.get("os_version")}),
            "updates_available": sum(1 for r in fpp if r.get("update_available")),
            "upgrades_available": sum(1 for r in fpp if r.get("upgrade")),
            "in_progress": [r["name"] for r in fpp if (r.get("activity") or {}).get("in_progress")],
        },
        "running_job": j.summary() if (j := mgr.running(LOCK)) else None,
    }


class UpdateRequest(BaseModel):
    op: str                               # update / upgrade / os
    host_ids: list[int]
    version: str | None = None            # for upgrade, e.g. "10.4"
    os_image: str | None = None           # for os: filename such as "Pi-10.3_2026-10b.fppos" (per platform: see os_images)
    parallel: int = 1                     # update only; upgrade/os always one at a time
    stop_on_failure: bool = True
    confirm: bool = False
    force: bool = False                   # allowed while the show is playing


@router.post("/update", status_code=202)
async def start_update(body: UpdateRequest, request: Request, c=Depends(collector), mgr=Depends(jobs)):
    """Update FPP / upgrade FPP / upgrade the OS on the chosen controllers, remotes first and the player last.
    Each controller's configuration is backed up first; a failed backup skips that controller."""
    if body.op not in ("update", "upgrade", "os"):
        raise HTTPException(422, "op must be update, upgrade or os")
    if mgr.running(LOCK):
        raise HTTPException(409, "an update job is already running")
    if not body.host_ids:
        raise HTTPException(422, "choose at least one controller")
    unknown = [i for i in body.host_ids if i not in c.hosts]
    if unknown:
        raise HTTPException(404, f"unknown host ids {unknown}")
    hosts = [c.hosts[i] for i in dict.fromkeys(body.host_ids)]
    not_fpp = [h.name for h in hosts if h.kind != "fpp"]
    if not_fpp:
        raise HTTPException(422, f"only FPP controllers can be updated here ({', '.join(not_fpp)}: update manually)")
    offline = [h.name for h in hosts if not _online(c, h)]
    if offline:
        raise HTTPException(409, f"offline: {', '.join(offline)}")

    player = find_player([h for h in c.hosts.values() if h.enabled], c.states)
    pst = c.states.get(player.id) if player else None
    if pst and pst.status == "playing" and not body.force:
        raise HTTPException(409, "the show is playing; updating restarts fppd on each controller. "
                                 "Send force: true to update anyway")

    rows = {r["id"]: r for r in await asyncio.gather(*(updates.host_versions(c.client, h, True) for h in hosts))}
    busy = [h.name for h in hosts if (rows[h.id].get("activity") or {}).get("in_progress")]
    if busy:
        raise HTTPException(409, f"an update is already running on {', '.join(busy)}")

    images: dict[int, dict] = {}
    if body.op == "upgrade":
        if not body.version or not re.fullmatch(r"\d+(\.\d+){1,2}", body.version):
            raise HTTPException(422, "upgrade needs a version such as 10.4")
        major = [h.name for h in hosts if (rows[h.id].get("upgrade") or {}).get("major")]
        if major or (rows[hosts[0].id].get("version") or "").split(".")[0] != body.version.split(".")[0]:
            raise HTTPException(422, "a major version upgrade needs an OS upgrade instead (FPP requires a fresh image)")
    elif body.op == "os":
        if not body.os_image or not body.os_image.endswith(".fppos"):
            raise HTTPException(422, "os needs os_image (an .fppos filename)")
        for h in hosts:
            r = rows[h.id]
            try:
                imgs = await updates.os_images(c.client, h.address, r.get("platform") or "", r.get("os_version"))
            except FppError as e:
                raise HTTPException(e.status, f"{h.name}: {e}")
            img = next((i for i in imgs if i["filename"] == body.os_image), None)
            if img is None:
                raise HTTPException(422, f"{body.os_image} is not an image for {h.name} ({r.get('platform')})")
            images[h.id] = img

    hosts.sort(key=lambda h: player is not None and h.id == player.id)     # the player goes last
    if not body.confirm:
        raise HTTPException(422, f"{body.op} on {len(hosts)} controllers needs confirm: true")

    backups = request.app.state.backups
    label = {"update": "Update FPP", "upgrade": f"Upgrade to FPP {body.version}", "os": "OS upgrade"}[body.op]
    steps = [({"label": h.name, "host_id": h.id, "host": h.name},
              updates.update_step(c.client, backups, h, body.op, version=body.version, image=images.get(h.id)))
             for h in hosts]
    parallel = max(1, min(body.parallel, 4)) if body.op == "update" else 1
    job = mgr.start(f"fpp_{body.op}" if body.op != "os" else "os_upgrade", f"{label} on {len(hosts)} controllers",
                    steps, parallel=parallel, stop_on_failure=body.stop_on_failure, lock=LOCK)
    log.info("%s started on %s (job %s)", body.op, [h.name for h in hosts], job.id)
    c.wake()
    return {"job": job.summary()}


# ---------------------------------------------------------------- settings compare
# Settings that are naturally different on every controller.
PER_HOST = re.compile(r"^(HostName|HostDescription|MQTTClientId|MQTTPrefix|storageDevice|privacyConsent|"
                      r"restartFlag|rebootFlag|initialSetup.*|UUID|.*Serial.*|LastBlock.*|"
                      r"AudioCard\d*Type|AudioMixerDevice|AudioOutput|PipeWire.*|FalconHardwareDetected|"
                      r"piRTC|BBBLed.*|cape.*|LEDDisplayType|UnpartitionedSpace|Variant|SubPlatform|Platform)$", re.I)
EXPECTED_ROLE = {"fppMode"}     # player vs remote


@router.get("/settings")
async def settings_compare(request: Request, host_ids: str | None = Query(None, description="comma-separated; default all FPP"),
                           all: bool = False, c=Depends(collector)):
    """Compare FPP settings across controllers using each one's latest successful backup (taken nightly).
    Rows are settings whose value differs between controllers; secrets are compared but never shown."""
    svc = request.app.state.backups
    ids = [int(x) for x in host_ids.split(",") if x.strip()] if host_ids else \
        [h.id for h in c.hosts.values() if h.enabled and h.kind == "fpp"]
    hosts = [c.hosts[i] for i in ids if i in c.hosts and c.hosts[i].kind == "fpp"]
    if len(hosts) < 2:
        raise HTTPException(422, "pick at least two FPP controllers")

    cols, data = [], {}
    async with c.sessions() as s:
        for h in sorted(hosts, key=lambda h: h.name.lower()):
            b = await s.scalar(select(Backup).where(Backup.host_id == h.id, Backup.status == "ok", Backup.kind == "fpp")
                               .order_by(Backup.created_at.desc()).limit(1))
            col = {"id": h.id, "name": h.name, "backup_id": b.id if b else None,
                   "taken_at": b.created_at if b else None, "version": b.version if b else None}
            cols.append(col)
            if b:
                try:
                    data[h.id] = _settings_map(await asyncio.to_thread(svc.read_json, b))
                except (OSError, ValueError) as e:
                    col["error"] = f"could not read backup: {type(e).__name__}"
    plugin_lists = await asyncio.gather(*(_plugins(c, c.hosts[col["id"]]) for col in cols))
    for col, plugins in zip(cols, plugin_lists):
        if col["id"] in data and plugins is not None:
            data[col["id"]].update({f"plugins/{p}": "installed" for p in plugins})
            for p in _all_plugins(plugin_lists):
                data[col["id"]].setdefault(f"plugins/{p}", "not installed")
    have = [col["id"] for col in cols if col["id"] in data]
    rows = []
    keys = sorted({k for m in data.values() for k in m}, key=lambda k: (k.split("/")[0] != "settings", k.lower()))
    for key in keys:
        vals = {i: data[i].get(key) for i in have}
        differs = len({v for v in vals.values()}) > 1
        name = key.rsplit("/", 1)[-1]
        expected = bool(PER_HOST.match(name)) or name in EXPECTED_ROLE
        if not differs and not all:
            continue
        secret = _is_secret(key)
        rows.append({"key": key, "area": key.split("/", 1)[0], "name": name, "differs": differs,
                     "expected": expected, "secret": secret,
                     "values": {str(i): (MASK if secret and v not in (None, "") else v) for i, v in vals.items()}})
    return {"hosts": cols, "rows": rows}


def _settings_map(backup: dict) -> dict[str, str]:
    """settings/<name> from the backup's system_settings (installed plugins are added live)."""
    out = {}
    s = backup.get("settings") or {}
    ss = s.get("system_settings")
    if isinstance(ss, list) and ss:
        ss = ss[0]
    for k, v in (ss or {}).items() if isinstance(ss, dict) else []:
        out[f"settings/{k}"] = v if isinstance(v, str) else str(v)
    return out


async def _plugins(c, h) -> list[str] | None:
    """Installed plugins, read live (backups only hold plugins that have settings)."""
    if not _online(c, h):
        return None
    try:
        r = await c.client.get(f"http://{h.address}/api/plugin", timeout=10)
        data = r.json() if r.status_code == 200 else None
        return [str(p) for p in data] if isinstance(data, list) else None
    except Exception:      # noqa: BLE001 - plugin rows are a bonus; never fail the compare
        return None


def _all_plugins(lists) -> set[str]:
    return {p for lst in lists if lst for p in lst}
