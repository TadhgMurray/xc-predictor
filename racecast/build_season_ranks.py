# Project: xc-predictor / racecast
# File:    build_season_ranks.py
# Purpose: Every athlete-season's place on every board the athlete page's
#          rank line shows, computed once. Pipeline step 10g, after
#          10_rankings (athlete_season with its unit columns stamped).
#
#     python racecast/build_season_ranks.py            # write season_rank
#     python racecast/build_season_ranks.py --verify 40 # and check 40 against the live count
#
# ★ WHY (issue 311, owner: "the athlete page is really slow when not
#   cached"). The rank line under the stat strip asked the boards for the
#   athlete's place in up to eight scopes per page load (nation, state,
#   the state's division, section, the section's division, area, league,
#   team), each a COUNT of everyone rated above them with that scope's
#   filter. Nation rides the (pool, sport, year, mean_rating) index; every
#   scoped count walks the index range above the athlete's rating and
#   filters row by row, which for a mid-pack athlete is most of the pool,
#   six times over. Seconds, uncached, on the site's most visited page.
#
# ★ THE SAME NUMBERS, ONCE. A window function per scope over
#   athlete_season: row_number() ordered exactly as the ability board
#   orders (mean_rating DESC, person_id) inside the partition the board's
#   filter would select. The rank line then reads one row. The live
#   computation stays in app.buildRankLine as the fallback for a database
#   without this table, and --verify compares a sample against it.
#
# ! WHAT THE PARTITIONS MEAN, so they match rankings._whereClauses:
#   - ranked: the boards' floor, three races up to 2026 TF and none after
#     (season_floor.floorSql); rows under it are on no board and get NULL.
#   - nation: US rows only (scope=usa adds state = ANY(US_STATES)).
#   - the high-school units carry the state (rankings.HS_UNITS get the
#     state filter), section_div and area carry their section too
#     (app._UNIT_PARENT); the college units carry nothing.
#   - state_div matches "state_div" OR "class" on the board; here the
#     partition key is coalesce(state_div, class), the one approximation.
#   - team: the school page's order, count of strictly faster + 1, any
#     pool, any race count (rank(), not row_number()).

import argparse
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

from database import getConn                            # noqa: E402
from dbfast import swapTable                            # noqa: E402
from rankings import US_STATES                          # noqa: E402
from season_floor import DEFAULT_FLOOR, OPEN_FROM       # noqa: E402

_COLS = ("nation", "nation_total", "state_rank", "state_div", "section", "section_div",
         "area", "league", "division", "region", "conference", "team")
_WORK_MEM = ("2GB", "1GB", "512MB", "256MB")


def rankSql(unit_cols=True):
    """The one statement. unit_cols says whether athlete_season carries the
    unit columns (older databases do not: those scopes come out NULL)."""
    u = (lambda c: f'"{c}"') if unit_cols else (lambda c: "NULL::text")
    # a window over the board's filter set; NULL outside it
    def rn(*keys):
        parts = ", ".join(["pool", "sport", "year", "ranked", "us"] + list(keys))
        return f"row_number() OVER (PARTITION BY {parts} ORDER BY mean_rating DESC, person_id)"
    return f"""
        CREATE TABLE season_rank_new AS
        WITH base AS (
            SELECT person_id, pool, sport, year, mean_rating, school, state,
                   (n_races >= {int(DEFAULT_FLOOR)} OR year >= {int(OPEN_FROM)}) AS ranked,
                   (state = ANY(%(us)s)) AS us,
                   coalesce({u('state_div')}, {u('class')}) AS sd,
                   {u('section')} AS sec, {u('section_div')} AS secd, {u('area')} AS ar,
                   {u('league')} AS lg, {u('division')} AS dv, {u('region')} AS rg,
                   {u('conference')} AS cf
            FROM   athlete_season
            WHERE  mean_rating IS NOT NULL
        )
        SELECT person_id, pool, sport, year,
               CASE WHEN ranked AND us THEN {rn()} END::int AS nation,
               CASE WHEN ranked AND us THEN count(*) OVER (PARTITION BY pool, sport, year, ranked, us) END::int AS nation_total,
               CASE WHEN ranked AND state IS NOT NULL THEN {rn('state')} END::int AS state_rank,
               CASE WHEN ranked AND state IS NOT NULL AND sd IS NOT NULL THEN {rn('state', 'sd')} END::int AS state_div,
               CASE WHEN ranked AND state IS NOT NULL AND sec IS NOT NULL THEN {rn('state', 'sec')} END::int AS section,
               CASE WHEN ranked AND state IS NOT NULL AND sec IS NOT NULL AND secd IS NOT NULL THEN {rn('state', 'sec', 'secd')} END::int AS section_div,
               CASE WHEN ranked AND state IS NOT NULL AND sec IS NOT NULL AND ar IS NOT NULL THEN {rn('state', 'sec', 'ar')} END::int AS area,
               CASE WHEN ranked AND state IS NOT NULL AND lg IS NOT NULL THEN {rn('state', 'lg')} END::int AS league,
               CASE WHEN ranked AND dv IS NOT NULL THEN {rn('dv')} END::int AS division,
               CASE WHEN ranked AND rg IS NOT NULL THEN {rn('rg')} END::int AS region,
               CASE WHEN ranked AND cf IS NOT NULL THEN {rn('cf')} END::int AS conference,
               CASE WHEN school IS NOT NULL THEN
                    rank() OVER (PARTITION BY school, sport, year ORDER BY mean_rating DESC) END::int AS team
        FROM   base
    """


