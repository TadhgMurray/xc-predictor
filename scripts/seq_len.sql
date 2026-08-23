-- Project: xc-predictor
-- File:    scripts/seq_len.sql
-- Purpose: THE REAL max_seq_len, which feature_extraction.py still has as a
--          placeholder of 500 with a TODO beside it.
--
-- ⚠ ATTENTION IS O(L^2) AND THE TENSORS ARE PADDED TO L. Every example is
--   saved as [L, 21] float32, so L=500 costs 42 KB per example on disk and
--   250,000 attention cells per head whether or not the athlete has 500
--   races. If the real 99th percentile is 60, that is ~70x the compute and
--   ~8x the disk for padding.
--
-- ! MATCHES THE EXTRACTOR'S OWN GRAIN: grouped by athlete_id (not person_id
--   -- that is what groupByAthlete uses), XC and TF merged, and the same
--   `normalized_time IS NOT NULL` filter both loaders apply.
--
-- USAGE
--   psql -f scripts/seq_len.sql
WITH per_athlete AS (
    SELECT athlete_id, count(*) AS n
    FROM (
        SELECT athlete_id FROM results
         WHERE normalized_time IS NOT NULL AND athlete_id IS NOT NULL
        UNION ALL
        SELECT athlete_id FROM results_tf
         WHERE normalized_time IS NOT NULL AND athlete_id IS NOT NULL
    ) both_sports
    GROUP BY athlete_id
)
SELECT count(*)                                        AS athletes,
       sum(n)                                          AS races,
       round(avg(n), 1)                                AS mean_races,
       max(n)                                          AS max_races,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY n) AS p50,
       percentile_disc(0.90) WITHIN GROUP (ORDER BY n) AS p90,
       percentile_disc(0.99) WITHIN GROUP (ORDER BY n) AS p99,
       percentile_disc(0.999) WITHIN GROUP (ORDER BY n) AS p999
FROM per_athlete;

-- ★ AND WHAT EACH CANDIDATE CAP WOULD COST. `truncated` is how many athletes
--   would lose their oldest races -- which is the cheap half of a career to
--   lose, since a freshman's races predict a senior's poorly anyway.
WITH per_athlete AS (
    SELECT athlete_id, count(*) AS n
    FROM (
        SELECT athlete_id FROM results
         WHERE normalized_time IS NOT NULL AND athlete_id IS NOT NULL
        UNION ALL
        SELECT athlete_id FROM results_tf
         WHERE normalized_time IS NOT NULL AND athlete_id IS NOT NULL
    ) both_sports
    GROUP BY athlete_id
), caps AS (SELECT unnest(ARRAY[32, 48, 64, 96, 128, 192, 256, 500]) AS cap)
SELECT c.cap,
       count(*) FILTER (WHERE a.n > c.cap)                   AS truncated,
       round(100.0 * count(*) FILTER (WHERE a.n > c.cap)
             / count(*), 2)                                  AS pct_truncated,
       round(100.0 * sum(least(a.n, c.cap)) / sum(a.n), 1)   AS pct_races_kept,
       -- ⚠ SIZED BY ATHLETE, NOT BY EXAMPLE, so this UNDERSTATES the real
       --   cost by ~11x: the extractor emits one example per race AFTER an
       --   athlete's first, plus FORECAST_TWIN_RATE more. Kept as a relative
       --   guide between caps. Since the ragged-chunk change this is moot
       --   anyway -- nothing is padded to disk.
       -- cap * 21 features * 4 bytes
       pg_size_pretty((count(*) * c.cap * 21 * 4)::bigint)    AS seq_tensor,
       -- attention cells per example per head, relative to the 500 placeholder
       round((c.cap::numeric / 500) ^ 2, 4)                  AS attn_vs_500
FROM per_athlete a CROSS JOIN caps c
GROUP BY c.cap
ORDER BY c.cap;
