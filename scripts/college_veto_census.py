# Project: xc-predictor / scripts
# File:    college_veto_census.py
# Purpose: BEFORE the rebuild, count what the 2026-09-29 pooling fix moves.
#
#     python scripts/college_veto_census.py
#     python scripts/college_veto_census.py --show 20      # and examples
#
# Read-only: one READ ONLY transaction, no temp tables, nothing written.
#
# ★ TWO FAULTS, TWO COUNTS (docs/ISSUES-RUNNING.md, 2026-09-29 blocker).
#
#   (a) A 'college' season verdict on a school season before the person's
#       first collegiate season (Tayvon Kitchen, TF ay 2024). Counted
#       against the athlete_season_level on the server NOW, with the rule
#       season_level.refuseCollegeBeforeCollege will apply when it is
#       rebuilt: these are the athlete-seasons whose verdict goes to NULL.
#       ⚠ ONE APPROXIMATION: the table as it stands has no grade counts, so
#         the season's grade is read from ALL of its rows here, where the
#         rebuild reads only the rows that voted. The two differ only for a
#         season whose non-voting rows carry a different kind of grade.
#
#   (b) The backfill's private July seam (Ajani Salcido, 2021-07-02). A
#       July row was looked up on the season it OPENS rather than the one it
#       closes. Counted: July rows whose two keys give the backfill
#       different facts -- a different grade_fix verdict or a different
#       unanimous season level -- and, of those, the ones where the old key
#       read a college class and the right one a school grade (Salcido's
#       exact shape, and the one that moves a row's anchor furthest).

import argparse
import sys
from collections import Counter

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                                   # noqa: E402
from normalize_distance import isSchoolGrade, isCollegeClass   # noqa: E402
from season_level import seasonIsSchool                        # noqa: E402
from season_year import seasonYearSqlInt                       # noqa: E402

_TABLES = (("XC", "results"), ("TF", "results_tf"))


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def faultA(cur, show):
    """Athlete-seasons whose stored 'college' verdict the rebuild refuses."""
    if not _exists(cur, "athlete_season_level"):
        print("  (a) athlete_season_level not found -- nothing to count")
        return
    have_gf = _exists(cur, "grade_fix")
    have_cfs = _exists(cur, "college_first_season")
    if not have_cfs:
        print("  (a) ⚠ college_first_season not found: every college verdict "
              "on a school grade counts as refused")
    gf_cols = ("gf.person_id IS NOT NULL, gf.grade" if have_gf
               else "FALSE, NULL::text")
    gf_join = ("LEFT JOIN grade_fix gf ON gf.person_id = asl.person_id "
               "AND gf.season = asl.ay" if have_gf else "")
    before = "TRUE"
    if have_cfs:
        first_ay = seasonYearSqlInt(None, "c.first_date::text")
        before = (f"NOT EXISTS (SELECT 1 FROM college_first_season c "
                  f"WHERE c.person_id = asl.person_id AND {first_ay} <= asl.ay)")
    cur.execute(f"""
        SELECT asl.person_id, asl.ay, asl.sport, asl.unanimous, {gf_cols}
        FROM   athlete_season_level asl
        {gf_join}
        WHERE  asl.level = 'college' AND {before}""")
    cand = cur.fetchall()
    seasons = sorted({(p, a) for p, a, *_ in cand})
    print(f"  (a) {len(cand):,} stored college verdicts sit before the "
          f"person's first collegiate season ({len(seasons):,} "
          f"athlete-seasons); reading their grades ...")
    if not seasons:
        return
    pids = [p for p, _a in seasons]
    ays = [a for _p, a in seasons]
    school, klass, rows = Counter(), Counter(), Counter()
    for sport, table in _TABLES:
        ay = seasonYearSqlInt(None, "r.date")
        cur.execute(f"""
            SELECT r.person_id, {ay} AS ay, r.grade, count(*)
            FROM   {table} r
            JOIN   unnest(%(p)s::bigint[], %(a)s::int[]) AS c(pid, ay)
                   ON c.pid = r.person_id AND c.ay = {ay}
            WHERE  r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}'
            GROUP  BY 1, 2, 3""", {"p": pids, "a": ays})
        for pid, a, grade, n in cur.fetchall():
            rows[(pid, a, sport)] += n
            if isSchoolGrade(grade):
                school[(pid, a, sport)] += n
            elif isCollegeClass(grade):
                klass[(pid, a, sport)] += n

    def counts(pid, a, sport):
        sports = ("XC", "TF") if sport == "ALL" else (sport,)
        return (sum(school[(pid, a, s)] for s in sports),
                sum(klass[(pid, a, s)] for s in sports),
                sum(rows[(pid, a, s)] for s in sports))

    refused, by_sport, n_rows, unan = [], Counter(), 0, 0
    for pid, a, sport, unanimous, has_fix, fix_grade in cand:
        ns, nc, nr = counts(pid, a, sport)
        if seasonIsSchool(has_fix, fix_grade, ns, nc):
            refused.append((pid, a, sport, fix_grade, ns, nc, nr))
            by_sport[sport] += 1
            unan += bool(unanimous)
            if sport != "ALL":
                n_rows += nr
    people = {(p, a) for p, a, *_ in refused}
    print(f"  (a) REFUSED on rebuild: {len(refused):,} verdict rows "
          f"({', '.join(f'{s} {n:,}' for s, n in sorted(by_sport.items()))}), "
          f"{unan:,} of them unanimous (the backfill reads only those); "
          f"{len(people):,} athlete-seasons; {n_rows:,} result rows sit in "
          f"a refused per-sport season")
    for pid, a, sport, fg, ns, nc, nr in refused[:show]:
        print(f"      person {pid}  ay {a}  {sport:<3}  grade_fix {fg!s:<5} "
              f"school-graded rows {ns:,}  class-worded {nc:,}  of {nr:,}")


