import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from callflow.alerts import evaluate  # noqa: E402
from callflow.handoff import HandoffQueue  # noqa: E402
from callflow.matcher import match_reply  # noqa: E402
from callflow.model import ALLOWED, UTC, Case, IllegalTransition, Policy, mask_phone, move  # noqa: E402
from callflow.naive import NaiveOrchestrator  # noqa: E402
from callflow.orchestrator import Orchestrator  # noqa: E402
from callflow.schedule import in_window, next_allowed, schedule_next  # noqa: E402
from callflow.sim import audit, simulate  # noqa: E402

IMPLS = {"naive": NaiveOrchestrator, "fixed": Orchestrator}
WED_15 = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)         # Wednesday: 10:00 New York, 07:00 Los Angeles
PH = "+15550001111"


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def ev(case, n, outcome, conf=1.0, eid=None):
    return {"event_id": eid or f"E-{case}-{n}-{outcome}", "case_id": case, "attempt_no": n, "outcome": outcome, "confidence": conf}


def setup(cls, tz="America/New_York", appt=None, now=WED_15, phone=PH, **kw):
    calls = []
    o = cls(dialer=calls.append, **kw)
    c = Case("A1", phone, tz, appt or now + timedelta(days=3))
    o.add_case(c, now)
    return o, c, calls


# Each contract: the naive implementation must FAIL it with the intended defect (assertion or crash), the fixed one must pass.
class Contracts(unittest.TestCase):
    pass


def contract(name, fn):
    for impl, cls in IMPLS.items():
        def t(self, cls=cls, impl=impl):
            if impl == "fixed":
                return fn(self, cls)
            with self.assertRaises((AssertionError, KeyError, TypeError)):
                fn(self, cls)
        setattr(Contracts, f"test_{impl}_{name}", t)


def c_window(tc, cls):
    o, c, calls = setup(cls, tz="America/Los_Angeles", now=utc(2026, 3, 4, 10, 0))   # 02:00 local
    o.tick(utc(2026, 3, 4, 10, 0))
    tc.assertEqual(calls, [], "no call at 02:00 local time")


def c_dnc(tc, cls):
    o, c, calls = setup(cls)
    o.add_dnc(PH)
    o.tick(WED_15)
    tc.assertEqual(calls, [])


def c_dup(tc, cls):
    o, c, calls = setup(cls)
    o.tick(WED_15)
    e = ev("A1", 1, "reschedule")
    o.handle_result(e, WED_15)
    tc.assertEqual(o.handle_result(dict(e), WED_15)["status"], "duplicate")
    tc.assertEqual(o.applied[("A1", e["event_id"])], 1)
    tc.assertEqual(len(o.tickets.tickets), 1)


def c_stale(tc, cls):
    o, c, calls = setup(cls)
    o.tick(WED_15)
    o.handle_result(ev("A1", 1, "no_answer"), WED_15 + timedelta(minutes=2))
    o.tick(WED_15 + timedelta(hours=3, minutes=3))
    tc.assertEqual(len(calls), 2)
    o.handle_result(ev("A1", 1, "confirmed", eid="late"), WED_15 + timedelta(hours=3, minutes=4))   # late result for attempt 1
    tc.assertEqual(c.state, "in_call", "a late result for attempt 1 must not close attempt 2")


def c_lowconf(tc, cls):
    o, c, calls = setup(cls)
    o.tick(WED_15)
    o.handle_result(ev("A1", 1, "confirmed", conf=0.55), WED_15)
    tc.assertNotEqual(c.state, "confirmed")


def c_gap_and_max(tc, cls):
    o, c, calls = setup(cls)
    t = WED_15
    for _ in range(10 * 24 * 2):                      # ten days, every 30 minutes
        o.tick(t)
        for call in list(calls):
            if call["attempt_no"] == len(c.attempts) and c.state == "in_call":
                o.handle_result(ev("A1", call["attempt_no"], "no_answer"), t)
        t += timedelta(minutes=30)
    tc.assertLessEqual(len(c.attempts), 4)
    ats = [a["at"] for a in c.attempts]
    tc.assertTrue(all(y - x >= timedelta(hours=3) for x, y in zip(ats, ats[1:])))


def c_timeout(tc, cls):
    o, c, calls = setup(cls)
    o.tick(WED_15)
    o.tick(WED_15 + timedelta(minutes=21))
    tc.assertNotEqual(c.state, "in_call", "a call with no result must time out")


