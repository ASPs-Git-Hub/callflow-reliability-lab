"""Tests added after two independent reviews (ChatGPT and Claude Fable) of the first version."""
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from callflow.alerts import evaluate  # noqa: E402
from callflow.handoff import HandoffQueue  # noqa: E402
from callflow.matcher import match_reply  # noqa: E402
from callflow.model import UTC, Case, Policy, mask_phone, norm_phone  # noqa: E402
from callflow.orchestrator import Orchestrator  # noqa: E402

WED_15 = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)
PH = "+15550001111"


def ev(case, n, outcome, conf=1.0, eid=None):
    return {"event_id": eid or f"E-{case}-{n}-{outcome}", "case_id": case, "attempt_no": n, "outcome": outcome, "confidence": conf}


def setup(tz="America/New_York", appt=None, now=WED_15, phone=PH, **kw):
    calls = []
    o = Orchestrator(dialer=kw.pop("dialer", calls.append), **kw)
    c = Case("A1", phone, tz, appt or now + timedelta(days=3))
    o.add_case(c, now)
    return o, c, calls


class LateResults(unittest.TestCase):
    def timed_out(self, **kw):
        o, c, calls = setup(**kw)
        o.tick(WED_15)
        o.tick(WED_15 + timedelta(minutes=20))                      # timeout: attempt 1 dropped, retry scheduled
        self.assertEqual((c.state, c.attempts[0]["timed_out"]), ("waiting", True))
        return o, c, calls

    def test_a_real_answer_after_the_timeout_is_used_not_called_again(self):
        o, c, calls = self.timed_out()
        self.assertEqual(o.handle_result(ev("A1", 1, "confirmed", 0.97), WED_15 + timedelta(minutes=25))["status"], "applied_late")
        self.assertEqual((c.state, c.next_attempt_at), ("confirmed", None))
        o.tick(WED_15 + timedelta(hours=4))
        self.assertEqual(len(calls), 1, "nobody is dialled again after confirming")

    def test_late_refusal_still_blocks_the_number_and_reaches_a_person(self):
        o, c, calls = self.timed_out()
        o.handle_result(ev("A1", 1, "refused"), WED_15 + timedelta(minutes=25))
        self.assertIn("15550001111", o.dnc)
        o.tick(WED_15 + timedelta(hours=24))
        self.assertEqual(len(calls), 1)
        self.assertEqual(o.tickets.open_tickets()[0]["reason"], "refused")

    def test_refusal_for_an_old_attempt_still_blocks_the_number(self):
        o, c, calls = setup()
        o.tick(WED_15)
        o.handle_result(ev("A1", 1, "no_answer"), WED_15 + timedelta(minutes=2))
        o.tick(WED_15 + timedelta(hours=3, minutes=3))
        self.assertEqual(o.handle_result(ev("A1", 1, "refused", eid="old"), WED_15 + timedelta(hours=3, minutes=5))["status"], "stale")
        self.assertIn("15550001111", o.dnc)

    def test_late_retryable_result_changes_nothing(self):
        o, c, calls = self.timed_out()
        self.assertEqual(o.handle_result(ev("A1", 1, "no_answer"), WED_15 + timedelta(minutes=25))["status"], "late_noop")
        self.assertEqual(c.state, "waiting")

    def test_timeout_and_result_order_is_deterministic_without_a_tick(self):
        o, c, calls = setup()
        o.tick(WED_15)
        r = o.handle_result(ev("A1", 1, "confirmed", 0.97), WED_15 + timedelta(minutes=25))   # no tick ran in between
        self.assertEqual((r["status"], c.state), ("applied_late", "confirmed"))
        self.assertTrue(c.attempts[0].get("timed_out"))

    def test_exact_timeout_boundary(self):
        o, c, calls = setup()
        o.tick(WED_15)
        o.tick(WED_15 + timedelta(minutes=19, seconds=59))
        self.assertEqual(c.state, "in_call")
        o.tick(WED_15 + timedelta(minutes=20))
        self.assertEqual(c.state, "waiting")

    def test_late_low_confidence_goes_to_a_person(self):
        o, c, calls = self.timed_out()
        o.handle_result(ev("A1", 1, "confirmed", 0.5), WED_15 + timedelta(minutes=25))
        self.assertEqual(c.state, "needs_person")
        self.assertEqual(o.tickets.open_tickets()[0]["reason"], "low_confidence")


