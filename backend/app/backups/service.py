"""Runs backups (on demand and nightly), stores them, applies retention, restores, and reports status."""
import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..fleet import Alert, HostInfo
from ..models import Backup
from . import devices
from .retention import Item, select_to_delete

log = logging.getLogger("fppcc.backups")
CONCURRENCY = 3


class BackupService:
    def __init__(self, collector):
        self.c = collector
        self.settings = collector.settings
        self.tz = ZoneInfo(self.settings.timezone)
        self.root = Path(self.settings.backup_dir)
        self.lock = asyncio.Lock()            # one fleet run at a time
        self.host_locks: dict[int, asyncio.Lock] = {}
        self.running: set[int] = set()        # host ids with a backup in progress
        self.next_run: datetime | None = None
        self.last_run: dict | None = None
        self._task: asyncio.Task | None = None
        # latest attempt per host (for alerts), loaded from the DB at start
        self.latest: dict[int, dict] = {}

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        await self.refresh_latest()
        if self.settings.backup_enabled:
            self._task = asyncio.create_task(self._schedule_loop(), name="backup-schedule")

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    # ------------------------------------------------------------ storage
    def _ensure_root(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _write(self, host: HostInfo, created: datetime, content: bytes) -> str:
        self._ensure_root()
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", host.hostname or host.name)
        rel = (f"{host.id:03d}-{safe}/{created.astimezone(self.tz):%Y%m%d-%H%M%S}-"
               f"{secrets.token_hex(3)}-{host.kind}.json.gz")   # gzip: FPP layouts make some backups ~10 MB
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_EXCL: never overwrite another backup. 0600: contains passwords / WiFi keys.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(gzip.compress(content, compresslevel=6))
        return rel

    def read(self, b: Backup) -> bytes:
        if not b.path:
            raise FileNotFoundError("backup has no file")
        path = (self.root / b.path).resolve()
        if self.root.resolve() not in path.parents:
            raise FileNotFoundError("bad backup path")
        data = path.read_bytes()
        return gzip.decompress(data) if path.suffix == ".gz" else data

    def read_json(self, b: Backup) -> dict:
        return json.loads(self.read(b))

    # ------------------------------------------------------------ taking backups
    async def backup_host(self, host: HostInfo, trigger: str, note: str | None = None, pinned: bool = False) -> Backup:
        lock = self.host_locks.setdefault(host.id, asyncio.Lock())
        async with lock:
            self.running.add(host.id)
            try:
                return await self._backup_host(host, trigger, note, pinned)
            finally:
                self.running.discard(host.id)

    async def _backup_host(self, host: HostInfo, trigger: str, note: str | None, pinned: bool) -> Backup:
        created = datetime.now(timezone.utc)
        rec = Backup(host_id=host.id, host_name=host.name, kind=host.kind, created_at=created, trigger=trigger,
                     note=note, pinned=pinned)
        st = self.c.states.get(host.id)
        if trigger == "scheduled" and st is not None and not st.online:
            rec.status, rec.error = "skipped", "controller offline"
        else:
            try:
                if host.kind == "fpp":
                    taken = await devices.fpp_backup(self.c.client, host.address, st.version if st else None)
                elif host.kind == "falcon":
                    taken = await devices.falcon_backup(self.c.client, host.address)
                else:
                    raise devices.BackupError(f"no backup support for kind '{host.kind}'")
                data = json.loads(taken.content)
                digest = hashlib.sha256(json.dumps(devices.normalized(data), sort_keys=True).encode()).hexdigest()
                rec.path = await asyncio.to_thread(self._write, host, created, taken.content)
                rec.size, rec.sha256 = len(taken.content), digest
                rec.version, rec.platform = taken.version, taken.platform
                rec.status = "ok"
            except (httpx.HTTPError, devices.BackupError, ValueError, OSError) as e:
                rec.status = "failed"
                rec.error = _describe(e)
                log.warning("backup of %s failed: %s", host.name, rec.error)

        async with self.c.sessions() as s:
            if rec.status == "ok":
                prev = await s.scalar(select(Backup).where(Backup.host_id == host.id, Backup.status == "ok")
                                      .order_by(Backup.created_at.desc()).limit(1))
                rec.changed = prev is None or prev.sha256 != rec.sha256
            s.add(rec)
            await s.commit()
            await s.refresh(rec)
        self.latest[host.id] = _summary(rec, self.latest.get(host.id))
        return rec

    async def backup_many(self, hosts: list[HostInfo], trigger: str) -> list[Backup]:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(h):
            async with sem:
                return await self.backup_host(h, trigger)

        results = await asyncio.gather(*(one(h) for h in hosts))
        await self.prune()
        return list(results)

    async def run_fleet(self, trigger: str = "scheduled") -> dict:
        async with self.lock:
            started = datetime.now(timezone.utc)
            hosts = [h for h in self.c.hosts.values() if h.enabled and h.kind in ("fpp", "falcon")]
            results = await self.backup_many(hosts, trigger)
            self.last_run = {
                "trigger": trigger, "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": sum(r.status == "ok" for r in results),
                "failed": sum(r.status == "failed" for r in results),
                "skipped": sum(r.status == "skipped" for r in results),
            }
            log.info("fleet backup (%s): %s", trigger, self.last_run)
            return self.last_run

    # ------------------------------------------------------------ retention
    async def prune(self) -> int:
        s_ = self.settings
        now = datetime.now(timezone.utc)
        removed = 0
        async with self.c.sessions() as s:
            rows = (await s.scalars(select(Backup))).all()
            by_host: dict[object, list[Backup]] = {}
            for b in rows:
                by_host.setdefault(b.host_id if b.host_id is not None else f"gone:{b.host_name}", []).append(b)
            for items in by_host.values():
                doomed = select_to_delete(
                    [Item(b.id, _aware(b.created_at), b.status, b.pinned) for b in items],
                    self.tz, s_.backup_keep_daily, s_.backup_keep_weekly, s_.backup_keep_monthly, now)
                for b in items:
                    if b.id in doomed:
                        self._unlink(b)
                        await s.delete(b)
                        removed += 1
            await s.commit()
        if removed:
            log.info("retention removed %d backups", removed)
        return removed

    def _unlink(self, b: Backup):
        if b.path:
            try:
                (self.root / b.path).unlink(missing_ok=True)
            except OSError as e:
                log.warning("could not delete %s: %s", b.path, e)

    async def delete(self, b: Backup, session):
        self._unlink(b)
        await session.delete(b)

    # ------------------------------------------------------------ restore
    async def restore(self, backup: Backup, target: HostInfo, area: str, keep_network: bool, keep_mode: bool,
                      allow_platform_mismatch: bool) -> dict:
        if backup.kind != "fpp" or target.kind != "fpp":
            raise RestoreRefused("only FPP backups can be restored automatically; restore a Falcon by hand "
                                 "from the downloaded export")
        data = self.read_json(backup)
        areas = devices.restore_areas(data)
        if area != "all" and area.split("/")[0] not in areas:
            raise RestoreRefused(f"area '{area}' is not in this backup (has: {', '.join(areas)})")

        # Safety net: back up the target first, pinned so retention never removes it.
        pre = await self.backup_host(target, "pre-restore", pinned=True,
                                     note=f"Taken automatically before restoring backup #{backup.id} "
                                          f"({backup.host_name}, {backup.created_at:%Y-%m-%d %H:%M})")
        if pre.status != "ok":
            raise RestoreRefused(f"could not back up {target.name} before restoring ({pre.error}); not restoring")
        if backup.platform and pre.platform and backup.platform != pre.platform and not allow_platform_mismatch:
            raise RestoreRefused(f"backup is from a {backup.platform} but {target.name} is a {pre.platform}; "
                                 "channel outputs would not match. Pass allow_platform_mismatch to force it.")

        filename = Path(backup.path or "backup.json").name
        log.info("restoring backup #%s (%s) to %s area=%s keep_network=%s keep_mode=%s",
                 backup.id, backup.host_name, target.name, area, keep_network, keep_mode)
        result = await devices.fpp_restore(self.c.client, target.address, self.read(backup), filename, area,
                                           keep_network, keep_mode)
        self.c.wake()
        return {"restored": result.restored, "areas": result.areas, "errors": result.errors,
                "pre_restore_backup_id": pre.id, "reboot_recommended": result.restored}

    # ------------------------------------------------------------ status / alerts
    async def refresh_latest(self):
        async with self.c.sessions() as s:
            rows = (await s.scalars(select(Backup).order_by(Backup.created_at))).all()
        latest: dict[int, dict] = {}
        for b in rows:
            if b.host_id is not None:
                latest[b.host_id] = _summary(b, latest.get(b.host_id))
        self.latest = latest

    def alerts(self, hosts: list[HostInfo]) -> list[Alert]:
        out = []
        now = datetime.now(timezone.utc)
        stale = timedelta(hours=self.settings.backup_stale_hours)
        for h in hosts:
            if not h.enabled or h.kind not in ("fpp", "falcon"):
                continue
            info = self.latest.get(h.id)
            if not info:
                continue
            if info["last_status"] == "failed":
                out.append(Alert(severity="warning", code="backup_failed", host_id=h.id, host_name=h.name,
                                 message=f"{h.name} backup failed: {info['last_error']}"))
            elif info.get("last_ok_at") and now - info["last_ok_at"] > stale:
                st = self.c.states.get(h.id)
                if st is None or st.online:   # offline hosts already have an offline alert
                    out.append(Alert(severity="warning", code="backup_stale", host_id=h.id, host_name=h.name,
                                     message=f"{h.name} has not been backed up since "
                                             f"{info['last_ok_at'].astimezone(self.tz):%b %d}"))
        return out

    # ------------------------------------------------------------ schedule
    def _next_run_after(self, now: datetime) -> datetime:
        hh, mm = (int(x) for x in self.settings.backup_time.split(":"))
        local = now.astimezone(self.tz)
        run = datetime.combine(local.date(), dtime(hh, mm), tzinfo=self.tz)
        if run <= local:
            run = datetime.combine(local.date() + timedelta(days=1), dtime(hh, mm), tzinfo=self.tz)
        return run

    async def _missed_last_run(self) -> bool:
        """True if no scheduled run happened in the last ~day (VM was down at backup time)."""
        async with self.c.sessions() as s:
            last = await s.scalar(select(Backup.created_at).where(Backup.trigger == "scheduled")
                                  .order_by(Backup.created_at.desc()).limit(1))
        return last is None or datetime.now(timezone.utc) - _aware(last) > timedelta(hours=26)

    async def _schedule_loop(self):
        await asyncio.sleep(60)   # let the first polls and discovery finish
        if await self._missed_last_run():
            log.info("catching up on a missed scheduled backup")
            await self._safe_run()
        while True:
            self.next_run = self._next_run_after(datetime.now(timezone.utc))
            await asyncio.sleep(max(1.0, (self.next_run - datetime.now(timezone.utc)).total_seconds()))
            await self._safe_run()

    async def _safe_run(self):
        try:
            await self.run_fleet("scheduled")
        except Exception:
            log.exception("scheduled backup failed")


class RestoreRefused(Exception):
    pass


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _summary(b: Backup, prev: dict | None) -> dict:
    out = dict(prev or {})
    out.update(last_status=b.status, last_error=b.error, last_at=_aware(b.created_at))
    if b.status == "ok":
        out.update(last_ok_at=_aware(b.created_at), last_ok_id=b.id)
    return out


def _describe(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "timed out"
    if isinstance(e, httpx.ConnectError):
        return "unreachable"
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return f"{e}"[:300] or type(e).__name__
