# Project: xc-predictor / scripts
# File:    school_pro_census.py
# Purpose: BEFORE the rebuild, count what the 2026-09-30 school veto moves.
#
#     python scripts/school_pro_census.py
#     python scripts/school_pro_census.py --show 30          # and who
#     python scripts/school_pro_census.py --person 30178075  # one athlete
#     python scripts/school_pro_census.py --factor           # the pro HS factor
#
# Read-only: one READ ONLY transaction, no temp tables, nothing written. The
# team levels come from the engine's own loaders (speed_ratings_db), which
# open their own connections and only SELECT.
#
# ★ THREE COUNTS (owner, 2026-09-30: Jackson Spencer, 30178075, pooled pro
#   for his high school senior season).
#
#   (a) pro_athlete_season rows the rebuilt pro_flag will not write: a
#       school grade at a school team in that season (pool_resolve.
#       schoolSeasonVetoesPro per row; pro_flag.buildSchoolSeasons over the
#       season), hand-listed seasons excepted. Counted against the table as
#       it stands, with the rule the rebuild applies -- the same season
#       grade (grade_fix's, else the rows'), the same team levels.
#       ⚠ A LOWER BOUND ON WHAT MOVES. A vetoed season stops counting as a
#         confirmed professional in every field it raced, so a propagated
#         season that leaned on it can fall too; only the rebuild's own
#         report ("school veto ...", then the rounds) says how far.
#   (b) seeded seasons (round 0) that cleared _MIN_PRO_RACES only because
#       the old seed counted a track result once per EVENT of its division
#       (the JOIN on meets_tf, 2026-09-30). They may still be reached by
#       propagation; this says how many leaned on the multiplication.
#   (c) --factor: the pro pool's HS-equivalent factor the DATA implies --
#       athletes with an hs-rated and a pro-rated mark at one track distance
#       in adjacent seasons: hs rating x hs time / pro time / pro rating --
#       beside engine_scale's pm(hs)/pm(college), the factor pool_view now
#       gives a pro pool (by ability; by pool it also carries the anchor
#       ratio F(college)/F(hs)).

import argparse
import os
import sys
from collections import Counter, defaultdict
from statistics import median

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

from database import getConn                                    # noqa: E402
from normalize_distance import isSchoolGrade, isCollegeClass    # noqa: E402
from pool_resolve import (isProPerson, isProTeam, teamLevelOf,  # noqa: E402
                          UNATTACHED_TEAM_ID)
from season_level import seasonIsSchool                         # noqa: E402
from season_year import seasonYearSqlInt                        # noqa: E402
import pro_flag as PF                                           # noqa: E402

_TABLES = (("XC", "results", "meets"), ("TF", "results_tf", "meets_tf"))


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _col(cur, table, col):
    return PF.hasColumn(cur, table, col)


def _teams():
    try:
        from speed_ratings_db import loadTeamLevels, loadProTeams
        return loadTeamLevels()[0], loadProTeams()[0]
    except Exception as exc:                                    # noqa: BLE001
        print(f"  ⚠ team levels unavailable ({type(exc).__name__}: {exc}); "
              f"only tfrrs slugs can name a school team")
        return {}, set()


def _schoolTeam(team_id, slug, school, levels, pro_teams):
    """The row-level test, exactly as the engine hands it to resolvePool."""
    try:
        tid = int(team_id) if team_id is not None else None
    except (TypeError, ValueError):
        tid = None
    if tid == UNATTACHED_TEAM_ID or (tid is not None and tid in pro_teams):
        return False
    if isProTeam(school):
        return False
    return teamLevelOf(team_id, slug, levels) in PF._SCHOOL_TEAM_LEVELS


