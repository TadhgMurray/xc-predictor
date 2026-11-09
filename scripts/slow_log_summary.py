#!/usr/bin/env python3
"""
slow_log_summary.py -- which pages and statements were slow, from the
site's own slow-query log (racecast/slow_queries.log, db_timing.py: every
statement over 250 ms, with the page that ran it).

    /srv/venv/bin/python scripts/slow_log_summary.py              # last 24 h
    /srv/venv/bin/python scripts/slow_log_summary.py --hours 168 --top 25

Prints two tables: by page kind (the path with its ids folded to :n, the
query string dropped) and by statement (its first 90 characters). Count,
total seconds, median, 95th percentile and worst, worst first by total.
Read-only; reads the log file, never the database.

★ WHY (2026-11-06 speed pass): the "Slow request" mails name one request
  at a time; this says which page kinds and which statements cost the most
  across a day, which is the order to fix them in.
"""
import argparse
import datetime as dt
import os
import re
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(_ROOT, "racecast", "slow_queries.log")
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+([\d.]+)ms\s+(\S+)\s+(.*)$")


def pageKind(path):
    """/athlete/123?x=1 -> /athlete/:n ; /school/Calvin -> /school/:name."""
    p = path.split("?", 1)[0]
    p = re.sub(r"/-?\d+(?=/|$|\.)", "/:n", p)
    for prefix in ("/school/", "/course/", "/unit/", "/recruiting/school/"):
        if p.startswith(prefix):
            rest = p[len(prefix):]
            tail = "/" + rest.split("/", 1)[1] if "/" in rest and prefix != "/unit/" else ""
            p = prefix + (":kind/:name" if prefix == "/unit/" else ":name") + tail
            break
    return p


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def summarise(lines, since=None):
    by_page, by_sql = defaultdict(list), defaultdict(list)
    for ln in lines:
        m = _LINE.match(ln.rstrip("\n"))
        if not m:
            continue
        at = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if since and at < since:
            continue
        ms = float(m.group(2))
        by_page[pageKind(m.group(3))].append(ms)
        by_sql[" ".join(m.group(4).split())[:90]].append(ms)
    return by_page, by_sql


def table(groups, top):
    rows = sorted(groups.items(), key=lambda kv: -sum(kv[1]))[:top]
    out = [f"{'n':>6} {'total s':>9} {'p50 s':>7} {'p95 s':>7} {'max s':>7}  what"]
    for k, xs in rows:
        out.append(f"{len(xs):>6} {sum(xs) / 1000:>9.1f} {_pct(xs, .5) / 1000:>7.2f} "
                   f"{_pct(xs, .95) / 1000:>7.2f} {max(xs) / 1000:>7.2f}  {k}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--log", default=LOG)
    ap.add_argument("--worst", type=int, default=0, metavar="N",
                    help="also list the N slowest single statements with their full URL")
    ap.add_argument("--path", default="",
                    help="only URLs starting with this, e.g. /school/ (the tables and --worst)")
    a = ap.parse_args(argv)
    if not os.path.exists(a.log):
        print(f"no log at {a.log}")
        return 1
    since = dt.datetime.now() - dt.timedelta(hours=a.hours)
    with open(a.log, encoding="utf-8", errors="replace") as fh:
        # ! --path NARROWS THE TABLES TOO (2026-11-09: it used to apply to
        #   --worst only, and "--path /school/" printed the whole site)
        lines = fh if not a.path else (
            ln for ln in fh
            if (m := _LINE.match(ln.rstrip("\n"))) and m.group(3).startswith(a.path))
        by_page, by_sql = summarise(lines, since)
    print(f"slow statements (>250 ms) in the last {a.hours:g} h"
          + (f" under {a.path}" if a.path else "") + ", by page kind:")
    print(table(by_page, a.top))
    print("\nby statement:")
    print(table(by_sql, a.top))
    if a.worst:
        with open(a.log, encoding="utf-8", errors="replace") as fh:
            rows = worst(fh, since, a.worst, a.path)
        print(f"\nthe {len(rows)} slowest single statements"
              + (f" under {a.path}" if a.path else "") + ":")
        for ms, at, path, sql in rows:
            print(f"{ms / 1000:8.2f} s  {at}  {path}\n           {sql[:150]}")
    return 0


def worst(lines, since, n, prefix=""):
    """The n slowest log lines since `since` whose URL starts with prefix:
    [(ms, time, full path, sql head)], slowest first."""
    import heapq
    keep = []
    for ln in lines:
        m = _LINE.match(ln.rstrip("\n"))
        if not m or not m.group(3).startswith(prefix):
            continue
        at = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if since and at < since:
            continue
        keep.append((float(m.group(2)), m.group(1), m.group(3), " ".join(m.group(4).split())))
    return heapq.nlargest(n, keep)


if __name__ == "__main__":
    sys.exit(main())
