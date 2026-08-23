"""
diagnose_rating.py -- is it the normalized time, or the rating?

    python engine/diagnose_rating.py --person 29346285 --pool ms_m
    python engine/diagnose_rating.py --only 3
    python engine/diagnose_rating.py --sql          # print the SQL, run none

Run from the PROJECT ROOT. Reads only; nothing here writes.

THE CHAIN, from speed_ratings.py:

    time_seconds
      -> normalized_time = time_seconds * (5000 / distance) ** k
      -> adjusted        = normalized_time / (1 + difficulty)
      -> speed_rating    = pool_mean / adjusted * 100

    pool_mean = the ARITHMETIC MEAN of ability, in seconds, over every valid
                athlete in the pool  (speed_ratings.poolMeans)

Four things can be wrong and they have four different fixes, so these cut the
chain at each link rather than judging the end of it. Run them in order and
stop when one of them answers.

★ Q3 IS THE ONE THAT SETTLES IT, and it does NOT work by comparing two
  percentiles. A wrong pool_mean is a constant multiplier over the whole pool,
  and a constant multiplier leaves every rank exactly where it was -- rank is
  blind to precisely this failure. Q3 compares where a result SITS (a
  percentile) against what it was RATED (an absolute number), with a
  median-anchored rating beside it for contrast.
"""

import sys
import argparse

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")


# ==================================================================== #
#  THE QUERIES
# ==================================================================== #
#
# Each carries what it asks and how to read the answer, because a number
# without a reading rule is how a diagnostic becomes a Rorschach test.

