import sys
import re
import time          # _reportThrottled

sys.path.insert(0, "scripts")
# ★ db_timing BEFORE every other racecast import: it patches
#   database.getConn so every module's queries are timed. /debug/queries
#   reads the buffer; slow_queries.log keeps the >250ms offenders.
import db_timing
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
from pool_view import (fetchPoolRows, stampHsRatings, seasonFactor,
                       stampRowsHs, stampBoardRows)
from teams import (parseFilters as parseTeamFilters, serveBoard,
                   getCoursePerformances as getTeamCoursePerformances)
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


def _dist_override_join(r="r"):
    """LEFT JOIN fragment exposing `dov` -- the hand-verified distance.

    ⚠ WITHOUT THIS THE SITE SHOWED A DISTANCE THE CORPUS HAD ALREADY
      REJECTED. dist_override carries 5,248 corrected divisions and app.py
      referenced it NOWHERE, so every page rendered COALESCE(meets.distance,
      the tfrrs blob) -- the scraped value the override exists to replace.

      Meet 26359 div 0, the JV Minutemen Classic at Ox Bow Park, is the
      example: scraped 8046 m, overridden to 5000, rated at 5000 (correctly),
      and DISPLAYED as 8046. The rating was right and the number beside it was
      wrong, which is why every rating-based audit called the division clean
      while it was plainly wrong on the page.

    ⚠ AND IT IS NOT COSMETIC. The same expression keys the join into
      course_difficulties: cd.distance_m is matched against the race distance
      snapped to 100m, so a wrong distance looks up a DIFFERENT CELL -- or no
      cell at all -- and the difficulty shown belongs to a course the athlete
      did not run. The engine keyed that cell at the OVERRIDE distance.

    ! (meet_id, div_id) IS THE WHOLE KEY -- dist_override has no source
      column, see engine/dump_overrides.py, and its primary key is exactly
      these two. So this cannot fan out.
    """
    return f"""
    LEFT JOIN dist_override dov
           ON dov.meet_id = {r}.meet_id
          AND dov.div_id  = {r}.div_id
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
    """Race distance -- the corrected one first, then either scraped source.

    ★ SAME PRECEDENCE AS EVERY OTHER READER. backfill_normalize, the engine's
      _xcQuery, build_ranking_results and apply_tilt all take dist_override
      ahead of the scrape; this file did not, so the site disagreed with its
      own ratings about how long the race was. See _dist_override_join.

    anet keeps the scraped value on `meets`; tfrrs keeps it per division
    inside a jsonb blob.
    """
    return (f"COALESCE(dov.distance::real, m.distance, "
            f"{_blob(r, 'distance')}::real)")


# Creates the app; __name__ tells Flask where "here" is
app = Flask(__name__)

# ★ TRACEBACKS SURVIVE THE SCROLLBACK. A 500's stack trace used to exist
#   only in the console window running the server -- gone by the time
#   anyone asked "where is that traceback". Flask routes unhandled
#   exceptions through app.logger; this handler lands them (with the
#   full stack) in racecast/errors.log.
import logging as _logging
import os as _os
# encoding=: Windows would open this cp1252 and a traceback quoting a
# source line with ★ in it would be dropped (logging eats the error, but
# the evidence is lost -- same disease that 500'd /search via db_timing).
_err_handler = _logging.FileHandler(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                  "errors.log"), encoding="utf-8")
_err_handler.setLevel(_logging.ERROR)
_err_handler.setFormatter(_logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s"))
app.logger.addHandler(_err_handler)

# ★ "Name (ST)" IS THE SITE-WIDE DEFAULT for a school. The raw tables
#   keep the raw strings (the scrapers would fight anything else); the
#   qualified label lives in the derived layer -- school_identity, built
#   at pipeline 10b -- and renders through this filter. Loaded once per
#   process; restart after a pipeline to pick up fresh labels. Missing
#   table (old database, mid-rebuild) = plain names, never an error.
import school_identity
school_identity.loadLabels(getConn)
app.template_filter("school_label")(school_identity.schoolLabel)


@app.errorhandler(404)
def not_found(_err):
    """The branded not-found page; every abort(404) and dead URL lands here
    instead of on the bare Flask default. See templates/404.html."""
    return render_template("404.html"), 404


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


def get_homepage_recent(cur):
    """The "Latest results" rows panels.py precomputed, grouped by sport.

    Empty dict on a database whose panels build predates homepage_recent --
    the module simply doesn't render, same posture as every other
    missing-table fallback in this file."""
    try:
        cur.execute("""
            SELECT sport, meet_id, meet_name, course_name, state,
                   date, n_results
            FROM   homepage_recent
            ORDER  BY sport, rank
        """)
        rows = cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        cur.connection.rollback()
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["sport"], []).append(r)
    return out


@app.route("/")
def home():
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            rows = get_homepage_panels(cur)
            meta = get_homepage_meta(cur)
            recent = get_homepage_recent(cur)

    # HS-equivalent view: each panel row carries its board's pool + sport;
    # season means and career bests take the representative factor.
    has_hs_view = stampBoardRows(rows, rating_keys=("rating",))

    panels = group_panels(rows)
    panels = pad_pool_pairs(panels)      # equalize lengths for the grid

    # Default sport: what panels.py decided from the wall clock, falling back
    # to XC if the meta row is somehow missing.
    default_sport = meta.get("default_sport") or "XC"

    return render_template("home.html",
                           has_hs_view=has_hs_view,
                           panels=panels,
                           meta=meta,
                           recent=recent,
                           default_sport=default_sport)



_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


@app.route("/meets")
def meets_page():
    """Every recent meet, grouped by month -- the view-all behind the home
    page's Latest results module. Serves the homepage_recent precompute
    (see panels.py), so it costs one small indexed read per view.

    ?course=X is a different page wearing the same clothes: every meet ever
    held at one course, grouped by YEAR (a venue spans decades; months are
    for the rolling recent list), from a live query with no results floor.
    The course page's meets table links here as its view-all."""
    from panels import RECENT_MIN_RESULTS

    course = (request.args.get("course") or "").strip()
    if course:
        with getConn() as conn:
            with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                rows = get_course_meets(cur, course, dist=None, limit=2000)
        years, current = [], None
        for m in rows:
            d = m.get("last_date")
            label = str(d.year) if d else "Undated"
            if current is None or current[0] != label:
                current = (label, [])
                years.append(current)
            current[1].append(m)
        return render_template("meets.html", course=course, months=years,
                               n_meets=len(rows), sport="XC",
                               min_results=RECENT_MIN_RESULTS)

    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        sport = "XC"

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            recent = get_homepage_recent(cur)

    # rows arrive newest first; group into (month label, rows) runs. A
    # malformed date cannot happen here (panels.py regex-guards the window),
    # but the fallback label keeps a surprise from taking the page down.
    months, current = [], None
    for m in recent.get(sport, []):
        d = m.get("date") or ""
        try:
            label = f"{_MONTHS[int(d[5:7]) - 1]} {d[:4]}"
        except (ValueError, IndexError):
            label = "Undated"
        if current is None or current[0] != label:
            current = (label, [])
            months.append(current)
        current[1].append(m)

    return render_template("meets.html", sport=sport, months=months,
                           min_results=RECENT_MIN_RESULTS)


# ===================================================================== #
#  HEAD TO HEAD
# ===================================================================== #

@app.route("/compare")
def compare_page():
    """Two athletes, one page: the record where they actually raced each
    other, seasons side by side, PRs, and both careers on one chart.
    Query builders live in compare.py; this route only assembles and
    stamps the HS-equivalent values."""
    from compare import (athleteCard, meetings, record, seasonRows,
                         bestRows, bestRatingRows, ratingSeries, chartPoints)

    a = request.args.get("a", type=int)
    b = request.args.get("b", type=int)
    ctx = {"card_a": None, "card_b": None, "same": bool(a and a == b)}

    # Arriving from an athlete page carries one id: prefill that picker so
    # the reader only has to find the rival.
    if bool(a) != bool(b):
        with getConn() as conn:
            with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if a:
                    ctx["card_a"] = athleteCard(cur, a)
                else:
                    ctx["card_b"] = athleteCard(cur, b)

    if a and b and a != b:
        with getConn() as conn:
            with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                card_a = athleteCard(cur, a)
                card_b = athleteCard(cur, b)
                if card_a and card_b:
                    # ! DEFAULT BEFORE STAMPING. rv() treats a missing key as
                    #   "has an alternate" (Undefined is not None in Jinja),
                    #   so a card that never gets stamped must carry an
                    #   explicit None.
                    card_a["season_rating_hs"] = None
                    card_b["season_rating_hs"] = None
                    mtgs = meetings(cur, a, b)
                    wa, wb, ties, avg = record(mtgs)
                    seasons = seasonRows(card_a, card_b)
                    bests = bestRows(cur, a, b)
                    brate = bestRatingRows(cur, a, b)
                    series_a = ratingSeries(cur, a)
                    series_b = ratingSeries(cur, b)

                    # HS-equivalent view: chart points get exact per-race
                    # factors; season means and the best-rated race carry
                    # their pool and take the board treatment.
                    has_hs = False
                    for sport in ("XC", "TF"):
                        for series in (series_a, series_b):
                            sub = [p for p in series if p["sport"] == sport]
                            has_hs = stampRowsHs(cur, sport, sub,
                                                 distance_key="distance") \
                                     or has_hs
                    cells = [c[s] for c in seasons for s in ("a", "b")
                             if c.get(s)]
                    has_hs = stampBoardRows(cells, rating_keys=("rating",)) \
                             or has_hs
                    head_cells = []
                    for card in (card_a, card_b):
                        if card["season_rating"] is not None:
                            cell = {"rating": card["season_rating"],
                                    "pool": card["pool"],
                                    "sport": card["season_sport"]}
                            head_cells.append((card, cell))
                    has_hs = stampBoardRows([c for _p, c in head_cells],
                                            rating_keys=("rating",)) or has_hs
                    for card, cell in head_cells:
                        card["season_rating_hs"] = cell.get("hs_rating")
                    has_hs = stampBoardRows(
                        list(brate.values()),
                        rating_keys=("speed_rating",)) or has_hs

                    # The chart speaks the athlete page's own point shape,
                    # drawn by athlete-charts.js (drawCompareChart).
                    chart = {"series": [
                        {"name": card_a["name"].split()[-1],
                         "colour": "#14477d",
                         "points": chartPoints(series_a)},
                        {"name": card_b["name"].split()[-1],
                         "colour": "#b45309",
                         "points": chartPoints(series_b)},
                    ]}
                    ctx.update(card_a=card_a, card_b=card_b,
                               meetings=mtgs, wins_a=wa, wins_b=wb,
                               ties=ties, avg_margin=avg,
                               seasons=seasons, bests=bests, brate=brate,
                               chart=chart, has_hs_view=has_hs)

    # Short names for the margin and edge labels: the last word carries
    # the identity in almost every real name.
    for key in ("card_a", "card_b"):
        card = ctx.get(key)
        if card:
            card["short"] = card["name"].split()[-1]
    return render_template("compare.html", **ctx)


