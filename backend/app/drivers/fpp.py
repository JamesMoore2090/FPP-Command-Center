import asyncio
import re
from datetime import datetime, timezone

from .base import Driver, HostStatus, Reading

STATUS_NAMES = {0: "idle", 1: "playing", 2: "stopping", 3: "stopping", 4: "paused", 5: "testing"}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _model(info: dict) -> str | None:
    hw = info.get("SubPlatform") or info.get("Variant") or info.get("Platform")
    cape = (info.get("capeInfo") or {}).get("name")
    return " / ".join(x for x in (hw, cape) if x) or None


def parse_fpp(status: dict, info: dict | None) -> HostStatus:
    info = info or {}
    temps, voltages = [], []
    for s in status.get("sensors") or []:
        v = _num(s.get("value"))
        if v is None:
            continue
        label = re.sub(r"[:\s]+$", "", s.get("label") or "").strip() or "Sensor"
        vt = (s.get("valueType") or "").lower()
        if vt == "temperature":
            temps.append(Reading(label=label, value=round(v, 1)))
        elif vt == "voltage":
            voltages.append(Reading(label=label, value=round(v, 2)))

    util = info.get("Utilization") or {}
    disk = (util.get("Disk") or {}).get("Media") or (util.get("Disk") or {}).get("Root") or {}
    playlist = status.get("current_playlist") or {}
    st_name = status.get("status_name") or STATUS_NAMES.get(status.get("status"))
    mode = status.get("mode_name") or info.get("Mode")

    return HostStatus(
        online=True,
        polled_at=datetime.now(timezone.utc),
        hostname=status.get("host_name") or info.get("HostName"),
        uuid=status.get("uuid") or info.get("uuid"),
        version=status.get("version") or info.get("Version"),
        mode=mode,
        model=_model(info),
        status=st_name,
        current_playlist=playlist.get("playlist") or None,
        current_sequence=status.get("current_sequence") or status.get("sequence_filename") or None,
        seconds_played=_num(status.get("seconds_played")),
        seconds_remaining=_num(status.get("seconds_remaining")),
        fppd=status.get("fppd"),
        volume=status.get("volume"),
        uptime_s=status.get("uptimeTotalSeconds"),
        cpu_pct=_round(util.get("CPU")),
        mem_pct=_round(util.get("Memory")),
        disk_free=disk.get("Free"),
        disk_total=disk.get("Total"),
        temps=temps,
        voltages=voltages,
        extra={
            "os_version": info.get("OSVersion"),
            "kernel": info.get("Kernel"),
            "branch": info.get("Branch"),
            "local_git": info.get("LocalGitVersion"),
            "remote_git": info.get("RemoteGitVersion"),
            "ips": info.get("IPs"),
            "outputs_enabled": status.get("channelOutputsEnabled"),
            "power_bad": status.get("powerBad"),
        },
    )


def _round(v):
    v = _num(v)
    return round(v, 1) if v is not None else None


class FppDriver(Driver):
    kind = "fpp"

    async def poll(self, address: str) -> HostStatus:
        base = f"http://{address}"
        status_r, info_r = await asyncio.gather(
            self.client.get(f"{base}/api/fppd/status"),
            self.client.get(f"{base}/api/system/info"),
        )
        status_r.raise_for_status()
        info = info_r.json() if info_r.status_code == 200 else None
        return parse_fpp(status_r.json(), info)

    async def identify(self, address: str, timeout: float) -> HostStatus | None:
        r = await self.client.get(f"http://{address}/api/system/info", timeout=timeout)
        if r.status_code != 200:
            return None
        try:
            info = r.json()
        except ValueError:
            return None
        if not isinstance(info, dict) or "HostName" not in info:
            return None
        return HostStatus(
            online=True,
            hostname=info.get("HostName"),
            uuid=info.get("uuid"),
            version=info.get("Version"),
            mode=info.get("Mode"),
            model=_model(info),
        )

    async def multisync_systems(self, address: str) -> list[dict]:
        r = await self.client.get(f"http://{address}/api/fppd/multiSyncSystems")
        r.raise_for_status()
        data = r.json()
        return data.get("systems", []) if isinstance(data, dict) else data
