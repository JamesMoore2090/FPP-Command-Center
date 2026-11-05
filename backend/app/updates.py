"""FPP software and OS updates, driven through the same pages FPP's own UI uses.

- Update (same version, newer commits): POST /manualUpdate.php?wrapped=1 (git pull + rebuild + fppd restart).
- Upgrade to a newer minor version:     POST /upgradefpp.php?version=v<X>&wrapped=1, then reboot.
- OS upgrade (a new .fppos image):       POST /upgradeOS.php?wrapped=1&os=<file or URL>; the box reboots itself.

Each page streams its log and always answers HTTP 200, so success is read from the output, the same way
FPP's MultiSync page does it. Afterwards we wait for the box to come back and re-read its version.
"""
import asyncio
import re
import time
from urllib.parse import quote

import httpx

from .jobs import StepContext, StepFailed
from .show import FppError, get_json

STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=900.0, write=30.0, pool=10.0)   # compiles go quiet for minutes
OS_CACHE_S = 6 * 3600
_os_cache: dict[tuple, tuple[float, list]] = {}

_FAILURE = re.compile(r"^(=====\s*)?(FPP\s+)?Upgrade\s+Failed\b", re.I)
_STAGE = re.compile(r"^=====\s*(.+?)\s*=====\s*$")


def is_failure_line(line: str) -> bool:
    line = line.strip()
    return bool(_FAILURE.match(line)) or "unable to pull git updates" in line


def os_date(os_version: str | None) -> str | None:
    """'v2026-10b (32bit)' -> '2026-10b'"""
    m = re.match(r"v?(\d{4}-\d{2}[a-z]?)", os_version or "")
    return m.group(1) if m else None


def image_date(filename: str) -> str | None:
    """'Pi-10.3_2026-10b.fppos' -> '2026-10b'"""
    m = re.search(r"_(\d{4}-\d{2}[a-z]?)\.fppos$", filename or "")
    return m.group(1) if m else None


async def _opt(coro):
    try:
        return await coro
    except FppError:
        return None


async def os_images(client, address: str, platform: str, os_version: str | None, refresh: bool = False) -> list[dict]:
    """Release OS images for this box's platform (the box asks GitHub), cached per platform and bitness."""
    key = (platform, "64" in (os_version or ""))
    hit = _os_cache.get(key)
    if hit and not refresh and time.time() - hit[0] < OS_CACHE_S:
        files = hit[1]
    else:
        d = await get_json(client, address, "git/releases/os/0")
        files = [{"tag": f.get("tag"), "name": f.get("release_name"), "filename": f.get("filename"), "url": f.get("url"),
                  "size": f.get("size"), "prerelease": bool(f.get("prerelease"))} for f in (d or {}).get("files", [])]
        _os_cache[key] = (time.time(), files)
    # "downloaded" is per box, so it is never cached
    return files


async def host_versions(client, h, online: bool) -> dict:
    base = {"id": h.id, "name": h.name, "address": h.address, "kind": h.kind, "role": h.role, "online": online}
    if h.kind != "fpp":
        return {**base, "updatable": False}
    if not online:
        return {**base, "updatable": True, "error": "offline"}
    info, upd, act, plugins = await asyncio.gather(
        _opt(get_json(client, h.address, "system/info")),
        _opt(get_json(client, h.address, "system/updateStatus")),
        _opt(get_json(client, h.address, "system/updateActivity")),
        _opt(get_json(client, h.address, "plugin/updateStatus")),
    )
    if info is None:
        return {**base, "updatable": True, "error": "no answer from /api/system/info"}
    upd = upd or {}
    act = act or {}
    upgrade = None
    if upd.get("branchUpgradeAvailable") and upd.get("branchUpgradeVersion"):
        upgrade = {"version": upd["branchUpgradeVersion"], "target": upd.get("branchUpgradeTarget"),
                   "major": bool(upd.get("isMajorVersionUpgrade"))}
    return {
        **base, "updatable": True,
        "platform": info.get("Platform"), "variant": info.get("Variant"), "mode": info.get("Mode"),
        "version": info.get("Version"), "branch": info.get("Branch"),
        "os_version": info.get("OSVersion"), "os_release": info.get("OSRelease"),
        "local_commit": upd.get("localCommit"), "remote_commit": (upd.get("remoteCommit") or "")[:9] or None,
        "update_available": bool(upd.get("commitUpdateAvailable")),
        "update_checked": bool(upd.get("checked")),
        "upgrade": upgrade, "end_of_life": bool(upd.get("isEndOfLife")),
        "activity": {"in_progress": bool(act.get("inProgress")), "op": act.get("op") or None,
                     "stage": act.get("stage") or None, "failed": bool(act.get("failed"))},
        "plugin_updates": [p.get("name") or p.get("repoName") or str(p) if isinstance(p, dict) else str(p)
                           for p in (plugins or {}).get("plugins") or []],
        "plugins_installed": (plugins or {}).get("installed"),
    }


