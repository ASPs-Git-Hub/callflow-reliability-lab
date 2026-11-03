"""Part 3: the lab's Retell adapter against Retell's public specification. See ../PROTOCOL.md.
Usage: python3 -I part3_retell.py <dir with the downloaded Retell docs files>
The 'reference' verifiers below are my own transcriptions of the algorithms in the public docs and SDK sources;
no downloaded code is executed."""
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("CALLFLOW_SRC") or os.path.join(HERE, "..", "..", "src"))
from callflow import retell_adapter as ra  # noqa: E402
from callflow.model import UTC, Case, Policy  # noqa: E402
from callflow.orchestrator import Orchestrator  # noqa: E402

FAILS = []


def check(desc, ok):
    print("%s  %s" % ("PASS" if ok else "FAIL", desc))
    if not ok:
        FAILS.append(desc)


def hexdig(key, text):
    return hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def ref_sign(body, key, ts):
    return "v=%d,d=%s" % (ts, hexdig(key, body + str(ts)))


def ref_py(body, header, key, now_ms, tol=300000):          # transcription of retell-python-sdk webhook_auth.verify
    m = re.fullmatch(r"v=(\d+),d=([0-9a-f]{64})", header)
    if not m:
        return False
    post = int(m.group(1))
    if abs(now_ms - post) > tol:
        return False
    return hmac.compare_digest(hexdig(key, body + str(post)), m.group(2))


def ref_ts(body, header, key, now_ms, tol=300000):          # transcription of retell-typescript-sdk webhook_auth.ts verify
    m = re.fullmatch(r"v=(\d+),d=([0-9a-fA-F]+)", header)
    if not m:
        return False
    post = int(m.group(1))
    if post > 2 ** 53 - 1 or len(m.group(2)) != 64 or abs(now_ms - post) > tol:
        return False
    return hmac.compare_digest(hexdig(key, body + str(post)), m.group(2).lower())


# ---- 1. SDK published test vector (both SDK test files carry the same constants)
BODY, KEY, TS = '{"event":"call_ended"}', "test-api-key", 1_700_000_000_000
DIGEST = "07024cabe7dd8f6d1c6e4a8324ca92c812e6ad4a7ee506c04f7e462e3321823e"
print("== 1. SDK test vector ==")
check("independent HMAC-SHA256(body + timestamp, key) reproduces the SDK test digest", hexdig(KEY, BODY + str(TS)) == DIGEST)
check("lab verify_signature accepts the SDK test vector", ra.verify_signature(BODY, "v=%d,d=%s" % (TS, DIGEST), KEY, TS))

# ---- 2. malformed-signature cases copied from the two SDK test files (all expected: rejected)
print("\n== 2. malformed signatures listed in the SDK tests (expected: rejected by every SDK) ==")
mal = [("", "empty"), ("v=%d" % TS, "no digest"), ("d=%s,v=%d" % (DIGEST, TS), "fields swapped"),
       ("prefix-v=%d,d=%s" % (TS, DIGEST), "prefix"), ("v=%d,d=%s-suffix" % (TS, DIGEST), "suffix"),
       ("v=%d,d=not-hex" % TS, "non-hex digest"), ("v=%d,d=%s" % (TS, DIGEST[2:]), "digest too short"),
       ("v=999999999999999999999999,d=%s" % DIGEST, "huge timestamp (TS test)"), ("v=-%d,d=%s" % (TS, DIGEST), "negative timestamp (PY test)"),
       ("v=%d,d=%s,extra=value" % (TS, DIGEST), "extra field (PY test)")]
for h, d in mal:
    check("lab rejects: %s" % d, ra.verify_signature(BODY, h, KEY, TS) is False)

# ---- 3. tolerance
print("\n== 3. five-minute tolerance (SDK tests: inclusive at 300000 ms, both directions) ==")
sig = "v=%d,d=%s" % (TS, DIGEST)
for delta, exp in ((300000, True), (300001, False), (-300000, True), (-300001, False)):
    check("now = signature time %+d ms -> %s" % (delta, "accept" if exp else "reject"), ra.verify_signature(BODY, sig, KEY, TS + delta) is exp)
check("tampered body rejected", ra.verify_signature(BODY + " ", sig, KEY, TS) is False)
check("wrong key rejected", ra.verify_signature(BODY, sig, "wrong-api-key", TS) is False)

