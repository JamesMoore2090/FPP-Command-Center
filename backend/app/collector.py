"""Background poller. Runs inside the API process: polls every enabled host, keeps live state in memory,
writes raw samples, updates host metadata, and pushes snapshots to WebSocket clients."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import WebSocket
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import Settings
from .drivers import Driver, HostStatus, make_drivers
from .fleet import HostInfo, Snapshot, build_snapshot
from .models import Host, Sample

log = logging.getLogger("fppcc.collector")


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, payload: str):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


class Collector:
    def __init__(self, settings: Settings, sessions: async_sessionmaker, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.sessions = sessions
        self.client = client or httpx.AsyncClient(timeout=settings.poll_timeout, follow_redirects=True)
        self.drivers: dict[str, Driver] = make_drivers(self.client)
        self.hub = Hub()
        self.hosts: dict[int, HostInfo] = {}
        self.states: dict[int, HostStatus] = {}
        self.failures: dict[int, int] = {}
        self.last_sample = 0.0
        self.last_discovery: dict | None = None
        self.discovery_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # lifecycle -----------------------------------------------------------
    async def start(self):
        await self.reload_hosts()
        self._tasks = [asyncio.create_task(self._poll_loop(), name="poll"),
                       asyncio.create_task(self._maintenance_loop(), name="maintenance")]
        if self.settings.discovery_on_startup or self.settings.discovery_interval > 0:
            self._tasks.append(asyncio.create_task(self._discovery_loop(), name="discovery"))

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.aclose()

    def wake(self):
        """Poll again right away (after host add/edit)."""
        self._wake.set()

    async def reload_hosts(self):
        async with self.sessions() as s:
            rows = (await s.scalars(select(Host))).all()
        self.hosts = {h.id: HostInfo.model_validate(h) for h in rows}
        for hid in set(self.states) - set(self.hosts):
            self.states.pop(hid, None)
            self.failures.pop(hid, None)

    # polling -------------------------------------------------------------
    async def _poll_loop(self):
        while True:
            started = time.monotonic()
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll cycle failed")
            self._wake.clear()
            delay = max(0.5, self.settings.poll_interval - (time.monotonic() - started))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def poll_host(self, host: HostInfo) -> HostStatus:
        driver = self.drivers.get(host.kind)
        if driver is None:
            return HostStatus(online=False, error=f"no driver for kind '{host.kind}'")
        try:
            st = await driver.poll(host.address)
            self.failures[host.id] = 0
            return st
        except Exception as e:  # network, HTTP or parse error
            n = self.failures.get(host.id, 0) + 1
            self.failures[host.id] = n
            prev = self.states.get(host.id)
            err = _describe(e)
            if prev and prev.online and n < self.settings.offline_after_failures:
                return prev.model_copy(update={"error": err})   # tolerate a single blip
            return HostStatus(online=False, error=err, polled_at=datetime.now(timezone.utc))

    async def poll_once(self) -> Snapshot:
        hosts = [h for h in self.hosts.values() if h.enabled]
        results = await asyncio.gather(*(self.poll_host(h) for h in hosts))
        for h, st in zip(hosts, results):
            self.states[h.id] = st
        await self._persist(hosts, results)
        snap = self.snapshot()
        if self.hub.clients:
            await self.hub.broadcast(snap.model_dump_json())
        return snap

    def snapshot(self) -> Snapshot:
        return build_snapshot(list(self.hosts.values()), self.states, self.settings)

    async def _persist(self, hosts: list[HostInfo], results: list[HostStatus]):
        now = datetime.now(timezone.utc)
        write_samples = time.monotonic() - self.last_sample >= self.settings.sample_interval
        changes: dict[int, dict] = {}
        for h, st in zip(hosts, results):
            if not st.online:
                continue
            upd = {}
            for field, value in (("hostname", st.hostname), ("role", st.mode), ("model", st.model), ("uuid", st.uuid)):
                if value and getattr(h, field) != value:
                    upd[field] = value
            if write_samples:
                upd["last_seen"] = now
            if upd:
                changes[h.id] = upd
        if not changes and not write_samples:
            return
        async with self.sessions() as s:
            for hid, upd in changes.items():
                row = await s.get(Host, hid)
                if row is None:
                    continue
                for k, v in upd.items():
                    setattr(row, k, v)
                try:
                    await s.flush()
                except IntegrityError:   # uuid already owned by another host row (duplicate entry)
                    await s.rollback()
                    upd.pop("uuid", None)
                    continue
                self.hosts[hid] = self.hosts[hid].model_copy(update=upd)
            if write_samples:
                for h, st in zip(hosts, results):
                    s.add(Sample(
                        host_id=h.id, ts=now, online=st.online, cpu_pct=st.cpu_pct, mem_pct=st.mem_pct,
                        disk_pct=st.disk_pct, temp_max_c=st.temp_max, status=st.status,
                        data={"temps": [t.model_dump() for t in st.temps],
                              "voltages": [v.model_dump() for v in st.voltages],
                              "sequence": st.current_sequence} if st.online else None,
                    ))
                self.last_sample = time.monotonic()
            await s.commit()

    # background jobs -----------------------------------------------------
    async def _discovery_loop(self):
        from .discovery import run_discovery
        if not self.settings.discovery_on_startup:
            await asyncio.sleep(self.settings.discovery_interval)
        while True:
            try:
                await run_discovery(self)
            except Exception:
                log.exception("discovery failed")
            if self.settings.discovery_interval <= 0:
                return
            await asyncio.sleep(self.settings.discovery_interval)

    async def _maintenance_loop(self):
        while True:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=self.settings.raw_sample_retention_days)
                async with self.sessions() as s:
                    await s.execute(delete(Sample).where(Sample.ts < cutoff))
                    await s.commit()
            except Exception:
                log.exception("sample pruning failed")
            await asyncio.sleep(3600)


def _describe(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, httpx.ConnectError):
        return "connection refused / unreachable"
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return f"{type(e).__name__}: {e}"[:200]
