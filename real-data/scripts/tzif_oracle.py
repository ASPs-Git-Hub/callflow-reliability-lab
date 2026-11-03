"""Independent UTC-offset oracle: reads a zone's TZif file (RFC 8536) directly and applies the POSIX TZ footer rule
for instants after the last stored transition. Does not import zoneinfo. Standard library only."""
import bisect
import os
import re
import struct

TZDIR = "/usr/share/zoneinfo"


def days_from_civil(y, m, d):                 # Howard Hinnant's algorithm; day 0 = 1970-01-01
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return y + (m <= 2), m, d


def year_of(t):
    return civil_from_days(t // 86400)[0]


def _mdays(y, m):
    return [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]


def _hms(s):
    m = re.fullmatch(r"([+-]?)(\d+)(?::(\d+))?(?::(\d+))?", s)
    if not m:
        raise ValueError("bad time " + s)
    v = int(m.group(2)) * 3600 + int(m.group(3) or 0) * 60 + int(m.group(4) or 0)
    return -v if m.group(1) == "-" else v


def _name(s, i):
    if s[i] == "<":
        j = s.index(">", i)
        return s[i + 1:j], j + 1
    j = i
    while j < len(s) and s[j].isalpha():
        j += 1
    return s[i:j], j


def parse_posix(tz):
    """Returns (std_utoff, None) or (std_utoff, dst_utoff, start_rule, end_rule); rule = (m, w, d, secs)."""
    i = 0
    _, i = _name(tz, i)
    m = re.match(r"[+-]?\d+(?::\d+){0,2}", tz[i:])
    std = -_hms(m.group(0))
    i += m.end()
    if i >= len(tz):
        return (std, None)
    _, i = _name(tz, i)
    m = re.match(r"[+-]?\d+(?::\d+){0,2}", tz[i:])
    if m:
        dst = -_hms(m.group(0))
        i += m.end()
    else:
        dst = std + 3600
    rules = []
    for part in tz[i:].lstrip(",").split(","):
        if "/" in part:
            r, tm = part.split("/", 1)
            secs = _hms(tm)
        else:
            r, secs = part, 7200
        mm = re.fullmatch(r"M(\d+)\.(\d+)\.(\d+)", r)
        if not mm:
            raise ValueError("unsupported POSIX rule " + part)
        rules.append((int(mm.group(1)), int(mm.group(2)), int(mm.group(3)), secs))
    return (std, dst, rules[0], rules[1])


def _rule_local_secs(y, rule):
    m, w, d, secs = rule
    first = days_from_civil(y, m, 1)
    wd_first = (first + 4) % 7                      # 0 = Sunday
    day = 1 + (d - wd_first) % 7 + 7 * (w - 1)
    while day > _mdays(y, m):
        day -= 7
    return (days_from_civil(y, m, day)) * 86400 + secs


class Zone:
    def __init__(self, name, tzdir=TZDIR):
        with open(os.path.join(tzdir, name), "rb") as f:
            b = f.read()
        if b[:4] != b"TZif":
            raise ValueError(name + ": not TZif")
        ver = b[4:5]
        off = 0

        def header(o):
            return struct.unpack(">6l", b[o + 20:o + 44])
        isut, isstd, leap, timecnt, typecnt, charcnt = header(0)
        tsz = 4
        if ver >= b"2":
            off = 44 + timecnt * 4 + timecnt + typecnt * 6 + charcnt + leap * 8 + isstd + isut
            isut, isstd, leap, timecnt, typecnt, charcnt = header(off)
            tsz = 8
        p = off + 44
        fmt = ">%d%s" % (timecnt, "q" if tsz == 8 else "l")
        self.trans = list(struct.unpack(fmt, b[p:p + timecnt * tsz]))
        p += timecnt * tsz
        idx = list(b[p:p + timecnt])
        p += timecnt
        types = [struct.unpack(">lBB", b[p + 6 * k:p + 6 * k + 6]) for k in range(typecnt)]
        p += typecnt * 6 + charcnt + leap * (tsz + 4) + isstd + isut
        self.offsets = [types[i][0] for i in idx]
        self.first_off = next((t[0] for t in types if not t[1]), types[0][0])
        self.footer = None
        if ver >= b"2":
            tail = b[p:].split(b"\n")
            self.footer = tail[1].decode() if len(tail) > 1 and tail[1] else None
        self.posix = parse_posix(self.footer) if self.footer else None
        self.name = name

    def _posix_off(self, t):
        pz = self.posix
        if pz[1] is None:
            return pz[0]
        std, dst, sr, er = pz
        y = year_of(t + std)
        events = []
        for yy in (y - 1, y, y + 1):
            events.append((_rule_local_secs(yy, sr) - std, dst))
            events.append((_rule_local_secs(yy, er) - dst, std))
        events.sort()
        cur = std
        for when, off in events:
            if when <= t:
                cur = off
        return cur

    def utoff(self, t):
        if not self.trans or t < self.trans[0]:
            return self.first_off if not self.trans or t < self.trans[0] else None
        if t >= self.trans[-1] and self.posix:
            return self._posix_off(t)
        return self.offsets[bisect.bisect_right(self.trans, t) - 1]

    def local_hour(self, t):
        return ((t + self.utoff(t)) % 86400) // 3600

    def transitions(self, lo, hi):
        """Offset changes in [lo, hi): list of (t, old_off, new_off)."""
        out, ts = [], set(x for x in self.trans if lo <= x < hi)
        if self.posix and self.posix[1] is not None:
            std, dst = self.posix[0], self.posix[1]
            for y in range(year_of(lo) - 1, year_of(hi) + 2):
                for when in (_rule_local_secs(y, self.posix[2]) - std, _rule_local_secs(y, self.posix[3]) - dst):
                    if lo <= when < hi and (not self.trans or when > self.trans[-1]):
                        ts.add(when)
        for t in sorted(ts):
            a, b = self.utoff(t - 1), self.utoff(t)
            if a != b:
                out.append((t, a, b))
        return out
