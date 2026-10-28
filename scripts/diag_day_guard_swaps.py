"""
diag_day_guard_swaps.py -- the race days the day guard's near-zero tie-break
(joint_solve._nearZeroSide, XCP_DAY_GUARD_NEAR_ZERO) would read the other
way, from the last go-live's tables. READ-ONLY.

★ WHY (owner, 2026-10-28, Berrien County Fairgrounds): two varsity 5000s
  ran normally and two divisions filed 3218 m ran ~3,700 m; two against
  two, the guard flagged the VARSITY races and the day came out +14%. The
  tie-break gives a close vote to the side nearer zero. This lists, before
  it is switched on, every race day where that would happen.

  An estimate from the stored tables, not the solve: rows stand in for the
  solve's information, the stored race_u for the kept side's day, and the
  race's rows are race_day_effect's for that course and date (every
  distance). The next solve with the switch on prints the real count.

    python scripts/diag_day_guard_swaps.py             # the 40 biggest
    python scripts/diag_day_guard_swaps.py --limit 0   # all of them
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from database import getConn                                   # noqa: E402
from joint_solve import DAY_GUARD_MAJORITY, RACE_DAY_CAP        # noqa: E402

_SQL = """
    WITH s AS (
        SELECT cell_key, race_date, canonical_id, course_name,
               min(meet_id) AS meet_id,
               sum(n_rows) AS flagged_rows,
               sum(division_day * n_rows) / sum(n_rows) AS flagged_day,
               max(race_u) AS race_u,
               array_agg(div_id ORDER BY div_id) AS divs,
               array_agg(division_day ORDER BY div_id) AS div_days
        FROM   race_day_suspect_division
        WHERE  sport = 'XC' OR sport IS NULL
        GROUP  BY 1, 2, 3, 4
    ),
    tot AS (
        SELECT s.cell_key, s.race_date,
               (SELECT sum(e.n_rows) FROM race_day_effect e
                WHERE  e.race_date = s.race_date
                  AND  (e.canonical_id = s.canonical_id
                        OR (s.canonical_id IS NULL AND e.course_name = s.course_name))
               ) AS race_rows
        FROM   s
    )
    SELECT s.race_date, s.course_name, s.meet_id,
           (SELECT meet_name FROM meets m WHERE m.meet_id = s.meet_id LIMIT 1),
           s.flagged_rows, t.race_rows, s.flagged_day, s.race_u, s.divs, s.div_days
    FROM   s JOIN tot t ON t.cell_key = s.cell_key AND t.race_date = s.race_date
"""


def wouldSwap(flagged_rows, race_rows, flagged_day, race_u, div_days,
              majority=DAY_GUARD_MAJORITY, cap=RACE_DAY_CAP):
    """The tie-break's test on one race day's stored numbers. Pure."""
    if not race_rows or race_rows <= flagged_rows:
        return False
    share_kept = (race_rows - flagged_rows) / race_rows
    spread = max(abs(d - flagged_day) for d in div_days)
    return share_kept < majority and spread <= cap and abs(flagged_day) < abs(race_u)


def main(limit=40):
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL)
        rows = cur.fetchall()
    hits = [r for r in rows
            if wouldSwap(r[4], r[5], float(r[6]), float(r[7]), [float(d) for d in r[9]])]
    hits.sort(key=lambda r: -abs(float(r[7])) * (r[5] or 0))
    print(f"[day guard] {len(hits):,} of {len(rows):,} race days with a flagged division "
          f"would read the other side (kept side under {DAY_GUARD_MAJORITY:.0%} of the rows, "
          f"the flagged side one reading nearer zero)\n")
    print(f"  {'date':<10}  {'course':<34} {'meet':<34} {'day now':>8} {'day then':>8} "
          f"{'rows':>9}  divisions flagged now")
    for r in hits[:limit or None]:
        date, course, _mid, meet, f_rows, r_rows, f_day, u, divs, _dd = r
        print(f"  {str(date):<10}  {str(course)[:34]:<34} {str(meet or '')[:34]:<34} "
              f"{100 * float(u):>+7.1f}% {100 * float(f_day):>+7.1f}% "
              f"{f_rows:>4}/{r_rows:<4}  {', '.join(map(str, divs))}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--limit", type=int, default=40)
    main(ap.parse_args().limit)
