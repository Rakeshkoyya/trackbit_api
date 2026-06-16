"""Org-timezone helpers. 'Today' and due-time logic are computed in org-local
time; all timestamps are stored/compared in UTC (PRD: org timezone governs)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def org_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def org_now(tz_name: str) -> datetime:
    return datetime.now(org_zone(tz_name))


def org_day_bounds(tz_name: str, ref: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """Return (start_utc, end_utc, now_local) for the org-local day containing `ref`."""
    tz = org_zone(tz_name)
    now_local = (ref or datetime.now(UTC)).astimezone(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC), now_local


def org_local_date(tz_name: str, dt: datetime) -> date:
    """The org-local calendar date a UTC/aware timestamp falls on."""
    return dt.astimezone(org_zone(tz_name)).date()


def org_day_span(tz_name: str, days: int, ref: datetime | None = None) -> tuple[datetime, datetime, list[date]]:
    """UTC bounds + ordered org-local dates for the last `days` org-local days
    ending with today (inclusive). Used by reports/history windows."""
    today_start, today_end, now_local = org_day_bounds(tz_name, ref)
    start = today_start - timedelta(days=days - 1)
    dates = [(now_local.date() - timedelta(days=days - 1 - i)) for i in range(days)]
    return start, today_end, dates


def org_due_at(tz_name: str, day: date, t: time | None) -> tuple[datetime, bool]:
    """Combine an org-local date + time into a UTC due timestamp.

    Returns (due_at_utc, all_day). When `t` is None the instance is all-day and
    due at org-local end of day (so it lands in the 'due today' bucket).
    """
    tz = org_zone(tz_name)
    if t is None:
        local = datetime.combine(day, time(23, 59), tzinfo=tz)
        return local.astimezone(UTC), True
    local = datetime.combine(day, t, tzinfo=tz)
    return local.astimezone(UTC), False