def recommended_os(images: list[dict], version: str | None, os_version: str | None) -> dict | None:
    """The release image for the FPP version the box runs, when the box's OS is not already that image."""
    for f in images:
        if f["prerelease"] or f["tag"] != version:
            continue
        if image_date(f["filename"]) and image_date(f["filename"]) != os_date(os_version):
            return f
        return None
    return None


# ---------------------------------------------------------------- running updates
async def stream_page(client, address: str, path: str, ctx: StepContext, *, drop_ok: bool = False) -> bool:
    """POST a wrapped FPP page and log its output. Returns True when it ran to the end without a failure line.
    drop_ok: a dropped connection after output started counts as finished (an OS upgrade reboots mid-stream)."""
    failed = False
    started = False
    partial = ""
    try:
        async with client.stream("POST", f"http://{address}{path}", timeout=STREAM_TIMEOUT) as r:
            if r.status_code == 409:
                raise StepFailed("another update is already running on this controller")
            if r.status_code >= 400:
                raise StepFailed(f"{path.split('?')[0]} answered HTTP {r.status_code}")
            async for text in r.aiter_text():
                started = True
                lines = (partial + text).split("\n")
                partial = lines.pop()
                for line in lines:
                    line = line.rstrip("\r")
                    ctx.log(line)
                    if is_failure_line(line):
                        failed = True
                    m = _STAGE.match(line)
                    if m:
                        ctx.progress(None, m.group(1))
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout) as e:
        if partial:
            ctx.log(partial)
        if drop_ok and started:
            ctx.log(f"(connection closed: {type(e).__name__}, expected while the controller reboots)")
            return not failed
        raise StepFailed(f"lost the connection during the update ({type(e).__name__}); check the controller")
    except httpx.HTTPError as e:
        raise StepFailed(f"could not start the update ({type(e).__name__})")
    if partial:
        ctx.log(partial)
        failed = failed or is_failure_line(partial)
    return not failed


