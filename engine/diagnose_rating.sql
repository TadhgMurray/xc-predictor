-- ====================================================================
--  diagnose_rating.sql -- is it the normalized time, or the rating?
--
--  Paste these into DBeaver one at a time. Nothing here writes.
--
--  THE CHAIN, from speed_ratings.py:
--
--      time_seconds
--        -> normalized_time = time_seconds * (5000 / distance) ** k
--        -> adjusted        = normalized_time / (1 + difficulty)
--        -> speed_rating    = pool_mean / adjusted * 100
--
--      pool_mean = the ARITHMETIC MEAN of ability, in seconds, over every
--                  valid athlete in the pool  (speed_ratings.poolMeans)
--
--  Four things can be wrong and they have four different fixes, so the
--  queries below cut the chain at each link rather than judging the end
--  of it:
--
--      Q1  do all the athlete's rows imply the SAME pool mean?
--      Q2  is the DISTANCE the normaliser used the real one?
--      Q3  is the RATING what a robust anchor would give?
--      Q4  what does the whole pool look like at each link?
--      Q5  is the POOL MEAN being dragged by a tail?
--
--  ★ Q3 IS THE ONE THAT SETTLES IT, and it does NOT work by comparing two
--    percentiles. A wrong pool_mean is a constant multiplier over the whole
--    pool, and a constant multiplier leaves every rank exactly where it
--    was -- rank is blind to precisely this failure. Q3 compares where the
--    result sits (a percentile) against what it was RATED (an absolute
--    number), with a median-anchored rating beside it for contrast.
--
--  Substitute your own case for 29346285 / 'ms_m' throughout.
-- ====================================================================


-- --------------------------------------------------------------------
-- Q0. Column names, so the rest of these actually run.
--     Different vintages of this schema differ; check before editing.
-- --------------------------------------------------------------------
SELECT table_name, column_name, data_type
FROM   information_schema.columns
WHERE  table_name IN ('results_tf', 'results', 'athlete_season',
                      'pair_athlete_season')
  AND  column_name IN ('time_seconds', 'normalized_time', 'speed_rating',
                       'distance', 'distance_meters', 'event', 'event_short',
                       'event_id', 'is_field', 'is_relay', 'pool', 'ability',
                       'mean_rating', 'person_id')
ORDER  BY table_name, column_name;


-- --------------------------------------------------------------------
-- Q1. The athlete's rows, end to end. Start here.
--
--     implied_pool_mean is the rating inverted: rating = pool_mean /
--     adjusted * 100, so pool_mean = rating * normalized_time / 100
--     (ignoring difficulty, which is a few percent).
--
--     ★ THE TELL: implied_pool_mean should be THE SAME NUMBER on every
--       row, because it is one constant for the whole pool. If it is, the
--       per-race arithmetic is sound and the constant itself is the
--       question -- go to Q5. If it varies row to row, something per-race
--       is wrong -- go to Q2.
-- --------------------------------------------------------------------
SELECT r.result_id,
       r.date,
       r.event_id,
       r.time_seconds,
       r.normalized_time,
       r.speed_rating,
       round((r.normalized_time / NULLIF(r.time_seconds, 0))::numeric, 4)
           AS norm_factor,
       -- The distance the normaliser must have used, recovered from the two
       -- time columns: nt/ts = (5000/d)^k  =>  d = 5000 / (nt/ts)^(1/k).
       round((5000.0 / power(r.normalized_time / NULLIF(r.time_seconds, 0),
                             1.0 / 1.06))::numeric, 1) AS distance_used,
       round((r.speed_rating * r.normalized_time / 100.0)::numeric, 1)
           AS implied_pool_mean
FROM   results_tf r
WHERE  r.person_id = 29346285
ORDER  BY r.date, r.time_seconds;


