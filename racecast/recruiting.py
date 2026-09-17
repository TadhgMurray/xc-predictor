# Project: xc-predictor / racecast
# File:    recruiting.py
# Purpose: Recruiting (issue 282), the part that needs no accounts: the
#          coach's search over every high-school season in the country,
#          and an athlete's recruiting profile.
#
# ★ WHAT A COACH ASKS, IN ORDER: who graduates when, how good, how fast
#   they are getting better, where. So the search is over athlete_season
#   (the boards' own table) joined to the same athlete's season a year
#   earlier, with the graduation year derived from the grade, and every
#   filter is one of those questions. The profile is the same numbers for
#   one athlete, season by season, plus where their rating would land on
#   each college division's board this season.
#
# ! THE GRADUATION YEAR IS DERIVED, NOT STORED. athlete_season keeps the
#   feed's grade spelling (12, Sr, senior); the stored year is the academic
#   year (August to July, named for the year it opens), so a senior in
#   stored year Y graduates in Y + 1 and a grade-g athlete in Y + 1 + (12
#   - g). Grades outside 5..12 are not high school and get no year.
#
# ! ONE COLLEGE SCALE PER POOL. A high-school rating placed on a college
#   board is divided by pool_view.hsFactor(college pool), the same one
#   number the HS-equivalent view multiplies college rows by, so the
#   placement here agrees with the mixed boards.
from season_floor import floorSql, DEFAULT_FLOOR
from us_state import fiftyStateList

POOLS = ("hs_m", "hs_f")
SPORTS = ("XC", "TF")
# ★ FOUR WAYS TO SAY "IMPROVED", BECAUSE THEY DISAGREE (owner, 2026-09-16:
#   "I see that a lot of things like the big gain are mostly bad athletes
#   getting better, is that actually true percentage wise, like is gaining 30
#   speed rating at 80 vs 110 the same amount of improvement percentage
#   wise?").
#
#   No, and not in the direction the board suggests. speed_rating is
#   100 * pool_mean / adjusted_time, so a rating is the RECIPROCAL of a time
#   and a gain of d from r is a fractional time improvement of exactly
#   d / (r + d):
#
#       80 -> 110   +30   25:50 -> 18:47   -27.3%   (hs_m 5k equivalent)
#      110 -> 140   +30   18:47 -> 14:46   -21.4%
#
#   So thirty points at the bottom is a BIGGER time gain than thirty at the
#   top, in percent and in seconds both. Equal rating steps are not equal
#   percentage steps.
#
# ★ AND NEITHER NUMBER IS "IMPROVEMENT", which is the real answer. Moving
#   25:50 -> 18:47 is roughly the 5th to the 55th percentile of high school
#   boys; 18:47 -> 14:46 is the 55th to the 99.7th. Same thirty points,
#   wildly different rarity. The only currency that compares across the range
#   is movement through the FIELD.
#
# ⚠ AND RANKING BY RAW GAIN SELECTS FOR ERROR. Three things put weak athletes
#   at the top of it and only the first is improvement:
#     headroom   an 80 has sixty points under the pool ceiling, a 130 has ten,
#                and untrained athletes really do improve fastest. Real.
#     noise      a rating off one or two races carries large error, and one
#                bad early race (first race ever, sick, wrong distance)
#                inflates the gain.
#     selection  sorting by max delta selects for max ERROR. This is why
#                every most-improved list in every sport is full of people
#                who were mismeasured.
#
#   So the views are offered side by side rather than one being declared
#   correct, and the two that answer the owner's question are the last two.
SORTS = {
    "rating": "j.mean_rating DESC NULLS LAST, j.person_id",
    # the raw points, kept: it is what a coach means by "improved"
    "gain":   "j.gain DESC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
    # the same gain as a share of TIME -- gain / new rating, from the
    # reciprocal above. Favours the bottom of the range even harder.
    "gain_pct": "j.gain_pct DESC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
    # movement through the FIELD: standard deviations of the pool gained
    # between the two seasons. Scale-free, and the honest "how much better
    # are they than they were".
    "gain_z": "j.gain_z DESC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
    # improved MORE THAN PEOPLE AT THEIR LEVEL NORMALLY DO: the gain minus
    # the average gain for that starting rating, over its spread. This is
    # the one that does not just rediscover the bottom of the range, and the
    # same instrument the recruiting projection wants.
    "gain_resid": "j.gain_resid DESC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
    # ★ WHAT THE MODEL THINKS IS LEFT (owner: "the underrated runners --
    #   underrated as in they could progress the most in college type"). Every
    #   gain_* view above reads two seasons that HAPPENED; these read
    #   recruit_projection, which is the network asked what this athlete runs
    #   a year from now. Backward-looking and forward-looking are different
    #   questions and a coach wants both.
    #
    # ! proj IS THE DEFAULT OF THE THREE, and it is the RESIDUAL, because
    #   everybody at 80 is projected to gain more than everybody at 115 --
    #   ranking the raw projection just re-sorts the bottom of the range,
    #   which is the trap gain_resid exists to avoid.
    "proj":      "j.proj_resid DESC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
    "proj_gain": "j.proj_gain DESC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
    "proj_rating": "j.proj_rating DESC NULLS LAST, j.person_id",
    "best":   "j.best_rating DESC NULLS LAST, j.person_id",
    "grad":   "j.grad_year ASC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
}

# a starting-rating band for the expected-gain curve: five points is fine
# enough to separate an 80 from a 110 and coarse enough that every band has
# a population to average over
GAIN_BAND = 5
MIN_BAND_N = 20          # below this the band's expectation is not one
LIMIT, MAX_LIMIT = 100, 200
TIMEOUT_MS = 12000
COLLEGE_DIVISIONS = ("NCAA DI", "NCAA DII", "NCAA DIII", "NAIA")


# the four views' columns, spliced into `j` only when one of them is the sort
_VIEW_COLS = """,
                   -- rating is 100*mean/time, so the time improvement is
                   -- gain / the NEW rating. See SORTS.
                   CASE WHEN p.mean_rating IS NOT NULL AND g.mean_rating > 0
                        THEN ((g.mean_rating - p.mean_rating)
                              / g.mean_rating)::real END AS gain_pct,
                   CASE WHEN p.mean_rating IS NOT NULL
                             AND fn.sd > 0 AND fp.sd > 0
                        THEN (((g.mean_rating - fn.mu) / fn.sd)
                              - ((p.mean_rating - fp.mu) / fp.sd))::real
                        END AS gain_z,
                   cv.exp_gain::real AS exp_gain,
                   CASE WHEN p.mean_rating IS NOT NULL AND cv.sd_gain > 0
                        THEN ((g.mean_rating - p.mean_rating - cv.exp_gain)
                              / cv.sd_gain)::real END AS gain_resid"""

