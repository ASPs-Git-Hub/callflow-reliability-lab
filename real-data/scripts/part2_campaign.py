"""Part 2: retry caps on the UCI Bank Marketing campaign. See ../PROTOCOL.md.
Usage: python3 -I part2_campaign.py <path to bank-additional-full.csv> <out data dir>"""
import csv
import hashlib
import math
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("CALLFLOW_SRC") or os.path.join(HERE, "..", "..", "src"))
from callflow.model import Case, Policy  # noqa: E402
from callflow.orchestrator import Orchestrator  # noqa: E402

UTC = timezone.utc


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def bucket(c):
    return str(c) if c <= 6 else ("7-10" if c <= 10 else "11+")


def replay(rows, cap):
    """Returns (total lab calls, rows where lab calls != min(campaign, cap), cases sent to a person for attempts_exhausted)."""
    pol = Policy(max_attempts=cap)
    start = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)           # a Monday
    total = mism = exhausted = 0
    for i, (c, y) in enumerate(rows):
        orch = Orchestrator(pol)
        case = Case("R%d" % i, "351910000000", "Europe/Lisbon", start + timedelta(days=3650))
        orch.add_case(case, start)
        for _ in range(60):
            if case.state != "waiting" or case.next_attempt_at is None:
                break
            now = case.next_attempt_at
            orch.tick(now)
            if case.state == "in_call":
                n = len(case.attempts)
                out = "no_answer" if n < c else ("confirmed" if y else "declined")
                orch.handle_result({"event_id": "e%d" % n, "case_id": case.id, "attempt_no": n, "outcome": out, "confidence": 1.0}, now + timedelta(minutes=2))
        calls = len(orch.calls)
        total += calls
        mism += calls != min(c, cap)
        exhausted += any(t["reason"] == "attempts_exhausted" for t in orch.tickets.tickets.values())
    return total, mism, exhausted


def main(path, outdir):
    raw = open(path, "rb").read()
    print("file sha256:", hashlib.sha256(raw).hexdigest(), "bytes:", len(raw))
    rd = list(csv.DictReader(raw.decode("utf-8").splitlines(), delimiter=";"))
    n = len(rd)
    camp = [int(r["campaign"]) for r in rd]
    ys = [r["y"] == "yes" for r in rd]
    print("rows:", n, "(expected 41188)", "| campaign min/max:", min(camp), max(camp), "| campaign < 1:", sum(c < 1 for c in camp),
          "| y values:", sorted(set(r["y"] for r in rd)), "| exact duplicate rows:", n - len(set(tuple(r.values()) for r in rd)))
    cap = Policy().max_attempts
    print("lab default max_attempts (read from code):", cap)
    rows = list(zip(camp, ys))
    total = sum(camp)
    for label, k in (("PRIMARY cap = lab default", cap), ("informational cap", 3), ("informational cap", 6)):
        over = [(c, y) for c, y in rows if c > k]
        excess = sum(c - k for c, _ in over)
        within = [(c, y) for c, y in rows if c <= k]
        yo, yw = sum(y for _, y in over), sum(y for _, y in within)
        lo_o, hi_o = wilson(yo, len(over)); lo_w, hi_w = wilson(yw, len(within))
        print("\n== %s: cap %d ==" % (label, k))
        print("clients above cap: %d of %d (%.2f%%)" % (len(over), n, 100 * len(over) / n))
        print("total calls in data: %d | calls above cap: %d (%.2f%% of all calls) | calls the cap would allow: %d" % (total, excess, 100 * excess / total, total - excess))
        print("subscribed, within cap: %d of %d = %.2f%% (95%% Wilson %.2f to %.2f)" % (yw, len(within), 100 * yw / len(within), 100 * lo_w, 100 * hi_w))
        print("subscribed, above cap:  %d of %d = %.2f%% (95%% Wilson %.2f to %.2f)" % (yo, len(over), 100 * yo / len(over), 100 * lo_o, 100 * hi_o))
        print("all subscriptions: %d; those from clients above cap: %d (%.2f%%)" % (yo + yw, yo, 100 * yo / (yo + yw)))
        # calls spent per subscription
        print("all calls of the group divided by its subscriptions: within-cap group %.1f | above-cap group %.1f"
              % (sum(c for c, _ in within) / yw, (sum(c for c, _ in over) / yo) if yo else float("nan")))
    order = ["1", "2", "3", "4", "5", "6", "7-10", "11+"]
    tab = {b: [0, 0, 0] for b in order}
    for c, y in rows:
        t = tab[bucket(c)]; t[0] += 1; t[1] += y; t[2] += c
    print("\nbucket (contacts per client): clients, share, subscribed, rate, 95% Wilson, calls")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "part2_by_bucket.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["campaign_bucket", "clients", "share_of_clients", "subscribed", "rate", "wilson_lo", "wilson_hi", "calls"])
        for b in order:
            k, yv, cl = tab[b]; lo, hi = wilson(yv, k)
            print("%-5s %6d %6.2f%% %5d %6.2f%% (%.2f to %.2f) %6d" % (b, k, 100 * k / n, yv, 100 * yv / k, 100 * lo, 100 * hi, cl))
            w.writerow([b, k, round(k / n, 5), yv, round(yv / k, 5), round(lo, 5), round(hi, 5), cl])
    hist = {}
    for c, y in rows:
        h = hist.setdefault(c, [0, 0]); h[0] += 1; h[1] += y
    with open(os.path.join(outdir, "part2_campaign_histogram.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["campaign", "clients", "subscribed"])
        for c in sorted(hist): w.writerow([c, hist[c][0], hist[c][1]])
    print("\n== replay through the lab ==")
    for k in (cap, 3, 6):
        tl, mism, exh = replay(rows, k)
        exp = sum(min(c, k) for c, _ in rows)
        print("cap %d: lab placed %d calls | expected sum(min(campaign, cap)) = %d | rows where they differ: %d | cases sent to a person for attempts_exhausted: %d | clients above cap: %d"
              % (k, tl, exp, mism, exh, sum(c > k for c, _ in rows)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
