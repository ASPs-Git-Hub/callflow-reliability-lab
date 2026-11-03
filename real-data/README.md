# CallFlow: checks against real public data

Status: BUILT_UNVERIFIED for the lab as a whole, as before. This folder adds three checks that use real public data instead of made-up data. I ran them myself on 2026-11-03, with Python 3.14 and only the standard library. No other person has reviewed them. No real call was made. Nothing here touched a Retell account.

The lab's tests so far used invented data (a fictional plumbing company). That leaves a fair question: does it still work on the real world? The three checks below try to answer that in three narrow ways.

## Words used here
- **Time zone.** A region that uses one clock. "9 am" means a different moment in New York and in Los Angeles.
- **IANA tz database.** The public list of every time zone's rules, including every clock change in history and the planned ones. Computers use it. Python reads it through `zoneinfo`.
- **DST (daylight-saving time).** The twice-a-year clock change. In spring, the clock jumps forward one hour, so one hour of local times never happens (a gap). In autumn, it goes back one hour, so one hour of local times happens twice (an overlap).
- **Calling window.** The hours when the lab may place a call, measured on the customer's own wall clock.
- **Retry cap.** The most times the lab will call one person. The lab's default is 4. The fifth call is refused and a human gets the case.
- **Webhook.** A message that Retell's servers send to your server when something happens on a call.
- **Signature.** A code attached to the message. Only someone who knows the shared secret key can produce it, so it proves who sent the message.
- **Oracle.** A second, separate way to work out the right answer, used to check the first.

## Result in one table

| Check | Real data | What was asked | Result |
|---|---|---|---|
| 1. Calling windows | US and Canada phone area codes (Apache-2.0) and the system IANA tz database | Does the lab ever call outside 08:00 to 21:00 local time, or refuse a valid minute, around clock changes? | 1,641,600 minutes tested in 49 zones. Lab: 0 calls outside the window, 0 valid minutes refused. The lab's chosen next-call time was right every time. Naive version: 752,400 minutes outside the window. |
| 2. Retry cap | A real bank phone campaign, 41,188 contacts (CC BY 4.0) | How much of that campaign would a cap of 4 have stopped? | 4,984 clients (12.10%) were contacted more than 4 times. 20,409 of 105,754 calls (19.30%) were calls number 5 or later. The lab, replayed on every row, placed exactly the allowed number of calls on all 41,188 rows. |
| 3. Retell adapter | Retell's public docs and open-source SDK tests | Does the adapter check signatures and read payloads the way Retell documents it? | 28 checks, 0 failed. The SDK's published test vector verifies. Four kinds of difference from the SDKs are listed below. One real gap was found and fixed (`scam_detected`). |

## Part 1. Calling windows on real time-zone rules