def c_cutoff(tc, cls):
    o, c, calls = setup(cls, appt=WED_15 + timedelta(minutes=90))
    o.tick(WED_15)
    tc.assertEqual(calls, [])
    tc.assertEqual(c.state, "needs_person")
    tc.assertEqual(o.tickets.open_tickets()[0]["priority"], "urgent")


def c_lowconf_declined(tc, cls):
    o, c, calls = setup(cls)
    o.tick(WED_15)
    o.handle_result(ev("A1", 1, "declined", conf=0.5), WED_15)
    tc.assertNotEqual(c.state, "declined")


def c_late_tick(tc, cls):
    o, c, calls = setup(cls, appt=WED_15 + timedelta(hours=5))
    o.tick(WED_15 + timedelta(hours=4))            # the scheduler was down; cutoff (3h before start) has passed
    tc.assertEqual(calls, [], "no call after the cutoff, even for a case scheduled earlier")
    tc.assertEqual(c.state, "needs_person")


def c_refused(tc, cls):
    calls = []
    o = cls(dialer=calls.append)
    a, b = Case("A1", PH, "America/New_York", WED_15 + timedelta(days=3)), Case("B1", PH, "America/New_York", WED_15 + timedelta(days=3))
    o.add_case(a, WED_15)
    o.tick(WED_15)
    o.handle_result(ev("A1", 1, "refused"), WED_15)
    o.add_case(b, WED_15 + timedelta(hours=1))
    o.tick(WED_15 + timedelta(hours=1))
    tc.assertEqual([x["case_id"] for x in calls], ["A1"], "after a refusal the number is never called again")


def c_malformed(tc, cls):
    o, c, calls = setup(cls)
    tc.assertEqual(o.handle_result({"case_id": "A1"}, WED_15)["status"], "rejected")
    tc.assertEqual(o.handle_result({"event_id": "x", "case_id": "NOPE", "attempt_no": 1, "outcome": "confirmed", "confidence": 1}, WED_15)["status"], "rejected")


def c_resolve_idempotent(tc, cls):
    o, c, calls = setup(cls)
    o.tick(WED_15)
    o.handle_result(ev("A1", 1, "reschedule"), WED_15)
    tid = o.tickets.open_tickets()[0]["id"]
    o.resolve_ticket(tid, "confirmed", WED_15)
    tc.assertEqual(o.resolve_ticket(tid, "confirmed", WED_15)["status"], "already_closed")


def c_concurrency(tc, cls):
    calls = []
    o = cls(policy=Policy(max_concurrent=20), dialer=calls.append)
    for i in range(30):
        o.add_case(Case(f"K{i}", f"+1555000{i:04d}", "America/New_York", WED_15 + timedelta(days=3)), WED_15)
    o.tick(WED_15)
    tc.assertLessEqual(len(calls), 20)


def c_nearest_first(tc, cls):
    calls = []
    o = cls(policy=Policy(max_concurrent=1), dialer=calls.append)
    o.add_case(Case("LATE", "+15550000001", "America/New_York", WED_15 + timedelta(days=4)), WED_15)
    o.add_case(Case("SOON", "+15550000002", "America/New_York", WED_15 + timedelta(days=1)), WED_15)
    o.tick(WED_15)
    tc.assertEqual([x["case_id"] for x in calls], ["SOON"], "exactly one call with a concurrency limit of one, nearest appointment first")


for n, f in [("never_calls_outside_local_window", c_window), ("respects_do_not_call", c_dnc), ("duplicate_event_applied_once", c_dup),
             ("stale_result_ignored", c_stale), ("low_confidence_goes_to_a_person", c_lowconf), ("gap_and_attempt_limit", c_gap_and_max),
             ("stuck_call_times_out", c_timeout), ("appointment_cutoff_escalates_urgent", c_cutoff), ("late_tick_after_cutoff_does_not_call", c_late_tick), ("low_confidence_decline_goes_to_a_person", c_lowconf_declined), ("refusal_blocks_the_number", c_refused),
             ("malformed_events_rejected", c_malformed), ("person_resolution_idempotent", c_resolve_idempotent),
             ("concurrency_limit", c_concurrency), ("nearest_appointment_first", c_nearest_first)]:
    contract(n, f)


