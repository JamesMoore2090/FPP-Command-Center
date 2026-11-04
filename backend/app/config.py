"""Runtime settings, read from environment variables prefixed FPPCC_ (see deploy/fppcc.env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FPPCC_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./fppcc-dev.db"

    # Collector
    poll_interval: float = 5.0          # seconds between status polls of each controller
    poll_timeout: float = 3.0           # per-request HTTP timeout
    offline_after_failures: int = 2     # consecutive failed polls before a host is shown offline
    sample_interval: float = 15.0       # seconds between raw samples written to the DB
    raw_sample_retention_days: int = 7

    # Discovery
    discovery_subnets: str = "10.100.100.0/24,10.55.1.0/24"   # comma separated, in address preference order
    discovery_interval: float = 600.0   # seconds; 0 disables periodic discovery
    discovery_on_startup: bool = True
    probe_timeout: float = 1.5
    probe_concurrency: int = 64

    # Alert thresholds (used for dashboard alerts now, HA/MQTT in phase 6)
    temp_warn_c: float = 70.0
    cpu_warn_pct: float = 90.0
    disk_warn_pct: float = 90.0

    run_collector: bool = True          # set false in tests

    @property
    def subnets(self) -> list[str]:
        return [s.strip() for s in self.discovery_subnets.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