def faultB(cur, show):
    """July rows the backfill's old July seam keyed on the wrong season."""
    have_gf = _exists(cur, "grade_fix")
    have_asl = _exists(cur, "athlete_season_level")
    if not (have_gf or have_asl):
        print("  (b) neither grade_fix nor athlete_season_level -- the seam "
              "had nothing to mis-key")
        return
    for sport, table in _TABLES:
        y = "substring(r.date, 1, 4)::int"
        parts, sel = [], []
        if have_gf:
            parts.append(f"""
                LEFT JOIN grade_fix a ON a.person_id = r.person_id
                                     AND a.season = {y}
                LEFT JOIN grade_fix b ON b.person_id = r.person_id
                                     AND b.season = {y} - 1""")
            sel += ["a.grade", "a.level", "b.grade", "b.level"]
        else:
            sel += ["NULL::text"] * 4
        if have_asl:
            # the backfill's own lookup: this sport's unanimous verdict,
            # else the combined one
            for tag, off in (("sa", ""), ("sb", " - 1")):
                for sp, s_tag in ((sport, "s"), ("ALL", "a")):
                    parts.append(f"""
                LEFT JOIN athlete_season_level {tag}{s_tag}
                       ON {tag}{s_tag}.person_id = r.person_id
                      AND {tag}{s_tag}.ay = {y}{off}
                      AND {tag}{s_tag}.sport = '{sp}'
                      AND {tag}{s_tag}.unanimous""")
            sel += ["COALESCE(sas.level, saa.level)",
                    "COALESCE(sbs.level, sba.level)"]
        else:
            sel += ["NULL::text"] * 2
        cur.execute(f"""
            SELECT {", ".join(sel)}, count(*), count(DISTINCT r.person_id)
            FROM   {table} r
            {"".join(parts)}
            WHERE  r.person_id IS NOT NULL
              AND  r.date ~ '^(19|20)[0-9]{{2}}-07-'
            GROUP  BY 1, 2, 3, 4, 5, 6""")
        total = differ = crossed = 0
        shapes = Counter()
        for ag, al, bg, bl, sa, sb, n, _people in cur.fetchall():
            total += n
            if (ag, al, sa) == (bg, bl, sb):
                continue
            differ += n
            if isCollegeClass(ag) and isSchoolGrade(bg):
                crossed += n
            shapes[(ag, al, sa, bg, bl, sb)] += n
        print(f"  (b) {sport}: {total:,} July rows; {differ:,} read different "
              f"facts on the old July key than on season_year's; {crossed:,} "
              f"of them a college class where the right season has a school "
              f"grade (Salcido's shape)")
        for (ag, al, sa, bg, bl, sb), n in shapes.most_common(show):
            print(f"      old key grade_fix {ag!s}/{al!s} season {sa!s}  ->  "
                  f"right key {bg!s}/{bl!s} season {sb!s}   {n:,} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=0,
                    help="print this many examples per count")
    args = ap.parse_args()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            print("[college veto] what the 2026-09-29 fix moves "
                  "(read-only, against the tables as they stand):")
            faultA(cur, args.show)
            faultB(cur, args.show)
        conn.rollback()
    print("\n  to apply: engine/college_flag.py --write; engine/season_level.py "
          "--write;\n  then pipeline --from 5 (backfill, anchor repair, pack, "
          "solve, fill, boards).")


if __name__ == "__main__":
    main()
