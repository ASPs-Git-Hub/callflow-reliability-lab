# Protocol (written before the first scored run; not changed after seeing results)

Date written: 2026-11-03. Lab under test: `proofs/callflow-reliability-lab` as committed in the working tree on this date, Python 3.14, standard library only. Data files live outside the repo (scratchpad); the scripts take their location as an argument. Each run's raw output goes to `evidence/*.txt`.

If a result forces a change to this file, the change goes in a dated "Amendments" section at the bottom. Nothing above it is edited.

## Part 1. Calling windows on real time-zone rules

### Policy under test (exactly)
A call is allowed at instant T for a customer in IANA zone Z when the local wall-clock hour of T in Z is 08 to 20 inclusive, that is 08:00:00 up to but not including 21:00:00 local time, on any day of the week. This is the window shape of common US telemarketing rules, used here as a stated policy only. It is not legal advice and the lab does not model any law. The lab is configured with `Policy(window_start_hour=8, window_end_hour=21, allowed_weekdays=frozenset(range(7)))`, which passes the lab's own `validate()`.

### Zones
- Source: Google libphonenumber `resources/timezones/map_data.txt` (Apache-2.0), every line whose key starts with `1` (North American Numbering Plan). The zone set is the union of the zone IDs on those lines. Area codes are mapped to zones in `data/areacode_zones.csv`.
- Area codes are labelled US, CA (Canada), US-territory or other-NANP (Caribbean) for reporting only, from the libphonenumber geocoding file plus a short hand list written in the script. All zones are tested whatever the label.
- Supplement, labelled as mine and not from the dataset: `America/Indiana/Indianapolis`, `America/Indiana/Knox`, `America/Kentucky/Louisville`, `America/Detroit`, `America/Whitehorse`, `America/Iqaluit`, `America/Moncton`. The dataset maps Indiana and Kentucky area codes to the big zones; the supplement checks the Indiana-style sub-zones directly.

