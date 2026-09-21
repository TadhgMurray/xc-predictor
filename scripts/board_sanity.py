#!/usr/bin/env python3
"""
board_sanity.py -- the boards, checked before anyone reads them.

    XCP_DB_PASSWORD=... python scripts/board_sanity.py [--top 60] [--strict]

The owner read the run-22 boards and found (2026-09-14): a college_f board
headed by rows in the 170s (rated on one pool's scale, ranked against
another's mean), clubs with professionals ranked among colleges, a 5:12
"mile" at 166. Every one of those is a fact the database can state about
the row, and none of them needs a solve to check. This script asks, for
the top rows of every board:

  anchor      does normalizeTime(time, distance, pool) reproduce the row's
              normalized_time? (engine/anchor_check.mismatch, the same gate
              build_ranking_results applies -- checked again on what is
              actually published, in the pool the row is published in)
  pace        is the row faster than the world record allows? (outside
              the college pools; engine/record_pace.py)
  pool        is the row's board pool the engine's rating_pool?
  club        is the row's team a club (a team with professionals, or a
              team the rows say carries no school grades)?
  margin      how far above the pool's own top season means is the row,
              and above the stale ceilings in racecast/pool_ceiling.py?
  seasons     athlete_season rows in school pools whose school is a club
  gap         the same athlete's XC and TF season medians per pool: the
              sport level, as the boards show it
  level       (--npz, XCP_SPORT_LEVEL_POOLS set) the same gap in log-time
              against the stated gain per level: the sport level HELD
  tilt        (--npz) the go-live's tilt-by-band table divided by the
              course scale it applied: implied/applied within TILT_TOL in
              every trusted band, both sports -- the course scale and the
              tilt HELD

Hard failures (exit 1, --strict makes the soft ones hard too): a published
row that fails the anchor or the pace gate, a row whose board pool is not
its rating pool, a professional pool on a board, a level or a tilt band
off its target. Soft: a club on a school board, a row more than MARGIN
above the pool's top season means.

★ THE CONSTANTS ARE CHECKED, NOT PRINTED (owner, 2026-09-15: "I'd really
  like to kill this once and for all"). Every measured constant has a
  number that says whether it held; this is where those numbers fail the
  run instead of scrolling past.

No engine import beyond the pure checks; reads only. Runs in seconds: the
top rows are read through the (pool, year, rating) index.
"""
import argparse
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from anchor_check import mismatch as anchorMismatch            # noqa: E402
from build_ranking_results import isRankablePool                # noqa: E402
from record_pace import impossiblePace, exemptPool               # noqa: E402
from database import getConn                                    # noqa: E402
from pool_ceiling import ceilingFor                             # noqa: E402
import speed_ratings_db as sdb                                  # noqa: E402

MARGIN = 12.0        # points above the pool's top season means before a row is "too high"
TOP_SEASONS = 20     # the pool's "top" is the mean of its best N season medians
MIN_GAP_PAIRS = 30   # athlete-years with both sports before the gap is reported
LEVEL_TOL = 0.005    # log-time (about 0.6 points at 120) the gap may miss the stated gain by
TILT_TOL = 0.06      # implied/applied (after the course scale) may miss 1.0 by this, per band
TILT_MIN_VOTERS = 5000
TILT_MAX_SE = 0.02


# ------------------------------------------------------------------ #
# pure
# ------------------------------------------------------------------ #

def clubLabel(school, team_id, club_schools, club_teams):
    """Why the team is a club, or None. Pure."""
    s = (school or "").strip().lower()
    if team_id is not None and int(team_id) in club_teams:
        return club_teams[int(team_id)]
    if s and s in club_schools:
        return club_schools[s]
    return None


def topMean(values, n=TOP_SEASONS):
    """Mean of the n largest values; None when there are none."""
    vals = sorted((float(v) for v in values if v is not None), reverse=True)[:n]
    return (sum(vals) / len(vals)) if vals else None


