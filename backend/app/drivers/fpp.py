import asyncio
import re
from datetime import datetime, timezone

import httpx

from .base import ActionSpec, Driver, HostDetail, HostStatus, OutputGroup, Port, Reading, VirtualString

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


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_pixel_outputs(data: dict | None, port_status: list | None) -> list[OutputGroup]:
    """co-pixelStrings (Pi capes) / co-bbbStrings (BeagleBone capes). FPP stores startChannel 0-based."""
    status_by_port = {}
    for i, ps in enumerate(port_status or []):
        num = _int(str(ps.get("name", "")).removeprefix("Port ").strip()) or i + 1
        status_by_port[num] = ps
    groups = []
    for co in (data or {}).get("channelOutputs") or []:
        ports = []
        for o in co.get("outputs") or []:
            num = (_int(o.get("portNumber")) or 0) + 1
            strings = [
                VirtualString(
                    description=v.get("description") or None,
                    start_channel=(_int(v.get("startChannel")) or 0) + 1,
                    pixels=_int(v.get("pixelCount")) or 0,
                    group=_int(v.get("groupCount")),
                    reverse=bool(v.get("reverse")),
                    brightness=_int(v.get("brightness")),
                    color_order=v.get("colorOrder"),
                )
                for v in o.get("virtualStrings") or []
            ]
            st = status_by_port.get(num, {})
            ports.append(Port(
                number=num, protocol=o.get("protocol"), strings=strings,
                current_ma=_int(st.get("ma")), fuse_ok=st.get("status") if "status" in st else None,
                enabled=st.get("enabled") if "enabled" in st else None,
            ))
        name = co.get("subType") or co.get("type") or "Pixel strings"
        groups.append(OutputGroup(
            name=f"{name} pixel strings" if "string" not in name.lower() else name,
            enabled=bool(co.get("enabled", 1)), start_channel=None,
            channel_count=_int(co.get("channelCount")) if (_int(co.get("channelCount")) or 0) > 0 else None,
            ports=ports,
        ))
    return groups


def parse_other_outputs(data: dict | None) -> list[OutputGroup]:
    out = []
    for co in (data or {}).get("channelOutputs") or []:
        label = " ".join(str(x) for x in (co.get("type"), co.get("device") or co.get("host") or co.get("address")) if x)
        out.append(OutputGroup(
            name=label or "Output", enabled=bool(co.get("enabled")),
            start_channel=_int(co.get("startChannel")), channel_count=_int(co.get("channelCount")),
        ))
    return out


def parse_interfaces(data) -> list[dict]:
    out = []
    for i in data if isinstance(data, list) else []:
        if i.get("ifname") == "lo":
            continue
        addrs = [f"{a.get('local')}/{a.get('prefixlen')}" for a in i.get("addr_info") or [] if a.get("family") == "inet"]
        out.append({"name": i.get("ifname"), "state": i.get("operstate"), "mac": i.get("address"),
                    "addresses": ", ".join(addrs) or None})
    return out


def parse_info(info: dict) -> dict:
    util = info.get("Utilization") or {}
    cape = info.get("capeInfo") or {}
    return {
        "Hostname": info.get("HostName"),
        "Description": info.get("HostDescription") or None,
        "Mode": info.get("Mode"),
        "FPP version": info.get("Version"),
        "Branch": info.get("Branch"),
        "Git (local / remote)": f"{info.get('LocalGitVersion')} / {info.get('RemoteGitVersion')}"
                                if info.get("LocalGitVersion") else None,
        "Platform": info.get("Platform"),
        "Hardware": info.get("SubPlatform") or info.get("Variant"),
        "Cape": " ".join(x for x in (cape.get("name"), cape.get("version") and f"v{cape['version']}") if x) or None,
        "OS": info.get("OSVersion"),
        "OS release": info.get("OSRelease"),
        "Kernel": info.get("Kernel"),
        "IP addresses": ", ".join(info.get("IPs") or []) or None,
        "Channel ranges": info.get("channelRanges") or None,
        "Memory used %": _round(util.get("Memory")),
        "UUID": info.get("uuid"),
    }


FPP_ACTIONS = [
    ActionSpec(id="restart_fppd", label="Restart fppd",
               confirm="Restarting fppd interrupts anything playing on this controller for a few seconds."),
    ActionSpec(id="stop_fppd", label="Stop fppd", danger=True,
               confirm="Stopping fppd stops all output from this controller until it is started again."),
    ActionSpec(id="start_fppd", label="Start fppd"),
    ActionSpec(id="reboot", label="Reboot", danger=True,
               confirm="The controller will be offline for about a minute while it reboots."),
    ActionSpec(id="shutdown", label="Shut down", danger=True,
               confirm="The controller powers off and stays off until someone power-cycles it."),
]
_ACTION_PATHS = {"restart_fppd": "system/fppd/restart", "stop_fppd": "system/fppd/stop",
                 "start_fppd": "system/fppd/start", "reboot": "system/reboot", "shutdown": "system/shutdown"}


class FppDriver(Driver):
    kind = "fpp"
    actions = FPP_ACTIONS

    async def poll(self, address: str) -> HostStatus:
        base = f"http://{address}"
        status_r, info_r = await asyncio.gather(
            self.client.get(f"{base}/api/fppd/status"),
            self.client.get(f"{base}/api/system/info"),
        )
        status_r.raise_for_status()
        info = info_r.json() if info_r.status_code == 200 else None
        return parse_fpp(status_r.json(), info)

    async def detail(self, address: str) -> HostDetail:
        base = f"http://{address}/api"
        names = ["system/info", "channel/output/co-pixelStrings", "channel/output/co-bbbStrings",
                 "channel/output/co-other", "fppd/ports", "network/interface", "fppd/warnings"]

        async def get(path):
            try:
                r = await self.client.get(f"{base}/{path}", timeout=10)
            except httpx.HTTPError as e:
                return e
            if r.status_code == 404:
                return None          # e.g. co-pixelStrings on a BeagleBone, co-bbbStrings on a Pi
            try:
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as e:
                return e

        res = dict(zip(names, await asyncio.gather(*(get(n) for n in names))))
        errors = [f"{k}: {type(v).__name__}" for k, v in res.items() if isinstance(v, Exception)]
        res = {k: (None if isinstance(v, Exception) else v) for k, v in res.items()}
        if res["system/info"] is None and len(errors) == len(names):
            raise httpx.ConnectError("controller not reachable")

        warnings = res["fppd/warnings"]
        if isinstance(warnings, dict):
            warnings = warnings.get("warnings") or []
        return HostDetail(
            info=parse_info(res["system/info"] or {}),
            outputs=parse_pixel_outputs(res["channel/output/co-pixelStrings"], res["fppd/ports"])
                    + parse_pixel_outputs(res["channel/output/co-bbbStrings"], res["fppd/ports"])
                    + parse_other_outputs(res["channel/output/co-other"]),
            interfaces=parse_interfaces(res["network/interface"]),
            warnings=[w if isinstance(w, str) else (w.get("message") or str(w)) for w in warnings or []],
            errors=errors,
            has_logs=True,
            actions=self.actions,
        )

    async def run_action(self, address: str, action: str) -> str:
        path = _ACTION_PATHS.get(action)
        if path is None:
            raise NotImplementedError(action)
        try:
            r = await self.client.post(f"http://{address}/api/{path}", timeout=15)
        except httpx.ReadTimeout:
            return "sent (controller did not answer before going down)"   # normal for reboot/shutdown
        except httpx.RemoteProtocolError:
            return "sent (connection closed by controller)"
        r.raise_for_status()
        return r.text[:500] or "ok"

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
