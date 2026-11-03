# Sources and licences

All files were fetched on 2026-11-03, one request at a time with a pause between requests, into a scratch folder outside the repository. They were parsed with `python3 -I` and never executed. SHA-256 hashes are in `data/SHA256SUMS`. Nothing here needed an account, a key or a login.

| What | Where from | Licence | Used for | Committed here |
|---|---|---|---|---|
| Time-zone map for phone prefixes (`resources/timezones/map_data.txt`) | https://github.com/google/libphonenumber (master; latest commit touching this file: `d1457634c3d95e570dad433ee0ab400beb343a56`, 2026-10-07) | Apache-2.0 (GitHub licence API says "Apache License 2.0"; the file `data/libphonenumber_LICENSE` is the repository's licence text) | Which zones the North American area codes fall in (Part 1) | Yes, unchanged, 85 KB, with the licence text |
| Place names for phone prefixes (`resources/geocoding/en/1.txt`) | same repository | Apache-2.0 | Labelling area codes as US or Canada (reporting only) | No (700 KB). Only the derived `data/areacode_zones.csv` is kept |
| IANA time-zone database | The copy installed on this Mac, `/usr/share/zoneinfo`, version line `# version 2026f-rearguard` in `tzdata.zi` | Public domain (IANA tz database) | Real daylight-saving rules (Part 1) | No |
| Bank Marketing, UCI dataset id 222 (Moro, Rita, Cortez, 2014; DOI 10.24432/C5K306) | https://archive.ics.uci.edu/static/public/222/bank+marketing.zip (page: https://archive.ics.uci.edu/dataset/222/bank+marketing) | CC BY 4.0, confirmed on the official dataset page on 2026-11-03 | Contact counts and outcomes of a real phone campaign (Part 2). Used file: `bank-additional-full.csv`, 41,188 rows | No raw file. Only two small tables made from it (`data/part2_campaign_histogram.csv`, `data/part2_by_bucket.csv`), which need the credit below |
| Retell public documentation pages: webhook overview, secure the webhook, post-call analysis consumption, Get Call reference, debug call disconnect | https://docs.retellai.com/features/webhook-overview, `/features/secure-webhook`, `/features/post-call-analysis-consumption`, `/api-references/get-call`, `/reliability/debug-call-disconnect` (fetched as the `.md` versions) | Documentation, no licence stated; only facts (field names, the algorithm, example shapes) are used, nothing is republished | Signature method, example payloads, list of disconnection reasons (Part 3) | No |
| Retell Python SDK, `src/retell/lib/webhook_auth.py` and `tests/test_webhook_auth.py`, commit `984a1b0a471c0367f17bc2d86982a9d5e55f4075` | https://github.com/RetellAI/retell-python-sdk | Apache-2.0 (GitHub licence API and the LICENSE file) | Reference for the verify code and the test vector (read, then re-written by me; not run) | No |
| Retell TypeScript SDK, `src/lib/webhook_auth.ts` and `tests/webhook-auth.test.ts`, commit `bd9ac51eb137ea885d9917ac71cd6aaa46b77a14` | https://github.com/RetellAI/retell-typescript-sdk | Apache-2.0 | Same | No |

## Credit (CC BY 4.0)
Moro, S., Rita, P., and Cortez, P. (2014). Bank Marketing [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5K306. The two small tables made from it only count rows by number of contacts and by outcome. No change was made to the data.

## Notes on what the sources are and are not
- I had expected the Retell SDKs to be MIT licensed. Both are Apache-2.0. No SDK code is copied into this repository. The published test vector (a body, a key, a timestamp and a digest) is a set of facts and is written into `scripts/part3_retell.py` as constants.
- The Retell documentation pages are live pages that change without version numbers. The hashes in `data/SHA256SUMS` identify the text I read.
- The libphonenumber file was taken from the master branch, not a release tag. The commit above is the latest one that touched the file.
- The tz database was not downloaded. The check reads whatever this machine has installed, which is the same data the lab's `zoneinfo` calls use.
- The Upwork platform rules in this repository's CLAUDE.md do not apply here: no Upwork data is involved.
