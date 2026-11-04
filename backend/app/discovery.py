"""Finds controllers via FPP MultiSync (from every known FPP host) plus an HTTP probe of the configured subnets,
then adds new devices and follows known devices (matched by UUID) that changed address."""
import asyncio
import ipaddress
import logging
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel
from sqlalchemy import select

from .drivers import HostStatus
from .models import Host

log = logging.getLogger("fppcc.discovery")


class Found(BaseModel):
    address: str
    kind: str
    hostname: str | None = None
    uuid: str | None = None
    mode: str | None = None
    model: str | None = None
    version: str | None = None
    via: str                     # multisync / probe
    action: str = "known"        # added / moved / updated / known / disabled


class DiscoveryResult(BaseModel):
    started_at: datetime
    finished_at: datetime
    found: list[Found]
    added: int
    moved: int
    errors: list[str]


def kind_from_type_id(type_id) -> str | None:
    """FPP MultiSync typeId ranges: 0x01-0x7F FPP, 0x80-0x8F Falcon. Others (ESPixelStick, WLED...) have no driver yet."""
    try:
        t = int(type_id)
    except (TypeError, ValueError):
        return "fpp"
    if 0x01 <= t <= 0x7F:
        return "fpp"
    if 0x80 <= t <= 0x8F:
        return "falcon"
    return None