# ★ TRAINING PACES FROM THE ATHLETE'S OWN RACES, which is the only place this
#   works. Critical speed is the slope of a distance-time line, so it needs two
#   races at different distances -- and the conversions page takes ONE result,
#   which is why it can only offer a coaching rule of thumb. Here the whole
#   season is already on file and the requirement costs the reader nothing.
#
# ⚠ ONE SEASON, NEWEST FIRST, AND NOT A CAREER. CS is a fitness and fitness
#   moves; pairing a freshman 3200 against a senior 5K measures growing up.
#   Walks back a season at a time and stops at the first that fits, so a
#   runner whose current season is all 5Ks still gets last year's paces rather
#   than nothing -- labelled with the year it came from.
#
# ! RAW time_seconds AND real distance. NOT normalized_time: that column
#   already contains the distance correction, so a distance-time line built
#   from it would be fitting this project's own exponent back to itself.
def _athletePaces(cur, person_id):
    import paces                       # noqa: E402
    cur.execute("""
        SELECT year, pool, distance, time_seconds
        FROM   ranking_results
        WHERE  person_id = %s
          AND  time_seconds > 0
          AND  distance > 0
        ORDER  BY year DESC
    """, (person_id,))
    rows = cur.fetchall()
    if not rows:
        return None

    by_year = {}
    for r in rows:
        by_year.setdefault(r["year"], []).append(r)

    for year in sorted(by_year, reverse=True):
        races = [(float(r["distance"]), float(r["time_seconds"]))
                 for r in by_year[year]]
        got, why = paces.criticalSpeed(races)
        if got is None:
            continue
        cs, dprime = got
        ladder = paces.trainingPaces(races)
        if not ladder:
            continue
        # VDOT off the model's own 5K, so it is one number for one fitness
        # rather than one that moves with whichever race is quoted.
        t5k = paces._timeFor(cs, dprime, 5000.0)
        pool = by_year[year][0]["pool"]
        return {
            "year": year,
            "n_races": len(races),
            "paces": ladder,
            "dprime": round(dprime),
            "vdot": paces.vdot(t5k, 5000.0, pool),
            "equiv_5k": t5k,
        }

    # ! THE REASON, NOT SILENCE. "No paces" and "your races are all the same
    #   distance" are different messages, and the second one tells a runner
    #   what to do about it.
    newest = sorted(by_year, reverse=True)[0]
    _, why = paces.criticalSpeed(
        [(float(r["distance"]), float(r["time_seconds"]))
         for r in by_year[newest]])
    return {"year": newest, "paces": [], "reason": why}


# The season rank line's scope sets, per level (the pool's prefix). Broadest
# to narrowest, ending at the athlete's place on their own team. "wip:"
# scopes render muted until their data exists -- HS sections/divisions/
# leagues and college divisions/regions/conferences are not in the data yet.
_RANK_SCOPES = {
    "ms":      ("nation", "state", "team"),
    "hs":      ("nation", "state", "wip:Section", "wip:Division",
                "wip:League", "team"),
    "college": ("nation", "wip:Division", "wip:Region", "wip:Conference",
                "state", "team"),
}


def buildRankLine(cur, person_id, season):
    """The entries for the rank line under the athlete's stat strip.

    Nation and state come from the SAME machinery the rankings page's
    find-yourself feature uses (rankOf with default filters, so the number
    here matches the board the link lands on); team is the athlete's place
    on their school's roster for the season, by the same mean-rating order
    the school page sorts by. All for the athlete's LATEST season -- the one
    the header rating already describes.

    Returns None when there is nothing real to show (unrankable pool, or no
    scope produced a number): a line of nothing but "soon" is noise.
    """
    from urllib.parse import quote
    from werkzeug.datastructures import MultiDict

    level = (season.get("pool") or "").split("_", 1)[0]
    scopes = _RANK_SCOPES.get(level)
    if not scopes:
        return None

    sport = season["sport"]
    label_year = season["year"] + 1 if sport == "TF" else season["year"]
    state = (season.get("state") or "").strip().upper() or None
    school = season.get("school")

    def boardArgs(with_state):
        # ! min_races=1, NOT the board's default floor (20 TF / 8 XC). The
        #   floor keeps thin seasons off the public board, but this line is
        #   about ONE athlete -- a 12-race college season ranking nowhere
        #   because 12 < 20 reads as a missing feature, not a policy. The
        #   href carries the same filter, so the board the link opens shows
        #   the same number the line does.
        args = {"board": "ability", "pool": season["pool"], "sport": sport,
                "year": str(label_year), "min_races": "1"}
        if with_state:
            args["state"] = state
        return args

    def boardRank(with_state):
        which = "state" if with_state else "nation"
        f, err = parseFilters(MultiDict(boardArgs(with_state)))
        if err:
            print(f"rank_line: {which} filters refused ({err})", flush=True)
            return None
        try:
            # Default sort, so rankOf takes its count-based shortcut -- an
            # indexed count, not a board sort, safe on the page-load path.
            # ⚠ ON A PLAIN CURSOR. rankings' rank machinery indexes tuple
            #   rows (row[0]); the athlete route's RealDict cursor made
            #   every lookup KeyError and the line silently dropped its
            #   board scopes.
            with cur.connection.cursor() as plain:
                return rankOf(plain, f, person_id)
        except Exception as exc:         # noqa: BLE001 -- a line, not a page
            cur.connection.rollback()
            print(f"rank_line: {which} rank failed "
                  f"({type(exc).__name__}: {exc})", flush=True)
            return None

    def boardHref(with_state):
        q = "&".join(f"{k}={v}" for k, v in boardArgs(with_state).items())
        return "/rankings?" + q

    entries = []
    for scope in scopes:
        if scope.startswith("wip:"):
            entries.append({"label": scope[4:], "wip": True})
        elif scope == "nation":
            r = boardRank(False)
            if r:
                entries.append({"label": "Nation", "rank": r,
                                "href": boardHref(False)})
        elif scope == "state" and state:
            r = boardRank(True)
            if r:
                entries.append({"label": state, "rank": r,
                                "href": boardHref(True)})
        elif scope == "team" and school and season.get("mean_rating") is not None:
            try:
                # Strictly-better count + 1 = place on the roster, the same
                # mean-rating order schoolRoster sorts by; ties share it.
                cur.execute("""
                    SELECT count(*) + 1 AS place
                    FROM   athlete_season t
                    WHERE  t.school = %(school)s
                      AND  t.sport  = %(sport)s
                      AND  t.year   = %(year)s
                      AND  t.mean_rating > %(mine)s
                """, {"school": school, "sport": sport,
                      "year": season["year"],
                      "mine": season["mean_rating"]})
                row = cur.fetchone()
            except Exception:            # noqa: BLE001
                cur.connection.rollback()
                row = None
            if row:
                entries.append({
                    "label": "Team", "rank": row["place"],
                    "href": (f"/school/{quote(school, safe='')}"
                             f"?sport={sport}&year={label_year}")})

    if not any("rank" in e for e in entries):
        return None
    return entries


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
                    SELECT mean_rating, sport, pool, year, n_races,
                           state, school
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

            # The season rank line under the stat strip -- see buildRankLine.
            rank_line = (buildRankLine(cur, person_id, season_rating)
                         if season_rating else None)

            # ⚠ THE FALLBACK IS THE OLD TABLE, and it still earns its keep:
            #   athlete_season is built from ranking_results, which is
            #   US-scoped and drops unresolved or untrusted seasons, while
            #   the engine rates them anyway. ORDER BY makes the pick
            #   deterministic -- the bare fetchone() this replaces returned
            #   an arbitrary pool's number.
            rating = None
            if season_rating is None:
                cur.execute("""
                    SELECT speed_rating, pool
                    FROM   athlete_ratings
                    WHERE  athlete_id = %s
                    ORDER  BY n_races DESC NULLS LAST, pool
                    LIMIT  1
                """, (person_id,))
                rating = cur.fetchone()
            races = get_races(cur, person_id)
            # For the HS-equivalent view: which pool each rated row belongs
            # to. Fetched here (the rows), consumed after dedupe (the stamp)
            # -- see pool_view.stampHsRatings.
            pool_rows = fetchPoolRows(cur, person_id)

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

    # 0c. the HS-equivalent view: stamp race["pool"] and race["hs_rating"].
    #     AFTER season_label (the fallback pools by season), BEFORE the
    #     aggregates below (they all carry the alt value along). has_hs_view
    #     is False for a pure-HS career, and the template hides the toggle.
    has_hs_view = stampHsRatings(pool_rows, races)

    # 1. format times for display
    for race in races:
        if not race["is_field"] and race["result"] is not None:
            race["result"] = format_time(race["result"])

    # (chart_data is built ONCE, below, after the record walk -- an earlier
    # copy of the call here was dead work thrown away by the second.)

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
        # A season MEAN has no single race context, so it scales by the
        # median per-race factor of the same displayed season -- computed
        # from the races already stamped above.
        _sf = seasonFactor(races, label=str(label),
                           sport=season_rating["sport"])
        athlete["rating_hs"] = (float(athlete["rating"]) * _sf
                                if athlete["rating"] is not None and _sf
                                else None)
    else:
        athlete["rating"] = rating["speed_rating"] if rating else None
        athlete["rating_note"] = None
        # The fallback number has no season attached; the career-wide
        # median factor is the honest stand-in.
        _sf = seasonFactor(races)
        athlete["rating_hs"] = (float(athlete["rating"]) * _sf
                                if athlete["rating"] is not None and _sf
                                else None)

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

    # ! ITS OWN CONNECTION SCOPE. The block above closed the cursor it opened;
    #   reopening for one small query keeps this out of the long-lived one.
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            training = _athletePaces(cur, person_id)
    athlete["person_id"] = person_id

    return render_template("athlete.html",
                           athlete=athlete,
                           rank_line=rank_line,
                           training=training,
                           xc_seasons=xc_seasons,
                           tf_seasons=tf_seasons,
                           tf_dists=tf_dists,
                           alltime=alltime,
                           season_bests=season_best_list,
                           has_hs_view=has_hs_view,
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
               r.source                      AS source,
               -- Finishing place, DERIVED -- the stored `place` column has
               -- never been trusted (see meet_compile). Count of strictly
               -- faster finishers + 1 = standard competition ranking, ties
               -- share a place. 999999 is the DNS/DNF sentinel, not a time.
               -- ! ONE lateral pass per race, both counts via FILTER: the
               --   two-subquery version scanned each division twice, and a
               --   200-race career paid 400 scans per page view.
               pl.place, pl.team_place
        FROM results r
        LEFT JOIN LATERAL (
            SELECT count(*) FILTER (WHERE r2.time_seconds < r.time_seconds)
                       + 1 AS place,
                   count(*) FILTER (WHERE r2.time_seconds < r.time_seconds
                       AND r2.school IS NOT DISTINCT FROM r.school)
                       + 1 AS team_place
            FROM results r2
            WHERE r2.meet_id = r.meet_id AND r2.div_id = r.div_id
              AND r2.time_seconds IS NOT NULL
              AND r2.time_seconds < 999999
        ) pl ON TRUE
        LEFT JOIN meets m
               ON m.div_id  = r.div_id
              AND m.meet_id = r.meet_id
              AND m.source  = r.source
        {_tfrrs_join('r')}{_dist_override_join('r')}
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
               r.source                      AS source,
               -- Same derived place as the XC half; the race here is
               -- (meet, event, div), div_id nullable on tfrrs rows. Field
               -- events rank by mark, not time -- no place rather than a
               -- wrong one. Same single-lateral shape as the XC half.
               CASE WHEN COALESCE(r.is_field, 0) = 1 THEN NULL
                    ELSE pl.place END       AS place,
               CASE WHEN COALESCE(r.is_field, 0) = 1 THEN NULL
                    ELSE pl.team_place END  AS team_place
       FROM results_tf r
        LEFT JOIN LATERAL (
            SELECT count(*) FILTER (WHERE r2.time_seconds < r.time_seconds)
                       + 1 AS place,
                   count(*) FILTER (WHERE r2.time_seconds < r.time_seconds
                       AND r2.school IS NOT DISTINCT FROM r.school)
                       + 1 AS team_place
            FROM results_tf r2
            WHERE r2.meet_id = r.meet_id
              AND r2.event_id = r.event_id
              AND r2.div_id IS NOT DISTINCT FROM r.div_id
              AND r2.time_seconds IS NOT NULL
              AND r2.time_seconds < 999999
        ) pl ON TRUE
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
                "canon_meet_id",
                # place is computed WITHIN the linked division; the tfrrs
                # twin's div 0 would give a different, wrong number.
                "place", "team_place"}


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


