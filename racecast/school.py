"""
school.py: one school: its roster, its seasons, its meets.

★ KEYED ON THE SCHOOL NAME, NOT AN ID. That is not a design preference: it
  is what the data supports. `results.school` and `athlete_season.school` are
  free-text strings written by two different scrapers; there is no school
  table and no stable id shared between them. The same convention the course
  page already uses.

⚠ WHICH MEANS SPELLING VARIANTS ARE DIFFERENT SCHOOLS HERE. "Niwot" and
  "Niwot High School" would be two pages. That is a real limitation and it is
  better surfaced than hidden: the alternative is guessing which names mean
  the same programme and silently merging rosters that do not belong together.

SEASON-SCOPED THROUGHOUT
    A roster only means anything for a season. An all-time list would rank a
    2009 state champion above the current seventh runner, and neither answers
    "who runs for this school".

★ AND A SEASON IS NOT A CALENDAR YEAR, WHICH THIS PAGE USED TO ASSUME TWICE.

  A track season opens in December and runs to July, so it is STORED under
  the year it opens in -- Dec 2025 through Jul 2026 is year 2025 -- and NAMED
  year + 1 everywhere a person reads it, because nobody calls that campaign
  their 2025 season. rankings._YEAR_LABEL and app.season_label already do
  exactly this; this page did neither.

      the year bar and the tables showed the STORED year, so a track season
      read 2025 here and 2026 on every board;

      and the meet list filtered on substring(date, 1, 4) -- the CALENDAR
      year -- so a December meet landed in the season before its own and
      January to July of the same campaign landed in the next one. One season
      split across two pages, with the December meets in the wrong one.

  So: the queries take the STORED year, the page shows and links the LABEL,
  and seasonYearSqlInt does the filtering. seasonLabel and storedYear below
  are the only two places that conversion happens.
"""

import sys

sys.path.insert(0, "engine")
from season_year import seasonYearSqlInt
from school_identity import stateFilterSql
from capped import fetchCapped


def seasonLabel(sport, year):
    """Stored season year -> what a person calls it."""
    if year is None:
        return None
    return int(year) + 1 if sport == "TF" else int(year)


def storedYear(sport, label):
    """What a person calls it -> the stored season year."""
    if label is None:
        return None
    return int(label) - 1 if sport == "TF" else int(label)


# The same rule in SQL, for the columns that come back as labels.
def _labelSql(year_col, sport_col):
    return f"(CASE WHEN {sport_col} = 'TF' THEN {year_col} + 1 " \
           f"ELSE {year_col} END)"


def schoolYears(cur, school):
    """Seasons this school has raced, newest first, with a count each."""
    cur.execute(f"""
        SELECT year, sport,
               {_labelSql("year", "sport")} AS label,
               count(*) AS athletes
        FROM   athlete_season
        WHERE  school = %(school)s
        GROUP  BY year, sport
        ORDER  BY year DESC, sport
    """, {"school": school})
    return cur.fetchall()


def schoolHeader(cur, school):
    """State and span. None when the school has no results at all.

    ⚠ THREE SOURCES DEEP, because athlete_season is a REBUILT table: it
      empties during a pipeline rebuild (and once emptied itself on crash
      recovery, the UNLOGGED bug), and while it is empty every school
      page on the site 404'd. The header now falls back to
      ranking_results, then to the raw results tables -- the page
      renders whatever sections have data instead of vanishing."""
    label = _labelSql("year", "sport")
    cur.execute(f"""
        SELECT count(DISTINCT person_id)          AS athletes,
               min({label})                       AS first_year,
               max({label})                       AS last_year,
               mode() WITHIN GROUP (ORDER BY state) AS state
        FROM   athlete_season
        WHERE  school = %(school)s
    """, {"school": school})
    row = cur.fetchone()
    if row and row["athletes"]:
        return row

    cur.execute("""
        SELECT count(DISTINCT person_id)          AS athletes,
               min(CASE WHEN sport = 'TF' THEN year + 1 ELSE year END)
                                                  AS first_year,
               max(CASE WHEN sport = 'TF' THEN year + 1 ELSE year END)
                                                  AS last_year,
               NULL::text                         AS state
        FROM   ranking_results
        WHERE  school = %(school)s
    """, {"school": school})
    row = cur.fetchone()
    if row and row["athletes"]:
        return row

    cur.execute("""
        SELECT count(DISTINCT person_id)              AS athletes,
               min(substring(date, 1, 4))::int        AS first_year,
               max(substring(date, 1, 4))::int        AS last_year,
               NULL::text                             AS state
        FROM (
            SELECT person_id, date FROM results
            WHERE  school = %(school)s AND date IS NOT NULL
            UNION ALL
            SELECT person_id, date FROM results_tf
            WHERE  school = %(school)s AND date IS NOT NULL
        ) u
        WHERE date ~ '^[0-9]{4}'
    """, {"school": school})
    row = cur.fetchone()
    return row if row and row["athletes"] else None