def address_rank(addr: str, subnets: list[str]) -> int:
    """Lower is better: position of the first configured subnet containing addr; unusable addresses last."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return 1000
    if ip.version != 4 or ip.is_link_local or ip.is_loopback:
        return 2000
    for i, net in enumerate(subnets):
        if ip in ipaddress.ip_network(net, strict=False):
            return i
    return 500


async def identify(collector, address: str) -> tuple[str, HostStatus] | None:
    t = collector.settings.probe_timeout
    for kind in ("fpp", "falcon"):
        try:
            st = await collector.drivers[kind].identify(address, timeout=t)
        except httpx.TransportError:
            return None   # nothing listening; don't try the other drivers on the same port
        except Exception:
            continue
        if st:
            return kind, st
    return None


async def _probe_subnets(collector, errors: list[str]) -> list[Found]:
    sem = asyncio.Semaphore(collector.settings.probe_concurrency)
    addrs = []
    for net in collector.settings.subnets:
        try:
            n = ipaddress.ip_network(net, strict=False)
        except ValueError:
            errors.append(f"bad subnet {net}")
            continue
        if n.num_addresses > 1024:
            errors.append(f"subnet {net} too large to probe (max /22)")
            continue
        addrs.extend(str(a) for a in n.hosts())

    async def one(a):
        async with sem:
            r = await identify(collector, a)
        if r:
            kind, st = r
            return Found(address=a, kind=kind, hostname=st.hostname, uuid=st.uuid, mode=st.mode,
                         model=st.model, version=st.version, via="probe")

    return [f for f in await asyncio.gather(*(one(a) for a in addrs)) if f]


async def _multisync(collector, probed: list[Found], errors: list[str]) -> list[Found]:
    # sources: known FPP hosts plus anything the probe just found; ask the player first
    sources = {h.address: h.role for h in collector.hosts.values() if h.kind == "fpp" and h.enabled}
    for f in probed:
        if f.kind == "fpp":
            sources.setdefault(f.address, f.mode)
    ordered = sorted(sources, key=lambda a: sources[a] != "player")
    out = []
    for addr in ordered[:3]:
        try:
            systems = await collector.drivers["fpp"].multisync_systems(addr)
        except Exception as e:
            errors.append(f"multisync via {addr}: {type(e).__name__}")
            continue
        for s in systems:
            kind = kind_from_type_id(s.get("typeId"))
            if not s.get("address") or kind is None:
                continue
            out.append(Found(address=s["address"], kind=kind, hostname=s.get("hostname"), uuid=s.get("uuid"),
                             mode=s.get("fppModeString"), model=s.get("model"), version=s.get("version"),
                             via="multisync"))
        if out:
            break
    return out


def merge(found: list[Found], subnets: list[str]) -> list[Found]:
    """One entry per device: group by UUID (FPP) or address (others), pick the best address."""
    groups: dict[str, list[Found]] = {}
    for f in found:
        groups.setdefault(f.uuid or f"addr:{f.address}", []).append(f)
    out = []
    for items in groups.values():
        items.sort(key=lambda f: (address_rank(f.address, subnets), f.via != "probe"))
        best = items[0].model_copy()
        if address_rank(best.address, subnets) >= 1000:
            continue   # only link-local/IPv6 addresses known
        # fill gaps from other sightings (multisync knows model names the probe may lack, etc.)
        for other in items[1:]:
            for k in ("hostname", "mode", "model", "version"):
                if getattr(best, k) is None and getattr(other, k) is not None:
                    setattr(best, k, getattr(other, k))
        out.append(best)

    # Second pass: the same address seen under different keys (e.g. a Falcon with a MultiSync MAC uuid and a
    # probe sighting without one). The probe result wins because it detected the protocol directly.
    by_addr: dict[str, Found] = {}
    for f in out:
        prev = by_addr.get(f.address)
        if prev is None:
            by_addr[f.address] = f
            continue
        keep, other = (f, prev) if f.via == "probe" and prev.via != "probe" else (prev, f)
        for k in ("uuid", "hostname", "mode", "model", "version"):
            if getattr(keep, k) is None and getattr(other, k) is not None:
                setattr(keep, k, getattr(other, k))
        by_addr[f.address] = keep
    return list(by_addr.values())


async def run_discovery(collector) -> DiscoveryResult:
    async with collector.discovery_lock:
        started = datetime.now(timezone.utc)
        errors: list[str] = []
        subnets = collector.settings.subnets
        probed = await _probe_subnets(collector, errors)
        ms = await _multisync(collector, probed, errors)
        found = merge(ms + probed, subnets)

        # multisync-only devices on an unranked address: verify they answer before adding
        verified = []
        for f in found:
            if f.via == "multisync" and address_rank(f.address, subnets) >= 500:
                if not await identify(collector, f.address):
                    continue
            verified.append(f)
        found = verified

        added = moved = updated = 0
        async with collector.sessions() as s:
            hosts = (await s.scalars(select(Host))).all()
            by_uuid = {h.uuid: h for h in hosts if h.uuid}
            by_addr = {h.address: h for h in hosts}
            for f in found:
                h = (by_uuid.get(f.uuid) if f.uuid else None) or by_addr.get(f.address)
                if h is None:
                    h = Host(name=f.hostname or f.address, address=f.address, kind=f.kind, hostname=f.hostname,
                             uuid=f.uuid, role=f.mode, model=f.model, source="discovered", enabled=True)
                    s.add(h)
                    by_addr[f.address] = h
                    if f.uuid:
                        by_uuid[f.uuid] = h
                    f.action = "added"
                    added += 1
                elif not h.enabled:
                    f.action = "disabled"
                elif f.via == "probe" and h.kind != f.kind and h.source == "discovered":
                    log.info("host %s kind corrected %s -> %s", h.name, h.kind, f.kind)
                    h.kind = f.kind
                    f.action = "updated"
                    updated += 1
                elif h.address != f.address and not _online(collector, h.id) and f.address not in by_addr:
                    log.info("host %s moved %s -> %s", h.name, h.address, f.address)
                    by_addr.pop(h.address, None)
                    h.address = f.address
                    by_addr[f.address] = h
                    f.action = "moved"
                    moved += 1
            await s.commit()

        result = DiscoveryResult(started_at=started, finished_at=datetime.now(timezone.utc), found=found,
                                 added=added, moved=moved, errors=errors)
        collector.last_discovery = result.model_dump(mode="json")
        if added or moved or updated:
            await collector.reload_hosts()
            collector.wake()
        log.info("discovery: %d found, %d added, %d moved", len(found), added, moved)
        return result


def _online(collector, host_id: int) -> bool:
    st = collector.states.get(host_id)
    return bool(st and st.online)
