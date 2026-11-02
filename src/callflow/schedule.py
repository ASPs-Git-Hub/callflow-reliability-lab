"""Call-window scheduling in the customer's local wall-clock time (DST-safe via zoneinfo)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .model import UTC


def local(dt_utc, tz):
    return dt_utc.astimezone(ZoneInfo(tz))


def in_window(dt_utc, tz, policy):
    l = local(dt_utc, tz)
    return l.weekday() in policy.allowed_weekdays and policy.window_start_hour <= l.hour < policy.window_end_hour


def next_allowed(dt_utc, tz, policy, horizon_days=14):
    """Earliest instant at or after dt_utc inside the customer's local call window, or None."""
    z = ZoneInfo(tz)
    l = dt_utc.astimezone(z)
    for day in range(horizon_days + 1):
        d = (l + timedelta(days=day)).date()
        if d.weekday() not in policy.allowed_weekdays:
            continue
        start = datetime(d.year, d.month, d.day, policy.window_start_hour, tzinfo=z)
        end = datetime(d.year, d.month, d.day, policy.window_end_hour, tzinfo=z)
        cand = max(l, start) if day == 0 else start
        if cand < end:
            return cand.astimezone(UTC)
    return None


def schedule_next(case, after_utc, policy, first=False):
    """Time of the next attempt, or None if no attempt can still happen before the cutoff."""
    base = after_utc if first else after_utc + policy.min_gap
    t = next_allowed(base, case.tz, policy)
    if t is None or t > case.appointment_start - policy.stop_before_appt:
        return None
    return t
