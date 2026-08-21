import sys
import re
import time          # _reportThrottled

sys.path.insert(0, "scripts")
from database import getConn
# ! THE ONE CLOCK. The athlete page groups its season tables on the academic
#   year (August-July, named for the year it opens in) like everything else --
#   the boards, athlete_season, grade_fix, the packer. It used to slice
#   date[:4], which split every indoor campaign from its own outdoor half and
#   disagreed with the season rating the rankings page shows for the same
#   athlete. See engine/season_year.py.
sys.path.insert(0, "engine")
from season_year import seasonYearFromIso
import psycopg2.extras
# ! EXPLICIT, NOT RELIED ON. `import psycopg2.extras` binds the package, and
#   psycopg2 does import .errors itself -- but depending on another module's
#   import side effect for an exception class in an except clause is a
#   NameError at the worst possible moment.
import psycopg2.errors
from flask import Flask, render_template, abort
from athlete_chart_data import build_chart_data
from athlete_bests import all_time_bests, season_bests_flat
from teams import parseFilters as parseTeamFilters, serveBoard
from courses import (parseFilters as parseCourseFilters,
                     getCourseRankings, countCourses)


# ===================================================================== #
#  THE SHARED ATHLETES LOOKUP
# ===================================================================== #

def _athlete_lateral(r="r"):
    """The canonical athletes lookup, as a LATERAL join fragment.

    WHY THIS EXISTS: `athletes` holds roughly one row per (person, school), so
    a prolific athlete has several -- Sophia Carcamo (27605295) has five, four
    named and one blank. A bare LIMIT 1 returns an arbitrary one, so her name
    rendered blank on some pages and fine on others, nondeterministically.

    ORDER BY makes the pick deterministic: rows that HAVE a name sort first,
    then rows with a usable gender. Booleans sort false < true, hence DESC.

    The gender filter is in the SORT, not the WHERE. Filtering would discard a
    named-but-genderless row entirely, throwing away the name to save a gender.

    concat_ws, not `||`: NULL || anything is NULL, so one missing half erases a
    name you actually have. TRIM+NULLIF because the blank rows hold '' and ' ',
    not NULL -- and '' || ' ' || '' yields ' ', which is TRUTHY in Python and
    renders as an empty cell instead of falling through to a fallback.

    Arguments: r -- alias of the results row in the OUTER query ('r', 'r2').
    Output: SQL text exposing a.name, a.gender, a.school.
    """
    return f"""
    LEFT JOIN LATERAL (
        SELECT NULLIF(TRIM(concat_ws(' ', a.first_name, a.last_name)), '') AS name,
               a.gender,
               a.school
        FROM   athletes a
        WHERE  a.athlete_id = COALESCE({r}.person_id, {r}.athlete_id)
        ORDER  BY (COALESCE(TRIM(a.first_name), '') <> ''
                OR COALESCE(TRIM(a.last_name),  '') <> '') DESC,
                  (a.gender IN ('M', 'F')) DESC
        LIMIT  1
    ) a ON TRUE
    """


# The display name, with fallbacks. `a.name` is already TRIMmed and NULLIFed by
# the lateral, so COALESCE can do its job. results.athlete_name is ~90% null but
# the 10% that isn't is exactly the population that fails the join.
def _name_sql(r="r"):
    return f"COALESCE(a.name, NULLIF(TRIM({r}.athlete_name), ''), 'Unknown')"


# ===================================================================== #
#  TFRRS XC META  --  the "Unknown meet" fix
# ===================================================================== #
#
# ★ `meets` IS ANET-ONLY. Its PK is div_id and it carries a `source` column,
#   and tfrrs XC never lands there. tfrrs meet metadata lives in `meets_tfrrs`
#   instead, keyed (meet_id, sport), with the per-division distance inside a
#   jsonb blob rather than in a column.
#
#   Consequence, before this fix:
#     * get_races LEFT JOINed `meets`, so m.course_name and m.distance came
#       back NULL -- the athlete page rendered "Unlinked meet", event None and
#       difficulty em-dash.
#     * get_race_header / get_meet_header selected FROM `meets`, so they
#       returned no row at all and the route did `abort(404)`. The meet page
#       for a tfrrs meet was UNREACHABLE, not merely unnamed.
#
#   This is the same COALESCE the engine's own loader (_xcQuery in
#   speed_ratings_db.py) already uses. The website was simply never taught it.
#
# ⚠ division_distances is JSONB, so ITS KEYS ARE STRINGS. An integer div_id
#   must be cast -- `-> r.div_id::text`, or a str() bound parameter. Looking it
#   up with an int returns NULL silently, which is the failure this whole fix
#   is about.
#
# ⚠ meets_tfrrs.venue_name is SPARSE (~6%) and on some meets holds the MEET
#   name rather than the venue (a parser fallback). It is used here for DISPLAY
#   only and must not be treated as a venue key.


def _tfrrs_join(r="r"):
    """LEFT JOIN fragment exposing `mt` — the tfrrs meet row for an XC result.

    Guarded by `{r}.source = 'tfrrs'` inside the ON clause rather than the
    WHERE, so an anet row simply never matches and keeps its own `meets` data.
    """
    return f"""
    LEFT JOIN meets_tfrrs mt
           ON {r}.source  = 'tfrrs'
          AND mt.meet_id  = {r}.meet_id
          AND mt.sport    = 'XC'
    """


def _blob(r="r", field="distance"):
    """The per-division value from meets_tfrrs.division_distances.

    `->` walks into the object with a TEXT key; `->>` extracts the leaf as
    text. Callers cast the result themselves, because 'distance' is numeric
    and 'div_name' is not.
    """
    return f"(mt.division_distances -> {r}.div_id::text ->> '{field}')"


def _xc_course_sql(r="r"):
    """Display course name, either source."""
    return "COALESCE(m.course_name, mt.venue_name)"


def _xc_distance_sql(r="r"):
    """Race distance, either source. anet keeps it on `meets`; tfrrs keeps it
    per division inside the blob."""
    return f"COALESCE(m.distance, {_blob(r, 'distance')}::real)"


# Creates the app; __name__ tells Flask where "here" is
app = Flask(__name__)


# When someone visits the address "/" run the function below.
# This is a decorator, it connects a URL to a function.
@app.route("/hello")
def yay():
    return "Hello from Flask"

def get_homepage_panels(cur):
    """Every visible panel row, flat. panels.py already ranked and filtered
    these, so the route does no work beyond reshaping. `visible` hides boards
    suppressed in panels.py (the all-time performance board, for now)."""
    cur.execute("""
        SELECT board, scope, sport, pool, rank,
               person_id, name, school, rating, season_year,
               detail, link, name_link
        FROM   homepage_panels
        WHERE  visible
        ORDER  BY sport, scope, board, pool, rank
    """)
    return cur.fetchall()

def group_panels(rows):
    """Flat rows -> panels[sport][scope][board][pool] = [rows].

    Four levels because that's the click path: pick a sport, a scope
    (season/all-time), then each board (athlete/performance) shows its pools.
    defaultdict so we never check 'does this key exist yet' -- it autovivifies.
    """
    from collections import defaultdict
    tree = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    for r in rows:
        tree[r["sport"]][r["scope"]][r["board"]][r["pool"]].append(r)
    return tree

def get_homepage_meta(cur):
    """The key/value bag panels.py wrote: season years, build time, default
    sport. Returned as a plain dict for easy template access."""
    cur.execute("SELECT key, value FROM homepage_meta")
    return {r["key"]: r["value"] for r in cur.fetchall()}


@app.route("/")
def home():
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            rows = get_homepage_panels(cur)
            meta = get_homepage_meta(cur)

    panels = group_panels(rows)

    panels = group_panels(rows)
    panels = pad_pool_pairs(panels)      # equalize lengths for the grid

    # Default sport: what panels.py decided from the wall clock, falling back
    # to XC if the meta row is somehow missing.
    default_sport = meta.get("default_sport") or "XC"

    return render_template("home.html",
                           panels=panels,
                           meta=meta,
                           default_sport=default_sport)