def checkRow(row, sport, club_schools, club_teams, pool_top):
    """[(kind, hard, note)] for one board row. row: dict with the keys the
    SELECT below emits; pool_top: {pool: top season mean}. Pure."""
    out = []
    pool = row["pool"]
    rp = (row.get("rating_pool") or "").split("|", 1)[0]
    if not isRankablePool(pool):
        out.append(("pool", True, f"board pool {pool} is not rankable"))
    if rp and rp != pool:
        out.append(("pool", True, f"rated in {rp}, ranked in {pool}"))
    if not exemptPool(pool) and impossiblePace(row.get("time_seconds"), row.get("distance"), row.get("gender")):
        pace = float(row["time_seconds"]) / (float(row["distance"]) / 1000.0)
        out.append(("pace", True, f"{pace:.0f} s/km over {row['distance']:.0f} m is faster than the record"))
    is_bad, expected, ratio = anchorMismatch(row.get("time_seconds"), row.get("distance"),
                                             row.get("normalized_time"), pool, sport)
    if is_bad:
        out.append(("anchor", True, f"normalized_time is {ratio:.2f}x what {pool} gives this time"))
    why = clubLabel(row.get("school"), row.get("team_id"), club_schools, club_teams)
    if why:
        out.append(("club", False, f"{row.get('school')!r} is a club ({why})"))
    top = pool_top.get(pool)
    if top is not None and row["speed_rating"] is not None and float(row["speed_rating"]) > top + MARGIN:
        out.append(("margin", False, f"{float(row['speed_rating']):.1f} is {float(row['speed_rating']) - top:.1f} "
                                     f"above the pool's top-{TOP_SEASONS} season mean {top:.1f}"))
    return out


