"""Falcon F16/F48 (v2/v3 firmware) driver using status.xml."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .base import Driver, HostStatus, Reading

_UPTIME = re.compile(r"(?:(\d+)\s*days?\s*)?(\d+):(\d+):(\d+)")


def _uptime_seconds(s: str | None) -> int | None:
    m = _UPTIME.search(s or "")
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


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
        extra={k: f.get(k) for k in ("p", "m", "a", "ns", "k0", "k1", "k2", "f", "zf", "zs", "d", "t")},
    )


class FalconDriver(Driver):
    kind = "falcon"

    async def poll(self, address: str) -> HostStatus:
        r = await self.client.get(f"http://{address}/status.xml")
        r.raise_for_status()
        return parse_status_xml(r.text)

    async def identify(self, address: str, timeout: float) -> HostStatus | None:
        r = await self.client.get(f"http://{address}/status.xml", timeout=timeout)
        if r.status_code != 200 or "<fv>" not in r.text:
            return None
        try:
            return parse_status_xml(r.text)
        except (ET.ParseError, ValueError):
            return None