@app.route("/athlete/<int:person_id>")
def athlete(person_id):
    with getConn() as conn:                # reuse the engine's connection
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Same duplicate-rows problem as everywhere else: this person may
            # have several `athletes` rows (one per school). ORDER BY makes the
            # pick deterministic instead of whatever Postgres returns first.
            cur.execute("""
                SELECT NULLIF(TRIM(concat_ws(' ', first_name, last_name)), '') AS name,
                       school,
                       gender
                FROM   athletes
                WHERE  person_id = %s
                ORDER  BY (COALESCE(TRIM(first_name), '') <> ''
                        OR COALESCE(TRIM(last_name),  '') <> '') DESC,
                          (gender IN ('M', 'F')) DESC
                LIMIT  1
            """, (person_id,))
            athlete = cur.fetchone()
            if athlete is None:
                abort(404)

            # ★ THE HEADER RATING COMES FROM athlete_season -- the table the
            #   boards rank -- so the number up top is one the athlete can go
            #   find on /rankings. The old source, athlete_ratings, holds one
            #   row per POOL with no ORDER BY, so fetchone() could show a
            #   middle-school number over a college career depending on
            #   physical row order. Most recent season = current form, which
            #   is what a header means; the note under it says which season.
            try:
                cur.execute("""
                    SELECT mean_rating, sport, pool, year, n_races
                    FROM   athlete_season
                    WHERE  person_id = %s
                    ORDER  BY last_race DESC NULLS LAST, year DESC,
                              n_races DESC
                    LIMIT  1
                """, (person_id,))
                season_rating = cur.fetchone()
            except psycopg2.errors.UndefinedTable:
                # A database that has never run build_ranking_results.
                conn.rollback()
                season_rating = None

            # ⚠ THE FALLBACK IS THE OLD TABLE, and it still earns its keep:
            #   athlete_season is built from ranking_results, which is
            #   US-scoped and drops unresolved or untrusted seasons, while
            #   the engine rates them anyway. ORDER BY makes the pick
            #   deterministic -- the bare fetchone() this replaces returned
            #   an arbitrary pool's number.
            rating = None
            if season_rating is None:
                cur.execute("""
                    SELECT speed_rating
                    FROM   athlete_ratings
                    WHERE  athlete_id = %s
                    ORDER  BY n_races DESC NULLS LAST, pool
                    LIMIT  1
                """, (person_id,))
                rating = cur.fetchone()
            races = get_races(cur, person_id)

            # ★ BOARD ELIGIBILITY, per academic season. ranking_results
            #   silently drops seasons grade_sanity could not place or could
            #   not corroborate; without this the page shows their ratings
            #   with no hint they are on no board. _attach_season_verdicts
            #   turns these rows into the season flags.
            try:
                cur.execute("""
                    SELECT season, method, trust
                    FROM   grade_fix
                    WHERE  person_id = %s
                """, (person_id,))
                verdicts = cur.fetchall()
            except psycopg2.errors.UndefinedTable:
                # A database from before grade_sanity wrote grade_fix. No
                # markers is the old behaviour, not a broken page.
                conn.rollback()
                verdicts = []

    # 0. collapse anet/tfrrs copies of the same physical race. MUST come before
    #    the record walk, or every duplicate gets its own PR/SR badge.
    races = dedupe_races(races)

    # 0b. stamp each race with its season -- the academic year, labelled the
    #     way the boards label it. Stamped ONCE here so the season tables, the
    #     record flags and the sidebar bests all group on the same value.
    for race in races:
        race["season_label"] = season_label(race["sport"], race["date"])

    # 1. format times for display
    for race in races:
        if not race["is_field"] and race["result"] is not None:
            race["result"] = format_time(race["result"])

    chart_data = build_chart_data(races)

    # 2. define the knobs
    # ! SEASON KEYS CARRY THE SPORT, because the label alone is ambiguous
    #   across sports: TF's label is academic year + 1, so "2026 TF" (academic
    #   2025) and "2026 XC" (academic 2026) are different campaigns wearing the
    #   same number. Every "season" flag now means exactly one displayed
    #   season block -- so a TF block's Rating Season Record is the best TF
    #   race of THAT block, never outranked by an XC race from some calendar
    #   year.
    by_distance = lambda r: r["event"]
    by_course   = lambda r: (r["meet"], r["event"])
    career_wide = lambda r: "ALL"
    by_season_d = lambda r: (r["season_label"], r["sport"], r["event"])
    time_of     = lambda r: r["time_raw"]
    rating_of   = lambda r: r["speed_rating"]
    faster      = lambda new, best: new < best
    higher      = lambda new, best: new > best
    by_season_course = lambda r: (r["season_label"], r["sport"], r["meet"], r["event"])
    by_season        = lambda r: (r["season_label"], r["sport"])

    # 3. compute the record sets
    pr_ids        = flag_records(races, by_distance, time_of,   faster)
    sr_ids        = flag_records(races, by_season_d, time_of,   faster)
    course_pr_ids = flag_records(races, by_course,   time_of,   faster)
    rating_pr_ids = flag_records(races, career_wide, rating_of, higher)
    course_sr_ids = flag_records(races, by_season_course, time_of,   faster)
    rating_sr_ids = flag_records(races, by_season,        rating_of, higher)
    # current record holders (the stars)
    star_pr_ids     = find_current_records(races, by_distance, time_of,   faster)
    star_course_ids = find_current_records(races, by_course,   time_of,   faster)
    star_rating_ids = find_current_records(races, career_wide, rating_of, higher)

    # 4. NOW attach them
    for race in races:
        rid = race["result_id"]
        race["is_pr"]        = rid in pr_ids
        race["is_sr"]        = rid in sr_ids
        race["is_course_pr"] = rid in course_pr_ids
        race["is_rating_pr"] = rid in rating_pr_ids
        race["is_course_sr"] = rid in course_sr_ids
        race["is_rating_sr"] = rid in rating_sr_ids
        race["is_star_pr"]     = rid in star_pr_ids
        race["is_star_course"] = rid in star_course_ids
        race["is_star_rating"] = rid in star_rating_ids

    # 5. group/enrich/sort
    seasons = group_into_seasons(races)
    seasons = enrich_seasons(seasons)
    # AFTER enrich_seasons -- it rebuilds the values, so a note attached to
    # the raw grouping would be thrown away with the list it sat on.
    _attach_season_verdicts(seasons, verdicts)
    ordered = sorted(seasons.items(), reverse=True)

    athlete["grade"]  = _season_grade(races)
    athlete["school"] = _season_school(races) or athlete["school"]
    if season_rating:
        athlete["rating"] = season_rating["mean_rating"]
        # The season label, same rule as everywhere: TF displays year + 1.
        label = (season_rating["year"] + 1 if season_rating["sport"] == "TF"
                 else season_rating["year"])
        athlete["rating_note"] = f"{label} {season_rating['sport']} season"
    else:
        athlete["rating"] = rating["speed_rating"] if rating else None
        athlete["rating_note"] = None

    # ★ THE HEADER STAT STRIP. These numbers all existed -- in the sidebar,
    #   below the fold, or not at all -- while the header carried just a name
    #   and a grey line. They are derived here rather than in the template so
    #   the season span is computed once from the same keys the blocks use.
    #
    # ! SEASONS COUNTS YEARS, NOT BLOCKS. `seasons` is keyed (label, sport),
    #   so an athlete running both sports in one year holds two entries for
    #   one season of their life -- "5 seasons" for four years of school
    #   would be wrong in the way nobody would think to check.
    labels = sorted({label for label, _sport in seasons})
    athlete["n_races"] = len(races)
    athlete["n_seasons"] = len(labels)
    athlete["season_span"] = (f"{labels[0]} — {labels[-1]}"
                              if len(labels) > 1 else
                              labels[0] if labels else None)

    xc_seasons = [(k, v) for k, v in ordered if k[1] == "XC"]
    tf_seasons = [(k, v) for k, v in ordered if k[1] == "TF"]
    tf_dists = tf_distances(races)

    # `seasons` (not `ordered`) because season_bests_flat looks up
    # season.rating by (year, sport) key -- it needs the dict, not the
    # sorted list of pairs.
    alltime = all_time_bests(races)
    season_best_list = season_bests_flat(races, seasons)

    chart_data = build_chart_data(races)

    return render_template("athlete.html",
                           athlete=athlete,
                           xc_seasons=xc_seasons,
                           tf_seasons=tf_seasons,
                           tf_dists=tf_dists,
                           alltime=alltime,
                           season_bests=season_best_list,
                           chart_data=chart_data)


def get_races(cur, person_id):
    """Return one athlete's FULL competition history, both sports, newest first.

    Every event is kept — sprints, distance, field. No normalized_time filter,
    so this is the complete record, not just the distance races the engine rates.
    Both halves of the UNION produce the SAME columns in the SAME order.
    """
    cur.execute(f"""
        -- ================= XC half: results + meets =================
        SELECT r.date,
               'XC'                          AS sport,
               -- COALESCE across sources: `meets` is anet-only, so a tfrrs row
               -- has to take its name from meets_tfrrs or render as "Unlinked".
               {_xc_course_sql('r')}         AS meet,
               -- XC "event" is the race distance. anet keeps it on `meets`;
               -- tfrrs keeps it PER DIVISION inside a jsonb blob.
               -- ::text so this column is text in BOTH halves (types must match).
               {_xc_distance_sql('r')}::text AS event,
               r.time_seconds                AS time_raw,     -- numeric, for PR comparison
               r.result_id                   AS result_id,
               r.time_seconds::text          AS result,
               r.grade                       AS grade,
               r.school                      AS school,
               r.speed_rating                AS speed_rating,
               cd.difficulty                 AS difficulty,
               0                             AS is_field,
               r.meet_id                     AS meet_id,
               r.div_id                      AS div_id,
               r.canon_meet_id               AS canon_meet_id,
               NULL::bigint                  AS event_id,
               r.source                      AS source
        FROM results r
        LEFT JOIN meets m
               ON m.div_id  = r.div_id
              AND m.meet_id = r.meet_id
              AND m.source  = r.source
        {_tfrrs_join('r')}
        -- Difficulty is keyed on (canonical_id, distance_m) since the
        -- per-distance split -- a venue hosting a 2300m and an 8000m has a
        -- separate difficulty for each, because they are different courses on
        -- the same grounds.
        --
        -- Joining on course_name alone matches EVERY distance cell AND every
        -- other venue sharing that name. There are 21 Woodward Parks, so one
        -- race came back as 21 rows with 21 different difficulties.
        --
        -- course_canonical resolves (name, lat, lng) to the venue id the
        -- engine actually used; distance_m picks the right cell within it.
        -- The COALESCEd name and gps let a tfrrs race resolve a cell too.
        LEFT JOIN course_canonical cc
               ON cc.course_name = {_xc_course_sql('r')}
              AND round(cc.gps_lat::numeric,  5)
                = round(COALESCE(m.gps_lat,  mt.gps_lat)::numeric,  5)
              AND round(cc.gps_long::numeric, 5)
                = round(COALESCE(m.gps_long, mt.gps_long)::numeric, 5)
        LEFT JOIN course_difficulties cd
               ON cd.canonical_id = cc.canonical_id
              AND cd.distance_m   =
                  (round({_xc_distance_sql('r')} / 100.0) * 100)::int
        WHERE r.person_id = %(pid)s
          AND r.time_seconds IS NOT NULL

        UNION ALL

        -- ================= TF half: results_tf + meets_tf ===========
        SELECT r.date,
               'TF'                          AS sport,
               m.meet_name                   AS meet,
               r.event_short                 AS event,
               r.time_seconds                AS time_raw,     -- same slot as XC
               r.result_id                   AS result_id,    -- same slot as XC
               CASE WHEN r.is_field = 1 THEN r.mark
                    ELSE r.time_seconds::text END AS result,
               r.grade                       AS grade,
               r.school                      AS school,
               r.speed_rating                AS speed_rating,
               cd.difficulty                 AS difficulty,
               COALESCE(r.is_field, 0)       AS is_field,
               r.meet_id                     AS meet_id,
               r.div_id  AS div_id,
               r.canon_meet_id               AS canon_meet_id,
               r.event_id                    AS event_id,
               r.source                      AS source
       FROM results_tf r
        -- tfrrs rows often carry a blank div_id (it's only populated for meets
        -- re-scraped with the capture code), and NULL = anything is NULL, so a
        -- plain equality join drops them. Match on the keys that ARE reliable
        -- and treat div_id as a preference instead of a requirement.
        --
        -- LATERAL + LIMIT 1 rather than a loosened ON clause: (meet_id,
        -- event_id) is not unique in meets_tf, so a plain join could emit two
        -- rows for one result. LIMIT 1 makes that impossible.
        LEFT JOIN LATERAL (
            SELECT m.meet_name, m.location_id, m.is_indoor
            FROM   meets_tf m
            WHERE  m.meet_id = r.meet_id
            -- meet_id is the ONLY key that crosses sources. div_id, event_id
            -- and source are all anet-local and do not match tfrrs results.
            -- meet_name and location_id are constant across a meet's rows, so
            -- picking any one is safe. DISTANCE IS NOT -- it varies per event,
            -- so it is deliberately not selected here.
            LIMIT  1
        ) m ON TRUE
        LEFT JOIN course_difficulties cd
               ON cd.course_name = 'TF:loc:' || m.location_id::text ||
                  CASE WHEN COALESCE(m.is_indoor, 0) = 1 THEN ':in' ELSE ':out' END
        WHERE r.person_id = %(pid)s
          AND (r.time_seconds IS NOT NULL OR r.mark IS NOT NULL)

        ORDER BY date DESC
    """, {"pid": person_id})
    return cur.fetchall()


# ===================================================================== #
#  CROSS-SOURCE DEDUPE
# ===================================================================== #
#
# One physical race can sit in `results` TWICE -- once from anet, once from
# tfrrs -- because the two scrapes are kept separate on purpose. Both copies
# carry the SAME `canon_meet_id` (the anet meet id of the physical race), which
# is exactly what that column is for.
#
# The copies are not equally useful. The tfrrs twin typically has div_id = 0
# and a tfrrs meet_id, so it joins to nothing in `meets`: no name, no distance,
# no rating, and a link that 404s. Left alone it also DOUBLES every race in the
# PR/SR walk and in any season average.

# Fields that identify WHICH page a row links to. Never merged between copies:
# a meet_id from one source plus a div_id from the other is a broken URL.
_LINK_FIELDS = {"meet_id", "div_id", "event_id", "result_id", "source",
                "canon_meet_id"}


def _race_identity(race):
    """A key that is identical for two source-copies of one physical race.

    Returns None when we cannot be sure -- an unknown key is never merged.
    Time is included because at a TF meet one athlete runs several events, all
    sharing a canon_meet_id; two different events will not share a time.
    """
    canon = race.get("canon_meet_id")
    time  = race.get("time_raw")
    if canon is None or time is None:
        return None
    return (race["sport"], canon, round(float(time), 1))


def _race_quality(race):
    """How useful a copy is. Higher wins. Compared as a tuple, left to right:
    did it resolve a meet, does it have a rating, does it have an event."""
    return (1 if race.get("meet") else 0,
            1 if race.get("speed_rating") is not None else 0,
            1 if race.get("event") else 0)


def _fill_blanks(winner, loser):
    """Copy non-link fields from the loser into any blanks on the winner.

    Neither copy is necessarily complete, so the survivor takes whatever the
    discarded twin can contribute -- but never the link fields, which must all
    come from one source or the URL is nonsense."""
    for key, value in loser.items():
        if key in _LINK_FIELDS:
            continue
        if winner.get(key) is None and value is not None:
            winner[key] = value
    return winner


def _merge_by_canon(races):
    """Collapse cross-source duplicates. Must run BEFORE the PR/SR walk."""
    best = {}              # identity -> the winning copy so far
    unmergeable = []       # no identity -> passed through untouched

    for race in races:
        key = _race_identity(race)
        if key is None:
            unmergeable.append(race)
            continue

        current = best.get(key)
        if current is None:
            best[key] = dict(race)                 # copy: we mutate it below
        elif _race_quality(race) > _race_quality(current):
            best[key] = _fill_blanks(dict(race), current)
        else:
            _fill_blanks(current, race)

    merged = unmergeable + list(best.values())
    return merged

