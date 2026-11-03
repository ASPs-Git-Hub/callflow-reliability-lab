"""Must-fire control for Part 2: removes the attempt limit at both guards in a scratch copy of src/ and requires the
replay to report a mismatch. Usage: python3 -I part2_control.py <scratch dir> <bank-additional-full.csv>"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "src", "callflow")
scratch, csv_path = sys.argv[1], sys.argv[2]
d = os.path.join(scratch, "p2ctl", "callflow")
shutil.rmtree(os.path.dirname(d), ignore_errors=True)
shutil.copytree(SRC, d, ignore=shutil.ignore_patterns("__pycache__"))
p = os.path.join(d, "orchestrator.py")
s = open(p, encoding="utf-8").read()
pairs = [('                if len(c.attempts) >= p.max_attempts:\n                    self._to_person(c, "attempts_exhausted", now)\n                    continue\n', ""),
         ('if len(c.attempts) >= p.max_attempts:\n                self._to_person(c, "attempts_exhausted", now)\n            else:', 'if False:\n                self._to_person(c, "attempts_exhausted", now)\n            else:')]
for o, n in pairs:
    assert s.count(o) == 1
    s = s.replace(o, n)
open(p, "w", encoding="utf-8").write(s)
r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "part2_campaign.py"), csv_path, os.path.join(scratch, "p2ctl", "out")],
                   capture_output=True, text=True, env={**os.environ, "CALLFLOW_SRC": os.path.dirname(d)}, timeout=600)
print("\n".join(l for l in r.stdout.splitlines() if l.startswith("cap ")) or r.stderr[-500:])
