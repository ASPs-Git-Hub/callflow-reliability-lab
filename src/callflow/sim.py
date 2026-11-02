"""Seeded simulator and an invariant audit that works from the SIMULATOR'S OWN observations:
every call is logged inside the injected dialer, every delivered result is logged with the status the orchestrator
returned, and local-time checks use zoneinfo directly (no import of the production scheduling code).
Caller behaviour is ASSUMED (PROFILE): the output shows how the orchestrators handle those assumptions, not how real
callers behave. The audit still reads final case states from the orchestrator, which is the only place they live."""
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .model import UTC, Case, Policy, norm_phone

TZS = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Asia/Kolkata", "Australia/Sydney"]
PROFILE = {  # assumed, illustrative
    "answer_day": 0.45, "answer_evening": 0.20, "answer_night": 0.02,
    "given_answer": {"confirmed": 0.78, "reschedule": 0.08, "declined": 0.05, "refused": 0.02, "unclear": 0.05, "dropped": 0.02},
    "clear_share": 0.85,          # share of confirmations and declines stated clearly (confidence 0.9 to 1.0); the rest 0.5 to 0.79
    "voicemail_share": 0.4, "wrong_number_rate": 0.03, "dnc_at_start_rate": 0.03,
    "p_lost": 0.03, "p_dup": 0.05, "p_late": 0.04, "late_minutes": 50,
}
START = datetime(2026, 3, 4, 14, 0, tzinfo=UTC)   # Wednesday; the 6 day run spans the US daylight-saving change on 2026-03-08


def _pick(rng, dist):
    r, acc = rng.random(), 0.0
    for k, p in dist.items():
        acc += p
        if r < acc:
            return k
    return "confirmed"


def _local_ok(at, tz, policy):
    """Independent re-implementation of 'inside the customer's local call window'."""
    l = at.astimezone(ZoneInfo(tz))
    return l.weekday() in policy.allowed_weekdays and policy.window_start_hour <= l.hour < policy.window_end_hour


class Observed:
    """What the simulator saw, independent of the orchestrator's own bookkeeping."""
    def __init__(self):
        self.dispatched = []      # (case_id, attempt_no, at, phone), logged inside the dialer
        self.delivered = []       # (at, event, status, state_after, latest_attempt_before)
        self.latest_attempt = {}  # case_id -> latest dispatched attempt number


def simulate(make_orch, n=150, seed=1, days=6, step_min=15, policy=None):
    policy = policy or Policy()
    rng = random.Random(seed)
    customers = {}
    for i in range(n):
        tz = rng.choice(TZS)
        customers[f"C{i:04d}"] = {
            "phone": f"+1555{1000000 + i * 7 + rng.randint(0, 6):07d}", "tz": tz, "wrong": rng.random() < PROFILE["wrong_number_rate"],
            "dnc0": rng.random() < PROFILE["dnc_at_start_rate"], "appt": START + timedelta(hours=rng.uniform(6, 5 * 24)),
        }
    obs, pending, issued = Observed(), [], {}

    def dialer(req):
        cid, now = req["case_id"], req["at"]
        obs.dispatched.append((cid, req["attempt_no"], now, req["phone"]))
        obs.latest_attempt[cid] = req["attempt_no"]
        c = customers[cid]
        h = now.astimezone(ZoneInfo(c["tz"])).hour
        pa = PROFILE["answer_day"] if 9 <= h < 18 else PROFILE["answer_evening"] if 18 <= h < 21 else PROFILE["answer_night"]
        if rng.random() < pa:
            out = "wrong_number" if c["wrong"] else _pick(rng, PROFILE["given_answer"])
            if out in ("confirmed", "declined"):
                conf = rng.uniform(0.9, 1.0) if rng.random() < PROFILE["clear_share"] else rng.uniform(0.5, 0.79)
            else:
                conf = rng.uniform(0.3, 0.7) if out == "unclear" else 1.0
        else:
            out, conf = ("voicemail" if rng.random() < PROFILE["voicemail_share"] else "no_answer"), 1.0
        issued[(cid, req["attempt_no"])] = (out, conf)
        if rng.random() < PROFILE["p_lost"]:
            return                                              # the result never arrives
        delay = timedelta(minutes=PROFILE["late_minutes"] if rng.random() < PROFILE["p_late"] else rng.uniform(1, 10))
        ev = {"event_id": f"E-{cid}-{req['attempt_no']}", "case_id": cid, "attempt_no": req["attempt_no"],
              "outcome": out, "confidence": round(conf, 3)}
        pending.append((now + delay, ev))
        if rng.random() < PROFILE["p_dup"]:
            pending.append((now + delay + timedelta(minutes=rng.uniform(0, 3)), dict(ev)))   # duplicate delivery

    orch = make_orch(policy=policy, dialer=dialer, dnc=[c["phone"] for c in customers.values() if c["dnc0"]])
    for cid, c in customers.items():
        orch.add_case(Case(cid, c["phone"], c["tz"], c["appt"]), START)
    t, end = START, START + timedelta(days=days)
    while t < end:
        due = sorted((p for p in pending if p[0] <= t), key=lambda p: p[0])
        pending[:] = [p for p in pending if p[0] > t]
        for _, ev in due:
            latest = obs.latest_attempt.get(ev["case_id"])           # the attempt outstanding when this result arrived
            status = orch.handle_result(ev, t)["status"]
            obs.delivered.append((t, ev, status, orch.cases[ev["case_id"]].state, latest))
        orch.tick(t)
        for tk in orch.tickets.open_tickets():                  # people clear tickets after about two hours
            if t - tk["created_at"] >= timedelta(hours=2) and rng.random() < 0.5:
                orch.resolve_ticket(tk["id"], rng.choice(["confirmed", "declined", "cancelled"]), t)
        t += timedelta(minutes=step_min)
    return orch, customers, obs, end