@app.template_filter("ordinal")
def ordinal(n):
    """1 -> '1st', 12 -> '12th'. None or junk renders empty, never raises."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd",
                                            3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


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
            "rating_hs": season_rating(races, key="hs_rating"),
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


def season_rating(races, key="speed_rating"):
    """Average the per-race speed ratings for one season, ignoring unrated
    races. key="hs_rating" averages the HS-equivalent view instead."""
    rated = [r[key] for r in races if r.get(key) is not None]
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
        {_tfrrs_join('r')}{_dist_override_join('r')}
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


# Same distance band the PR rankings board uses (rankings.PR_DISTANCE_TOL):
# 5000 and 5000.0 and a 5010m remeasure are one distance, 4828m is not.
_REC_DIST_TOL = 0.0025


def stampRecordFlags(cur, sport, rows, distance, race_date):
    """Stamp is_pr / is_sr onto race result rows, anet-style.

    A row is a PR when no earlier rated race by that athlete at this
    distance was faster, and an SR when none THIS SEASON was (PR wins the
    badge; the template shows one or the other). "Earlier" means strictly
    before this race's date, so a record here that fell later in the season
    still reads PR -- it was one when it was run, which is the claim the
    badge makes on the athlete page too.

    One query over ranking_results for the whole field (person_id is
    indexed). An athlete with no earlier rated race at the distance gets
    the PR badge -- a debut at a distance is that athlete's best at it,
    which is how every results site treats it.
    """
    if not distance or not race_date:
        return
    pids = [r["person_id"] for r in rows
            if r.get("person_id")
            and r.get("time_seconds") and r["time_seconds"] < 999999]
    if not pids:
        return
    yr = seasonYearFromIso(sport, race_date)
    try:
        cur.execute("""
            SELECT person_id,
                   min(time_seconds) FILTER (
                       WHERE distance BETWEEN %(lo)s AND %(hi)s)
                       AS best_before,
                   min(time_seconds) FILTER (
                       WHERE distance BETWEEN %(lo)s AND %(hi)s
                         AND year = %(yr)s)
                       AS season_best_before
            FROM   ranking_results
            WHERE  sport = %(sport)s
              AND  person_id = ANY(%(pids)s)
              AND  race_date < %(day)s
              AND  time_seconds > 0 AND time_seconds < 999999
            GROUP  BY person_id
        """, {"sport": sport, "pids": pids, "day": race_date, "yr": yr,
              "lo": float(distance) * (1 - _REC_DIST_TOL),
              "hi": float(distance) * (1 + _REC_DIST_TOL)})
        prior = {r["person_id"]: r for r in cur.fetchall()}
    except Exception:                    # noqa: BLE001 -- UndefinedTable et al.
        cur.connection.rollback()
        return

    for row in rows:
        t = row.get("time_seconds")
        if not row.get("person_id") or not t or t >= 999999:
            continue
        p = prior.get(row["person_id"])
        best = p["best_before"] if p else None
        season = p["season_best_before"] if p else None
        row["is_pr"] = best is None or float(t) < float(best)
        row["is_sr"] = (not row["is_pr"]
                        and (season is None or float(t) < float(season)))


@app.route("/race/xc/<int:meet_id>/<int:div_id>")
def race_xc(meet_id, div_id):
    from meet_compile import scoreRows, publishedScores, annotateScoring

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header  = get_race_header(cur, meet_id, div_id)
            results = get_race_results(cur, meet_id, div_id)
            published = publishedScores(cur, meet_id)
            # HS-equivalent view: one race, one distance; pools per row.
            has_hs_view = (stampRowsHs(cur, "XC", results,
                                       distance=header.get("distance"))
                           if header else False)
            if header and results:
                stampRecordFlags(cur, "XC", results,
                                 header.get("distance"),
                                 results[0].get("date"))

    # Stamp score_place / team_place on the rendered rows themselves --
    # scoreRows below runs on `ranked` COPIES, so its stamps never reach
    # the table.
    annotateScoring(results)

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
        # ⚠ AND A DUPLICATED NAME GRAFTS TO NOBODY. NXN 2025 published two
        #   Jesuits (287 and 336 points) and two Lincolns -- different
        #   schools, one string. Grafting by name gave both the SAME seven
        #   runners; no scorers shown is honest, the same seven twice is not.
        from collections import Counter
        dup = {s for s, c in Counter(t["school"] for t in pub).items()
               if c > 1}
        teams = [{**t, "runners": ([] if t["school"] in dup else
                                   by_school.get(t["school"], {})
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
                           has_hs_view=has_hs_view,
                           header=header,
                           results=results,
                           race_date=race_date,
                           scores=scores)


# ===================================================================== #
#  XC MEET
# ===================================================================== #

def get_meet_header(cur, meet_id, source=None):
    """Basic info for a meet — taken from any one of its divisions.

    ★ Driven from `results` for the same reason as get_race_header: a tfrrs
      meet has no `meets` row, so the old `FROM meets` returned None and the
      route 404'd.

    ⚠ `source` picks WHICH meet when two share the id — the anet and tfrrs
      id spaces overlap, and without it a tfrrs meet's page could carry the
      colliding anet meet's name.
    """
    cur.execute(f"""
        SELECT COALESCE(m.meet_name, mt.venue_name) AS meet_name,
               {_xc_course_sql('r')}                AS course_name,
               m.state                              AS state,
               r.meet_id                            AS meet_id
        FROM (SELECT DISTINCT meet_id, div_id, source
                FROM results WHERE meet_id = %(meet)s
                 AND (%(src)s::text IS NULL OR source = %(src)s)) r
        LEFT JOIN meets m
               ON m.meet_id = r.meet_id
              AND m.div_id  = r.div_id
              AND m.source  = r.source
        {_tfrrs_join('r')}{_dist_override_join('r')}
        -- Rows that resolved a name sort first, so a meet where only SOME
        -- divisions carry metadata still shows one.
        ORDER BY (COALESCE(m.meet_name, mt.venue_name) IS NOT NULL) DESC
        LIMIT 1
    """, {"meet": meet_id, "src": source})
    return cur.fetchone()


def get_meet_date(cur, table, meet_id, source=None):
    """The meet's date: the earliest sane result date.

    Derived from results because neither meets table carries a date column
    (the race pages already do the same). `date` is TEXT and the corpus
    holds junk years (0023, 2223), hence the regex; min() picks the opening
    day of a multi-day meet. `table` is one of two literals from the
    callers, never user input."""
    assert table in ("results", "results_tf")
    cur.execute(f"""
        SELECT min(date) AS date
        FROM   {table}
        WHERE  meet_id = %(meet)s
          AND  (%(src)s::text IS NULL OR source = %(src)s)
          AND  date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}'
    """, {"meet": meet_id, "src": source})
    row = cur.fetchone()
    return row["date"] if row else None


def get_meet_divisions(cur, meet_id, source=None):
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
        {_tfrrs_join('r')}{_dist_override_join('r')}
        {_athlete_lateral('r')}
        WHERE r.meet_id = %(meet)s
          AND (%(src)s::text IS NULL OR r.source = %(src)s)
        GROUP BY r.div_id, m.division, mt.division_distances,
                 dov.distance, m.distance, r.source
        ORDER BY division NULLS LAST, r.div_id
    """, {"meet": meet_id, "src": source})
    return cur.fetchall()


def meet_sources(cur, table, meet_id):
    """[{source, n}] for one meet_id, biggest first.

    The anet and tfrrs id spaces OVERLAP: one meet_id can hold two different
    real-world meets (15,096 of them in `results`). Measured 2026-08-24 by
    scripts/census_meet_collision.py; the split is by results.source, which
    the census verified clean (the div_id<100 folklore holds for XC and is
    REVERSED for TF, which is why the column and not the folklore is used).
    """
    cur.execute(f"""
        SELECT source, count(*) AS n
        FROM   {table}
        WHERE  meet_id = %(meet)s AND source IS NOT NULL
        GROUP  BY source ORDER BY count(*) DESC
    """, {"meet": meet_id})
    return cur.fetchall()


def pick_source(sources, alt):
    """(chosen, alt_idx, others) from the biggest-first source list.

    ⚠ THE PARAM AND THE PAGE STAY SOURCE-BLIND. Feed names never appear in
      the UI or in a URL -- the site presents one dataset, not its scrapers.
      The toggle is an opaque index (?alt=N) into the size-ordered list;
      each entry in `others` carries the alt value that reaches it and its
      row count, nothing else.
    """
    if not sources:
        return None, 0, []
    try:
        idx = max(0, min(int(alt or 0), len(sources) - 1))
    except (TypeError, ValueError):
        idx = 0
    others = [{"alt": i, "n": s["n"]}
              for i, s in enumerate(sources) if i != idx]
    return sources[idx]["source"], idx, others


@app.route("/meet/xc/<int:meet_id>")
def meet_xc(meet_id):
    from meet_compile import compiledResults, publishedScores

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sources = meet_sources(cur, "results", meet_id)
            src, alt_idx, other_sources = pick_source(
                sources, request.args.get("alt"))
            header    = get_meet_header(cur, meet_id, source=src)
            divisions = get_meet_divisions(cur, meet_id, source=src)
            compiled  = compiledResults(cur, meet_id, source=src)
            meet_date = get_meet_date(cur, "results", meet_id, source=src)

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
                           compiled=compiled_index, meet_date=meet_date,
                           alt_idx=alt_idx, other_sources=other_sources)


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
            # Same source discipline as the meet page: on a colliding id the
            # compiled merge must never mix the two real-world meets.
            sources = meet_sources(cur, "results", meet_id)
            src, _idx, _others = pick_source(sources,
                                             request.args.get("alt"))
            header = get_meet_header(cur, meet_id, source=src)
            groups = compiledResults(cur, meet_id, source=src)
            group = next((g for g in groups
                          if g["distance"] == distance
                          and g["gender"] == gender), None)
            # HS-equivalent view -- inside the block, the stamp needs the
            # cursor for the pools-by-result lookup.
            has_hs_view = (stampRowsHs(cur, "XC", group["results"],
                                       distance=group["distance"])
                           if group else False)

    if header is None or group is None:
        abort(404)

    return render_template("compiled.html", header=header, group=group,
                           has_hs_view=has_hs_view)


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
            sources = meet_sources(cur, "results", meet_id)
            src, _idx, _others = pick_source(sources,
                                             request.args.get("alt"))
            compiled = compiledResults(cur, meet_id, source=src)
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
               r.round,
               r.heat,
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


def _reconstruct_heats(rows):
    """Heats recovered from PLACE RESETS when the heat column is empty.

    The feed enters results heat by heat and its place column restarts
    at 1 in each -- so in result-id order, a place lower than the one
    before it marks a heat boundary (ties, place equal, stay together).
    Gated hard: every reconstructed heat must open at place 1 and there
    must be as many heats as place-1 rows, else the reconstruction is
    rejected and the round renders as one block. An event with GLOBAL
    places trips the gate naturally (one place-1 row, one run).
    Returns [rows_per_heat] in entry order, or None."""
    if len(rows) < 3 or any(r.get("place") is None for r in rows):
        return None
    ordered = sorted(rows, key=lambda r: r["result_id"])
    heats = [[]]
    for r in ordered:
        if heats[-1] and r["place"] < heats[-1][-1]["place"]:
            heats.append([])
        heats[-1].append(r)
    if len(heats) < 2:
        return None
    if any(h[0]["place"] != 1 for h in heats):
        return None
    if sum(1 for r in rows if r["place"] == 1) != len(heats):
        return None
    return heats


