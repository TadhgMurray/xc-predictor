"""
build_venue_tf_boards.py -- precompute the track venue pages.

    python racecast/build_venue_tf_boards.py
    python racecast/build_venue_tf_boards.py --limit 50       # smoke run
    python racecast/build_venue_tf_boards.py --prepare        # then
    python racecast/build_venue_tf_boards.py --shard 0/6 ...  # then
    python racecast/build_venue_tf_boards.py --finish

Run from the PROJECT ROOT after 10_rankings (stampRowsHs reads
ranking_results). Pipeline step 12c, beside the course pages (12b).

★ COLD TIME IS THE PRODUCT (2026-11-07: /venue/tf/<id>/<in|out> at 2-5 s in
  the slow log). Every view copied the venue's whole history into a temp
  table, ANALYZEd it and computed its records from scratch. This stores the
  finished render context per (location_id, indoor) in venue_tf_boards, so
  the first viewer costs a primary-key lookup and one small count.

★ ONE CODE PATH. The context comes from app.buildVenueTfCtx, the exact
  function the live route falls back to. The route serves a board only while
  the venue's meet list is the one it was built from (app.loadVenueTfBoard):
  a venue raced after the build renders live until the next pipeline.

Swap discipline as build_course_boards: built into venue_tf_boards_new,
renamed over the live table at the end (dbfast.swapTable), so readers see
the old boards until the new ones are complete.
"""

import argparse
import datetime
import decimal
import json
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")

import psycopg2
import psycopg2.extras
from database import getConn
from dbfast import swapTable, interruptible, Progress, SWAP_WAIT_S

TABLE = "venue_tf_boards"

_DDL = f"""
CREATE TABLE {TABLE}_new (
    location_id bigint   NOT NULL,
    indoor      smallint NOT NULL,
    -- app.venueTfFingerprint when the board was built
    fp          text     NOT NULL,
    ctx         jsonb    NOT NULL,
    built_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (location_id, indoor)
)
"""

_INSERT = f"""
    INSERT INTO {TABLE}_new (location_id, indoor, fp, ctx) VALUES %s
    ON CONFLICT (location_id, indoor) DO NOTHING
"""


def _default(o):
    # the venue's columns are REAL / TEXT / INT; anything else is kept as the
    # number or the ISO day it means rather than refused
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    return str(o)


def payloadJson(ctx):
    """The render context as jsonb text.

    ⚠ ensure_ascii=False for the reason build_course_boards gives: the
      cluster is SQL_ASCII and cannot translate a \\uXXXX escape for an
      accented name. allow_nan=False: jsonb has no NaN, and a venue that
      would carry one is skipped (it renders live) rather than failing the
      batch it rides in."""
    return json.dumps(ctx, default=_default, ensure_ascii=False,
                      allow_nan=False)


def _flush(conn, cur, batch):
    """Insert a batch; one row that Postgres refuses costs only itself."""
    if not batch:
        return 0
    try:
        psycopg2.extras.execute_values(cur, _INSERT, batch,
                                       template="(%s, %s, %s, %s::jsonb)")
        conn.commit()
        return 0
    except psycopg2.Error:
        conn.rollback()
    bad = 0
    for row in batch:
        try:
            psycopg2.extras.execute_values(cur, _INSERT, [row],
                                           template="(%s, %s, %s, %s::jsonb)")
            conn.commit()
        except psycopg2.Error as exc:
            conn.rollback()
            bad += 1
            print(f"  ! venue {row[0]}/{row[1]}: insert refused: "
                  f"{type(exc).__name__}: {exc}", flush=True)
    return bad


def venueList(cur):
    """[(location_id, indoor)], the venues with the most meet rows first."""
    cur.execute("""
        SELECT location_id, COALESCE(is_indoor, 0) AS indoor, count(*) AS n
        FROM   meets_tf
        WHERE  location_id IS NOT NULL
        GROUP  BY 1, 2
        ORDER  BY n DESC, 1, 2
    """)
    return [(r["location_id"], int(r["indoor"])) for r in cur.fetchall()]