class Validation(unittest.TestCase):
    def test_boolean_and_nonpositive_attempt_numbers_rejected(self):
        o, c, calls = setup()
        o.tick(WED_15)
        for bad in (True, 0, -1, 1.0, "1"):
            self.assertEqual(o.handle_result({**ev("A1", 1, "confirmed"), "attempt_no": bad}, WED_15)["status"], "rejected", bad)
        self.assertEqual(c.state, "in_call")

    def test_non_string_ids_rejected(self):
        o, c, calls = setup()
        for bad in ({"event_id": ["x"]}, {"case_id": ["A1"]}, {"event_id": ""}, {"event_id": "x" * 200}):
            self.assertEqual(o.handle_result({**ev("A1", 1, "confirmed"), **bad}, WED_15)["status"], "rejected", bad)

    def test_result_before_its_case_exists_is_not_lost(self):
        o = Orchestrator()
        early = ev("B1", 1, "confirmed", eid="E-early")
        self.assertEqual(o.handle_result(early, WED_15)["status"], "rejected")
        c = Case("B1", PH, "America/New_York", WED_15 + timedelta(days=3))
        o.add_case(c, WED_15)
        o.tick(WED_15)
        self.assertEqual(o.handle_result(early, WED_15)["status"], "applied")

    def test_same_event_id_on_two_cases_is_not_a_duplicate(self):
        o = Orchestrator()
        for cid, ph in (("A1", "+15550001111"), ("A2", "+15550002222")):
            o.add_case(Case(cid, ph, "America/New_York", WED_15 + timedelta(days=3)), WED_15)
        o.tick(WED_15)
        self.assertEqual(o.handle_result(ev("A1", 1, "confirmed", eid="same"), WED_15)["status"], "applied")
        self.assertEqual(o.handle_result(ev("A2", 1, "confirmed", eid="same"), WED_15)["status"], "applied")

    def test_duplicate_case_id_rejected(self):
        o, c, calls = setup()
        with self.assertRaises(ValueError):
            o.add_case(Case("A1", PH, "America/New_York", WED_15 + timedelta(days=9)), WED_15)

    def test_naive_datetimes_rejected(self):
        with self.assertRaises(ValueError):
            Case("X", PH, "UTC", datetime(2026, 3, 4, 15, 0))
        o = Orchestrator()
        with self.assertRaises(ValueError):
            o.add_case(Case("X", PH, "UTC", WED_15), datetime(2026, 3, 4, 15, 0))

    def test_appointment_is_stored_in_utc_so_cutoff_arithmetic_is_elapsed_time(self):
        from zoneinfo import ZoneInfo
        c = Case("X", PH, "America/New_York", datetime(2026, 3, 8, 4, 0, tzinfo=ZoneInfo("America/New_York")))   # 04:00 EDT after the jump
        self.assertEqual(c.appointment_start, datetime(2026, 3, 8, 8, 0, tzinfo=UTC))
        self.assertEqual(c.appointment_start - timedelta(hours=2), datetime(2026, 3, 8, 6, 0, tzinfo=UTC))

    def test_policy_window_validation(self):
        for bad in (dict(window_start_hour=2, window_end_hour=3), dict(window_start_hour=9, window_end_hour=9),
                    dict(window_start_hour=9, window_end_hour=23), dict(max_attempts=0)):
            with self.assertRaises(ValueError):
                Orchestrator(policy=Policy(**bad))

    def test_unknown_ticket_rejected_not_raised(self):
        o, c, calls = setup()
        self.assertEqual(o.resolve_ticket("T99999", "confirmed", WED_15)["status"], "rejected")


