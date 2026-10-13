#!/usr/bin/env python3
"""
page_timing.py -- how long every kind of page takes with NOTHING cached.
Pipeline step 14_page_timing (report only; never fails the run).

    /srv/venv/bin/python scripts/page_timing.py            # print the report
    /srv/venv/bin/python scripts/page_timing.py --write    # ... and page_timing table
    /srv/venv/bin/python scripts/page_timing.py --n 10     # pages per kind

(owner, 2026-10-13: "some are slow, especially when not cached ... it
should be fast with or without a cache".) The live site answers most pages
from its in-process caches, so a page that is slow cold is hidden from
anyone who loads it twice -- and is exactly what a visitor from a search
result gets. This renders pages in a FRESH PROCESS (no in-process cache can
be warm), on ids SAMPLED AT RANDOM (not the popular pages a crawler or a
user already warmed), through Flask's own test client (no network, no
nginx), and times every SQL statement each page runs.

Report: per page kind, p50 / p95 / max milliseconds and queries per page;
the slowest pages with their three slowest statements; every page that
errored. A page over SLOW_MS is the work list.
"""
import argparse
import contextlib
import os
import random
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

SLOW_MS = 1000.0
STATIC = ["/", "/meets", "/rankings", "/schools", "/records", "/predictions",
          "/recruiting", "/what-it-takes", "/conversions", "/about",
          "/search?q=smith"]

_QUERIES = []          # (ms, sql) for the request in flight


class _Cur:
    """A cursor that times execute(); everything else is the real one."""

    def __init__(self, cur):
        self._c = cur

    def execute(self, sql, *a, **k):
        t = time.perf_counter()
        try:
            return self._c.execute(sql, *a, **k)
        finally:
            _QUERIES.append((1000 * (time.perf_counter() - t),
                             " ".join(str(sql if isinstance(sql, str) else
                                          getattr(sql, "as_string", lambda _c: sql)(self._c)).split())[:140]))

    def __enter__(self):
        self._c.__enter__()
        return self

    def __exit__(self, *exc):
        return self._c.__exit__(*exc)

    def __iter__(self):
        return iter(self._c)

    def __getattr__(self, name):
        return getattr(self._c, name)


class _Conn:
    def __init__(self, conn):
        self._c = conn

    def cursor(self, *a, **k):
        return _Cur(self._c.cursor(*a, **k))

    def __getattr__(self, name):
        return getattr(self._c, name)


def _patchConn():
    """Wrap database.getConn BEFORE the site imports it, so every module's
    `from database import getConn` binds the timed one."""
    import database
    orig = database.getConn

    @contextlib.contextmanager
    def timed(*a, **k):
        with orig(*a, **k) as conn:
            yield _Conn(conn)
    database.getConn = timed
    return orig


def samplePaths(cur, n):
    """{kind: [path]} -- random ids, so nothing was warmed by a visitor."""
    out = {"static": list(STATIC)}

    def q(sql):
        try:
            cur.execute(sql)
            return cur.fetchall()
        except Exception:                                   # noqa: BLE001
            cur.connection.rollback()
            return []
    out["athlete"] = [f"/athlete/{p}" for (p,) in q(
        f"SELECT DISTINCT person_id FROM results TABLESAMPLE SYSTEM (0.05) "
        f"WHERE person_id IS NOT NULL AND speed_rating > 0 LIMIT {n}")]
    out["race_xc"] = [f"/race/xc/{m}/{d}" for m, d in q(
        f"SELECT DISTINCT meet_id, div_id FROM results TABLESAMPLE SYSTEM (0.05) "
        f"WHERE source = 'anet' LIMIT {n}")]
    out["meet_xc"] = [f"/meet/xc/{m}" for (m,) in q(
        f"SELECT DISTINCT meet_id FROM results TABLESAMPLE SYSTEM (0.05) "
        f"WHERE source = 'anet' LIMIT {n}")]
    out["race_tf"] = [f"/race/tf/{m}/{e}/{d}" for m, e, d in q(
        f"SELECT DISTINCT meet_id, event_id, div_id FROM results_tf TABLESAMPLE SYSTEM (0.02) "
        f"WHERE source = 'anet' AND event_id IS NOT NULL LIMIT {n}")]
    out["meet_tf"] = [f"/meet/tf/{m}" for (m,) in q(
        f"SELECT DISTINCT meet_id FROM results_tf TABLESAMPLE SYSTEM (0.02) "
        f"WHERE source = 'anet' LIMIT {n}")]
    from urllib.parse import quote
    out["school"] = [f"/school/{quote(s)}" for (s,) in q(
        f"SELECT DISTINCT school FROM athletes TABLESAMPLE SYSTEM (0.5) "
        f"WHERE school IS NOT NULL AND school <> '' LIMIT {n}")]
    out["course"] = [f"/course/{quote(c)}" for (c,) in q(
        f"SELECT course_name FROM course_difficulties WHERE course_name NOT LIKE 'TF:%%' "
        f"ORDER BY random() LIMIT {n}".replace("%%", "%"))]
    return out


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))]


