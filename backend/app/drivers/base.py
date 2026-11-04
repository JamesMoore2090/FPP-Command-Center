"""Driver interface. Each controller kind (fpp, falcon, later wled/espixelstick) implements `poll` and
`identify`, returning the normalized HostStatus that the collector, dashboard and alerts consume."""
from abc import ABC, abstractmethod
from datetime import datetime

import httpx
from pydantic import BaseModel, Field


class Reading(BaseModel):
    label: str
    value: float


class HostStatus(BaseModel):
    online: bool = False
    error: str | None = None
    polled_at: datetime | None = None

    hostname: str | None = None
    uuid: str | None = None
    version: str | None = None
    mode: str | None = None              # player / remote / bridge / standalone
    model: str | None = None             # hardware, e.g. "Raspberry Pi 4 / K8-Pi"
    status: str | None = None            # playing / idle / paused / testing / stopped
    current_playlist: str | None = None
    current_sequence: str | None = None
    seconds_played: float | None = None
    seconds_remaining: float | None = None
    fppd: str | None = None              # running / stopped (FPP only)
    volume: int | None = None
    uptime_s: int | None = None
    cpu_pct: float | None = None
    mem_pct: float | None = None
    disk_free: int | None = None
    disk_total: int | None = None
    temps: list[Reading] = Field(default_factory=list)      # degrees C
    voltages: list[Reading] = Field(default_factory=list)   # volts
    extra: dict = Field(default_factory=dict)

    @property
    def disk_pct(self) -> float | None:
        if self.disk_total:
            return round(100 * (1 - (self.disk_free or 0) / self.disk_total), 1)
        return None

    @property
    def temp_max(self) -> float | None:
        return max((t.value for t in self.temps), default=None)


class Driver(ABC):
    kind: str

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    @abstractmethod
    async def poll(self, address: str) -> HostStatus:
        """Fetch current status. Raise on network/HTTP errors; the collector marks the host offline."""

    @abstractmethod
    async def identify(self, address: str, timeout: float) -> HostStatus | None:
        """Cheap probe used by subnet discovery. Return None if the address is not this kind of device."""