# ---- 4. differential cases of mine
print("\n== 4. differential cases (lab vs my transcription of each SDK) ==")
nb = '{"name":"Zoë – 東京"}'
cases = [
    ("valid", BODY, sig, KEY),
    ("upper-case digest", BODY, "v=%d,d=%s" % (TS, DIGEST.upper()), KEY),
    ("leading zero in timestamp, digest over the normalised number", BODY, "v=0%d,d=%s" % (TS, DIGEST), KEY),
    ("leading zero in timestamp, digest over the raw string", BODY, "v=0%d,d=%s" % (TS, hexdig(KEY, BODY + "0" + str(TS))), KEY),
    ("leading space in header", BODY, " " + sig, KEY),
    ("trailing space in header", BODY, sig + " ", KEY),
    ("trailing newline in header", BODY, sig + "\n", KEY),
    ("digest 63 hex chars", BODY, "v=%d,d=%s" % (TS, DIGEST[:-1]), KEY),
    ("digest 65 hex chars", BODY, "v=%d,d=%s0" % (TS, DIGEST), KEY),
    ("non-ASCII body, correct signature", nb, ref_sign(nb, KEY, TS), KEY),
    ("empty body, correct signature", "", ref_sign("", KEY, TS), KEY),
    ("body re-serialised (space added after colon)", '{"event": "call_ended"}', sig, KEY),
]
diffs = []
for name, body, header, key in cases:
    lab = ra.verify_signature(body, header, key, TS)
    py, ts_ = ref_py(body, header, key, TS), ref_ts(body, header, key, TS)
    flag = "" if lab == py == ts_ else "   <-- differs"
    print("%-62s lab=%-5s py_sdk=%-5s ts_sdk=%-5s%s" % (name, lab, py, ts_, flag))
    if flag:
        diffs.append((name, lab, py, ts_))
lab_accepts_both_reject = [d[0] for d in diffs if d[1] and not d[2] and not d[3]]
print("LISTED (lab accepts, both SDKs reject):", lab_accepts_both_reject)
print("differences listed:", [(d[0], "lab=%s" % d[1], "py=%s" % d[2], "ts=%s" % d[3]) for d in diffs])

# ---- 5. documented sample payloads, made valid JSON
print("\n== 5. documented sample payloads through handle_webhook ==")
T0 = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)               # 09:00 in Chicago, inside the default window
NOW_MS = int(T0.timestamp() * 1000)
SAMPLE_ENDED = {"event": "call_ended", "call": {"call_type": "phone_call", "from_number": "+12137771234", "to_number": "+12137771235",
    "direction": "inbound", "call_id": "Jabr9TXYYJHfvl6Syypi88rdAHYHmcq6", "agent_id": "oBeDLoLOeuAbiuaMFXRtDOLriTJ5tSxD",
    "call_status": "registered", "metadata": {}, "retell_llm_dynamic_variables": {"customer_name": "John Doe"},
    "start_timestamp": 1714608475945, "end_timestamp": 1714608491736, "disconnection_reason": "user_hangup", "transcript": "...",
    "transcript_object": [], "transcript_with_tool_calls": [], "data_storage_setting": "everything"}}
SAMPLE_ANALYZED = {"event": "call_analyzed", "call": {**SAMPLE_ENDED["call"], "call_status": "ended", "call_analysis": {
    "call_summary": "The agent called the user to ask about their purchase inquiry.", "in_voicemail": False, "user_sentiment": "Positive",
    "call_successful": True, "custom_analysis_data": {"appointment_booked": True}}}}


def fresh():
    o = Orchestrator(Policy())
    c = Case("C-1", "2025550100", "America/Chicago", T0 + timedelta(days=30))
    o.add_case(c, T0)
    o.tick(T0)
    assert c.state == "in_call"
    return o, c


def send(o, payload, key=KEY, ts=NOW_MS, header=None):
    body = json.dumps(payload, separators=(",", ":"))
    return ra.handle_webhook(body, header or ref_sign(body, key, ts), key, o, T0 + timedelta(minutes=1), NOW_MS)


def with_meta(p, meta=None, **call_over):
    q = json.loads(json.dumps(p))
    q["call"]["metadata"] = meta if meta is not None else {"case_id": "C-1", "attempt_no": 1}
    q["call"].update(call_over)
    return q


