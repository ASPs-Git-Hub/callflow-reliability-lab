"""Human handoff queue: one ticket per (case, reason), priority, idempotent resolve."""
from .model import mask_phone

PRIORITY_RANK = {"urgent": 0, "normal": 1}
ALLOWED_CONTEXT = ("note", "detail")      # callers may add these free-text fields, nothing that could unmask protected ones


class HandoffQueue:
    def __init__(self):
        self.tickets = {}      # id -> ticket dict
        self._by_key = {}      # (case_id, reason) -> id

    def create(self, case, reason, priority, now, context=None):
        key = (case.id, reason)
        if key in self._by_key:                      # retries and repeated triggers never duplicate a ticket
            t = self.tickets[self._by_key[key]]
            if PRIORITY_RANK[priority] < PRIORITY_RANK[t["priority"]]:
                t["priority"] = priority             # but a re-trigger inside the urgent window upgrades it
            return t
        tid = f"T{len(self.tickets) + 1:05d}"
        extra = {k: v for k, v in (context or {}).items() if k in ALLOWED_CONTEXT}
        t = {"id": tid, "case_id": case.id, "reason": reason, "priority": priority, "created_at": now,
             "status": "open", "resolution": None, "context": {
                 **extra, "phone": mask_phone(case.phone), "appointment_start": case.appointment_start.isoformat(),
                 "attempts": len(case.attempts), "last_outcome": case.last_outcome}}
        self.tickets[tid] = t
        self._by_key[key] = tid
        return t

    def open_tickets(self):
        return sorted((t for t in self.tickets.values() if t["status"] == "open"),
                      key=lambda t: (PRIORITY_RANK[t["priority"]], t["created_at"]))

    def resolve(self, tid, resolution, now):
        """Returns True if this call closed the ticket, False if it was already closed."""
        t = self.tickets[tid]
        if t["status"] == "closed":
            return False
        t["status"], t["resolution"], t["closed_at"] = "closed", resolution, now
        return True