-- --------------------------------------------------------------------
-- Q2. Is the distance the normaliser used the distance that was run?
--
--     distance_used comes only from the two time columns, so it is what
--     the normaliser ACTUALLY did -- not what any metadata claims. An
--     800m that reads 1600 here is a distance bug and nothing else.
--
--     ⚠ 1/1.06 ASSUMES THE FLAT PLACEHOLDER EXPONENT. If distance_spline
--       .pkl exists, the exponent varies with distance and this recovery
--       is approximate -- a distance_used within a few percent of the real
--       event is fine, one that is double or half is not.
-- --------------------------------------------------------------------
SELECT round((5000.0 / power(r.normalized_time / NULLIF(r.time_seconds, 0),
                             1.0 / 1.06))::numeric, -1) AS distance_used,
       count(*)                                         AS n,
       round(min(r.time_seconds)::numeric, 1)           AS fastest_raw,
       round(avg(r.normalized_time)::numeric, 1)        AS avg_norm,
       round(avg(r.speed_rating)::numeric, 1)           AS avg_rating
FROM   results_tf r
WHERE  r.meet_id = (SELECT meet_id FROM results_tf
                    WHERE person_id = 29346285 LIMIT 1)
  AND  r.normalized_time IS NOT NULL
  AND  r.time_seconds > 0
GROUP  BY 1
ORDER  BY 1;


-- --------------------------------------------------------------------
-- Q3. ★ THE DECIDING QUERY. Where does this result sit in its pool, and
--     what would it rate against a robust anchor?
--
--     ⚠ COMPARING TWO PERCENTILES CANNOT ANSWER THIS, and that was the
--       first version of this query. Ratings are pool_mean / time * 100,
--       so a wrong pool_mean is a CONSTANT MULTIPLIER on the whole pool --
--       and a constant multiplier leaves every rank exactly where it was.
--       The time percentile and the rating percentile agree perfectly
--       whether the anchor is right or twice too big. Rank is blind to
--       precisely the failure being hunted.
--
--     So the comparison is the percentile against the ABSOLUTE rating,
--     with the median-anchored rating computed beside it. The median does
--     not move when a handful of abilities blow up; the mean does.
--
--       time_pctile 99.9, rating 168        consistent. He is that far
--                                           ahead and the pool is the
--                                           question, not the arithmetic.
--       time_pctile 70,   rating 168        a middling performance cannot
--                                           rate 168. Look at
--                                           rating_on_median_anchor: if it
--                                           lands near 100-110, the mean
--                                           anchor is the whole story.
--       rating_on_median  ~= speed_rating   the anchor is honest. The
--                                           fault is upstream -> Q2.
-- --------------------------------------------------------------------
WITH pool_rows AS (
    SELECT rr.result_id, rr.normalized_time, rr.speed_rating
    FROM   results_tf rr
    JOIN   ranking_results k ON k.result_id = rr.result_id AND k.sport = 'TF'
    WHERE  k.pool = 'ms_m'
      AND  rr.normalized_time IS NOT NULL
      AND  rr.speed_rating IS NOT NULL
), stats AS (
    SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY normalized_time) AS nt_median,
           avg(normalized_time)                                          AS nt_mean
    FROM   pool_rows
), ranked AS (
    SELECT result_id, normalized_time, speed_rating,
           -- ! FLIPPED, so both columns read the same direction: high means
           --   "further ahead of the pool". percent_rank on time is
           --   ascending, and faster is a lower number.
           100 * (1 - percent_rank() OVER (ORDER BY normalized_time)) AS time_pctile
    FROM   pool_rows
)
SELECT r.result_id, r.date, r.time_seconds,
       round(k.normalized_time::numeric, 1)             AS normalized_time,
       round(k.speed_rating::numeric, 1)                AS speed_rating,
       round(k.time_pctile::numeric, 2)                 AS time_pctile,
       round((s.nt_median / k.normalized_time * 100)::numeric, 1)
           AS rating_on_median_anchor,
       round((s.nt_mean / s.nt_median)::numeric, 3)     AS anchor_inflation
FROM   ranked k
JOIN   results_tf r ON r.result_id = k.result_id
CROSS  JOIN stats s
WHERE  r.person_id = 29346285
ORDER  BY r.date;