o, c = fresh()
r = send(o, SAMPLE_ENDED)
print("sample call_ended exactly as published (metadata {}):", r)
check("published sample without lab metadata is rejected, not applied", r["status"] == "rejected" and c.state == "in_call")
r = send(o, with_meta(SAMPLE_ENDED))
print("sample call_ended + lab metadata (user_hangup):", r)
check("spoken call, call_ended alone is ignored (waits for call_analyzed)", r == {"status": "ignored"} and c.state == "in_call")
r = send(o, with_meta(SAMPLE_ANALYZED))
print("sample call_analyzed (docs custom_analysis_data has no outcome field) + lab metadata:", r, "| case state:", c.state)
check("analysis without the lab's outcome field goes to a person, never to confirmed", c.state == "needs_person" and c.last_outcome == "unclear")
o, c = fresh()
good = with_meta(SAMPLE_ANALYZED)
good["call"]["call_analysis"]["custom_analysis_data"] = {"outcome": "confirmed", "confidence": 0.93}
rs = [send(o, good) for _ in range(3)]
print("same signed call_analyzed delivered 3 times (documented retries):", [x["status"] for x in rs], "| state:", c.state)
check("three deliveries apply the result once", [x["status"] for x in rs][0] == "applied" and all(x["status"] != "applied" for x in rs[1:]) and c.state == "confirmed" and o.applied.get(("C-1", "retell:" + good["call"]["call_id"])) == 1)
o, c = fresh()
nc = with_meta(SAMPLE_ENDED, disconnection_reason="dial_no_answer", call_status="not_connected")
r1 = send(o, nc)
na = with_meta(SAMPLE_ANALYZED, disconnection_reason="dial_no_answer", call_status="not_connected")
r2 = send(o, na)
print("not-connected call: call_ended then call_analyzed (docs: both are sent):", r1["status"], r2["status"], "| state:", c.state)
check("not-connected call is completed by call_ended; the later call_analyzed is a duplicate", r1["status"] == "applied" and r2["status"] != "applied" and c.state == "waiting")
o, c = fresh()
r = send(o, with_meta(SAMPLE_ENDED), header=ref_sign(json.dumps(with_meta(SAMPLE_ENDED), separators=(",", ":")), "other-key", NOW_MS))
check("wrong-key signature rejected at the webhook level", r["status"] == "rejected")
r = send(o, with_meta(SAMPLE_ENDED), ts=NOW_MS - 301000)
check("signature older than 5 minutes rejected at the webhook level", r["status"] == "rejected")

# ---- 6. DisconnectionReason enum from the Get Call reference
print("\n== 6. documented DisconnectionReason enum vs the adapter ==")
txt = open(os.path.join(sys.argv[1], "docs_get-call.md"), encoding="utf-8").read()
block = txt.split("DisconnectionReason:", 1)[1].split("CallAnalysis:", 1)[0]
enum = re.findall(r"^\s+- ([a-z_]+)\s*$", block, re.M)
print("enum values read from the reference:", len(enum))
check("enum was found and read (count printed above)", len(enum) > 30)
check("every key in the adapter's FINAL mapping is a documented value (typo check)", set(ra.FINAL) <= set(enum))
check("every account-level stop reason is a documented value", set(ra.ACCOUNT_HALT) <= set(enum))
rows = []
for r_ in enum:
    base = {"call_id": "x1", "metadata": {"case_id": "C-1", "attempt_no": 1}, "disconnection_reason": r_}
    e1 = ra.to_result_event({"event": "call_ended", "call": base})
    e2 = ra.to_result_event({"event": "call_analyzed", "call": base})
    halt = r_ in ra.ACCOUNT_HALT
    rows.append((r_, "halt dispatch" if halt else (e1["outcome"] if e1 else "wait for analysis"), e2["outcome"] if e2 else "-"))
print("%-40s %-22s %s" % ("disconnection_reason", "on call_ended", "on call_analyzed with no analysis"))
for a, b, c_ in rows:
    print("%-40s %-22s %s" % (a, b, c_))
unmapped = [a for a, b, _ in rows if b == "wait for analysis"]
print("\nreasons that fall to the analysis path (spoken-call or not otherwise decided):", unmapped)

print("\nFAILED CHECKS: %d" % len(FAILS))
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