def dedupe_races(races):
    """Collapse duplicate copies of one physical race, in two passes.

    PASS 1 -- canon_meet_id. Exact and safe: that column exists to say 'these
    are the same physical race'. Covers ~89% of the corpus.

    PASS 2 -- same sport, same time to the hundredth, different sources, dates
    within a day. For the rows pass 1 could not reach. Fuzzier, hence the
    same-source guard.
    """
    races = _merge_by_canon(races)
    races = _merge_cross_source(races)
    races.sort(key=lambda r: r["date"], reverse=True)
    return races


def format_time(seconds):
    """Format raw seconds for display, keeping whatever precision the data has.

       11.24 -> '11.24'    14:58.2 -> '14:58.2'    1:05:03 -> '1:05:03'

    Precision is NOT fixed at two places. Printing '19:57.60' on a value stored
    as 1197.6 would claim hundredth accuracy the scrape never captured. We show
    the decimals that exist and nothing more.
    """
    seconds = float(seconds)
    whole   = int(seconds)                       # truncate, never round
    frac    = seconds - whole

    tail = _format_fraction(frac)                # '', '.6', or '.24'

    if seconds < 60:
        return f"{whole}{tail}"                  # sprint: '11.24'

    hours   = whole // 3600
    minutes = (whole % 3600) // 60
    secs    = whole % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}{tail}"
    return f"{minutes}:{secs:02d}{tail}"


def _format_fraction(frac):
    """The decimal tail, at the precision the value actually carries.

    Rounded to 2dp first because `real` is a 4-byte float: a mark entered as
    11.24 can be stored as 11.239999771, and testing that raw would report
    false precision on effectively every row.
    """
    hundredths = round(frac * 100)
    if hundredths == 0:
        return ""                                # whole second -> no tail
    if hundredths % 10 == 0:
        return f".{hundredths // 10}"            # tenth  -> '.6'
    return f".{hundredths:02d}"                  # hundredth -> '.24'


def season_label(sport, date_text):
    """The season a race belongs to, as the string the page displays.

    ★ ACADEMIC YEAR IN, BOARD LABEL OUT. seasonYearFromIso gives the stored
      season (August-July, named for its opening year); the LABEL is what the
      boards show -- year + 1 for TF, because nobody calls the Dec 2025 - Jul
      2026 campaign their 2025 season. Same rule as rankings._YEAR_LABEL, so
      the season heading here and the year column on /rankings agree.

    ! A STRING, because the old key was date[:4] and the template, anchors
      (#2026-TF) and tuple sorts all built on strings. The fallback below can
      only return a string, and one int key beside it would make sorted() raise.

    ⚠ THE FALLBACK IS THE OLD BEHAVIOUR, NOT AN ERROR. get_races has no date
      regex (unlike build_ranking_results), so a malformed date must not take
      the whole page down -- it gets the calendar slice, exactly what every
      race got before this function existed.
    """
    try:
        year = seasonYearFromIso(sport, date_text)
    except (TypeError, ValueError, IndexError):
        return (date_text or "")[:4]
    return str(year + 1) if sport == "TF" else str(year)


@app.template_filter("event_label")
def event_label(event):
    """A bare number is a distance in metres, so give it its unit.

    XC stores the race distance AS the event ("5000"), so the athlete page
    printed a naked number in a column headed Event. TF's event_short already
    carries one ("1600m", "4X400") and comes back untouched.

    ! THE TEST IS "IS THIS ONLY A NUMBER", NOT THE SPORT. A TF row stored as a
      bare 5000 is 5000 metres too, and keying on sport would leave it naked
      while dressing the identical XC value.

    ⚠ ROUNDED TO WHOLE METRES. The stored distance is whatever the meet
      recorded, so a five-mile race arrives as 8046.72 and a 5000 can read
      4988.9663 -- printing those verbatim in a table cell claims a precision
      nobody measured. Same reasoning as PR_DISTANCE_TOL in rankings.py.
    """
    if event is None:
        return ""
    text = str(event).strip()
    if not text:
        return ""
    try:
        return f"{round(float(text))}m"
    except (TypeError, ValueError):
        return text


def group_into_seasons(races):
    """Turn a flat list of races into a dict keyed by (season label, sport),
    each value being the list of races in that season.

    Reads race["season_label"], stamped once by the route -- the record flags
    and the sidebar bests key on the same value, and computing it in one place
    is what keeps the three from drifting."""
    seasons = {}
    for race in races:
        key = (race["season_label"], race["sport"])
        seasons.setdefault(key, []).append(race)
    return seasons


def enrich_seasons(seasons):
    """Turn {key: [races]} into {key: {races, rating, grade, school}} by
    computing each season's header fields from its own races."""
    enriched = {}
    for key, races in seasons.items():
        enriched[key] = {
            "races":  races,
            "rating": season_rating(races),
            "grade":  _season_grade(races),
            "school": _season_school(races),
        }
    return enriched


# The verdicts that mean grade_sanity looked and could not place the season.
# ⚠ MUST MATCH pool_resolve.resolvePool's refusal list -- these four make it
#   return no pool, which is what drops the season from every board. They are
#   inlined there rather than named, so this copy is the thing to keep in step.
_NO_POOL_VERDICTS = ("no_evidence", "contradicted", "thin_field", "lone_word")


def _attach_season_verdicts(seasons, verdicts):
    """Mark each season block the ranking boards exclude, and why.

    ★ THE SILENT DROP, MADE VISIBLE. build_ranking_results skips a row when
      its season verdict resolves to no pool, or when its trust is 'low'
      (fewer than two races behind the grade -- grade_sanity rule 7). The
      rating itself is written separately by the engine, so the athlete page
      shows a number that appears on no board, with nothing saying so. This
      attaches season["board_note"] and the template renders the flag.

    ! VERDICT FIRST, TRUST SECOND, same order resolvePool applies them: a
      season with no pool is off the boards before trust is ever consulted,
      so "grade unresolved" is the truer message when both hold.

    grade_fix is keyed on the ACADEMIC year; the seasons dict is keyed on the
    display label, which is academic + 1 for TF. Undo that here, not in the
    query -- the label is a display rule and the database never sees it.
    """
    by_year = {int(v["season"]): v for v in verdicts}
    for (label, sport), season in seasons.items():
        try:
            academic = int(label) - (1 if sport == "TF" else 0)
        except ValueError:
            continue                       # malformed-date fallback label
        v = by_year.get(academic)
        if v is None:
            continue
        if v.get("method") in _NO_POOL_VERDICTS:
            season["board_note"] = v["method"]
        elif v.get("trust") == "low":
            season["board_note"] = "low_trust"


def _season_grade(races):
    """The grade for this season — same across its races, so take the first
    one that actually has a grade."""
    for r in races:
        if r["grade"]:                 # skip None/empty
            return r["grade"]
    return None                        # no grade on any race this season


def _season_school(races):
    """The school for this season — same idea, first race that has one."""
    for r in races:
        if r.get("school"):
            return r["school"]
    return None


def season_rating(races):
    """Average the per-race speed ratings for one season, ignoring unrated races."""
    rated = [r["speed_rating"] for r in races if r["speed_rating"] is not None]
    if not rated:                      # a season with no rated races
        return None                    # -> template shows "—", not a crash
    return sum(rated) / len(rated)


def flag_records(races, key_fn, value_fn, better):
    """Which races WERE a record at the time they were run (chronological walk)."""
    best = {}
    records = set()
    for race in sorted(races, key=lambda r: r["date"]):   # oldest first
        k = key_fn(race)         # <-- CALLS the key_fn you passed in
        v = value_fn(race)       # <-- CALLS the value_fn you passed in
        if v is None:
            continue             # no time/rating -> can't be a record
        if k not in best or better(v, best[k]):   # <-- CALLS your better()
            records.add(race["result_id"])
            best[k] = v
    return records


def find_current_records(races, key_fn, value_fn, better):
    """Return the set of race ids that CURRENTLY hold the record in their group.

    Unlike flag_records (which asks 'was this a record at the time'), this asks
    'is this the best one, period' — so exactly one race per group wins.
    """
    best_race = {}                       # key -> the race dict currently winning
    for race in races:
        k = key_fn(race)
        v = value_fn(race)
        if v is None:
            continue
        if k not in best_race or better(v, value_fn(best_race[k])):
            best_race[k] = race          # this race is now the leader for its group
    return {r["result_id"] for r in best_race.values()}


def _better_time(new, old):
    """True if `new` is a faster time than `old` (or there's no old yet)."""
    return old is None or new < old


def _better_rating(new, old):
    """True if `new` is a higher rating than `old` (or there's no old yet)."""
    return old is None or new > old


def _track_event_best(bucket, race):
    """If this race is the year's fastest at its distance, record it."""
    event = race["event"]
    time  = race["time_raw"]
    if event is None or time is None:
        return                              # field events / missing data: skip
    current = bucket["events"].get(event)
    if current is None or _better_time(time, current["time_raw"]):
        bucket["events"][event] = race


def _track_rating_best(bucket, race):
    """If this race is the year's best-rated, record it."""
    rating = race["speed_rating"]
    if rating is None:
        return
    current = bucket["best_rating"]
    if current is None or _better_rating(rating, current["speed_rating"]):
        bucket["best_rating"] = race


def season_bests_by_year(races):
    """{year: {sport: {"events": {...}, "best_rating": race}}}"""
    years = {}
    for r in races:
        year  = r["date"][:4]
        sport = r["sport"]
        bucket = years.setdefault(year, {}).setdefault(
            sport, {"events": {}, "best_rating": None}
        )
        _track_event_best(bucket, r)
        _track_rating_best(bucket, r)
    return years


def tf_distances(races):
    """Distinct TF events this athlete ran — one time-chart per distance."""
    seen = []
    for r in races:
        if r["sport"] == "TF" and r["event"] and r["event"] not in seen:
            seen.append(r["event"])
    return seen

_DUP_DAYS = 1     # sources disagree about which day a two-day meet's race fell on


def _time_key(race):
    """Bucket key for the fallback match: sport + time to the hundredth.

    Rows only get COMPARED if they land in the same bucket, so this is what
    keeps the pass cheap -- no all-pairs scan over the athlete's career.
    """
    t = race.get("time_raw")
    if t is None:
        return None                    # field events etc. -> never fuzzy-matched
    return (race["sport"], round(float(t), 2))


def _days_apart(d1, d2):
    """Whole days between two 'YYYY-MM-DD' text dates, or None if unparseable.

    `date` is a TEXT column and the corpus contains junk years (0023, 2223),
    so this must not assume the string is a real date.
    """
    from datetime import date
    try:
        return abs((date.fromisoformat(d1[:10]) - date.fromisoformat(d2[:10])).days)
    except (ValueError, TypeError, AttributeError):
        return None                    # unparseable -> treated as "not a twin"


def _is_cross_source_twin(a, b):
    """Same race seen twice? Time already matches (same bucket), so:

      1. DIFFERENT sources. This is the load-bearing guard. Two anet rows with
         the same time are a prelim and a final -- two real races that must
         NOT be merged. Only an anet/tfrrs pair is one race seen twice.
      2. Dates within a day. Arcadia runs across two days and the sources
         disagree about which one this race was on.
    """
    if a.get("source") == b.get("source"):
        return False
    gap = _days_apart(a.get("date"), b.get("date"))
    return gap is not None and gap <= _DUP_DAYS

def _collapse_group(group):
    """Fold cross-source twins within one (sport, time) bucket.

    O(n^2) inside a bucket, but a bucket is 1-2 rows in practice -- an athlete
    rarely runs the same hundredth twice.
    """
    kept = []
    for race in group:
        twin = next((k for k in kept if _is_cross_source_twin(k, race)), None)

        if twin is None:
            kept.append(dict(race))                      # first of its kind
        elif _race_quality(race) > _race_quality(twin):
            kept[kept.index(twin)] = _fill_blanks(dict(race), twin)
        else:
            _fill_blanks(twin, race)                     # incumbent wins
    return kept