def _tf_heat_sections(results, is_field):
    """[{label, rows}] for a race page: rows grouped into the rounds and
    heats the event was actually run in, finals first. Heats come from
    the heat column where the feed filled it, else from place resets
    (see _reconstruct_heats). One undivided field renders as a single
    unlabeled section -- the page looks exactly as before. Each row gets
    sec_place, its place within its own heat."""
    from tf_points import rowRound, parseMark

    def heat_no(r):
        try:
            return int(str(r.get("heat") or "").strip() or 0)
        except ValueError:
            return 0

    rounds = {rowRound(r) for r in results}
    multi_round = len(rounds) > 1
    word = "Flight" if is_field else "Heat"

    def sort_in(r):
        if is_field:
            mk = parseMark(r.get("mark"))
            return (r.get("place") is None, r.get("place") or 0,
                    -(mk if mk is not None else -1e9))
        t = r.get("time_seconds")
        return (t is None, t or 0)

    # round groups first, finals on top
    by_round = {}
    for r in results:
        rd = rowRound(r)
        by_round.setdefault({"final": 0, None: 1,
                             "prelim": 2}.get(rd, 2), []).append(r)

    sections = []
    for rk in sorted(by_round):
        r_rows = by_round[rk]
        r_label = ({0: "Finals", 2: "Prelims"}.get(rk, "")
                   if multi_round else "")

        # explicit heat numbers when the feed filled them...
        heats = {}
        for r in r_rows:
            heats.setdefault(heat_no(r), []).append(r)
        if len(heats) > 1:
            for hn in sorted(heats):
                rows = sorted(heats[hn], key=sort_in)
                for i, r in enumerate(rows):
                    r["sec_place"] = i + 1
                hl = f"{word} {hn}" if hn else ""
                label = " · ".join(x for x in (r_label, hl) if x)
                sections.append({"label": label, "rows": rows})
            continue

        # ...else recover them from place resets
        rebuilt = _reconstruct_heats(r_rows)
        if rebuilt:
            for i, rows in enumerate(rebuilt):
                for r in rows:
                    r["sec_place"] = r["place"]   # official within-heat place
                hl = f"{word} {i + 1}"
                label = " · ".join(x for x in (r_label, hl) if x)
                sections.append({"label": label, "rows": rows})
            continue

        rows = sorted(r_rows, key=sort_in)
        for i, r in enumerate(rows):
            r["sec_place"] = i + 1
        sections.append({"label": r_label, "rows": rows})
    return sections


# ★ SCORING A MEET IS PER-MEET WORK ON A PER-RACE PAGE. Every TF race
#   page needs the whole meet scored just to print its Points column, and
#   a big invitational is thousands of rows -- so the points map caches
#   in-process per (meet, source). Meet results never change after the
#   scrape; the TTL only bounds staleness across a re-scrape.
_TF_POINTS_CACHE = {}
_TF_POINTS_TTL = 6 * 3600
_TF_POINTS_MAX = 256


def _tf_points_cached(cur, meet_id, source):
    """points_by_result for one meet, cached. Compute via the same path
    every page uses; evict oldest beyond the cap."""
    import time as _time
    from tf_points import scoreMeet

    key = (meet_id, source)
    hit = _TF_POINTS_CACHE.get(key)
    now = _time.time()
    if hit and now - hit[0] < _TF_POINTS_TTL:
        return hit[1], hit[2]
    rows = get_tf_meet_scoring_rows(cur, meet_id, source=source)
    stamp_tf_meet_extras(cur, meet_id, rows)
    points = scoreMeet(rows)["points_by_result"]
    # event names ride along: a nameless race page borrows its rows'
    # (possibly catalog-stamped) event_short for its title
    names = {}
    for r in rows:
        k = (r.get("div_id"), r.get("event_id"))
        if k not in names and (r.get("event_short") or "").strip():
            names[k] = r["event_short"]
    _TF_POINTS_CACHE[key] = (now, points, names)
    if len(_TF_POINTS_CACHE) > _TF_POINTS_MAX:
        oldest = min(_TF_POINTS_CACHE, key=lambda k: _TF_POINTS_CACHE[k][0])
        _TF_POINTS_CACHE.pop(oldest, None)
    return points, names


def _tf_seed_points_cache(meet_id, source, rows, scored):
    """Meet and compiled pages already scored the meet; bank it so the
    race pages ride their work."""
    import time as _time
    names = {}
    for r in rows:
        k = (r.get("div_id"), r.get("event_id"))
        if k not in names and (r.get("event_short") or "").strip():
            names[k] = r["event_short"]
    _TF_POINTS_CACHE[(meet_id, source)] = (
        _time.time(), scored["points_by_result"], names)


@app.route("/race/tf/<int:meet_id>/<int:event_id>/<int:div_id>")
def race_tf(meet_id, event_id, div_id):
    from tf_points import prettyEventName

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header  = get_tf_race_header(cur, meet_id, div_id, event_id)
            results = get_tf_race_results(cur, meet_id, div_id, event_id)
            # HS-equivalent view: the event's distance; pools per row.
            has_hs_view = (stampRowsHs(cur, "TF", results,
                                       distance=header.get("distance_meters"))
                           if header else False)
            if header and results:
                stampRecordFlags(cur, "TF", results,
                                 header.get("distance_meters"),
                                 results[0].get("date"))
            # Points come from scoring the WHOLE meet, not this page's rows:
            # a prelim page's athletes score in the final, and a sectioned
            # final scores across its sections. Scope to this race's own
            # source first -- the meet_id collision rule. Cached per meet:
            # only the first race page of a meet pays for the scoring.
            points_by_result, event_names = {}, {}
            if header and results:
                cur.execute("""
                    SELECT source FROM results_tf
                    WHERE meet_id = %(meet)s AND div_id = %(div)s
                      AND event_id = %(event)s AND source IS NOT NULL
                    GROUP BY source ORDER BY count(*) DESC LIMIT 1
                """, {"meet": meet_id, "div": div_id, "event": event_id})
                srow = cur.fetchone()
                race_src = srow["source"] if srow else None
                points_by_result, event_names = _tf_points_cached(
                    cur, meet_id, race_src)

    if header is None:
        abort(404)

    header["gender"] = _field_gender(results)
    # A nameless event's page borrows its rows' (possibly catalog-
    # stamped) name; then 'hj' is a column value, not a title.
    if not (header.get("event_short") or "").strip():
        header["event_short"] = event_names.get((div_id, event_id))
    if header.get("event_short"):
        header["event_short"] = prettyEventName(header["event_short"])

    for row in results:
        if row["is_field"]:
            row["display_result"] = row["mark"] if row["mark"] else "—"
        elif row["time_seconds"] is not None:
            row["display_result"] = format_time(row["time_seconds"])
        else:
            row["display_result"] = "—"
        row["points"] = points_by_result.get(row["result_id"], "")

    race_date = results[0]["date"] if results else None
    sections = _tf_heat_sections(results,
                                 any(r.get("is_field") for r in results))

    return render_template("race_tf.html",
                           has_hs_view=has_hs_view,
                           header=header,
                           results=results,
                           sections=sections,
                           race_date=race_date)


# ===================================================================== #
#  TF MEET
# ===================================================================== #

def get_tf_meet_header(cur, meet_id, source=None):
    """Basic info for a TF meet, from any one of its events.

    ⚠ `source` picks WHICH meet when the anet and tfrrs id spaces collide on
      this id — LIMIT 1 without it could hand a tfrrs meet the colliding anet
      meet's name.
    """
    cur.execute("""
        SELECT meet_name, state, is_indoor, meet_id, location_id
        FROM meets_tf
        WHERE meet_id = %(meet)s
          AND (%(src)s::text IS NULL OR source = %(src)s)
        LIMIT 1
    """, {"meet": meet_id, "src": source})
    return cur.fetchone()


def get_tf_meet_events(cur, meet_id, source=None):
    """Every event in this TF meet, with result counts.

    ★ NO ATHLETE LATERAL AND NO GENDER AGGREGATE HERE. This query used
      to probe the 16M-row athletes table once per RESULT to compute a
      majority gender per event -- but the route scores the meet anyway,
      and the scorer resolves every event's gender (and name) deeper
      than a mode() could. The route reuses that; this stays cheap."""
    cur.execute("""
        SELECT m.div_id,
               m.event_id,
               -- Nameless meets_tf rows borrow their results' own name
               -- (the per-row copy is populated on plenty of them).
               COALESCE(NULLIF(TRIM(m.event_short), ''),
                        mode() WITHIN GROUP (
                            ORDER BY NULLIF(TRIM(r.event_short), '')))
                   AS event_short,
               m.division,
               m.distance_meters,
               count(r.result_id) AS n_results
        FROM meets_tf m
        LEFT JOIN results_tf r
               ON r.meet_id  = m.meet_id
              AND r.div_id   = m.div_id
              AND r.event_id = m.event_id
        WHERE m.meet_id = %(meet)s
          AND (%(src)s::text IS NULL OR m.source = %(src)s)
        GROUP BY m.div_id, m.event_id, m.event_short, m.division, m.distance_meters
        ORDER BY m.division, m.distance_meters NULLS LAST, m.event_short
    """, {"meet": meet_id, "src": source})
    return cur.fetchall()


def stamp_home_states(cur, rows):
    """Stamp each scoring row with its athlete's home state (the modal
    state of their racing history, person_home_state at pipeline 10b).
    tf_points' _splitMap reads it to split a school name two real
    schools share. No table yet = no stamps = no splits."""
    from school_identity import homeStates
    hs = homeStates(cur, {r.get("person_id") for r in rows})
    for r in rows:
        r["home_state"] = hs.get(r.get("person_id"))


def get_tf_meet_scoring_rows(cur, meet_id, source=None):
    """Every result in a TF meet with the event context tf_points needs.

    One query for the whole meet, because scoring cannot work event page by
    event page: prelims and finals arrive as DIFFERENT event_ids, and only
    the full set lets tf_points merge them back into one scored event.

    ⚠ `source` must scope BOTH tables. The anet and tfrrs id spaces collide
      on meet_id (see meet_sources); an unscoped join could score two
      real-world meets as one.
    """
    cur.execute(f"""
        SELECT r.result_id,
               r.person_id,
               r.time_seconds,
               r.mark,
               r.is_field,
               COALESCE(r.is_relay, 0) AS is_relay,
               r.result_kind,
               r.round,
               r.place,
               r.event_type_id,
               r.grade,
               r.school,
               r.speed_rating,
               r.score,
               r.date,
               -- The meets_tf name when it has one; else the RESULT's own
               -- event_short -- the per-row copy is populated on plenty of
               -- events whose meets_tf row is nameless.
               COALESCE(NULLIF(TRIM(m.event_short), ''),
                        NULLIF(TRIM(r.event_short), '')) AS event_short,
               m.division,
               m.distance_meters,
               m.div_id,
               m.event_id,
               {_name_sql('r')} AS athlete_name,
               a.gender          AS gender
        FROM results_tf r
        JOIN meets_tf m ON m.meet_id  = r.meet_id
                       AND m.div_id   = r.div_id
                       AND m.event_id = r.event_id
        {_athlete_lateral('r')}
        WHERE r.meet_id = %(meet)s
          AND (%(src)s::text IS NULL
               OR (r.source = %(src)s AND m.source = %(src)s))
          AND (r.time_seconds IS NOT NULL OR r.mark IS NOT NULL)
        ORDER BY r.time_seconds ASC NULLS LAST
    """, {"meet": meet_id, "src": source})
    return cur.fetchall()


