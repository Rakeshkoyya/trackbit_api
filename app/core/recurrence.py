"""Recurrence rules — human presets only (PRD §S5: never expose cron/RRULE).

A rule is JSON like:
    {"freq": "daily", "time": "10:00"}
    {"freq": "weekdays", "time": "09:00"}              # Mon–Fri
    {"freq": "weekly", "days": ["mon","fri"], "time": "10:00"}
    {"freq": "monthly", "day": 15, "time": "08:00"}    # day-of-month (clamped to month end)
    {"freq": "custom", "interval_days": 3, "time": "07:00", "start": "2026-06-01"}

`time` is optional; absent => all-day instances (due at org-local end of day).
All occurrence math is done in the org's local calendar.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.core.exceptions import ValidationError

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # Mon=0
_WEEKDAY_IDX = {d: i for i, d in enumerate(_WEEKDAYS)}
_FREQS = {"daily", "weekdays", "weekly", "monthly", "custom"}


def validate_rule(rule: dict) -> dict:
    """Validate + normalize a recurrence rule. Raises ValidationError if invalid."""
    if not isinstance(rule, dict):
        raise ValidationError("Recurrence must be an object.", code="bad_recurrence")
    freq = rule.get("freq")
    if freq not in _FREQS:
        raise ValidationError(f"Unknown recurrence: {freq}", code="bad_recurrence")

    out: dict = {"freq": freq}

    # Optional time-of-day "HH:MM".
    t = rule.get("time")
    if t is not None:
        out["time"] = _parse_time_str(t)

    if freq == "weekly":
        days = rule.get("days") or []
        if not days or any(d not in _WEEKDAY_IDX for d in days):
            raise ValidationError("Weekly recurrence needs valid days.", code="bad_recurrence")
        # de-dup, keep canonical order
        out["days"] = [d for d in _WEEKDAYS if d in set(days)]
    elif freq == "monthly":
        day = rule.get("day")
        if not isinstance(day, int) or not (1 <= day <= 31):
            raise ValidationError("Monthly recurrence needs a day 1–31.", code="bad_recurrence")
        out["day"] = day
    elif freq == "custom":
        n = rule.get("interval_days")
        if not isinstance(n, int) or n < 1:
            raise ValidationError("Custom recurrence needs interval_days ≥ 1.", code="bad_recurrence")
        out["interval_days"] = n
        if rule.get("start"):
            out["start"] = _parse_date_str(rule["start"]).isoformat()

    return out


def occurs_on(rule: dict, day: date) -> bool:
    """Does this rule produce an occurrence on the given org-local date?"""
    freq = rule["freq"]
    if freq == "daily":
        return True
    if freq == "weekdays":
        return day.weekday() < 5
    if freq == "weekly":
        return _WEEKDAYS[day.weekday()] in rule.get("days", [])
    if freq == "monthly":
        return day.day == _clamp_month_day(rule["day"], day.year, day.month)
    if freq == "custom":
        start = _parse_date_str(rule["start"]) if rule.get("start") else day
        if day < start:
            return False
        return (day - start).days % rule["interval_days"] == 0
    return False


def next_occurrences(rule: dict, *, after: date, count: int, horizon_days: int = 400) -> list[date]:
    """The next `count` occurrence dates strictly after `after`."""
    out: list[date] = []
    d = after + timedelta(days=1)
    for _ in range(horizon_days):
        if occurs_on(rule, d):
            out.append(d)
            if len(out) >= count:
                break
        d += timedelta(days=1)
    return out


def occurrences_in(rule: dict, *, start: date, end: date) -> list[date]:
    """All occurrence dates in [start, end] inclusive."""
    out: list[date] = []
    d = start
    while d <= end:
        if occurs_on(rule, d):
            out.append(d)
        d += timedelta(days=1)
    return out


def due_time(rule: dict) -> time | None:
    """The time-of-day for occurrences (parsed), or None for all-day."""
    t = rule.get("time")
    if t is None:
        return None
    if isinstance(t, time):
        return t
    hh, mm = _parse_time_str(t).split(":")
    return time(int(hh), int(mm))


def _parse_time_str(t: str) -> str:
    try:
        hh, mm = t.split(":")
        time(int(hh), int(mm))  # validate
        return f"{int(hh):02d}:{int(mm):02d}"
    except (ValueError, AttributeError) as exc:
        raise ValidationError("Time must be HH:MM.", code="bad_recurrence") from exc


def _parse_date_str(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise ValidationError("Date must be YYYY-MM-DD.", code="bad_recurrence") from exc


def _clamp_month_day(day: int, year: int, month: int) -> int:
    """Clamp a requested day-of-month to the last valid day (e.g. 31 → 30 in April)."""
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - timedelta(days=1)).day
    return min(day, last)
