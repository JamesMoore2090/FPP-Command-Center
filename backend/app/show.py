"""FPP playback, schedule, command and test-mode calls (used by the show and fleet APIs)."""
import asyncio
import re
from datetime import date
from urllib.parse import quote

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Schedule "day" codes (FPP src/ScheduleEntry.h). Values >= 0x10000 are custom day bitmasks.
DAY_NAMES = {
    0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday",
    7: "Everyday", 8: "Weekdays", 9: "Weekends", 10: "Mon/Wed/Fri", 11: "Tue/Thu", 12: "Sun–Thu",
    13: "Fri/Sat", 14: "Odd days", 15: "Even days",
}
STOP_TYPES = {0: "Graceful", 1: "Hard stop", 2: "Graceful after loop"}


class FppError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


async def _req(client: httpx.AsyncClient, method: str, address: str, path: str, **kw) -> httpx.Response:
    try:
        r = await client.request(method, f"http://{address}/api/{path}", timeout=TIMEOUT, **kw)
    except httpx.TimeoutException:
        raise FppError(f"{address} timed out", 504)
    except httpx.HTTPError as e:
        raise FppError(f"{address} unreachable ({type(e).__name__})")
    return r


async def get_json(client, address, path):
    r = await _req(client, "GET", address, path)
    if r.status_code >= 400:
        raise FppError(f"{path}: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise FppError(f"{path}: not JSON")


async def run_command(client, address: str, command: str, args: list, multisync: bool = False,
                      multisync_hosts: str = "") -> str:
    """POST /api/command. fppd answers 200 with the command's output, or 500 with an error message."""
    body: dict = {"command": command, "args": [str(a) if not isinstance(a, str) else a for a in args]}
    if multisync:
        body.update(multisyncCommand=True, multisyncHosts=multisync_hosts)
    r = await _req(client, "POST", address, "command", json=body)
    if r.status_code >= 400:
        raise FppError(f"'{command}' failed: {r.text.strip()[:300] or f'HTTP {r.status_code}'}", 422 if r.status_code == 500 else 502)
    return r.text.strip()[:2000]


async def post(client, address: str, path: str, json=None) -> str:
    r = await _req(client, "POST", address, path, json=json)
    if r.status_code >= 400:
        raise FppError(f"{path}: HTTP {r.status_code} {r.text.strip()[:200]}")
    return r.text.strip()[:2000]


# ---------------------------------------------------------------- playlists
async def playlists(client, address: str) -> dict:
    names, playable, sequences = await asyncio.gather(
        get_json(client, address, "playlists"),
        get_json(client, address, "playlists/playable"),
        get_json(client, address, "sequence"),
    )
    return {"playlists": names or [], "playable": playable or [], "sequences": sequences or []}


async def playlist_detail(client, address: str, name: str) -> dict:
    d = await get_json(client, address, f"playlist/{quote(name, safe='')}")
    items = []
    for section in ("leadIn", "mainPlaylist", "leadOut"):
        for e in d.get(section) or []:
            items.append({
                "section": section, "type": e.get("type"), "enabled": bool(e.get("enabled", 1)),
                "name": e.get("sequenceName") or e.get("mediaName") or e.get("command") or e.get("name")
                        or e.get("type"),
                "duration": e.get("duration"),
            })
    info = d.get("playlistInfo") or {}
    return {"name": d.get("name", name), "description": d.get("desc") or None, "repeat": bool(d.get("repeat")),
            "random": bool(d.get("random")), "total_duration": info.get("total_duration"), "items": items}


# ---------------------------------------------------------------- schedule
def describe_entry(e: dict) -> dict:
    day = e.get("day")
    if isinstance(day, int) and day >= 0x10000:
        bits = [("Sun", 0x4000), ("Mon", 0x2000), ("Tue", 0x1000), ("Wed", 0x800), ("Thu", 0x400),
                ("Fri", 0x200), ("Sat", 0x100)]
        day_str = ", ".join(n for n, b in bits if day & b) or "No days"
    else:
        day_str = DAY_NAMES.get(day, str(day))
    kind = "command" if e.get("command") else ("sequence" if e.get("sequence") else "playlist")
    return {"kind": kind, "day_str": day_str, "stop_str": STOP_TYPES.get(e.get("stopType"), str(e.get("stopType")))}


async def schedule(client, address: str) -> dict:
    entries, computed, names = await asyncio.gather(get_json(client, address, "schedule"),
                                                    get_json(client, address, "fppd/schedule"),
                                                    get_json(client, address, "playlists"))
    sched = (computed or {}).get("schedule") or {}
    upcoming = []
    for it in sched.get("items") or []:
        upcoming.append({
            "entry": it.get("id"), "start": it.get("startTime"), "end": it.get("endTime"),
            "start_str": it.get("startTimeStr"), "end_str": it.get("endTimeStr"),
            "command": it.get("command"), "args": it.get("args") or [],
        })
    upcoming.sort(key=lambda u: u["start"] or 0)
    entries = entries or []
    warnings = await _entry_warnings(client, address, entries, set(names or []))
    return {
        "enabled": bool(sched.get("enabled", 1)),
        "entries": [{"index": i, **describe_entry(e), "warning": warnings.get(i), "raw": e} for i, e in enumerate(entries)],
        "upcoming": upcoming,
    }


async def _entry_warnings(client, address: str, entries: list[dict], playlists: set[str]) -> dict[int, str]:
    """Flag enabled, not-yet-finished playlist entries whose playlist is missing or has nothing in it."""
    today = date.today().isoformat()
    check: dict[int, str] = {}
    for i, e in enumerate(entries):
        name = e.get("playlist")
        end = str(e.get("endDate") or "")
        if not e.get("enabled") or e.get("command") or not name or name.lower().endswith(".fseq"):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) and end < today:
            continue   # already over
        check[i] = name
    warnings: dict[int, str] = {}
    details = {}
    for name in sorted(set(check.values()) & playlists):
        try:
            details[name] = await playlist_detail(client, address, name)
        except FppError:
            pass
    for i, name in check.items():
        if name not in playlists:
            warnings[i] = f"Playlist '{name}' does not exist on the player"
        elif name in details and not any(it["enabled"] for it in details[name]["items"]):
            warnings[i] = f"Playlist '{name}' is empty"
    return warnings


async def save_schedule(client, address: str, entries: list[dict]) -> None:
    await post(client, address, "schedule", json=entries)
    await post(client, address, "schedule/reload")


# ---------------------------------------------------------------- test mode
async def test_pattern_args(client, address: str, pattern: str) -> list[dict]:
    d = await get_json(client, address, f"fppd/testing/tests/{quote(pattern, safe='')}")
    return d.get("args") or []


async def test_start(client, address: str, pattern: str, interval_ms: int, args: list[str]) -> str:
    """Fill blank pattern arguments with this controller's own default (e.g. its channel range)."""
    spec = await test_pattern_args(client, address, pattern)
    filled = []
    for i, a in enumerate(spec):
        v = args[i] if i < len(args) and args[i] not in (None, "") else None
        if v is None:
            contents = a.get("contents") or []
            v = contents[0] if contents else a.get("default", "")
        filled.append(str(v))
    return await run_command(client, address, "Test Start", [str(interval_ms), pattern, *filled])


async def test_state(client, address: str) -> dict:
    return await get_json(client, address, "testmode")
