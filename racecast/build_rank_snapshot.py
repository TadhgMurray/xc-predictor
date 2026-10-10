# Project: xc-predictor / racecast
# File:    build_rank_snapshot.py
# Purpose: keep a weekly copy of the live season's state ranks, so the
#          /movers digest can say who climbed (owner, 2026-10-10, approved).
#          Pipeline step 10g2, right after 10g_season_ranks.
#
#     python racecast/build_rank_snapshot.py            # write this week's snapshot
#     python racecast/build_rank_snapshot.py --dry-run  # counts only
#
# ★ THERE WAS NO RANK HISTORY. season_rank (build_season_ranks.py, 10g) is
#   rebuilt and swapped every run, and team_season the same; nothing kept
#   last week's board. This table is the smallest thing that answers "what
#   changed": one row per ranked athlete-season of the LIVE season, per week.
#
# ★ ONE ROW PER WEEK, AND THE WEEK STARTS ON FRIDAY. Each run overwrites the
#   current week's rows, so a week's snapshot is the board as of its last
#   run -- and with weeks running Friday to Thursday, that is the board just
#   BEFORE a weekend's meets, with the previous weekend's results in. /movers
#   compares the newest week with the one before it: on a Monday that is
#   "this weekend's racing", on a Friday it is next to nothing, and the page
#   says which dates it compares.
#
# ! THE LIVE SEASON ONLY, AND THE ONE BEFORE IT KEPT. A season's history is
#   what the digest reads; anything older than last season is deleted, so
#   the table is about two seasons of ~weekly boards (a few million rows),
#   not a copy of season_rank per run forever.
#
# ! NOT SWAPPED. Unlike the boards this table accumulates, so it is written
#   in place inside one transaction: delete this week's rows, insert them.
import argparse
import datetime
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

from database import getConn                            # noqa: E402

FRIDAY = 4                       # date.weekday(): the week's first day

DDL = """
CREATE TABLE IF NOT EXISTS season_rank_weekly (
    week        date    NOT NULL,   -- the Friday the week starts on
    taken_at    timestamptz NOT NULL DEFAULT now(),
    person_id   bigint  NOT NULL,
    pool        text    NOT NULL,
    sport       text    NOT NULL,
    year        int     NOT NULL,
    state       text,
    school      text,
    nation      int,
    state_rank  int,
    mean_rating real,
    PRIMARY KEY (week, person_id, pool, sport, year)
);
CREATE INDEX IF NOT EXISTS season_rank_weekly_board
    ON season_rank_weekly (sport, pool, year, state, week, state_rank);
"""


def weekKey(day):
    """The Friday on or before `day`."""
    return day - datetime.timedelta(days=(day.weekday() - FRIDAY) % 7)


def liveYears(cur):
    """{sport: stored year} of the season the boards show: homepage_meta's
    label through predict's own reading, so this and the pages agree."""
    from predict import _currentSeasonUncached
    out = {}
    for sport in ("XC", "TF"):
        try:
            out[sport] = int(_currentSeasonUncached(cur, sport))
        except Exception as exc:                        # noqa: BLE001
            cur.connection.rollback()
            print(f"  live {sport} season unknown ({type(exc).__name__}); skipped")
    return out


def main():
    ap = argparse.ArgumentParser(description="Snapshot this week's state ranks.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    week = weekKey(datetime.date.today())
    t0 = time.time()
    import psycopg2.extras
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.season_rank') AS t")
            if cur.fetchone()["t"] is None:
                print("  season_rank missing -- run 10g_season_ranks first.")
                return
            years = liveYears(cur)
            if a.dry_run:
                for sport, y in years.items():
                    cur.execute("""SELECT count(*) AS n FROM season_rank
                                   WHERE sport = %s AND year = %s AND state_rank IS NOT NULL""",
                                (sport, y))
                    print(f"  {sport} {y}: {cur.fetchone()['n']:,} ranked rows for week {week}")
                conn.rollback()
                return
            cur.execute(DDL)
            for sport, y in years.items():
                cur.execute("DELETE FROM season_rank_weekly WHERE week = %s AND sport = %s",
                            (week, sport))
                cur.execute("""
                    INSERT INTO season_rank_weekly
                           (week, person_id, pool, sport, year, state, school,
                            nation, state_rank, mean_rating)
                    SELECT %(w)s, r.person_id, r.pool, r.sport, r.year, s.state, s.school,
                           r.nation, r.state_rank, s.mean_rating
                    FROM   season_rank r
                    JOIN   athlete_season s USING (person_id, pool, sport, year)
                    WHERE  r.sport = %(sp)s AND r.year = %(y)s
                      AND  r.state_rank IS NOT NULL
                """, {"w": week, "sp": sport, "y": y})
                n = cur.rowcount
                cur.execute("DELETE FROM season_rank_weekly WHERE sport = %s AND year < %s",
                            (sport, y - 1))
                print(f"  {sport} {y}: week of {week}: {n:,} ranked rows "
                      f"(older than {y - 1} pruned: {cur.rowcount:,})")
            conn.commit()
    print(f"  season_rank_weekly written [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
