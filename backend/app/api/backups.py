from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..backups import devices
from ..backups.diff import diff_backups
from ..backups.service import BackupService, RestoreRefused
from ..db import get_session
from ..models import Backup
from .hosts import collector

router = APIRouter(prefix="/api/backups")


def service(request: Request) -> BackupService:
    return request.app.state.backups


class BackupOut(BaseModel):
    id: int
    host_id: int | None
    host_name: str
    kind: str
    created_at: datetime
    trigger: str
    status: str
    error: str | None
    size: int | None
    changed: bool | None
    version: str | None
    platform: str | None
    pinned: bool
    note: str | None

    model_config = {"from_attributes": True}


class HostBackupStatus(BaseModel):
    host_id: int
    host_name: str
    kind: str
    enabled: bool
    running: bool
    last_status: str | None = None
    last_error: str | None = None
    last_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_ok_id: int | None = None
    count: int = 0


class BackupStatus(BaseModel):
    enabled: bool
    schedule: str
    timezone: str
    next_run: datetime | None
    last_run: dict | None
    retention: dict
    hosts: list[HostBackupStatus]


async def _get(backup_id: int, s: AsyncSession) -> Backup:
    b = await s.get(Backup, backup_id)
    if b is None:
        raise HTTPException(404, "backup not found")
    return b


@router.get("", response_model=list[BackupOut])
async def list_backups(host_id: int | None = None, limit: int = Query(200, le=2000),
                       s: AsyncSession = Depends(get_session)):
    q = select(Backup).order_by(Backup.created_at.desc()).limit(limit)
    if host_id is not None:
        q = q.where(Backup.host_id == host_id)
    return (await s.scalars(q)).all()


@router.get("/status", response_model=BackupStatus)
async def backup_status(svc: BackupService = Depends(service), c=Depends(collector),
                        s: AsyncSession = Depends(get_session)):
    counts: dict[int, int] = {}
    for hid in (await s.scalars(select(Backup.host_id).where(Backup.status == "ok"))).all():
        counts[hid] = counts.get(hid, 0) + 1
    hosts = []
    for h in sorted(c.hosts.values(), key=lambda h: h.name.lower()):
        if h.kind not in ("fpp", "falcon"):
            continue
        info = svc.latest.get(h.id, {})
        hosts.append(HostBackupStatus(host_id=h.id, host_name=h.name, kind=h.kind, enabled=h.enabled,
                                      running=h.id in svc.running, count=counts.get(h.id, 0), **info))
    st = svc.settings
    return BackupStatus(
        enabled=st.backup_enabled, schedule=f"daily at {st.backup_time}", timezone=st.timezone,
        next_run=svc.next_run, last_run=svc.last_run,
        retention={"daily": st.backup_keep_daily, "weekly": st.backup_keep_weekly, "monthly": st.backup_keep_monthly},
        hosts=hosts,
    )


class RunRequest(BaseModel):
    host_ids: list[int] | None = None   # omit for the whole fleet


@router.post("/run", response_model=list[BackupOut])
async def run_backups(body: RunRequest, svc: BackupService = Depends(service), c=Depends(collector)):
    if body.host_ids is None:
        hosts = [h for h in c.hosts.values() if h.enabled and h.kind in ("fpp", "falcon")]
    else:
        missing = [i for i in body.host_ids if i not in c.hosts]
        if missing:
            raise HTTPException(404, f"unknown host ids {missing}")
        hosts = [c.hosts[i] for i in body.host_ids]
    if not hosts:
        raise HTTPException(422, "no hosts to back up")
    results = await svc.backup_many(hosts, "manual")
    return results


@router.get("/diff")
async def diff(a: int, b: int, s: AsyncSession = Depends(get_session), svc: BackupService = Depends(service)):
    """Compare two backups (same controller over time, or two controllers)."""
    ba, bb = await _get(a, s), await _get(b, s)
    if ba.kind != bb.kind:
        raise HTTPException(422, "can only compare backups of the same kind of controller")
    try:
        result = diff_backups(svc.read_json(ba), svc.read_json(bb))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(410, f"backup file unavailable: {e}")
    return {"a": BackupOut.model_validate(ba), "b": BackupOut.model_validate(bb), **result}


@router.get("/{backup_id}", response_model=BackupOut)
async def get_backup(backup_id: int, s: AsyncSession = Depends(get_session)):
    return await _get(backup_id, s)


@router.get("/{backup_id}/areas")
async def backup_areas(backup_id: int, s: AsyncSession = Depends(get_session), svc: BackupService = Depends(service)):
    b = await _get(backup_id, s)
    if b.kind != "fpp" or b.status != "ok":
        return {"areas": []}
    return {"areas": devices.restore_areas(svc.read_json(b))}


@router.get("/{backup_id}/download")
async def download(backup_id: int, s: AsyncSession = Depends(get_session), svc: BackupService = Depends(service)):
    b = await _get(backup_id, s)
    try:
        content = svc.read(b)
    except FileNotFoundError as e:
        raise HTTPException(410, f"backup file unavailable: {e}")
    name = Path(b.path).name.removesuffix(".gz") if b.path else f"backup-{b.id}.json"
    # FPP's own restore page accepts this file as-is (same name pattern as FPP's downloads).
    if b.kind == "fpp":
        local = b.created_at.replace(tzinfo=b.created_at.tzinfo or timezone.utc).astimezone(svc.tz)
        name = f"{b.host_name}_all-backup_fppcc_{local:%Y%m%d%H%M%S}.json"
    return Response(content, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


class BackupPatch(BaseModel):
    pinned: bool | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.patch("/{backup_id}", response_model=BackupOut)
async def update_backup(backup_id: int, body: BackupPatch, s: AsyncSession = Depends(get_session)):
    b = await _get(backup_id, s)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    await s.commit()
    return b


@router.delete("/{backup_id}", status_code=204)
async def delete_backup(backup_id: int, s: AsyncSession = Depends(get_session), svc: BackupService = Depends(service)):
    b = await _get(backup_id, s)
    await svc.delete(b, s)
    await s.commit()
    await svc.refresh_latest()


class RestoreRequest(BaseModel):
    target_host_id: int | None = None   # default: the controller the backup came from
    area: str = "all"
    keep_network: bool = True           # keep the target's current IP/WiFi settings
    keep_mode: bool = True              # keep the target's current player/remote mode
    allow_platform_mismatch: bool = False
    confirm: bool = False               # must be true: restores overwrite the controller's configuration


@router.post("/{backup_id}/restore")
async def restore(backup_id: int, body: RestoreRequest, s: AsyncSession = Depends(get_session),
                  svc: BackupService = Depends(service), c=Depends(collector)):
    if not body.confirm:
        raise HTTPException(422, "restore overwrites the controller's configuration; send confirm: true")
    b = await _get(backup_id, s)
    if b.status != "ok":
        raise HTTPException(422, "only successful backups can be restored")
    target_id = body.target_host_id if body.target_host_id is not None else b.host_id
    target = c.hosts.get(target_id) if target_id is not None else None
    if target is None:
        raise HTTPException(404, "target controller not found; pass target_host_id")
    try:
        return await svc.restore(b, target, body.area, body.keep_network, body.keep_mode, body.allow_platform_mismatch)
    except RestoreRefused as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(410, f"backup file unavailable: {e}")
    except Exception as e:  # network errors from the controller
        raise HTTPException(502, f"restore request to {target.name} failed: {type(e).__name__}: {e}")
