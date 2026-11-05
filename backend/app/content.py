"""Show content across the fleet: which sequences each remote has compared with the player, which files the
player's playlists reference but can't find, and pushing sequences from the player to remotes.

Sizes are not compared: xLights' FPP Connect uploads a trimmed, per-controller copy of each sequence to a
remote, so a remote's copy is normally smaller than the player's and that is not a problem. A remote
with no sequences at all is assumed to be fed channel data over the network (DDP/E1.31) rather than playing
locally, so files "missing" there are not reported as problems.

A push streams the file from the player through this VM into the remote's upload folder, checks that every
byte arrived, then has FPP move it into place (an atomic rename), the same as FPP's own MultiSync file
copy. A broken transfer never leaves a half-written sequence where fppd would play it.
"""
import asyncio
import re
from datetime import date
from urllib.parse import quote

import h11
import httpx

from .jobs import StepContext, StepFailed
from .show import FppError, get_json

PUSH_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=10.0)
CHUNK = 256 * 1024


async def list_dir(client, address: str, d: str) -> dict[str, dict]:
    data = await get_json(client, address, f"files/{d}")
    return {f["name"]: {"size": f.get("sizeBytes"), "mtime": f.get("mtime")} for f in (data or {}).get("files", [])}


def playlist_refs(pl: dict) -> list[dict]:
    """Files an FPP playlist JSON refers to."""
    refs = []
    for section in ("leadIn", "mainPlaylist", "leadOut"):
        for e in pl.get(section) or []:
            if not e.get("enabled", 1):
                continue
            t = e.get("type")
            if t in ("both", "sequence") and e.get("sequenceName"):
                refs.append({"kind": "sequence", "name": e["sequenceName"]})
            if t in ("both", "media") and e.get("mediaName"):
                refs.append({"kind": "media", "name": e["mediaName"]})
            if t == "playlist" and e.get("name"):
                refs.append({"kind": "playlist", "name": e["name"]})
    return refs


def scheduled_playlists(entries: list[dict]) -> set[str]:
    """Playlists in enabled schedule entries that haven't ended yet."""
    today = date.today().isoformat()
    out = set()
    for e in entries or []:
        end = str(e.get("endDate") or "")
        if not e.get("enabled") or e.get("command") or not e.get("playlist"):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) and end < today:
            continue
        out.add(e["playlist"])
    return out


async def _safe(coro):
    try:
        return await coro, None
    except FppError as e:
        return None, str(e)


async def overview(client, player, remotes: list, online: dict[int, bool]) -> dict:
    """player/remotes are HostInfo; online maps host id -> last known online state."""
    seq_task = list_dir(client, player.address, "sequences")
    music_task = list_dir(client, player.address, "music")
    video_task = list_dir(client, player.address, "videos")
    names_task = get_json(client, player.address, "playlists")
    sched_task = get_json(client, player.address, "schedule")
    seqs, music, videos, names, sched = await asyncio.gather(seq_task, music_task, video_task, names_task, sched_task)
    media = {**music, **videos}

    async def remote_files(h):
        if not online.get(h.id, True):
            return None, "offline"
        return await _safe(list_dir(client, h.address, "sequences"))

    remote_results = await asyncio.gather(*(remote_files(h) for h in remotes))
    playlists = {}
    for name in names or []:
        pl, err = await _safe(get_json(client, player.address, f"playlist/{quote(name, safe='')}"))
        playlists[name] = playlist_refs(pl) if pl else []
    scheduled = scheduled_playlists(sched)

    # which playlists (directly or via a sub-playlist) use each file
    used_by: dict[tuple[str, str], set[str]] = {}

    def walk(top: str, name: str, seen: set[str]):
        for r in playlists.get(name, []):
            if r["kind"] == "playlist":
                if r["name"] not in seen:
                    walk(top, r["name"], seen | {r["name"]})
            else:
                used_by.setdefault((r["kind"], r["name"]), set()).add(top)

    for name in playlists:
        walk(name, name, {name})

    problems = []
    for (kind, fname), users in sorted(used_by.items()):
        present = fname in seqs if kind == "sequence" else fname in media
        if not present:
            sev = "critical" if users & scheduled else "warning"
            problems.append({"severity": sev, "host_id": player.id, "host": player.name, "kind": kind, "file": fname,
                             "playlists": sorted(users),
                             "message": f"{kind.title()} '{fname}' is in {', '.join(sorted(users))} but not on {player.name}"})
    for name, refs in playlists.items():
        for r in refs:
            if r["kind"] == "playlist" and r["name"] not in playlists:
                problems.append({"severity": "warning", "host_id": player.id, "host": player.name, "kind": "playlist",
                                 "file": r["name"], "playlists": [name],
                                 "message": f"Playlist '{name}' includes playlist '{r['name']}', which doesn't exist"})

    remote_views = []
    for h, (files, err) in zip(remotes, remote_results):
        files = files or {}
        uses = bool(files)
        missing = sorted(n for n in seqs if n not in files) if files is not None and not err else []
        remote_views.append({
            "id": h.id, "name": h.name, "online": err != "offline", "error": err if err != "offline" else None,
            "uses_sequences": uses, "count": len(files), "missing": missing if uses else [],
            "extra": sorted(n for n in files if n not in seqs),
            "files": {n: f["size"] for n, f in files.items()},
        })
        if uses:
            for n in missing:
                users = used_by.get(("sequence", n), set())
                if users:
                    problems.append({
                        "severity": "critical" if users & scheduled else "warning", "host_id": h.id, "host": h.name,
                        "kind": "sequence", "file": n, "playlists": sorted(users),
                        "message": f"{h.name} is missing '{n}' (used by {', '.join(sorted(users))})"})

    sev_order = {"critical": 0, "warning": 1}
    problems.sort(key=lambda p: (sev_order[p["severity"]], p["host"], p["file"]))
    return {
        "player": {"id": player.id, "name": player.name},
        "scheduled_playlists": sorted(scheduled),
        "sequences": [{"name": n, "size": f["size"], "mtime": f["mtime"],
                       "playlists": sorted(used_by.get(("sequence", n), set()))} for n, f in sorted(seqs.items())],
        "media": [{"name": n, "size": f["size"], "mtime": f["mtime"],
                   "playlists": sorted(used_by.get(("media", n), set()))} for n, f in sorted(media.items())],
        "remotes": remote_views,
        "problems": problems,
    }