def levelGains(spec):
    """{level: gain} from 'college=0,hs=0.008' (XCP_SPORT_LEVEL_POOLS). Pure."""
    out = {}
    for part in (spec or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = float(v)
    return out


def levelCheck(pairs, gains, tol=LEVEL_TOL, min_pairs=MIN_GAP_PAIRS):
    """[(level, n, log gap read, target, ok)]: the same-athlete gap in
    log-time (ln XC/TF of season means: positive = track slower) per pool
    level against the stated gain -- the go-live shifted the track rows
    so the gap reads -gain. pairs: [(pool, xc_mean, tf_mean)]. Pure."""
    by = {}
    for pool, xc, tf in pairs:
        if xc and tf and float(xc) > 0 and float(tf) > 0:
            by.setdefault(pool.split("|", 1)[0].split("_", 1)[0], []).append(
                math.log(float(tf) / float(xc)))
    out = []
    for level, vals in sorted(by.items()):
        if level not in gains or len(vals) < min_pairs:
            continue
        # log(TF rating / XC rating) is -(log-time gap); the target gap is -gain
        read = -statistics.median(vals)
        target = -float(gains[level])
        out.append((level, len(vals), read, target, abs(read - target) <= tol))
    return out


def tiltCheck(bands, scale, tol=TILT_TOL, min_voters=TILT_MIN_VOTERS, max_se=TILT_MAX_SE):
    """[(sport, band lo, voters, ratio, ok)] from the npz's tilt-by-band
    array (sport, lo, n, applied, implied, se) and the course scale
    [XC, TF] it applied: implied/(applied*scale) should be 1. Pure."""
    out = []
    for row in bands if bands is not None else ():
        sp, lo, n, applied, implied, se = [float(x) for x in row]
        if n < min_voters or se > max_se or applied <= 0:
            continue
        sc = float(scale[int(sp)]) if scale is not None and len(scale) > int(sp) else 1.0
        ratio = implied / (applied * sc)
        out.append(("XC" if sp == 0 else "TF", lo, int(n), ratio, abs(ratio - 1.0) <= tol))
    return out


def gapReport(pairs):
    """{pool: (n, median TF-XC)} from [(pool, xc_med, tf_med)]. Pure."""
    by = {}
    for pool, xc, tf in pairs:
        if xc is None or tf is None:
            continue
        by.setdefault(pool, []).append(float(tf) - float(xc))
    return {p: (len(v), statistics.median(v)) for p, v in by.items() if len(v) >= MIN_GAP_PAIRS}


# ------------------------------------------------------------------ #
# database
# ------------------------------------------------------------------ #

def _hasColumn(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s AND column_name = %s""",
                (table, col))
    return cur.fetchone() is not None


def loadTopRows(cur, sport, top, year=None):
    table = "results" if sport == "XC" else "results_tf"
    rp = "r.rating_pool" if _hasColumn(cur, table, "rating_pool") else "NULL::text"
    tid = "r.team_id" if _hasColumn(cur, table, "team_id") else "NULL::bigint"
    yr = "AND rr.year = %(year)s" if year else ""
    cur.execute(f"""
        WITH top AS (
            SELECT rr.*, row_number() OVER (PARTITION BY rr.pool ORDER BY rr.speed_rating DESC) AS rk
            FROM   ranking_results rr
            WHERE  rr.sport = %(sport)s AND rr.speed_rating IS NOT NULL {yr})
        SELECT t.pool, t.rk, t.result_id, t.person_id, t.speed_rating, t.race_date, t.year,
               t.school, t.time_seconds, t.distance, t.meet_id,
               r.normalized_time, {rp} AS rating_pool, {tid} AS team_id,
               COALESCE(a.gender, CASE WHEN t.pool LIKE '%%\\_f' THEN 'F' ELSE 'M' END) AS gender,
               NULLIF(TRIM(concat_ws(' ', a.first_name, a.last_name)), '') AS name
        FROM   top t
        JOIN   {table} r ON r.result_id = t.result_id
        LEFT JOIN LATERAL (SELECT a.first_name, a.last_name, a.gender FROM athletes a
                           WHERE a.athlete_id = t.person_id
                           ORDER BY (a.gender IN ('M', 'F')) DESC LIMIT 1) a ON TRUE
        WHERE  t.rk <= %(top)s
        ORDER  BY t.pool, t.rk""", {"sport": sport, "top": int(top), "year": year})
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def loadPoolTops(cur, sport, n=TOP_SEASONS):
    cur.execute("""
        SELECT pool, mean_rating FROM (
            SELECT pool, mean_rating,
                   row_number() OVER (PARTITION BY pool ORDER BY mean_rating DESC) AS rk
            -- ⚠ RATED ONLY (2026-09-21). athlete_season now carries
            --   sprint/field seasons with no rating, and `ORDER BY
            --   mean_rating DESC` puts NULLs FIRST in Postgres: they would
            --   take rk 1..N inside every pool, topMean would then average
            --   whatever real seasons were left below them, and this
            --   checker would stop flagging the ceiling breaches it exists
            --   to flag. A sanity check that fails open is worse than none.
            FROM   athlete_season
            WHERE  sport = %s AND n_races >= 3
              AND  mean_rating IS NOT NULL) s
        WHERE rk <= %s""", (sport, int(n)))
    by = {}
    for pool, m in cur.fetchall():
        by.setdefault(pool, []).append(m)
    return {p: topMean(v, n) for p, v in by.items()}


def loadClubSeasons(cur, club_schools, limit=40):
    """athlete_season rows in school pools whose team is a club."""
    if not club_schools:
        return []
    cur.execute("""
        SELECT s.person_id, s.pool, s.sport, s.year, s.school, s.mean_rating, s.n_races
        FROM   athlete_season s
        WHERE  s.pool NOT LIKE 'pro%%' AND s.school IS NOT NULL
          AND  lower(btrim(s.school)) = ANY(%s)
        ORDER  BY s.mean_rating DESC NULLS LAST
        LIMIT  %s""", (sorted(club_schools), int(limit)))
    return cur.fetchall()


def loadSportGap(cur):
    cur.execute("""
        SELECT x.pool, x.mean_rating, t.mean_rating
        FROM   athlete_season x
        JOIN   athlete_season t ON t.person_id = x.person_id AND t.pool = x.pool
                                AND t.year = x.year AND t.sport = 'TF'
        WHERE  x.sport = 'XC' AND x.n_races >= 3 AND t.n_races >= 3""")
    return cur.fetchall()


def clubSets():
    """({school: why}, {team_id: why}) from the pack's own two club readers."""
    schools, teams = {}, {}
    pro_teams, pro_schools = sdb.loadClubPros()
    for t, n in pro_teams.items():
        teams[int(t)] = f"{n} professional(s)"
    for s, n in pro_schools.items():
        schools[s] = f"{n} professional(s)"
    data_teams, data_schools = sdb.loadClubTeams()
    for t, n in data_teams.items():
        teams.setdefault(int(t), f"no school grades on {n} rows")
    for s, n in data_schools.items():
        schools.setdefault(s, f"no school grades on {n} rows")
    return schools, teams


# ------------------------------------------------------------------ #
# report
# ------------------------------------------------------------------ #

def _fmtRow(row):
    t = row.get("time_seconds"); d = row.get("distance")
    tt = f"{int(t) // 60}:{float(t) % 60:05.2f}" if t else "-"
    dd = f"{float(d):.0f}m" if d else "-"
    return (f"#{row['rk']:<3} {float(row['speed_rating']):6.1f}  {row.get('name') or row['person_id']!s:<26.26} "
            f"{(row.get('school') or '-')!s:<28.28} {tt:>9} {dd:>6}  {row['race_date']}  result {row['result_id']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=60, help="rows per (sport, pool) to check")
    ap.add_argument("--year", type=int, default=None, help="one season year; default every year")
    ap.add_argument("--strict", action="store_true", help="soft findings fail the step too")
    ap.add_argument("--show", type=int, default=25, help="offenders printed per check")
    ap.add_argument("--npz", default=os.path.join(_ROOT, "engine", "data", "joint_difficulty.npz"),
                    help="the go-live's solve file, for the tilt check ('' skips it)")
    ap.add_argument("--level-pools", default=os.environ.get("XCP_SPORT_LEVEL_POOLS", ""),
                    help="the stated gain per level the go-live applied (XCP_SPORT_LEVEL_POOLS)")
    args = ap.parse_args()

    print("[sanity] club teams from the rows...")
    club_schools, club_teams = clubSets()
    print(f"[sanity] {len(club_schools):,} club school strings, {len(club_teams):,} club team ids")

    hard = soft = 0
    with getConn() as conn, conn.cursor() as cur:
        for sport in ("XC", "TF"):
            rows = loadTopRows(cur, sport, args.top, args.year)
            tops = loadPoolTops(cur, sport)
            findings = {}
            for row in rows:
                for kind, is_hard, note in checkRow(row, sport, club_schools, club_teams, tops):
                    findings.setdefault(kind, []).append((is_hard, note, row))
            pools = sorted({r["pool"] for r in rows})
            print(f"\n== {sport}: top {args.top} of {len(pools)} pools, {len(rows):,} rows ==")
            for p in pools:
                top = tops.get(p)
                head = [r for r in rows if r["pool"] == p][:3]
                ceil = ceilingFor(p)
                print(f"  {p:<12} top-{TOP_SEASONS} season mean {top if top is None else round(top, 1)!s:>6}"
                      f"  ceiling {ceil:5.0f}  board head "
                      + ", ".join(f"{float(r['speed_rating']):.1f}" for r in head))
            for kind in ("pool", "anchor", "pace", "club", "margin"):
                got = findings.get(kind, [])
                if not got:
                    print(f"  {kind:<7} ok")
                    continue
                n_hard = sum(1 for h, _, _ in got if h)
                hard += n_hard; soft += len(got) - n_hard
                print(f"  {kind:<7} {len(got)} finding(s){' HARD' if n_hard else ''}:")
                for _, note, row in got[:args.show]:
                    print(f"      {row['pool']:<10} {_fmtRow(row)}")
                    print(f"                 {note}")
                if len(got) > args.show:
                    print(f"      ... {len(got) - args.show} more")

        print("\n== seasons in school pools on club teams (athlete_season) ==")
        seasons = loadClubSeasons(cur, set(club_schools))
        if not seasons:
            print("  none")
        for pid, pool, sport, year, school, mean, n in seasons:
            soft += 1
            print(f"  {pool:<10} {sport} {year}  {float(mean):6.1f} over {n:>2}  {school!s:<30.30}"
                  f"  person {pid}  ({club_schools.get(school.strip().lower(), '?')})")

        print("\n== TF minus XC, same athlete, same pool and year (season medians) ==")
        pairs = loadSportGap(cur)
        gaps = gapReport(pairs)
        if not gaps:
            print("  (too few athlete-years with both sports)")
        for p in sorted(gaps):
            n, med = gaps[p]
            print(f"  {p:<12} {n:>7,} athlete-years   median TF-XC {med:+6.2f}")

    print("\n== the sport level held? (log-time gap per level against XCP_SPORT_LEVEL_POOLS) ==")
    gains = levelGains(args.level_pools)
    if not gains:
        print("  (no XCP_SPORT_LEVEL_POOLS in the environment: not checked)")
    for level, n, read, target, ok in levelCheck(pairs, gains):
        if not ok:
            hard += 1
        print(f"  {level:<9} {n:>8,} athlete-years   gap read {read:+.4f}   target {target:+.4f}"
              f"   {'ok' if ok else 'HARD: off by more than ' + str(LEVEL_TOL)}")

    print("\n== the course scale and the tilt held? (implied/applied per band, after the scale) ==")
    if args.npz and os.path.exists(args.npz):
        import numpy as np
        with np.load(args.npz, allow_pickle=False) as npz:
            bands = npz["bracket_tilt_bands"] if "bracket_tilt_bands" in npz else None
            scale = npz["bracket_course_scale"] if "bracket_course_scale" in npz else None
        if bands is None or bands.size == 0:
            print("  (the solve file carries no band table: a go-live before 2026-09-15)")
        else:
            print(f"  course scale applied: XC x{float(scale[0]):.3f}, TF x{float(scale[1]):.3f}")
            for sport, lo, n, ratio, ok in tiltCheck(bands, scale):
                if not ok:
                    hard += 1
                lab = "<100" if lo == float("-inf") else f"{lo:.0f}+"
                print(f"  {sport:<4} {lab:>6} {n:>10,} voters   implied/applied {ratio:.3f}"
                      f"   {'ok' if ok else 'HARD: off by more than ' + str(TILT_TOL)}")
    else:
        print("  (no solve file at --npz: not checked)")

    print(f"\n[sanity] {hard} hard finding(s), {soft} soft finding(s)")
    if hard or (args.strict and soft):
        print("[sanity] FAILED")
        return 1
    print("[sanity] ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
