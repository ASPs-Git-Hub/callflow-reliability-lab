"""Alert rules evaluated over orchestrator state. Returns a list of (code, detail); nothing runs silently."""
from datetime import timedelta


def evaluate(orch, now, stuck_factor=2, overdue=timedelta(hours=1), ticket_age=timedelta(hours=4),
             urgent_age=timedelta(hours=1), spike_window=20, spike_share=0.3):
    out = []
    p = orch.policy
    if getattr(orch, "halted", None):
        out.append(("provider_halt", orch.halted))
    for c in orch.cases.values():
        if c.state == "in_call" and c.in_call_since is not None and now - c.in_call_since >= p.call_timeout * stuck_factor:
            out.append(("stuck_in_call", c.id))
        if c.state == "waiting" and c.next_attempt_at is not None and now - c.next_attempt_at >= overdue:
            out.append(("overdue_attempt", c.id))
    for t in orch.tickets.open_tickets():
        limit = urgent_age if t["priority"] == "urgent" else ticket_age
        if now - t["created_at"] >= limit:
            out.append(("ticket_aging", t["id"]))
    recent = orch.recent[-spike_window:]
    if len(recent) == spike_window:
        bad = sum(1 for _, o, conf in recent if o == "unclear" or conf < p.confidence_threshold)
        if bad / spike_window >= spike_share:
            out.append(("low_confidence_spike", f"{bad}/{spike_window}"))
    return out