class Dispatch(unittest.TestCase):
    def test_a_failing_dialer_does_not_abort_the_batch_or_leave_a_phantom_call(self):
        calls = []

        def dialer(req):
            if req["case_id"] == "BAD":
                raise RuntimeError("provider down")
            calls.append(req)
        o = Orchestrator(dialer=dialer)
        for cid, ph in (("BAD", "+15550000001"), ("OK", "+15550000002")):
            o.add_case(Case(cid, ph, "America/New_York", WED_15 + timedelta(days=3)), WED_15)
        o.tick(WED_15)
        self.assertEqual([x["case_id"] for x in calls], ["OK"])
        self.assertEqual(o.cases["BAD"].state, "waiting")                 # dropped, retry scheduled
        self.assertEqual(o.cases["OK"].state, "in_call")
        self.assertTrue(any(kind == "dialer_error" for _, kind, _ in o.log))

    def test_cutoff_boundary_is_inclusive_then_blocks(self):
        appt = WED_15 + timedelta(hours=2)
        o, c, calls = setup(appt=appt)
        o.tick(WED_15)
        self.assertEqual(len(calls), 1)                                   # exactly two hours before: still allowed
        o2, c2, calls2 = setup(appt=appt - timedelta(seconds=1))
        o2.tick(WED_15)
        self.assertEqual(calls2, [])

    def test_urgent_window_boundary(self):
        o, c, calls = setup(appt=WED_15 + timedelta(hours=24))
        o._to_person(c, "unclear", WED_15)
        self.assertEqual(o.tickets.open_tickets()[0]["priority"], "urgent")
        o2, c2, _ = setup(appt=WED_15 + timedelta(hours=24, seconds=1))
        o2._to_person(c2, "unclear", WED_15)
        self.assertEqual(o2.tickets.open_tickets()[0]["priority"], "normal")

    def test_confidence_exactly_at_the_threshold_is_accepted(self):
        o, c, calls = setup()
        o.tick(WED_15)
        o.handle_result(ev("A1", 1, "confirmed", 0.8), WED_15)
        self.assertEqual(c.state, "confirmed")

    def test_provider_halt_stops_dispatch_until_resumed(self):
        o, c, calls = setup()
        o.halt("budget_reached", WED_15)
        o.tick(WED_15)
        self.assertEqual(calls, [])
        o.resume()
        o.tick(WED_15)
        self.assertEqual(len(calls), 1)

    def test_two_threads_ticking_place_exactly_one_call(self):
        o, c, calls = setup()
        errors = []

        def run():
            try:
                o.tick(WED_15)
            except Exception as e:        # pragma: no cover
                errors.append(e)
        ts = [threading.Thread(target=run) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual((errors, len(calls), len(c.attempts)), ([], 1, 1))


class PhonesAndTickets(unittest.TestCase):
    def test_numbers_from_different_countries_do_not_collide(self):
        self.assertNotEqual(norm_phone("+442079460958"), norm_phone("+12079460958"))
        self.assertEqual(norm_phone("(555) 000-1111"), norm_phone("+1 555 000 1111"))
        self.assertEqual(mask_phone("+442079460958"), "***0958")

    def test_dnc_for_one_country_does_not_block_another(self):
        o = Orchestrator(dnc=["+442079460958"])
        c = Case("X", "+12079460958", "America/New_York", WED_15 + timedelta(days=3))
        calls = []
        o.dialer = calls.append
        o.add_case(c, WED_15)
        o.tick(WED_15)
        self.assertEqual(len(calls), 1)

    def test_context_cannot_unmask_the_phone_or_inject_fields(self):
        q, c = HandoffQueue(), Case("X", PH, "UTC", WED_15)
        t = q.create(c, "a", "normal", WED_15, context={"phone": PH, "note": "call back after lunch", "secret": "x"})
        self.assertEqual(t["context"]["phone"], "***1111")
        self.assertEqual(t["context"]["note"], "call back after lunch")
        self.assertNotIn("secret", t["context"])

    def test_retrigger_in_the_urgent_window_upgrades_priority(self):
        q, c = HandoffQueue(), Case("X", PH, "UTC", WED_15)
        q.create(c, "a", "normal", WED_15)
        self.assertEqual(q.create(c, "a", "urgent", WED_15 + timedelta(hours=1))["priority"], "urgent")
        self.assertEqual(q.create(c, "a", "normal", WED_15 + timedelta(hours=2))["priority"], "urgent")

    def test_open_tickets_are_ordered_by_creation_time_within_a_priority(self):
        q = HandoffQueue()
        q.create(Case("L", PH, "UTC", WED_15), "x", "normal", WED_15 + timedelta(hours=2))
        q.create(Case("E", PH, "UTC", WED_15), "x", "normal", WED_15)
        self.assertEqual([t["case_id"] for t in q.open_tickets()], ["E", "L"])

    def test_ticket_context_carries_attempts_and_last_outcome(self):
        o, c, calls = setup()
        o.tick(WED_15)
        o.handle_result(ev("A1", 1, "unclear", 0.4), WED_15)
        ctx = o.tickets.open_tickets()[0]["context"]
        self.assertEqual((ctx["attempts"], ctx["last_outcome"]), (1, "unclear"))


class MatcherAndAlerts(unittest.TestCase):
    def test_two_appointments_with_the_same_phone_and_date_are_ambiguous(self):
        appts = [{"id": "A-1", "phone": "+1 555 000 1111", "name": "A B", "date": "2026-03-05"},
                 {"id": "A-2", "phone": "5550001111", "name": "C D", "date": "2026-03-05"}]
        s, hits, how = match_reply({"phone": "+15550001111", "date": "2026-03-05"}, appts)
        self.assertEqual((s, how, len(hits)), ("ambiguous", "phone+date", 2))

    def test_reference_is_case_insensitive_and_unknown_reference_falls_through(self):
        appts = [{"id": "A-1", "phone": "+15550001111", "name": "A B", "date": "2026-03-05"}]
        self.assertEqual(match_reply({"ref": "a-1"}, appts)[0], "matched")
        self.assertEqual(match_reply({"ref": "zzz", "phone": "5550001111", "date": "2026-03-05"}, appts)[2], "phone+date")

    def test_spike_alert_boundary(self):
        o, c, calls = setup()
        o.recent = [(WED_15, "unclear", 0.4)] * 6 + [(WED_15, "confirmed", 1.0)] * 14      # 6 of 20 = 30%: alert
        self.assertTrue(any(code == "low_confidence_spike" for code, _ in evaluate(o, WED_15)))
        o.recent = [(WED_15, "unclear", 0.4)] * 5 + [(WED_15, "confirmed", 1.0)] * 15      # 25%: quiet
        self.assertFalse(any(code == "low_confidence_spike" for code, _ in evaluate(o, WED_15)))

    def test_provider_halt_alert(self):
        o, c, calls = setup()
        o.halt("credit_exhausted", WED_15)
        self.assertIn(("provider_halt", "credit_exhausted"), evaluate(o, WED_15))


if __name__ == "__main__":
    unittest.main()
