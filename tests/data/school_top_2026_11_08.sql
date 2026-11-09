        SELECT person_id, name, best, pool, seasons, first_year, last_year
        FROM (SELECT y.*, row_number() OVER (PARTITION BY y.pool
                                             ORDER BY y.best DESC) AS rn_pool
              FROM (
            SELECT s.person_id,
                   max(COALESCE(a.first_name, '') || ' '
                       || COALESCE(a.last_name, ''))  AS name,
                   max(s.best_rating)                 AS best,
                   -- the pool of the season holding that best, for the
                   -- HS-equivalent view (an athlete can span pools).
                   (array_agg(s.pool ORDER BY s.best_rating DESC))[1] AS pool,
                   count(*)                           AS seasons,
                   min(CASE WHEN s.sport = 'TF' THEN s.year + 1
                            ELSE s.year END)          AS first_year,
                   max(CASE WHEN s.sport = 'TF' THEN s.year + 1
                            ELSE s.year END)          AS last_year
            FROM   athlete_season s
            LEFT JOIN LATERAL (
                SELECT NULLIF(TRIM(x.first_name), '') AS first_name,
                       NULLIF(TRIM(x.last_name),  '') AS last_name
                FROM   athletes x
                WHERE  x.athlete_id = s.person_id
                ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL) DESC
                LIMIT  1
            ) a ON TRUE
            WHERE  s.school = %(school)s
              AND  s.sport  = %(sport)s
              AND  s.best_rating IS NOT NULL
              {sf}
            GROUP  BY s.person_id
              ) y
        ) x
        -- the cap is per pool, as schoolBest's
        WHERE  x.rn_pool <= %(lim)s
        ORDER  BY best DESC
