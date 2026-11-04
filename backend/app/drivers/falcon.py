"""Falcon F16/F48 (v2/v3 firmware) driver using status.xml."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import asyncio

import httpx

from .base import Driver, HostDetail, HostStatus, OutputGroup, Port, Reading, VirtualString

_UPTIME = re.compile(r"(?:(\d+)\s*days?\s*)?(\d+):(\d+):(\d+)")


def _uptime_seconds(s: str | None) -> int | None:
    m = _UPTIME.search(s or "")
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def _fmt_uptime(s: int | None) -> str | None:
    if s is None:
        return None
    d, rem = divmod(s, 86400)
    return f"{d}d {rem // 3600}h {rem % 3600 // 60}m"


def _float(s: str | None) -> float | None:
    m = re.search(r"-?\d+(\.\d+)?", s or "")
    return float(m.group()) if m else None


def parse_status_xml(text: str) -> HostStatus:
    root = ET.fromstring(text)
    f = {c.tag: (c.text or "").strip() for c in root}
    if "fv" not in f:
        raise ValueError("not a Falcon status.xml")
    temps = []
    for i in (1, 2, 3):
        v = _float(f.get(f"t{i}"))
        # t1 is the board sensor; unconnected external probes read near zero or negative.
        if v is not None and (i == 1 or v > 5):
            temps.append(Reading(label="Board" if i == 1 else f"Temp {i}", value=v))
    voltages = [Reading(label=f"V{i}", value=v) for i in (1, 2) if (v := _float(f.get(f"v{i}"))) is not None]
    np_ = f.get("np")
    return HostStatus(
        online=True,
        polled_at=datetime.now(timezone.utc),
        hostname=f.get("n") or None,
        version=f.get("fv"),
        mode="standalone",
        model=f"Falcon F{np_}" if np_ else "Falcon",
        uptime_s=_uptime_seconds(f.get("u")),
        temps=temps,
        voltages=voltages,
        extra={k: f.get(k) for k in ("p", "m", "a", "np", "ns", "k0", "k1", "k2", "f", "zf", "zs", "d", "t")},
    )


def _i(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_strings_xml(text: str) -> list[Port]:
    """strings.xml: one <vs> per virtual string. p = 0-based port, y = name, s = start channel (as the
    controller reports it), c = pixel count, g = grouping, d = direction (1 = reverse), e = enabled."""
    root = ET.fromstring(text)
    ports: dict[int, Port] = {}
    for vs in root.iter("vs"):
        a = vs.attrib
        num = (_i(a.get("p")) or 0) + 1
        port = ports.setdefault(num, Port(number=num))
        port.strings.append(VirtualString(
            description=a.get("y") or None, start_channel=_i(a.get("s")), pixels=_i(a.get("c")) or 0,
            group=_i(a.get("g")), reverse=a.get("d") == "1", enabled=a.get("e", "1") == "1",
        ))
    return [ports[k] for k in sorted(ports)]


class FalconDriver(Driver):
    kind = "falcon"

    async def poll(self, address: str) -> HostStatus:
        r = await self.client.get(f"http://{address}/status.xml")
        r.raise_for_status()
        return parse_status_xml(r.text)

    async def detail(self, address: str) -> HostDetail:
        base = f"http://{address}"

        async def get(path):
            try:
                r = await self.client.get(f"{base}/{path}", timeout=10)
                r.raise_for_status()
                return r.text
            except httpx.HTTPError as e:
                return e

        # settings.xml universes / packet.xml are skipped: on DDP-fed controllers they hold stale garbage.
        names = ["status.xml", "strings.xml"]
        res = dict(zip(names, await asyncio.gather(*(get(n) for n in names))))
        errors = [f"{k}: {type(v).__name__}" for k, v in res.items() if isinstance(v, Exception)]
        if isinstance(res["status.xml"], Exception):
            raise res["status.xml"]
        res = {k: (None if isinstance(v, Exception) else v) for k, v in res.items()}

        st = parse_status_xml(res["status.xml"])
        ports: list[Port] = []
        try:
            if res["strings.xml"]:
                ports = parse_strings_xml(res["strings.xml"])
        except ET.ParseError as e:
            errors.append(f"XML parse error: {e}")
        x = st.extra
        info = {
            "Name": st.hostname, "Model": st.model, "Firmware": st.version,
            "Ports": x.get("np"), "Smart receiver ports": x.get("ns"),
            "Uptime": _fmt_uptime(st.uptime_s), "Controller date / time": f"{x.get('d')} {x.get('t')}".strip(),
            "Pixels per bank": " / ".join(str(x[k]) for k in ("k0", "k1", "k2") if x.get(k)) or None,
        }
        for t in st.temps:
            info[f"Temperature ({t.label})"] = f"{t.value:.0f} °C"
        for v in st.voltages:
            info[f"Voltage {v.label}"] = f"{v.value} V"
        return HostDetail(
            info=info,
            outputs=[OutputGroup(name="Pixel string ports", ports=ports)] if ports else [],
            errors=errors, actions=self.actions,
        )

    async def identify(self, address: str, timeout: float) -> HostStatus | None:
        r = await self.client.get(f"http://{address}/status.xml", timeout=timeout)
        if r.status_code != 200 or "<fv>" not in r.text:
            return None
        try:
            return parse_status_xml(r.text)
        except (ET.ParseError, ValueError):
            return None
