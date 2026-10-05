# Project: xc-predictor / racecast
# File:    build_breakouts.py
# Purpose: Breakouts, precomputed (owner, 2026-10-05: "kind of slow", "include
#          the section/league filters as usual, and maybe more day options").
#          Every breakout and every new PR of the last WINDOW_MAX days, per
#          level, with names, meet names and the row's unit columns, in one
#          table the page only reads. Nightly (after the boards) and pipeline
#          step 10h.
#
#     python racecast/build_breakouts.py
#     python racecast/build_breakouts.py --sport XC --dry-run
#
# ★ ONE BUILD SERVES EVERY WINDOW. A breakout is judged against the athlete's
#   EARLIER races this season (breakouts.breakoutOf) and a PR against their
#   earlier times (breakouts.prOf); neither depends on how many days back the
#   page looks. So the longest window is scored once and 7 / 14 / 30 days is
#   a date filter on the read.
#
# ★ THE RULES ARE breakouts.py's, UNCHANGED. Same dedup (same person, day,
#   rounded time), same outlier anti-join, same MIN_PRIOR / MIN_JUMP / PR
#   distance band, same check marks. Only the per-window rank limits are
#   gone: the page applies them after its filters, so a league or a search
#   never runs out of rows a national top 200 would have cut.
#
# ! ALL IN SQL. A level's month is a few hundred thousand rows in season;
#   they go from ranking_results to the shadow table without a round trip
#   through Python, names and meet names joined on the way.
#
# Swap discipline as the other builds: shadow table, indexed, then
# dbfast.swapTable under a bounded lock; the live table is never empty.

import argparse
import datetime
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import breakouts as B                                   # noqa: E402
from database import getConn                            # noqa: E402
from dbfast import swapTable                            # noqa: E402
from rankings import nameLateral                        # noqa: E402

_DDL = """
DROP TABLE IF EXISTS breakout_rows_new;
CREATE TABLE breakout_rows_new (
    kind          text    NOT NULL,      -- 'jump' | 'pr'
    sport         text    NOT NULL,
    level         text    NOT NULL,      -- breakouts.LEVELS key
    year          integer NOT NULL,
    result_id     bigint  NOT NULL,
    person_id     bigint  NOT NULL,
    name          text,
    race_date     date    NOT NULL,
    speed_rating  real,
    time_seconds  real,
    distance      real,
    meet_id       bigint,
    div_id        bigint,
    event_id      bigint,
    meet_name     text,
    school        text,
    state         text,
    grade         text,
    base          real,     -- jump: median of the earlier races this season
    prev_best     real,     -- jump: best earlier rating; pr: the old PR time
    n_prior       integer,  -- jump: earlier races; pr: earlier times at the distance
    jump          real,
    std           real,     -- pr: the standard distance
    gain          real,     -- pr: share faster than the old PR
    beat_best     boolean,
    "check"       boolean{units}
);
DROP TABLE IF EXISTS breakout_meta_new;
CREATE TABLE breakout_meta_new (
    sport     text NOT NULL,
    level     text NOT NULL,
    year      integer NOT NULL,
    anchor    date,
    built_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sport, level)
);
"""

# the level's season, deduplicated as breakouts._SEASON_CTE does
_SEASON = """
CREATE TEMP TABLE bo_season AS
SELECT DISTINCT ON (rr.person_id, rr.race_date, round(rr.time_seconds::numeric, 1))
       rr.result_id, rr.person_id, rr.race_date, rr.speed_rating, rr.time_seconds,
       rr.distance, rr.meet_id, rr.div_id, rr.event_id, rr.school, rr.state, rr.grade
       {unit_sel}{kind_sel}
FROM   ranking_results rr
WHERE  rr.pool = %(pool)s AND rr.sport = %(sport)s AND rr.year = %(year)s
  AND  rr.speed_rating IS NOT NULL
  AND  rr.race_date <= %(tomorrow)s
  {outliers}
ORDER  BY rr.person_id, rr.race_date, round(rr.time_seconds::numeric, 1), rr.result_id
"""