### What I ran
1. I took Google's libphonenumber time-zone map (Apache-2.0). It lists, for each North American area code, which time zones it falls in. That gives 442 area codes (358 US, 56 Canadian, 6 US territories, 22 Caribbean) and 42 distinct zones. I added 7 zones that the map does not list, to cover Indiana and Kentucky sub-zones, Detroit, and Yukon and Nunavut places (`America/Indiana/Indianapolis`, `America/Indiana/Knox`, `America/Kentucky/Louisville`, `America/Detroit`, `America/Whitehorse`, `America/Iqaluit`, `America/Moncton`). That is 49 zones. The 7 are marked "supplement" in the tables.
2. The policy: a call is allowed when the local clock reads 08:00 up to, but not including, 21:00, on any day. I picked this window because it is a common one in US phone rules. The lab does not model any law. `PROTOCOL.md` has the exact rules and was written before the first scored run.
3. I wrote a small reader for the time-zone files themselves (`scripts/tzif_oracle.py`). It does not use Python's `zoneinfo`. It gives the "right answer" for any moment. Before scoring I checked it against `zoneinfo` for every hour from 2025 to 2027 in all 49 zones: 0 differences. It also found the same 135 clock changes that `zoneinfo` shows.
4. For every clock change from 2025 to 2027, I took every minute from 48 hours before to 48 hours after (5,760 minutes). For the 25 zones that never change their clock (Arizona's Phoenix, Regina, Honolulu, Puerto Rico and others) I used the same 96-hour windows around New York's clock changes, as controls. St. John's, with its half-hour offset, is in the set.
5. At each minute I asked the lab's `Orchestrator` two things. First: "if a call is due right now, do you dial?" Second: "when will you schedule the next call?" I compared both with the oracle. I also ran the lab's naive version, and a second baseline that I made myself, which checks the window on the UTC clock (the naive file's header says that is what it does, but its code has no window at all).
6. Stress test, kept apart from the main result: for each of the 135 clock changes I used a window that sits on top of the skipped or repeated hour (for example 01:00 to 04:00), and called the lab's scheduling functions directly. The lab's own `validate()` would reject such windows. This only shows what the functions do.

### Numbers
Counted in whole minutes. "Outside" means the system said call and the oracle said the local time was outside 08:00 to 21:00 (the worst kind of error). "Refused" means the system said no for a minute that was inside the window.

| System | Minutes tested | Outside the window | Valid minutes refused |
|---|---|---|---|
| Lab, dispatch (call due now) | 1,641,600 | **0** | **0** |
| Lab, scheduling (time chosen for next call) | 1,641,600 | **0** | **0** (and 0 times it returned no time at all) |
| Naive orchestrator | 1,641,600 | 752,400 (45.8%) | 0 |
| UTC-clock baseline (mine) | 1,641,600 | 375,480 | 375,480 |
| Lab, stress windows on skipped and repeated hours | 777,600 | 0 | 0 |

The oracle says 889,200 of the 1,641,600 minutes are inside the window (54.2%).

The clock changes themselves: 135 changes in 24 zones, 69 spring gaps (all 02:00 to 03:00 local) and 66 autumn overlaps (all 01:00 to 02:00 local). None of them touches 08:00 to 21:00, which is why the main policy shows no problem there. `data/part1_transitions.csv` has every one.

Spread over zones (full table in `data/part1_zone_summary.csv`): the naive version's minutes outside the window per zone run from 7,920 (Edmonton and Vancouver, 17,280 minutes tested each) to 15,840 (Adak, Anchorage and others, 34,560 tested each). The UTC baseline is wrong for the most minutes in Pacific/Pago_Pago (15,840 outside and 15,840 refused). Worst single local day for the naive version: Adak on 2025-11-02, 720 of 1,500 minutes tested were outside the window. `data/part1_violations_by_zone_day.csv` has all 1,413 zone-days (the first and last day of each window are partial days). The lab has 0 on every zone-day.

### Does this check have teeth?
A harness that always says "zero" proves nothing. So I planted four defects, one at a time, in a scratch copy of the lab and ran the same harness (`scripts/part1_controls.py`, output in `evidence/part1_controls.txt`):

| Planted defect | What the harness reported |
|---|---|
| Time zone ignored when scheduling | 720,720 scheduled times outside the window |
| Window end made inclusive (21:00 allowed) | 68,400 calls outside the window |
| Dispatch-time window check removed | 752,400 calls outside the window |
| Clock change ignored (winter offset all year) | 16,200 outside and 16,200 refused |

All four were caught.

### What went wrong, and what this does not show
- The result is zero because the lab asks `zoneinfo` for local time and my oracle asks the same time-zone files. If those files are wrong, both are wrong in the same way. The check finds mistakes in the lab's code, not mistakes in the rules.
- The tz data on this machine is version `2026f-rearguard`. It shows Edmonton, Vancouver and Winnipeg making only three changes, with the last one in March 2026 and none after. That means those places stay on one clock from then on, as this data says. I did not check that against any news source or the official IANA release.
- The main window never meets a clock change, because changes happen at night. The lab refuses windows that start before 06:00 anyway. That is why the result is clean, and it is also why this part of the lab was never likely to fail. The stress test is the part that looks at the awkward hours, and it was clean too.
- A day with a clock change in zones near midnight, or a zone whose change happens during the day, would be a harder test. None in this set does.
- The 08:00 to 21:00 window is my stated policy. Real rules differ by state, province and call type. A person who knows the rules must set them.
- A phone area code does not tell you where the person is now. Mobile numbers move. The lab takes the zone from the case record.

## Part 2. Retry caps on a real call campaign

### Data
UCI Machine Learning Repository, "Bank Marketing" (dataset 222). It records a real telephone sales campaign by a Portuguese bank, 2008 to 2010, 41,188 rows in the file I used. For each row it gives the number of contacts made in the campaign (`campaign`, including the last one) and whether the client subscribed to a term deposit (`y`). Licence: CC BY 4.0, confirmed on the official page. The file has no client id, so one row is called one client here. 12 rows are exact duplicates of other rows and I left them in.

### What I ran
Counted how many clients got more calls than the lab's default cap (4, read from the code at run time). Then replayed every row through the lab: one case per row, the lab dials, the first `campaign - 1` calls are reported as "no answer", and the last as "confirmed" or "declined" from `y`. The data does not say how the earlier calls ended, so "no answer" is my assumption. The lab should place `min(campaign, 4)` calls for each row.

### Numbers (cap of 4; `evidence/part2_campaign.txt`)

| Measure | Value |
|---|---|
| Clients | 41,188 |
| Total calls in the data | 105,754 |
| Clients called more than 4 times | 4,984 (12.10%) |
| Calls number 5 and later (the calls a cap of 4 would stop) | 20,409 (19.30% of all calls) |
| Calls the lab placed in the replay | 85,345 (exactly 105,754 minus 20,409) |
| Rows where the lab's call count differed from the cap's arithmetic | 0 |
| Subscribed, clients within the cap | 4,334 of 36,204 = 11.97% (95% interval 11.64 to 12.31) |
| Subscribed, clients above the cap | 306 of 4,984 = 6.14% (95% interval 5.51 to 6.84) |
| All subscriptions that came from clients above the cap | 306 of 4,640 (6.59%) |

By number of contacts (`data/part2_by_bucket.csv`):

| Contacts per client | Clients | Share | Subscribed | Rate |
|---|---|---|---|---|
| 1 | 17,642 | 42.83% | 2,300 | 13.04% |
| 2 | 10,570 | 25.66% | 1,211 | 11.46% |
| 3 | 5,341 | 12.97% | 574 | 10.75% |
| 4 | 2,651 | 6.44% | 249 | 9.39% |
| 5 | 1,599 | 3.88% | 120 | 7.50% |
| 6 | 979 | 2.38% | 75 | 7.66% |
| 7 to 10 | 1,537 | 3.73% | 84 | 5.47% |
| 11 or more | 869 | 2.11% | 27 | 3.11% |

For comparison only, with other caps (not the lab's default): a cap of 3 would stop 28,044 calls (26.52%) and a cap of 6 would stop 12,040 (11.38%). The lab placed the matching number of calls in each replay, again with 0 differing rows.

Must-fire control: with the attempt limit removed from a scratch copy of the lab, the replay placed all 105,754 calls and reported 4,984 differing rows (`evidence/part2_control.txt`).

### What this shows and what it does not
- Real campaigns do call people many times. Here 1 client in 8 got more than four calls and one call in five was call number five or later. The lab's cap would have stopped those, and sends each such case to a person instead.
- The people above the cap subscribed less often (6.14% against 11.97%). That is a description of this file. It does not say that more calls cause fewer sales, or that stopping at four would have lost or kept any sale. Who gets called many times is not random: people who do not pick up or keep saying no get called again. The 306 subscriptions above the cap are real, and a cap of 4 would have given up on those clients before they said yes. The file cannot tell whether they would have said yes anyway.
- The replay shows the lab's cap counts correctly on a real spread of attempts, up to 56 contacts for one client. It does not show the right cap value. Four is the lab's default, not a finding.
- The file has no time of day and no time zone, so it says nothing about calling windows. Part 1 does not borrow anything from it.
- One bank, one country, 2008 to 2010. It is not a model of any other business.
- Rows are not known to be distinct people.

## Part 3. The Retell adapter against Retell's public description

### What I ran
`scripts/part3_retell.py` (output: `evidence/part3_retell.txt`). I read Retell's webhook, signature and post-call-analysis pages, the Get Call reference, and the signature code and tests in Retell's Python and TypeScript SDKs (both Apache-2.0). I wrote my own versions of the two SDKs' verify functions from that reading and ran them next to the lab's `verify_signature`. I made the docs' sample payloads valid JSON (the docs show `[Object]` where an inspector shortened them), signed them with a test key using the documented method, and sent them through the lab's `handle_webhook`.

### Confirmed (all run, 28 checks, 0 failed)
- The SDKs' published test vector (body `{"event":"call_ended"}`, key `test-api-key`, timestamp `1700000000000`, digest `07024cab...823e`) verifies in the lab. My own HMAC-SHA256 reproduces the same digest, so the method "HMAC-SHA256 of the raw body followed by the timestamp, keyed with the API key" is what the lab uses.
- All 10 malformed-signature cases from the two SDK test files are rejected.
- The five-minute tolerance matches the SDK tests: accepted at exactly 300,000 ms either side, rejected at 300,001 ms.
- A body changed by even one space, and a wrong key, are rejected. Non-ASCII and empty bodies verify correctly.
- The docs' `call_ended` sample, as published, is rejected by the adapter because its `metadata` is empty and the lab needs a `case_id` and `attempt_no` there. This is intended.
- With the lab's metadata added, a spoken-call `call_ended` is ignored until `call_analyzed` arrives. The docs' `call_analyzed` sample has `custom_analysis_data` with no `outcome` field, and the adapter sends such a call to a person instead of confirming it.
- Docs say a failed webhook is retried up to 3 times. The same signed message delivered 3 times applied once.
- For calls that did not connect, the docs say `call_ended` and `call_analyzed` are both sent. The adapter completes the attempt on the first and treats the second as a duplicate.
- A wrong-key signature and a signature older than five minutes are rejected end to end.
- Every key in the adapter's reason table is a real value from the documented list of 39 `disconnection_reason` values (no typos). The table of what the adapter does with each value is in `evidence/part3_retell.txt`.

### Differences from the SDKs (listed, none changes who can get a message accepted)
| Case | Lab | Python SDK | TypeScript SDK |
|---|---|---|---|
| Upper-case hex digest | accepts | rejects | accepts |
| Spaces or a newline around an otherwise valid header | accepts | rejects | rejects |
| Leading zero in the timestamp, digest over the zero-padded text | accepts | rejects | rejects |
| Leading zero in the timestamp, digest over the plain number | rejects | accepts | accepts |

The two SDKs disagree with each other on upper-case hex. The lab follows the TypeScript one. In every case that needs a valid digest, the sender must know the secret key, so nobody without the key gains anything. Retell does not send these forms. I left the adapter as it is.

### What went wrong
- Real gap found and fixed. The docs list `scam_detected` among the error stops: the agent was flagged for suspected abuse. The adapter handled the three other stops (`budget_reached`, `credit_exhausted`, `no_valid_payment`) by halting all dialing, but `scam_detected` fell through, so other cases would have kept dialing. Following the bug rule I first wrote a failing test (`test_abuse_flag_from_the_provider_halts_dispatch_for_every_case`; before: `('ignored', None) != ('halted', 'scam_detected')`, `evidence/bug1_before_fix.txt`), then added the reason to `ACCOUNT_HALT`, then added a mutant to `scripts/mutate.py`. After: 118 of 118 tests pass, 78 of 78 mutants killed, and the simulation report is identical to the committed one. Before the fix the lab had 117 tests and 77 mutants. The lab's top-level README and `evidence/` files still say 117 and 77 and need a line update.
- My script had two false FAIL lines on its first run: it treated a list-only item as pass or fail, and I had guessed the reason list has 40 values when it has 39. Both are my script's mistakes. The first run's output is kept in `evidence/part3_retell_run1_before_fix.txt` and the change is in `PROTOCOL.md` under "Amendments".

### Still not confirmed
- The adapter has not run on a live Retell account or against live deliveries. Everything above compares it to published text and published tests.
- How a web framework hands over the raw request body, and how the header name is spelled in a real request, are outside the adapter and were not tested. The signature is only valid over the exact raw text, and a framework that re-writes the body would break it.
- Retell's docs change without version numbers. This is what the pages said on 2026-11-03.
- Whether a real analysis result will carry `outcome` and `confidence` depends on the agent set-up, which is in `retell/agent_spec.json` and was not deployed.
- The adapter sends `scam_detected` to a campaign stop. Whether that is the right reaction for every business is a decision for the owner of the campaign.
- Reasons that go through the analysis path (for example `user_hangup`, `inactivity`, `call_transfer`) were not checked against real conversations.

## What this folder does not prove, in short
- Not a test on a live system, a real customer or a real call.
- Part 1 used the same time-zone data as the lab. Part 2 used one old campaign and invented the outcomes of earlier calls. Part 3 compared the adapter with text and test vectors only.
- I wrote the policy, the oracle and the scripts, and an AI model helped. Nobody outside the project has reproduced the numbers.

## Files
- `PROTOCOL.md`: the rules, written first. `SOURCES.md`: where each file came from and its licence.
- `scripts/`: `tzif_oracle.py`, `build_zones.py`, `validate_oracle.py`, `part1_windows.py`, `part1_controls.py`, `part2_campaign.py`, `part2_control.py`, `part3_retell.py`.
- `data/`: the libphonenumber map and its licence, derived tables, per-zone and per-day results, `SHA256SUMS`.
- `evidence/`: raw output of every run, including the failing test before the fix.

To repeat (the raw downloads are not in the repository; see `SOURCES.md` for the URLs and `data/SHA256SUMS`):
```
python3 -I scripts/validate_oracle.py data/zones.txt
python3 -I scripts/part1_windows.py data data
python3 -I scripts/part2_campaign.py <path>/bank-additional-full.csv data
python3 -I scripts/part3_retell.py <folder with the Retell docs files>
```
