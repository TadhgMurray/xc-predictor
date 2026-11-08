"""
build_wit_marks.py -- "What it takes", every state's marks, precomputed.

    python racecast/build_wit_marks.py                   # every state, swap
    python racecast/build_wit_marks.py --state CA,OR     # just these, in place
    python racecast/build_wit_marks.py --dry-run         # compute, write nothing
    python racecast/build_wit_marks.py --jobs 6          # states at once

Run from the PROJECT ROOT. Pipeline step 13f2 (after the boards, the units,
meet_unit and the panels' season year -- every table cuts reads); nightly
after the publish block. Read by cuts.marksFor / cuts.athleteLine.

★ WHY (2026-11-08, the 24 h slow log): the site computed every state's marks
  inside the web workers, in background threads, every 6 h -- the largest
  item in the log (~830 s of database time a day, mainly cuts._finishers'
  unnest query and cuts._seasonRatings), competing with visitors, and every
  state again after each restart. Now once per pipeline / nightly, here.

★ ONE CODE PATH. Each state's payload IS cuts._computeUncached(cur, state),
  the exact function the site falls back to, stored as cuts.payloadText
  (exact JSON: it refuses a payload that would not come back identical,
  types and key order included). built_at is when that state was computed,
  which the page prints as "computed ...". A state that fails or cannot be
  stored gets no row; the site computes it live, as before.

★ SWAP DISCIPLINE as build_venue_tf_boards: built into wit_marks_new and
  renamed over the live table at the end (dbfast.swapTable), so readers see
  the old rows until the new ones are complete. --state writes the named
  states into the live table in place (an owner's one-off).

! SHORT TRANSACTIONS. Each state is read in its own transaction, ended
  before the next, so a concurrent CREATE INDEX CONCURRENTLY never waits on
  this run for more than one state.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2                                         # noqa: E402
import psycopg2.extras                                  # noqa: E402

import cuts                                             # noqa: E402

TABLE = cuts.TABLE

_DDL = """
CREATE TABLE {name} (
    state     text        PRIMARY KEY,
    -- cuts.payloadText(cuts._computeUncached(cur, state))
    payload   text        NOT NULL,
    -- when THIS state was computed (the page's "computed ...")
    built_at  timestamptz NOT NULL,
    -- cuts.CODE: the cuts.py that computed it; the site serves only its own
    code      text        NOT NULL,
    n_bytes   int         NOT NULL
)
"""

_UPSERT = """
    INSERT INTO {name} (state, payload, built_at, code, n_bytes)
    VALUES (%s, %s, to_timestamp(%s), %s, %s)
    ON CONFLICT (state) DO UPDATE SET
        payload = EXCLUDED.payload, built_at = EXCLUDED.built_at,
        code = EXCLUDED.code, n_bytes = EXCLUDED.n_bytes
"""

# ★ A BUILD THAT LOST MOST STATES IS NOT SWAPPED IN: the old table stays and
#   ages out by cuts.MAX_AGE_S, which is the same as not having run.
_MIN_SHARE = 0.5


def allStates():
    from landing import US_STATES
    return list(US_STATES)


def buildOne(conn, state, now=time.time):
    """(state, payload text, computed_at epoch, seconds) -- cuts' own compute,
    on its own transaction (ended before returning)."""
    t0 = time.time()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            data = cuts._computeUncached(cur, state)
        at = now()
    finally:
        conn.rollback()            # read-only: end the transaction
    return state, cuts.payloadText(data), at, time.time() - t0


def computeAll(states, jobs, getConn, log=print):
    """{state: (payload, at)} for every state that computed and stored
    cleanly; failures are logged and left out. `getConn` is a context
    manager factory (database.getConn): a connection per state, so a
    thread's transaction never outlives the state it reads."""
    out = {}

    def one(st):
        try:
            with getConn() as conn:
                return buildOne(conn, st)
        except Exception as exc:                        # noqa: BLE001
            return st, exc, None, None

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = [ex.submit(one, st) for st in states]
        for f in as_completed(futs):
            st, payload, at, secs = f.result()
            if isinstance(payload, Exception):
                log(f"  ! {st}: {type(payload).__name__}: {payload}")
                continue
            out[st] = (payload, at)
            log(f"  {st}: {len(payload):,} bytes [{secs:.1f}s]")
    return out


def _write(conn, name, rows):
    with conn.cursor() as cur:
        for st, (payload, at) in sorted(rows.items()):
            cur.execute(_UPSERT.format(name=name),
                        (st, payload, at, cuts.CODE, len(payload)))
    conn.commit()


def publish(conn, rows, n_states, in_place=False):
    """Write `rows` ({state: (payload, at)}): in place into the live table
    (--state), or into a shadow table swapped over the live one."""
    from dbfast import swapTable, SWAP_WAIT_S
    if in_place:
        with conn.cursor() as cur:
            cur.execute(f"SELECT to_regclass('public.{TABLE}') AS t")
            if cur.fetchone()[0] is None:
                cur.execute(_DDL.format(name=TABLE))
        conn.commit()
        _write(conn, TABLE, rows)
        print(f"{TABLE}: {len(rows)} state(s) written in place", flush=True)
        return
    if len(rows) < _MIN_SHARE * n_states:
        raise SystemExit(f"only {len(rows)} of {n_states} states computed: "
                         f"refusing to swap (the old table stays)")
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = %s",
                    (f"{int(SWAP_WAIT_S * 1000)}ms",))
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}_new")
        cur.execute(_DDL.format(name=f"{TABLE}_new"))
    conn.commit()
    _write(conn, f"{TABLE}_new", rows)
    swapTable(conn, TABLE, renames=[(f"{TABLE}_new_pkey", f"{TABLE}_pkey")])
    print(f"{TABLE}: swapped in, {len(rows)} states", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=None,
                    help="comma list: build only these, into the live table")
    ap.add_argument("--jobs", type=int, default=4,
                    help="states computed at once (default 4)")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and check every payload; write nothing")
    args = ap.parse_args(argv)
    from dbfast import interruptible
    from database import getConn
    interruptible()

    every = allStates()
    if args.state:
        states = [s.strip().upper() for s in args.state.split(",") if s.strip()]
        bad = [s for s in states if s not in every]
        if bad:
            sys.exit(f"unknown state(s): {', '.join(bad)}")
    else:
        states = every
    t0 = time.time()
    print(f"computing {len(states)} state(s), {args.jobs} at once "
          f"(code {cuts.CODE})", flush=True)
    rows = computeAll(states, args.jobs, getConn)
    n_bytes = sum(len(p) for p, _a in rows.values())
    print(f"computed {len(rows)}/{len(states)} states, {n_bytes:,} bytes, "
          f"{time.time() - t0:.0f} s", flush=True)
    if args.dry_run:
        return 0
    if not rows:
        sys.exit("no state computed: nothing written")

    with getConn() as conn:
        publish(conn, rows, len(states), in_place=bool(args.state))
    print(f"done in {(time.time() - t0) / 60:.1f} min", flush=True)
    missing = sorted(set(states) - set(rows))
    if missing:
        print(f"  no row (computed live by the site): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