# race name: XC by division (anet) else the tfrrs meet; TF by division
_MEET_NAME = {
    "XC": """COALESCE((SELECT m.meet_name FROM meets m WHERE m.div_id = s.div_id LIMIT 1),
                      (SELECT t.meet_name FROM meets_tfrrs t
                       WHERE t.meet_id = s.meet_id AND t.sport = 'XC' LIMIT 1))""",
    "TF": """(SELECT m.meet_name FROM meets_tf m WHERE m.div_id = s.div_id LIMIT 1)""",
}

_INSERT_JUMPS = """
INSERT INTO breakout_rows_new ({cols})
SELECT 'jump', %(sport)s, %(level)s, %(year)s, s.result_id, s.person_id,
       COALESCE(a.name, 'Unknown'), s.race_date, s.speed_rating, s.time_seconds,
       s.distance, s.meet_id, s.div_id, s.event_id, {meet_name}, s.school, s.state,
       s.grade, p.base, p.prev_best, p.n_prior, s.speed_rating - p.base, NULL, NULL,
       s.speed_rating > p.prev_best, s.speed_rating - p.base >= %(check_jump)s
       {unit_vals}
FROM   bo_season s
JOIN  (SELECT r.result_id,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY q.speed_rating) AS base,
              max(q.speed_rating) AS prev_best, count(*) AS n_prior
       FROM   bo_season r
       JOIN   bo_season q ON q.person_id = r.person_id AND q.race_date < r.race_date
       WHERE  r.race_date > %(lo)s
       GROUP  BY r.result_id
       HAVING count(*) >= %(min_prior)s) p USING (result_id)
{names}
WHERE  s.race_date > %(lo)s
  AND  s.speed_rating - p.base >= %(min_jump)s
"""

_INSERT_PRS = """
INSERT INTO breakout_rows_new ({cols})
SELECT 'pr', %(sport)s, %(level)s, %(year)s, s.result_id, s.person_id,
       COALESCE(a.name, 'Unknown'), s.race_date, s.speed_rating, s.time_seconds,
       s.distance, s.meet_id, s.div_id, s.event_id, {meet_name}, s.school, s.state,
       s.grade, NULL, h.prev_best, h.n_prev, NULL, s.std,
       (h.prev_best - s.time_seconds) / h.prev_best, NULL,
       (h.prev_best - s.time_seconds) / h.prev_best >= %(check_gain)s
       {unit_vals}
FROM  (SELECT b.*, d.std
       FROM   bo_season b
       JOIN   unnest(%(stds)s::real[]) AS d(std)
              ON b.distance BETWEEN d.std * (1 - %(tol)s) AND d.std * (1 + %(tol)s)
       WHERE  b.race_date > %(lo)s
         AND  b.time_seconds > 0 AND b.time_seconds < 19999 {flat}) s
CROSS JOIN LATERAL (
       SELECT min(x.time_seconds) AS prev_best, count(*) AS n_prev
       FROM   ranking_results x
       WHERE  x.person_id = s.person_id AND x.sport = %(sport)s
         AND  x.race_date < s.race_date
         AND  x.distance BETWEEN s.std * (1 - %(tol)s) AND s.std * (1 + %(tol)s)
         AND  x.time_seconds > 0 AND x.time_seconds < 19999
         {flat_x}) h
{names}
WHERE  h.n_prev >= 1 AND s.time_seconds < h.prev_best
"""

_COLS = ["kind", "sport", "level", "year", "result_id", "person_id", "name",
         "race_date", "speed_rating", "time_seconds", "distance", "meet_id",
         "div_id", "event_id", "meet_name", "school", "state", "grade", "base",
         "prev_best", "n_prior", "jump", "std", "gain", "beat_best", "check"]


