"""NaiveOrchestrator: the same interface with the defects seen in real call automation.
  N1 ignores the customer's time zone (uses UTC wall clock)     N2 retries every tick, no gap, no limit
  N3 never checks a do-not-call list                              N4 applies duplicate result events again
  N5 accepts stale results                                        N6 trusts low-confidence confirmations
  N7 opens a new ticket every time a case needs a person         N8 no call timeout, no appointment cutoff
  N9 crashes on malformed events
"""
from datetime import timedelta

from .handoff import HandoffQueue
from .model import OUTCOMES, Policy, RETRYABLE


class _Dup(HandoffQueue):
    def create(self, case, reason, priority, now, context=None):   # N7: no idempotency key
        tid = f"T{len(self.tickets) + 1:05d}"
        t = {"id": tid, "case_id": case.id, "reason": reason, "priority": priority, "created_at": now,
             "status": "open", "resolution": None, "context": {}}
        self.tickets[tid] = t
        return t


class NaiveOrchestrator:
    def __init__(self, policy=None, dialer=None, dnc=()):
        self.policy = policy or Policy()
        self.dialer = dialer or (lambda request: None)
        self.dnc = set()
        self.cases = {}
        self.tickets = _Dup()
        self.calls, self.applied, self.stale_applied, self.log, self.recent = [], {}, 0, [], []

    def add_case(self, case, now):
        self.cases[case.id] = case
        case.next_attempt_at = now

    def add_dnc(self, phone):
        pass

    def halt(self, reason, now):
        pass

    def tick(self, now):
        for c in list(self.cases.values()):
            if c.state == "waiting" and c.next_attempt_at is not None and c.next_attempt_at <= now:
                n = len(c.attempts) + 1
                c.attempts.append({"no": n, "at": now, "outcome": None})
                c.state, c.in_call_since, c.next_attempt_at = "in_call", now, None   # no transition table
                self.calls.append((c.id, n, now, c.phone))
                self.dialer({"case_id": c.id, "attempt_no": n, "phone": c.phone, "at": now})

    def handle_result(self, ev, now):
        c = self.cases[ev["case_id"]]                       # N9: KeyError on malformed events
        k = (ev["case_id"], ev["event_id"])
        self.applied[k] = self.applied.get(k, 0) + 1   # N4: no dedupe
        if c.state != "in_call" or ev["attempt_no"] != len(c.attempts):
            self.stale_applied += 1                          # N5: applied anyway
        out = ev["outcome"]
        if c.attempts:
            c.attempts[-1]["outcome"] = out
        c.last_outcome = out
        self.recent.append((now, out, ev["confidence"]))
        if out in ("confirmed", "declined"):
            c.state = out                                    # N6: ignores confidence
        elif out in RETRYABLE:
            c.state, c.next_attempt_at = "waiting", now      # N2
        else:
            c.state = "needs_person"
            self.tickets.create(c, out, "normal", now)
        return {"status": "applied"}

    def resolve_ticket(self, tid, resolution, now):
        t = self.tickets.tickets[tid]
        t["status"] = "closed"
        self.cases[t["case_id"]].state = "resolved"
        return {"status": "resolved"}