### Time data
System IANA tz database read by `zoneinfo` (the lab's path). Version string recorded from `/usr/share/zoneinfo/tzdata.zi`.

### Oracle (independent of the lab and of `zoneinfo`)
A small TZif (RFC 8536) reader written for this check, `scripts/tzif_oracle.py`. It reads the zone's binary file, uses the transition table, and for instants after the last stored transition applies the POSIX TZ rule string from the file footer. Local offset = what the file says for that instant. Local hour = `((T + offset) mod 86400) div 3600`. Allowed = 8 <= hour < 21. The reader is validated before any scoring by comparing its UTC offset with `zoneinfo`'s for every whole hour of 2025-01-01 to 2028-01-01 UTC in every zone. If any difference appears, the reader is fixed or the zone is reported as "oracle disputed" and excluded from the headline count, and the exclusion is stated.

### Instants
- Transitions: every change of UTC offset from 2025-01-01 00:00 UTC to 2028-01-01 00:00 UTC, found from the oracle and cross-checked against hourly `zoneinfo` scanning.
- For each transition: every whole UTC minute from 48 hours before to 48 hours after it (5,760 instants).
- Zones with no transition in the range (for example Phoenix, Regina, Honolulu) are tested over the same 96-hour windows around the transitions of `America/New_York` in the range, as controls.
- Half-hour zones (`America/St_Johns`) and Arizona are in the zone set and tested like all others.

### Systems scored
1. **Lab dispatch** (headline). For each instant T: a fresh `Orchestrator`, one case in the zone with an appointment 400 days later, `next_attempt_at` forced to T (a stale schedule, the situation the dispatch guard exists for), `tick(T)`. Allowed = the injected dialer was called. 
2. **Lab scheduling.** For each instant T the case's `next_attempt_at` after `add_case(T)`. Correct = the earliest oracle-allowed minute at or after T. Wrong-too-early = the scheduled time is outside the oracle window (unsafe). Wrong-too-late = inside the window but later than the earliest allowed minute (valid minutes refused).
3. **Naive orchestrator** (`naive.py`, as shipped). Allowed whenever the case is due.
4. **UTC-clock baseline** (my own addition, not in the lab): `in_window(T, "UTC", policy)`, the behaviour the naive module's header describes as "uses UTC wall clock".
5. **Stress variant** (outside the lab's `validate()` range, reported separately and never mixed into the headline): for every transition, a window of two local hours placed on the skipped or repeated local hours (for example 02:00 to 04:00 for a spring change at 02:00), tested with `schedule.in_window` and `schedule.next_allowed` directly against the same wall-clock-hour oracle.

### What counts as a violation
- **Unsafe allow (worst):** system says call, oracle says outside the window.
- **Valid refused:** system says no call (or schedules later than the earliest minute), oracle says inside the window.
Counted in whole minutes per zone and per zone-local calendar day. Also reported: for every transition, the skipped (gap) or repeated (overlap) local interval, and whether it touches 08:00 to 21:00.

### Expectation written in advance
The lab should show 0 unsafe allows and 0 valid refused in systems 1 and 2. The naive orchestrator should allow every minute, so its unsafe allows equal the oracle's closed minutes. A non-zero lab count is a finding to report, not to explain away.

## Part 2. Retry caps on a real campaign

- Data: UCI Machine Learning Repository, Bank Marketing, dataset id 222, file `bank-additional-full.csv` inside `bank-additional.zip` inside the downloaded zip. Moro, Rita and Cortez (2014). Licence confirmed on the official dataset page: CC BY 4.0.
- Unit: one row = one client record. The file has no client id, so "client" means "row". 41,188 rows are expected; a different count is reported as a deviation.
- Field `campaign`: number of contacts performed during this campaign for this client, including the last one. Field `y`: subscribed a term deposit, yes or no.
- Cap: the lab's default `Policy().max_attempts`, read from the code at run time (currently 4). The lab refuses a fifth call and sends the case to a person.
- Counts: clients with `campaign` above the cap; total calls = sum of `campaign`; excess calls = sum of max(0, campaign - cap); excess share = excess / total; subscription rate for `campaign` <= cap versus > cap, with a Wilson 95% interval; table by `campaign` bucket. Descriptive only: no causal claim, because who gets called many times is not random.
- Replay through the lab: every row becomes a case; the lab is ticked at each scheduled time; the first `campaign - 1` contacts are reported as `no_answer` and the last contact as `confirmed` if `y` is yes, otherwise `declined`. The outcome of the earlier contacts is not in the data, so this is an assumption of the replay and is stated. The number of calls the lab places must equal min(`campaign`, cap) for every row; any difference is reported.
- Sensitivity caps 3 and 6 are reported as extra rows, labelled informational.
- The dataset has no clock time of day and no time zone, so it cannot test Part 1's window. No time-window claim is made from it.
- No live calls. No personal data is needed or kept: only the aggregate table is committed.

## Part 3. Retell adapter against the public specification

- Sources: Retell public docs pages (webhook overview, secure webhook, post-call analysis, get-call reference with the `DisconnectionReason` enum) and the signature code and tests in Retell's open-source Python and TypeScript SDK repositories, pinned by commit. No Retell account, key or call log is used.
- Checks, each with a pass/fail line:
  1. The SDKs' published test vector (body, key, timestamp, digest) verifies in the lab's `verify_signature`.
  2. Every malformed-signature case in both SDK test files is rejected by the lab; where the two SDKs disagree with each other (upper-case hex) the lab is compared with each and the difference is reported.
  3. The five-minute tolerance, both directions, boundary inclusive, matches the SDK tests.
  4. Extra cases of mine (leading zeros in the timestamp, leading and trailing spaces, non-ASCII body, digest length) are run against a reference implementation I write from the documented algorithm; lab and reference must agree, and any lab acceptance that both SDKs would reject is listed.
  5. The documented sample payloads (`call_ended`, `call_analyzed`) are made valid JSON (the docs show `[Object]` placeholders) and signed with a test key by my reference signer; the lab's `handle_webhook` is run on them, as published (no `case_id` in metadata) and with the lab's required metadata added.
  6. All values of the documented `DisconnectionReason` enum are put through the adapter: the table of outcomes, and a check that no key in the lab's mapping is absent from the enum (a typo check) and which enum values fall to the analysis path.
  7. Duplicate delivery (the docs say up to 3 retries) applies the result once.
- "Confirmed" means the lab matches the published algorithm, tests and payload shape on this date. It does not mean the adapter has run against Retell. That stays unverified.

## Bug rule
If anything in `src/` is found to be wrong: first a failing test, then the fix, then `scripts/mutate.py` extended with a mutant for it, then all gates rerun (unit tests, mutation check, simulation report), and the before and after numbers reported.

## Downloads
Data files only, into a new directory in the session scratchpad, never executed, parsed with `python3 -I`, official sources, one request at a time with a pause. Hashes recorded in `data/SHA256SUMS`.

## Amendments (dated; the text above is unchanged)

**2026-11-03, Part 3 script.** The first run of `scripts/part3_retell.py` (kept as `evidence/part3_retell_run1_before_fix.txt`) reported two FAIL lines that were mistakes in my script, not findings about the adapter: (a) I had coded "lab accepts a header both SDKs reject" as a pass/fail check although this protocol only requires such cases to be listed, so it is now a LISTED line; (b) I had guessed the enum has 40 values; the reference file has 39, so the check now only requires the enum to be found and prints the count. No score or number from the lab changed because of this.

**2026-11-03, Part 1 harness.** After the first scored run I added an optional environment variable `CALLFLOW_SRC` to `part1_windows.py` so the same harness can run against a deliberately broken copy of `src/` (`scripts/part1_controls.py`). It changes nothing when unset.

**2026-11-03, Bug rule triggered by Part 3.** Reading the documented `DisconnectionReason` table showed that `scam_detected` (documented as an error stop: the agent was flagged for suspected abuse) was not treated as a campaign-level stop by the adapter. Handled under the bug rule above; see README, "What went wrong".
