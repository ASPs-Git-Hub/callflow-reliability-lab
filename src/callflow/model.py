"""Case model, fixed list of states, outcome vocabulary and an explicit transition table.
Fictional domain: a plumbing company confirming service appointments by automated call. Synthetic data only."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

STATES = ("waiting", "in_call", "confirmed", "declined", "needs_person", "resolved", "cancelled")
ALLOWED = {
    "waiting": {"in_call", "needs_person", "cancelled", "confirmed", "declined"},   # confirmed/declined: a late real result
    "in_call": {"waiting", "confirmed", "declined", "needs_person", "cancelled"},
    "needs_person": {"resolved", "cancelled"},
    "confirmed": set(),
    "declined": set(),
    "resolved": set(),
    "cancelled": set(),
}
OUTCOMES = ("confirmed", "declined", "reschedule", "no_answer", "voicemail", "wrong_number", "refused", "dropped", "unclear")
RETRYABLE = frozenset({"no_answer", "voicemail", "dropped"})


class IllegalTransition(Exception):
    pass


@dataclass
class Policy:
    window_start_hour: int = 9          # customer's local time, inclusive
    window_end_hour: int = 18           # exclusive; a call placed just before it can run past it
    allowed_weekdays: frozenset = frozenset({0, 1, 2, 3, 4, 5})  # Monday to Saturday
    min_gap: timedelta = timedelta(hours=3)
    max_attempts: int = 4
    stop_before_appt: timedelta = timedelta(hours=2)
    max_concurrent: int = 20
    call_timeout: timedelta = timedelta(minutes=20)
    confidence_threshold: float = 0.8
    urgent_within: timedelta = timedelta(hours=24)

    def validate(self):
        """Calling-hour rules differ by country and must come from counsel; this lab only requires a sane window
        that stays clear of the 00:00 to 06:00 hours where daylight-saving transitions happen."""
        if not (6 <= self.window_start_hour < self.window_end_hour <= 22):
            raise ValueError("call window must satisfy 6 <= start < end <= 22 (local hours)")
        if self.max_attempts < 1 or self.max_concurrent < 1:
            raise ValueError("max_attempts and max_concurrent must be at least 1")
        return self


@dataclass
class Case:
    id: str
    phone: str
    tz: str                              # IANA name, e.g. "America/Chicago"
    appointment_start: datetime          # must be timezone-aware; stored in UTC
    state: str = "waiting"
    attempts: list = field(default_factory=list)
    next_attempt_at: datetime = None
    in_call_since: datetime = None
    last_outcome: str = None
    resolution: str = None
    history: list = field(default_factory=list)   # (at, from_state, to_state, reason)

    def __post_init__(self):
        if not isinstance(self.appointment_start, datetime) or self.appointment_start.tzinfo is None:
            raise ValueError("appointment_start must be a timezone-aware datetime")
        self.appointment_start = self.appointment_start.astimezone(UTC)   # elapsed-time arithmetic is done in UTC


def move(case, new_state, now, reason=""):
    if new_state not in ALLOWED.get(case.state, set()):
        raise IllegalTransition(f"{case.id}: {case.state} -> {new_state}")
    case.history.append((now, case.state, new_state, reason))
    case.state = new_state


def norm_phone(p):
    """E.164 digits without the plus. A bare 10-digit number is assumed to be a US/Canada number (US-first intake);
    anything else keeps its full digits, so numbers from different countries never collide."""
    digits = "".join(ch for ch in str(p) if ch.isdigit())
    return "1" + digits if len(digits) == 10 else digits


def mask_phone(p):
    d = norm_phone(p)
    return "***" + d[-4:] if d else ""
