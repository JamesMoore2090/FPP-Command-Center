import httpx

from .base import Driver, HostStatus, Reading
from .falcon import FalconDriver
from .fpp import FppDriver

DRIVER_CLASSES: dict[str, type[Driver]] = {d.kind: d for d in (FppDriver, FalconDriver)}
KINDS = list(DRIVER_CLASSES)


def make_drivers(client: httpx.AsyncClient) -> dict[str, Driver]:
    return {kind: cls(client) for kind, cls in DRIVER_CLASSES.items()}


__all__ = ["Driver", "HostStatus", "Reading", "FppDriver", "FalconDriver", "DRIVER_CLASSES", "KINDS", "make_drivers"]