SAFETY = ("calls_outside_local_window", "calls_to_do_not_call", "calls_after_refusal", "over_max_attempts",
          "attempts_closer_than_min_gap", "calls_after_cutoff", "duplicate_tickets", "events_applied_twice",
          "stale_results_applied", "low_confidence_accepted", "called_again_after_a_clear_answer")
LIVENESS = ("cases_never_dispatched_or_escalated", "cases_waiting_past_appointment")


def audit(orch, customers, obs, end, policy):
    """Each key is a count of violations (0 is the goal), derived from the simulator's observations."""
    v = dict.fromkeys(SAFETY + LIVENESS, 0)
    dnc0 = {norm_phone(c["phone"]) for c in customers.values() if c["dnc0"]}
    by_case = {}
    for cid, n, at, phone in obs.dispatched:
        c = customers[cid]
        by_case.setdefault(cid, []).append((n, at))
        v["calls_outside_local_window"] += not _local_ok(at, c["tz"], policy)
        v["calls_to_do_not_call"] += norm_phone(phone) in dnc0
        v["calls_after_cutoff"] += at > c["appt"] - policy.stop_before_appt
    applied_ids, answered_clearly, refused_at = {}, {}, {}
    for at, ev, status, state_after, latest in obs.delivered:
        cid = ev["case_id"]
        if status in ("applied", "applied_late"):
            applied_ids[(cid, ev["event_id"])] = applied_ids.get((cid, ev["event_id"]), 0) + 1
            if status == "applied" and ev["attempt_no"] != latest:
                v["stale_results_applied"] += 1
            if ev["outcome"] in ("confirmed", "declined") and state_after in ("confirmed", "declined"):
                v["low_confidence_accepted"] += ev["confidence"] < policy.confidence_threshold
                answered_clearly.setdefault(cid, at)
        if ev["outcome"] == "refused" and status in ("applied", "applied_late", "stale"):
            refused_at.setdefault(cid, at)
    v["events_applied_twice"] = sum(1 for k in applied_ids.values() if k > 1)
    for cid, calls in by_case.items():
        v["over_max_attempts"] += len(calls) > policy.max_attempts
        ats = [a for _, a in sorted(calls)]
        v["attempts_closer_than_min_gap"] += sum(1 for x, y in zip(ats, ats[1:]) if y - x < policy.min_gap)
        v["calls_after_refusal"] += sum(1 for _, a in calls if cid in refused_at and a > refused_at[cid])
        v["called_again_after_a_clear_answer"] += sum(1 for _, a in calls if cid in answered_clearly and a > answered_clearly[cid])
    seen = {}
    for tk in orch.tickets.tickets.values():
        seen[(tk["case_id"], tk["reason"])] = seen.get((tk["case_id"], tk["reason"]), 0) + 1
    v["duplicate_tickets"] = sum(1 for k in seen.values() if k > 1)
    ticketed = {tk["case_id"] for tk in orch.tickets.tickets.values()}
    for cid, case in orch.cases.items():
        dispatched = cid in by_case
        v["cases_never_dispatched_or_escalated"] += (not dispatched and cid not in ticketed and case.state == "waiting")
        v["cases_waiting_past_appointment"] += (case.state in ("waiting", "in_call") and customers[cid]["appt"] < end)
    return v


def funnel(orch, customers, obs):
    st = {}
    for c in orch.cases.values():
        st[c.state] = st.get(c.state, 0) + 1
    n = len(orch.cases)
    return {"cases": n, "system_confirmed": st.get("confirmed", 0), "system_declined": st.get("declined", 0),
            "reached_a_person": len({t["case_id"] for t in orch.tickets.tickets.values()}),
            "resolved_by_person": st.get("resolved", 0) + st.get("cancelled", 0),
            "still_open": st.get("waiting", 0) + st.get("in_call", 0) + st.get("needs_person", 0),
            "calls": len(obs.dispatched)}