def _merge_cross_source(races):
    """Second dedupe pass, for rows canon_meet_id could not match."""
    buckets, out = {}, []
    for race in races:
        key = _time_key(race)
        if key is None:
            out.append(race)                             # untouchable
        else:
            buckets.setdefault(key, []).append(race)

    for group in buckets.values():
        out.extend(_collapse_group(group))
    return out


# ===================================================================== #
#  XC RACE
# ===================================================================== #

def get_race_header(cur, meet_id, div_id):
    """Meet/course info for one XC race, plus the field's gender.

    ★ DRIVEN FROM `results`, NOT `meets`. `meets` is anet-only, so selecting
      FROM it returned no row for a tfrrs race and the route called abort(404).
      `results` always has the rows -- that is what makes the page exist at all
      -- so the base is a DISTINCT over the division's own rows and both
      metadata tables hang off it as LEFT JOINs.

      The subselect is DISTINCT because `results` holds one row per finisher;
      without it the header would repeat once per athlete.
    """
    cur.execute(f"""
        SELECT COALESCE(m.meet_name, mt.venue_name)          AS meet_name,
               {_xc_course_sql('r')}                         AS course_name,
               {_xc_distance_sql('r')}                       AS distance,
               COALESCE(m.division,
                        mt.division_distances -> %(divtext)s ->> 'div_name')
                                                             AS division,
               m.state                                       AS state,
               r.meet_id                                     AS meet_id,
               r.div_id                                      AS div_id,
               r.source                                      AS source,
               cd.difficulty                                 AS difficulty,
               -- FILTER because the lateral no longer restricts gender to M/F
               -- (it sorts by it instead), so junk values could reach mode().
               (SELECT mode() WITHIN GROUP (ORDER BY a.gender)
                        FILTER (WHERE a.gender IN ('M', 'F'))
                  FROM results r2
                  {_athlete_lateral('r2')}
                 WHERE r2.meet_id = %(meet)s
                   AND r2.div_id  = %(div)s
               ) AS gender
        FROM (SELECT DISTINCT meet_id, div_id, source
                FROM results
               WHERE meet_id = %(meet)s AND div_id = %(div)s) r
        LEFT JOIN meets m
               ON m.meet_id = r.meet_id
              AND m.div_id  = r.div_id
              AND m.source  = r.source
        {_tfrrs_join('r')}
        LEFT JOIN course_canonical cc
               ON cc.course_name = {_xc_course_sql('r')}
              AND round(cc.gps_lat::numeric,  5)
                = round(COALESCE(m.gps_lat,  mt.gps_lat)::numeric,  5)
              AND round(cc.gps_long::numeric, 5)
                = round(COALESCE(m.gps_long, mt.gps_long)::numeric, 5)
        LEFT JOIN course_difficulties cd
               ON cd.canonical_id = cc.canonical_id
              AND cd.distance_m   =
                  (round({_xc_distance_sql('r')} / 100.0) * 100)::int
        LIMIT 1
    """, {"meet": meet_id, "div": div_id, "divtext": str(div_id)})
    return cur.fetchone()


def get_race_results(cur, meet_id, div_id):
    """Every athlete's result in one XC race, fastest first."""
    cur.execute(f"""
        SELECT r.result_id,
               r.person_id,
               r.place,
               r.time_seconds,
               r.grade,
               r.school,
               r.speed_rating,
               r.date,
               {_name_sql('r')} AS name
        FROM results r
        {_athlete_lateral('r')}
        WHERE r.meet_id = %(meet)s
          AND r.div_id  = %(div)s
          AND r.time_seconds IS NOT NULL
        ORDER BY r.time_seconds ASC
    """, {"meet": meet_id, "div": div_id})
    return cur.fetchall()


@app.route("/race/xc/<int:meet_id>/<int:div_id>")
def race_xc(meet_id, div_id):
    from meet_compile import scoreRows, publishedScores

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header  = get_race_header(cur, meet_id, div_id)
            results = get_race_results(cur, meet_id, div_id)
            published = publishedScores(cur, meet_id)

    if header is None:
        abort(404)

    # format times for display
    for row in results:
        if row["time_seconds"] is not None:
            row["display_time"] = format_time(row["time_seconds"])
        else:
            row["display_time"] = "—"

    # the race date comes from the results (meets has no date column)
    race_date = results[0]["date"] if results else None

    # ★ PLACE FROM POSITION, NOT FROM results.place. The template already
    #   renders Place as loop.index because that column is not a
    #   within-division finishing position -- a division of nine carries values
    #   running past 60. Scoring has to use the same numbers the reader sees,
    #   or the scorers listed will not match the places in the table.
    ranked = [{**r, "place": i}
              for i, r in enumerate(results, start=1)
              if r.get("time_seconds") is not None]

    # ★ PUBLISHED FIRST, COMPUTED AS A FALLBACK. What the meet reported
    #   includes whatever local scoring applied -- byes, exhibition runners,
    #   an incomplete team scored anyway -- and recomputing would quietly
    #   overrule all of it. Only when nothing was published is it derived.
    #
    # ⚠ THE KEY IS (div_id, gender). One division can carry both, so a gender
    #   is tried before falling back to a genderless entry.
    pub = (published.get((div_id, header.get("gender")))
           or published.get((div_id, None)))

    # ★ ALWAYS COMPUTE, EVEN WHEN PUBLISHED SCORES EXIST. The published data
    #   carries points and a finishing place but NOT which runners scored --
    #   so on its own it cannot fill the 1-7 columns. Computing alongside gives
    #   the scorers while the published points stay authoritative.
    computed = scoreRows(ranked) if ranked else {"teams": [], "incomplete": []}
    by_school = {t["school"]: t for t in computed["teams"]}

    if pub:
        # Published points and order win; the scorers are grafted on by name.
        # ⚠ A NAME MISS IS SILENT AND HARMLESS -- meet_extras spells schools
        #   its own way ("Name" vs "rawName"), so a team whose spelling differs
        #   simply shows no scorers rather than the wrong ones.
        teams = [{**t, "runners": (by_school.get(t["school"], {})
                                   .get("runners", []))}
                 for t in pub]
        scores = {"teams": teams, "incomplete": computed["incomplete"],
                  "source": "published"}
    elif computed["teams"]:
        scores = {**computed, "source": "computed"}
    else:
        scores = {"teams": [], "incomplete": computed["incomplete"],
                  "source": "computed"}

    return render_template("race.html",
                           header=header,
                           results=results,
                           race_date=race_date,
                           scores=scores)


# ===================================================================== #
#  XC MEET
# ===================================================================== #

def get_meet_header(cur, meet_id):
    """Basic info for a meet — taken from any one of its divisions.

    ★ Driven from `results` for the same reason as get_race_header: a tfrrs
      meet has no `meets` row, so the old `FROM meets` returned None and the
      route 404'd.
    """
    cur.execute(f"""
        SELECT COALESCE(m.meet_name, mt.venue_name) AS meet_name,
               {_xc_course_sql('r')}                AS course_name,
               m.state                              AS state,
               r.meet_id                            AS meet_id
        FROM (SELECT DISTINCT meet_id, div_id, source
                FROM results WHERE meet_id = %(meet)s) r
        LEFT JOIN meets m
               ON m.meet_id = r.meet_id
              AND m.div_id  = r.div_id
              AND m.source  = r.source
        {_tfrrs_join('r')}
        -- Rows that resolved a name sort first, so a meet where only SOME
        -- divisions carry metadata still shows one.
        ORDER BY (COALESCE(m.meet_name, mt.venue_name) IS NOT NULL) DESC
        LIMIT 1
    """, {"meet": meet_id})
    return cur.fetchone()


def get_meet_divisions(cur, meet_id):
    """Every division in this meet, with how many results each has.

    ★ Grouped from `results`, so tfrrs divisions appear. The division NAME and
      DISTANCE come from the jsonb blob when `meets` has nothing, which is what
      turns a row of blanks into a usable link.
    """
    cur.execute(f"""
        SELECT r.div_id                              AS div_id,
               COALESCE(m.division,
                        mt.division_distances -> r.div_id::text ->> 'div_name')
                                                     AS division,
               {_xc_distance_sql('r')}               AS distance,
               count(r.result_id)                    AS n_results,
               mode() WITHIN GROUP (ORDER BY a.gender)
                   FILTER (WHERE a.gender IN ('M', 'F')) AS gender
        FROM results r
        LEFT JOIN meets m
               ON m.meet_id = r.meet_id
              AND m.div_id  = r.div_id
              AND m.source  = r.source
        {_tfrrs_join('r')}
        {_athlete_lateral('r')}
        WHERE r.meet_id = %(meet)s
        GROUP BY r.div_id, m.division, mt.division_distances,
                 m.distance, r.source
        ORDER BY division NULLS LAST, r.div_id
    """, {"meet": meet_id})
    return cur.fetchall()


@app.route("/meet/xc/<int:meet_id>")
def meet_xc(meet_id):
    from meet_compile import compiledResults, publishedScores

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header    = get_meet_header(cur, meet_id)
            divisions = get_meet_divisions(cur, meet_id)
            compiled  = compiledResults(cur, meet_id)

    # ★ THE MEET PAGE ONLY LISTS THE COMPILED RACES; each one has its own
    #   page. A compiled result IS a race -- it has a distance, a gender, a
    #   field and a set of team scores -- so it belongs at its own URL,
    #   linkable and shareable, rather than as a section of something else.
    #   Embedding them also meant a big invitational rendered several hundred
    #   rows per group into a page nobody asked for all of.
    compiled_index = [{"distance": g["distance"], "gender": g["gender"],
                       "n_results": len(g["results"]),
                       "n_divisions": len(g["divisions"]),
                       "n_teams": len(g["scores"]["teams"])}
                      for g in compiled]

    if header is None:
        abort(404)

    return render_template("meet.html", header=header, divisions=divisions,
                           compiled=compiled_index)


@app.route("/race/xc/<int:meet_id>/compiled/<int:distance>/<gender>")
def compiled_race(meet_id, distance, gender):
    """One compiled race: every division at this distance and gender, merged.

    ⚠ A REAL RACE PAGE FOR A RACE THAT DID NOT HAPPEN. Varsity and JV started
      separately; this merges them by time. The page says so, because a
      finishing order that was never contested is a different claim from one
      that was.
    """
    from meet_compile import compiledResults

    gender = (gender or "").upper()[:1]
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header = get_meet_header(cur, meet_id)
            groups = compiledResults(cur, meet_id)

    if header is None:
        abort(404)

    group = next((g for g in groups
                  if g["distance"] == distance and g["gender"] == gender), None)
    if group is None:
        abort(404)

    return render_template("compiled.html", header=header, group=group)


@app.route("/api/meet/xc/<int:meet_id>/compiled")
def api_meet_compiled(meet_id):
    """Compiled results and team scores, as JSON.

    Split out from the page so the compiled view can be loaded on demand --
    a big invitational is several thousand rows across four groups, and most
    visits only want one of them.
    """
    from meet_compile import compiledResults, publishedScores

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            compiled = compiledResults(cur, meet_id)
            published = publishedScores(cur, meet_id)

    return jsonify({
        "compiled": compiled,
        # Keys are tuples server-side; JSON needs strings.
        "published": {f"{d}|{g or ''}": v for (d, g), v in published.items()},
    })


# ===================================================================== #
#  TF RACE
# ===================================================================== #

def get_tf_race_header(cur, meet_id, div_id, event_id):
    cur.execute("""
        SELECT m.meet_name,
               m.event_short,
               m.distance_meters,
               m.division,
               m.state,
               m.is_indoor,
               m.meet_id,
               m.div_id,
               m.event_id,
               cd.difficulty,
               m.location_id
        FROM meets_tf m
        LEFT JOIN course_difficulties cd
               ON cd.course_name = 'TF:loc:' || m.location_id::text ||
                  CASE WHEN COALESCE(m.is_indoor, 0) = 1 THEN ':in' ELSE ':out' END
        WHERE m.meet_id  = %(meet)s
          AND m.div_id   = %(div)s
          AND m.event_id = %(event)s
        LIMIT 1
    """, {"meet": meet_id, "div": div_id, "event": event_id})
    return cur.fetchone()


