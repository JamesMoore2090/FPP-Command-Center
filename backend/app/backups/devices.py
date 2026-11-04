"""Talking to controllers for backup and restore.

FPP: uses FPP's own backup page (backup.php). Posting btnDownloadConfig streams a complete JSON backup
in FPP's native format without depending on the copy FPP keeps on the box: FPP keeps only 60 on-box
backups and prunes right after writing, so a create-then-download via /api/backups can lose the file.
Restore posts the file back to the same page, which (unlike /api/backups/configuration/restore) lets us
choose whether to keep the box's current network and player/remote mode, as a replacement box needs.

Falcon (v2/v3 firmware): no backup API, so the export is a bundle of its XML config files plus the
network settings scraped from config.htm. It is for safekeeping, viewing and diffing; restoring a Falcon
is manual (its write format is undocumented).
"""
import asyncio
import html
import json
import re
from dataclasses import dataclass, field

import httpx

BACKUP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
RESTORE_TIMEOUT = httpx.Timeout(180.0, connect=5.0)
# Change on every backup, so they're ignored for change detection and diffs.
# status_at_backup is the Falcon status.xml snapshot (uptime, clock, temperatures).
METADATA_KEYS = {"backup_comment", "backup_taken", "backup_trigger_source", "status_at_backup"}


@dataclass
class TakenBackup:
    content: bytes               # what is stored on disk
    version: str | None = None
    platform: str | None = None


class BackupError(Exception):
    pass


def normalized(data: dict) -> dict:
    """Backup content without per-backup metadata, for change detection and diffs."""
    return {k: v for k, v in data.items() if k not in METADATA_KEYS}


# ---------------------------------------------------------------- FPP
async def fpp_backup(client: httpx.AsyncClient, address: str, version: str | None) -> TakenBackup:
    r = await client.post(f"http://{address}/backup.php", data={"btnDownloadConfig": "1", "backuparea": "all"},
                          timeout=BACKUP_TIMEOUT)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        raise BackupError("FPP returned something other than a JSON backup (backup page error?)")
    if not isinstance(data, dict) or "fpp_backup_version" not in data:
        raise BackupError("response is not an FPP backup (no fpp_backup_version)")
    data["backup_comment"] = "FPP Command Center"
    return TakenBackup(content=json.dumps(data, indent=1).encode(), version=version, platform=data.get("platform"))


@dataclass
class RestoreResult:
    restored: bool
    areas: list[dict] = field(default_factory=list)    # [{"area": "Show Setup - Playlist", "ok": True}]
    errors: list[str] = field(default_factory=list)


_AREA = re.compile(r"callout-(success|danger)[^>]*>(.*?)</span>", re.S)
_ERRORS = re.compile(r"Backup failed:\s*<br>\s*<ul>(.*?)</ul>", re.S)
_LI = re.compile(r"<li>(.*?)</li>", re.S)
_TAGS = re.compile(r"<[^>]+>")


def parse_restore_page(page: str) -> RestoreResult:
    """The backup page renders a restoreSuccessFlag block with one success/danger callout per area."""
    restored = 'id="restoreSuccessFlag"' in page
    areas = []
    if restored:
        block = page.split('id="restoreSuccessFlag"', 1)[1]
        for cls, text in _AREA.findall(block):
            label = html.unescape(_TAGS.sub("", text)).strip()
            label = re.sub(r"\s*-\s*(Success|Failed)$", "", label)
            areas.append({"area": label, "ok": cls == "success"})
    errors = []
    m = _ERRORS.search(page)
    if m:
        errors = [html.unescape(_TAGS.sub("", e)).strip() for e in _LI.findall(m.group(1))]
    return RestoreResult(restored=restored, areas=areas, errors=errors)


async def fpp_restore(client: httpx.AsyncClient, address: str, content: bytes, filename: str, area: str,
                      keep_network: bool, keep_mode: bool) -> RestoreResult:
    form = {"btnRestoreConfig": "1", "restorearea": area}
    if keep_network:
        form["keepExitingNetwork"] = "1"      # sic: FPP's field name
    if keep_mode:
        form["keepMasterSlave"] = "1"
    r = await client.post(f"http://{address}/backup.php", data=form,
                          files={"conffile": (filename, content, "application/json")}, timeout=RESTORE_TIMEOUT)
    r.raise_for_status()
    return parse_restore_page(r.text)


def restore_areas(data: dict) -> list[str]:
    """Top-level areas present in an FPP backup, for per-area restore."""
    return [k for k, v in data.items() if isinstance(v, dict) and k not in METADATA_KEYS]


# ---------------------------------------------------------------- Falcon
FALCON_FILES = ["settings.xml", "strings.xml", "serialsettings.xml"]
_INPUT = re.compile(r"<input\b([^>]*)>", re.I)
_ATTR = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def parse_falcon_network(page: str) -> dict:
    """Named <input> values from config.htm (ip, gw, sub, dns1, dns2, dhcp, host, mac...)."""
    out = {}
    for m in _INPUT.finditer(page):
        attrs = dict((k.lower(), v) for k, v in _ATTR.findall(m.group(1)))
        name = attrs.get("name")
        if not name:
            continue
        if attrs.get("type", "").lower() in ("checkbox", "radio"):
            if "checked" in m.group(1).lower():
                out[name] = attrs.get("value", "on")
            continue
        out[name] = attrs.get("value", "").strip()
    return out


async def _falcon_get(client: httpx.AsyncClient, url: str, tries: int = 3) -> str:
    """Falcons serve one request at a time and time out under load, so fetch sequentially with retries."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            r = await client.get(url, timeout=httpx.Timeout(10.0, connect=5.0))
            r.raise_for_status()
            return r.text
        except httpx.HTTPError as e:
            last = e
            await asyncio.sleep(1 + attempt)
    raise last  # type: ignore[misc]


async def falcon_backup(client: httpx.AsyncClient, address: str) -> TakenBackup:
    base = f"http://{address}"
    files = {}
    for f in ["status.xml", *FALCON_FILES]:
        files[f] = await _falcon_get(client, f"{base}/{f}")
    network = parse_falcon_network(await _falcon_get(client, f"{base}/config.htm"))
    fw = re.search(r"<fv>([^<]+)</fv>", files["status.xml"])
    model = re.search(r"<np>(\d+)</np>", files["status.xml"])
    status = files.pop("status.xml")
    data = {
        "falcon_backup_version": 1,
        "firmware": fw.group(1) if fw else None,
        "model": f"Falcon F{model.group(1)}" if model else "Falcon",
        "network": network,
        "files": files,
        "status_at_backup": status,
    }
    return TakenBackup(content=json.dumps(data, indent=1).encode(), version=data["firmware"], platform=data["model"])
