"""Mutation check: plants one small defect at a time in src/callflow (temporary copy), runs the suite, and requires
it to FAIL. Exit 1 if any mutation survives or a pattern no longer matches exactly once.
Run from the lab root: python3 scripts/mutate.py. The table is the exact record of what is planted."""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
O, S, H, M, X, A, R = "orchestrator.py", "schedule.py", "handoff.py", "model.py", "matcher.py", "alerts.py", "retell_adapter.py"

MUTATIONS = [
    # orchestrator.py
    (O, "do-not-call check removed at dispatch", "if norm_phone(c.phone) in self.dnc:", "if False:"),
    (O, "appointment cutoff removed at dispatch", "if now > c.appointment_start - p.stop_before_appt:", "if False:"),
    (O, "attempt limit removed at BOTH guards (dispatch and after a retryable result)", [
        ('                if len(c.attempts) >= p.max_attempts:\n                    self._to_person(c, "attempts_exhausted", now)\n                    continue\n', ""),
        ('if len(c.attempts) >= p.max_attempts:\n                self._to_person(c, "attempts_exhausted", now)\n            else:', 'if False:\n                self._to_person(c, "attempts_exhausted", now)\n            else:')], None),
    (O, "call window check removed at dispatch", "if not in_window(now, c.tz, p):", "if False:"),
    (O, "concurrency limit removed", "if in_call >= p.max_concurrent:", "if False:"),
    (O, "nearest appointment no longer first", "key=lambda c: c.appointment_start)  # nearest appointment first", "key=lambda c: c.id)  # nearest appointment first"),
    (O, "call timeout removed", 'if c.state == "in_call" and c.in_call_since is not None and now - c.in_call_since >= self.policy.call_timeout:', "if False:"),
    (O, "duplicate event check removed", "if key in self.seen:", "if False:"),
    (O, "stale results accepted for any attempt", 'if c.state == "in_call" and current:', 'if c.state == "in_call":'),
    (O, "late result path removed", 'if c.state == "waiting" and current and c.attempts[-1].get("timed_out"):', "if False:"),
    (O, "deadline not enforced when a result arrives", "            self._expire(c, now)                            # a result after the deadline counts as late, deterministically\n", ""),
    (O, "refusal suppression tied to attempt freshness", 'if ev["outcome"] == "refused":                  # suppression is about the number, not about this attempt\'s freshness', "if False:"),
    (O, "refusal no longer blocks the number (both places)", [
        ('            if ev["outcome"] == "refused":                  # suppression is about the number, not about this attempt\'s freshness\n                self.dnc.add(norm_phone(c.phone))\n', ""),
        ('            self.dnc.add(norm_phone(c.phone))\n            self._to_person(c, "refused", now)', '            self._to_person(c, "refused", now)')], None),
    (O, "confidence threshold ignored", 'if ev["confidence"] >= p.confidence_threshold:', "if True:"),
    (O, "late result ignores the confidence threshold", 'if out in ("confirmed", "declined") and ev["confidence"] >= self.policy.confidence_threshold:', 'if out in ("confirmed", "declined"):'),
    (O, "retry ignores the minimum gap", "t = schedule_next(c, now, p)", "t = schedule_next(c, now, p, first=True)"),
    (O, "urgent priority never set", "urgent = c.appointment_start - now <= self.policy.urgent_within", "urgent = False"),
    (O, "missing-field validation removed", "if not isinstance(ev, dict) or any(k not in ev for k in REQUIRED):", "if False:"),
    (O, "confidence range check removed", 'or not 0 <= ev["confidence"] <= 1:', "or False:"),
    (O, "boolean confidence accepted", 'or isinstance(ev["confidence"], bool)', "or False"),
    (O, "boolean attempt number accepted", 'or isinstance(ev["attempt_no"], bool)', "or False"),
    (O, "non-positive attempt number accepted", 'or ev["attempt_no"] < 1', "or False"),
    (O, "unknown case check removed", "if c is None:", "if False:"),
    (O, "event ids not namespaced by case", 'key = (ev["case_id"], ev["event_id"])', 'key = ev["event_id"]'),
    (O, "duplicate case ids accepted", "if case.id in self.cases:", "if False:"),
    (O, "naive current time accepted", "if now.tzinfo is None:", "if False:"),
    (O, "dialer failure leaves a phantom call", 'self._apply(c, {"event_id": f"dialer_error:{c.id}:{n}", "outcome": "dropped", "confidence": 1.0}, now)', "pass"),
    (O, "provider halt ignored at dispatch", "if self.halted:\n                return", "if False:\n                return"),
    (O, "late retryable result processed", "if out in RETRYABLE:                                # nothing new: the retry is already scheduled", "if False:                                # nothing new: the retry is already scheduled"),
    (O, "late result leaves the retry scheduled", 'c.attempts[-1]["outcome"], c.last_outcome, c.next_attempt_at = out, out, None', 'c.attempts[-1]["outcome"], c.last_outcome, c.next_attempt_at = out, out, c.next_attempt_at'),
    (O, "person resolution not idempotent", "if not self.tickets.resolve(tid, resolution, now):", "if False:"),
    (O, "resolution value not validated", "if resolution not in HUMAN_RESOLUTIONS:", "if False:"),
    (O, "unknown ticket raises", 'if t is None:\n                return {"status": "rejected", "reason": "unknown ticket"}', 'if False:\n                return {"status": "rejected", "reason": "unknown ticket"}'),
    # handoff.py
    (H, "ticket idempotency key removed", "if key in self._by_key:", "if False:"),
    (H, "re-trigger never upgrades priority", 'if PRIORITY_RANK[priority] < PRIORITY_RANK[t["priority"]]:', "if False:"),
    (H, "urgent tickets sorted last", 'PRIORITY_RANK = {"urgent": 0, "normal": 1}', 'PRIORITY_RANK = {"urgent": 1, "normal": 0}'),
    (H, "context can override the masked phone (allowlist and merge order, two guards)", [
        ('ALLOWED_CONTEXT = ("note", "detail")', 'ALLOWED_CONTEXT = ("note", "detail", "phone")'),
        ('                 **extra, "phone": mask_phone(case.phone), "appointment_start": case.appointment_start.isoformat(),\n                 "attempts": len(case.attempts), "last_outcome": case.last_outcome}}',
         '                 "phone": mask_phone(case.phone), "appointment_start": case.appointment_start.isoformat(),\n                 "attempts": len(case.attempts), "last_outcome": case.last_outcome, **extra}}')], None),
    (H, "tickets not ordered by creation time", 'key=lambda t: (PRIORITY_RANK[t["priority"]], t["created_at"]))', 'key=lambda t: (PRIORITY_RANK[t["priority"]], t["id"]))'),
    # model.py
    (M, "phone not masked in ticket context", 'return "***" + d[-4:] if d else ""', "return d"),
    (M, "phones reduced to their last ten digits", 'return "1" + digits if len(digits) == 10 else digits', "return digits[-10:]"),
    (M, "terminal state can be reopened", '"confirmed": set(),', '"confirmed": {"waiting"},'),
    (M, "transition table not enforced", "if new_state not in ALLOWED.get(case.state, set()):", "if False:"),
    (M, "appointment not normalised to UTC", "self.appointment_start = self.appointment_start.astimezone(UTC)", "pass"),
    (M, "call window not validated", "if not (6 <= self.window_start_hour < self.window_end_hour <= 22):", "if False:"),
    # schedule.py
    (S, "retry gap ignored in scheduling", "base = after_utc if first else after_utc + policy.min_gap", "base = after_utc"),
    (S, "appointment cutoff ignored in scheduling", "if t is None or t > case.appointment_start - policy.stop_before_appt:", "if t is None:"),
    (S, "window end made inclusive", "policy.window_start_hour <= l.hour < policy.window_end_hour", "policy.window_start_hour <= l.hour <= policy.window_end_hour"),
    (S, "same-day candidate ignores the current time", "cand = max(l, start) if day == 0 else start", "cand = start"),
    (S, "customer time zone ignored", "start = datetime(d.year, d.month, d.day, policy.window_start_hour, tzinfo=z)", "start = datetime(d.year, d.month, d.day, policy.window_start_hour, tzinfo=UTC)"),
    (S, "weekday rule ignored", "if d.weekday() not in policy.allowed_weekdays:", "if False:"),
    # matcher.py
    (X, "fuzzy name threshold removed", "FUZZY_MIN = 0.85", "FUZZY_MIN = 0.0"),
    (X, "ambiguous name match auto-resolved", 'if len(hits) > 1:\n            return "ambiguous", hits, "name+date"', 'if False:\n            return "ambiguous", hits, "name+date"'),
    (X, "ambiguous phone match auto-resolved", 'if len(hits) > 1:\n            return "ambiguous", hits, "phone+date"', 'if False:\n            return "ambiguous", hits, "phone+date"'),
    # alerts.py
    (A, "stuck-call alert disabled", "now - c.in_call_since >= p.call_timeout * stuck_factor", "now - c.in_call_since >= p.call_timeout * 1000"),
    (A, "overdue-attempt alert disabled", "now - c.next_attempt_at >= overdue", "now - c.next_attempt_at >= overdue * 1000"),
    (A, "urgent tickets age like normal ones", 'limit = urgent_age if t["priority"] == "urgent" else ticket_age', "limit = ticket_age"),
    (A, "confidence spike alert disabled", "if bad / spike_window >= spike_share:", "if bad / spike_window > 1:"),
    (A, "provider halt alert disabled", 'if getattr(orch, "halted", None):', "if False:"),
    # retell_adapter.py
    (R, "webhook signature check removed", "if not verify_signature(raw_body, header, api_key, now_ms):", "if False:"),
    (R, "replay tolerance removed", "if abs(now_ms - int(ts)) > TOLERANCE_MS:", "if False:"),
    (R, "timestamp left out of the signed data", "(raw_body + ts).encode()", "raw_body.encode()"),
    (R, "no-answer mapped to confirmed", '"dial_no_answer": "no_answer"', '"dial_no_answer": "confirmed"'),
    (R, "do-not-call request mapped to no-answer", '"user_requested_dnc": "refused"', '"user_requested_dnc": "no_answer"'),
    (R, "spam label retried instead of reviewed", '"marked_as_spam": "unclear"', '"marked_as_spam": "no_answer"'),
    (R, "declined call sent to a person instead of retried", '"user_declined": "no_answer"', '"user_declined": "unclear"'),
    (R, "provider errors no longer retryable", 'reason.startswith("error_")', 'reason.startswith("errorX")'),
    (R, "telephony fact only wins on call_ended", "if reason in FINAL:                                   # telephony fact wins on either event", 'if reason in FINAL and event == "call_ended":'),
    (R, "analysis confidence range not checked", "and 0 <= conf <= 1:", "and True:"),
    (R, "metadata validation removed", 'raise AdapterError("call metadata must carry case_id and attempt_no")', "pass"),
    (R, "boolean attempt number accepted", "or isinstance(attempt_no, bool)", "or False"),
    (R, "non-positive attempt number accepted", "or attempt_no < 1:", "or False:"),
    (R, "metadata type not checked", "if meta is not None and not isinstance(meta, dict):", "if False:"),
    (R, "nested values not type-guarded", "return v if isinstance(v, dict) else {}", "return v"),
    (R, "spoken call closed before analysis", "return None                                       # a spoken call: wait for call_analyzed", 'return {**base, "outcome": "confirmed", "confidence": 1.0}'),
    (R, "missing analysis trusted as confirmed", 'return {**base, "outcome": "unclear", "confidence": 0.0}', 'return {**base, "outcome": "confirmed", "confidence": 1.0}'),
    (R, "account-level stop ignored", "if reason in ACCOUNT_HALT:", "if False:"),
    (R, "provider abuse flag (scam_detected) no longer halts dispatch", '"no_valid_payment", "scam_detected"})', '"no_valid_payment"})'),
    (R, "call bindings not enforced", 'if bindings is not None and bindings.get(payload["call"]["call_id"]) != (ev["case_id"], ev["attempt_no"]):', "if False:"),
]