def get_tf_race_results(cur, meet_id, div_id, event_id):
    cur.execute(f"""
        SELECT r.result_id,
               r.person_id,
               r.place,
               r.time_seconds,
               r.mark,
               r.is_field,
               r.grade,
               r.school,
               r.speed_rating,
               r.date,
               {_name_sql('r')} AS athlete_name,
               a.gender         AS gender,
               r.wind           AS wind
        FROM results_tf r
        {_athlete_lateral('r')}
        WHERE r.meet_id  = %(meet)s
          AND r.div_id   = %(div)s
          AND r.event_id = %(event)s
          AND (r.time_seconds IS NOT NULL OR r.mark IS NOT NULL)
        ORDER BY r.time_seconds ASC NULLS LAST
    """, {"meet": meet_id, "div": div_id, "event": event_id})
    return cur.fetchall()


def _field_gender(results):
    """Most common gender among athletes in this field ('M'/'F'/None)."""
    genders = [r["gender"] for r in results if r.get("gender") in ("M", "F")]
    if not genders:
        return None
    return max(set(genders), key=genders.count)


@app.route("/race/tf/<int:meet_id>/<int:event_id>/<int:div_id>")
def race_tf(meet_id, event_id, div_id):
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header  = get_tf_race_header(cur, meet_id, div_id, event_id)
            results = get_tf_race_results(cur, meet_id, div_id, event_id)

    if header is None:
        abort(404)

    header["gender"] = _field_gender(results)

    for row in results:
        if row["is_field"]:
            row["display_result"] = row["mark"] if row["mark"] else "—"
        elif row["time_seconds"] is not None:
            row["display_result"] = format_time(row["time_seconds"])
        else:
            row["display_result"] = "—"

    race_date = results[0]["date"] if results else None

    return render_template("race_tf.html",
                           header=header,
                           results=results,
                           race_date=race_date)


# ===================================================================== #
#  TF MEET
# ===================================================================== #

def get_tf_meet_header(cur, meet_id):
    """Basic info for a TF meet, from any one of its events."""
    cur.execute("""
        SELECT meet_name, state, is_indoor, meet_id
        FROM meets_tf
        WHERE meet_id = %(meet)s
        LIMIT 1
    """, {"meet": meet_id})
    return cur.fetchone()


def get_tf_meet_events(cur, meet_id):
    """Every event in this TF meet, with result counts."""
    cur.execute("""
        SELECT m.div_id,
               m.event_id,
               m.event_short,
               m.division,
               m.distance_meters,
               count(r.result_id) AS n_results
        FROM meets_tf m
        LEFT JOIN results_tf r
               ON r.meet_id  = m.meet_id
              AND r.div_id   = m.div_id
              AND r.event_id = m.event_id
        WHERE m.meet_id = %(meet)s
        GROUP BY m.div_id, m.event_id, m.event_short, m.division, m.distance_meters
        ORDER BY m.division, m.distance_meters NULLS LAST, m.event_short
    """, {"meet": meet_id})
    return cur.fetchall()


@app.route("/meet/tf/<int:meet_id>")
def meet_tf(meet_id):
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header = get_tf_meet_header(cur, meet_id)
            events = get_tf_meet_events(cur, meet_id)

    if header is None:
        abort(404)

    return render_template("meet_tf.html", header=header, events=events)


# ===================================================================== #
#  XC COURSE
# ===================================================================== #

def get_course_header(cur, course_name):
    """Difficulty and race/athlete counts for one course."""
    cur.execute("""
        SELECT cd.course_name,
               cd.difficulty,
               cd.n_results,
               cd.n_athletes
        FROM course_difficulties cd
        WHERE cd.course_name = %(key)s
        LIMIT 1
    """, {"key": "XC:" + course_name})
    return cur.fetchone()


def get_course_bests(cur, course_name, limit=25):
    """All-time best performances on this course, by speed rating."""
    cur.execute(f"""
        SELECT r.result_id,
               r.person_id,
               r.time_seconds,
               r.date,
               r.grade,
               r.school,
               r.speed_rating,
               m.distance,
               m.meet_id,
               m.div_id,
               m.meet_name,
               {_name_sql('r')} AS name,
               a.gender
        FROM results r
        JOIN meets m
             ON m.div_id = r.div_id
            AND m.source = r.source
        {_athlete_lateral('r')}
        WHERE m.course_name = %(course)s
          AND r.speed_rating IS NOT NULL
        ORDER BY r.speed_rating DESC
        LIMIT %(limit)s
    """, {"course": course_name, "limit": limit})
    return cur.fetchall()


def get_course_meets(cur, course_name, limit=50):
    """Meets held at this course, newest first.

    distance is per-DIVISION on `meets`, so one meet can carry several
    (varsity 5000m, frosh 3200m). array_agg collects them into one list
    per meet instead of collapsing to a single value.
    """
    cur.execute("""
        SELECT m.meet_id,
               m.meet_name,
               -- DISTINCT so a distance run by six divisions appears once.
               -- ORDER BY so the list is stable between page loads.
               -- FILTER drops NULLs, which would otherwise become a literal
               -- NULL *element* in the array (tfrrs XC rows have no distance).
               array_agg(DISTINCT m.distance ORDER BY m.distance)
                   FILTER (WHERE m.distance IS NOT NULL) AS distances,
               max(r.date)  AS last_date,
               count(*)     AS n_results
        FROM meets m
        JOIN results r
             ON r.div_id = m.div_id
            AND r.source = m.source
        WHERE m.course_name = %(course)s
        GROUP BY m.meet_id, m.meet_name
        ORDER BY max(r.date) DESC
        LIMIT %(limit)s
    """, {"course": course_name, "limit": limit})
    return cur.fetchall()


def get_course_distances(cur, course_name):
    """Which distances have been raced on this course, and how often."""
    cur.execute("""
        SELECT m.distance,
               count(*)     AS n_results,
               min(r.date)  AS first_date,
               max(r.date)  AS last_date
        FROM meets m
        JOIN results r
             ON r.div_id = m.div_id
            AND r.source = m.source
        WHERE m.course_name = %(course)s
          AND m.distance IS NOT NULL
        GROUP BY m.distance
        ORDER BY count(*) DESC
    """, {"course": course_name})
    return cur.fetchall()


@app.route("/school/<path:school_name>")
def school_page(school_name):
    """One school: its roster for a season, its meets, its best ever.

    ★ <path:>, NOT THE DEFAULT CONVERTER. School names are free text and
      genuinely contain slashes -- "Chisago Lakes/Rush City" is a real co-op.
      The default string converter stops at the first slash and would 404 every
      one of them.
    """
    from school import (schoolHeader, schoolYears, schoolRoster, schoolMeets,
                        schoolBest, schoolTopAthletes, currentSeason,
                        seasonLabel, storedYear)

    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        sport = "XC"

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header = schoolHeader(cur, school_name)
            if header is None:
                abort(404)

            years = schoolYears(cur, school_name)

            # ★ THE SCHOOL NAME LANDS ON THE HISTORY, NOT ON A YEAR. With no
            #   ?year the page is the whole programme, with the CURRENT roster
            #   on top. With one, the roster and the race list narrow to that
            #   season while the all-time tables stay put: the year is a filter
            #   on the history, not a different page.
            # ⚠ THE URL CARRIES THE LABEL, THE QUERIES TAKE THE STORED YEAR.
            #   A track season is stored under the year it opens in and named
            #   year + 1, so ?year=2026 on TF means stored 2025. Converting
            #   once here is what keeps the year bar, the roster and the meet
            #   list all describing the same season -- see school.py's header.
            raw = request.args.get("year")
            picked = int(raw) if raw and raw.isdigit() else None
            picked_stored = storedYear(sport, picked)

            year   = picked_stored or currentSeason(cur, school_name, sport)
            roster = schoolRoster(cur, school_name, year, sport) if year else []
            meets  = schoolMeets(cur, school_name, sport, year=picked_stored)
            best   = schoolBest(cur, school_name, sport)
            top    = schoolTopAthletes(cur, school_name, sport, limit=25)

    return render_template("school.html", school=school_name, header=header,
                           years=years, year=seasonLabel(sport, year),
                           sport=sport,
                           roster=roster, meets=meets, best=best, top=top,
                           picked=picked)


@app.route("/course/<course_name>")
def course(course_name):
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header    = get_course_header(cur, course_name)
            bests     = get_course_bests(cur, course_name)
            meets     = get_course_meets(cur, course_name)
            distances = get_course_distances(cur, course_name)

    for row in bests:
        row["display_time"] = format_time(row["time_seconds"])

    return render_template("course.html",
                           course_name=course_name,
                           header=header,
                           bests=bests,
                           distances=distances,
                           meets=meets)


# ===================================================================== #
#  TF VENUE
# ===================================================================== #

def get_tf_venue_label(cur, location_id, is_indoor):
    """Most common meet name at this venue — our stand-in for a venue name."""
    cur.execute("""
        SELECT m.meet_name
        FROM meets_tf m
        WHERE m.location_id = %(loc)s
          AND COALESCE(m.is_indoor, 0) = %(indoor)s
          AND m.meet_name IS NOT NULL
        GROUP BY m.meet_name
        ORDER BY count(*) DESC
        LIMIT 1
    """, {"loc": location_id, "indoor": 1 if is_indoor else 0})
    row = cur.fetchone()
    return row["meet_name"] if row else None


def get_tf_venue_difficulty(cur, location_id, is_indoor):
    """The engine's difficulty row for this venue, if it has one."""
    key = f"TF:loc:{location_id}:{'in' if is_indoor else 'out'}"
    cur.execute("""
        SELECT difficulty, n_results, n_athletes
        FROM course_difficulties
        WHERE course_name = %(key)s
        LIMIT 1
    """, {"key": key})
    return cur.fetchone()


def get_tf_venue_bests(cur, location_id, is_indoor, limit=25):
    """Best-rated performances at this venue. Filters meets by location FIRST,
    so we never scan all of results_tf."""
    cur.execute(f"""
        WITH venue_meets AS (
            SELECT meet_id, div_id, event_id, meet_name
            FROM   meets_tf
            WHERE  location_id = %(loc)s
              AND  COALESCE(is_indoor, 0) = %(indoor)s
        )
        SELECT r.result_id, r.person_id, r.time_seconds, r.mark, r.is_field,
               r.date, r.grade, r.school, r.speed_rating, r.event_short,
               vm.meet_id, vm.div_id, vm.event_id, vm.meet_name,
               {_name_sql('r')} AS name
        FROM   venue_meets vm
        JOIN   results_tf r
               ON r.meet_id  = vm.meet_id
              AND r.div_id   = vm.div_id
              AND r.event_id = vm.event_id
        {_athlete_lateral('r')}
        WHERE  r.speed_rating IS NOT NULL
        ORDER  BY r.speed_rating DESC
        LIMIT  %(limit)s
    """, {"loc": location_id, "indoor": 1 if is_indoor else 0, "limit": limit})
    return cur.fetchall()

def get_tf_venue_meets(cur, location_id, is_indoor, limit=50):
    """Meets held at this venue, newest first."""
    cur.execute("""
        SELECT m.meet_id,
               MAX(m.meet_name) AS meet_name,
               MAX(r.date)      AS last_date,
               COUNT(*)         AS n_results
        FROM   meets_tf m
        JOIN   results_tf r
               ON r.meet_id = m.meet_id AND r.div_id = m.div_id
              AND r.event_id = m.event_id
        WHERE  m.location_id = %(loc)s
          AND  COALESCE(m.is_indoor,0) = %(indoor)s
        GROUP  BY m.meet_id
        ORDER  BY MAX(r.date) DESC
        LIMIT  %(limit)s
    """, {"loc": location_id, "indoor": 1 if is_indoor else 0, "limit": limit})
    return cur.fetchall()


