"""
diag_merged_ids.py -- person_ids that hold two people. Issue 68. Read-only.

    python scripts/diag_merged_ids.py            # counts + samples
    python scripts/diag_merged_ids.py --limit 40

Three tells, cheapest first:
  1. BOTH GENDERS: a person_id whose athlete rows carry 'M' and 'F'. The
     data contradicts itself; no inference involved.
  2. TWICE IN ONE RACE: the same person_id with two finishing rows in one
     (meet, division). One body cannot finish a race twice -- unless the
     race itself is stored twice, which result_twin (twin_flag.py) now
     files; run that first so this tell means what it says.
  3. TWO MEETS ON ONE DAY, both feeds, different meet ids that are NOT
     canon-linked. Rarely a real double; usually two people or one meet
     under two ids.
Nothing here writes. The counts say how big the problem is before a
splitting rule is designed (see the note under #68).
"""
import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

BOTH_GENDERS = """
    SELECT person_id, count(*) AS n_rows,
           string_agg(DISTINCT gender, '/') AS genders,
           string_agg(DISTINCT school, ' | ') AS schools
    FROM   athletes
    WHERE  person_id IS NOT NULL AND gender IN ('M', 'F')
    GROUP  BY person_id
    HAVING count(DISTINCT gender) > 1
"""

TWICE_IN_RACE = {
    "XC": """
        SELECT person_id, meet_id, div_id, count(*) AS n
        FROM   results
        WHERE  person_id IS NOT NULL AND time_seconds IS NOT NULL
          AND  time_seconds < 100000
          AND  NOT EXISTS (SELECT 1 FROM result_twin x
                           WHERE x.sport = 'XC' AND x.result_id = results.result_id)
        GROUP  BY person_id, meet_id, div_id
        HAVING count(*) > 1
    """,
    "TF": """
        SELECT person_id, meet_id, event_id, div_id, count(*) AS n
        FROM   results_tf
        WHERE  person_id IS NOT NULL AND time_seconds IS NOT NULL
          AND  COALESCE(is_relay, 0) = 0
          AND  NOT EXISTS (SELECT 1 FROM result_twin x
                           WHERE x.sport = 'TF' AND x.result_id = results_tf.result_id)
        GROUP  BY person_id, meet_id, event_id, div_id
        HAVING count(*) > 1
    """,
}

TWO_MEETS_ONE_DAY = """
    SELECT a.person_id, a.date, a.meet_id, b.meet_id
    FROM   results a
    JOIN   results b ON b.person_id = a.person_id AND b.date = a.date
                    AND b.meet_id > a.meet_id
    WHERE  a.person_id IS NOT NULL
      AND  a.time_seconds < 100000 AND b.time_seconds < 100000
      AND  COALESCE(a.canon_meet_id, -1) <> COALESCE(b.canon_meet_id, -2)
    LIMIT  %(lim)s
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('result_twin')")
        if cur.fetchone()[0] is None:
            print("  result_twin absent: run engine/twin_flag.py --write first,\n"
                  "  or tell 2 counts stored-twice races as merged people")
            cur.execute("CREATE TEMP TABLE result_twin (sport text, result_id bigint)")

        cur.execute(f"SELECT count(*) FROM ({BOTH_GENDERS}) s")
        print(f"\n  1. person_ids carrying both genders: {cur.fetchone()[0]:,}")
        cur.execute(BOTH_GENDERS + " ORDER BY n_rows DESC LIMIT %(lim)s",
                    {"lim": args.limit})
        for pid, n, g, schools in cur.fetchall():
            print(f"     {pid:>12}  {n:>4} rows  {g}  {schools[:70]}")

        for sport, sql in TWICE_IN_RACE.items():
            cur.execute(f"SELECT count(*), coalesce(sum(n), 0) FROM ({sql}) s")
            k, n = cur.fetchone()
            print(f"\n  2. [{sport}] person twice in one race: {k:,} races, {int(n):,} rows")
            cur.execute(sql + " ORDER BY n DESC LIMIT %(lim)s", {"lim": args.limit})
            for row in cur.fetchall():
                print("     " + "  ".join(str(v) for v in row))

        cur.execute(TWO_MEETS_ONE_DAY, {"lim": args.limit})
        rows = cur.fetchall()
        print(f"\n  3. two unlinked XC meets on one day (first {args.limit}):")
        for row in rows:
            print("     " + "  ".join(str(v) for v in row))
    print()


if __name__ == "__main__":
    main()