async def wait_for(client, address: str, ctx: StepContext, *, want_down: bool = False, timeout: float = 600,
                   label: str = "") -> bool:
    """Poll fppd until it answers (or, with want_down, until it stops answering)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = await client.get(f"http://{address}/api/fppd/status", timeout=httpx.Timeout(5.0, connect=3.0))
            up = r.status_code == 200
        except httpx.HTTPError:
            up = False
        if up != want_down:
            return True
        ctx.progress(None, label or ("waiting for it to go down" if want_down else "waiting for it to come back"))
        await asyncio.sleep(5)
    return False


async def _version(client, address: str) -> dict:
    return await _opt(get_json(client, address, "system/info")) or {}


def update_step(client, backups, host, op: str, *, version: str | None = None, image: dict | None = None,
                reboot: bool = True):
    """op: update / upgrade / os. backups: BackupService for a pre-change configuration backup."""
    async def run(ctx: StepContext) -> str:
        before = await _version(client, host.address)
        if not before:
            raise StepFailed("controller is not answering")
        act = await _opt(get_json(client, host.address, "system/updateActivity")) or {}
        if act.get("inProgress"):
            raise StepFailed(f"an update is already running on it ({act.get('op') or 'unknown'})")

        ctx.progress(None, "backing up configuration")
        what = {"update": "FPP update", "upgrade": f"upgrade to {version}", "os": "OS upgrade"}[op]
        b = await backups.backup_host(host, "pre-change", note=f"Before {what}")
        if b.status != "ok":
            raise StepFailed(f"configuration backup failed ({b.error}); not updating")
        ctx.log(f"Configuration backed up (backup #{b.id})")

        if op == "update":
            ctx.progress(None, "pulling and building")
            ok = await stream_page(client, host.address, "/manualUpdate.php?wrapped=1", ctx)
            if not ok:
                raise StepFailed("FPP reported the update failed; see the log")
            ctx.progress(None, "waiting for fppd")
            if not await wait_for(client, host.address, ctx, timeout=300):
                raise StepFailed("updated, but fppd did not come back within 5 minutes")
        elif op == "upgrade":
            ctx.progress(None, f"upgrading to {version}")
            ok = await stream_page(client, host.address, f"/upgradefpp.php?version=v{quote(version or '')}&wrapped=1", ctx)
            if not ok:
                raise StepFailed("FPP reported the upgrade failed; see the log")
            if reboot:
                await _reboot(client, host.address, ctx)
            elif not await wait_for(client, host.address, ctx, timeout=300):
                raise StepFailed("upgraded, but fppd did not come back within 5 minutes")
        else:
            assert image is not None
            rel = await _opt(get_json(client, host.address, "git/releases/os/0")) or {}
            local = image["filename"] in (rel.get("downloaded") or [])
            free = (((before.get("Utilization") or {}).get("Disk") or {}).get("Media") or {}).get("Free")
            if not local and isinstance(free, (int, float)) and free < (image.get("size") or 0) * 1.5:
                raise StepFailed(f"not enough free space for the OS image ({free / 2**30:.1f} GB free)")
            src = image["filename"] if local else image["url"]
            ctx.log("Using the copy already downloaded on the controller" if local else f"Downloading {image['url']}")
            ctx.progress(None, "downloading and installing the OS image")
            ctx.log(f"OS image: {image['filename']}")
            ok = await stream_page(client, host.address, f"/upgradeOS.php?wrapped=1&os={quote(src, safe='')}", ctx,
                                   drop_ok=True)
            if not ok:
                raise StepFailed("FPP reported the OS upgrade failed; see the log")
            ctx.progress(None, "rebooting into the new OS")
            await wait_for(client, host.address, ctx, want_down=True, timeout=300)
            if not await wait_for(client, host.address, ctx, timeout=1800, label="waiting for it to come back (up to 30 min)"):
                raise StepFailed("did not come back within 30 minutes after the OS upgrade; check it")

        after = await _version(client, host.address)
        changed = []
        for key, label in (("Version", "FPP"), ("OSVersion", "OS")):
            if before.get(key) != after.get(key):
                changed.append(f"{label} {before.get(key)} → {after.get(key)}")
        if op == "os" and before.get("OSVersion") == after.get("OSVersion"):
            raise StepFailed(f"came back, but the OS version is still {after.get('OSVersion')}")
        if op == "upgrade" and version and str(after.get("Version")) != str(version):
            raise StepFailed(f"came back on FPP {after.get('Version')}, expected {version}")
        return "; ".join(changed) or f"up to date on FPP {after.get('Version')}"
    return run


async def _reboot(client, address: str, ctx: StepContext):
    ctx.progress(None, "rebooting")
    ctx.log("Rebooting")
    try:
        await client.post(f"http://{address}/api/system/reboot", timeout=httpx.Timeout(10.0, connect=5.0))
    except httpx.HTTPError:
        pass
    await wait_for(client, address, ctx, want_down=True, timeout=180)
    if not await wait_for(client, address, ctx, timeout=600):
        raise StepFailed("did not come back within 10 minutes after rebooting")