class Schedule(unittest.TestCase):
    def test_in_window_respects_local_hours_and_weekdays(self):
        p = Policy()
        self.assertTrue(in_window(utc(2026, 3, 4, 15), "America/New_York", p))        # Wed 10:00
        self.assertFalse(in_window(utc(2026, 3, 4, 23), "America/New_York", p))       # Wed 18:00 closed (end exclusive)
        self.assertFalse(in_window(utc(2026, 3, 8, 16), "America/New_York", p))       # Sunday

    def test_dst_change_moves_the_utc_time_of_the_window(self):
        # Saturday 2026-03-07 18:00 New York (EST) is after the window; next is Monday 09:00 EDT = 13:00 UTC (DST began 03-08)
        t = next_allowed(utc(2026, 3, 7, 23), "America/New_York", Policy())
        self.assertEqual(t, utc(2026, 3, 9, 13))
        self.assertEqual(next_allowed(utc(2026, 3, 2, 12), "America/New_York", Policy()), utc(2026, 3, 2, 14))   # EST: 09:00 = 14:00 UTC

    def test_other_time_zones(self):
        self.assertEqual(next_allowed(utc(2026, 3, 4, 0), "Asia/Kolkata", Policy()), utc(2026, 3, 4, 3, 30))     # 09:00 IST
        self.assertEqual(next_allowed(utc(2026, 3, 4, 0), "Australia/Sydney", Policy()), utc(2026, 3, 4, 0))     # 11:00 AEDT already inside

    def test_no_window_inside_horizon_returns_none(self):
        self.assertIsNone(next_allowed(utc(2026, 3, 4, 15), "America/New_York", Policy(allowed_weekdays=frozenset())))

    def test_schedule_next_respects_cutoff_and_gap(self):
        c = Case("X", PH, "America/New_York", WED_15 + timedelta(hours=10))
        self.assertEqual(schedule_next(c, WED_15, Policy(), first=True), WED_15)
        self.assertIsNone(schedule_next(c, WED_15 + timedelta(hours=6), Policy()))                              # gap pushes past cutoff


class Model(unittest.TestCase):
    def test_transition_table_is_enforced(self):
        c = Case("X", PH, "UTC", WED_15)
        move(c, "in_call", WED_15)
        move(c, "confirmed", WED_15)
        with self.assertRaises(IllegalTransition):
            move(c, "waiting", WED_15)
        self.assertEqual(ALLOWED["confirmed"], set())

    def test_every_state_in_the_table_is_declared(self):
        self.assertEqual(set(ALLOWED), {"waiting", "in_call", "confirmed", "declined", "needs_person", "resolved", "cancelled"})

    def test_phone_is_masked(self):
        self.assertEqual(mask_phone("+1 (555) 000-1234"), "***1234")


class Handoff(unittest.TestCase):
    def test_one_ticket_per_case_and_reason(self):
        q, c = HandoffQueue(), Case("X", PH, "UTC", WED_15)
        a, b = q.create(c, "unclear", "normal", WED_15), q.create(c, "unclear", "urgent", WED_15 + timedelta(hours=1))
        self.assertIs(a, b)
        self.assertEqual(len(q.tickets), 1)
        self.assertNotEqual(q.create(c, "wrong_number", "normal", WED_15)["id"], a["id"])

    def test_urgent_first_and_context_is_masked(self):
        q, c = HandoffQueue(), Case("X", PH, "UTC", WED_15)
        q.create(c, "a", "normal", WED_15)
        q.create(Case("Y", PH, "UTC", WED_15), "b", "urgent", WED_15 + timedelta(minutes=5))
        self.assertEqual([t["reason"] for t in q.open_tickets()], ["b", "a"])
        self.assertEqual(q.open_tickets()[0]["context"]["phone"], "***1111")
        self.assertNotIn(PH[-7:], str(q.open_tickets()[0]["context"]))

    def test_resolve_twice(self):
        q, c = HandoffQueue(), Case("X", PH, "UTC", WED_15)
        t = q.create(c, "a", "normal", WED_15)
        self.assertTrue(q.resolve(t["id"], "confirmed", WED_15))
        self.assertFalse(q.resolve(t["id"], "declined", WED_15))
        self.assertEqual(t["resolution"], "confirmed")