def _tf_meet_extras(cur, meet_id, column):
    """One JSONB blob from meet_extras for a TF meet, or None. Wrapped so
    a database without the table degrades to the no-blob path."""
    try:
        cur.execute(f"""
            SELECT {column} FROM meet_extras
            WHERE meet_id = %(meet)s AND sport = 'TF'
        """, {"meet": meet_id})
        row = cur.fetchone()
    except Exception:                    # noqa: BLE001 -- UndefinedTable et al.
        cur.connection.rollback()
        return None
    if row is None:
        return None
    return row[column] if isinstance(row, dict) else row[0]


def _pick(d, *keys):
    """First present, non-null value among possible feed spellings."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _tf_event_type_names(cur, meet_id):
    """{event_type_id: name} from the meet's eventTypes catalog blob.

    ★ THIS is how a nameless event gets its real name: every result row
      carries event_type_id, and the catalog the feed shipped alongside
      the meet names each one. Defensive about key spellings because the
      blob is stored verbatim from the feed.
    """
    blob = _tf_meet_extras(cur, meet_id, "event_types_json")
    if not isinstance(blob, list):
        return {}
    names = {}
    for e in blob:
        if not isinstance(e, dict):
            continue
        tid = _pick(e, "ID", "Id", "id", "EventTypeID", "EventTypeId")
        name = _pick(e, "Name", "name", "EventName", "Description",
                     "EventShort", "Title")
        if tid is not None and isinstance(name, str) and name.strip():
            try:
                names[int(tid)] = name.strip()
            except (TypeError, ValueError):
                continue
    return names


def _tf_relay_leg_genders(cur, meet_id):
    """{result_id: 'M'/'F'} for relay squads, from the ACTUAL runners:
    meet_extras.relay_legs_json lists each squad's legs by athlete id,
    and the athletes table knows their genders. The majority of the
    listed legs genders the squad; a split squad stays unknown."""
    blob = _tf_meet_extras(cur, meet_id, "relay_legs_json")
    if not isinstance(blob, list):
        return {}
    legs = {}
    for e in blob:
        if not isinstance(e, dict):
            continue
        rid = _pick(e, "IDResult", "ResultID", "ResultId", "result_id",
                    "IDRelayResult", "RelayResultID")
        aid = _pick(e, "AthleteID", "AthleteId", "athlete_id", "IDAthlete")
        if rid is None or aid is None:
            continue
        try:
            legs.setdefault(int(rid), []).append(int(aid))
        except (TypeError, ValueError):
            continue
    if not legs:
        return {}
    all_ids = sorted({a for v in legs.values() for a in v})
    genders = {}
    try:
        cur.execute("""
            SELECT athlete_id, gender FROM athletes
            WHERE athlete_id = ANY(%(ids)s) AND gender IN ('M', 'F')
        """, {"ids": all_ids})
        for rec in cur.fetchall():
            aid, g = ((rec["athlete_id"], rec["gender"])
                      if isinstance(rec, dict) else (rec[0], rec[1]))
            genders[aid] = g
    except Exception:                    # noqa: BLE001
        cur.connection.rollback()
        return {}
    out = {}
    for rid, aids in legs.items():
        gs = [genders[a] for a in aids if a in genders]
        m, f = gs.count("M"), gs.count("F")
        if m != f:
            out[rid] = "M" if m > f else "F"
    return out


def stamp_tf_meet_extras(cur, meet_id, rows):
    """Enrich scoring rows with what the meet's sidecar blobs know: real
    names for nameless events (which also lets their rounds merge), and
    real genders for relay squads -- so tf_points' median-pairing
    fallback only fires where the feed genuinely said nothing."""
    names = _tf_event_type_names(cur, meet_id)
    if names:
        for r in rows:
            if not (r.get("event_short") or "").strip():
                nm = names.get(r.get("event_type_id"))
                if nm:
                    r["event_short"] = nm
    relay_g = _tf_relay_leg_genders(cur, meet_id)
    if relay_g:
        for r in rows:
            if r.get("is_relay") and not r.get("gender"):
                g = relay_g.get(r.get("result_id"))
                if g:
                    r["gender"] = g


def _stamp_tf_display(rows):
    """row["display_result"]: the mark string for field events and multis
    (result_kind says what `mark` holds), the formatted time otherwise --
    the same rule race_tf renders by."""
    for r in rows:
        if r.get("is_field") or r.get("result_kind") in ("field", "combined"):
            r["display_result"] = r["mark"] if r.get("mark") else "—"
        elif r.get("time_seconds") is not None:
            r["display_result"] = format_time(r["time_seconds"])
        else:
            r["display_result"] = "—"


@app.route("/meet/tf/<int:meet_id>")
def meet_tf(meet_id):
    from tf_points import scoreMeet, genderOf, prettyEventName, eventDistance

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sources = meet_sources(cur, "meets_tf", meet_id)
            src, alt_idx, other_sources = pick_source(
                sources, request.args.get("alt"))
            header = get_tf_meet_header(cur, meet_id, source=src)
            events = get_tf_meet_events(cur, meet_id, source=src)
            meet_date = get_meet_date(cur, "results_tf", meet_id, source=src)
            scoring_rows = get_tf_meet_scoring_rows(cur, meet_id, source=src)
            stamp_tf_meet_extras(cur, meet_id, scoring_rows)
            stamp_home_states(cur, scoring_rows)

    if header is None:
        abort(404)

    scored = scoreMeet(scoring_rows)
    _tf_seed_points_cache(meet_id, src, scoring_rows, scored)

    # The scorer already resolved each raw event's gender and name as
    # deeply as it can (name word or catalog name, athlete majority,
    # relay legs, relay pairing) -- reuse that here rather than
    # re-deriving a shallower answer.
    scored_gender, scored_name = {}, {}
    for d in scored["divisions"]:
        for ev in d["events"]:
            for rw in ev["rows"]:
                k = (rw.get("div_id"), rw.get("event_id"))
                if ev["gender"] in ("M", "F"):
                    scored_gender[k] = ev["gender"]
                scored_name[k] = ev["name"]

    # Bare feed codes get their reader names, a nameless event takes its
    # catalog or distance name, and events sort by distance parsed from
    # the name when the column is empty, so the 200 stops listing after
    # the 3200 and nameless events sink to the end.
    for e in events:
        k = (e["div_id"], e["event_id"])
        e["gender"] = (genderOf(e.get("event_short")) or
                       scored_gender.get(k) or e.get("gender"))
        if e.get("event_short"):
            e["display_name"] = prettyEventName(e["event_short"])
        elif scored_name.get(k):
            e["display_name"] = scored_name[k]
        elif e.get("distance_meters"):
            e["display_name"] = f"{int(e['distance_meters'])}m"
        else:
            e["display_name"] = f"Event {e['event_id']}"
    events.sort(key=lambda e: (
        (e.get("division") or "").lower(),
        d if (d := eventDistance(e.get("event_short"),
                                 e.get("distance_meters"))) else 1e9,
        e["display_name"], e.get("gender") or "?"))

    # Sections shipped as separate event_ids render as identical rows
    # ("300m Hurdles / Boys / Open", twice). Number the duplicates so
    # each link has an identity; the scorer already merges them.
    tally = {}
    for e in events:
        k = (e["display_name"], e.get("gender"), e.get("division"))
        tally[k] = tally.get(k, 0) + 1
    seen = {}
    for e in events:
        k = (e["display_name"], e.get("gender"), e.get("division"))
        if tally[k] > 1:
            seen[k] = seen.get(k, 0) + 1
            e["dup_ix"] = seen[k]

    return render_template("meet_tf.html", header=header, events=events,
                           meet_date=meet_date, scored=scored,
                           alt_idx=alt_idx, other_sources=other_sources)


@app.route("/meet/tf/<int:meet_id>/compiled")
def compiled_tf(meet_id):
    """A TF meet's compiled results: computed team points on top, then
    every event as its own section with that event's individual scores --
    the XC meet-results reading order, for track."""
    from tf_points import scoreMeet

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sources = meet_sources(cur, "meets_tf", meet_id)
            src, alt_idx, other_sources = pick_source(
                sources, request.args.get("alt"))
            header = get_tf_meet_header(cur, meet_id, source=src)
            rows = get_tf_meet_scoring_rows(cur, meet_id, source=src)
            stamp_tf_meet_extras(cur, meet_id, rows)
            stamp_home_states(cur, rows)
            meet_date = get_meet_date(cur, "results_tf", meet_id, source=src)
            # Stamp BEFORE scoreMeet: it copies rows into its event
            # sections, so hs_rating and display_result must already be on
            # them. Per-row distance; the event string is the fallback.
            has_hs_view = stampRowsHs(cur, "TF", rows,
                                      distance_key="distance_meters",
                                      event_key="event_short")

    if header is None or not rows:
        abort(404)

    _stamp_tf_display(rows)
    scored = scoreMeet(rows)
    _tf_seed_points_cache(meet_id, src, rows, scored)

    return render_template("compiled_tf.html", header=header, scored=scored,
                           meet_date=meet_date, has_hs_view=has_hs_view,
                           alt_idx=alt_idx, other_sources=other_sources)


# ===================================================================== #
#  XC COURSE
# ===================================================================== #

def get_course_header(cur, course_name, dist=None):
    """Result/athlete counts for the header line, counted from the real
    rows and scoped to one distance when the page is.

    ! NOT course_difficulties' n_results. That table keeps one row per
      (course, distance) CELL, so the old LIMIT 1 read served one
      arbitrary cell's fit-sample counts as the whole course's, right
      above a distance table it visibly disagreed with."""
    cur.execute("""
        SELECT count(*)                    AS n_results,
               count(DISTINCT r.person_id) AS n_athletes
        FROM results r
        JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
        WHERE m.course_name = %(course)s
          AND (%(dist)s::int IS NULL OR round(m.distance)::int = %(dist)s)
    """, {"course": course_name, "dist": dist})
    row = cur.fetchone()
    return row if row and row["n_results"] else None


def get_course_rating_bests(cur, course_name, dist=None, limit=60):
    """Best speed ratings on this course: best per athlete, top `limit`
    per gender. dist=None is the overview (all distances, which rating
    makes comparable; each row carries its own); an int scopes to one.
    Replaces the old mixed-gender get_course_bests table."""
    dist_sql = "AND round(m.distance)::int = %(dist)s" if dist else ""
    cur.execute(f"""
        WITH rows AS (
            SELECT r.person_id, r.result_id, r.time_seconds, r.date,
                   r.grade, r.school, r.speed_rating,
                   round(m.distance)::int AS distance,
                   m.meet_id, m.div_id, a.gender, {_name_sql('r')} AS name,
                   row_number() OVER (PARTITION BY r.person_id
                                      ORDER BY r.speed_rating DESC) AS pr_rn
            FROM results r
            JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
            {_athlete_lateral('r')}
            WHERE m.course_name = %(course)s
              {dist_sql}
              AND m.distance IS NOT NULL
              AND r.person_id IS NOT NULL
              AND a.gender IN ('M', 'F')
              AND r.speed_rating IS NOT NULL
              AND r.time_seconds IS NOT NULL
              AND r.time_seconds BETWEEN m.distance * {_REC_PACE_LO}
                                     AND m.distance * {_REC_PACE_HI}
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY gender
                                         ORDER BY speed_rating DESC) AS rn
            FROM rows WHERE pr_rn = 1
        )
        SELECT * FROM ranked WHERE rn <= %(limit)s
        ORDER BY gender, speed_rating DESC
    """, {"course": course_name, "dist": dist, "limit": limit})
    return cur.fetchall()


def get_course_team_rating_bests(cur, course_name, limit=60):
    """Best team performances by RATING: top-5 average speed rating within
    one race, best race per school, per gender. The overview's twin of the
    time-based team records -- rating is what makes a 3200 squad and an
    8000 squad comparable on one list."""
    cur.execute(f"""
        WITH finishers AS (
            SELECT r.meet_id, r.div_id, r.source, r.school, r.speed_rating,
                   r.date, m.meet_name, round(m.distance)::int AS distance,
                   a.gender,
                   row_number() OVER (
                       PARTITION BY r.meet_id, r.div_id, r.source, r.school
                       ORDER BY r.speed_rating DESC) AS tn
            FROM results r
            JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
            {_athlete_lateral('r')}
            WHERE m.course_name = %(course)s
              AND m.distance IS NOT NULL
              AND NULLIF(TRIM(r.school), '') IS NOT NULL
              AND a.gender IN ('M', 'F')
              AND r.speed_rating IS NOT NULL
              AND r.time_seconds IS NOT NULL
              AND r.time_seconds BETWEEN m.distance * {_REC_PACE_LO}
                                     AND m.distance * {_REC_PACE_HI}
        ),
        teams AS (
            SELECT meet_id, div_id, school,
                   avg(speed_rating) FILTER (WHERE tn <= 5) AS avg5,
                   min(meet_name) AS meet_name,
                   min(date)      AS date,
                   min(distance)  AS distance,
                   mode() WITHIN GROUP (ORDER BY gender) AS gender
            FROM finishers
            GROUP BY meet_id, div_id, source, school
            HAVING count(*) >= 5
        ),
        best_per_school AS (
            SELECT *, row_number() OVER (PARTITION BY school, gender
                                         ORDER BY avg5 DESC) AS sn
            FROM teams
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY gender
                                         ORDER BY avg5 DESC) AS rn
            FROM best_per_school WHERE sn = 1
        )
        SELECT * FROM ranked WHERE rn <= %(limit)s
        ORDER BY gender, avg5 DESC
    """, {"course": course_name, "limit": limit})
    return cur.fetchall()


def get_course_meets(cur, course_name, dist=None, limit=200):
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
        -- dist picks WHICH meets appear (those that ran the selected
        -- distance) but not what a row says about them: the distances
        -- and result counts stay the whole meet's. A HAVING, not a
        -- WHERE, so the filter doesn't also throw away the other
        -- divisions' rows before the aggregates see them.
        HAVING %(dist)s::int IS NULL
            OR bool_or(round(m.distance)::int = %(dist)s)
        ORDER BY max(r.date) DESC
        LIMIT %(limit)s
    """, {"course": course_name, "dist": dist, "limit": limit})
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
    from school_identity import stateChips

    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        sport = "XC"

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header = schoolHeader(cur, school_name)
            if header is None:
                abort(404)

            # same name, two schools: state chips scope every table to
            # one home-state cluster (see school_identity.py)
            chips, primary_state = stateChips(cur, school_name)
            state = (request.args.get("state") or "").strip().upper() or None
            if state and not any(c["state"] == state for c in chips):
                state = None

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
            roster = (schoolRoster(cur, school_name, year, sport,
                                   state=state, primary=primary_state)
                      if year else [])
            meets  = schoolMeets(cur, school_name, sport, year=picked_stored,
                                 state=state, primary=primary_state)
            # deeper than the old 25: the tables reveal in place now, and
            # the cap is what "show more" runs out against
            best   = schoolBest(cur, school_name, sport, limit=100,
                                state=state, primary=primary_state)
            top    = schoolTopAthletes(cur, school_name, sport, limit=100,
                                       state=state, primary=primary_state)

    # HS-equivalent view: rows carry their pool straight from
    # ranking_results / athlete_season, so no lookup is needed.
    has_hs_view = stampBoardRows(best, rating_keys=("rating",), sport=sport)
    has_hs_view = stampBoardRows(top, rating_keys=("best",),
                                 sport=sport) or has_hs_view
    has_hs_view = stampBoardRows(roster, rating_keys=("mean_rating",
                                                      "best_rating"),
                                 sport=sport) or has_hs_view

    # ⚠ SAY WHY THE ROSTER IS MISSING. Years and rosters live in
    #   athlete_season, a rebuilt table that is empty mid-rebuild (and
    #   was once emptied by crash recovery). A page silently missing its
    #   roster reads as a broken overhaul; a page that says the season
    #   tables are rebuilding reads as what it is.
    season_rebuilding = not years and (roster == []) and bool(best or meets)

    # Modal pool per gender, for the view-all links: a rankings board is
    # pool-scoped, so a mixed table links Boys and Girls separately.
    pool_counts = {"M": {}, "F": {}}
    for row in list(best) + list(top) + list(roster):
        p = row.get("pool") or ""
        g = "M" if p.endswith("_m") else "F" if p.endswith("_f") else None
        if g:
            pool_counts[g][p] = pool_counts[g].get(p, 0) + 1
    pools = {g: (max(c, key=c.get) if c else
                 ("hs_m" if g == "M" else "hs_f"))
             for g, c in pool_counts.items()}

    return render_template("school.html", school=school_name, header=header,
                           state_chips=chips, state=state,
                           has_hs_view=has_hs_view,
                           years=years, year=seasonLabel(sport, year),
                           sport=sport, pools=pools,
                           season_rebuilding=season_rebuilding,
                           roster=roster, meets=meets, best=best, top=top,
                           picked=picked)