def summarise(rows):
    """{kind: (n, p50, p95, max, mean queries)} from (kind, path, status,
    ms, queries) rows. Pure."""
    out = {}
    for kind in sorted({r[0] for r in rows}):
        ks = [r for r in rows if r[0] == kind]
        ms = [r[3] for r in ks]
        out[kind] = (len(ks), pct(ms, 50), pct(ms, 95), max(ms),
                     sum(len(r[4]) for r in ks) / len(ks))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6, help="pages per kind")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    random.seed(args.seed)
    orig = _patchConn()
    with orig() as conn, conn.cursor() as cur:
        paths = samplePaths(cur, args.n)
        conn.rollback()
    import app as A
    client = A.app.test_client()
    rows = []
    for kind, ps in paths.items():
        for p in ps:
            _QUERIES.clear()
            t = time.perf_counter()
            try:
                status = client.get(p).status_code
            except Exception as exc:                        # noqa: BLE001
                status = f"raised {type(exc).__name__}"
            ms = 1000 * (time.perf_counter() - t)
            rows.append((kind, p, status, ms, list(_QUERIES)))
    summ = summarise(rows)
    print(f"[timing] {len(rows)} pages, fresh process, nothing cached:")
    print(f"    {'kind':<10}{'pages':>6}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}{'queries':>9}")
    for kind, (n, p50, p95, mx, nq) in summ.items():
        print(f"    {kind:<10}{n:>6}{p50:>9.0f}{p95:>9.0f}{mx:>9.0f}{nq:>9.1f}")
    bad = [r for r in rows if r[2] != 200 and r[2] not in (301, 302, 404)]
    if bad:
        print(f"[timing] {len(bad)} pages did not render:")
        for kind, p, status, ms, _q in bad[:20]:
            print(f"    {status}  {p}")
    slow = sorted((r for r in rows if r[3] > SLOW_MS), key=lambda r: -r[3])
    print(f"[timing] {len(slow)} pages over {SLOW_MS:.0f} ms (the work list):")
    for kind, p, status, ms, qs in slow[:15]:
        print(f"    {ms:7.0f} ms  {len(qs):3} queries  {p}")
        for qms, sql in sorted(qs, reverse=True)[:3]:
            print(f"        {qms:7.0f} ms  {sql}")
    if args.write:
        with orig() as conn, conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS page_timing (
                kind text, path text, status text, ms real, n_queries integer,
                slowest_sql text, slowest_ms real, measured_at timestamptz DEFAULT now())""")
            cur.execute("DELETE FROM page_timing")
            for kind, p, status, ms, qs in rows:
                top = max(qs) if qs else (None, None)
                cur.execute("""INSERT INTO page_timing (kind, path, status, ms, n_queries,
                               slowest_sql, slowest_ms) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                            (kind, p, str(status), ms, len(qs), top[1], top[0]))
            conn.commit()
        print(f"[timing] page_timing: {len(rows)} rows")


if __name__ == "__main__":
    main()