_VIEW_JOINS = """
            LEFT JOIN fld fn ON fn.year = %(year)s
            LEFT JOIN fld fp ON fp.year = %(year)s - 1
            LEFT JOIN curve cv
                   ON cv.band = (round(p.mean_rating / {band}) * {band})::real"""

_VIEW_CTE = """
        -- ★ THE FIELD, BOTH YEARS, so a gain can be expressed as movement
        --   through it rather than as points. Two index-only aggregates on
        --   as_board_mean_idx (pool, sport, year, mean_rating).
        fld AS (
            SELECT s.year, avg(s.mean_rating) AS mu,
                   stddev_samp(s.mean_rating) AS sd
            FROM   athlete_season s
            WHERE  s.pool = %(pool)s AND s.sport = %(sport)s
              AND  s.year IN (%(year)s, %(year)s - 1)
              AND  s.mean_rating IS NOT NULL
            GROUP  BY s.year
        ),
        -- ★ AND WHAT A GAIN NORMALLY IS AT EACH STARTING RATING. The same
        --   year-pair the page already joins, banded by where the athlete
        --   STARTED -- which is the whole point: an 80 and a 110 are not
        --   drawn from one distribution of gains, so one average cannot
        --   judge both.
        --
        -- ! THE WHOLE POOL, NOT THE FILTERED SEARCH. An expectation built
        --   from the rows a user happened to filter to would move with the
        --   filter, and "improved more than expected" would mean something
        --   different on every page.
        curve AS (
            SELECT (round(p2.mean_rating / {band}) * {band})::real AS band,
                   avg(c2.mean_rating - p2.mean_rating) AS exp_gain,
                   stddev_samp(c2.mean_rating - p2.mean_rating) AS sd_gain,
                   count(*) AS n
            FROM   athlete_season p2
            JOIN   athlete_season c2
                   ON c2.person_id = p2.person_id AND c2.pool = p2.pool
                  AND c2.sport = p2.sport AND c2.year = p2.year + 1
            WHERE  p2.pool = %(pool)s AND p2.sport = %(sport)s
              AND  p2.year = %(year)s - 1
              AND  p2.mean_rating IS NOT NULL AND c2.mean_rating IS NOT NULL
            GROUP  BY 1
            HAVING count(*) >= {min_band_n}
        ),
"""


# ★ THE PROJECTION IS A JOIN, NOT A MODEL CALL. recruit_projection is built
#   offline by build_recruit_projection.py -- running the network over a
#   search's worth of athletes inside a 12-second statement timeout is not a
#   page, it is a batch job. So the page reads a column.
#
# ! LEFT JOINED AND PAY-PER-USE, exactly like the gain views. A search that
#   sorts by rating must not grow a join, and an athlete with no projection
#   sorts last rather than disappearing -- the table is built per pool and
#   season and can legitimately be missing a row.
_PROJ_COLS = """,
                   x.proj_rating::real  AS proj_rating,
                   x.proj_gain::real    AS proj_gain,
                   x.proj_gain_pct::real AS proj_gain_pct,
                   x.proj_resid::real   AS proj_resid,
                   x.proj_sigma_pct::real AS proj_sigma_pct,
                   x.horizon_weeks      AS proj_weeks"""

_PROJ_JOIN = """
            LEFT JOIN recruit_projection x
                   ON x.person_id = g.person_id AND x.sport = %(sport)s
                  AND x.pool = %(pool)s AND x.year = %(year)s"""


def wantsProjection(sort):
    """Whether this sort needs recruit_projection joined in."""
    return str(sort or "").startswith("proj")


def gradeNumSql(alias):
    """The grade as a number 5..12, or NULL, from the feed's spelling."""
    g = f"regexp_replace(lower(trim({alias}.grade)), '\\.$', '')"
    return (f"(CASE WHEN {g} ~ '^[0-9]+$' AND {g}::int BETWEEN 5 AND 12 THEN {g}::int "
            f"WHEN {g} IN ('fr', 'freshman') THEN 9 WHEN {g} IN ('so', 'sophomore') THEN 10 "
            f"WHEN {g} IN ('jr', 'junior') THEN 11 WHEN {g} IN ('sr', 'senior') THEN 12 END)")


def storedYear(label_year, sport):
    """The label year the page shows -> the academic year the table stores
    (the track season named 2026 is stored as 2025)."""
    return int(label_year) - 1 if sport == "TF" else int(label_year)


def labelYear(stored, sport):
    return int(stored) + 1 if sport == "TF" else int(stored)


def _ints(args, key, lo, hi):
    out = []
    for v in (args.get(key) or "").split(","):
        v = v.strip()
        if v.isdigit() and lo <= int(v) <= hi:
            out.append(int(v))
    return out


