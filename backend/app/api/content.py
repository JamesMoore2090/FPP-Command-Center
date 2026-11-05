"""Content: sequences and media across the fleet, missing-file check, push from the player to remotes."""
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import content
from ..fleet import find_player
from ..show import FppError
from .hosts import collector
from .jobs import jobs

log = logging.getLogger("fppcc.content")
router = APIRouter(prefix="/api/content")
LOCK = "content"


def _player_and_remotes(c):
    hosts = [h for h in c.hosts.values() if h.enabled]
    player = find_player(hosts, c.states)
    if player is None:
        raise HTTPException(409, "no FPP player in the fleet")
    remotes = sorted((h for h in hosts if h.kind == "fpp" and h.id != player.id), key=lambda h: h.name.lower())
    return player, remotes


def _online(c) -> dict[int, bool]:
    return {i: s.online for i, s in c.states.items()}


@router.get("")
async def content_overview(c=Depends(collector)):
    """Player's sequences and media, what each remote has, and problems (files playlists need but can't find)."""
    player, remotes = _player_and_remotes(c)
    if not _online(c).get(player.id, True):
        raise HTTPException(503, f"{player.name} (the player) is offline")
    try:
        return await content.overview(c.client, player, remotes, _online(c))
    except FppError as e:
        raise HTTPException(e.status, str(e))


class PushRequest(BaseModel):
    host_ids: list[int] | None = None       # remotes; omit for every online remote that has sequences
    files: list[str] | None = None          # sequence names; omit for "whatever each remote is missing"
    overwrite: bool = False                 # also replace copies the remote already has
    confirm: bool = False
    force: bool = False                     # allowed while the show is playing


@router.post("/push", status_code=202)
async def push(body: PushRequest, c=Depends(collector), mgr=Depends(jobs)):
    """Copy sequences from the player to remotes. Runs as a background job; returns the job."""
    player, remotes = _player_and_remotes(c)
    if mgr.running(LOCK):
        raise HTTPException(409, "a content push is already running")
    online = _online(c)
    pst = c.states.get(player.id)
    if pst and pst.status == "playing" and not body.force:
        raise HTTPException(409, "the show is playing; copying big files can make remotes stutter. "
                                 "Send force: true to copy anyway")
    try:
        ov = await content.overview(c.client, player, remotes, online)
    except FppError as e:
        raise HTTPException(e.status, str(e))
    sizes = {s["name"]: s["size"] for s in ov["sequences"]}
    views = {r["id"]: r for r in ov["remotes"]}

    if body.files is not None:
        unknown = [f for f in body.files if f not in sizes]
        if unknown:
            raise HTTPException(404, f"not on the player: {', '.join(unknown)}")
    if body.host_ids is None:
        targets = [h for h in remotes if views[h.id]["online"] and views[h.id]["uses_sequences"]]
    else:
        by_id = {h.id: h for h in remotes}
        bad = [i for i in body.host_ids if i not in by_id]
        if bad:
            raise HTTPException(404, f"not an FPP remote: {bad}")
        targets = [by_id[i] for i in body.host_ids]
        offline = [h.name for h in targets if not views[h.id]["online"] or views[h.id]["error"]]
        if offline:
            raise HTTPException(409, f"not reachable: {', '.join(offline)}")

    plan = []
    for h in targets:
        have = views[h.id]["files"]
        wanted = body.files if body.files is not None else views[h.id]["missing"]
        names = [n for n in wanted if body.overwrite or n not in have]
        if names:
            plan.append((h, names))
    if not plan:
        return {"job": None, "message": "nothing to copy: the selected remotes already have these files"}
    total = sum(sizes[n] or 0 for _, names in plan for n in names)
    if not body.confirm:
        raise HTTPException(422, f"copying {sum(len(n) for _, n in plan)} files ({total / 2**20:.0f} MB) to "
                                 f"{len(plan)} remotes needs confirm: true")

    log.info("content push: %s", {h.name: names for h, names in plan})
    steps = [({"label": f"{len(names)} file{'s' if len(names) != 1 else ''} → {h.name}", "host_id": h.id, "host": h.name},
              content.push_step(c.client, player, h, names, sizes)) for h, names in plan]
    job = mgr.start("content_push", f"Copy sequences from {player.name}", steps, parallel=2, lock=LOCK)
    return {"job": job.summary()}


class DeleteRequest(BaseModel):
    host_id: int
    files: list[str] = Field(min_length=1)
    confirm: bool = False


@router.post("/delete")
async def delete_files(body: DeleteRequest, c=Depends(collector)):
    """Delete sequences from one remote (e.g. copies the player no longer has). Never on the player."""
    player, remotes = _player_and_remotes(c)
    h = next((r for r in remotes if r.id == body.host_id), None)
    if h is None:
        raise HTTPException(404, "not an FPP remote (deleting on the player is not supported here)")
    if not body.confirm:
        raise HTTPException(422, f"deleting {len(body.files)} files from {h.name} needs confirm: true")
    results = []
    for name in body.files:
        try:
            r = await c.client.delete(f"http://{h.address}/api/file/sequences/{quote(name, safe='')}", timeout=15)
            ok = r.status_code < 400 and (r.json().get("status") == "OK")
            results.append({"file": name, "ok": ok, "message": None if ok else r.text[:200]})
        except Exception as e:      # noqa: BLE001
            results.append({"file": name, "ok": False, "message": type(e).__name__})
    log.info("deleted on %s: %s", h.name, results)
    return results