def suite_passes(d):
    r = subprocess.run([sys.executable, "-B", "-I", "-m", "unittest", "discover", "-s", "tests"], cwd=d,
                       capture_output=True, text=True, timeout=300, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return r.returncode == 0


def main():
    tmp = tempfile.mkdtemp(prefix="mutate-callflow-")
    work = os.path.join(tmp, "lab")
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", "evidence"))
    if not suite_passes(work):
        print("baseline suite FAILS; refusing to run mutations")
        sys.exit(2)
    bad = 0
    for i, (fname, desc, old, new) in enumerate(MUTATIONS, 1):
        path = os.path.join(work, "src", "callflow", fname)
        with open(path, encoding="utf-8") as f:
            original = f.read()
        pairs = old if isinstance(old, list) else [(old, new)]
        if any(original.count(o) != 1 for o, _ in pairs):
            print(f"M{i:02d} SKIPPED (a pattern does not match exactly once): {desc}")
            bad += 1
            continue
        mutated = original
        for o, n in pairs:
            mutated = mutated.replace(o, n)
        with open(path, "w", encoding="utf-8") as f:
            f.write(mutated)
        killed = not suite_passes(work)
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
        print(f"M{i:02d} {'KILLED  ' if killed else 'SURVIVED'} {fname}: {desc}")
        bad += 0 if killed else 1
    print(f"restored baseline passes: {suite_passes(work)}")
    print(f"{len(MUTATIONS) - bad} of {len(MUTATIONS)} mutations killed")
    shutil.rmtree(tmp)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
