"""
build_school_pages.py -- the big schools' page history, precomputed.

    python racecast/build_school_pages.py                  # every big school, swap
    python racecast/build_school_pages.py --school "Perrysburg"   # in place
    python racecast/build_school_pages.py --dry-run        # compute, write nothing
    python racecast/build_school_pages.py --min-seasons 300 --jobs 4

Run from the PROJECT ROOT. Pipeline step 13f3 / nightly, after the boards and
school_identity (the state chips). Read by school.sharedSchool through
school_pages.storedValue. See school_pages.py for why and for the rules a
stored row must meet to be served.

★ ONE CODE PATH. Each value IS the page's own function (school.py) called
  with the page's own arguments for the bare page -- the state the route
  settles on (its chip logic, below) -- in both sports, stored only when it
  round-trips exactly. A school that fails gets no rows; its page computes
  live, as before.

★ ONLY THE BIG PROGRAMMES (--min-seasons, XCP_SCHOOL_PAGE_MIN_SEASONS,
  default 300 athlete-seasons). The rest are fast live, and computing every
  school nightly would cost more database time than the pages save.

★ SWAP DISCIPLINE as build_wit_marks: built into school_page_new and renamed
  over the live table, so readers see the old rows until the new are whole;
  each school read in its own short transaction.
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                  # noqa: E402

import school_pages as SP                               # noqa: E402

TABLE = SP.TABLE
MIN_SEASONS = int(os.environ.get("XCP_SCHOOL_PAGE_MIN_SEASONS") or 300)
_MIN_SHARE = 0.5

_DDL = """
CREATE TABLE {name} (
    key       text        PRIMARY KEY,   -- school_pages.keyOf
    call      text        NOT NULL,      -- the call, readable
    school    text        NOT NULL,
    payload   text        NOT NULL,      -- school_pages.payloadText
    oids      text        NOT NULL,      -- athlete_season, ranking_results
    code      text        NOT NULL,      -- school_pages.CODE
    built_at  timestamptz NOT NULL,
    n_bytes   int         NOT NULL
)
"""


def bigSchools(cur, min_seasons):
    cur.execute("""SELECT school FROM athlete_season
                   WHERE school IS NOT NULL
                   GROUP BY school HAVING count(*) >= %s
                   ORDER BY count(*) DESC""", (min_seasons,))
    from panels import isTeamName
    return [r[0] for r in cur.fetchall() if isTeamName(r[0])]


def pageCalls(cur, school):
    """[(fn, args, kw)] the bare school page makes, in both sports -- the
    state settled exactly as app.school_page settles it for a bare URL."""
    import school as S
    from school_identity import stateChips, namesakeElsewhere
    chips, primary = stateChips(cur, school, include=None)
    state = None
    if chips:
        state = primary
    if not state and primary and namesakeElsewhere(cur, school, primary):
        state = primary
    calls = [(S.schoolHeader, (school, state, primary), {}),
             (S.schoolYears, (school, state, primary), {})]
    for sport in ("XC", "TF"):
        calls += [(S.currentSeason, (school, sport, state, primary), {}),
                  (S.schoolBest, (school, sport), {"limit": 100, "state": state,
                                                   "primary": primary}),
                  (S.schoolTopAthletes, (school, sport), {"limit": 100, "state": state,
                                                          "primary": primary})]
    return calls


def buildOne(conn, school, oids):
    """[(key, call text, payload, at)] for one school, on its own transaction."""
    import school as S
    out = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for fn, args, kw in pageCalls(cur, school):
                value = S._plain(fn(cur, *args, **kw))
                if not S._keepShared(value):
                    continue               # a degraded answer is computed live
                key, text = SP.keyOf(fn.__name__, args, kw)
                out.append((key, text, SP.payloadText(value), time.time()))
    finally:
        conn.rollback()
    return out


def currentOids(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('athlete_season')::oid, "
                    "to_regclass('ranking_results')::oid")
        a, r = cur.fetchone()
    conn.rollback()
    return (int(a), int(r))


def computeAll(schools, jobs, getConn, oids, log=print):
    rows, failed = {}, []

    def one(name):
        try:
            with getConn() as conn:
                return name, buildOne(conn, name, oids)
        except Exception as exc:                        # noqa: BLE001
            return name, exc

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = [ex.submit(one, s) for s in schools]
        for i, f in enumerate(as_completed(futs), 1):
            name, got = f.result()
            if isinstance(got, Exception):
                failed.append(name)
                log(f"  ! {name}: {type(got).__name__}: {got}")
                continue
            rows[name] = got
            if i % 500 == 0:
                log(f"  {i}/{len(schools)} schools [{time.time() - t0:.0f}s]")
    return rows, failed


def _write(conn, name, rows, oids):
    import psycopg2.extras as E
    vals = [(key, text, school, payload, SP.oidText(oids), SP.CODE, at, len(payload))
            for school, items in rows.items() for key, text, payload, at in items]
    with conn.cursor() as cur:
        E.execute_values(cur, f"""
            INSERT INTO {name} (key, call, school, payload, oids, code, built_at, n_bytes)
            VALUES %s ON CONFLICT (key) DO UPDATE SET
                call = EXCLUDED.call, school = EXCLUDED.school,
                payload = EXCLUDED.payload, oids = EXCLUDED.oids,
                code = EXCLUDED.code, built_at = EXCLUDED.built_at,
                n_bytes = EXCLUDED.n_bytes""",
            vals, template="(%s, %s, %s, %s, %s, %s, to_timestamp(%s), %s)", page_size=500)
    conn.commit()
    return len(vals)


def publish(conn, rows, n_schools, oids, in_place=False):
    from dbfast import swapTable, SWAP_WAIT_S
    if in_place:
        with conn.cursor() as cur:
            cur.execute(f"SELECT to_regclass('{TABLE}')")
            if cur.fetchone()[0] is None:
                cur.execute(_DDL.format(name=TABLE))
        conn.commit()
        n = _write(conn, TABLE, rows, oids)
        print(f"{TABLE}: {n} value(s) written in place", flush=True)
        return
    if len(rows) < _MIN_SHARE * n_schools:
        raise SystemExit(f"only {len(rows)} of {n_schools} schools computed: "
                         f"refusing to swap (the old table stays)")
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = %s", (f"{int(SWAP_WAIT_S * 1000)}ms",))
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}_new")
        cur.execute(_DDL.format(name=f"{TABLE}_new"))
    conn.commit()
    n = _write(conn, f"{TABLE}_new", rows, oids)
    swapTable(conn, TABLE, renames=[(f"{TABLE}_new_pkey", f"{TABLE}_pkey")])
    print(f"{TABLE}: swapped in, {n} values for {len(rows)} schools", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--school", default=None, help="build only this school, in place")
    ap.add_argument("--min-seasons", type=int, default=MIN_SEASONS)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from dbfast import interruptible
    from database import getConn, dbJobs
    interruptible()
    t0 = time.time()
    with getConn() as conn:
        oids = currentOids(conn)
        if args.school:
            schools = [args.school]
        else:
            with conn.cursor() as cur:
                schools = bigSchools(cur, args.min_seasons)
            conn.rollback()
    jobs = dbJobs(args.jobs)
    print(f"computing {len(schools)} school(s) with {args.min_seasons}+ seasons, "
          f"{jobs} at once (code {SP.CODE}, oids {SP.oidText(oids)})", flush=True)
    rows, failed = computeAll(schools, jobs, getConn, oids)
    n_bytes = sum(len(p) for items in rows.values() for _k, _t, p, _a in items)
    print(f"computed {len(rows)}/{len(schools)} schools, {n_bytes:,} bytes, "
          f"{time.time() - t0:.0f} s", flush=True)
    if args.dry_run or not rows:
        return 0 if rows or args.dry_run else 1
    with getConn() as conn:
        if currentOids(conn) != oids:
            sys.exit("the boards were swapped during the build: nothing written "
                     "(the next run builds against the new ones)")
        publish(conn, rows, len(schools), oids, in_place=bool(args.school))
    print(f"done in {(time.time() - t0) / 60:.1f} min", flush=True)
    if failed:
        print(f"  no rows (computed live by the site): {len(failed)} school(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
