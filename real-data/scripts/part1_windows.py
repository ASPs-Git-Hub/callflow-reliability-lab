"""Part 1: calling windows on real time-zone rules. See ../PROTOCOL.md. Usage: python3 -I part1_windows.py <data dir> <out csv dir>
Run from anywhere; imports the lab from ../../src."""
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("CALLFLOW_SRC") or os.path.join(HERE, "..", "..", "src"))
import tzif_oracle as o  # noqa: E402
from callflow.model import Case, Policy  # noqa: E402
from callflow.naive import NaiveOrchestrator  # noqa: E402
from callflow.orchestrator import Orchestrator  # noqa: E402
from callflow.schedule import in_window, next_allowed  # noqa: E402

UTC = timezone.utc
LO = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
HI = int(datetime(2028, 1, 1, tzinfo=UTC).timestamp())
SPAN, EXT = 2880, 2880          # minutes before/after the anchor; extension for next-allowed lookups
START, END = 8, 21
POLICY = Policy(window_start_hour=START, window_end_hour=END, allowed_weekdays=frozenset(range(7)))
APPT = datetime(2030, 1, 1, tzinfo=UTC)


def dt(t):
    return datetime.fromtimestamp(t, UTC)


def date_of(z, t):
    y, m, d = o.civil_from_days((t + z.utoff(t)) // 86400)
    return "%04d-%02d-%02d" % (y, m, d)


def allowed_range(z, base, n, s=START, e=END):
    return [s <= z.local_hour(base + 60 * i) < e for i in range(n)]


def next_array(al):
    nxt, cur = [None] * len(al), None
    for i in range(len(al) - 1, -1, -1):
        if al[i]:
            cur = i
        nxt[i] = cur
    return nxt


def lab_probe(zone, t):
    """Returns (dispatch_allowed, scheduled_ts). Fresh orchestrator per instant."""
    calls = []
    orch = Orchestrator(POLICY, dialer=lambda r: calls.append(r))
    c = Case("C", "2025550100", zone, APPT)
    now = dt(t)
    orch.add_case(c, now)
    sched = c.next_attempt_at
    c.next_attempt_at = now                      # stale schedule: due now, whatever the window says
    orch.tick(now)
    return (len(calls) == 1), (None if sched is None else int(sched.timestamp()))


def naive_probe(zone, t):
    calls = []
    orch = NaiveOrchestrator(POLICY, dialer=lambda r: calls.append(r))
    c = Case("C", "2025550100", zone, APPT)
    now = dt(t)
    orch.add_case(c, now)
    orch.tick(now)
    return len(calls) == 1


def gap_text(t, old, new):
    if new > old:
        a, b, kind = t + old, t + new, "gap (skipped local times)"
    else:
        a, b, kind = t + new, t + old, "overlap (repeated local times)"
    mins = [((a + 60 * i) % 86400) // 60 for i in range((b - a) // 60)]
    touches = any(START * 60 <= m < END * 60 for m in mins)
    f = lambda s: "%02d:%02d" % ((s % 86400) // 3600, (s % 3600) // 60)
    return kind, "%s to %s" % (f(a), f(b)), touches


def main(datadir, outdir):
    names = [l.split("\t")[0] for l in open(os.path.join(datadir, "zones.txt"), encoding="utf-8") if l.strip()]
    kinds = {l.split("\t")[0]: l.split("\t")[1].strip() for l in open(os.path.join(datadir, "zones.txt"), encoding="utf-8") if l.strip()}
    ny = o.Zone("America/New_York")
    controls = [t for t, _, _ in ny.transitions(LO, HI)]
    t0 = time.time()
    zsum, byday, trrows, stress_rows = [], {}, [], []
    tot = dict(minutes=0, orc_allowed=0, lab_unsafe=0, lab_refused=0, sch_unsafe=0, sch_late_inst=0, sch_late_min=0, sch_none=0,
               naive_unsafe=0, utc_unsafe=0, utc_refused=0, st_in_unsafe=0, st_in_refused=0, st_nx_unsafe=0, st_nx_late=0, st_minutes=0)
    for name in names:
        z = o.Zone(name)
        trs = z.transitions(LO, HI)
        anchors = [t for t, _, _ in trs] or controls
        label = "transition" if trs else "control"
        zt = {k: 0 for k in tot}
        for anchor in anchors:
            base = anchor - SPAN * 60
            al = allowed_range(z, base, 2 * SPAN + EXT)
            nx = next_array(al)
            for i in range(2 * SPAN):
                t = base + 60 * i
                orc = al[i]
                day = byday.setdefault((name, date_of(z, t)), dict(minutes=0, orc_allowed=0, lab_unsafe=0, lab_refused=0, sch_unsafe=0, sch_late=0, naive_unsafe=0, utc_unsafe=0, utc_refused=0))
                lab, sched = lab_probe(name, t)
                nv = naive_probe(name, t)
                ub = in_window(dt(t), "UTC", POLICY)
                zt["minutes"] += 1; day["minutes"] += 1
                if orc:
                    zt["orc_allowed"] += 1; day["orc_allowed"] += 1
                if lab and not orc: zt["lab_unsafe"] += 1; day["lab_unsafe"] += 1
                if orc and not lab: zt["lab_refused"] += 1; day["lab_refused"] += 1
                if nv and not orc: zt["naive_unsafe"] += 1; day["naive_unsafe"] += 1
                if ub and not orc: zt["utc_unsafe"] += 1; day["utc_unsafe"] += 1
                if orc and not ub: zt["utc_refused"] += 1; day["utc_refused"] += 1
                exp = base + 60 * nx[i]
                if sched is None:
                    zt["sch_none"] += 1
                elif sched != exp:
                    if not (START <= z.local_hour(sched) < END):
                        zt["sch_unsafe"] += 1; day["sch_unsafe"] += 1
                    else:
                        zt["sch_late_inst"] += 1; day["sch_late"] += 1; zt["sch_late_min"] += max(0, (sched - exp) // 60)
        # transitions table and stress variant
        for t, old, new in trs:
            kind, interval, touches = gap_text(t, old, new)
            trrows.append((name, dt(t).strftime("%Y-%m-%d %H:%MZ"), old, new, kind, interval, touches))
            h = ((t + (old if new > old else new)) % 86400) // 3600
            s, e = max(h - 1, 0), min(h + 2, 24)
            pol = Policy(window_start_hour=s, window_end_hour=e, allowed_weekdays=frozenset(range(7)))
            base = t - SPAN * 60
            al = allowed_range(z, base, 2 * SPAN + EXT, s, e)
            nx = next_array(al)
            c = dict(in_unsafe=0, in_refused=0, nx_unsafe=0, nx_late=0, nx_err=0)
            for i in range(2 * SPAN):
                tt = base + 60 * i
                got = in_window(dt(tt), name, pol)
                if got and not al[i]: c["in_unsafe"] += 1
                if al[i] and not got: c["in_refused"] += 1
                try:
                    r = next_allowed(dt(tt), name, pol)
                except Exception as ex:                      # noqa: BLE001
                    c["nx_err"] += 1
                    continue
                r = None if r is None else int(r.timestamp())
                exp = None if nx[i] is None else base + 60 * nx[i]
                if r != exp:
                    if r is None or not (s <= z.local_hour(r) < e): c["nx_unsafe"] += 1
                    else: c["nx_late"] += 1
            zt["st_minutes"] += 2 * SPAN
            zt["st_in_unsafe"] += c["in_unsafe"]; zt["st_in_refused"] += c["in_refused"]
            zt["st_nx_unsafe"] += c["nx_unsafe"]; zt["st_nx_late"] += c["nx_late"]
            stress_rows.append((name, dt(t).strftime("%Y-%m-%d %H:%MZ"), kind, "%02d:00-%02d:00" % (s, e), c["in_unsafe"], c["in_refused"], c["nx_unsafe"], c["nx_late"], c["nx_err"]))
        for k in tot: tot[k] += zt[k]
        zsum.append((name, kinds[name], label, len(trs), len(anchors), zt))
        print("%-32s %-10s %-10s transitions=%d windows=%d minutes=%d oracle_allowed=%d | lab: unsafe=%d refused=%d sched_unsafe=%d sched_late=%d | naive_unsafe=%d | utc_unsafe=%d utc_refused=%d | stress in_unsafe=%d in_refused=%d nx_unsafe=%d nx_late=%d"
              % (name, kinds[name], label, len(trs), len(anchors), zt["minutes"], zt["orc_allowed"], zt["lab_unsafe"], zt["lab_refused"], zt["sch_unsafe"], zt["sch_late_inst"], zt["naive_unsafe"], zt["utc_unsafe"], zt["utc_refused"], zt["st_in_unsafe"], zt["st_in_refused"], zt["st_nx_unsafe"], zt["st_nx_late"]), flush=True)
    print("\nTOTAL", tot)
    print("elapsed seconds: %.1f" % (time.time() - t0))
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "part1_zone_summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        keys = ["minutes", "orc_allowed", "lab_unsafe", "lab_refused", "sch_unsafe", "sch_late_inst", "sch_late_min", "sch_none", "naive_unsafe", "utc_unsafe", "utc_refused", "st_minutes", "st_in_unsafe", "st_in_refused", "st_nx_unsafe", "st_nx_late"]
        w.writerow(["zone", "source", "kind", "transitions", "windows"] + keys)
        for n, k, lab_, nt, na, zt in zsum: w.writerow([n, k, lab_, nt, na] + [zt[x] for x in keys])
    with open(os.path.join(outdir, "part1_violations_by_zone_day.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone", "local_date", "minutes_tested", "oracle_allowed", "lab_dispatch_unsafe", "lab_dispatch_refused", "lab_sched_unsafe", "lab_sched_late", "naive_unsafe", "utc_baseline_unsafe", "utc_baseline_refused"])
        for (n, d), v in sorted(byday.items()):
            w.writerow([n, d, v["minutes"], v["orc_allowed"], v["lab_unsafe"], v["lab_refused"], v["sch_unsafe"], v["sch_late"], v["naive_unsafe"], v["utc_unsafe"], v["utc_refused"]])
    with open(os.path.join(outdir, "part1_transitions.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone", "transition_utc", "old_offset_s", "new_offset_s", "kind", "local_interval", "touches_08_21_window"])
        w.writerows(trrows)
    with open(os.path.join(outdir, "part1_stress.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone", "transition_utc", "kind", "stress_window", "in_window_unsafe", "in_window_refused", "next_allowed_unsafe", "next_allowed_late", "next_allowed_exceptions"])
        w.writerows(stress_rows)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
