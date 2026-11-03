import hashlib
import hmac
import json
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from callflow.model import UTC, Case  # noqa: E402
from callflow.orchestrator import Orchestrator  # noqa: E402
from callflow.retell_adapter import AdapterError, handle_webhook, to_result_event, verify_signature  # noqa: E402

KEY = "test-key-not-a-real-key"
NOW_MS = 1_800_000_000_000
T0 = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)


def sign(body, key=KEY, ts=NOW_MS):
    """Documented scheme: header v={ms},d={hex HMAC-SHA256(raw_body + ms, api_key)}."""
    return f"v={ts},d=" + hmac.new(key.encode(), (body + str(ts)).encode(), hashlib.sha256).hexdigest()


def payload(event="call_analyzed", reason="agent_hangup", meta=None, analysis=None, call_id="call_1"):
    call = {"call_id": call_id, "disconnection_reason": reason, "metadata": {"case_id": "A1", "attempt_no": 1} if meta is None else meta}
    if analysis is not None:
        call["call_analysis"] = {"custom_analysis_data": analysis}
    return {"event": event, "call": call}


class Signature(unittest.TestCase):
    BODY = '{"event":"call_ended","call":{"call_id":"x"}}'

    def test_valid(self):
        self.assertTrue(verify_signature(self.BODY, sign(self.BODY), KEY, NOW_MS))

    def test_tampered_body_wrong_key_and_missing_inputs(self):
        self.assertFalse(verify_signature(self.BODY + " ", sign(self.BODY), KEY, NOW_MS))
        self.assertFalse(verify_signature(self.BODY, sign(self.BODY), "other", NOW_MS))
        self.assertFalse(verify_signature(self.BODY, None, KEY, NOW_MS))
        self.assertFalse(verify_signature(self.BODY, sign(self.BODY), "", NOW_MS))
        self.assertFalse(verify_signature(b"bytes", sign(self.BODY), KEY, NOW_MS))

    def test_timestamp_is_part_of_the_signed_data(self):
        h = sign(self.BODY, ts=NOW_MS)
        forged = h.replace(f"v={NOW_MS}", f"v={NOW_MS + 1}")
        self.assertFalse(verify_signature(self.BODY, forged, KEY, NOW_MS))

    def test_five_minute_tolerance_both_directions(self):
        for delta, ok in ((299_000, True), (301_000, False), (-299_000, True), (-301_000, False)):
            self.assertEqual(verify_signature(self.BODY, sign(self.BODY, ts=NOW_MS), KEY, NOW_MS + delta), ok, delta)

    def test_malformed_headers(self):
        for bad in ("", "v=abc,d=00", f"v={NOW_MS}", f"d=00,v={NOW_MS}", f"v={NOW_MS},d=", f"v={NOW_MS},d=zz", " garbage "):
            self.assertFalse(verify_signature(self.BODY, bad, KEY, NOW_MS), bad)

    def test_uppercase_hex_accepted(self):
        h = sign(self.BODY)
        self.assertTrue(verify_signature(self.BODY, h.split("d=")[0] + "d=" + h.split("d=")[1].upper(), KEY, NOW_MS))


