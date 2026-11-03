"""Adapter from Retell AI webhooks to this lab's result events.

Written against Retell's public documentation (checked 2026-11-03): webhook events `call_started`, `call_ended`,
`call_analyzed`; header `X-Retell-Signature: v={ms timestamp},d={hex digest}` where the digest is
HMAC-SHA256(raw_body + timestamp, api_key) with a 5 minute tolerance; `call.disconnection_reason` enum;
`call.metadata`; `call_analysis.custom_analysis_data`. NOT verified against a live Retell account or live webhooks.
The agent must be created with call metadata {case_id, attempt_no} and a post-call analysis schema that returns
`outcome` (one of this lab's outcomes) and `confidence` (0 to 1); see retell/agent_spec.json.

Design rules:
- Telephony facts beat language-model analysis: when `disconnection_reason` says the call did not connect (or the person
  asked not to be called), that mapping wins on BOTH events, whatever order the webhooks arrive in.
- One completion per call (`event_id = retell:{call_id}`): whichever event completes the attempt first wins, so the
  precedence rule above has to hold for either order.
- Doubtful input never retries silently: it becomes `unclear` and goes to a person.
- Webhooks carry unscrubbed call data (transcripts, numbers): this adapter reads fields and stores nothing.
- Only the API key that carries the webhook badge verifies signatures; Retell also publishes a source IP to allowlist.
- Replay protection here is the orchestrator's in-memory dedupe: an endpoint restart inside the 5 minute window loses it.
  Persist processed (event, call_id) keys in production."""
import hashlib
import hmac
import json
import re

from .model import OUTCOMES

TOLERANCE_MS = 5 * 60 * 1000
# disconnection reasons that decide the outcome without analysis
FINAL = {
    "dial_no_answer": "no_answer", "dial_busy": "no_answer", "voicemail_reached": "voicemail", "ivr_reached": "no_answer",
    "user_declined": "no_answer",                       # Retell: outbound call not connected, user declined the call
    "dial_failed": "dropped", "telephony_provider_unavailable": "dropped", "registered_call_timeout": "dropped",
    "concurrency_limit_reached": "dropped",
    "invalid_destination": "wrong_number", "user_requested_dnc": "refused", "user_requested_callback": "reschedule",
    # operator review rather than a retry: a carrier block, a spam label or a routing fault will repeat
    "marked_as_spam": "unclear", "network_blocked": "unclear", "telephony_provider_permission_denied": "unclear",
    "sip_routing_error": "unclear",
}
# stop the campaign, alert; never a per-case ticket. `scam_detected` is documented as an error stop (the agent was flagged
# for suspected abuse), added after the real-data check against the public docs showed it fell through to a per-case path.
ACCOUNT_HALT = frozenset({"budget_reached", "credit_exhausted", "no_valid_payment", "scam_detected"})


class AdapterError(Exception):
    pass


def verify_signature(raw_body, header, api_key, now_ms):
    if not isinstance(raw_body, str) or not isinstance(header, str) or not api_key:
        return False
    m = re.fullmatch(r"v=(\d+),d=([0-9a-fA-F]+)", header.strip())
    if not m:
        return False
    ts, digest = m.group(1), m.group(2)
    if abs(now_ms - int(ts)) > TOLERANCE_MS:
        return False
    expected = hmac.new(api_key.encode(), (raw_body + ts).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest.lower())


def _obj(v):
    return v if isinstance(v, dict) else {}


def to_result_event(payload):
    """Returns a result event dict, or None when this webhook carries no final outcome yet. Raises AdapterError on bad input."""
    if not isinstance(payload, dict) or not isinstance(payload.get("call"), dict):
        raise AdapterError("payload has no call object")
    event, call = payload.get("event"), payload["call"]
    if event not in ("call_ended", "call_analyzed"):
        return None
    meta = call.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        raise AdapterError("call metadata must be an object")
    meta = meta or {}
    call_id = call.get("call_id")
    case_id, attempt_no = meta.get("case_id"), meta.get("attempt_no")
    if not isinstance(call_id, str) or not call_id or not isinstance(case_id, str) or not 0 < len(case_id) <= 128 \
            or not isinstance(attempt_no, int) or isinstance(attempt_no, bool) or attempt_no < 1:
        raise AdapterError("call metadata must carry case_id and attempt_no")
    base = {"event_id": f"retell:{call_id}", "case_id": case_id, "attempt_no": attempt_no}
    reason = call.get("disconnection_reason")
    if reason in FINAL:                                   # telephony fact wins on either event
        return {**base, "outcome": FINAL[reason], "confidence": 1.0}
    if isinstance(reason, str) and reason.startswith("error_"):
        return {**base, "outcome": "dropped", "confidence": 1.0}
    if event == "call_ended":
        return None                                       # a spoken call: wait for call_analyzed
    data = _obj(_obj(call.get("call_analysis")).get("custom_analysis_data"))
    out, conf = data.get("outcome"), data.get("confidence")
    if out in OUTCOMES and isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0 <= conf <= 1:
        return {**base, "outcome": out, "confidence": float(conf)}
    return {**base, "outcome": "unclear", "confidence": 0.0}      # missing or invalid analysis goes to a person


def handle_webhook(raw_body, header, api_key, orchestrator, now_utc, now_ms, bindings=None):
    """`bindings`, if given, maps provider call_id -> (case_id, attempt_no) recorded at dispatch; a webhook whose
    metadata does not match is rejected, so metadata alone is never trusted."""
    if not verify_signature(raw_body, header, api_key, now_ms):
        return {"status": "rejected", "reason": "bad signature"}
    try:
        payload = json.loads(raw_body)
    except ValueError:
        return {"status": "rejected", "reason": "bad json"}
    try:
        ev = to_result_event(payload)
        reason = _obj(_obj(payload).get("call")).get("disconnection_reason")
    except AdapterError as e:
        return {"status": "rejected", "reason": str(e)}
    if reason in ACCOUNT_HALT:
        orchestrator.halt(reason, now_utc)
        return {"status": "halted", "reason": reason}
    if ev is None:
        return {"status": "ignored"}
    if bindings is not None and bindings.get(payload["call"]["call_id"]) != (ev["case_id"], ev["attempt_no"]):
        return {"status": "rejected", "reason": "call is not bound to this case and attempt"}
    return orchestrator.handle_result(ev, now_utc)