class Orchestration(unittest.TestCase):
    def test_refusal_adds_to_dnc_and_opens_a_ticket(self):
        o, c, calls = setup(Orchestrator)
        o.tick(WED_15)
        o.handle_result(ev("A1", 1, "refused"), WED_15)
        self.assertIn("15550001111", o.dnc)
        self.assertEqual(o.tickets.open_tickets()[0]["reason"], "refused")

    def test_retryable_outcome_schedules_next_in_window(self):
        o, c, calls = setup(Orchestrator, now=utc(2026, 3, 4, 21, 0))   # 16:00 New York
        o.tick(utc(2026, 3, 4, 21, 0))
        o.handle_result(ev("A1", 1, "no_answer"), utc(2026, 3, 4, 21, 1))
        self.assertEqual(c.state, "waiting")
        self.assertTrue(in_window(c.next_attempt_at, c.tz, Policy()))
        self.assertGreaterEqual(c.next_attempt_at - utc(2026, 3, 4, 21, 1), timedelta(hours=3))

    def test_invalid_event_values_rejected(self):
        o, c, calls = setup(Orchestrator)
        o.tick(WED_15)
        for bad in ({"outcome": "maybe"}, {"attempt_no": "1"}, {"confidence": 1.5}, {"confidence": True}):
            self.assertEqual(o.handle_result({**ev("A1", 1, "confirmed"), **bad}, WED_15)["status"], "rejected", bad)
        self.assertEqual(c.state, "in_call")

    def test_human_resolution_updates_the_case(self):
        o, c, calls = setup(Orchestrator)
        o.tick(WED_15)
        o.handle_result(ev("A1", 1, "wrong_number"), WED_15)
        tid = o.tickets.open_tickets()[0]["id"]
        self.assertEqual(o.resolve_ticket(tid, "confirmed", WED_15)["status"], "resolved")
        self.assertEqual((c.state, c.resolution), ("resolved", "confirmed"))
        self.assertEqual(o.resolve_ticket(tid, "bogus", WED_15)["status"], "rejected")

    def test_window_closed_since_scheduling_reschedules_instead_of_calling(self):
        o, c, calls = setup(Orchestrator)
        c.next_attempt_at = WED_15
        o.tick(utc(2026, 3, 4, 23, 30))                                  # 18:30 New York, window closed
        self.assertEqual(calls, [])
        self.assertTrue(in_window(c.next_attempt_at, c.tz, Policy()))

    def test_state_history_only_contains_legal_moves(self):
        o, objs = simulate(Orchestrator, n=60, seed=3)[:2]
        for c in o.cases.values():
            for _, a, b, _ in c.history:
                self.assertIn(b, ALLOWED[a])


class Matcher(unittest.TestCase):
    APPTS = [{"id": "A-100", "phone": "+1 555 000 1111", "name": "Maria Lopez", "date": "2026-03-05"},
             {"id": "A-101", "phone": "+1 555 000 2222", "name": "Maria Lopes", "date": "2026-03-05"},
             {"id": "A-102", "phone": "+1 555 000 1111", "name": "Maria Lopez", "date": "2026-03-06"}]

    def test_reference_first(self):
        s, a, how = match_reply({"ref": "a-101"}, self.APPTS)
        self.assertEqual((s, a["id"], how), ("matched", "A-101", "reference"))

    def test_phone_and_date(self):
        s, a, how = match_reply({"phone": "5550001111", "date": "2026-03-06"}, self.APPTS)
        self.assertEqual((s, a["id"], how), ("matched", "A-102", "phone+date"))

    def test_ambiguous_is_not_auto_matched(self):
        s, hits, how = match_reply({"phone": "5550001111"}, self.APPTS)       # no date: falls through, never guesses
        self.assertEqual(s, "unmatched")
        s, hits, how = match_reply({"name": "Maria Lopez", "date": "2026-03-05"}, self.APPTS)
        self.assertEqual((s, how), ("ambiguous", "name+date"))
        self.assertEqual(len(hits), 2)

    def test_name_alone_never_matches(self):
        self.assertEqual(match_reply({"name": "Maria Lopez"}, self.APPTS)[0], "unmatched")

    def test_weak_name_below_threshold_is_unmatched(self):
        self.assertEqual(match_reply({"name": "Mario Lopes Jr", "date": "2026-03-05"}, self.APPTS)[0], "unmatched")