@app.route("/school/<path:school_name>/prs")
def school_prs_page(school_name):
    """School PRs: best mark per athlete, one section per distance/event.
    ?sport=XC|TF, ?year=<label> narrows to a season's bests, ?course=
    (XC) narrows to bests run there."""
    from school_prs import schoolPrData
    from school_identity import stateChips

    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        sport = "XC"
    raw_year = request.args.get("year")
    year = int(raw_year) if raw_year and raw_year.isdigit() else None
    course = (request.args.get("course") or "").strip() or None

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            chips, primary_state = stateChips(cur, school_name)
            state = (request.args.get("state") or "").strip().upper() or None
            if state and not any(c["state"] == state for c in chips):
                state = None
            data = schoolPrData(cur, school_name, sport,
                                year_label=year, course=course,
                                state=state, primary=primary_state)

    if not data["any"] and not year and not course:
        abort(404)

    # HS-equivalent view: running rows carry pool + sport + distance, the
    # exact trio stampBoardRows wants.
    has_hs_view = False
    for sec in data["sections"]:
        if sec["kind"] != "running":
            continue
        for g in ("M", "F"):
            has_hs_view = stampBoardRows(
                sec["tables"][g],
                rating_keys=("speed_rating",)) or has_hs_view

    for sec in data["sections"]:
        for g in ("M", "F"):
            for r in sec["tables"][g]:
                if sec["kind"] == "running":
                    r["display_mark"] = format_time(r["time_seconds"])
                elif sec["kind"] == "hurdles":
                    # the feed's own clock string when it has one --
                    # "15.24" should not re-round through float
                    r["display_mark"] = (r.get("mark") or
                                         format_time(r["time_seconds"]))
                else:
                    r["display_mark"] = r.get("mark") or "—"

    return render_template("school_prs.html", school=school_name,
                           sport=sport, data=data, state_chips=chips,
                           has_hs_view=has_hs_view)


@app.route("/debug/queries")
def debug_queries():
    """The site's own query log: slowest recent statements and the
    aggregate per statement, plain text. Local tooling, not a feature
    page -- the timing layer is db_timing.py."""
    from flask import Response
    rows, by_total = db_timing.summary()
    out = ["SLOWEST RECENT QUERIES (this process)", "=" * 76]
    for r in rows:
        out.append(f"{r['ms']:9.1f}ms  {r['path']}")
        out.append(f"           {r['sql']}")
    out += ["", "BY STATEMENT (count, total ms, max ms)", "=" * 76]
    for sql, a in by_total:
        out.append(f"n={a['n']:<5} total={a['total']:9.1f} "
                   f"max={a['max']:8.1f}  {sql}")
    out.append("")
    out.append(f"slow log (>= {db_timing.SLOW_MS}ms): racecast/slow_queries.log")
    return Response("\n".join(out), mimetype="text/plain")


# ⚠ RECORD LISTS ARE WHERE ONE TYPO TOPS A PAGE FOREVER, so both record
#   queries carry the engine's pace band (packResults' 0.12-0.72 s/m of the
#   raw distance): a 6:00 "5000m" or a doubled-time entry never reaches a
#   records table. The rating-ranked table below is unaffected.
_REC_PACE_LO, _REC_PACE_HI = 0.12, 0.72


def get_course_records(cur, course_name, dist, limit=60):
    """Fastest times at one distance on this course: best per athlete, top
    `limit` per gender. Gender from the same athlete lateral every page
    uses; rows without a linked person or a gender stay off the records
    (they remain in the rating table below)."""
    cur.execute(f"""
        WITH rows AS (
            SELECT r.person_id, r.result_id, r.time_seconds, r.date,
                   r.grade, r.school, r.speed_rating,
                   m.meet_id, m.div_id, m.meet_name,
                   a.gender, {_name_sql('r')} AS name,
                   row_number() OVER (PARTITION BY r.person_id
                                      ORDER BY r.time_seconds ASC) AS pr_rn
            FROM results r
            JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
            {_athlete_lateral('r')}
            WHERE m.course_name = %(course)s
              AND round(m.distance)::int = %(dist)s
              AND r.person_id IS NOT NULL
              AND a.gender IN ('M', 'F')
              AND r.time_seconds IS NOT NULL
              AND r.time_seconds BETWEEN %(dist)s * {_REC_PACE_LO}
                                     AND %(dist)s * {_REC_PACE_HI}
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY gender
                                         ORDER BY time_seconds ASC) AS rn
            FROM rows WHERE pr_rn = 1
        )
        SELECT * FROM ranked WHERE rn <= %(limit)s
        ORDER BY gender, time_seconds ASC
    """, {"course": course_name, "dist": int(dist), "limit": limit})
    return cur.fetchall()


def get_course_team_records(cur, course_name, dist, limit=60):
    """Fastest team performances at one distance: top-5 time total within
    ONE race, best race per school, top `limit` per gender. Ranked by the
    total; the average is displayed alongside for readability."""
    cur.execute(f"""
        WITH finishers AS (
            SELECT r.meet_id, r.div_id, r.source, r.school, r.time_seconds,
                   r.date, m.meet_name, a.gender,
                   row_number() OVER (
                       PARTITION BY r.meet_id, r.div_id, r.source, r.school
                       ORDER BY r.time_seconds ASC) AS tn
            FROM results r
            JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
            {_athlete_lateral('r')}
            WHERE m.course_name = %(course)s
              AND round(m.distance)::int = %(dist)s
              AND NULLIF(TRIM(r.school), '') IS NOT NULL
              AND a.gender IN ('M', 'F')
              AND r.time_seconds IS NOT NULL
              AND r.time_seconds BETWEEN %(dist)s * {_REC_PACE_LO}
                                     AND %(dist)s * {_REC_PACE_HI}
        ),
        teams AS (
            SELECT meet_id, div_id, school,
                   sum(time_seconds) FILTER (WHERE tn <= 5) AS total,
                   min(meet_name)  AS meet_name,
                   min(date)       AS date,
                   -- a division is one gender in practice; mode() settles
                   -- the stray mislabeled row rather than splitting a squad.
                   mode() WITHIN GROUP (ORDER BY gender) AS gender
            FROM finishers
            GROUP BY meet_id, div_id, source, school
            HAVING count(*) >= 5
        ),
        best_per_school AS (
            SELECT *, row_number() OVER (PARTITION BY school, gender
                                         ORDER BY total ASC) AS sn
            FROM teams
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY gender
                                         ORDER BY total ASC) AS rn
            FROM best_per_school WHERE sn = 1
        )
        SELECT * FROM ranked WHERE rn <= %(limit)s
        ORDER BY gender, total ASC
    """, {"course": course_name, "dist": int(dist), "limit": limit})
    return cur.fetchall()