def faultA(cur, show, person, levels, pro_teams):
    if not _exists(cur, "pro_athlete_season"):
        print("  (a) pro_athlete_season not found -- nothing to count")
        return
    where = "WHERE person_id = %s" if person else ""
    cur.execute(f"SELECT person_id, season, pro_races, round "
                f"FROM pro_athlete_season {where}",
                (person,) if person else None)
    cand = {(int(p), int(s)): (n, r) for p, s, n, r in cur.fetchall()}
    print(f"  (a) {len(cand):,} professional athlete-seasons in the table; "
          f"reading their rows ...")
    if not cand:
        return
    pids = [p for p, _s in cand]
    ays = [s for _p, s in cand]
    n_school, n_class, n_team, n_rows = (Counter(), Counter(), Counter(),
                                         Counter())
    for _sport, table, _meets in _TABLES:
        ay = seasonYearSqlInt(None, "r.date")
        tid = "r.team_id" if _col(cur, table, "team_id") else "NULL::bigint"
        slug = "r.team_slug" if _col(cur, table, "team_slug") else "NULL::text"
        cur.execute(f"""
            SELECT r.person_id, {ay} AS ay, r.grade, {tid}, {slug}, r.school,
                   count(*)
            FROM   {table} r
            JOIN   unnest(%(p)s::bigint[], %(a)s::int[]) AS c(pid, ay)
                   ON c.pid = r.person_id AND c.ay = {ay}
            WHERE  r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}'
            GROUP  BY 1, 2, 3, 4, 5, 6""", {"p": pids, "a": ays})
        for pid, a, grade, team_id, sl, school, n in cur.fetchall():
            k = (int(pid), int(a))
            n_rows[k] += n
            if not _schoolTeam(team_id, sl, school, levels, pro_teams):
                continue
            n_team[k] += n
            if isSchoolGrade(grade):
                n_school[k] += n
            elif isCollegeClass(grade):
                n_class[k] += n
    fix = {}
    if _exists(cur, "grade_fix"):
        cur.execute("""
            SELECT gf.person_id, gf.season, gf.grade
            FROM   grade_fix gf
            JOIN   unnest(%(p)s::bigint[], %(a)s::int[]) AS c(pid, ay)
                   ON c.pid = gf.person_id AND c.ay = gf.season""",
                    {"p": pids, "a": ays})
        fix = {(int(p), int(s)): g for p, s, g in cur.fetchall()}

    vetoed, hand = [], 0
    for k, (races, rnd) in cand.items():
        if not n_team[k]:
            continue
        if not seasonIsSchool(k in fix, fix.get(k), n_school[k], n_class[k]):
            continue
        if isProPerson(k[0], "XC", k[1]) or isProPerson(k[0], "TF", k[1]):
            hand += 1
            continue
        vetoed.append((k, races, rnd))
    by_round = Counter("seeded" if rnd == 0 else "propagated"
                       for _k, _n, rnd in vetoed)
    rows = sum(n_rows[k] for k, _n, _r in vetoed)
    print(f"  (a) STOP BEING PRO on rebuild: {len(vetoed):,} athlete-seasons "
          f"({', '.join(f'{w} {n:,}' for w, n in sorted(by_round.items())) or 'none'}), "
          f"{len({k[0] for k, _n, _r in vetoed}):,} athletes, {rows:,} result "
          f"rows; {hand:,} hand-listed seasons stay professional")
    if vetoed and show:
        names = {}
        if _exists(cur, "athlete_named"):
            cur.execute("SELECT person_id, name FROM athlete_named "
                        "WHERE person_id = ANY(%s)",
                        ([k[0] for k, _n, _r in vetoed[:show]],))
            names = {int(p): n for p, n in cur.fetchall()}
        print("      person      ay  round  pro_races  school-team rows "
              "(school grade / class word)  grade_fix  name")
        for (pid, a), races, rnd in sorted(vetoed, key=lambda v: -v[1])[:show]:
            k = (pid, a)
            rows_ = f"{n_team[k]:,} ({n_school[k]:,} / {n_class[k]:,})"
            print(f"      {pid:<10} {a}  {rnd:>5}  {races:>9}  {rows_:<44}"
                  f"{fix.get(k, '-')!s:<9}  {names.get(pid, '?')}")


def faultB(cur, show):
    """Round-0 seasons whose seed rows, counted once each, fall short."""
    if not _exists(cur, "pro_athlete_season"):
        return
    cur.execute("SELECT person_id, season FROM pro_athlete_season "
                "WHERE round = 0")
    seeded = {(int(p), int(s)) for p, s in cur.fetchall()}
    if not seeded:
        print("  (b) no seeded seasons in the table")
        return
    pids = [p for p, _s in seeded]
    ays = [s for _p, s in seeded]
    once = Counter()
    for _sport, table, meets in _TABLES:
        ay = seasonYearSqlInt(None, "r.date")
        cur.execute(f"""
            SELECT r.person_id, {ay}, count(*)
            FROM   {table} r
            JOIN   unnest(%(p)s::bigint[], %(a)s::int[]) AS c(pid, ay)
                   ON c.pid = r.person_id AND c.ay = {ay}
            WHERE  EXISTS (SELECT 1 FROM {meets} m
                           WHERE m.meet_id = r.meet_id AND m.div_id = r.div_id
                             AND m.meet_name ~* %(inc)s
                             AND m.meet_name !~* %(exc)s)
            GROUP  BY 1, 2""", {"p": pids, "a": ays,
                                "inc": PF._INCLUDE, "exc": PF._EXCLUDE})
        for pid, a, n in cur.fetchall():
            once[(int(pid), int(a))] += n
    # a national-team seed is one race by design; it is not this fault
    short = sorted(k for k in seeded
                   if 0 < once[k] < PF._MIN_PRO_RACES)
    print(f"  (b) {len(seeded):,} seeded seasons; {len(short):,} of them had "
          f"1-{PF._MIN_PRO_RACES - 1} seed RESULTS once each counted -- "
          f"seeded by the per-event multiplication, and left to propagation "
          f"on rebuild")
    for pid, a in short[:show]:
        print(f"      person {pid}  ay {a}  seed results {once[(pid, a)]}")


