from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..discovery import DiscoveryResult, identify, run_discovery
from ..drivers import KINDS, HostStatus
from ..fleet import HostView, Snapshot
from ..models import Host

router = APIRouter(prefix="/api")


class HostCreate(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=100)
    kind: str | None = None          # auto-detected when omitted
    notes: str | None = None
    enabled: bool = True

    @field_validator("address")
    @classmethod
    def strip_address(cls, v: str) -> str:
        v = v.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
        if not v or "/" in v or " " in v:
            raise ValueError("address must be an IP or hostname")
        return v


class HostUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = None
    kind: str | None = None
    notes: str | None = None
    enabled: bool | None = None

    _strip = field_validator("address")(HostCreate.strip_address.__func__)


def collector(request: Request):
    return request.app.state.collector


def _check_kind(kind: str | None):
    if kind is not None and kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {KINDS}")


@router.get("/snapshot", response_model=Snapshot)
async def get_snapshot(c=Depends(collector)):
    return c.snapshot()


@router.get("/hosts", response_model=list[HostView])
async def list_hosts(c=Depends(collector)):
    snap = c.snapshot()
    shown = {v.id for v in snap.hosts}
    # disabled hosts are not in the dashboard snapshot, but the manage page needs them
    extra = [HostView(**h.model_dump(), status=HostStatus(online=False, error="disabled"))
             for h in c.hosts.values() if h.id not in shown]
    return snap.hosts + sorted(extra, key=lambda v: v.name.lower())


@router.get("/hosts/{host_id}", response_model=HostView)
async def get_host(host_id: int, c=Depends(collector)):
    for v in await list_hosts(c):
        if v.id == host_id:
            return v
    raise HTTPException(404, "host not found")


@router.post("/hosts", response_model=HostView, status_code=201)
async def create_host(body: HostCreate, s: AsyncSession = Depends(get_session), c=Depends(collector)):
    _check_kind(body.kind)
    if await s.scalar(select(Host).where(Host.address == body.address)):
        raise HTTPException(409, f"{body.address} is already in the fleet")
    kind, name, ident = body.kind, body.name, None
    if kind is None or name is None:
        found = await identify(c, body.address)
        if found:
            kind = kind or found[0]
            ident = found[1]
        elif kind is None:
            raise HTTPException(422, f"could not reach {body.address} to detect its type; choose a kind")
    if ident and ident.uuid and await s.scalar(select(Host).where(Host.uuid == ident.uuid)):
        raise HTTPException(409, f"that device ({ident.hostname}) is already in the fleet under another address")
    h = Host(address=body.address, kind=kind, name=name or (ident.hostname if ident else None) or body.address,
             notes=body.notes, enabled=body.enabled, source="manual",
             hostname=ident.hostname if ident else None, uuid=ident.uuid if ident else None,
             role=ident.mode if ident else None, model=ident.model if ident else None)
    s.add(h)
    await s.commit()
    await c.reload_hosts()
    await c.poll_once()
    return await get_host(h.id, c)


@router.patch("/hosts/{host_id}", response_model=HostView)
async def update_host(host_id: int, body: HostUpdate, s: AsyncSession = Depends(get_session), c=Depends(collector)):
    _check_kind(body.kind)
    h = await s.get(Host, host_id)
    if h is None:
        raise HTTPException(404, "host not found")
    data = body.model_dump(exclude_unset=True)
    if "address" in data and data["address"] != h.address:
        if await s.scalar(select(Host).where(Host.address == data["address"])):
            raise HTTPException(409, f"{data['address']} is already in the fleet")
    for k, v in data.items():
        setattr(h, k, v)
    await s.commit()
    await c.reload_hosts()
    c.states.pop(host_id, None)
    c.wake()
    return await get_host(host_id, c)


@router.delete("/hosts/{host_id}", status_code=204)
async def delete_host(host_id: int, s: AsyncSession = Depends(get_session), c=Depends(collector)):
    h = await s.get(Host, host_id)
    if h is None:
        raise HTTPException(404, "host not found")
    await s.delete(h)
    await s.commit()
    await c.reload_hosts()


@router.post("/discovery/run", response_model=DiscoveryResult)
async def discover(c=Depends(collector)):
    return await run_discovery(c)


@router.get("/discovery/last")
async def last_discovery(c=Depends(collector)):
    return c.last_discovery
