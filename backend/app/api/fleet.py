"""Fleet-wide status and actions (e.g. "restart fppd on all")."""
import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .hosts import collector

log = logging.getLogger("fppcc.fleet")
router = APIRouter(prefix="/api/fleet")


@router.get("/status")
async def fleet_status(c=Depends(collector)):
    """One compact row per controller (for Home Assistant templates and scripts)."""
    snap = c.snapshot()
    return [{
        "id": h.id, "name": h.name, "address": h.address, "kind": h.kind, "role": h.role,
        "online": h.status.online, "version": h.status.version, "status": h.status.status,
        "sequence": h.status.current_sequence, "cpu": h.status.cpu_pct, "temp": h.temp_max,
        "disk_pct": h.disk_pct, "uptime_s": h.status.uptime_s, "sync": h.sync,
    } for h in snap.hosts]


class FleetAction(BaseModel):
    host_ids: list[int] | None = None   # omit for every enabled, online controller that supports the action
    confirm: bool = False


@router.post("/actions/{action}")
async def fleet_action(action: str, body: FleetAction, c=Depends(collector)):
    supported = {h.id for h in c.hosts.values()
                 if action in {a.id for a in getattr(c.drivers.get(h.kind), "actions", [])}}
    if not supported:
        raise HTTPException(404, f"no controller supports '{action}'")
    if body.host_ids is None:
        hosts = [h for h in c.hosts.values() if h.id in supported and h.enabled
                 and (c.states.get(h.id) is None or c.states[h.id].online)]
    else:
        unknown = [i for i in body.host_ids if i not in c.hosts]
        if unknown:
            raise HTTPException(404, f"unknown host ids {unknown}")
        hosts = [c.hosts[i] for i in body.host_ids]
        unsupported = [h.name for h in hosts if h.id not in supported]
        if unsupported:
            raise HTTPException(422, f"'{action}' is not available on {', '.join(unsupported)}")
    if not hosts:
        raise HTTPException(422, "no controllers to act on")
    if not body.confirm:
        raise HTTPException(422, f"'{action}' on {len(hosts)} controllers needs confirm: true")

    log.info("fleet action %s on %s", action, [h.name for h in hosts])

    async def one(h):
        try:
            msg = await c.drivers[h.kind].run_action(h.address, action)
            return {"host_id": h.id, "host": h.name, "ok": True, "message": msg}
        except httpx.HTTPError as e:
            return {"host_id": h.id, "host": h.name, "ok": False, "message": f"{type(e).__name__}"}

    results = await asyncio.gather(*(one(h) for h in hosts))
    c.wake()
    return list(results)