def get_course_cell_difficulties(cur, course_name):
    """{rounded distance -> fitted difficulty} for this course's cells.

    Through course_canonical by NAME: same-named venues merge here exactly
    as they do everywhere else on this page (the 21-Woodward-Parks caveat);
    where they collide, the cell with the most results speaks."""
    try:
        cur.execute("""
            SELECT DISTINCT ON (cd.distance_m)
                   cd.distance_m, cd.difficulty
            FROM   course_canonical cc
            JOIN   course_difficulties cd ON cd.canonical_id = cc.canonical_id
            WHERE  cc.course_name = %(course)s
              AND  cd.difficulty IS NOT NULL
            ORDER  BY cd.distance_m, cd.n_results DESC NULLS LAST
        """, {"course": course_name})
        return {int(r["distance_m"]): float(r["difficulty"])
                for r in cur.fetchall()}
    except Exception:                    # noqa: BLE001
        cur.connection.rollback()
        return {}


# ★ A COURSE PAGE IS A HISTORY, AND HISTORY ONLY CHANGES AT THE PIPELINE.
#   Mt. SAC's records/bests queries are real work (window functions over
#   hundreds of thousands of rows -- 39s measured cold), so the finished
#   render context caches per (course, distance) like the TF points do.
_COURSE_CACHE = {}
_COURSE_TTL = 6 * 3600
_COURSE_MAX = 64


def loadCourseBoard(cur, course_name, picked):
    """The PRECOMPUTED render context from course_boards, or None.

    ★ COLD TIME IS THE PRODUCT. Warm caches only help the second viewer
      of the same course within the TTL, which on this site is rare; the
      builder (build_course_boards.py, pipeline step 12b) computes every
      course's page once per pipeline, so the FIRST viewer gets the same
      milliseconds. An unknown (course, dist) falls back to the overview
      row, matching the live route's dist handling; a course not built
      yet falls back to live compute."""
    try:
        cur.execute("""
            SELECT ctx FROM course_boards
            WHERE course_name = %(c)s AND dist = %(d)s
        """, {"c": course_name, "d": picked or 0})
        row = cur.fetchone()
        if row is None and picked:
            cur.execute("""
                SELECT ctx FROM course_boards
                WHERE course_name = %(c)s AND dist = 0
            """, {"c": course_name})
            row = cur.fetchone()
    except Exception:                    # noqa: BLE001 -- table not built yet
        cur.connection.rollback()
        return None
    if row is None:
        return None
    return row["ctx"] if isinstance(row, dict) else row[0]


def buildCourseCtx(cur, course_name, picked):
    """Everything course.html renders, computed live. The route uses it
    as the fallback; build_course_boards.py uses it as the builder --
    ONE code path, so the precomputed page can never drift from the live
    one. Returns None for a course with no results."""
    distances = get_course_distances(cur, course_name)

    # ★ THE SELECTED DISTANCE, None = the overview. Chips come from
    #   the distances the course actually raced, most-run first; a
    #   ?dist that matches nothing falls back to the overview rather
    #   than 404ing a shared link.
    dist_values = [int(round(float(d["distance"])))
                   for d in distances]
    sel_dist = picked if picked in dist_values else None

    # After sel_dist on purpose: the header counts follow the
    # selected distance, so the gray line describes what the
    # page below it is showing.
    header = get_course_header(cur, course_name, sel_dist)

    # Per-distance difficulty; the engine's cells are keyed on the
    # distance rounded to the nearest 100m, so look up the same way.
    cells = get_course_cell_difficulties(cur, course_name)

    def _cell(d):
        return (cells.get(int(round(d / 100.0) * 100))
                if d else None)

    # The header difficulty is the MOST-RUN distance's, said so.
    primary_dist = dist_values[0] if dist_values else None
    primary_difficulty = _cell(primary_dist)
    sel_difficulty = _cell(sel_dist)

    rating_bests = get_course_rating_bests(cur, course_name,
                                           sel_dist)
    if sel_dist:
        records = get_course_records(cur, course_name, sel_dist)
        team_records = get_course_team_records(cur, course_name,
                                               sel_dist)
        team_rating = []
    else:
        records, team_records = [], []
        team_rating = get_course_team_rating_bests(cur, course_name)
    meets = get_course_meets(cur, course_name, dist=sel_dist)

    # HS-equivalent view: per-row distance on the overview, one
    # distance when scoped.
    has_hs_view = stampRowsHs(cur, "XC", rating_bests,
                              distance_key="distance")
    has_hs_view = stampRowsHs(cur, "XC", records,
                              distance=sel_dist) or has_hs_view

    for row in records + rating_bests:
        row["display_time"] = format_time(row["time_seconds"])
    for row in team_records:
        row["display_total"] = format_time(row["total"])
        row["display_avg"] = format_time(float(row["total"]) / 5.0)

    def bySex(rows):
        return {"M": [r for r in rows if r["gender"] == "M"],
                "F": [r for r in rows if r["gender"] == "F"]}

    # The overview's distance table: every distance with its own fitted
    # difficulty, doubling as a second way into the scoped views.
    dist_table = [{"distance": int(round(float(d["distance"]))),
                   "difficulty": _cell(int(round(float(d["distance"])))),
                   "n_results": d["n_results"],
                   "first_date": d["first_date"],
                   "last_date": d["last_date"]}
                  for d in distances]
    sel_n = next((d["n_results"] for d in distances
                  if int(round(float(d["distance"]))) == sel_dist), None)

    return dict(has_hs_view=has_hs_view,
                course_name=course_name,
                header=header,
                dist_values=dist_values,
                dist_table=dist_table,
                sel_dist=sel_dist,
                pr_ok=(sel_dist in PR_DISTANCES) if sel_dist else False,
                sel_n=sel_n,
                sel_difficulty=sel_difficulty,
                primary_dist=primary_dist,
                primary_difficulty=primary_difficulty,
                records=bySex(records),
                team_records=bySex(team_records),
                rating_bests=bySex(rating_bests),
                team_rating=bySex(team_rating),
                meets=meets)