@app.route("/venue/tf/<int:location_id>/<indoor>")
def venue_tf(location_id, indoor):
    is_indoor = (indoor == "in")

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            label      = get_tf_venue_label(cur, location_id, is_indoor)
            difficulty = get_tf_venue_difficulty(cur, location_id, is_indoor)
            bests      = get_tf_venue_bests(cur, location_id, is_indoor)
            venue_meets = get_tf_venue_meets(cur, location_id, is_indoor)

    for row in bests:
        if row["is_field"]:
            row["display_result"] = row["mark"] if row["mark"] else "—"
        elif row["time_seconds"] is not None:
            row["display_result"] = format_time(row["time_seconds"])
        else:
            row["display_result"] = "—"

    return render_template("venue_tf.html",
                           label=label,
                           location_id=location_id,
                           is_indoor=is_indoor,
                           difficulty=difficulty,
                           bests=bests,
                           venue_meets=venue_meets)

def pad_pool_pairs(panels):
    """For each (sport, scope, pool), make the athlete and performance lists
    the same length by padding the shorter with None.

    WHY: the two boards share one CSS grid per pool, and a grid's rows only
    line up if both sides have the same row count. A None entry renders as a
    blank cell -- the empty space under the shorter board.

    Mutates in place; panels is the nested dict from group_panels().
    """
    for sport in panels:
        for scope in panels[sport]:
            boards = panels[sport][scope]
            athlete = boards.get("athlete", {})
            perf    = boards.get("performance", {})

            # every pool that appears on either board
            pools = set(athlete) | set(perf)
            for pool in pools:
                a = athlete.get(pool, [])
                p = perf.get(pool, [])
                n = max(len(a), len(p))          # the taller side sets the count
                athlete.setdefault(pool, a)
                perf.setdefault(pool, p)
                # pad each up to n with None -> blank grid cells
                athlete[pool] = a + [None] * (n - len(a))
                perf[pool]    = p + [None] * (n - len(p))
    return panels

from flask import request, jsonify

@app.route("/search/api")
def search_api():
    """The typeahead endpoint. Used by the topbar and by every picker.

    ★ KEYWORDS, AND-ED. The query is split on whitespace and EVERY token must
      appear somewhere in the row. Word order stops mattering, which is the
      whole problem with this data:

          stored:  "2026 arcadia invitational"
          typed:   "arcadia invitational"     -> matches (order-free)
          typed:   "arcadia 2026"             -> matches (year is a token)
          typed:   "invitational arcadia"     -> matches

      A single-string match, anchored or not, gets all three of those wrong,
      because meets are stored with the year and the edition number in FRONT
      of the distinctive part.

    ⚠ THE TWO VERSIONS BEFORE THIS WERE BOTH WRONG, differently.
      1. Left-anchored prefix: "arcadia invitational" could never find "2026
         arcadia invitational" at all.
      2. Worse, a trigram fallback fired whenever the prefix returned fewer
         than five rows -- which for a meet is always -- so the list was
         similarity-ranked NOISE that reshuffled on every keystroke. That is
         the "typing the l makes them vanish" bug. It was never the letter.

    ★ RANKING, IN ORDER: rows where the first token starts the text, then rows
      where every token sits at a word boundary, then the shortest. Without it
      "arcadia" surfaces "Allen East JH Tri--Arcadia, McComb" above "Arcadia".

    ⚠ NO FUZZY FALLBACK. An honest short list beats a long unstable one, and
      the fallback was the single biggest source of the noise being complained
      about. A typo now returns nothing, which is at least legible.

    `kind` filters to one type -- what lets a meet picker get meets rather
    than competing with 15.5M athletes.
    """
    raw = (request.args.get("q") or "").strip().lower()
    if len(raw) < 2:
        return jsonify([])

    # Punctuation is separator, not content: "mt. sac" and "mt sac" and
    # "arcadia-loup" should all tokenise the same way.
    tokens = [t for t in re.split(r"[^a-z0-9]+", raw) if t][:6]
    if not tokens:
        return jsonify([])

    kind = (request.args.get("kind") or "").strip()
    limit = min(int(request.args.get("limit") or 10), 40)

    where, params = [], {"lim": limit, "first": tokens[0] + "%"}
    for i, tok in enumerate(tokens):
        where.append(f"(search_text LIKE %(t{i})s OR search_last LIKE %(t{i})s)")
        params[f"t{i}"] = f"%{tok}%"
        params[f"w{i}"] = f"% {tok}%"
    if kind:
        where.append("kind = %(kind)s")
        params["kind"] = kind

    # How many tokens land on a word boundary. A row matching every token at a
    # word start is a better hit than one matching them mid-word.
    word_score = " + ".join(
        f"(CASE WHEN search_text LIKE %(w{i})s OR search_text LIKE %(t{i}_start)s"
        f" THEN 1 ELSE 0 END)" for i in range(len(tokens)))
    for i, tok in enumerate(tokens):
        params[f"t{i}_start"] = f"{tok}%"

    # Meets sort on the year, people and schools on how much they raced.
    # Both fall back to the other so a tie is still broken sensibly.
    second_key = ("sort_year DESC NULLS LAST, sort_count DESC NULLS LAST"
                  if kind == "meet" else
                  "sort_count DESC NULLS LAST, sort_year DESC NULLS LAST")

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT kind, label, sublabel, link
                FROM   search_index
                WHERE  {' AND '.join(where)}
                -- ★ RECENCY BEFORE LENGTH, AND DROPPING IT WAS THE BUG.
                --   search_index carries sort_year and sort_count; the older
                --   _run_search used both and this rewrite ignored them, so
                --   the final tiebreak was length(search_text) -- which means
                --   a short 2010 name beats a longer 2025 one every time.
                --   That is why the list "switched" partway through typing:
                --   as tokens narrowed the match set, length took over.
                --
                --   Someone searching a meet almost always wants the recent
                --   edition; the older ones are still there, just below.
                -- ⚠ WORD MATCHES BEAT "STARTS WITH", and the order of these
                --   lines is the whole ranking.
                --
                --   Ranking "starts with the first token" highest is the
                --   obvious choice and it is WRONG FOR THIS DATA: meets are
                --   stored with the year in front, so "2025 colorado state
                --   championships" never starts with "colorado" and a 2009
                --   meet named without a year always outranked it.
                --
                -- ★ AND THE SECOND KEY DEPENDS ON THE KIND, because the two
                --   populations answer different questions:
                --
                --     meet    -> RECENCY. Thirty editions of one name, and the
                --                recent one is nearly always the one meant.
                --     athlete -> HOW MUCH THEY RACED. Fifteen million rows,
                --                most of them one-race entries; someone
                --                searching a name wants the runner with a
                --                career, not a namesake with a single result.
                --     school  -> HOW MANY ATHLETES. Same argument: the real
                --                programme outranks the one-off entry.
                --
                --   sort_count carries races for an athlete and athletes for a
                --   school, so one column serves both.
                ORDER  BY ({word_score}) DESC,
                          {second_key},
                          (search_text LIKE %(first)s) DESC,
                          length(search_text)
                LIMIT  %(lim)s
            """, params)
            rows = cur.fetchall()

    return jsonify(rows)


def _parse_year(q):
    """Pull a 4-digit year (1990-2030) out of the query text.
    Returns (year_or_None, query_without_year)."""
    import re
    m = re.search(r'\b(19[9]\d|20[0-3]\d)\b', q)
    if not m:
        return None, q
    yr = m.group(1)
    cleaned = (q[:m.start()] + q[m.end():]).strip()
    return yr, cleaned


def _run_search(q, kind, year_filter, offset):
    """Returns (results, per_kind_counts, available_years)."""
    # a year typed in the box acts as a filter too
    typed_year, q_clean = _parse_year(q)
    year = year_filter or typed_year
    needle = q_clean.lower().strip()

    # ★ SUBSTRING, NOT PREFIX, AND THIS WAS A REAL BUG.
    #
    #   The old match was `search_text LIKE 'arcadia invitational%'` --
    #   LEFT-ANCHORED. Meets are stored with the year in front:
    #
    #       2026 arcadia invitational
    #       27th annual arcadia invite
    #       arcadia-loup city rebel invite
    #
    #   so "Arcadia Invitational" could never find the 2026 one, and typing
    #   more of the name made MORE results vanish rather than fewer. Anchoring
    #   at the start assumes people type a name from its beginning, and for
    #   meets they do not -- they type the distinctive part.
    #
    # ⚠ A LEADING WILDCARD CANNOT USE A BTREE INDEX. It CAN use a GIN trigram
    #   index, which the athlete branch above already relies on (search_text
    #   %% ...), so pg_trgm is installed. If this is slow, the missing piece is
    #       CREATE INDEX CONCURRENTLY idx_search_text_trgm
    #           ON search_index USING gin (search_text gin_trgm_ops);
    #   not a return to prefix matching.
    #
    # ★ PREFIX MATCHES STILL RANK FIRST. Substring matching alone would bury
    #   "Arcadia" under "Allen East JH Tri--Arcadia, McComb"; the ORDER BY
    #   below puts a prefix hit ahead of a mid-string one.
    where = ["(search_text LIKE %(sub)s OR search_last LIKE %(sub)s)"]
    params = {"sub": f"%{needle}%", "p": needle + "%"}

    if kind != "all":
        where.append("kind = %(k)s")
        params["k"] = kind
    if year:
        # match sort_year OR the name containing the year (2026 Arcadia)
        where.append("(sort_year = %(y)s OR search_text LIKE %(yp)s)")
        params["y"] = int(year)
        params["yp"] = f"%{year}%"

    where_sql = " AND ".join(where)

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # the page of results, ordered by your sort columns
            cur.execute(f"""
                SELECT kind, label, sublabel, link, sort_year, sort_count
                FROM   search_index
                WHERE  {where_sql}
                -- ★ A PREFIX HIT OUTRANKS A MID-STRING ONE. Without this,
                --   substring matching buries "Arcadia" under "Allen East JH
                --   Tri--Arcadia, McComb" -- both match, but only one is what
                --   was meant. Booleans sort false < true, hence DESC.
                ORDER  BY (search_text LIKE %(p)s) DESC,
                          sort_year DESC, sort_count DESC, length(search_text)
                LIMIT  %(lim)s OFFSET %(off)s
            """, {**params, "lim": PAGE_SIZE, "off": offset})
            results = cur.fetchall()

            # per-kind counts for the tabs (ignore the kind filter for counts)
            count_where = ["(search_text LIKE %(p)s OR search_last LIKE %(p)s)"]
            cparams = {"p": prefix}
            if year:
                count_where.append("(sort_year = %(y)s OR search_text LIKE %(yp)s)")
                cparams["y"] = int(year); cparams["yp"] = f"%{year}%"
            cur.execute(f"""
                SELECT kind, COUNT(*) AS n
                FROM search_index
                WHERE {' AND '.join(count_where)}
                GROUP BY kind
            """, cparams)
            counts = {r["kind"]: r["n"] for r in cur.fetchall()}

            # distinct years -- from q+kind ONLY, never the year filter itself,
            # or picking a year collapses the dropdown to just that year.
            yr_where = ["(search_text LIKE %(p)s OR search_last LIKE %(p)s)"]
            yparams = {"p": prefix}
            if kind != "all":
                yr_where.append("kind = %(k)s")
                yparams["k"] = kind
            cur.execute(f"""
                SELECT DISTINCT sort_year FROM search_index
                WHERE {' AND '.join(yr_where)} AND sort_year > 0
                ORDER BY sort_year DESC
            """, yparams)
            years = [r["sort_year"] for r in cur.fetchall()]

    return results, counts, years

PAGE_SIZE = 30

@app.route("/search")
def search_page():
    """Full results page. Query params: q, kind (tab), year, offset."""
    q      = (request.args.get("q") or "").strip()
    kind   = request.args.get("kind") or "all"
    year   = request.args.get("year")            # optional year filter
    offset = int(request.args.get("offset") or 0)

    results, counts, years = _run_search(q, kind, year, offset)

    # Load More sends ?offset=N and wants JSON, not a full page
    if request.args.get("format") == "json":
        return jsonify(results)

    return render_template("search.html",
                           q=q, kind=kind, year=year,
                           results=results,
                           counts=counts,        # per-tab counts
                           years=years,          # for the year dropdown
                           offset=offset,
                           page_size=PAGE_SIZE)

# ===================================================================== #
#  CONVERSIONS  — paste these two routes into app.py
# ===================================================================== #
#
# Depends on the validated math module:
from conversions import convert_spread
# (and the usual: from flask import request, jsonify, render_template)

# Default TF distances shown in the TF table. Mile is outdoor (1609.34m).
_TF_DEFAULT_DISTANCES = [
    (800,     "800m"),
    (1500,    "1500m"),
    (1609.34, "Mile"),
    (3000,    "3000m"),
    (3200,    "3200m"),
    (5000,    "5000m"),
    (10000,   "10,000m"),
]

# Pools offered in the source selector (bare pools -- sport is separate).
_POOLS = [
    ("hs_m", "HS Boys"), ("hs_f", "HS Girls"),
    ("college_m", "College Men"), ("college_f", "College Women"),
    ("ms_m", "MS Boys"), ("ms_f", "MS Girls"),
]


def _default_xc_courses(cur, n=10):
    cur.execute("""
        SELECT substring(course_name from 4) AS name,
               difficulty, common_distance
        FROM   course_common_distance
        ORDER  BY n_results DESC
        LIMIT  %(n)s
    """, {"n": n})
    # `or 0.0` would swallow a course genuinely fitted at 0.0 AND turn a NULL
    # (no fitted cell) into a claim of averageness. Pass NULL through as None
    # so the conversion layer can tell "unrated" from "average".
    return [{"course_name": r["name"],
             "difficulty": r["difficulty"],
             "distance":   r["common_distance"] or 5000.0}
            for r in cur.fetchall()]


@app.route("/conversions")
def conversions_page():
    """The conversions tool page. Renders default courses + distances; the
       actual computing happens client-side via /api/convert."""
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            xc_courses = _default_xc_courses(cur, 10)

    return render_template("conversions.html",
                           xc_courses=xc_courses,
                           tf_distances=_TF_DEFAULT_DISTANCES,
                           pools=_POOLS)


@app.route("/api/convert", methods=["POST"])
def api_convert():
    """Compute one source spread across the XC + TF targets. Thin wrapper over
       convert_spread -- all the math is in the validated module."""
    body = request.get_json(force=True) or {}

    source   = dict(body.get("source", {}))        # copy: we normalise it below
    pool     = source.get("pool") or "hs_m"        # output pool = source pool
    weather_out = body.get("weather_out")          # per-table output weather

    # NO COURSE CHOSEN -> DO NOT ASSERT A DIFFICULTY.
    #
    # The client sends "difficulty": 0.0 whether or not a course was picked,
    # and conversions.resolve_difficulty treats an explicit 0.0 as a deliberate
    # "this venue is exactly average" -- so the source resolved NEUTRAL while
    # the targets resolved the sport's TYPICAL venue. Different values on the
    # two sides means the default stops cancelling and the round trip breaks:
    #
    #     541s / 3200m / no course  ->  8:51.87 at 3200m / no course
    #
    # Deleting the key (rather than setting it to 0.0) is what lets
    # resolve_difficulty supply the SAME default at both ends, so a blank
    # course means one thing everywhere and 541 comes back as 541.
    if not source.get("course") and not source.get("canonical_id") \
            and not source.get("location_id"):
        source.pop("difficulty", None)

    # XC targets: each course, at its difficulty. XC distance defaults to 5000
    # (the standard); the course carries the difficulty + name (weather soil).
    xc_targets = []
    for c in body.get("xc_courses", []):
        xc_targets.append({
            "label":      c.get("label"),
            "distance":   c.get("distance", 5000.0),
            "pool":       pool,
            # c.get("difficulty") with NO 0.0 fallback: a course with no
            # fitted cell should fall through to the sport default, not be
            # asserted average. resolve_difficulty reads absent-or-None as
            # "unspecified" and only an explicit number as a choice.
            "difficulty": c.get("difficulty"),
            "course":     c.get("course"),         # for weather soil sensitivity
            "weather":    weather_out,
        })

    # TF targets: each distance, at a TYPICAL track.
    #
    # This used to pass difficulty 0.0 with the comment "flat track". A track
    # is standard in SHAPE, not in speed: fitted TF cells run about -0.016 to
    # -0.045, so 0.0 claimed every track is exactly average when none is.
    # No key at all -> conversions.resolve_difficulty supplies the sport
    # default, the same one the source gets.
    tf_targets = []
    for d in body.get("tf_distances", []):
        tf_targets.append({
            "label":    d.get("label"),
            "distance": d.get("distance"),
            "pool":     pool,
            "weather":  weather_out,
        })

    result = convert_spread(source, xc_targets, tf_targets)
    return jsonify(result)

@app.route("/api/course_search")
def course_search():
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify([])
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT substring(course_name from 4) AS name,
                       difficulty, distance, n
                FROM   course_distances
                WHERE  lower(course_name) LIKE %(q)s
                ORDER  BY (SELECT SUM(n) FROM course_distances c2
                           WHERE c2.course_name = course_distances.course_name) DESC,
                          n DESC
            """, {"q": "xc:%" + q + "%"})
            rows = cur.fetchall()
    # group distances under each course
    courses = {}
    for r in rows:
        c = courses.setdefault(r["name"], {"name": r["name"],
                                           "difficulty": r["difficulty"],
                                           "distances": []})
        c["distances"].append({"distance": r["distance"], "n": r["n"]})
    return jsonify(list(courses.values())[:8])