class Alerts(unittest.TestCase):
    def test_stuck_overdue_aging_and_spike(self):
        o, c, calls = setup(Orchestrator)
        o.tick(WED_15)
        late = WED_15 + timedelta(minutes=45)
        self.assertIn(("stuck_in_call", "A1"), evaluate(o, late))
        o2, c2, _ = setup(Orchestrator)
        self.assertIn(("overdue_attempt", "A1"), evaluate(o2, WED_15 + timedelta(hours=2)))
        o3, c3, _ = setup(Orchestrator)
        o3.tick(WED_15)
        o3.handle_result(ev("A1", 1, "unclear", conf=0.4), WED_15)
        self.assertTrue(any(code == "ticket_aging" for code, _ in evaluate(o3, WED_15 + timedelta(hours=5))))
        o3.recent = [(WED_15, "unclear", 0.4)] * 20
        self.assertTrue(any(code == "low_confidence_spike" for code, _ in evaluate(o3, WED_15)))

    def test_urgent_tickets_alert_sooner_than_normal_ones(self):
        urgent, c, _ = setup(Orchestrator, appt=WED_15 + timedelta(hours=10))      # within 24h: urgent
        urgent.handle_result(ev("A1", 1, "no_answer"), WED_15)                     # ignored: not in call
        urgent._to_person(c, "unclear", WED_15)
        normal, c2, _ = setup(Orchestrator, appt=WED_15 + timedelta(days=3))
        normal._to_person(c2, "unclear", WED_15)
        later = WED_15 + timedelta(minutes=61)
        self.assertTrue(any(code == "ticket_aging" for code, _ in evaluate(urgent, later)))
        self.assertFalse(any(code == "ticket_aging" for code, _ in evaluate(normal, later)))

    def test_quiet_when_healthy(self):
        o, c, calls = setup(Orchestrator)
        self.assertEqual(evaluate(o, WED_15), [])


class SimulationInvariants(unittest.TestCase):
    """Seeded simulation under ASSUMED caller behaviour. The audit works from what the simulator itself observed."""
    SEEDS = (1, 2, 3, 4, 5)

    @classmethod
    def setUpClass(cls):
        pol = Policy()
        cls.totals, cls.runs = {}, {}
        for name, impl in IMPLS.items():
            agg = {}
            cls.runs[name] = []
            for s in cls.SEEDS:
                o, cust, obs, end = simulate(impl, n=120, seed=s, policy=pol)
                cls.runs[name].append((o, obs))
                for k, v in audit(o, cust, obs, end, pol).items():
                    agg[k] = agg.get(k, 0) + v
            cls.totals[name] = agg

    def test_fixed_has_zero_violations_on_every_seed(self):
        self.assertEqual({k: v for k, v in self.totals["fixed"].items() if v}, {})

    def test_audit_is_not_vacuous_for_the_fixed_run(self):
        for o, obs in self.runs["fixed"]:
            self.assertGreater(len(obs.dispatched), 50)
            self.assertGreater(len(obs.delivered), 50)
            self.assertGreater(sum(1 for c in o.cases.values() if c.state == "confirmed"), 5)
            self.assertEqual(len(obs.dispatched), len(o.calls))      # the simulator's own log agrees with the orchestrator's

    def test_the_audit_sees_late_results_applied_after_a_timeout(self):
        statuses = {st for o, obs in self.runs["fixed"] for _, _, st, _, _ in obs.delivered}
        self.assertIn("applied_late", statuses)
        self.assertIn("stale", statuses)
        self.assertIn("duplicate", statuses)

    def test_naive_violates_each_invariant_we_claim_to_fix(self):
        for k in ("calls_outside_local_window", "calls_to_do_not_call", "over_max_attempts", "attempts_closer_than_min_gap",
                  "events_applied_twice", "low_confidence_accepted"):
            self.assertGreater(self.totals["naive"][k], 0, k)

    def test_a_do_nothing_orchestrator_fails_the_liveness_checks(self):
        class NoOp:
            def __init__(self, policy=None, dialer=None, dnc=()):
                self.cases, self.tickets = {}, HandoffQueue()
            def add_case(self, case, now): self.cases[case.id] = case
            def tick(self, now): pass
            def handle_result(self, ev, now): return {"status": "stale"}
            def resolve_ticket(self, tid, resolution, now): return {"status": "rejected"}
        o, cust, obs, end = simulate(NoOp, n=40, seed=1, policy=Policy())
        a = audit(o, cust, obs, end, Policy())
        self.assertEqual({k: v for k, v in a.items() if v and k not in ("cases_never_dispatched_or_escalated", "cases_waiting_past_appointment")}, {})
        self.assertGreater(a["cases_never_dispatched_or_escalated"], 0)
        self.assertGreater(a["cases_waiting_past_appointment"], 0)


if __name__ == "__main__":
    unittest.main()