def buildOne(cur, location_id, indoor):
    """(fp, jsonb text) for one venue -- app's own functions."""
    from app import buildVenueTfCtx, venueTfFingerprint
    # the fingerprint FIRST: a meet landing during the build leaves the
    # board with the older list's fingerprint, so it is never served
    fp = venueTfFingerprint(cur, location_id, bool(indoor))
    ctx = buildVenueTfCtx(cur, location_id, bool(indoor))
    return fp, payloadJson(ctx)


def main():
    ap = argparse.ArgumentParser()
    # ⚠ THE HEAD, NOT THE WORLD, as for the course pages: the slow venues are
    #   the big ones; the long tail renders live in a few hundred ms.
    ap.add_argument("--limit", type=int, default=2000,
                    help="build the N biggest venues (default 2000)")
    ap.add_argument("--all", action="store_true",
                    help="every venue, however long it takes")
    ap.add_argument("--prepare", action="store_true",
                    help="create the empty shadow table and stop")
    ap.add_argument("--shard", default=None,
                    help="K/N: build venues K, K+N, ... into the existing "
                         "shadow table; no swap")
    ap.add_argument("--finish", action="store_true",
                    help="swap the shadow table in and stop")
    args = ap.parse_args()
    interruptible()

    if args.prepare:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = %s",
                            (f"{int(SWAP_WAIT_S * 1000)}ms",))
                cur.execute(f"DROP TABLE IF EXISTS {TABLE}_new")
                cur.execute(_DDL)
            conn.commit()
        print(f"{TABLE}_new: empty shadow table ready", flush=True)
        return
    if args.finish:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {TABLE}_new")
                n = cur.fetchone()[0]
                if not n:
                    sys.exit(f"{TABLE}_new is empty: refusing to swap")
            conn.commit()
            swapTable(conn, TABLE,
                      renames=[(f"{TABLE}_new_pkey", f"{TABLE}_pkey")])
        print(f"{TABLE}: swapped in, {n:,} rows", flush=True)
        return
    shard_k = shard_n = None
    if args.shard:
        shard_k, shard_n = (int(x) for x in args.shard.split("/"))
        assert 0 <= shard_k < shard_n

    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if shard_n is None:
            cur.execute("SET LOCAL lock_timeout = %s",
                        (f"{int(SWAP_WAIT_S * 1000)}ms",))
            cur.execute(f"DROP TABLE IF EXISTS {TABLE}_new")
        cur.execute(f"SELECT to_regclass('public.{TABLE}_new') AS t")
        if cur.fetchone()["t"] is None:
            cur.execute(_DDL)
        conn.commit()

        venues = venueList(cur)
        conn.commit()
        if not args.all and args.limit:
            venues = venues[:args.limit]
        if shard_n is not None:
            venues = venues[shard_k::shard_n]
            print(f"shard {shard_k}/{shard_n}: {len(venues):,} venues",
                  flush=True)
        print(f"building boards for {len(venues):,} venues", flush=True)

        built = failed = 0
        batch = []
        prog = Progress("venues", len(venues))
        for loc, indoor in venues:
            try:
                fp, payload = buildOne(cur, loc, indoor)
                batch.append((loc, indoor, fp, payload))
                built += 1
            except Exception as exc:        # noqa: BLE001 -- one venue, not the run
                failed += 1
                print(f"  ! venue {loc}/{indoor}: {type(exc).__name__}: {exc}",
                      flush=True)
            # read-only work: end the transaction, which drops the venue's
            # temp table (ON COMMIT DROP) before the next one
            conn.rollback()
            prog.tick(extra=f"({built:,} built, {failed:,} failed)")
            if len(batch) >= 100:
                failed += _flush(conn, cur, batch)
                batch = []
        failed += _flush(conn, cur, batch)

        if shard_n is None:
            conn.commit()
            swapTable(conn, TABLE,
                      renames=[(f"{TABLE}_new_pkey", f"{TABLE}_pkey")])

    print(f"done: {built:,} venues built, {failed:,} failed, "
          f"{(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