class Mapping(unittest.TestCase):
    def test_final_on_call_ended(self):
        for reason, outcome in (("dial_no_answer", "no_answer"), ("dial_busy", "no_answer"), ("voicemail_reached", "voicemail"),
                                ("ivr_reached", "no_answer"), ("dial_failed", "dropped"), ("invalid_destination", "wrong_number"),
                                ("user_requested_dnc", "refused"), ("user_requested_callback", "reschedule"), ("error_asr", "dropped"),
                                ("user_declined", "no_answer"), ("marked_as_spam", "unclear"), ("network_blocked", "unclear"),
                                ("telephony_provider_permission_denied", "unclear"), ("sip_routing_error", "unclear"),
                                ("registered_call_timeout", "dropped"), ("concurrency_limit_reached", "dropped")):
            ev = to_result_event(payload("call_ended", reason))
            self.assertEqual((ev["outcome"], ev["confidence"]), (outcome, 1.0), reason)
            self.assertEqual((ev["event_id"], ev["case_id"], ev["attempt_no"]), ("retell:call_1", "A1", 1))

    def test_call_ended_with_a_spoken_call_waits_for_analysis(self):
        for reason in ("agent_hangup", "call_transfer", "inactivity", "max_duration_reached"):
            self.assertIsNone(to_result_event(payload("call_ended", reason)), reason)

    def test_analysis_outcome_and_confidence(self):
        ev = to_result_event(payload(analysis={"outcome": "confirmed", "confidence": 0.93}))
        self.assertEqual((ev["outcome"], ev["confidence"]), ("confirmed", 0.93))

    def test_missing_or_invalid_analysis_goes_to_a_person(self):
        for analysis in (None, {}, {"outcome": "maybe", "confidence": 0.9}, {"outcome": "confirmed", "confidence": 1.4},
                         {"outcome": "confirmed", "confidence": True}, {"outcome": "confirmed", "confidence": "0.9"}):
            ev = to_result_event(payload(analysis=analysis))
            self.assertEqual((ev["outcome"], ev["confidence"]), ("unclear", 0.0), analysis)

    def test_telephony_facts_beat_analysis_on_either_event_order(self):
        for reason, outcome in (("voicemail_reached", "voicemail"), ("dial_no_answer", "no_answer"), ("user_requested_dnc", "refused"),
                                ("invalid_destination", "wrong_number")):
            for event in ("call_analyzed", "call_ended"):
                ev = to_result_event(payload(event, reason, analysis={"outcome": "confirmed", "confidence": 0.99}))
                self.assertEqual(ev["outcome"], outcome, (reason, event))

    def test_nested_values_of_the_wrong_type_never_raise(self):
        for call_patch in ({"call_analysis": []}, {"call_analysis": {"custom_analysis_data": []}}, {"call_analysis": "x"}):
            p = payload()
            p["call"].update(call_patch)
            self.assertEqual(to_result_event(p)["outcome"], "unclear")
        p = payload()
        p["call"]["metadata"] = ["case"]
        with self.assertRaises(AdapterError):
            to_result_event(p)
        for bad_id in (["x"], 5, "", "x" * 200):
            with self.assertRaises(AdapterError):
                to_result_event(payload(meta={"case_id": bad_id, "attempt_no": 1}))
        with self.assertRaises(AdapterError):
            to_result_event(payload(meta={"case_id": "A1", "attempt_no": 0}))

    def test_other_events_ignored(self):
        for event in ("call_started", "transcript_updated", "transfer_started", "chat_ended"):
            self.assertIsNone(to_result_event(payload(event)), event)

    def test_metadata_required(self):
        for meta in ({}, {"case_id": "A1"}, {"attempt_no": 1}, {"case_id": 5, "attempt_no": 1}, {"case_id": "A1", "attempt_no": "1"},
                     {"case_id": "A1", "attempt_no": True}):
            with self.assertRaises(AdapterError):
                to_result_event(payload(meta=meta))
        with self.assertRaises(AdapterError):
            to_result_event({"event": "call_ended"})
        with self.assertRaises(AdapterError):
            to_result_event(None)


