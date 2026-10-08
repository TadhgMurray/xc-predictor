"""
diag_race_day_suspects.py -- the divisions the day guard dropped from their
race's day, named, biggest first. READ-ONLY.

★ WHY (owner, 2026-10-08). Production pools the race-day term over a whole
  venue-day (XCP_RACE_KEY=venue). At Midlothian James Smith HS Invitational,
  Aug 27 2026, three of eight divisions were stored at 3218 m but run at
  5000 m; their 344 rows read ~40% slow, the pooled day came out "slow
  10.3%", and every CORRECT race that afternoon was credited ~12%. The solve
  now gives such a division zero weight in its day
  (joint_solve.raceDayDivisions) and writes it to race_day_suspect_division
  at go-live (joint_golive.writeSuspectDivisions). These are very likely
  wrong stored distances: reconciled_m is the distance that would make the
  division agree with the rest of its venue that day, snapped_m that
  distance on distance_pin's standard list.

    python scripts/diag_race_day_suspects.py               # the 40 biggest
    python scripts/diag_race_day_suspects.py --limit 0     # all of them
    python scripts/diag_race_day_suspects.py --sport XC --min-rows 10
    python scripts/diag_race_day_suspects.py --csv > suspects.csv

"Biggest" is |excess| x rows: the rating points a wrong distance moves.
own day / race: the division's own race-day reading and the venue-day term
the rest of the field ran (log-time, + = slow). A pack from before
2026-10-08 has no (meet_id, div_id): the division is then (venue cell,
pool) and the meet is found through sample_result_id.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from database import getConn                                   # noqa: E402

# ! to_jsonb(m) ->> 'division', NOT m.division: a column a database lacks
#   reads NULL instead of failing the whole listing
_SQL = """
    WITH s AS (
        SELECT s.*,
               COALESCE(s.meet_id, rx.meet_id, rt.meet_id) AS meet,
               COALESCE(s.div_id,  rx.div_id,  rt.div_id)  AS div
        FROM   race_day_suspect_division s
        LEFT   JOIN results    rx ON s.sport = 'XC' AND rx.result_id = s.sample_result_id
        LEFT   JOIN results_tf rt ON s.sport = 'TF' AND rt.result_id = s.sample_result_id
        WHERE  (%(sport)s = '' OR s.sport = %(sport)s)
          AND  s.n_rows >= %(min_rows)s
    )
    SELECT s.course_name, s.cell_key, s.race_date, s.sport, s.meet, s.div,
           COALESCE(mx.meet_name, mt.meet_name, mf.meet_name) AS meet_name,
           COALESCE(to_jsonb(mx) ->> 'division',
                    mt.division_distances -> s.div::text ->> 'name',
                    to_jsonb(mf) ->> 'division',
                    mf.event_short) AS division,
           s.pool, s.n_rows, s.distance_m, s.division_day, s.race_u, s.excess,
           s.reconciled_m, s.snapped_m, s.division_source, s.solve_source,
           s.sample_result_id, s.last_updated
    FROM   s
    LEFT   JOIN LATERAL (SELECT m.* FROM meets m
                         WHERE s.sport = 'XC' AND m.div_id = s.div LIMIT 1) mx ON TRUE
    LEFT   JOIN LATERAL (SELECT m.* FROM meets_tfrrs m
                         WHERE s.sport = 'XC' AND m.meet_id = s.meet
                           AND m.sport = 'XC' LIMIT 1) mt ON TRUE
    LEFT   JOIN LATERAL (SELECT m.* FROM meets_tf m
                         WHERE s.sport = 'TF' AND m.meet_id = s.meet
                           AND m.div_id = s.div LIMIT 1) mf ON TRUE
    ORDER  BY abs(s.excess) * s.n_rows DESC, s.race_date, s.cell_key
"""

_HEAD = ("course_name", "cell_key", "race_date", "sport", "meet_id", "div_id",
         "meet_name", "division", "pool", "n_rows", "distance_m", "division_day",
         "race_u", "excess", "reconciled_m", "snapped_m", "division_source",
         "solve_source", "sample_result_id", "last_updated")


def fetch(cur, sport="", min_rows=1):
    cur.execute("SELECT to_regclass('race_day_suspect_division')")
    if cur.fetchone()[0] is None:
        return None
    cur.execute(_SQL, {"sport": sport, "min_rows": int(min_rows)})
    return [dict(zip(_HEAD, r)) for r in cur.fetchall()]


def _pct(x):
    return "      -" if x is None else f"{100 * float(x):+6.1f}%"


def _m(x):
    return "    -" if x is None else f"{float(x):5.0f}"


def render(rows, limit=40):
    if not rows:
        return ["no divisions in race_day_suspect_division -- the day guard "
                "flagged nothing on the last go-live"]
    n_r = sum(int(r["n_rows"]) for r in rows)
    days = len({(r["cell_key"].partition("@e")[0].rpartition(":d")[0], r["race_date"])
                for r in rows})
    out = [f"{len(rows):,} divisions ({n_r:,} rows) on {days:,} race day{'' if days == 1 else 's'}, given "
           f"zero weight in their race's day; last written {rows[0]['last_updated']}, "
           f"divisions from the {rows[0]['division_source']} key, flags from the "
           f"{rows[0]['solve_source']} solve. Biggest (|excess| x rows) first:",
           f"{'date':<10} {'course':<34} {'meet / division':<46} {'pool':<8}"
           f"{'rows':>5} {'own day':>8} {'race':>8} {'stored':>6} {'->':>6} {'snap':>5}"]
    for r in rows[: (limit or len(rows))]:
        meet = (r["meet_name"] or f"meet {r['meet_id']}")[:30]
        div = (r["division"] or (f"div {r['div_id']}" if r["div_id"] else ""))[:14]
        out.append(f"{str(r['race_date']):<10} {(r['course_name'] or r['cell_key'])[:34]:<34} "
                   f"{(meet + ' / ' + div)[:46]:<46} {(r['pool'] or '')[:8]:<8}"
                   f"{int(r['n_rows']):>5} {_pct(r['division_day']):>8} {_pct(r['race_u']):>8} "
                   f"{_m(r['distance_m']):>6} {_m(r['reconciled_m']):>6} {_m(r['snapped_m']):>5}")
    if limit and len(rows) > limit:
        out.append(f"... {len(rows) - limit:,} more (--limit 0 for all)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", default="", choices=("", "XC", "TF"))
    ap.add_argument("--min-rows", type=int, default=1)
    ap.add_argument("--limit", type=int, default=40, help="0 = all")
    ap.add_argument("--csv", action="store_true", help="every row, as CSV on stdout")
    args = ap.parse_args(argv)
    with getConn() as conn, conn.cursor() as cur:
        rows = fetch(cur, args.sport, args.min_rows)
    if rows is None:
        print("race_day_suspect_division does not exist yet: it is written by the "
              "go-live of a solve with the day guard (engine/run_joint.py --golive)")
        return 1
    if args.csv:
        wr = csv.DictWriter(sys.stdout, fieldnames=list(_HEAD))
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
        return 0
    for line in render(rows, args.limit):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
