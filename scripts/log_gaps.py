#!/usr/bin/env python3
"""
log_gaps.py -- where a pipeline step spent its time. READ ONLY.

    /srv/venv/bin/python scripts/log_gaps.py logs/<run>/08_golive.log
    /srv/venv/bin/python scripts/log_gaps.py logs/<run>/04_grade_sanity.log logs/<run>/04c_twins.log --top 25

★ WHY (2026-10-08, "speed it up"): run_pipeline stamps every log line with
  the wall clock (_stampTo), so the gap between one line and the next is
  the time the work after that line took -- but a grep for durations only
  finds the few lines that print one. This ranks every gap: the line that
  STARTED each slow stretch, how long until the next line, and the next
  line. A stamp earlier than the one before is taken as past midnight.
"""
import argparse
import re

_STAMP = re.compile(r"^(\d\d):(\d\d):(\d\d) ?(.*)$")


def gaps(path):
    out, prev = [], None
    day = 0
    for line in open(path, errors="replace"):
        m = _STAMP.match(line.rstrip("\n"))
        if not m:
            continue
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if prev is not None and t + day < prev[0]:
            day += 86400
        t += day
        if prev is not None:
            out.append((t - prev[0], prev[1], m[4]))
        prev = (t, m[4])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()
    for path in a.logs:
        g = gaps(path)
        total = sum(x[0] for x in g)
        print(f"\n== {path}: {total:,} s stamped")
        for s, start, nxt in sorted(g, key=lambda x: -x[0])[:a.top]:
            print(f"  {s:>6,} s  {100 * s / max(total, 1):4.1f}%  {start[:90]}")
            print(f"  {'':>15}-> {nxt[:90]}")


if __name__ == "__main__":
    main()
