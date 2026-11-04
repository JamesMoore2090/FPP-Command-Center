from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Host(Base):
    """A controller in the show. `kind` selects the driver (fpp, falcon; later wled, espixelstick)."""

    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))                 # display name, user editable
    address: Mapped[str] = mapped_column(String(255), unique=True)  # IP or hostname used for polling
    kind: Mapped[str] = mapped_column(String(20), default="fpp")
    hostname: Mapped[str | None] = mapped_column(String(100))      # as reported by the device
    uuid: Mapped[str | None] = mapped_column(String(100), unique=True)
    role: Mapped[str | None] = mapped_column(String(20))           # player / remote / bridge / standalone
    model: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual / discovered
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Sample(Base):
    """Raw status sample. Kept 7 days; rolled up to 1-minute / 15-minute tables in phase 6."""

    __tablename__ = "samples"
    __table_args__ = (Index("ix_samples_host_ts", "host_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    online: Mapped[bool] = mapped_column(Boolean)
    cpu_pct: Mapped[float | None] = mapped_column(Float)
    mem_pct: Mapped[float | None] = mapped_column(Float)
    disk_pct: Mapped[float | None] = mapped_column(Float)
    temp_max_c: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(30))
    data: Mapped[dict | None] = mapped_column(JSON)   # temps / voltages / sequence etc.