def _unitCols(cur):
    """The unit columns and event_kind as ranking_results carries them here."""
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'ranking_results'""")
    have = {r[0] for r in cur.fetchall()}
    from build_ranking_results import _UNIT_COLS
    return [c for c in _UNIT_COLS if c in have], "event_kind" in have


def _level(cur, sport, level, year, units, has_kind):
    """Score one level's last WINDOW_MAX days into the shadow table; returns
    (n_jumps, n_prs, anchor)."""
    pool, _ = B.LEVELS[level]
    cur.execute("DROP TABLE IF EXISTS bo_season")
    cur.execute(_SEASON.format(unit_sel="".join(f', rr."{c}"' for c in units),
                               kind_sel=", rr.event_kind" if has_kind else "",
                               outliers=B._outlierSql(cur, sport)),
                {"pool": pool, "sport": sport, "year": year,
                 "tomorrow": (datetime.date.today() + datetime.timedelta(days=1)).isoformat()})
    cur.execute("CREATE INDEX ON bo_season (person_id, race_date)")
    cur.execute("ANALYZE bo_season")
    cur.execute("SELECT max(race_date) FROM bo_season")
    anchor = cur.fetchone()[0]
    if anchor is None:
        return 0, 0, None
    cols = ", ".join(f'"{c}"' for c in _COLS + units)
    unit_vals = "".join(f', s."{c}"' for c in units)
    p = {"sport": sport, "level": level, "year": year,
         "lo": anchor - datetime.timedelta(days=max(B.WINDOW_CHOICES)),
         "min_prior": B.MIN_PRIOR, "min_jump": B.MIN_JUMP, "check_jump": B.CHECK_JUMP,
         "check_gain": B.CHECK_GAIN, "stds": list(B.STANDARD[sport]), "tol": B.DIST_TOL}
    names = nameLateral("s")
    cur.execute(_INSERT_JUMPS.format(cols=cols, meet_name=_MEET_NAME[sport],
                                     unit_vals=unit_vals, names=names), p)
    n_j = cur.rowcount
    cur.execute(_INSERT_PRS.format(
        cols=cols, meet_name=_MEET_NAME[sport], unit_vals=unit_vals, names=names,
        flat=" AND b.event_kind IS NULL" if has_kind else "",
        flat_x=" AND x.event_kind IS NULL" if (has_kind and sport == "TF") else ""), p)
    return n_j, cur.rowcount, anchor


def main():
    ap = argparse.ArgumentParser(description="Build breakout_rows (the Breakouts page).")
    ap.add_argument("--sport", choices=["XC", "TF"], action="append")
    ap.add_argument("--dry-run", action="store_true", help="score, print, write nothing")
    a = ap.parse_args()
    sports = a.sport or ["XC", "TF"]
    t0 = time.time()
    total = 0
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SET jit = off")
            cur.execute("SET work_mem = '256MB'")
            units, has_kind = _unitCols(cur)
            latest = B.latestDates(cur)
            cur.execute(_DDL.format(units="".join(f',\n    "{c}" text' for c in units)))
            meta = []
            for sport in sports:
                year = B.seasonFor(sport, latest)
                for level in B.LEVELS:
                    t1 = time.time()
                    n_j, n_p, anchor = _level(cur, sport, level, year, units, has_kind)
                    total += n_j + n_p
                    meta.append((sport, level, year, anchor))
                    print(f"  {sport} {level} {year}: {n_j:,} breakouts, {n_p:,} PRs "
                          f"in the {max(B.WINDOW_CHOICES)} days to {anchor}  "
                          f"[{time.time() - t1:.0f}s]", flush=True)
            if a.dry_run:
                conn.rollback()
                return
            for m in meta:
                cur.execute("INSERT INTO breakout_meta_new (sport, level, year, anchor) "
                            "VALUES (%s, %s, %s, %s)", m)
            # a --sport run keeps the other sport's rows
            cur.execute("SELECT to_regclass('breakout_rows') IS NOT NULL")
            if cur.fetchone()[0] and set(sports) != {"XC", "TF"}:
                cur.execute(f"""INSERT INTO breakout_rows_new ({', '.join(f'"{c}"' for c in _COLS + units)})
                               SELECT {', '.join(f'"{c}"' for c in _COLS + units)}
                               FROM breakout_rows WHERE sport <> ALL(%s)""", (sports,))
                cur.execute("""INSERT INTO breakout_meta_new SELECT * FROM breakout_meta
                               WHERE sport <> ALL(%s)""", (sports,))
            cur.execute("CREATE INDEX idx_breakout_rows_new_read "
                        "ON breakout_rows_new (sport, level, kind, race_date)")
            conn.commit()
        swapTable(conn, "breakout_rows", renames=[
            ("idx_breakout_rows_new_read", "idx_breakout_rows_read")])
        swapTable(conn, "breakout_meta", renames=[
            ("breakout_meta_new_pkey", "breakout_meta_pkey")])
    print(f"  breakout_rows: {total:,} rows [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