QUERIES = [

    dict(n=0, title="Column names, so the rest of these actually run",
         reading="Different vintages of this schema differ. If a column below "
                 "is missing, edit the query rather than trusting a NULL.",
         sql="""
        SELECT table_name, column_name, data_type
        FROM   information_schema.columns
        WHERE  table_name IN ('results_tf', 'results', 'athlete_season',
                              'pair_athlete_season', 'ranking_results')
          AND  column_name IN ('time_seconds', 'normalized_time',
                               'speed_rating', 'distance', 'distance_meters',
                               'event_id', 'is_field', 'is_relay', 'pool',
                               'ability', 'mean_rating', 'person_id')
        ORDER  BY table_name, column_name
    """),

    dict(n=1, title="The athlete's rows, end to end -- START HERE",
         reading="★ implied_pool_mean is the rating inverted: rating = "
                 "pool_mean / adjusted * 100, so pool_mean = rating * "
                 "normalized_time / 100. That is ONE CONSTANT for the whole "
                 "pool, so it must come out the SAME on every row.\n"
                 "      same on every row -> the per-race arithmetic is sound "
                 "and the constant itself is the question. Go to Q5.\n"
                 "      varies row to row -> something per-race is wrong. "
                 "Go to Q2.",
         sql="""
        SELECT r.result_id, r.date, r.event_id, r.time_seconds,
               r.normalized_time, r.speed_rating,
               round((r.normalized_time / NULLIF(r.time_seconds, 0))::numeric,
                     4) AS norm_factor,
               round((5000.0 / power(r.normalized_time
                                     / NULLIF(r.time_seconds, 0),
                                     1.0 / 1.06))::numeric, 1) AS distance_used,
               round((r.speed_rating * r.normalized_time / 100.0)::numeric, 1)
                   AS implied_pool_mean
        FROM   results_tf r
        WHERE  r.person_id = %(person)s
        ORDER  BY r.date, r.time_seconds
    """),

    dict(n=2, title="Is the distance the normaliser used the one that was run?",
         reading="distance_used comes only from the two time columns, so it "
                 "is what the normaliser ACTUALLY did -- not what any "
                 "metadata claims. An 800m that reads 1600 here is a distance "
                 "bug and nothing else.\n"
                 "    ⚠ 1/1.06 assumes the flat placeholder exponent. With a "
                 "fitted curve this recovery is approximate: within a few "
                 "percent is fine, double or half is not.",
         sql="""
        SELECT round((5000.0 / power(r.normalized_time
                                     / NULLIF(r.time_seconds, 0),
                                     1.0 / 1.06))::numeric, -1) AS distance_used,
               count(*)                                  AS n,
               round(min(r.time_seconds)::numeric, 1)    AS fastest_raw,
               round(avg(r.normalized_time)::numeric, 1) AS avg_norm,
               round(avg(r.speed_rating)::numeric, 1)    AS avg_rating
        FROM   results_tf r
        WHERE  r.meet_id IN (SELECT meet_id FROM results_tf
                             WHERE person_id = %(person)s)
          AND  r.normalized_time IS NOT NULL AND r.time_seconds > 0
        GROUP  BY 1
        ORDER  BY 1
    """),

    dict(n=3, title="★ THE DECIDING ONE: percentile against absolute rating",
         reading="⚠ Comparing two PERCENTILES cannot answer this, and the "
                 "first version of this query did. A wrong pool_mean is a "
                 "constant multiplier over the pool, and a constant "
                 "multiplier leaves every rank where it was.\n"
                 "      time_pctile 99.9, rating 168 -> consistent. He really "
                 "is that far ahead; the question is who else is in the pool.\n"
                 "      time_pctile 70, rating 168   -> a middling run cannot "
                 "rate 168. If rating_on_median_anchor lands near 100-110, "
                 "the mean anchor is the whole story. Go to Q5.\n"
                 "      median anchor ~= speed_rating -> the anchor is "
                 "honest. Go to Q2.",
         sql="""
        WITH pool_rows AS (
            SELECT rr.result_id, rr.normalized_time, rr.speed_rating
            FROM   results_tf rr
            JOIN   ranking_results k ON k.result_id = rr.result_id
                                    AND k.sport = 'TF'
            WHERE  k.pool = %(pool)s
              AND  rr.normalized_time IS NOT NULL
              AND  rr.speed_rating IS NOT NULL
        ), stats AS (
            SELECT percentile_cont(0.50) WITHIN GROUP
                       (ORDER BY normalized_time) AS nt_median,
                   avg(normalized_time)           AS nt_mean
            FROM   pool_rows
        ), ranked AS (
            SELECT result_id, normalized_time, speed_rating,
                   100 * (1 - percent_rank() OVER (ORDER BY normalized_time))
                       AS time_pctile
            FROM   pool_rows
        )
        SELECT r.result_id, r.date, r.time_seconds,
               round(k.normalized_time::numeric, 1)  AS normalized_time,
               round(k.speed_rating::numeric, 1)     AS speed_rating,
               round(k.time_pctile::numeric, 2)      AS time_pctile,
               round((s.nt_median / k.normalized_time * 100)::numeric, 1)
                   AS rating_on_median_anchor,
               round((s.nt_mean / s.nt_median)::numeric, 3) AS anchor_inflation
        FROM   ranked k
        JOIN   results_tf r ON r.result_id = k.result_id
        CROSS  JOIN stats s
        WHERE  r.person_id = %(person)s
        ORDER  BY r.date
    """),

    dict(n=4, title="The whole pool, at each link of the chain",
         reading="Read nt_median against times you know. If the median "
                 "normalised time in this pool is a 15-minute 5000m the pool "
                 "is ordinary and the ratings are wrong; if it is a "
                 "25-minute 5000m the pool really does contain everybody and "
                 "the ratings are only reporting that.",
         sql="""
        SELECT k.pool, count(*) AS n,
               round(percentile_cont(0.50) WITHIN GROUP
                     (ORDER BY r.normalized_time)::numeric, 1) AS nt_median,
               round(percentile_cont(0.01) WITHIN GROUP
                     (ORDER BY r.normalized_time)::numeric, 1) AS nt_fastest_1pct,
               round(avg(r.normalized_time)::numeric, 1)       AS nt_mean,
               round(percentile_cont(0.50) WITHIN GROUP
                     (ORDER BY r.speed_rating)::numeric, 1)    AS rating_median,
               round(percentile_cont(0.99) WITHIN GROUP
                     (ORDER BY r.speed_rating)::numeric, 1)    AS rating_p99,
               round((avg(r.normalized_time)
                      / percentile_cont(0.50) WITHIN GROUP
                        (ORDER BY r.normalized_time))::numeric, 3)
                   AS mean_over_median
        FROM   results_tf r
        JOIN   ranking_results k ON k.result_id = r.result_id AND k.sport = 'TF'
        WHERE  r.normalized_time IS NOT NULL AND r.speed_rating IS NOT NULL
        GROUP  BY k.pool
        ORDER  BY k.pool
    """),

    dict(n=5, title="The pool mean itself -- what every rating is divided by",
         reading="★ mean_over_median is the diagnosis. At 1.00 the mean is "
                 "honest, and a pool that still looks wrong is wrong in WHO "
                 "IS IN IT. Well above 1.00 and a handful of solved abilities "
                 "are moving the anchor and every rating in the pool with it.\n"
                 "    rating_if_median is what a 180 becomes on a median "
                 "anchor -- ratings scale linearly with it.",
         sql="""
        SELECT pool, count(*) AS athletes,
               round(avg(ability)::numeric, 0) AS mean,
               round(percentile_cont(0.50) WITHIN GROUP
                     (ORDER BY ability)::numeric, 0) AS median,
               round(percentile_cont(0.99) WITHIN GROUP
                     (ORDER BY ability)::numeric, 0) AS p99,
               round(max(ability)::numeric, 0) AS worst,
               round((avg(ability) / percentile_cont(0.50) WITHIN GROUP
                      (ORDER BY ability))::numeric, 3) AS mean_over_median,
               round((180 * percentile_cont(0.50) WITHIN GROUP
                      (ORDER BY ability) / avg(ability))::numeric, 0)
                   AS rating_if_median
        FROM   pair_athlete_season
        WHERE  ability IS NOT NULL AND ability > 0
        GROUP  BY pool
        ORDER  BY pool
    """),

    dict(n=6, title="If Q5 shows a tail: who is in it",
         reading="⚠ Read the names. A genuinely slow twelve-year-old means "
                 "the pool is wide and the fix is upstream, in who is "
                 "admitted. An ability of 40,000 seconds means a solve that "
                 "failed, and the fix is a robust statistic. Counts cannot "
                 "tell these apart.",
         sql="""
        SELECT person_id, season, races,
               round(ability::numeric, 0)         AS ability_seconds,
               round(rating_seasonal::numeric, 1) AS rating
        FROM   pair_athlete_season
        WHERE  pool = %(pool)s AND ability IS NOT NULL
        ORDER  BY ability DESC
        LIMIT  25
    """),

    dict(n=7, title="Sanity: does the stored season rating reproduce?",
         reading="rating = pool_mean / ability * 100. If stored and "
                 "recomputed differ, the stored value is stale -- a rebuild "
                 "ran on one side only, and nothing above is measuring what "
                 "the site is serving.",
         sql="""
        WITH m AS (
            SELECT pool, avg(ability) AS pool_mean
            FROM   pair_athlete_season
            WHERE  ability IS NOT NULL AND ability > 0
            GROUP  BY pool
        )
        SELECT a.person_id, a.pool, a.season, a.races,
               round(a.ability::numeric, 1)   AS ability,
               round(m.pool_mean::numeric, 1) AS pool_mean,
               round(a.rating_seasonal::numeric, 2) AS stored,
               round((m.pool_mean / a.ability * 100)::numeric, 2) AS recomputed
        FROM   pair_athlete_season a
        JOIN   m ON m.pool = a.pool
        WHERE  a.person_id = %(person_text)s
    """),
    dict(n=8, title="★ ANCHOR MISMATCH: rated against another pool's scale",
         reading="★ THE CONFIRMED BUG. An athlete's rows are normalised to "
                 "one pool's anchor (ms 3200m, hs 5000m) but the solve puts "
                 "the athlete-season in another pool and divides by THAT "
                 "pool's mean. A 3200m-anchored ability over a "
                 "5000m-anchored mean is inflated by about 1.64x, for free.\n"
                 "    Person 29346285: ability 675.2s, hs_m mean 1236.4s, "
                 "rating 187. Both his races recover an exponent of 1.11 at "
                 "a 3200m anchor and an impossible 0.70-0.85 at 5000m -- so "
                 "he was normalised as ms and rated as hs. On one anchor he "
                 "rates about 112.\n"
                 "    ⚠ The fingerprint is an ability far BELOW what its pool "
                 "can produce -- too fast to be real, because it is measured "
                 "over a shorter distance. Read the count: a handful is a "
                 "pooling edge case, thousands is a systematic seam.",
         sql="""
        WITH b AS (
            SELECT pool,
                   percentile_cont(0.001) WITHIN GROUP (ORDER BY ability)
                       AS floor_001,
                   percentile_cont(0.50) WITHIN GROUP (ORDER BY ability)
                       AS median
            FROM   pair_athlete_season
            WHERE  ability IS NOT NULL AND ability > 0
            GROUP  BY pool
        )
        SELECT a.pool,
               count(*)                                   AS impossible,
               round(min(a.ability)::numeric, 0)          AS fastest,
               round(max(b.floor_001)::numeric, 0)        AS pool_p001,
               round(max(b.median)::numeric, 0)           AS pool_median,
               round(max(a.rating_seasonal)::numeric, 1)  AS worst_rating
        FROM   pair_athlete_season a
        JOIN   b ON b.pool = a.pool
        WHERE  a.ability IS NOT NULL AND a.ability > 0
          -- ! BELOW THE POOL'S OWN 0.1st PERCENTILE, not below a number
          --   somebody chose. Each pool sets its own floor, so this needs no
          --   opinion about how fast a middle schooler can be.
          AND  a.ability < b.floor_001
        GROUP  BY a.pool
        ORDER  BY count(*) DESC
    """),

    dict(n=9, title="The worst of them, by name",
         reading="Each of these is an athlete-season whose ability is faster "
                 "than the 0.1st percentile of its own pool. Check one on the "
                 "site: if their races are short TF events and their grade is "
                 "blank, it is the anchor seam.",
         sql="""
        WITH b AS (
            SELECT pool, percentile_cont(0.001) WITHIN GROUP
                   (ORDER BY ability) AS floor_001
            FROM   pair_athlete_season
            WHERE  ability IS NOT NULL AND ability > 0
            GROUP  BY pool
        )
        SELECT a.person_id, a.pool, a.season, a.races,
               round(a.ability::numeric, 1)         AS ability,
               round(b.floor_001::numeric, 0)       AS pool_floor,
               round(a.rating_seasonal::numeric, 1) AS rating
        FROM   pair_athlete_season a
        JOIN   b ON b.pool = a.pool
        WHERE  a.ability IS NOT NULL AND a.ability > 0
          AND  a.ability < b.floor_001
        ORDER  BY a.rating_seasonal DESC NULLS LAST
        LIMIT  25
    """),
]