def _hasUnitCols(cur):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'athlete_season' AND column_name = 'section'""")
    return cur.fetchone() is not None


def verify(conn, n):
    """A sample of athletes: the stored nation, state and team ranks
    against the live computation the page used to do. Prints mismatches;
    returns their count."""
    import psycopg2.extras
    from werkzeug.datastructures import MultiDict
    from rankings import parseFilters, rankOf
    from season_floor import floorFor
    bad = 0
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT r.*, s.state AS st, s.school, s.mean_rating
                       FROM season_rank_new r JOIN athlete_season s USING (person_id, pool, sport, year)
                       WHERE r.nation IS NOT NULL AND s.year >= 2024
                       ORDER BY random() LIMIT %s""", (n,))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        with conn.cursor() as plain:
            for with_state in (False, True):
                args = {"board": "ability", "pool": r["pool"], "sport": r["sport"], "year": str(r["year"]),
                        "min_races": str(floorFor(r["year"]))}
                if with_state:
                    if not r["st"]:
                        continue
                    args["state"] = r["st"]
                f, err = parseFilters(MultiDict(args))
                if err:
                    continue
                live = rankOf(plain, f, r["person_id"])
                stored = r["state_rank"] if with_state else r["nation"]
                if live != stored:
                    bad += 1
                    print(f"  MISMATCH {'state' if with_state else 'nation'} person={r['person_id']} "
                          f"{r['pool']}/{r['sport']}/{r['year']}: stored {stored} live {live}")
        conn.rollback()
    print(f"  verified {len(rows)} seasons, nation and state: {bad} mismatches")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", type=int, default=0, help="check N random seasons against the live count")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.athlete_season')")
            if cur.fetchone()[0] is None:
                print("  athlete_season missing -- run 10_rankings first.")
                return
            unit_cols = _hasUnitCols(cur)
            print(f"  unit columns on athlete_season: {'yes' if unit_cols else 'no (unit scopes come out NULL)'}")
            if args.dry_run:
                print(rankSql(unit_cols))
                return
            cur.execute("DROP TABLE IF EXISTS season_rank_new")
            for want in _WORK_MEM:
                try:
                    cur.execute(f"SET LOCAL work_mem = '{want}'")
                    print(f"    work_mem {want} for the window sorts")
                    break
                except Exception as exc:                  # noqa: BLE001
                    conn.rollback()
                    print(f"    (server refused work_mem {want}: {str(exc).splitlines()[0]})")
            t0 = time.time()
            cur.execute(rankSql(unit_cols), {"us": list(US_STATES)})
            cur.execute("SELECT count(*) FROM season_rank_new")
            n = cur.fetchone()[0]
            print(f"    [{time.time() - t0:7.1f}s] season_rank_new: {n:,} rows")
            cur.execute("ALTER TABLE season_rank_new ADD CONSTRAINT season_rank_new_pkey "
                        "PRIMARY KEY (person_id, pool, sport, year)")
            conn.commit()
        if args.verify:
            verify(conn, args.verify)
        swapTable(conn, "season_rank", renames=[("season_rank_new_pkey", "season_rank_pkey")])
    print(f"  season_rank: {n:,} rows written.")


if __name__ == "__main__":
    main()