@app.route("/api/athlete_results")
def athlete_results():
    pid = request.args.get("person_id")
    sport = request.args.get("sport", "XC")
    if sport == "XC":
        sql = """
            SELECT r.result_id, r.date, r.time_seconds, r.speed_rating,
                   m.meet_name
            FROM results r
            LEFT JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
            WHERE r.person_id = %(pid)s AND r.speed_rating IS NOT NULL
            ORDER BY r.date DESC LIMIT 50
        """
    else:
        sql = """
            SELECT r.result_id, r.date, r.time_seconds, r.speed_rating,
                   r.event_short,
                   (SELECT meet_name FROM meets_tf m
                    WHERE m.meet_id = r.meet_id LIMIT 1) AS meet_name
            FROM results_tf r
            WHERE r.person_id = %(pid)s AND r.speed_rating IS NOT NULL
            ORDER BY r.date DESC LIMIT 50
        """
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, {"pid": pid})
            return jsonify(cur.fetchall())


from rankings import (parseFilters, getPerformanceRankings, getPrRankings,
                      getAbilityRankings, rankOf)


@app.route("/api/rankings")
def api_rankings():
    f, err = parseFilters(request.args)
    if err:
        return jsonify({"error": err}), 400

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                rows = {"performance": getPerformanceRankings,
                        "pr": getPrRankings,
                        "ability": getAbilityRankings}[f["board"]](cur, f)
            except psycopg2.errors.UndefinedColumn as exc:
                # ★ AN HONEST 400 BEATS AN HTML STACK TRACE. Unhandled, Flask
                #   answers with its debug PAGE, and the frontend reports
                #   "Unexpected token '<'" -- an error about JSON parsing,
                #   which sends whoever reads it entirely the wrong way.
                #
                # ⚠ AND IT REPORTS WHICH COLUMN, rather than naming one.
                #   The first version said "the distance column is not there
                #   yet" for every UndefinedColumn, so a board failing on some
                #   OTHER missing column produced a confident, wrong diagnosis
                #   -- worse than the stack trace it replaced, because it
                #   looked like an answer.
                conn.rollback()
                detail = str(exc).strip().splitlines()[0]
                return jsonify({"error": f"This board needs a rebuilt "
                                         f"ranking_results \u2014 {detail}"}), 400

    return jsonify({"filters": f, "count": len(rows),
                    # ! national_bias IS ABOUT THE RATING SCALE, so it does not
                    #   apply to a board of raw times. The per-state offset is
                    #   in speed_rating; a clock has no such thing.
                    "national_bias": (f["state"] is None
                                      and f["board"] != "pr"),
                    "rows": rows})

@app.route("/api/teams")
def api_teams():
    """One page of a team board.

    ★ THE RANKING IS A MEET. team_season holds the finish order of a
      hypothetical meet per (scope, pool, sport, season) -- every team's top
      seven entered, sorted by season rating, scored with the ordinary rules.
      See team_rank.py for why the teams are raced rather than having their
      ratings averaged.

    ★ AND WHEN A FILTER SPANS SEASONS, ANOTHER MEET IS RUN. Three seasons of
      stored boards hold three first places; the only honest way to get one
      is to race the selected teams against each other, which teams.serveBoard
      does whenever the field fits under its ceiling. `raced` in the response
      says whether that happened, so the page can explain what its rank
      column means instead of guessing at the rule a second time.

    national_bias is louder here than on the athlete boards on purpose: the
    per-state offset lands on all five scorers at once and pushes them the
    same way, instead of being one athlete's error.
    """
    f, err = parseTeamFilters(request.args)
    if err:
        return jsonify({"error": err}), 400

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                rows, info = serveBoard(cur, f)
            except psycopg2.errors.UndefinedTable:
                # ! THE HONEST 400 THE RANKINGS ROUTE ALREADY LEARNED TO GIVE.
                #   Unhandled, Flask answers with its debug PAGE and the
                #   frontend reports "Unexpected token '<'" -- an error about
                #   JSON parsing, which sends the reader entirely the wrong way.
                conn.rollback()
                return jsonify({"error": "Team rankings have not been built "
                                         "yet \u2014 run "
                                         "racecast/build_team_season.py after "
                                         "build_ranking_results.py."}), 400
            except Exception:
                # ★ AND A JSON-SHAPED 500 FOR EVERYTHING ELSE, because this
                #   endpoint is only ever read by fetch(). A KeyError here
                #   reached the browser as "Unexpected token '<' ... is not
                #   valid JSON" -- a message about parsing, for a cursor
                #   mistake, which is exactly the wrong direction to send
                #   somebody. The traceback still goes to the server log
                #   where it belongs; the client gets a sentence.
                conn.rollback()
                app.logger.exception("/api/teams failed")
                return jsonify({"error": "Team rankings failed to load. The "
                                         "server log has the traceback."}), 500

    # ! f IS SENT BACK AFTER serveBoard, NOT BEFORE. It picks the sort, and
    #   the page draws its header arrow from what came back -- so a response
    #   describing the filters it was asked for rather than the ones it
    #   served would put the arrow on a column the board is not sorted by.
    return jsonify({"filters": f, "count": len(rows),
                    "board_scope": f["board_scope"],
                    "national_bias": f["board_scope"] == "usa",
                    **info, "rows": rows})


@app.route("/api/courses")
def api_courses():
    """One page of the course board.

    ★ THE NUMBER IS THE ENGINE'S OWN. course_rank is built from
      course_difficulties, which the solve re-centres to mean zero every
      iteration -- so difficulty reads as "harder than an average course" and
      not as a score anybody chose. See build_course_rank.py.

    ⚠ AND IT IS ALREADY DISTANCE-NEUTRAL, applied after normalisation, which
      is the only reason 5k and 8k courses can share one ranking.
    """
    f, err = parseCourseFilters(request.args)
    if err:
        return jsonify({"error": err}), 400

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                rows = getCourseRankings(cur, f)
                total = countCourses(cur, f)
            except psycopg2.errors.UndefinedTable:
                # ! THE HONEST 400 THE OTHER BOARDS ALREADY LEARNED TO GIVE.
                #   Unhandled, Flask answers with its debug PAGE and the
                #   frontend reports "Unexpected token '<'" -- an error about
                #   JSON parsing, which sends the reader the wrong way.
                conn.rollback()
                return jsonify({"error": "Course rankings have not been built "
                                         "yet \u2014 run "
                                         "racecast/build_course_rank.py after "
                                         "the engine writes "
                                         "course_difficulties."}), 400
            except Exception:
                conn.rollback()
                app.logger.exception("/api/courses failed")
                return jsonify({"error": "Course rankings failed to load. The "
                                         "server log has the traceback."}), 500

    return jsonify({"filters": f, "count": len(rows), "total": total,
                    "rows": rows})