@app.route("/course/<course_name>")
def course(course_name):
    picked = request.args.get("dist", type=int)

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # precomputed page first (pipeline step 12b): the COLD path
            # is one PK lookup
            ctx = loadCourseBoard(cur, course_name, picked)
            if ctx is None:
                cache_key = (course_name, picked)
                hit = _COURSE_CACHE.get(cache_key)
                if hit and time.time() - hit[0] < _COURSE_TTL:
                    ctx = hit[1]
                else:
                    ctx = buildCourseCtx(cur, course_name, picked)
                    _COURSE_CACHE[cache_key] = (time.time(), ctx)
                    if len(_COURSE_CACHE) > _COURSE_MAX:
                        oldest = min(_COURSE_CACHE,
                                     key=lambda k: _COURSE_CACHE[k][0])
                        _COURSE_CACHE.pop(oldest, None)

    return render_template("course.html", **ctx)


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
            # HS-equivalent view: distance parsed from each row's event.
            has_hs_view = stampRowsHs(cur, "TF", bests,
                                      event_key="event_short")

    for row in bests:
        if row["is_field"]:
            row["display_result"] = row["mark"] if row["mark"] else "—"
        elif row["time_seconds"] is not None:
            row["display_result"] = format_time(row["time_seconds"])
        else:
            row["display_result"] = "—"

    return render_template("venue_tf.html",
                           has_hs_view=has_hs_view,
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

    terms = _searchTerms(raw)
    if terms is None:
        return jsonify([])
    where, params, word_score = terms

    kind = (request.args.get("kind") or "").strip()
    limit = min(int(request.args.get("limit") or 10), 40)
    params["lim"] = limit
    if kind:
        where.append("kind = %(kind)s")
        params["kind"] = kind

    order_tail = _searchOrder()

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
                          {order_tail}
                LIMIT  %(lim)s
            """, params)
            rows = cur.fetchall()

    return jsonify(rows)


def _searchTerms(raw, prefix="t"):
    """(where_clauses, params, word_score_sql) for a typed query, or None.

    ★ ONE MATCHER FOR THE DROPDOWN AND THE PAGE, BECAUSE THEY DRIFTED AND IT
      SHOWED. The dropdown ANDed per-token substrings; the page looked for the
      whole phrase contiguously. Same box, same words, two different result
      sets -- and since meets are stored with the year and edition number in
      front of the distinctive part, the page was the one that failed. Click a
      dropdown hit's "see all results" and you could land on an empty page.

    ⚠ AND THE PAGE'S COUNTS AND YEAR LIST DRIFTED AGAIN INSIDE THE PAGE. They
      were left on the old left-anchored predicate, referring to a `prefix`
      variable that had already been deleted, so /search raised NameError on
      every query. Three copies of one rule is three chances to fix two.

    Punctuation is a separator, not content: "mt. sac", "mt sac" and
    "arcadia-loup" all tokenise the same. Six tokens is the cap -- past that
    the AND is narrow enough that more only costs time.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", (raw or "").lower()) if t][:6]
    if not tokens:
        return None

    # ⚠ search_text ALONE. THE `OR search_last` WAS DEAD CODE THAT COST A
    #   SEQUENTIAL SCAN OF 16 MILLION ROWS PER TOKEN.
    #
    #   search_index has a GIN trigram index on search_text, which is what
    #   serves LIKE '%tok%'. search_last has only a btree text_pattern_ops
    #   index, which serves LEFT-ANCHORED patterns and nothing else -- so the
    #   second half of the OR was unindexable, and an OR is only as fast as
    #   its worst branch. Measured on "arcadia": Parallel Seq Scan, 5,428,976
    #   rows removed per worker, 385,773 buffers read, 1.35 s for the tab
    #   counts alone.
    #
    # ★ AND IT COULD NEVER HAVE MATCHED ANYTHING EXTRA. Every loader in
    #   search_index.py puts search_last inside search_text already: athletes
    #   get the last name, which is a token of "name school"; schools,
    #   courses, meets and venues set the two columns to the identical string.
    #   So `search_last LIKE '%tok%'` implies `search_text LIKE '%tok%'`.
    #
    #   It earned its place under the OLD left-anchored match, where "smith"
    #   could not prefix-match "john smith northgate" but could prefix-match
    #   search_last. The move to substring matching made it redundant, and
    #   nobody removed it.
    where, params = [], {}
    for i, tok in enumerate(tokens):
        k = f"{prefix}{i}"
        where.append(f"search_text LIKE %({k})s")
        params[k] = f"%{tok}%"
        params[f"{k}_w"] = f"% {tok}%"
        params[f"{k}_s"] = f"{tok}%"
    params[f"{prefix}_first"] = tokens[0] + "%"
    # the typed words as one contiguous phrase, anchored at the start --
    # for a non-meet row that is "the NAME begins with what was typed",
    # since every loader puts the name first. See _ORDER_TAIL for why
    # this key exists and why meets are exempt from it.
    params[f"{prefix}_phrase"] = " ".join(tokens) + "%"

    # How many tokens land on a word boundary. A row matching every token at a
    # word start is a better hit than one matching them mid-word -- it is what
    # keeps "arcadia" from surfacing "Allen East JH Tri--Arcadia, McComb"
    # above "Arcadia".
    word_score = " + ".join(
        f"(CASE WHEN search_text LIKE %({prefix}{i}_w)s OR search_text "
        f"LIKE %({prefix}{i}_s)s THEN 1 ELSE 0 END)"
        for i in range(len(tokens)))
    return where, params, word_score



# ★ ONE ORDER FOR BOTH SURFACES, AND KIND-AWARE PER ROW RATHER THAN PER
#   REQUEST. The two populations answer different questions:
#
#     meet   -> RECENCY. Thirty editions of one name, and the recent one is
#               nearly always the one meant.
#     others -> HOW MUCH THEY RACED. sort_count carries races for an athlete,
#               athletes for a school, results for a course or venue; someone
#               searching a name wants the runner with a career, not a
#               namesake with a single result.
#
# ⚠ IT USED TO BRANCH ON ?kind=, AND THAT MADE IT UNREACHABLE FROM THE SEARCH
#   BOX. topbar-search.js calls /search/api with no kind at all, so `kind`
#   was "" and every meet fell through to the count-first branch -- exactly
#   the "a short 2010 name beats a longer 2025 one" bug the comment above
#   claims to have fixed. It was only ever fixed for the pickers in
#   predictions.js and rankings.js, which pass kind explicitly.
#
#   A CASE on the row's own kind cannot be bypassed by the caller, and it is
#   also the only thing that can order a MIXED list correctly: the "All" tab
#   and the dropdown both hold meets and athletes at once, and no
#   per-request choice can rank both of those the way each wants.
#
# ! WORD MATCHES STILL COME FIRST, ABOVE THIS. The order of the keys is the
#   whole ranking, and word_score leads it -- meets are stored with the year
#   and edition number in front, so "starts with" is a weak signal here and
#   "every token on a word boundary" is a strong one.
# ⚠ NAME MATCHES BEAT NAME+SCHOOL COINCIDENCES, and this key is why.
#   Athlete search_text is "name school", and the tokens match order-free
#   anywhere in it -- so "jackson spencer" is equally satisfied by the
#   runner Jackson Spencer, by a Spencer Jackson, and by any kid named
#   Jackson AT a Spencer school. The career-size tiebreak below then
#   crowns whichever coincidence raced most: type "jackson spence" and a
#   big-career Jackson Spence tops the list looking exactly right, add
#   the r and he vanishes, promoting some Spencer-school stranger. The
#   phrase key ranks rows whose text STARTS with the typed words, in
#   order, above every scattered match -- the person actually named what
#   was typed wins.
#
#   Meets are exempt: their year sits in front of the name ("2026
#   arcadia invitational"), so a phrase-prefix boost would resurrect the
#   "a 2009 meet named without a year outranks every recent edition"
#   bug this ranking already fixed once.
_ORDER_TAIL = """
          (CASE WHEN kind <> 'meet'
                AND search_text LIKE %({p}_phrase)s THEN 1 ELSE 0 END)
              DESC,
          (CASE WHEN kind = 'meet' THEN sort_year  ELSE sort_count END)
              DESC NULLS LAST,
          (CASE WHEN kind = 'meet' THEN sort_count ELSE sort_year  END)
              DESC NULLS LAST,
          (search_text LIKE %({p}_first)s) DESC,
          length(search_text)
"""


def _searchOrder(prefix="t"):
    """The ranking below word_score. Both surfaces use this, unmodified."""
    return _ORDER_TAIL.format(p=prefix)


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
    terms = _searchTerms(needle)
    if terms is None:
        return [], {}, []
    where, params, word_score = terms
    order_tail = _searchOrder()
    # ! CAPTURED BEFORE kind AND year ARE APPENDED. The tab counts must ignore
    #   the kind filter (that is what makes the tabs switchable) and the year
    #   list must ignore the year filter (or picking a year collapses the
    #   dropdown to just that year).
    where_base = list(where)

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
                -- ★ THE SAME RANKING THE DROPDOWN USES, from the same
                --   _searchOrder. A result that is first in the dropdown has
                --   to be first here, or clicking through reshuffles the list
                --   under the cursor and the page looks broken even when it
                --   is not. It also fixes the page's own key, which sorted
                --   EVERYTHING by year -- right for meets, wrong for athletes
                --   and schools, where the question is how much they raced.
                ORDER  BY ({word_score}) DESC,
                          {order_tail}
                LIMIT  %(lim)s OFFSET %(off)s
            """, {**params, "lim": PAGE_SIZE, "off": offset})
            results = cur.fetchall()

            # per-kind counts for the tabs (ignore the kind filter for counts)
            #
            # ⚠ THE SAME PREDICATE AS THE RESULTS. These two queries were left
            #   on the old left-anchored match when the results moved to
            #   substring, referring to a `prefix` variable that had gone away
            #   with it -- so /search raised NameError on every query, while
            #   the dropdown, which never comes through here, kept working.
            #
            #   Fixing only the name would have been worse than the crash: the
            #   tabs would count PREFIX hits beside SUBSTRING results, so a
            #   query could list thirty meets under a tab reading 0. A count
            #   that disagrees with the list under it is a bug nobody reports
            #   and everybody distrusts.
            #
            # ★ AND IT IS COUNTED WITH A CEILING. An exact COUNT(*) has to
            #   touch every matching row -- the results query stops at 30, this
            #   one did not, so on a common token the tabs cost more than the
            #   results they label. Nobody reads a four-digit tab count; they
            #   read "lots". One capped scan per kind answers the only question
            #   the tabs actually ask, which is whether it is worth clicking.
            count_where = list(where_base)
            cparams = dict(params)
            cparams["cap"] = SEARCH_COUNT_CAP + 1
            if year:
                count_where.append("(sort_year = %(y)s OR search_text LIKE %(yp)s)")
                cparams["y"] = int(year); cparams["yp"] = f"%{year}%"
            kinds_sql = ", ".join(f"('{k}')" for k in SEARCH_KINDS)
            cur.execute(f"""
                SELECT k.kind, c.n
                FROM   (VALUES {kinds_sql}) AS k(kind)
                CROSS  JOIN LATERAL (
                    SELECT count(*) AS n FROM (
                        SELECT 1 FROM search_index s
                        WHERE  s.kind = k.kind
                          AND  {' AND '.join(count_where)}
                        LIMIT  %(cap)s
                    ) t
                ) c
            """, cparams)
            counts = {r["kind"]: r["n"] for r in cur.fetchall() if r["n"]}

            # distinct years -- from q+kind ONLY, never the year filter itself,
            # or picking a year collapses the dropdown to just that year.
            yr_where = list(where_base)
            yparams = dict(params)
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

# ! THE TAB COUNTS STOP HERE AND SAY "1000+". See the note in _run_search: an
#   exact count has to walk the whole match set, and the tabs are a
#   worth-clicking signal, not a statistic.
SEARCH_COUNT_CAP = 1000

# The tab order on the results page, and the kinds the counts are taken over.
SEARCH_KINDS = ("athlete", "meet", "course", "venue", "school")

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
                           page_size=PAGE_SIZE,
                           count_cap=SEARCH_COUNT_CAP)

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

    # ★ DEEP LINK FROM AN ATHLETE PAGE. /conversions?athlete=123 arrives with
    #   the source already chosen, so the handoff is one click rather than a
    #   name typed twice.
    prefill = request.args.get("athlete", type=int)
    return render_template("conversions.html",
                           prefill_athlete=prefill,
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
                      getAbilityRankings, rankOf, PR_DISTANCES)


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

    # HS-equivalent view: rows carry their own pool (and, for single-race
    # boards, distance). Display-only -- the ORDER stays the server's, so on
    # a pool=all board the hs column can read unsorted; rankings.js says so.
    hs_movable = stampBoardRows(rows, rating_keys=("rating", "best_rating"))

    return jsonify({"filters": f, "count": len(rows),
                    # ! national_bias IS ABOUT THE RATING SCALE, so it does not
                    #   apply to a board of raw times. The per-state offset is
                    #   in speed_rating; a clock has no such thing.
                    "national_bias": (f["state"] is None
                                      and f["board"] != "pr"),
                    "hs_movable": hs_movable,
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

    # \u2605 A COURSE CHANGES WHAT THE BOARD IS -- single races at one venue, not
    #   season squads. Served here, before the season machinery, because none
    #   of serveBoard's questions (raced? which span?) exist for it.
    if f.get("course"):
        with getConn() as conn:
            with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                try:
                    rows = getTeamCoursePerformances(cur, f)
                except psycopg2.errors.UndefinedTable:
                    conn.rollback()
                    return jsonify({"error": "Course team rankings need "
                                             "ranking_results: run "
                                             "racecast/build_ranking_results"
                                             ".py."}), 400
                except Exception:
                    conn.rollback()
                    app.logger.exception("/api/teams (course) failed")
                    return jsonify({"error": "Team rankings failed to load. "
                                             "The server log has the "
                                             "traceback."}), 500
        # Rows carry their race distance, so the exact factor applies
        # rather than the representative one.
        hs_movable = stampBoardRows(rows, rating_keys=("top5_mean",),
                                    pool=f.get("pool"), sport="XC")
        return jsonify({"filters": f, "count": len(rows),
                        "course_mode": True,
                        # One venue: everyone was measured on the same
                        # ground, so the cross-state caveat has no work here.
                        "national_bias": False,
                        "hs_movable": hs_movable,
                        "rows": rows})

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
    # HS-equivalent view: a team board is one pool throughout; the top-5
    # average and the fifth man scale by the same factor as any member.
    hs_movable = stampBoardRows(rows, rating_keys=("top5_mean",
                                                   "fifth_rating"),
                                pool=f.get("pool"), sport=f.get("sport"))

    return jsonify({"filters": f, "count": len(rows),
                    "board_scope": f["board_scope"],
                    "national_bias": f["board_scope"] == "usa",
                    "hs_movable": hs_movable,
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
    if mode not in ("meet", "rerun", "rerun_exact", "manual"):
        return None, "mode must be meet, rerun, rerun_exact or manual"

    t = {"mode": mode}
    if mode in ("meet", "rerun", "rerun_exact"):
        raw = (args.get("meet_id") or "").strip()
        if not raw.isdigit():
            return None, "meet_id is required for that mode"
        t["meet_id"] = int(raw)
        t["div_id"] = args.get("div_id")
        t["sport"] = args.get("sport") or "XC"
        if mode == "rerun":
            # The date the re-run is FOR: the page sends its editable
            # date (same month and day this year by default). A bare
            # year still works and shifts the original date.
            t["date"] = (args.get("date") or "").strip() or None
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

    when = (request.args.get("when") or "thisyear").strip()
    if when not in ("thisyear", "asran"):
        when = "thisyear"

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            out = meetField(cur, int(meet), int(div) if div.isdigit() else None,
                            sport, when=when)
    return jsonify(out)


@app.route("/api/predict/races")
def api_predict_races():
    """The races inside a meet, so the page can predict ONE of them.

    An XC championship is several races sharing one meet_id (Boys D1,
    Girls D2...); predicting the whole meet mixes fields that never
    raced each other. Works without the model -- a database question.
    """
    meet = (request.args.get("meet_id") or "").strip()
    if not meet.isdigit():
        return jsonify({"error": "meet_id is required."}), 400
    sport = (request.args.get("sport") or "XC").strip().upper()

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if sport == "XC":
                rows = get_meet_divisions(cur, int(meet))
                races = [{"div_id": r["div_id"],
                          "label": r.get("division") or f"Race {r['div_id']}",
                          "distance": r.get("distance"),
                          "gender": r.get("gender"),
                          "n_results": r["n_results"]} for r in rows]
            else:
                cur.execute("""
                    SELECT r.div_id, m.division,
                           count(*) AS n_results
                    FROM   results_tf r
                    LEFT JOIN meets_tf m ON m.meet_id = r.meet_id
                                        AND m.div_id  = r.div_id
                    WHERE  r.meet_id = %(meet)s
                    GROUP  BY r.div_id, m.division
                    ORDER  BY m.division NULLS LAST, r.div_id
                """, {"meet": int(meet)})
                races = [{"div_id": r["div_id"],
                          "label": r.get("division") or f"Division {r['div_id']}",
                          "distance": None, "gender": None,
                          "n_results": r["n_results"]} for r in cur.fetchall()]
    return jsonify({"races": races})


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