# ---------------------------------------------------------------- pushing
async def push_file(client: httpx.AsyncClient, src: str, dst: str, name: str, length: int, ctx: StepContext,
                    done_before: int, total: int) -> int:
    """Copy one sequence from src to dst. length is the player's listed size: FPP streams downloads without a
    Content-Length, and the upload must declare one (so a short or long download fails instead of storing a
    truncated file). Returns bytes copied."""
    qname = quote(name, safe="")
    sent = 0
    try:
        async with client.stream("GET", f"http://{src}/api/file/sequences/{qname}", timeout=PUSH_TIMEOUT) as r:
            if r.status_code != 200:
                raise StepFailed(f"{name}: player answered HTTP {r.status_code}")

            async def body():
                nonlocal sent
                async for chunk in r.aiter_bytes(CHUNK):
                    sent += len(chunk)
                    ctx.progress((done_before + sent) / total if total else None)
                    yield chunk

            up = await client.post(f"http://{dst}/api/file/uploads/{qname}", content=body(), timeout=PUSH_TIMEOUT,
                                   headers={"Content-Length": str(length), "Content-Type": "application/octet-stream"})
    except (httpx.HTTPError, h11.LocalProtocolError) as e:
        await _cleanup(client, dst, qname)
        if sent != length:
            raise StepFailed(f"{name}: the player sent {sent} of {length} bytes (file changed during the copy?)")
        raise StepFailed(f"{name}: transfer failed ({type(e).__name__})")
    try:
        result = up.json()
    except ValueError:
        result = {}
    if up.status_code >= 400 or str(result.get("status")) != "OK" or int(result.get("written") or 0) != length:
        await _cleanup(client, dst, qname)
        raise StepFailed(f"{name}: remote stored {result.get('written', '?')} of {length} bytes "
                         f"(HTTP {up.status_code} {result.get('status', '')})")
    try:
        mv = await client.post(f"http://{dst}/api/file/move/{qname}", timeout=httpx.Timeout(30.0, connect=5.0))
        status = mv.json().get("status") if mv.status_code < 400 else f"HTTP {mv.status_code}"
    except (httpx.HTTPError, ValueError) as e:
        status = type(e).__name__
    if status != "OK":
        await _cleanup(client, dst, qname)
        raise StepFailed(f"{name}: copied but could not be moved into place ({status})")
    return length


async def _cleanup(client, dst: str, qname: str):
    try:
        await client.delete(f"http://{dst}/api/file/uploads/{qname}", timeout=httpx.Timeout(10.0, connect=5.0))
    except httpx.HTTPError:
        pass


def push_step(client, player, remote, names: list[str], sizes: dict[str, int]):
    async def run(ctx: StepContext) -> str:
        total = sum(sizes.get(n) or 0 for n in names)
        try:
            info = await get_json(client, remote.address, "system/info")
            free = ((info.get("Utilization") or {}).get("Disk") or {}).get("Media", {}).get("Free")
        except FppError as e:
            raise StepFailed(str(e))
        if isinstance(free, (int, float)) and free < total * 1.1 + 200 * 2**20:
            raise StepFailed(f"not enough free space ({free / 2**30:.1f} GB free, {total / 2**30:.1f} GB to copy)")
        done = 0
        for i, n in enumerate(names, 1):
            if ctx.cancelled:
                raise StepFailed(f"cancelled after {i - 1} of {len(names)} files")
            ctx.progress(done / total if total else None, f"{i}/{len(names)}: {n}")
            ctx.log(f"Copying {n} ({(sizes.get(n) or 0) / 2**20:.1f} MB)")
            done += await push_file(client, player.address, remote.address, n, sizes[n], ctx, done, total)
            ctx.log("  done")
        return f"copied {len(names)} file{'s' if len(names) != 1 else ''} ({done / 2**20:.1f} MB)"
    return run