def schoolRoster(cur, school, year, sport, state=None, primary=None):
    """Everyone who raced for this school in one season, best first.
    `state` narrows to athletes ASSIGNED to that home-state cluster --
    the same-name-two-schools chips (school_identity)."""
    sf, sfp = stateFilterSql("s", state, primary)
    cur.execute(f"""
        SELECT s.person_id,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '')  AS name,
               s.grade,
               s.pool,
               s.mean_rating,
               s.best_rating,
               s.n_races,
               s.first_race,
               s.last_race
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
          AND  s.year   = %(year)s
          AND  s.sport  = %(sport)s
          {sf}
        ORDER  BY s.mean_rating DESC NULLS LAST
    """, {"school": school, "year": year, "sport": sport, **sfp})
    return cur.fetchall()


def schoolMeets(cur, school, sport, year=None, limit=2000,
                state=None, primary=None):
    """Every meet this school has raced, newest first. One season if `year`.

    ⚠ LEFT JOIN ON meets, WITH A tfrrs FALLBACK. `meets` is anet-only: an
      inner join returns nothing for a tfrrs meet, which is the same trap that
      made compiled results come back empty.
    """
    # ★ ONE ROW PER MEET, WITH THE DISTANCES RUN (owner, 2026-09-02). It
    #   was one row per DIVISION, so a big invitational the school entered
    #   four races at showed as four identical names with no distance to
    #   tell them apart. Read from ranking_results: it carries the
    #   distance, the stored season year and the school's state on every
    #   row, so the season filter is an equality on an indexed column
    #   instead of a date expression, and the two sports are one query.
    #   Every row the boards accepted counts -- rated or timed (a
    #   sprinter's meets are meets too).
    # ! THE NAME COMES FROM A LATERAL, once per meet after the GROUP BY,
    #   not from a join that would multiply the rows before it: meets_tf
    #   has ~21 rows per meet and meets one per division.
    year_clause = "AND rr.year = %(year)s" if year else ""
    sf, sfp = stateFilterSql("rr", state, primary)
    if sport == "XC":
        name_sql = """
            SELECT COALESCE(
                     (SELECT min(m.meet_name) FROM meets m
                       WHERE m.meet_id = g.meet_id),
                     (SELECT min(mt.meet_name) FROM meets_tfrrs mt
                       WHERE mt.meet_id = g.meet_id AND mt.sport = 'XC'))
                   AS meet_name"""
    else:
        name_sql = """
            SELECT min(m.meet_name) AS meet_name
            FROM   meets_tf m WHERE m.meet_id = g.meet_id"""

    cur.execute(f"""
        WITH g AS (
            SELECT rr.meet_id,
                   min(rr.race_date)                            AS date,
                   count(*)                                     AS runners,
                   count(DISTINCT rr.div_id)                    AS divisions,
                   round(avg(rr.speed_rating)::numeric, 1)      AS avg_rating,
                   round(max(rr.speed_rating)::numeric, 1)      AS best_rating,
                   array_agg(DISTINCT round(rr.distance)::int)
                       FILTER (WHERE rr.distance > 0)           AS distances
            FROM   ranking_results rr
            WHERE  rr.school = %(school)s
              AND  rr.sport  = %(sport)s
              {year_clause}
              {sf}
            GROUP  BY rr.meet_id
        )
        SELECT g.meet_id, g.date, g.runners, g.divisions, g.avg_rating,
               g.best_rating, g.distances, n.meet_name
        FROM   g
        LEFT JOIN LATERAL ({name_sql}) n ON TRUE
        ORDER  BY g.date DESC
        LIMIT  %(lim)s
    """, {"school": school, "sport": sport, "year": year,
          # +1: the extra row is how the cap reports that it bit. See capped.py.
          "lim": limit + 1, **sfp})
    rows = fetchCapped(cur, limit)
    for r in rows:
        r["distance_labels"] = [distLabel(d) for d in sorted(r.get("distances") or [])]
    return rows