def factor(cur):
    """(c): the pro factor the athletes' own marks imply, per gender."""
    ay = seasonYearSqlInt(None, "r.date")
    scale = {}
    if _exists(cur, "engine_scale"):
        cur.execute("SELECT pool, sport, pool_mean FROM engine_scale")
        for pool, sport, pm in cur.fetchall():
            scale.setdefault(str(pool).split("|")[0], {})[sport] = float(pm)
    for g in ("m", "f"):
        hs, pro, col = f"hs_{g}", f"pro_{g}", f"college_{g}"
        cur.execute(f"""
            WITH pro_people AS (
                SELECT DISTINCT person_id FROM results_tf
                WHERE  split_part(rating_pool, '|', 1) = %(pro)s
                  AND  speed_rating > 0),
            marks AS (
                SELECT r.person_id, split_part(r.rating_pool, '|', 1) AS pool,
                       round(m.distance_meters) AS d, {ay} AS ay,
                       r.time_seconds::float AS t, r.speed_rating::float AS s
                FROM   results_tf r
                JOIN   pro_people p ON p.person_id = r.person_id
                JOIN   meets_tf m ON m.meet_id = r.meet_id
                                 AND m.div_id = r.div_id
                                 AND m.event_id = r.event_id
                WHERE  r.speed_rating > 0 AND r.time_seconds > 0
                  AND  m.distance_meters > 0
                  AND  split_part(r.rating_pool, '|', 1) IN (%(hs)s, %(pro)s))
            SELECT a.person_id, a.s * a.t / b.t / b.s
            FROM   marks a
            JOIN   marks b ON b.person_id = a.person_id AND b.d = a.d
                          AND abs(b.ay - a.ay) <= 1
            WHERE  a.pool = %(hs)s AND b.pool = %(pro)s""",
                    {"hs": hs, "pro": pro})
        per = defaultdict(list)
        for pid, f in cur.fetchall():
            per[int(pid)].append(float(f))
        meds = sorted(median(v) for v in per.values())
        pm = {p: (scale.get(p, {}).get("XC") or scale.get(p, {}).get("TF"))
              for p in (hs, col, pro)}
        eng = (f"{pm[hs] / pm[col]:.4f}" if pm[hs] and pm[col] else "n/a")
        if meds:
            q = lambda x: meds[min(len(meds) - 1, int(x * len(meds)))]
            print(f"  (c) {pro}: implied by {len(meds):,} athletes' own hs and "
                  f"pro marks: median x{median(meds):.4f} "
                  f"(quartiles {q(0.25):.4f}-{q(0.75):.4f}); engine_scale "
                  f"pm({hs})/pm({col}) = {eng}; pm({pro}) = "
                  f"{pm[pro] or 'n/a'} (the college mean, by _proScaleMap)")
        else:
            print(f"  (c) {pro}: no athlete has hs- and pro-rated marks at one "
                  f"distance in adjacent seasons; engine_scale ratio {eng}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=0,
                    help="print this many athlete-seasons per count")
    ap.add_argument("--person", type=int, default=None,
                    help="one person_id only (count (a))")
    ap.add_argument("--factor", action="store_true",
                    help="also measure the pro HS factor the marks imply")
    args = ap.parse_args()
    levels, pro_teams = _teams()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            print("[school veto] what the 2026-09-30 fix moves (read-only, "
                  "against the tables as they stand):")
            faultA(cur, args.show, args.person, levels, pro_teams)
            if args.person is None:
                faultB(cur, args.show)
            if args.factor:
                factor(cur)
        conn.rollback()
    print("\n  to apply: engine/pro_flag.py --skip-dist --write (step 03), "
          "then the pipeline --from 7\n  (pack, solve, go-live, fill, "
          "boards, pool constants); the backfill (05) is unaffected.")


if __name__ == "__main__":
    main()
