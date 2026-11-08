#!/usr/bin/env python3
"""
latency_report.py -- p50 / p90 / p99 / max of real requests, overall and by
page kind, from the timing log deploy/nginx_timing_log.sh installs.

    python3 scripts/latency_report.py                     # last 24 h
    python3 scripts/latency_report.py --hours 1 --top 25
    python3 scripts/latency_report.py --log /var/log/nginx/racecast_timing.log --origin

--origin counts only requests gunicorn answered ($upstream_response_time
set), i.e. what the server itself costs; without it every request nginx
logged, static files included. Read-only; no database.
"""
import argparse
import datetime as dt
import re
import sys

_LINE = re.compile(r'^(\S+) (\d{3}) ([\d.]+) (\S+) (\d+) "(\S+) (\S+)"')
_NUM = re.compile(r"/\d+")


def kind(uri):
    path = uri.split("?", 1)[0]
    if path.startswith("/static/") or path.startswith("/img/"):
        return path.split("/", 2)[1] and "/" + path.split("/")[1] + "/*"
    path = _NUM.sub("/:n", path)
    parts = path.split("/")
    # /athlete/:n, /school/<name> -> /school/:name, /race/xc/:n/:n
    if len(parts) > 2 and parts[1] in ("school", "course", "unit", "venue"):
        path = "/".join(parts[:2] + [":name"] + [p for p in parts[3:] if p])
    return path


def pct(xs, p):
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/var/log/nginx/racecast_timing.log")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--origin", action="store_true")
    a = ap.parse_args(argv)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=a.hours)
    by, all_t, n_err = {}, [], 0
    with open(a.log, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _LINE.match(line)
            if not m:
                continue
            ts, status, rt, up, _b, method, uri = m.groups()
            try:
                if dt.datetime.fromisoformat(ts) < since:
                    continue
            except ValueError:
                continue
            if a.origin and up in ("-", ""):
                continue
            t = float(rt)
            if status.startswith("5"):
                n_err += 1
            all_t.append(t)
            by.setdefault(kind(uri), []).append(t)
    if not all_t:
        print("no requests in the window")
        return 1
    all_t.sort()
    print(f"{len(all_t):,} requests, {n_err} 5xx  |  p50 {pct(all_t,.5)*1000:.0f} ms  "
          f"p90 {pct(all_t,.9)*1000:.0f} ms  p99 {pct(all_t,.99)*1000:.0f} ms  "
          f"max {all_t[-1]*1000:.0f} ms")
    rows = []
    for k, v in by.items():
        v.sort()
        rows.append((sum(v), k, v))
    print(f"\n{'kind':40s} {'n':>7s} {'p50':>7s} {'p90':>7s} {'p99':>7s} {'max':>7s} {'total s':>8s}")
    for tot, k, v in sorted(rows, reverse=True)[:a.top]:
        print(f"{k[:40]:40s} {len(v):7d} {pct(v,.5)*1000:6.0f}m {pct(v,.9)*1000:6.0f}m "
              f"{pct(v,.99)*1000:6.0f}m {v[-1]*1000:6.0f}m {tot:8.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