# The imperial distances, said the way the people who ran them say them
# (the same table school_prs uses for its section headings).
_MILE_LABELS = {1609: "1 Mile", 2414: "1.5 Mile", 3218: "2 Mile",
                4023: "2.5 Mile", 4828: "3 Mile", 6437: "4 Mile",
                8047: "5 Mile"}


def distLabel(metres):
    try:
        d = int(round(float(metres)))
    except (TypeError, ValueError):
        return ""
    return _MILE_LABELS.get(d, f"{d}m")



def currentSeason(cur, school, sport):
    """The newest season this school raced THIS sport, or None.

    ⚠ NOT max(year) OVERALL. A school whose last track season predates its
      last cross country one would otherwise show an empty roster.
    """
    cur.execute("""
        SELECT max(year) AS y
        FROM   athlete_season
        WHERE  school = %(school)s AND sport = %(sport)s
    """, {"school": school, "sport": sport})
    row = cur.fetchone()
    return row["y"] if row else None


def schoolBest(cur, school, sport, limit=25, state=None, primary=None):
    """The school's best single performances, all time.

    ★ EVERY PERFORMANCE, NOT ONE PER ATHLETE. A performance board is a list of
      races, and if the same runner owns the top four of them that IS the
      school's history. Collapsing to one row each answers a different
      question, and the roster tables above already answer that one.
    """
    sf, sfp = stateFilterSql("rr", state, primary)
    cur.execute(f"""
        SELECT rr.person_id,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '')  AS name,
               rr.speed_rating                   AS rating,
               -- pool + distance ride along for the HS-equivalent view
               -- (pool_view.stampBoardRows in the route).
               rr.pool,
               rr.distance,
               (CASE WHEN rr.sport = 'TF' THEN rr.year + 1
                     ELSE rr.year END)          AS year,
               rr.grade,
               rr.race_date,
               rr.meet_id,
               rr.div_id,
               rr.event_id
        FROM   ranking_results rr
        LEFT JOIN LATERAL (
            SELECT NULLIF(TRIM(x.first_name), '') AS first_name,
                   NULLIF(TRIM(x.last_name),  '') AS last_name
            FROM   athletes x
            WHERE  x.athlete_id = rr.person_id
            ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL) DESC
            LIMIT  1
        ) a ON TRUE
        WHERE  rr.school = %(school)s
          AND  rr.sport  = %(sport)s
          AND  rr.speed_rating IS NOT NULL
          {sf}
        ORDER  BY rr.speed_rating DESC
        LIMIT  %(lim)s
    """, {"school": school, "sport": sport, "lim": limit + 1, **sfp})
    return fetchCapped(cur, limit)


def schoolTopAthletes(cur, school, sport, limit=12,
                      state=None, primary=None):
    """Best career rating per athlete, for the chart.

    ★ THIS is where one-per-athlete belongs: the chart asks "who are the best
      runners this school has had", which is a question about people. The
      performance table asks about races, and keeps every one.
    """
    sf, sfp = stateFilterSql("s", state, primary)
    cur.execute(f"""
        SELECT person_id, name, best, seasons, first_year, last_year
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
        ) x
        ORDER  BY best DESC
        LIMIT  %(lim)s
    """, {"school": school, "sport": sport, "lim": limit + 1, **sfp})
    return fetchCapped(cur, limit)