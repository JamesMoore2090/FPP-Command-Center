"""Grandfather-father-son retention: keep the newest good backup of each of the last N days, weeks and months."""
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo


@dataclass
class Item:
    id: int
    created_at: datetime
    status: str
    pinned: bool


def select_to_delete(items: list[Item], tz: tzinfo, daily: int, weekly: int, monthly: int,
                     now: datetime, failed_days: int = 30, recent_hours: int = 48) -> set[int]:
    """Return ids to delete for one host. Pinned backups, the newest good backup and everything from the last
    `recent_hours` (so extra manual backups don't vanish right away) are always kept; failed/skipped records
    are kept for `failed_days`."""
    good = sorted((i for i in items if i.status == "ok"), key=lambda i: i.created_at, reverse=True)
    recent = now - timedelta(hours=recent_hours)
    keep: set[int] = {i.id for i in items if i.pinned or i.created_at >= recent}
    if good:
        keep.add(good[0].id)

    for limit, keyfn in (
        (daily, lambda d: d.date()),
        (weekly, lambda d: tuple(d.isocalendar()[:2])),
        (monthly, lambda d: (d.year, d.month)),
    ):
        seen = []
        for i in good:
            k = keyfn(i.created_at.astimezone(tz))
            if k in seen:
                continue
            if len(seen) >= limit:
                break
            seen.append(k)
            keep.add(i.id)   # newest backup in this period (list is newest first)

    delete = {i.id for i in good if i.id not in keep}
    cutoff = now - timedelta(days=failed_days)
    delete |= {i.id for i in items if i.status != "ok" and not i.pinned and i.created_at < cutoff}
    return delete
