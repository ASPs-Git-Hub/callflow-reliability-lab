"""Call orchestrator: schedules attempts in local time, applies structured results idempotently,
escalates to a person with context. No telephony and no language model: the dialer is an injected callable
and results arrive as events. One serialized controller: every public method holds a lock; nothing is persisted."""
import threading

from .handoff import HandoffQueue
from .model import OUTCOMES, RETRYABLE, Policy, move, norm_phone
from .schedule import in_window, next_allowed, schedule_next

REQUIRED = ("event_id", "case_id", "attempt_no", "outcome", "confidence")
HUMAN_RESOLUTIONS = ("confirmed", "declined", "resolved", "cancelled")


def _text(v):
    return isinstance(v, str) and 0 < len(v) <= 128


class Orchestrator:
    def __init__(self, policy=None, dialer=None, dnc=()):
        self.policy = (policy or Policy()).validate()
        self.dialer = dialer or (lambda request: None)
        self.dnc = {norm_phone(p) for p in dnc}
        self.cases, self.seen = {}, set()      # seen holds (case_id, event_id)
        self.tickets = HandoffQueue()
        self.calls = []        # (case_id, attempt_no, at_utc, phone) for every call placed
        self.applied = {}      # (case_id, event_id) -> times applied
        self.stale_applied = 0
        self.halted = None     # set when the provider reports an account-level stop (budget, credit, payment)
        self.log = []          # operational log: (at, kind, detail)
        self.recent = []       # recent applied outcomes for alerting
        self._lock = threading.RLock()

    # ---- intake -------------------------------------------------------------------------------
    def add_case(self, case, now):
        with self._lock:
            if now.tzinfo is None:
                raise ValueError("now must be timezone-aware")
            if case.id in self.cases:
                raise ValueError(f"duplicate case id {case.id}")   # replacing a case would orphan its outstanding call
            self.cases[case.id] = case
            t = schedule_next(case, now, self.policy, first=True)
            if t is None:
                self._to_person(case, "sla_risk", now)
            else:
                case.next_attempt_at = t

    def add_dnc(self, phone):
        with self._lock:
            self.dnc.add(norm_phone(phone))

    def halt(self, reason, now):
        with self._lock:
            self.halted = reason
            self.log.append((now, "provider_halt", reason))

    def resume(self):
        with self._lock:
            self.halted = None

    # ---- clock tick ---------------------------------------------------------------------------
    def tick(self, now):
        with self._lock:
            p = self.policy
            for c in list(self.cases.values()):          # calls that never reported back
                self._expire(c, now)
            if self.halted:
                return
            in_call = sum(1 for c in self.cases.values() if c.state == "in_call")
            due = sorted((c for c in self.cases.values()
                          if c.state == "waiting" and c.next_attempt_at is not None and c.next_attempt_at <= now),
                         key=lambda c: c.appointment_start)  # nearest appointment first
            for c in due:
                if in_call >= p.max_concurrent:
                    break
                if norm_phone(c.phone) in self.dnc:      # checked at dispatch time, the list can change after scheduling
                    self._to_person(c, "do_not_call", now)
                    continue
                if now > c.appointment_start - p.stop_before_appt:
                    self._to_person(c, "sla_risk", now)
                    continue
                if len(c.attempts) >= p.max_attempts:
                    self._to_person(c, "attempts_exhausted", now)
                    continue
                if not in_window(now, c.tz, p):          # window closed since scheduling (DST, delays): reschedule, do not call
                    nxt = next_allowed(now, c.tz, p)
                    if nxt is None or nxt > c.appointment_start - p.stop_before_appt:
                        self._to_person(c, "sla_risk", now)
                    else:
                        c.next_attempt_at = nxt
                    continue
                n = len(c.attempts) + 1
                c.attempts.append({"no": n, "at": now, "outcome": None})
                move(c, "in_call", now, f"attempt {n}")
                c.in_call_since, c.next_attempt_at = now, None
                self.calls.append((c.id, n, now, c.phone))
                try:
                    self.dialer({"case_id": c.id, "attempt_no": n, "phone": c.phone, "at": now})
                except Exception as e:                   # one failing dial must not abort the batch or leave a phantom call
                    self.log.append((now, "dialer_error", f"{c.id}: {type(e).__name__}"))
                    self._apply(c, {"event_id": f"dialer_error:{c.id}:{n}", "outcome": "dropped", "confidence": 1.0}, now)
                    continue
                in_call += 1

    def _expire(self, c, now):
        """Deadline enforcement, also run when a result arrives so that completion versus timeout is deterministic."""
        if c.state == "in_call" and c.in_call_since is not None and now - c.in_call_since >= self.policy.call_timeout:
            c.attempts[-1]["timed_out"] = True
            self.log.append((now, "call_timeout", c.id))
            self._apply(c, {"event_id": f"timeout:{c.id}:{len(c.attempts)}", "outcome": "dropped", "confidence": 1.0}, now)

    # ---- results ------------------------------------------------------------------------------
    def handle_result(self, ev, now):
        with self._lock:
            if not isinstance(ev, dict) or any(k not in ev for k in REQUIRED):
                self.log.append((now, "rejected", "missing fields"))
                return {"status": "rejected", "reason": "missing fields"}
            if not _text(ev["event_id"]) or not _text(ev["case_id"]) or ev["outcome"] not in OUTCOMES \
                    or not isinstance(ev["attempt_no"], int) or isinstance(ev["attempt_no"], bool) or ev["attempt_no"] < 1 \
                    or isinstance(ev["confidence"], bool) or not isinstance(ev["confidence"], (int, float)) \
                    or not 0 <= ev["confidence"] <= 1:
                self.log.append((now, "rejected", "invalid values"))
                return {"status": "rejected", "reason": "invalid values"}
            c = self.cases.get(ev["case_id"])
            if c is None:                                   # not consumed: a redelivery after the case exists is still processed
                self.log.append((now, "unknown_case", ev["case_id"]))
                return {"status": "rejected", "reason": "unknown case"}
            key = (ev["case_id"], ev["event_id"])
            if key in self.seen:
                return {"status": "duplicate"}
            self.seen.add(key)
            if ev["outcome"] == "refused":                  # suppression is about the number, not about this attempt's freshness
                self.dnc.add(norm_phone(c.phone))
            self._expire(c, now)                            # a result after the deadline counts as late, deterministically
            current = ev["attempt_no"] == len(c.attempts)
            if c.state == "in_call" and current:
                self._apply(c, ev, now, key)
                return {"status": "applied"}
            if c.state == "waiting" and current and c.attempts[-1].get("timed_out"):
                return self._apply_late(c, ev, now, key)
            self.log.append((now, "stale_result", c.id))    # result for an earlier attempt or a case a person already owns
            return {"status": "stale"}

    def _apply(self, c, ev, now, key=None):
        k = key or (c.id, ev["event_id"])
        self.applied[k] = self.applied.get(k, 0) + 1
        out = ev["outcome"]
        c.attempts[-1]["outcome"] = out
        c.last_outcome, c.in_call_since = out, None
        self.recent.append((now, out, ev["confidence"]))
        p = self.policy
        if out in ("confirmed", "declined"):
            if ev["confidence"] >= p.confidence_threshold:
                move(c, out, now, "result")
            else:
                self._to_person(c, "low_confidence", now)
        elif out == "reschedule":
            self._to_person(c, "reschedule_requested", now)
        elif out == "wrong_number":
            self._to_person(c, "wrong_number", now)
        elif out == "refused":
            self.dnc.add(norm_phone(c.phone))
            self._to_person(c, "refused", now)
        elif out == "unclear":
            self._to_person(c, "unclear", now)
        elif out in RETRYABLE:
            if len(c.attempts) >= p.max_attempts:
                self._to_person(c, "attempts_exhausted", now)
            else:
                t = schedule_next(c, now, p)
                if t is None:
                    self._to_person(c, "sla_risk", now)
                else:
                    move(c, "waiting", now, f"retry after {out}")
                    c.next_attempt_at = t

    def _apply_late(self, c, ev, now, key):
        """A real result that arrived after the timeout for the attempt we gave up on: use it instead of calling again."""
        out = ev["outcome"]
        if out in RETRYABLE:                                # nothing new: the retry is already scheduled
            return {"status": "late_noop"}
        self.applied[key] = self.applied.get(key, 0) + 1
        c.attempts[-1]["outcome"], c.last_outcome, c.next_attempt_at = out, out, None
        self.recent.append((now, out, ev["confidence"]))
        if out in ("confirmed", "declined") and ev["confidence"] >= self.policy.confidence_threshold:
            move(c, out, now, "late result")
        else:
            reason = {"confirmed": "low_confidence", "declined": "low_confidence", "reschedule": "reschedule_requested",
                      "wrong_number": "wrong_number", "refused": "refused", "unclear": "unclear"}[out]
            self._to_person(c, reason, now)
        return {"status": "applied_late"}

    # ---- people -------------------------------------------------------------------------------
    def _to_person(self, c, reason, now):
        if c.state != "needs_person":
            move(c, "needs_person", now, reason)
        urgent = c.appointment_start - now <= self.policy.urgent_within
        return self.tickets.create(c, reason, "urgent" if urgent else "normal", now)

    def resolve_ticket(self, tid, resolution, now):
        with self._lock:
            if resolution not in HUMAN_RESOLUTIONS:
                return {"status": "rejected", "reason": "unknown resolution"}
            t = self.tickets.tickets.get(tid)
            if t is None:
                return {"status": "rejected", "reason": "unknown ticket"}
            if not self.tickets.resolve(tid, resolution, now):
                return {"status": "already_closed"}
            c = self.cases[t["case_id"]]
            if c.state == "needs_person":
                c.resolution = resolution
                move(c, "cancelled" if resolution == "cancelled" else "resolved", now, f"person: {resolution}")
            return {"status": "resolved"}