# ==================================================================== #
#  RUNNING AND PRINTING
# ==================================================================== #

def render(cols, rows, limit=40):
    """A plain aligned table. No dependencies, no truncation of numbers."""
    if not rows:
        return "    (no rows)"
    shown = rows[:limit]
    cells = [[("" if v is None else str(v)) for v in r] for r in shown]
    widths = [max(len(c), *(len(row[i]) for row in cells))
              for i, c in enumerate(cols)]
    out = ["    " + "  ".join(c.rjust(w) for c, w in zip(cols, widths)),
           "    " + "  ".join("-" * w for w in widths)]
    for row in cells:
        out.append("    " + "  ".join(v.rjust(w) for v, w in zip(row, widths)))
    if len(rows) > limit:
        out.append(f"    ... {len(rows) - limit:,} more rows")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Cut the rating chain at each link and report where it "
                    "breaks.")
    ap.add_argument("--person", type=int, default=29346285,
                    help="the athlete to explain (default: the reported case)")
    ap.add_argument("--pool", default="ms_m")
    ap.add_argument("--only", type=int, action="append",
                    help="run just these query numbers; repeatable")
    ap.add_argument("--sql", action="store_true",
                    help="print the SQL and run nothing")
    args = ap.parse_args()

    wanted = [q for q in QUERIES
              if args.only is None or q["n"] in args.only]

    if args.sql:
        for q in wanted:
            print(f"\n-- Q{q['n']}. {q['title']}\n{q['sql'].strip()};")
        return

    params = {"person": args.person, "person_text": str(args.person),
              "pool": args.pool}

    from database import getConn
    import psycopg2
    import psycopg2.extras

    print(f"\nperson {args.person}, pool {args.pool}")
    with getConn() as conn:
        for q in wanted:
            print(f"\n{'=' * 72}\nQ{q['n']}. {q['title']}\n{'=' * 72}")
            print(f"    {q['reading']}\n")
            # ! ONE CURSOR PER QUERY, INSIDE A SAVEPOINT-LESS ROLLBACK. A
            #   missing column aborts the transaction, and without the
            #   rollback every later query then fails with "current
            #   transaction is aborted" -- which reads as eight broken
            #   queries instead of one.
            try:
                with conn.cursor(
                        cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(q["sql"], params)
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description]
                print(render(cols, [[r[c] for c in cols] for r in rows]))
            except psycopg2.Error as exc:
                conn.rollback()
                print(f"    !! {str(exc).strip().splitlines()[0]}")
                print("    (run --only 0 to check this schema's column names)")

    print("\nRead Q1 first: if implied_pool_mean is identical on every row, "
          "the\nper-race arithmetic is sound and Q5 is where the answer is.")


if __name__ == "__main__":
    main()
