"""
build_athlete_pick.py -- the "add anyone" picker's table: one row per rated
runner, sport and gender, with their name and newest qualifying season.

    python racecast/build_athlete_pick.py            # build and swap
    python racecast/build_athlete_pick.py --dry-run

Run from the PROJECT ROOT. Pipeline step 13f5 / nightly, after the boards.
Read by app._athleteSearchRows (/api/predict/athletes).

★ WHY (2026-11-12, the slow log after the fallback walk shipped: 1.9 s
  median, some at the 10 s cap). The search answers "the 200 lowest
  person_ids whose name matches every token and who have a rated season in
  this sport and gender, each with that newest season". Walking athletes
  (16M rows, most never rated) by id stops at the 200th qualifying match,
  and for a short or common name that is still a long walk. Here every row
  qualifies, so the same walk stops 200 matches in.

★ THE SAME ROWS. A row is athletes' name (the search's own expression) and
  the newest athlete_season row with sport, mean_rating NOT NULL and the
  pool's gender letter -- exactly the old join's DISTINCT ON pick -- for
  every person with an athletes row (the old inner join).

★ EXACT, OR NOT USED. The meta row keeps athletes' and athlete_season's
  oids and change stamps (build_meet_counts.stamp), read before and after
  the build; the search uses the table only while both are unchanged.
"""
import argparse
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

TABLE = "athlete_pick"
META = "athlete_pick_meta"
SOURCES = ("athletes", "athlete_season")
NAME_SQL = "COALESCE(a.first_name,'') || ' ' || COALESCE(a.last_name,'')"

_BUILD = f"""
CREATE TABLE {TABLE}_new AS
SELECT DISTINCT ON (s.person_id, s.sport, upper(right(s.pool, 1)))
       s.person_id, s.sport, upper(right(s.pool, 1)) AS g,
       s.school, s.year, s.mean_rating, s.pool,
       {NAME_SQL} AS name
FROM   athlete_season s
JOIN   athletes a ON a.athlete_id = s.person_id
WHERE  s.mean_rating IS NOT NULL
  AND  upper(right(s.pool, 1)) IN ('M', 'F')
ORDER  BY s.person_id, s.sport, upper(right(s.pool, 1)), s.year DESC
"""


def stamps(cur):
    from build_meet_counts import stamp
    out = []
    for t in SOURCES:
        st = stamp(cur, t)
        if st is None:
            return None
        out.append(st)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from database import getConn
    from dbfast import interruptible, swapTable
    interruptible()
    t0 = time.time()
    with getConn() as conn:
        with conn.cursor() as cur:
            before = stamps(cur)
            conn.commit()
            if before is None:
                sys.exit("athletes or athlete_season missing: nothing built")
            cur.execute(f"DROP TABLE IF EXISTS {TABLE}_new, {META}_new")
            cur.execute(_BUILD)
            cur.execute(f"SELECT count(*) FROM {TABLE}_new")
            n = cur.fetchone()[0]
            print(f"  {n:,} rows [{time.time() - t0:.0f}s]", flush=True)
            if args.dry_run:
                conn.rollback()
                print("nothing written (dry run)")
                return 0
            cur.execute(f"ALTER TABLE {TABLE}_new ADD PRIMARY KEY (sport, g, person_id)")
            cur.execute(f"CREATE INDEX {TABLE}_new_name_trgm ON {TABLE}_new "
                        f"USING gin (name gin_trgm_ops)")
            cur.execute(f"""CREATE TABLE {META}_new (src text PRIMARY KEY, oid bigint NOT NULL,
                                stamp bigint NOT NULL, built_at timestamptz NOT NULL DEFAULT now())""")
            conn.commit()
            after = stamps(cur)
            conn.commit()
            if after != before:
                cur.execute(f"DROP TABLE IF EXISTS {TABLE}_new, {META}_new")
                conn.commit()
                sys.exit(f"the source tables changed during the build ({before} -> {after}): "
                         f"nothing swapped -- the search runs as before")
            for src, (oid, st) in zip(SOURCES, before):
                cur.execute(f"INSERT INTO {META}_new (src, oid, stamp) VALUES (%s, %s, %s)",
                            (src, oid, st))
            cur.execute(f"ANALYZE {TABLE}_new")
        conn.commit()
        # the rows first, then the stamps that vouch for them (a reader never
        # pairs new stamps with old rows)
        swapTable(conn, TABLE, renames=[(f"{TABLE}_new_pkey", f"{TABLE}_pkey"),
                                        (f"{TABLE}_new_name_trgm", f"{TABLE}_name_trgm")])
        swapTable(conn, META, renames=[(f"{META}_new_pkey", f"{META}_pkey")])
    print(f"{TABLE}: swapped in, {n:,} rows, {time.time() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