def _float(args, key):
    v = (args.get(key) or "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def parseFilters(args, season_years):
    """request.args -> (filters, error). season_years: {"XC": label,
    "TF": label} from the homepage meta, the default season per sport."""
    sport = (args.get("sport") or "XC").strip().upper()
    if sport not in SPORTS:
        return None, "sport must be XC or TF"
    pool = (args.get("pool") or "hs_m").strip().lower()
    if pool not in POOLS:
        return None, "pool must be hs_m or hs_f"
    year = (args.get("year") or "").strip()
    if year and not (year.isdigit() and 1990 <= int(year) <= 2100):
        return None, "year must be a season"
    label = int(year) if year else season_years.get(sport)
    if not label:
        return None, "no current season is known"
    states = [s.strip().upper() for s in (args.get("state") or "").split(",") if s.strip()]
    if any(len(s) != 2 or not s.isalpha() for s in states):
        return None, "state must be two letters"
    schools = [s.strip() for s in (args.get("school") or "").split(",") if s.strip()][:20]
    sort = (args.get("sort") or "rating").strip().lower()
    if sort not in SORTS:
        return None, f"sort must be one of {sorted(SORTS)}"
    limit = _ints(args, "limit", 1, MAX_LIMIT)
    offset = _ints(args, "offset", 0, 100000)
    min_races = _ints(args, "min_races", 1, 200)
    return {
        "sport": sport, "pool": pool, "label": label, "year": storedYear(label, sport),
        "grad": _ints(args, "grad", 1990, 2100),
        "states": states, "schools": schools,
        "min_rating": _float(args, "min_rating"), "max_rating": _float(args, "max_rating"),
        "min_gain": _float(args, "min_gain"),
        "min_races": min_races[0] if min_races else DEFAULT_FLOOR,
        "min_races_explicit": bool(min_races),
        "sort": sort,
        "limit": limit[0] if limit else LIMIT,
        "offset": offset[0] if offset else 0,
    }, None


def searchRecruits(cur, f):
    """The rows for one search: this season's rating, last season's, the
    gain, the graduation year, the best race and the race count."""
    from rankings import nameLateral
    params = {"pool": f["pool"], "sport": f["sport"], "year": f["year"],
              "min_races": f["min_races"], "limit": f["limit"],
              "offset": f["offset"], "fifty": fiftyStateList()}
    where = []
    if f["states"]:
        params["states"] = f["states"]
        where.append("AND s.state = ANY(%(states)s)")
    if f["schools"]:
        params["schools"] = f["schools"]
        where.append("AND s.school = ANY(%(schools)s)")
    if f["min_rating"] is not None:
        params["min_rating"] = f["min_rating"]
        where.append("AND s.mean_rating >= %(min_rating)s")
    if f["max_rating"] is not None:
        params["max_rating"] = f["max_rating"]
        where.append("AND s.mean_rating <= %(max_rating)s")
    grad_where = ""
    if f["grad"]:
        params["grad"] = f["grad"]
        grad_where = "WHERE c.grade_num IS NOT NULL AND c.year + 13 - c.grade_num = ANY(%(grad)s)"
    gain_where = ""
    if f["min_gain"] is not None:
        params["min_gain"] = f["min_gain"]
        gain_where = "WHERE (g.mean_rating - p.mean_rating) >= %(min_gain)s"
    # a search too wide to answer in time says so rather than holding the
    # page: the savepoint keeps the connection usable after a cancel
    cur.execute("SAVEPOINT recruit_search")
    cur.execute(f"SET LOCAL statement_timeout = {int(TIMEOUT_MS)}")
    # ★ THE EXTRA VIEWS ARE PAY-PER-USE. fld is two aggregates and curve is
    #   a self-join over a pool-year; running them for a search that sorts by
    #   rating would spend that on every page for nothing, under a 12 s
    #   statement timeout. Sorting by a gain_* view is what buys them, and
    #   the default page's SQL is then byte-for-byte what it was.
    band, min_band_n = GAIN_BAND, MIN_BAND_N
    wants_views = str(f.get("sort") or "").startswith("gain_")
    if wants_views:
        view_cte = _VIEW_CTE.format(band=band, min_band_n=min_band_n)
        view_cols = _VIEW_COLS
        view_joins = _VIEW_JOINS.format(band=band)
    else:
        view_cte = view_cols = view_joins = ""
    # ! AND THE PROJECTION, ON THE SAME TERMS. A missing table is not an
    #   error: the build needs a trained model and may not have run, and the
    #   page falls back to the rating sort rather than 500-ing.
    # ⚠ AND IT USED TO FALL BACK IN SILENCE (owner, 2026-09-17: "is the
    #   predicted gain thing actually wired bcs I don't see it anywhere,
    #   like I press the model predicted section but it doesn't show
    #   anythign"). It is wired end to end -- the join, the JSON, the
    #   column in recruiting-search.js -- but when recruit_projection has
    #   not been built the sort quietly became "rating" and the projection
    #   column, which only appears when some row HAS a projection, quietly
    #   did not. Two silences look exactly like a dead button.
    projection_missing = False
    if wantsProjection(f.get("sort")):
        if _tableExists(cur, "recruit_projection"):
            view_cols += _PROJ_COLS
            view_joins += _PROJ_JOIN
        else:
            projection_missing = True
            f = dict(f, sort="rating")
    cur.execute(f"""
        WITH cur AS (
            SELECT s.person_id, s.school, s.state, s.grade, s.year,
                   s.mean_rating, s.best_rating, s.n_races,
                   {gradeNumSql('s')} AS grade_num
            FROM   athlete_season s
            WHERE  s.pool = %(pool)s AND s.sport = %(sport)s AND s.year = %(year)s
              AND  s.state IS NOT NULL AND s.mean_rating IS NOT NULL
              -- ★ THE FIFTY STATES (owner, 2026-09-17). The boards keep a
              --   row they cannot prove foreign, because dropping a real
              --   one costs a ranking; a recruiting list is a list of
              --   people a US college may sign, so it asks the narrower
              --   question. See racecast/us_state.py, which is its own set
              --   precisely so changing it cannot move the boards.
              AND  upper(s.state) = ANY(%(fifty)s)
              AND  {floorSql(f['min_races_explicit'])}
              {' '.join(where)}
        ),
        g AS (
            SELECT c.*, c.year + 13 - c.grade_num AS grad_year
            FROM   cur c
            {grad_where}
        ),
{view_cte}        j AS (
            SELECT g.*, p.mean_rating AS prev_rating,
                   (g.mean_rating - p.mean_rating)::real AS gain
                   {view_cols}
            FROM   g
            LEFT JOIN athlete_season p
                   ON p.person_id = g.person_id AND p.pool = %(pool)s
                  AND p.sport = %(sport)s AND p.year = %(year)s - 1
            {view_joins}
            {gain_where}
        )
        SELECT j.*, a.name
        FROM   j
        {nameLateral('j')}
        ORDER  BY {SORTS[f['sort']]}
        LIMIT  %(limit)s OFFSET %(offset)s
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute("RELEASE SAVEPOINT recruit_search")
    if projection_missing and rows:
        # carried on the rows so searchOrTimeout's signature does not change;
        # the route lifts it off the first one and drops it.
        rows[0]["_projection_missing"] = True
    return rows


def searchOrTimeout(cur, f):
    """(rows, None) or (None, message) when the search timed out."""
    try:
        return searchRecruits(cur, f), None
    except Exception as exc:                          # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT recruit_search")
        if "canceling statement" in str(exc) or "timeout" in str(exc).lower():
            return None, "That search is too wide to answer in time. Add a state, a graduation year or a rating floor."
        raise


def collegePlacements(cur, sport, hs_pool, rating, label_year):
    """Where a high-school rating would rank on each college division's
    board for the season: [{division, rank, total, board_url}]. The board
    is the ability board with the division filter, the same one the
    rankings page shows, so a coach can click through and see the list."""
    from urllib.parse import urlencode
    from werkzeug.datastructures import MultiDict
    from rankings import parseFilters as boardFilters, countOf
    from pool_view import hsFactor
    if rating is None:
        return []
    college = "college_m" if hs_pool.endswith("_m") else "college_f"
    factor = hsFactor(college, sport, None)
    if not factor:
        return []
    equiv = float(rating) / float(factor)
    # the latest college season on the board at or before this one
    cur.execute("""SELECT max(year) AS y FROM athlete_season
                   WHERE pool = %s AND sport = %s AND year <= %s""",
                (college, sport, storedYear(label_year, sport)))
    row = cur.fetchone()
    stored = (row["y"] if isinstance(row, dict) else row[0]) if row else None
    if stored is None:
        return []
    from rankings import _whereClauses
    out = []
    for div in COLLEGE_DIVISIONS:
        args = {"board": "ability", "pool": college, "sport": sport,
                # the boards take the stored academic year (rankings.
                # boardYear); labelYear is for what a reader is SHOWN
                "year": str(stored), "division": div}
        f, err = boardFilters(MultiDict(args))
        if err:
            continue
        params = {"min_races": f["min_races"], "equiv": equiv}
        where = _whereClauses(f, params, with_dates=False)
        try:
            # a plain cursor: the board machinery reads rows by position
            with cur.connection.cursor() as plain:
                plain.execute("SAVEPOINT recruit_place")
                plain.execute(f"""SELECT count(*) FROM athlete_season s
                                  WHERE {floorSql(f['min_races_explicit'])} {where}
                                    AND s.mean_rating > %(equiv)s""", params)
                above = plain.fetchone()[0]
                total = countOf(plain, f)
                plain.execute("RELEASE SAVEPOINT recruit_place")
        except Exception as exc:                    # noqa: BLE001
            print(f"recruit: placement {div} failed ({type(exc).__name__}: {exc})", flush=True)
            try:
                cur.execute("ROLLBACK TO SAVEPOINT recruit_place")
            except Exception:                       # noqa: BLE001
                cur.connection.rollback()
            continue
        if not total:
            continue
        out.append({"division": div, "rank": int(above) + 1, "total": int(total),
                    "equiv": equiv, "year": labelYear(stored, sport),
                    "board_url": "/rankings?" + urlencode(args)})
    return out


def recruitProfile(cur, person_id):
    """One athlete's recruiting numbers: every high-school season with its
    gain, the graduation year, and the college placements of the latest
    season in each sport. None when the athlete has no HS season."""
    from rankings import nameLateral
    from school_identity import schoolLabelIn
    cur.execute(f"""
        SELECT s.pool, s.sport, s.year, s.grade, s.school, s.state,
               s.mean_rating, s.best_rating, s.n_races, s.last_race,
               {gradeNumSql('s')} AS grade_num, a.name
        FROM   athlete_season s
        {nameLateral('s')}
        WHERE  s.person_id = %s AND s.pool IN ('hs_m', 'hs_f')
        ORDER  BY s.year, s.sport
    """, (person_id,))
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return None
    seasons = []
    prev = {}
    for r in rows:
        p = prev.get(r["sport"])
        gain = (float(r["mean_rating"]) - float(p["mean_rating"])) if p and r["mean_rating"] is not None \
            and p["mean_rating"] is not None and p["year"] == r["year"] - 1 else None
        seasons.append({**r, "label": labelYear(r["year"], r["sport"]), "gain": gain,
                        "grad_year": (r["year"] + 13 - r["grade_num"]) if r["grade_num"] else None})
        prev[r["sport"]] = r
    latest = max(seasons, key=lambda s: (s["year"], s.get("last_race") or ""))
    grad = next((s["grad_year"] for s in reversed(seasons) if s["grad_year"]), None)
    placements = []
    for sport in SPORTS:
        last = next((s for s in reversed(seasons) if s["sport"] == sport and s["mean_rating"] is not None), None)
        if last:
            placements.append({"sport": sport, "label": last["label"], "rating": float(last["mean_rating"]),
                               "rows": collegePlacements(cur, sport, last["pool"], last["mean_rating"], last["label"])})
    return {"person_id": person_id, "name": latest.get("name") or "Unknown",
            "school": latest.get("school"), "state": latest.get("state"),
            "school_label": schoolLabelIn(latest.get("school"), latest.get("state")) if latest.get("school") else "",
            "pool": latest["pool"], "grad_year": grad, "seasons": seasons, "placements": placements}


# ===================================================================== #
#  THE ATHLETE EDITION (282, owner: "the athlete edition is the default")
# ===================================================================== #
#
# ★ YOU, GETTING RECRUITED. college_recruit (build_recruiting.py, step
#   10f) holds one row per recruit and sport with the rating they were
#   recruited at, on the HIGH-SCHOOL scale. Everything here reads that
#   table: the school table (each college's recruits summarised), the one
#   college page, and the placement of a reader's own rating against each
#   school's recruits, in words a recruit understands.
#
# ★ THE TIERS ARE QUARTILES OF THE SCHOOL'S OWN RECRUITS, not a national
#   bar: "scholarship range" at Colorado and at a DIII school are
#   different numbers because their recruits are. Top quarter: where
#   athletic aid tends to go where it exists (DI, DII, NAIA), "top
#   recruit" where it does not (DIII) or the division is unknown. Above
#   the median: a solid recruit. Middle half: recruit range. Between the
#   slowest recruit and the first quartile: walk-on range. Within
#   REACH_PTS under the slowest: just under. Below that: not yet.
#
# ! THE SUBJECT IS A SEAM FOR ACCOUNTS (283). subjectFrom reads ?athlete=
#   or a typed time today; when accounts exist, a signed-in athlete's
#   claimed person_id fills the athlete slot when the query names none.
#   Nothing else needs to change.

import math
import threading
import time as _time

GENDERS = ("m", "f")
GENDER_WORDS = {"m": "men", "f": "women"}
MIN_RECRUITS = 3               # a school needs this many to be summarised
REACH_PTS = 3.0                # "just under" band below the slowest recruit
CACHE_TTL = 600.0
SCHOLARSHIP_DIVISIONS = ("NCAA DI", "NCAA DII", "NAIA", "NJCAA")

# event key -> (metres, sport, label). The keys are what the page's select
# and the query string carry.
EVENTS = {
    "5k":   (5000.0,   "XC", "5K cross country"),
    "3mi":  (4828.0,   "XC", "3 mile cross country"),
    "800":  (800.0,    "TF", "800m"),
    "1500": (1500.0,   "TF", "1500m"),
    "1600": (1600.0,   "TF", "1600m"),
    "mile": (1609.34,  "TF", "Mile"),
    "3000": (3000.0,   "TF", "3000m"),
    "3200": (3200.0,   "TF", "3200m"),
    "2mi":  (3218.69,  "TF", "2 mile"),
    "5000": (5000.0,   "TF", "5000m on the track"),
}
# the events every threshold is quoted in
THRESHOLD_EVENTS = (("5k", 5000.0, "XC"), ("1600", 1600.0, "TF"), ("3200", 3200.0, "TF"))

TIERS = {
    # key: (order, label, what it means)
    "top":     (5, "Top recruit",     "faster than three quarters of their recent recruits"),
    "solid":   (4, "Solid recruit",   "faster than half of their recent recruits"),
    "recruit": (3, "Recruit range",   "inside the middle half of their recent recruits"),
    "walkon":  (2, "Walk-on range",   "slower than most of their recruits, faster than their slowest"),
    "reach":   (1, "Just under",      f"within {REACH_PTS:g} rating points of their slowest recruit"),
    "below":   (0, "Not yet",         "under their slowest recent recruit"),
}
SCHOLARSHIP_LABEL = "Scholarship range"
TIER_ORDER = ("top", "solid", "recruit", "walkon", "reach", "below")


def parseTime(text):
    """'16:32', '16:32.4', '4:21.5', '1:02:03' or '992' -> seconds, or None."""
    s = str(text or "").strip().replace(",", "")
    if not s:
        return None
    parts = s.split(":")
    if len(parts) > 3 or any(p == "" for p in parts):
        return None
    try:
        secs = 0.0
        for p in parts:
            secs = secs * 60.0 + float(p)
    except ValueError:
        return None
    if not (30.0 <= secs <= 7200.0):
        return None
    return secs


def fmtTime(seconds, tenths=None):
    """A time for the page: '16:32' over fifteen minutes, '4:21.5' under
    (a track split is quoted to the tenth, a 5K is not). None -> ''."""
    if seconds is None or seconds <= 0:
        return ""
    if tenths is None:
        tenths = seconds < 900.0
    if tenths:
        s = math.floor(float(seconds) * 10.0 + 0.5) / 10.0
        m, r = int(s // 60), s - 60 * int(s // 60)
        return f"{m}:{r:04.1f}"
    s = int(round(float(seconds)))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def scholarshipDivision(division):
    return (division or "").strip().upper() in SCHOLARSHIP_DIVISIONS


def tierFor(rating, dist, division=None):
    """Where a rating sits among one school's recruits.

    dist: {min, p25, median, p75, max} on the HS scale. Returns
    {key, order, label, blurb, gap, next_label}: gap is the rating points
    to the next tier up (None at the top), next_label names it."""
    if rating is None or dist is None or dist.get("min") is None:
        return None
    r = float(rating)
    lo, p25, med, p75 = (float(dist["min"]), float(dist["p25"]),
                         float(dist["median"]), float(dist["p75"]))
    top_label = SCHOLARSHIP_LABEL if scholarshipDivision(division) else TIERS["top"][1]
    if r >= p75:
        key, gap, nxt = "top", None, None
    elif r >= med:
        key, gap, nxt = "solid", p75 - r, top_label
    elif r >= p25:
        key, gap, nxt = "recruit", med - r, TIERS["solid"][1]
    elif r >= lo:
        key, gap, nxt = "walkon", p25 - r, TIERS["recruit"][1]
    elif r >= lo - REACH_PTS:
        key, gap, nxt = "reach", lo - r, TIERS["walkon"][1]
    else:
        key, gap, nxt = "below", lo - r, TIERS["walkon"][1]
    order, label, blurb = TIERS[key]
    if key == "top":
        label = top_label
        if top_label == SCHOLARSHIP_LABEL:
            blurb += "; where athletic aid tends to go in this division"
    return {"key": key, "order": order, "label": label, "blurb": blurb,
            "gap": round(gap, 1) if gap is not None else None, "next_label": nxt}


# ---- the rating <-> time bridge, through conversions ------------------ #
# ★ THE SAME MACHINERY THE CONVERSIONS PAGE USES, never a pace table of
#   our own: a threshold quoted here as a 5K must be the 5K the site would
#   convert that rating to anywhere else. Every call is wrapped: the
#   constants come from the database, and a page with no times is better
#   than a page that 500s.
_TIMES = {"at": 0.0, "map": {}}
_times_lock = threading.Lock()


def _hsPool(gender):
    return "hs_f" if (gender or "m") == "f" else "hs_m"


def timesFor(rating, gender):
    """{'5k': sec, '1600': sec, '3200': sec} for a HS-scale rating, each
    at the sport's typical venue; a value the conversion cannot give is
    None. Memoised at half a point, ten minutes."""
    if rating is None:
        return {k: None for k, _, _ in THRESHOLD_EVENTS}
    key = (round(float(rating) * 2.0) / 2.0, _hsPool(gender))
    with _times_lock:
        if _time.time() - _TIMES["at"] > CACHE_TTL:
            _TIMES["map"].clear()
            _TIMES["at"] = _time.time()
        hit = _TIMES["map"].get(key)
    if hit is not None:
        return hit
    out = {}
    try:
        from conversions import _norm_from_rating, normalized_to_time
        for ev, dist, sport in THRESHOLD_EVENTS:
            norm = _norm_from_rating(key[0], key[1], sport=sport)
            t = normalized_to_time(norm, {"distance": dist, "pool": key[1], "sport": sport}) \
                if norm else None
            out[ev] = round(float(t), 1) if t else None
    except Exception as exc:                            # noqa: BLE001
        print(f"recruiting: timesFor({rating}, {gender}) failed ({type(exc).__name__}: {exc})",
              flush=True)
        out = {k: None for k, _, _ in THRESHOLD_EVENTS}
    with _times_lock:
        _TIMES["map"][key] = out
    return out


def ratingFromTime(seconds, event, gender):
    """A typed time -> (rating on the HS scale, sport) or (None, sport).
    The event names the distance and the sport; the sport's typical venue
    is assumed, as the conversions page does with no course chosen."""
    if event not in EVENTS or seconds is None:
        return None, None
    dist, sport, _ = EVENTS[event]
    pool = _hsPool(gender)
    try:
        from conversions import _norm_from_time, normalized_to_rating
        norm = _norm_from_time(float(seconds), dist, pool, sport=sport)
        r = normalized_to_rating(norm, pool, sport=sport) if norm else None
    except Exception as exc:                            # noqa: BLE001
        print(f"recruiting: ratingFromTime({seconds}, {event}, {gender}) failed "
              f"({type(exc).__name__}: {exc})", flush=True)
        r = None
    return (round(float(r), 1) if r else None), sport


# ---- the schools ------------------------------------------------------ #
# ★ RECENCY HALF-LIFE, IN RECRUITING CLASSES (owner, 2026-09-17: "it
#   probably is an overestimate, needs to weight recent years much more
#   heavily"). A class this many years older than the newest one counts
#   half as much; six classes back counts about a fourteenth. A program
#   that was good four years ago and is not now used to read as though it
#   still were, because every class in the window counted the same.
#
#   1.5 -> weights 1.00, 0.63, 0.40, 0.25, 0.16, 0.10 across the six
#   classes build_recruiting keeps. One number, here, to tune.
RECENCY_HALF_LIFE = 1.5

# ! WEIGHTED PERCENTILES, WHICH POSTGRES HAS NO AGGREGATE FOR.
#   percentile_cont cannot take a weight, so the quantile is found by
#   walking the ratings in order and taking the first one at which the
#   running weight crosses the share. `min(...) FILTER (WHERE cum >= q *
#   tot)` is exactly that, because cum only increases with the ordering.
#   The result is the weighted LOWER quantile -- an actual recruit's
#   rating rather than an interpolation between two, which is also the
#   honest thing to print beside a name.
_AGG_SQL = """
    WITH src AS (
        SELECT school, state, division, conference, recruit_rating, source,
               first_year
        FROM   college_recruit
        WHERE  gender = %(gender)s AND sport = %(sport)s {extra}
    ),
    -- ⚠ THE NEWEST CLASS IN THE WHOLE POOL, NOT IN `src`. {extra} scopes
    --   src to one school for the single-college page; anchoring the decay
    --   to that school's own newest class would give a program that stopped
    --   recruiting three years ago full weight on its stale top class, and
    --   the college page would then disagree with the table it came from.
    --   One anchor, both views.
    newest AS (
        SELECT max(first_year) AS y FROM college_recruit
        WHERE  gender = %(gender)s AND sport = %(sport)s
    ),
    w AS (
        SELECT src.*,
               power(0.5, (newest.y - src.first_year)::float
                          / %(half_life)s)::float AS wt
        FROM   src CROSS JOIN newest
    ),
    c AS (
        SELECT w.*,
               sum(wt) OVER (PARTITION BY school, state
                             ORDER BY recruit_rating
                             ROWS BETWEEN UNBOUNDED PRECEDING
                                      AND CURRENT ROW) AS cum,
               sum(wt) OVER (PARTITION BY school, state) AS tot
        FROM   w
    )
    SELECT school, state, min(division) AS division, min(conference) AS conference,
           count(*)::int AS n,
           sum(CASE WHEN source = 'hs' THEN 1 ELSE 0 END)::int AS n_hs,
           min(recruit_rating)::real AS min,
           min(recruit_rating) FILTER (WHERE cum >= 0.25 * tot)::real AS p25,
           min(recruit_rating) FILTER (WHERE cum >= 0.50 * tot)::real AS median,
           min(recruit_rating) FILTER (WHERE cum >= 0.75 * tot)::real AS p75,
           max(recruit_rating)::real AS max,
           min(first_year) AS first_year, max(first_year) AS last_year,
           -- how much of the verdict is the newest two classes, so the page
           -- can say when a school's number is really an old school's
           (sum(wt) FILTER (WHERE first_year >= (SELECT y FROM newest) - 1)
            / nullif(sum(wt), 0))::real AS recent_share
    FROM   c
    GROUP  BY school, state
    HAVING count(*) >= %(min_n)s
"""
_SCHOOLS = {}                    # (gender, sport) -> (at, rows)
_schools_lock = threading.Lock()


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    row = cur.fetchone()
    v = row[0] if not isinstance(row, dict) else list(row.values())[0]
    return v is not None


def _rowsOf(cur):
    return [dict(r) if isinstance(r, dict) else dict(zip([d[0] for d in cur.description], r))
            for r in cur.fetchall()]


def recruitMeta(cur):
    """{(gender, sport): {gain, n_pairs, factor, since_year, built_at}} or {}."""
    if not _tableExists(cur, "college_recruit_meta"):
        return {}
    cur.execute("SELECT gender, sport, gain, n_pairs, factor, since_year, built_at "
                "FROM college_recruit_meta")
    return {(r["gender"], r["sport"]): r for r in _rowsOf(cur)}


def schoolTable(cur, gender, sport, min_n=MIN_RECRUITS):
    """Every college's recruits summarised, for one gender and sport, with
    the label and the threshold times stamped. Cached ten minutes: the
    table changes once a run. [] when the table is not built."""
    from school_identity import schoolLabelIn
    key = (gender, sport, int(min_n))
    with _schools_lock:
        hit = _SCHOOLS.get(key)
        if hit and _time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    if not _tableExists(cur, "college_recruit"):
        return []
    cur.execute(_AGG_SQL.format(extra=""),
                {"gender": gender, "sport": sport, "min_n": int(min_n),
                 "half_life": float(RECENCY_HALF_LIFE)})
    rows = _rowsOf(cur)
    for r in rows:
        for k in ("min", "p25", "median", "p75", "max"):
            r[k] = round(float(r[k]), 1) if r[k] is not None else None
        r["label"] = schoolLabelIn(r["school"], r.get("state")) if r.get("school") else ""
        r["times"] = {k: timesFor(r[k], gender) for k in ("p25", "median", "p75")}
        r["classes"] = (f"{labelYear(r['first_year'], sport)}" if r["first_year"] == r["last_year"]
                        else f"{labelYear(r['first_year'], sport)} to {labelYear(r['last_year'], sport)}")
    rows.sort(key=lambda r: (-(r["median"] or 0), r["school"]))
    with _schools_lock:
        _SCHOOLS[key] = (_time.time(), rows)
    return rows


SCHOOL_SORTS = {
    "median": lambda r: (-(r["median"] or 0), r["school"]),
    "top":    lambda r: (-(r["p75"] or 0), r["school"]),
    "floor":  lambda r: (-(r["min"] or 0), r["school"]),
    "n":      lambda r: (-r["n"], r["school"]),
    "name":   lambda r: (r["school"], r.get("state") or ""),
}


def _csv(args, key, upper=False):
    out = []
    for v in (args.get(key) or "").split(","):
        v = v.strip()
        if v:
            out.append(v.upper() if upper else v)
    return out


def parseSchoolFilters(args, season_years=None):
    """request.args -> (filters, error) for the school table."""
    gender = (args.get("gender") or "m").strip().lower()
    if gender not in GENDERS:
        return None, "gender must be m or f"
    sport = (args.get("sport") or "XC").strip().upper()
    if sport not in SPORTS:
        return None, "sport must be XC or TF"
    sort = (args.get("sort") or "median").strip().lower()
    if sort not in SCHOOL_SORTS:
        return None, f"sort must be one of {sorted(SCHOOL_SORTS)}"
    states = _csv(args, "state", upper=True)
    if any(len(s) != 2 or not s.isalpha() for s in states):
        return None, "state must be two letters"
    min_n = _ints(args, "min_n", 1, 500)
    return {
        "gender": gender, "sport": sport, "sort": sort,
        "divisions": [d.upper() for d in _csv(args, "division")],
        "conferences": [c.upper() for c in _csv(args, "conference")],
        "states": states,
        "min_n": min_n[0] if min_n else MIN_RECRUITS,
        "q": (args.get("q") or "").strip().lower(),
    }, None


def filterSchools(rows, f):
    out = rows
    if f["divisions"]:
        want = set(f["divisions"])
        out = [r for r in out if (r.get("division") or "").upper() in want]
    if f["conferences"]:
        want = set(f["conferences"])
        out = [r for r in out if (r.get("conference") or "").upper() in want]
    if f["states"]:
        want = set(f["states"])
        out = [r for r in out if (r.get("state") or "") in want]
    if f["q"]:
        out = [r for r in out if f["q"] in r["school"].lower()]
    return sorted(out, key=SCHOOL_SORTS[f["sort"]])


def distinctUnits(rows, key):
    return sorted({r[key] for r in rows if r.get(key)})


# ---- one college ------------------------------------------------------ #

def schoolRecruits(cur, school, state, gender, sport):
    """(summary, recruits) for one college, gender and sport: the same
    aggregate the table shows and the recruits behind it, newest class
    first, fastest first inside a class. (None, []) when it has none."""
    from rankings import nameLateral
    from school_identity import schoolLabelIn
    if not _tableExists(cur, "college_recruit"):
        return None, []
    params = {"gender": gender, "sport": sport, "min_n": 1, "school": school,
              "half_life": float(RECENCY_HALF_LIFE)}
    extra = "AND school = %(school)s"
    if state:
        params["state"] = state
        extra += " AND state = %(state)s"
    cur.execute(_AGG_SQL.format(extra=extra), params)
    agg = _rowsOf(cur)
    if not agg:
        return None, []
    summary = agg[0]
    for k in ("min", "p25", "median", "p75", "max"):
        summary[k] = round(float(summary[k]), 1) if summary[k] is not None else None
    summary["label"] = schoolLabelIn(summary["school"], summary.get("state"))
    summary["times"] = {k: timesFor(summary[k], gender) for k in ("min", "p25", "median", "p75", "max")}
    summary["classes"] = (f"{labelYear(summary['first_year'], sport)}"
                          if summary["first_year"] == summary["last_year"] else
                          f"{labelYear(summary['first_year'], sport)} to {labelYear(summary['last_year'], sport)}")
    cur.execute(f"""
        SELECT r.person_id, r.first_year, r.grade, r.first_rating, r.hs_equiv,
               r.hs_rating, r.hs_year, r.hs_school, r.hs_state, r.recruit_rating,
               r.source, r.n_races, a.name
        FROM   college_recruit r
        {nameLateral('r')}
        WHERE  r.gender = %(gender)s AND r.sport = %(sport)s {extra}
        ORDER  BY r.first_year DESC, r.recruit_rating DESC, r.person_id
    """, params)
    recruits = _rowsOf(cur)
    for r in recruits:
        r["class_label"] = labelYear(r["first_year"], sport)
        r["hs_label"] = (schoolLabelIn(r["hs_school"], r.get("hs_state"))
                         if r.get("hs_school") else "")
        for k in ("first_rating", "hs_equiv", "hs_rating", "recruit_rating"):
            r[k] = round(float(r[k]), 1) if r.get(k) is not None else None
    return summary, recruits


# ---- the subject: you ------------------------------------------------- #

def _athleteSubject(cur, person_id):
    """The latest high-school season per sport for one athlete, as a
    subject. None when the athlete has no HS season."""
    from rankings import nameLateral
    from school_identity import schoolLabelIn
    cur.execute(f"""
        SELECT s.pool, s.sport, s.year, s.grade, s.school, s.state, s.mean_rating,
               s.n_races, s.last_race, {gradeNumSql('s')} AS grade_num, a.name
        FROM   athlete_season s
        {nameLateral('s')}
        WHERE  s.person_id = %s AND s.pool IN ('hs_m', 'hs_f') AND s.mean_rating IS NOT NULL
        ORDER  BY s.year DESC, s.last_race DESC NULLS LAST
    """, (person_id,))
    rows = _rowsOf(cur)
    if not rows:
        return None
    latest = rows[0]
    ratings, seasons = {}, {}
    for r in rows:
        if r["sport"] not in ratings:
            ratings[r["sport"]] = round(float(r["mean_rating"]), 1)
            seasons[r["sport"]] = labelYear(r["year"], r["sport"])
    grad = next((r["year"] + 13 - r["grade_num"] for r in rows if r.get("grade_num")), None)
    return {
        "kind": "athlete", "person_id": int(person_id), "name": latest.get("name") or "Unknown",
        "prs": personalBests(cur, int(person_id)),
        "school": latest.get("school"), "state": latest.get("state"),
        "school_label": schoolLabelIn(latest["school"], latest.get("state")) if latest.get("school") else "",
        "gender": latest["pool"].rsplit("_", 1)[-1], "grad_year": grad,
        "ratings": ratings, "seasons": seasons,
    }


def subjectFrom(cur, args, account_person_id=None):
    """(subject, error). The reader's own number, from ?athlete=<id>, from
    a typed ?time=&event=(&gender=), or -- the accounts seam (283) -- from
    the signed-in athlete's claimed page when the query names nobody.
    (None, None) when there is no subject."""
    pid = (args.get("athlete") or "").strip()
    if not pid and account_person_id:
        pid = str(account_person_id)
    if pid:
        if not pid.isdigit():
            return None, "athlete must be an id"
        sub = _athleteSubject(cur, int(pid))
        if sub is None:
            return None, "That athlete has no high-school season to place."
        return sub, None
    text = (args.get("time") or "").strip()
    event = (args.get("event") or "").strip().lower()
    if not text and not event:
        return None, None
    if event not in EVENTS:
        return None, f"event must be one of {', '.join(EVENTS)}"
    seconds = parseTime(text)
    if seconds is None:
        return None, "time must look like 16:32 or 4:21.5"
    gender = (args.get("gender") or "m").strip().lower()
    if gender not in GENDERS:
        return None, "gender must be m or f"
    rating, sport = ratingFromTime(seconds, event, gender)
    if rating is None:
        return None, "That time could not be converted to a rating right now."
    return {"kind": "time", "gender": gender, "event": event, "event_label": EVENTS[event][2],
            "seconds": seconds, "time": fmtTime(seconds), "sport": sport,
            # ★ ONE SCALE PER POOL: a rating from a track time places on the
            #   cross-country recruits too (the joint solve's sport level)
            "ratings": {"XC": rating, "TF": rating}}, None


def subjectRating(subject, sport):
    """The subject's rating for a sport's recruits: the sport's own season
    when there is one, else the other sport's (one scale per pool)."""
    if not subject:
        return None
    r = subject["ratings"].get(sport)
    if r is None:
        other = "TF" if sport == "XC" else "XC"
        r = subject["ratings"].get(other)
    return r


def placeRows(rows, rating):
    """Stamp each school row with the subject's tier."""
    for r in rows:
        r["tier"] = tierFor(rating, r, r.get("division")) if rating is not None else None
    return rows


def suggestions(rows, rating, per_tier=30):
    """The schools grouped by where the rating lands, the fastest
    programmes first inside each tier: [{key, label, blurb, schools}].
    Tiers with nothing in them are left out; 'below' is never listed."""
    if rating is None:
        return []
    groups = {k: [] for k in TIER_ORDER}
    for r in rows:
        t = tierFor(rating, r, r.get("division"))
        if t and t["key"] != "below":
            groups[t["key"]].append(dict(r, tier=t))
    out = []
    for key in TIER_ORDER:
        if key == "below" or not groups[key]:
            continue
        schools = sorted(groups[key], key=lambda r: (-(r["median"] or 0), r["school"]))
        label = TIERS[key][1]
        if key == "top" and any(scholarshipDivision(r.get("division")) for r in schools):
            label = f"{SCHOLARSHIP_LABEL} / top recruit"
        out.append({"key": key, "label": label, "blurb": TIERS[key][2],
                    "total": len(schools), "schools": schools[:per_tier]})
    return out


# ---- the subject's own PRs ------------------------------------------ #
# ★ REAL TIMES, NOT CONVERSIONS (owner, 2026-09-15: "it grabs prs that
#   aren't actually their prs"). The card used to show the rating converted
#   to a 5K, a 1600 and a 3200 and called them the athlete's times. Those
#   are equivalents. The PRs are the fastest result at each distance the
#   athlete actually ran, from ranking_results (the index on person_id
#   carries sport, distance and time, so this is one index-only read).
PR_EVENTS = (
    # key, label, sport, metres, tolerance in metres
    ("5k",   "5K XC",   "XC", 5000.0, 60.0),
    ("3mi",  "3 mile",  "XC", 4828.0, 30.0),
    ("800",  "800",     "TF", 800.0, 4.0),
    ("1500", "1500",    "TF", 1500.0, 4.0),
    ("1600", "1600",    "TF", 1600.0, 4.0),
    ("mile", "Mile",    "TF", 1609.3, 4.0),
    ("3000", "3000",    "TF", 3000.0, 6.0),
    ("3200", "3200",    "TF", 3200.0, 6.0),
    ("2mi",  "2 mile",  "TF", 3218.7, 6.0),
    ("5000", "5000 track", "TF", 5000.0, 10.0),
)


def personalBests(cur, person_id):
    """[{key, label, sport, seconds, time, date}] fastest first by event
    order, one per event the athlete has run; [] when nothing is on file."""
    try:
        cur.execute("""SELECT sport, distance, time_seconds, race_date
                       FROM   ranking_results
                       WHERE  person_id = %s AND time_seconds > 0 AND distance > 0""",
                    (person_id,))
        rows = _rowsOf(cur)
    except Exception as exc:                            # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:                               # noqa: BLE001
            pass
        print(f"recruiting: personalBests({person_id}) failed ({type(exc).__name__}: {exc})", flush=True)
        return []
    best = {}
    for r in rows:
        d, t = float(r["distance"]), float(r["time_seconds"])
        for key, label, sport, metres, tol in PR_EVENTS:
            if r["sport"] == sport and abs(d - metres) <= tol:
                if key not in best or t < best[key]["seconds"]:
                    best[key] = {"key": key, "label": label, "sport": sport, "seconds": t,
                                 "time": fmtTime(t), "date": str(r.get("race_date") or "")[:10]}
                break
    return [best[k] for k, *_ in PR_EVENTS if k in best]