class Integration(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.o = Orchestrator(dialer=self.calls.append)
        self.case = Case("A1", "+15550001111", "America/New_York", T0 + timedelta(days=3))
        self.o.add_case(self.case, T0)
        self.o.tick(T0)

    def post(self, body_dict, key=KEY):
        body = json.dumps(body_dict)
        return handle_webhook(body, sign(body, key), KEY, self.o, T0, NOW_MS)

    def test_confirmed_analysis_closes_the_case(self):
        self.assertEqual(self.post(payload(analysis={"outcome": "confirmed", "confidence": 0.95}))["status"], "applied")
        self.assertEqual(self.case.state, "confirmed")

    def test_ended_then_analyzed_for_the_same_call_applies_once(self):
        self.assertEqual(self.post(payload("call_ended", "dial_no_answer"))["status"], "applied")
        self.assertEqual(self.post(payload(analysis={"outcome": "no_answer", "confidence": 1}))["status"], "duplicate")
        self.assertEqual(self.o.applied[("A1", "retell:call_1")], 1)

    def test_bad_signature_changes_nothing(self):
        body = json.dumps(payload(analysis={"outcome": "confirmed", "confidence": 0.95}))
        r = handle_webhook(body, sign(body, "other"), KEY, self.o, T0, NOW_MS)
        self.assertEqual((r["status"], self.case.state), ("rejected", "in_call"))

    def test_malformed_json_and_metadata_are_rejected_not_raised(self):
        self.assertEqual(handle_webhook("{", sign("{"), KEY, self.o, T0, NOW_MS)["reason"], "bad json")
        self.assertEqual(self.post(payload(meta={}))["status"], "rejected")

    def test_low_confidence_confirmation_reaches_a_person(self):
        self.post(payload(analysis={"outcome": "confirmed", "confidence": 0.5}))
        self.assertEqual(self.case.state, "needs_person")

    def test_dnc_request_blocks_the_number(self):
        self.post(payload("call_ended", "user_requested_dnc"))
        self.assertIn("15550001111", self.o.dnc)

    def test_analyzed_first_then_ended_does_not_flip_a_do_not_call(self):
        self.post(payload("call_analyzed", "user_requested_dnc", analysis={"outcome": "no_answer", "confidence": 1}))
        self.assertIn("15550001111", self.o.dnc)
        self.assertEqual(self.case.state, "needs_person")
        self.assertEqual(self.post(payload("call_ended", "user_requested_dnc"))["status"], "duplicate")

    def test_not_connected_reason_wins_even_when_analysis_arrives_first(self):
        self.post(payload("call_analyzed", "voicemail_reached", analysis={"outcome": "confirmed", "confidence": 0.95}))
        self.assertNotEqual(self.case.state, "confirmed")
        self.assertEqual(self.case.last_outcome, "voicemail")

    def test_account_level_stop_halts_dispatch_and_opens_no_ticket(self):
        for reason in ("budget_reached", "credit_exhausted", "no_valid_payment"):
            o = Orchestrator(dialer=self.calls.append)
            case = Case("A1", "+15550001111", "America/New_York", T0 + timedelta(days=3))
            o.add_case(case, T0)
            o.tick(T0)
            body = json.dumps(payload("call_ended", reason))
            r = handle_webhook(body, sign(body), KEY, o, T0, NOW_MS)
            self.assertEqual((r["status"], o.halted), ("halted", reason))
            self.assertEqual(o.tickets.tickets, {})

    def test_abuse_flag_from_the_provider_halts_dispatch_for_every_case(self):
        """Found by the real-data check (real-data/PROTOCOL.md, Part 3): Retell documents `scam_detected` as an error stop
        ("the agent was flagged for suspected abuse"). Dialing on for other cases while the sender is flagged is the wrong
        default, so it must stop the campaign like the other account-level stops, on either event."""
        for event in ("call_ended", "call_analyzed"):
            o = Orchestrator(dialer=self.calls.append)
            a = Case("A1", "+15550001111", "America/New_York", T0 + timedelta(days=3))
            b = Case("B1", "+15550002222", "America/New_York", T0 + timedelta(days=3))
            o.add_case(a, T0)
            o.tick(T0)
            o.add_case(b, T0)
            body = json.dumps(payload(event, "scam_detected"))
            r = handle_webhook(body, sign(body), KEY, o, T0, NOW_MS)
            self.assertEqual((r["status"], o.halted), ("halted", "scam_detected"), event)
            self.assertEqual(o.tickets.tickets, {})
            n = len(o.calls)
            o.tick(T0 + timedelta(hours=1))
            self.assertEqual(len(o.calls), n, "no call may be placed for case B1 while halted")

    def test_bindings_reject_metadata_that_was_not_issued_by_us(self):
        body = json.dumps(payload(analysis={"outcome": "confirmed", "confidence": 0.95}))
        bad = handle_webhook(body, sign(body), KEY, self.o, T0, NOW_MS, bindings={"call_1": ("OTHER", 1)})
        self.assertEqual(bad["status"], "rejected")
        self.assertEqual(self.case.state, "in_call")
        unknown = handle_webhook(body, sign(body), KEY, self.o, T0, NOW_MS, bindings={})
        self.assertEqual(unknown["status"], "rejected")
        ok = handle_webhook(body, sign(body), KEY, self.o, T0, NOW_MS, bindings={"call_1": ("A1", 1)})
        self.assertEqual(ok["status"], "applied")

    def test_ignored_event_leaves_the_case_in_call(self):
        self.assertEqual(self.post(payload("call_started"))["status"], "ignored")
        self.assertEqual(self.case.state, "in_call")


if __name__ == "__main__":
    unittest.main()
