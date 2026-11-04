"""Driver interface. Each controller kind (fpp, falcon, later wled/espixelstick) implements `poll` and
`identify`, returning the normalized HostStatus that the collector, dashboard and alerts consume."""
from abc import ABC, abstractmethod
from datetime import datetime

import httpx
from pydantic import BaseModel, Field, computed_field


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


class VirtualString(BaseModel):
    description: str | None = None
    start_channel: int | None = None     # 1-based absolute channel
    pixels: int = 0
    group: int | None = None
    reverse: bool | None = None
    brightness: int | None = None        # percent where known
    color_order: str | None = None
    enabled: bool = True


class Port(BaseModel):
    number: int                          # 1-based, as printed on the board
    protocol: str | None = None
    strings: list[VirtualString] = Field(default_factory=list)
    current_ma: int | None = None        # from fppd/ports on boards with current sensing
    fuse_ok: bool | None = None
    enabled: bool | None = None

    @computed_field
    @property
    def pixels(self) -> int:
        return sum(v.pixels for v in self.strings)


class Universe(BaseModel):
    universe: int
    start_channel: int | None = None
    size: int | None = None
    protocol: str | None = None
    packets: int | None = None


class OutputGroup(BaseModel):
    """One channel-output config (e.g. 'K8-Pro pixel strings', 'DMX-Open on ttyAMA0')."""
    name: str
    enabled: bool = True
    start_channel: int | None = None
    channel_count: int | None = None
    ports: list[Port] = Field(default_factory=list)


class ActionSpec(BaseModel):
    id: str
    label: str
    danger: bool = False                 # reboot / shutdown / stop: red button + confirmation
    confirm: str | None = None           # confirmation text; None = no confirmation


class HostDetail(BaseModel):
    info: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    outputs: list[OutputGroup] = Field(default_factory=list)
    universes: list[Universe] = Field(default_factory=list)
    interfaces: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)     # sections that failed to load
    has_logs: bool = False
    actions: list[ActionSpec] = Field(default_factory=list)


class Driver(ABC):
    kind: str
    actions: list[ActionSpec] = []

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    @abstractmethod
    async def poll(self, address: str) -> HostStatus:
        """Fetch current status. Raise on network/HTTP errors; the collector marks the host offline."""

    async def detail(self, address: str) -> HostDetail:
        """Driver-specific detail page data (system info, outputs/ports, universes). Slower than poll()."""
        return HostDetail(actions=self.actions)

    async def run_action(self, address: str, action: str) -> str:
        raise NotImplementedError(action)

    @abstractmethod
    async def identify(self, address: str, timeout: float) -> HostStatus | None:
        """Cheap probe used by subnet discovery. Return None if the address is not this kind of device."""
