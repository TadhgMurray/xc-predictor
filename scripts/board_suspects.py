#!/usr/bin/env python3
"""
board_suspects.py -- the facts behind an all-time board row that looks off.
READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/board_suspects.py                    # the 2026-11-07 sweep's rows
    $P scripts/board_suspects.py 60244611 -2439324138270172062

★ WHY (owner, 2026-11-07, the post-run board audit, CLAUDE.md). A row is
  fixed by finding the class it belongs to, and the class is in facts a
  page does not show together: the row's grade and pool, the person's
  gender, who else ran in that division (boys? adults? a field of two?),
  the person's other races, and whether the same run sits on the other
  feed too. One block per row, plain text, for pasting back.
"""
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# the 2026-11-07 sweep: ms boards' adults, the college feed's high school
# girls, boys in girls' races, the 1991 Kentucky state meet
SWEEP = [
    60244611, 59859856, 59859895, 49141141, 3786724, 36013598, 35666971,   # ms_m adults
    2601284, 38902076, -2263872006933512126, -1963402960020770801,          # ms_f
    -2439324138270172062, -3813372891704153713, -3788890387214794915,        # hs_f, college feed
    35732905, 24687381, 594648,                                              # hs_f, boys' times?
    -2993216687176499846, -1542863673123062788,                              # college_f
    33276717,                                                                # KY state 1991
]

_ROW = """
    SELECT rr.result_id, rr.person_id, rr.pool, rr.school, rr.grade, rr.state, rr.meet_id,
           rr.div_id, rr.race_date, rr.time_seconds, rr.distance, rr.speed_rating,
           COALESCE(m.meet_name, mt.meet_name) AS meet,
           COALESCE(m.course_name, mt.venue_name) AS course,
           (SELECT gender FROM person_gender pg WHERE pg.person_id = rr.person_id) AS gender
    FROM   ranking_results rr
    LEFT   JOIN meets m ON rr.result_id > 0 AND m.div_id = rr.div_id
    LEFT   JOIN meets_tfrrs mt ON rr.result_id < 0 AND mt.meet_id = rr.meet_id AND mt.sport = 'XC'
    WHERE  rr.sport = 'XC' AND rr.result_id = %s
"""

_FIELD = """
    SELECT count(*), count(*) FILTER (WHERE pg.gender = 'M'), count(*) FILTER (WHERE pg.gender = 'F'),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rr.speed_rating),
           min(rr.time_seconds), percentile_cont(0.5) WITHIN GROUP (ORDER BY rr.time_seconds),
           string_agg(DISTINCT split_part(rr.pool, '|', 1), ','),
           count(*) FILTER (WHERE rr.grade IS NULL OR rr.grade = '')
    FROM   ranking_results rr
    LEFT   JOIN person_gender pg ON pg.person_id = rr.person_id
    WHERE  rr.sport = 'XC' AND rr.meet_id = %(m)s AND rr.div_id = %(d)s
      AND  sign(rr.result_id) = sign(%(r)s::bigint)
"""

_OTHER = """
    SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating),
           string_agg(DISTINCT split_part(pool, '|', 1), ','), min(race_date), max(race_date),
           string_agg(DISTINCT grade, ',')
    FROM   ranking_results
    WHERE  sport = 'XC' AND person_id = %(p)s AND result_id <> %(r)s
"""

_COPY = """
    SELECT rr.result_id, rr.race_date, rr.time_seconds, rr.speed_rating, rr.meet_id, rr.div_id
    FROM   ranking_results rr
    WHERE  rr.sport = 'XC' AND rr.person_id = %(p)s AND rr.result_id <> %(r)s
      AND  abs(rr.time_seconds - %(t)s) < 1.0
      AND  abs(rr.race_date - %(day)s::date) <= 31
"""


def _t(s):
    if s is None:
        return "-"
    s = float(s)
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main(argv=None):
    # ★ A SITE TOKEN WORKS TOO (public_ids.py): the boards now show our own
    #   ids, so a result or person token from a page or the API is decoded here
    import public_ids as _pi
    ids = [(_pi.decode(a) or (None, None))[1] if _pi.decode(a) else int(a)
           for a in (argv if argv is not None else sys.argv[1:])] or SWEEP
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        for rid in ids:
            cur.execute(_ROW, (rid,))
            row = cur.fetchone()
            if not row:
                print(f"\n== {rid}: not on the boards' table")
                continue
            (rid, pid, pool, school, grade, st, meet_id, div_id, day, t, dist, rating,
             meet, course, gender) = row
            print(f"\n== {rid}  /athlete/{pid}  feed {'2' if rid < 0 else '1'}  {day}  {meet!r}"
                  f" ({course or 'no course'})  meet {meet_id} div {div_id}")
            print(f"   row: pool {pool}  grade {grade!r}  school {school!r} ({st})  gender {gender}"
                  f"  {_t(t)} for {dist or 0:.0f} m  rating {rating}")
            cur.execute(_FIELD, {"m": meet_id, "d": div_id, "r": rid})
            n, nm, nf, med, tmin, tmed, pools, nogr = cur.fetchone()
            print(f"   division: {n} rated rows ({nm} men, {nf} women known), pools {pools},"
                  f" {nogr} with no grade; fastest {_t(tmin)}, median {_t(tmed)},"
                  f" median rating {med and round(med, 1)}")
            cur.execute(_OTHER, {"p": pid, "r": rid})
            k, omed, opools, d0, d1, grades = cur.fetchone()
            print(f"   their other races: {k}, median rating {omed and round(omed, 1)},"
                  f" pools {opools}, grades {grades}, {d0} to {d1}")
            cur.execute("SELECT side FROM rating_outlier WHERE sport = 'XC' AND result_id = %s",
                        (rid,))
            fl = cur.fetchone()
            print(f"   outlier flag: {fl[0] if fl else 'none'}")
            cur.execute(_COPY, {"p": pid, "r": rid, "t": t, "day": day})
            for c in cur.fetchall():
                print(f"   SAME RUN ELSEWHERE: {c[0]} on {c[1]}, {_t(c[2])}, rating {c[3]},"
                      f" meet {c[4]} div {c[5]}")
        conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
