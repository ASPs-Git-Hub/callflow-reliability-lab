"""Must-fire controls for Part 1: plants one defect at a time in a scratch copy of src/, runs the harness on it, and
requires the harness to report non-zero violations. Usage: python3 -I part1_controls.py <scratch dir>"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "src")
DATA = os.path.join(HERE, "..", "data")
MUT = [
    ("customer time zone ignored in scheduling", "schedule.py", "start = datetime(d.year, d.month, d.day, policy.window_start_hour, tzinfo=z)", "start = datetime(d.year, d.month, d.day, policy.window_start_hour, tzinfo=UTC)"),
    ("window end made inclusive", "schedule.py", "policy.window_start_hour <= l.hour < policy.window_end_hour", "policy.window_start_hour <= l.hour <= policy.window_end_hour"),
    ("dispatch-time window check removed", "orchestrator.py", "if not in_window(now, c.tz, p):", "if False:"),
    ("window evaluated on the fixed standard-time offset (DST ignored)", "schedule.py", "l = dt_utc.astimezone(ZoneInfo(tz))\n    return l.weekday()", None),
]


def main(scratch):
    for i, (desc, fname, old, new) in enumerate(MUT, 1):
        if new is None:                                  # custom: in_window uses the January offset all year
            old = "def in_window(dt_utc, tz, policy):\n    l = local(dt_utc, tz)"
            new = "def in_window(dt_utc, tz, policy):\n    l = dt_utc.astimezone(timezone(ZoneInfo(tz).utcoffset(datetime(2025, 1, 15))))"
        d = os.path.join(scratch, "ctl%d" % i, "callflow")
        shutil.rmtree(os.path.dirname(d), ignore_errors=True)
        shutil.copytree(os.path.join(SRC, "callflow"), d, ignore=shutil.ignore_patterns("__pycache__"))
        p = os.path.join(d, fname)
        s = open(p, encoding="utf-8").read()
        assert s.count(old) == 1, (desc, "pattern does not match once")
        s = s.replace(old, new)
        if "timezone(" in new:
            s = s.replace("from datetime import datetime, timedelta", "from datetime import datetime, timedelta, timezone")
        open(p, "w", encoding="utf-8").write(s)
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "part1_windows.py"), DATA, os.path.join(scratch, "ctl%d" % i, "out")],
                           capture_output=True, text=True, env={**os.environ, "CALLFLOW_SRC": os.path.dirname(d)}, timeout=600)
        line = [l for l in r.stdout.splitlines() if l.startswith("TOTAL")]
        print("CONTROL %d: %s\n  %s" % (i, desc, line[0] if line else ("NO TOTAL; stderr tail: " + r.stderr[-400:])))


if __name__ == "__main__":
    main(sys.argv[1])