@app.route("/api/rankings/rank")
def api_rankings_rank():
    """Which page of the CURRENT board is this athlete on?

    Takes the same filter parameters as /api/rankings, plus person_id, and
    returns the athlete's 1-based position and the offset that lands on their
    page -- so the caller does one round trip, not a scan.

    ⚠ ABILITY BOARD ONLY, and it says so rather than guessing. The performance
      board bounds its candidate set with LIMIT before deduping, so a rank
      computed over it is a rank within that window, not within the corpus.
      Returning a plausible wrong number is worse than refusing.

    ⚠ This SORTS THE FILTERED SET. It is a click, not a page load. If it ever
      moves onto the page-load path it needs a different implementation.
    """
    f, err = parseFilters(request.args)
    if err:
        return jsonify({"error": err}), 400

    # ★ ALL THREE BOARDS NOW, WITH ONE RESTRICTION. The result boards are
    #   ranked by COUNTING rows that beat the athlete's own best -- exact and
    #   cheap, but the comparison reproduces the DEFAULT ordering only. A
    #   custom sort would need its direction and NULLS LAST handling mirrored
    #   in the count, which is the trap that made the ability board's general
    #   path a full sort.
    if f["board"] in ("performance", "pr"):
        natural, natural_dir = (("time", "ASC") if f["board"] == "pr"
                                else ("rating", "DESC"))
        if f["sort"] != natural or f["dir"] not in ("", natural_dir):
            return jsonify({"error": "Jumping works on this board when it is "
                                     f"sorted by {natural}."}), 400

    raw = (request.args.get("person_id") or "").strip()
    if not raw.isdigit():
        return jsonify({"error": "person_id is required."}), 400
    person_id = int(raw)

    with getConn() as conn:
        with conn.cursor() as cur:
            rank = rankOf(cur, f, person_id)

    if rank is None:
        # Not an error -- they are genuinely absent under these filters, most
        # often because min_races excludes them. Say WHICH, so the user can
        # act on it instead of assuming the search is broken.
        reason = ("Not on this board \u2014 no result at this distance under "
                  "the current filters."
                  if f["board"] == "pr" else
                  "Not on this board \u2014 no rated race under the current "
                  "filters."
                  if f["board"] == "performance" else
                  f"Not on this board \u2014 they may have fewer than "
                  f"{f['min_races']} races, or be outside the current "
                  f"filters.")
        return jsonify({"found": False, "reason": reason})

    limit = f["limit"]
    return jsonify({"found": True,
                    "rank": rank,
                    "offset": ((rank - 1) // limit) * limit})


@app.route("/predictions")
def predictions_page():
    return render_template("predictions.html")


@app.route("/api/predict/status")
def api_predict_status():
    """Is the model trained yet? The page asks on load and shows the reason.

    Separate from the predict endpoints so the page can put an honest banner
    up front instead of letting someone fill in a form and only then discover
    nothing is behind it.
    """
    from predict import modelStatus
    return jsonify(modelStatus())


def _target(args):
    """The target race, from the request. See predict.py for the three modes.

    ⚠ VALIDATED HERE, NOT IN predict.py. That module should receive a target
      it can trust; a bad mode is a request error and belongs in the layer
      that owns requests.
    """
    mode = (args.get("mode") or "meet").strip()
    if mode not in ("meet", "rerun", "manual"):
        return None, "mode must be meet, rerun or manual"

    t = {"mode": mode}
    if mode in ("meet", "rerun"):
        raw = (args.get("meet_id") or "").strip()
        if not raw.isdigit():
            return None, "meet_id is required for that mode"
        t["meet_id"] = int(raw)
        t["div_id"] = args.get("div_id")
        t["sport"] = args.get("sport") or "XC"
        if mode == "rerun":
            # The year the re-run is FOR. Defaults to the current season, so
            # "run last year's state meet again" needs no extra input.
            t["year"] = args.get("year")
    else:
        t["date"] = (args.get("date") or "").strip()
        t["distance"] = args.get("distance")
        t["course"] = args.get("course")
        t["sport"] = args.get("sport") or "XC"
        if not t["date"]:
            return None, "date is required for a manual target"
    return t, None


@app.route("/api/predict/field")
def api_predict_field():
    """Who ran a meet, grouped by school, marked returning or not.

    ★ THIS WORKS WITHOUT THE MODEL, and that is the point of having it. The
      field is a database question -- who entered, who is still racing -- so
      the page can show a real roster and let it be edited long before there
      is anything to predict with.
    """
    from predict import meetField

    meet = (request.args.get("meet_id") or "").strip()
    if not meet.isdigit():
        return jsonify({"error": "meet_id is required."}), 400

    div = (request.args.get("div_id") or "").strip()
    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        return jsonify({"error": "sport must be XC or TF."}), 400

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            out = meetField(cur, int(meet), int(div) if div.isdigit() else None,
                            sport)
    return jsonify(out)


@app.route("/api/predict/squad")
def api_predict_squad():
    """Everyone racing for a school this season, best first.

    Serves both "add a team that was not at the meet" (take the first seven)
    and "add one more runner to a team" (show the rest). Works without the
    model -- it is a database question.
    """
    from predict import schoolSquad

    school = (request.args.get("school") or "").strip()
    if not school:
        return jsonify({"error": "school is required."}), 400
    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        return jsonify({"error": "sport must be XC or TF."}), 400

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            out = schoolSquad(cur, school, sport)
    return jsonify(out)


@app.route("/api/predict/individual")
def api_predict_individual():
    from predict import predictIndividual

    # ★ ONE PARAMETER, ONE OR MANY VALUES. Comparing two athletes at the same
    #   race is the obvious next question after predicting one, and it is the
    #   same request -- so `person_id` takes a comma-separated list rather than
    #   the page needing a second endpoint for the plural case.
    ids = [s.strip() for s in (request.args.get("person_id") or "").split(",")
           if s.strip()]
    if not ids or not all(i.isdigit() for i in ids):
        return jsonify({"error": "person_id is required."}), 400
    if len(ids) > 12:
        return jsonify({"error": "Twelve athletes at most."}), 400

    target, err = _target(request.args)
    if err:
        return jsonify({"error": err}), 400

    try:
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                results = [predictIndividual(cur, int(i), target) for i in ids]
                # A single athlete returns the bare object, so nothing that
                # already reads this endpoint has to change.
                out = (results[0] if len(results) == 1
                       else {"available": True, "athletes": results})
    except NotImplementedError:
        # ★ THE EXPECTED PATH UNTIL THE MODEL EXISTS, and it is not an error.
        #   The page shows this as "not available yet", the same as a missing
        #   checkpoint -- because from the outside they are the same thing.
        return jsonify({"available": False,
                        "reason": "The prediction model is not wired up yet."})
    return jsonify(out)


@app.route("/api/predict/team")
def api_predict_team():
    from predict import predictTeam

    target, err = _target(request.args)
    if err:
        return jsonify({"error": err}), 400

    # ⚠ SCHOOLS ARE OPTIONAL WHEN A MEET IS GIVEN, AND DEMANDING THEM WAS A
    #   BUG. The page re-runs a real meet, so the field is the meet's OWN
    #   entrants -- it sends the EDITS (remove/add), not a roster. Requiring
    #   `schools` made every prediction fail with "At least one school is
    #   required" for a field the server could already see.
    schools = [s.strip() for s in (request.args.get("schools") or "").split(",")
               if s.strip()]
    if not schools and not target.get("meet_id"):
        return jsonify({"error": "Pick a meet, or name at least one team."}), 400
    if len(schools) > 20:
        return jsonify({"error": "Twenty teams at most."}), 400

    # The page's edits to the meet's field.
    remove = {s for s in (request.args.get("remove") or "").split(",") if s}
    add = {s for s in (request.args.get("add") or "").split(",") if s}

    h2h = (request.args.get("head_to_head") or "").lower() in ("1", "true", "yes")

    try:
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                out = predictTeam(cur, schools, target, head_to_head=h2h,
                                  remove=remove, add=add)
    except NotImplementedError:
        return jsonify({"available": False,
                        "reason": "The prediction model is not wired up yet."})
    return jsonify(out)


@app.route("/rankings")
def rankings_page():
    return render_template("rankings.html")


# ------------------------------------------------------------------ #
#  ABOUT
# ------------------------------------------------------------------ #

@app.route("/about")
def about_page():
    """Explains what a rating is, and where it is still wrong.

    The figures come from homepage_meta -- the same bag the landing page
    reads -- so the About page cannot drift away from the boards.

    ★ WRAPPED. An explanatory page must not 500 because the database is
      mid-rebuild; about.html falls back to prose for every figure it does
      not get.
    """
    meta = {}
    try:
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                meta = get_homepage_meta(cur)
    except Exception as exc:
        app.logger.warning("about: meta unavailable (%s)", exc)

    return render_template("about.html", meta=meta)


# ------------------------------------------------------------------ #
#  ISSUE REPORTS
# ------------------------------------------------------------------ #
#
# Table (run once, in DBeaver):
#
#   CREATE TABLE IF NOT EXISTS issue_reports (
#       report_id   bigserial PRIMARY KEY,
#       created_at  timestamptz NOT NULL DEFAULT now(),
#       kind        text NOT NULL,
#       page        text,
#       detail      text NOT NULL,
#       email       text,
#       remote_ip   inet,
#       user_agent  text,
#       resolved    boolean NOT NULL DEFAULT false,
#       note        text
#   );
#   CREATE INDEX IF NOT EXISTS idx_issue_reports_open
#       ON issue_reports (created_at DESC) WHERE NOT resolved;

# How many reports each IP has filed recently. In-process and not durable --
# it exists so one bored person cannot fill the table faster than you can read
# it. A real rate limiter belongs in nginx.
_REPORT_HITS = {}
_REPORT_WINDOW = 3600          # seconds
_REPORT_MAX = 10               # per window, per IP


def _reportThrottled(ip):
    """True if this IP has already had its allowance. Prunes as it goes."""
    now = time.time()
    hits = [t for t in _REPORT_HITS.get(ip, []) if now - t < _REPORT_WINDOW]
    _REPORT_HITS[ip] = hits + [now]
    return len(hits) >= _REPORT_MAX


def _reporterIp():
    """The real client IP.

    X-Forwarded-For FIRST: behind nginx, remote_addr is always 127.0.0.1, so
    throttling on it would throttle every visitor as one.
    """
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() or request.remote_addr


@app.route("/report")
def report_page():
    """The report form. `about` prefills which page the report concerns.

    ⚠ ONLY A PATH IS ACCEPTED, never a full URL. This value is echoed back
      into the page, and echoing an arbitrary attacker-supplied absolute URL
      is how a feedback form becomes an open redirect. A leading '//' is
      rejected too -- browsers read '//evil.com' as protocol-relative.
    """
    about = (request.args.get("about") or "").strip()
    if not about.startswith("/") or about.startswith("//"):
        about = ""
    return render_template("report.html", about=about[:200])


@app.route("/api/report", methods=["POST"])
def api_report():
    """Store one issue report.

    Fields are length-capped BEFORE the insert. Not for safety -- the insert
    is parameterised -- but because an unbounded text column is an invitation
    and a 2 MB paste helps nobody.
    """
    data = request.get_json(silent=True) or {}

    detail = (data.get("detail") or "").strip()
    if not detail:
        return jsonify({"error": "Tell us what looks wrong first."}), 400
    if len(detail) > 4000:
        return jsonify({"error": "That is too long -- 4000 characters max."}), 400

    kind = (data.get("kind") or "other").strip()[:40]
    page = (data.get("page") or "").strip()[:300]
    email = (data.get("email") or "").strip()[:200]
    ip = _reporterIp()

    if _reportThrottled(ip):
        return jsonify({"error": "You have sent a few already -- "
                                 "give it an hour."}), 429

    try:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO issue_reports
                        (kind, page, detail, email, remote_ip, user_agent)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING report_id
                """, (kind, page or None, detail, email or None, ip,
                      (request.headers.get("User-Agent") or "")[:400]))
                report_id = cur.fetchone()[0]
            conn.commit()
    except Exception:
        app.logger.exception("report insert failed")
        # ★ SAY IT DID NOT SAVE. A cheerful "thanks!" over a failed insert
        #   means the user believes they have told you and they have not.
        return jsonify({"error": "Could not save that -- nothing was "
                                 "recorded. Try again shortly."}), 500

    return jsonify({"ok": True, "report_id": report_id})

if __name__ == "__main__":
    app.run(debug=True)