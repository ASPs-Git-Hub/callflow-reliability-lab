# CallFlow Reliability Lab

**Independent technical demonstration on a fictional plumbing company, synthetic data only. Not a client result.**

Status: BUILT_UNVERIFIED. Self-tested on 2026-11-03 (Python 3.14, standard library only) and reviewed once by two independent AI reviewers (ChatGPT and Claude Fable, working separately); their findings are fixed below. No human engineer has reviewed it. No telephony and no language model: calls are an injected function and results arrive as events.

## Problem
Automated outbound calling (appointment confirmations, reminders, collections) fails quietly in predictable ways: calls in the middle of the customer's night, retries that never stop, duplicate and late results applied twice or thrown away, low-confidence "confirmations" trusted, do-not-call requests ignored, duplicated tickets for people, calls that never report back. This lab reproduces those failures in a naive orchestrator and fixes each one, with tests that fail on the naive version and pass on the fixed one.

## What is in it (`src/callflow/`)
- `model.py`: a fixed list of case states, an explicit transition table, a structured outcome vocabulary, a validated call policy, E.164-style phone normalisation (US-first intake), timezone-aware appointments stored in UTC.
- `schedule.py`: call windows in the customer's local time, weekday rules, daylight-saving-safe (zoneinfo), appointment cutoff, minimum gap between attempts.
- `orchestrator.py`: one serialized controller (a lock on every public method; no persistence). Dispatch (concurrency limit, nearest appointment first, do-not-call checked at dispatch time, a failing dialer cannot abort the batch), idempotent result handling keyed by case and event, deterministic precedence between a result and its timeout, late real answers used instead of calling again, number-level do-not-call that does not depend on how fresh the result is, low-confidence results routed to a person, provider halt.
- `handoff.py`: one ticket per (case, reason), priority upgrade on re-trigger, an allowlist for free-text context so the phone stays masked.
- `matcher.py`: reply-to-appointment matching (reference, phone plus date, fuzzy name plus date); ambiguous or weak matches go to a person.
- `alerts.py`: stuck calls, overdue attempts, ageing tickets (urgent sooner), low-confidence spike, provider halt.
- `retell_adapter.py`: Retell AI webhook adapter written from Retell's public documentation (checked 2026-11-03): signature check, five-minute replay window, telephony facts (`disconnection_reason`) win over language-model analysis on either event order, spam and carrier-block reasons go to a person instead of retrying, account-level stops halt dispatch, optional call-to-case bindings, every doubtful input becomes `unclear`. `retell/agent_spec.json` specifies the agent (specification only).
- `naive.py`: the same interface with nine documented defects. `sim.py`: seeded simulator whose audit works from its own observations.
Run: `python3 -m unittest discover -s tests`, `python3 scripts/report.py`, `python3 scripts/mutate.py` (Python 3.10+, no dependencies).

## Evidence (executed 2026-11-03; output in `evidence/`)
- `unittest-run.txt`: 117 tests pass. Fifteen contracts run against both implementations: the naive one must fail each, the fixed one must pass.
- `mutation-run.txt`: 77 of 77 planted defects detected. Where the code has two guards for the same rule (for example the attempt limit), both are removed together. The mutants were chosen by the author after two independent reviewers proposed more; the score measures sensitivity to those defects, not completeness of the tests.
- `simulation-report.txt`: 5 seeds x 150 synthetic cases over 6 days, spanning a US daylight-saving change. 13 checks, computed from what the simulator itself observed (calls logged inside the dialer, deliveries logged with the status returned, local time checked with zoneinfo directly). The fixed orchestrator has 0 violations in all 13. The naive one violates 9: for example 5,224 calls outside the customer's local window, 140 calls to do-not-call numbers, 444 cases over the attempt limit, 312 results applied twice, 73 low-confidence results accepted as confirmed, 172 cases still being worked after their appointment. Four checks are zero for the naive version too (they exist to catch regressions, not as evidence of its failure), and non-vacuity and liveness tests prove the audit can fail: a do-nothing orchestrator trips the liveness checks.

### Funnel (assumed caller behaviour, see `PROFILE` in `sim.py`)
Under the assumptions (85% of confirmations and declines stated clearly, 15% hesitantly; answer rates by local hour; 3% lost and 4% late results) the naive orchestrator confirms 437 of 750 cases (58%) and the fixed one 402 (54%), sending 330 (44%) to a person. The gap is small and is a property of the assumed confidence distribution and policy defaults (three-hour gap, four attempts, two-hour cutoff), not of real callers. Do not quote any funnel number as a real-world rate.

## Review history
First-version reviews found real defects, all fixed with tests: a real answer arriving after the timeout was discarded and the customer was called again; a late refusal did not block the number; the audit trusted the orchestrator's own counters (a deleted call log passed); Retell analysis could override "voicemail" or "do-not-call" when webhooks arrived out of order; user-declined and spam-label reasons were mishandled; phones were compared on their last ten digits; boolean attempt numbers were accepted; a caller-supplied context could unmask the phone; duplicate case ids and naive datetimes were accepted; a failing dialer aborted the batch.

## What this does NOT prove
- The caller model and the funnel are assumed. No real answer, confirmation, complaint or consent rates are measured.
- The Retell adapter has not been run against a live Retell account or live webhooks, and no agent has been deployed. It uses Retell's documented formats only.
- One serialized controller with in-memory state: nothing survives a restart (including the replay and duplicate memory, so a restart inside the five-minute signature window can re-apply a webhook), nothing is shared between workers, there are no database transactions, and a provider call is not reconciled after a timeout.
- Legal rules (calling hours, consent, recording, do-not-call registries) differ by country and are not modelled; the window validation only keeps hours between 06:00 and 22:00 local and away from daylight-saving transition hours. Calls placed just before the window ends can run past it. Six sample time zones only.
- Phone handling is US-first and not a validator. The matcher thresholds are demonstration values on tiny examples.
- Self-tested and AI-reviewed only.
