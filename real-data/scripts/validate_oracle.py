"""Pre-scoring check of the independent oracle against zoneinfo. Usage: python3 -I validate_oracle.py <zones.txt>"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tzif_oracle as o  # noqa: E402

LO = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
HI = int(datetime(2028, 1, 1, tzinfo=timezone.utc).timestamp())


def zi_off(zi, t):
    return int(datetime.fromtimestamp(t, zi).utcoffset().total_seconds())


def main(zfile):
    names = [l.split("\t")[0] for l in open(zfile, encoding="utf-8") if l.strip()]
    tot_bad = tot_pbad = tot_tr = tot_trbad = 0
    for n in names:
        z, zi = o.Zone(n), ZoneInfo(n)
        bad = sum(1 for t in range(LO, HI, 3600) if z.utoff(t) != zi_off(zi, t))
        pbad = 0
        if z.posix and z.posix[1] is not None:     # footer rule alone must also reproduce 2025-2027
            pbad = sum(1 for t in range(LO, HI, 3600) if z._posix_off(t) != zi_off(zi, t))
        tr = z.transitions(LO, HI)
        # independent transition list from zoneinfo: hourly scan, then minute refinement
        zt, prev = [], zi_off(zi, LO)
        for t in range(LO + 3600, HI, 3600):
            cur = zi_off(zi, t)
            if cur != prev:
                a, b = t - 3600, t
                while b - a > 1:
                    m = (a + b) // 2
                    if zi_off(zi, m) == prev:
                        a = m
                    else:
                        b = m
                zt.append(b)
            prev = cur
        trbad = 0 if [x[0] for x in tr] == zt else 1
        tot_bad += bad; tot_pbad += pbad; tot_tr += len(tr); tot_trbad += trbad
        print("%-34s footer=%-34s offsets_diff=%d footer_diff=%d transitions=%d list_match=%s" % (n, z.footer, bad, pbad, len(tr), not trbad))
    print("TOTAL zones=%d hourly_offset_differences=%d footer_only_differences=%d transitions=%d zones_with_transition_list_mismatch=%d"
          % (len(names), tot_bad, tot_pbad, tot_tr, tot_trbad))
    with open("/usr/share/zoneinfo/tzdata.zi", encoding="utf-8") as f:
        print("tzdata version line:", f.readline().strip())
    sys.exit(1 if (tot_bad or tot_pbad or tot_trbad) else 0)


if __name__ == "__main__":
    main(sys.argv[1])