-- --------------------------------------------------------------------
-- Q4. What the whole pool looks like at each link of the chain.
--
--     Read the normalized_time column against times you know. If the
--     MEDIAN normalized time in ms_m is a 15-minute 5000m, the pool is
--     ordinary and the ratings are wrong. If it is a 25-minute 5000m, the
--     pool really does contain everybody and the ratings are only
--     reporting that.
-- --------------------------------------------------------------------
SELECT k.pool,
       count(*) AS n,
       round(percentile_cont(0.50) WITHIN GROUP
             (ORDER BY r.normalized_time)::numeric, 1) AS nt_median,
       round(percentile_cont(0.01) WITHIN GROUP
             (ORDER BY r.normalized_time)::numeric, 1) AS nt_fastest_1pct,
       round(avg(r.normalized_time)::numeric, 1)       AS nt_mean,
       round(percentile_cont(0.50) WITHIN GROUP
             (ORDER BY r.speed_rating)::numeric, 1)    AS rating_median,
       round(percentile_cont(0.99) WITHIN GROUP
             (ORDER BY r.speed_rating)::numeric, 1)    AS rating_p99,
       -- ★ mean/median on the TIME. Ratings divide by a mean of seconds,
       --   which has no upper bound; if this is far from 1 the anchor is
       --   being pulled by a tail rather than describing a middle.
       round((avg(r.normalized_time) / percentile_cont(0.50) WITHIN GROUP
              (ORDER BY r.normalized_time))::numeric, 3) AS mean_over_median
FROM   results_tf r
JOIN   ranking_results k ON k.result_id = r.result_id AND k.sport = 'TF'
WHERE  r.normalized_time IS NOT NULL AND r.speed_rating IS NOT NULL
GROUP  BY k.pool
ORDER  BY k.pool;


-- --------------------------------------------------------------------
-- Q5. The pool mean itself -- the one number every rating is divided by.
--
--     This reads ability directly, which is what poolMeans averages.
--     mean_over_median is the diagnosis: at 1.00 the mean is honest and a
--     pool that still looks wrong is wrong in WHO IS IN IT; well above
--     1.00 and a handful of solved abilities are moving the anchor and
--     every rating in the pool with it.
--
--     rating_if_median is what a 180 would become on a median anchor --
--     ratings scale linearly with it.
-- --------------------------------------------------------------------
SELECT pool,
       count(*)                                                  AS athletes,
       round(avg(ability)::numeric, 0)                           AS mean,
       round(percentile_cont(0.50) WITHIN GROUP
             (ORDER BY ability)::numeric, 0)                     AS median,
       round(percentile_cont(0.99) WITHIN GROUP
             (ORDER BY ability)::numeric, 0)                     AS p99,
       round(max(ability)::numeric, 0)                           AS worst,
       round((avg(ability) / percentile_cont(0.50) WITHIN GROUP
              (ORDER BY ability))::numeric, 3)                   AS mean_over_median,
       round((180 * percentile_cont(0.50) WITHIN GROUP
              (ORDER BY ability) / avg(ability))::numeric, 0)    AS rating_if_median
FROM   pair_athlete_season
WHERE  ability IS NOT NULL AND ability > 0
GROUP  BY pool
ORDER  BY pool;


-- --------------------------------------------------------------------
-- Q6. If Q5 shows a tail: who is in it.
--
--     A genuinely slow twelve-year-old means the pool is wide and the fix
--     is upstream, in who is admitted. An ability of 40,000 seconds means
--     a solve that failed, and the fix is a robust statistic.
-- --------------------------------------------------------------------
SELECT person_id, season, races,
       round(ability::numeric, 0)         AS ability_seconds,
       round(rating_seasonal::numeric, 1) AS rating
FROM   pair_athlete_season
WHERE  pool = 'ms_m' AND ability IS NOT NULL
ORDER  BY ability DESC
LIMIT  25;


-- --------------------------------------------------------------------
-- Q7. Sanity: does the athlete's own season rating reproduce?
--
--     rating = pool_mean / ability * 100. If recomputed and stored differ,
--     the stored value is stale -- a rebuild ran on one side only.
-- --------------------------------------------------------------------
WITH m AS (
    SELECT pool, avg(ability) AS pool_mean
    FROM   pair_athlete_season
    WHERE  ability IS NOT NULL AND ability > 0
    GROUP  BY pool
)
SELECT a.person_id, a.pool, a.season, a.races,
       round(a.ability::numeric, 1)                        AS ability,
       round(m.pool_mean::numeric, 1)                      AS pool_mean,
       round(a.rating_seasonal::numeric, 2)                AS stored,
       round((m.pool_mean / a.ability * 100)::numeric, 2)  AS recomputed
FROM   pair_athlete_season a
JOIN   m ON m.pool = a.pool
WHERE  a.person_id = '29346285';
