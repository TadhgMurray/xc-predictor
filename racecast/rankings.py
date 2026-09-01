"""
rankings.py -- query builders for /api/rankings.

Lives beside app.py. app.py keeps the route (all endpoints live there); this
file holds the SQL so the route stays about ten lines.

TWO BOARDS, TWO SOURCES, TWO MEANINGS

  performance   single races, from ranking_results.
                "What were the best races?" Carries full per-race noise --
                measured ~3.3%, about 4 rating points -- so a great race by an
                ordinary athlete outranks an ordinary race by a great one.
                Correct for a performance board, WRONG as a ranking of athletes.

  ability       season averages, from athlete_season.
                "Who is best?" Averaging kills the noise. This is the default.

KNOWN BIAS -- national boards carry an unresolved per-state rating offset of
roughly 9.5 points (XC: CA +3.5, TX -4.0; TF similar). Cross-state comparison
is wrong by up to ~9 points. STATE-FILTERED BOARDS ARE UNAFFECTED, because a
constant shift cancels within a state. Until it is fixed, prefer state boards
and label national ones. api_rankings returns national_bias for exactly this.
"""

# ------------------------------------------------------------------ #
#  SORTING
# ------------------------------------------------------------------ #
#
# ★ A WHITELIST, NOT AN INTERPOLATION. ORDER BY cannot be a bind parameter, so
#   the caller names a KEY and the expression is a constant here. Nothing the
#   caller types ever reaches the SQL text. Adding a sort option means adding a
#   line to one of these dicts.
#
# Each entry is (expression, default direction). The default direction is per
# column because "best" means different things: the best RATING is the highest,
# the best FINISH is the lowest, and the most recent DATE is the latest.

_SORTS_ABILITY = {
    "rating":  ("s.mean_rating",    "DESC"),
    "best":    ("s.best_rating",    "DESC"),
    "races":   ("s.n_races",        "DESC"),
    # ! THE LABEL, so the column sorts the way it reads. See _YEAR_LABEL.
    "year":    ("(CASE WHEN s.sport = 'TF' THEN s.year + 1 ELSE s.year END)", "DESC"),
    "name":    ("a.name",           "ASC"),
    "school":  ("s.school",         "ASC"),
    "state":   ("s.state",          "ASC"),
    "grade":   ("s.grade",          "ASC"),
    "first":   ("s.first_race",     "ASC"),
    "last":    ("s.last_race",      "DESC"),
}

_SORTS_PERFORMANCE = {
    "rating":  ("p.speed_rating",   "DESC"),
    "date":    ("p.race_date",      "DESC"),
    "time":    ("p.time_seconds",   "ASC"),
    "year":    ("(CASE WHEN p.sport = 'TF' THEN p.year + 1 ELSE p.year END)", "DESC"),
    "name":    ("a.name",           "ASC"),
    "school":  ("p.school",         "ASC"),
    "state":   ("p.state",          "ASC"),
    "grade":   ("p.grade",          "ASC"),
}

# ★ THE PR BOARD RANKS A CLOCK, NOT A RATING, AND THAT IS THE POINT OF IT.
#
#   Both other boards rank speed_rating, which is difficulty-adjusted and
#   distance-normalised -- exactly what makes cross-course comparison fair,
#   and exactly what makes it unrecognisable to somebody who wants to know who
#   ran the fastest 5k. This board answers that question instead: raw
#   time_seconds, one distance at a time.
#
# ⚠ SO IT IS NOT A FAIR RANKING, AND MUST NOT BE READ AS ONE. A 5k PR at a
#   flat course beats the same effort on a hill, and no correction is applied
#   because applying one would stop it being a PR. The rating boards are where
#   "who is best" is answered; this is where "what are the fast times" is.
_SORTS_PR = {
    "time":    ("p.time_seconds",  "ASC"),
    "date":    ("p.race_date",     "DESC"),
    "rating":  ("p.speed_rating",  "DESC"),
    "year":    ("(CASE WHEN p.sport = 'TF' THEN p.year + 1 ELSE p.year END)", "DESC"),
    "name":    ("a.name",          "ASC"),
    "school":  ("p.school",        "ASC"),
    "state":   ("p.state",         "ASC"),
    "grade":   ("p.grade",         "ASC"),
}

# Filters that accept several values at once. Within a field the values are
# OR-ed (state CA or TX); across fields they are AND-ed (a CA athlete in grade
# 12). That is what a reader expects from a set of filter chips.
_MULTI = ("state", "school", "grade", "year")

# How many values one filter may carry. Not a safety limit -- the values are
# bound, not interpolated -- but a very long array turns an index scan into a
# sequential one, and fifty states is already the whole country.
MAX_MULTI = 60


# ★ SCOPE IS A PROXY, AND IT SAYS SO. There is no nation column anywhere in
#   ranking_results or athlete_season -- state is the only geography stored.
#   But a US meet carries one of the fifty state codes, so an explicit list
#   separates them without inventing a column.
#
# ⚠ NOT `state IS NOT NULL`. A foreign meet can carry a state code too -- a
#   Canadian one reads QC, ON, BC -- so a null test keeps exactly the athletes
#   this is meant to exclude. The membership test is the only version that
#   works.
#
#   'usa' is the DEFAULT because the alternative silently mixes scales. The
#   ratings are built from a linkage graph that is overwhelmingly American;
#   a Québec programme year or a Kenyan collegiate entry rides on whatever
#   thin connection its meet happens to have, and lands next to a US athlete
#   as though the two were measured the same way. Showing that by default
#   presents a comparison the data does not support.
#
# ⚠ AND IT IS STILL A PROXY. A US result whose meet never got a state is
#   excluded. The undercount is the safer error: an athlete missing from a
#   board is visible to them, while a wrongly-included one distorts
#   everybody's rank.
SCOPES = {"usa", "all"}

# The fifty states plus DC. Territories are deliberately absent: PR, GU, VI
# and AS run their own calendars with almost no cross-linking to the mainland,
# which is the same disconnection this filter exists to keep off the board.
US_STATES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
)

BOARDS = {"performance", "ability", "pr"}
SPORTS = {"XC", "TF", "both"}
POOLS = {"hs_m", "hs_f", "ms_m", "ms_f", "college_m", "college_f"}

# ★ THE STORED YEAR IS ACADEMIC; THE DISPLAYED ONE IS NOT.
#
#   build_ranking_results writes year = seasonYearFromIso, which is the
#   academic year -- August through July, named for the year it opens in. For
#   XC that is what everyone calls the season: autumn 2025 IS the 2025 season.
#
#   For track it is a year behind. The 2026 indoor and outdoor seasons run
#   from December 2025 to July 2026, all of which is academic 2025, and no
#   athlete calls that their 2025 season.
#
# ! SO THE LABEL IS year + 1 FOR TF AND year FOR XC, and the filter accepts
#   the LABEL rather than the stored value -- see _yearClause. Storing one
#   thing and showing another is only safe when the filter agrees with what is
#   shown.
_YEAR_LABEL = "(CASE WHEN sport = 'TF' THEN year + 1 ELSE year END)"


# ! 'all' IS A PR-BOARD POOL AND ONLY A PR-BOARD POOL. A rating board mixing
#   middle schoolers with collegians is meaningless -- speed_rating is
#   relative to a pool's own 100 point, so the numbers are not on one scale.
#   A TIME is on one scale by definition, so "the fastest 5000s, anybody" is a
#   real question and this is the board that can answer it.
PR_POOLS = POOLS | {"all"}

# ★ THE UNITS SPLIT BY LEVEL, AND THE TWO SETS DO NOT OVERLAP.
#
#   A high school has no conference and a college has no league, so a filter
#   bar offering all of them at once offers several that can only ever return
#   nothing for whoever is looking.
#
# ★ AND THE ORDER IS THE HIERARCHY, BIGGEST FIRST (owner, 2026-08-30).
#   College: division, then region, then conference. High school: the state's
#   division, then the section, then that section's division, then the
#   league -- the smallest grouping, and last.
#
# ! division AND class ARE ONE FILTER. The corpus writes a state's tier as
#   either -- "D2" in one state, "Class AA" in another -- and nobody looking
#   for one means to exclude the other. One box searches both columns.
#
# ⚠ county AND district ARE GONE. They parsed out of meet and division names
#   (see check_school_units' _NOT_A_COUNTY), they are not a unit anyone
#   competes in, and they were the two boxes on the bar that answered no
#   question. The COLUMNS stay in school_unit; only the filters go.
COLLEGE_UNITS = ("division", "region", "conference")
HS_UNITS = ("state_div", "section", "section_div", "league")

UNIT_FILTERS = COLLEGE_UNITS + HS_UNITS

# One filter key -> the school_unit columns it searches.
UNIT_COLUMNS = {
    "division":    ("division",),
    "region":      ("region",),
    "conference":  ("conference",),
    "state_div":   ("state_div", "class"),
    "section":     ("section",),
    "section_div": ("section_div",),
    "league":      ("league",),
}

_UPPER_UNITS = {"division", "region", "state_div", "section", "section_div",
                "class"}

# The distances a PR board will accept, in metres. A whitelist rather than a
# free number because a board of "best 4,987 m times" is a data-entry artefact
# with a leaderboard attached.
#
# ⚠ EXACT MATCH, NOT A TOLERANCE. 1600 and 1609 are different races to the
#   people who run them, and merging them would put a mile PR on a 1600 board.
#   ranking_results stores what the meet recorded, corrected by dist_override.
PR_DISTANCES = (
    # Sprints and middle distance, all track.
    55, 60, 70, 100, 110, 200, 300, 400, 500, 600,
    800, 1000, 1500, 1600, 1609, 2000, 3000, 3200, 3218,
    # ! AND THE IMPERIAL XC DISTANCES, which a metric list quietly omits.
    #   Cross country is run in miles across much of the country: 1.5 miles is
    #   2414 m -- the whole elementary calendar -- and 2.5, 3, 4 and 5 miles
    #   are 4023, 4828, 6437 and 8047.
    2414, 4000, 4023, 4828, 5000, 6000, 6437, 8000, 8047, 10000)

# ★ A BAND, NOT AN EQUALITY, BECAUSE THE STORED DISTANCE IS NOT ROUND.
#
#   ranking_results.distance is whatever the meet recorded, so a five-mile
#   race appears as 8047 and as 8046.72, and one 5000 in the corpus is stored
#   as 4988.9663. Exact matching puts those on no board at all -- and it is
#   the fast, well-attended meets that tend to record a surveyed distance.
#
# ⚠ AND 0.25% IS CHOSEN, NOT ROUNDED TO. The metric and imperial pairs sit
#   0.56% apart -- 1600/1609, 3200/3218, 4000/4023, 8000/8047 -- so a band
#   wide enough to be generous starts overlapping its neighbour. At 0.5% the
#   windows are [1592, 1608] and [1601, 1617]: a race recorded as 1604 shows
#   up on BOTH boards. At 0.25% they are [1596, 1604] and [1605, 1613],
#   disjoint, and the worst real stray -- 4988.9663, 0.221% off 5000 -- is
#   still caught.
#
#   A mile and a 1600 are different races to the people who run them, and
#   merging them is the one mistake this board cannot make.
PR_DISTANCE_TOL = 0.0025

# ★ THE DID-NOT-FINISH SENTINEL. time_seconds is a numeric column, so a DNS or
#   DNF still needs a number in it, and the scrapers write 999999. Measured:
#   270,166 rows carry exactly that value, against ~17,000 for every real time
#   -- sixteen times the next commonest, and perfectly round.
#
# ⚠ THE RATING BOARDS NEVER SEE THESE, because the pace band drops them long
#   before a rating exists. The PR board ranks the CLOCK, so nothing upstream
#   has excluded them -- and sorted ascending they sit at the bottom rather
#   than the top, which is exactly how a bug like this survives review.
#
# ! THE TEST IS A DAY, NOT THE VALUE. 999999 lands in a `real` column, and a
#   feed that rounds or scales it differently would slip past an equality
#   check with no symptom. No race is a day long, so anything past 86400 is
#   no time -- the same threshold rankings.js uses, for the same reason.
DNF_SENTINEL = 86400

# ⚠ FIELD EVENTS ARE NOT HERE, AND NOT BY OVERSIGHT. results_tf carries them
#   with is_field = 1 and a `mark` column that is TEXT -- and the commonest
#   values in it are NH, ND, DNS, SCR and FOUL rather than measurements. The
#   real ones read '4-06.00': feet and inches, where 5-00.00 outranks 4-10.00
#   and sorts below it as text.
#
#   A marks board therefore needs a parser, a unit decision, a normaliser for
#   the two spellings the corpus uses ('shot' and "Women's Shot Put" are the
#   same event), and a sanity floor. Every one of those is a place to put a
#   wrong number at the top of a national list, so it wants building
#   deliberately rather than alongside something else.

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
# ★ NO PER-ATHLETE CAP. This board ranks RACES, and a cap makes it rank races
#   subject to a quota -- the third-best race in the country absent while the
#   twentieth is shown. That is a different list from the one the heading
#   promises, and nobody is told.
#
#   The cap came from panels.py, where 25 home-page slots and one athlete
#   holding twelve of them looks broken. A page with filters and paging has
#   no such problem: somebody who asks for the top fifty races should get the
#   top fifty races.
#
# ! AND REMOVING IT IS WHAT MAKES THE BOARD RANKABLE. With a cap, an athlete's
#   position depends on a window function over everyone above them, so a rank
#   could only be computed by sorting the whole filtered set. Uncapped, it is
#   COUNT(*) WHERE speed_rating > theirs over the deduped rows -- exact, and
#   one index scan.

# Rows pulled before thinning. Dedup and the per-athlete cap both remove rows,
# so the candidate set must exceed the requested page by enough that thinning
# cannot leave it short. Same reasoning as panels.py's COLLECT_N=250 for
# TOP_N=25, scaled to paging.
CANDIDATE_FACTOR = 40


# ------------------------------------------------------------------ #
#  1. THE ATHLETE NAME LOOKUP
# ------------------------------------------------------------------ #

# A LOCAL COPY of app.py's _athlete_lateral, and here is why it is not imported:
# that one keys on COALESCE(r.person_id, r.athlete_id), but neither
# ranking_results nor athlete_season has an athlete_id column -- person_id is
# NOT NULL in both, so the COALESCE has nothing to fall back to and the query
# would not compile.
#
# The ORDER BY is copied verbatim and MUST stay in sync. `athletes` holds
# roughly one row per (person, school), so a prolific athlete has several --
# Sophia Carcamo has five, four named and one blank. A bare LIMIT 1 returns an
# arbitrary one, so her name rendered blank on some pages and fine on others.
# Rows that HAVE a name sort first, then rows with a usable gender. Booleans
# sort false < true, hence DESC.
#
# The gender test is in the SORT, not the WHERE: filtering would discard a
# named-but-genderless row entirely, throwing away the name to save a gender.
_NAME_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT NULLIF(TRIM(concat_ws(' ', a.first_name, a.last_name)), '') AS name
        FROM   athletes a
        WHERE  a.athlete_id = {alias}.person_id
        ORDER  BY (COALESCE(TRIM(a.first_name), '') <> ''
                OR COALESCE(TRIM(a.last_name),  '') <> '') DESC,
                  (a.gender IN ('M', 'F')) DESC
        LIMIT  1
    ) a ON TRUE
"""


def nameLateral(alias):
    """The athletes lookup, joined onto a rankings row aliased `alias`."""
    return _NAME_LATERAL.format(alias=alias)


# ------------------------------------------------------------------ #
#  2. REQUEST PARSING
# ------------------------------------------------------------------ #

def _boundedInt(args, name, default, lo, hi):
    """Bounded int from the query string; default when absent or unparseable."""
    raw = args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _multiValue(args, name, upper=False):
    """One filter, one or many values. "CA,TX" -> ["CA", "TX"]; absent -> None.

    Returns None rather than [] for an absent filter, because _whereClauses
    distinguishes "no filter" (add no clause at all, so the index is usable)
    from "a filter that matches nothing".

    Values are stripped and de-duplicated with the order kept, so a chip added
    twice does not widen the array or change the plan.
    """
    raw = args.get(name)
    if not raw:
        return None
    seen, out = set(), []
    for piece in raw.split(","):
        v = piece.strip()
        if upper:
            v = v.upper()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out[:MAX_MULTI] or None


def _multiInt(args, name, lo, hi):
    """As _multiValue, but ints, and silently dropping anything unparseable.

    Dropping beats erroring here: a year list arrives from checkboxes the page
    generated itself, so a bad element means a bug on our side, and failing the
    whole request would hide the good values with it.
    """
    values = _multiValue(args, name)
    if not values:
        return None
    out = []
    for v in values:
        try:
            n = int(v)
        except ValueError:
            continue
        if lo <= n <= hi:
            out.append(n)
    return out or None


def parseFilters(args):
    """request.args -> (filters dict, error string or None).

    Returns the error rather than raising so the route can send a 400 with a
    message. Silently ignoring a bad filter is worse: the user sees a board and
    has no idea it is not the one they asked for.
    """
    board = args.get("board", "ability")
    if board not in BOARDS:
        return None, f"board must be one of {sorted(BOARDS)}"

    sport = args.get("sport", "both")
    if sport not in SPORTS:
        return None, f"sport must be one of {sorted(SPORTS)}"

    # ! THE VALID POOL SET DEPENDS ON THE BOARD. 'all' is meaningful only
    #   where the ranked quantity is a time; see PR_POOLS.
    allowed = PR_POOLS if board == "pr" else POOLS
    pool = args.get("pool", "hs_m")
    if pool not in allowed:
        return None, f"pool must be one of {sorted(allowed)}"

    # ★ GENDER IS ONLY A SUFFIX ON THE POOL, WHICH IS WHY 'all' MIXES IT.
    #
    #   Every pool name carries its gender -- hs_m, college_f -- so filtering
    #   by pool has always filtered by gender as a side effect, and nothing
    #   ever needed a gender axis. Then the Best times board added 'all',
    #   _whereClauses correctly adds no pool clause for it, and the only
    #   thing constraining gender vanished with it. A board of the fastest
    #   5000s then ranks men and women together, which is not a board.
    #
    # ! SO 'all' MEANS ALL LEVELS, NOT ALL PEOPLE. gender=m|f narrows to the
    #   pools ending in that suffix; omitted, behaviour is exactly as before,
    #   so no existing link changes meaning.
    #
    # ⚠ AND IT IS REFUSED WHERE IT WOULD LIE. On a gendered pool the suffix
    #   already decides, and accepting a contradicting gender ("pool=hs_m&
    #   gender=f") would return an empty board with no explanation. Say so.
    gender = (args.get("gender") or "").strip().lower() or None
    if gender is not None:
        if gender not in ("m", "f"):
            return None, "gender must be m or f"
        if pool != "all" and not pool.endswith(f"_{gender}"):
            return None, (f"pool {pool} is already {pool.rsplit('_', 1)[1]}; "
                          f"drop the gender filter or change the pool")

    distance = None
    if board == "pr":
        # ⚠ THE COLUMN IS NEWER THAN THE TABLE. `distance` is written by
        #   build_ranking_results, so between deploying this and the next
        #   rebuild the query would raise UndefinedColumn -- and Flask answers
        #   an unhandled exception with an HTML debug page, which the frontend
        #   reports as "Unexpected token '<'". Saying so plainly is better
        #   than a stack trace rendered as a parse error.
        raw = args.get("distance")
        if raw is None:
            return None, ("the pr board needs a distance; one of "
                          f"{list(PR_DISTANCES)}")
        try:
            distance = int(raw)
        except ValueError:
            return None, "distance must be a whole number of metres"
        if distance not in PR_DISTANCES:
            return None, f"distance must be one of {list(PR_DISTANCES)}"
    elif board == "performance":
        # ★ OPTIONAL here, unlike pr: the performance board is already
        #   distance-normalised, so the filter narrows scope (a course at
        #   5000m) rather than defining the ranked quantity. Absent means
        #   every distance, which is the board's whole point.
        raw = args.get("distance")
        if raw:
            try:
                distance = int(raw)
            except ValueError:
                return None, "distance must be a whole number of metres"
            if distance not in PR_DISTANCES:
                return None, f"distance must be one of {list(PR_DISTANCES)}"

    # ★ THE COURSE SPECIFIER, Performances and Best times only. Ability
    #   averages seasons and Teams races rosters, so a single-venue filter
    #   has no meaning there -- refuse loudly instead of silently ignoring.
    course = _multiValue(args, "course")
    if course and board not in ("performance", "pr"):
        return None, ("the course filter applies only to the Performances "
                      "and Best times boards")

    scope = args.get("scope", "usa")
    if scope not in SCOPES:
        return None, f"scope must be one of {sorted(SCOPES)}"

    f = {
        "board": board,
        "sport": sport,
        "pool": pool,
        "scope": scope,
        "distance": distance,
        # Lists, not scalars -- see _multiValue. None when absent, so
        # _whereClauses still adds no clause at all for an unset filter.
        "gender": gender,
        "state":  _multiValue(args, "state", upper=True),
        # ★ THE UNIT FILTERS. school_unit already carries these per school --
        #   the athlete page's NCAA DI / WEST / PAC-12 chips read the same
        #   columns -- they were simply never reachable from a board.
        **{k: _multiValue(args, k, upper=(k in _UPPER_UNITS))
           for k in UNIT_FILTERS},
        "course": course,
        "school": _multiValue(args, "school"),
        "grade":  _multiValue(args, "grade"),
        "year":      _multiInt(args, "year", 1990, 2100),
        "date_from": args.get("date_from") or None,
        "date_to":   args.get("date_to")   or None,
        # ★ THE DEFAULT DEPENDS ON THE SPORT, BECAUSE A SEASON DOES.
        #
        #   This was a flat 20, argued from noise: a four-race mean carries
        #   most of a single race's ~3.3% spread, twenty is a real campaign.
        #   Sound for track, where an athlete contests several events a meet
        #   and reaches twenty rated marks easily.
        #
        # ⚠ AND IMPOSSIBLE FOR CROSS COUNTRY. An XC season is eight to twelve
        #   races -- one a week for a term. A floor of twenty does not raise
        #   the bar there, it empties the board: every XC athlete in the
        #   corpus fails it, which is why sport='both' appeared to show only
        #   track and why an athlete with a full season could not be found.
        #
        #   Eight is an XC campaign. The filter is still editable; this only
        #   moves what an unconfigured board shows.
        "min_races": _boundedInt(args, "min_races",
                                 20 if sport == "TF" else 8, 1, 200),
        "limit":     _boundedInt(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
        "offset":    _boundedInt(args, "offset", 0, 0, 100000),
    }

    table = {"ability": _SORTS_ABILITY,
             "performance": _SORTS_PERFORMANCE,
             "pr": _SORTS_PR}[board]
    # ! THE PR BOARD DEFAULTS TO TIME. Every other board is about rating, and
    #   a PR board that opened sorted by rating would be the performance board
    #   with extra steps.
    sort = args.get("sort") or ("time" if board == "pr" else "rating")
    if sort not in table:
        return None, f"sort must be one of {sorted(table)}"
    f["sort"] = sort

    direction = (args.get("dir") or "").upper()
    if direction not in ("ASC", "DESC", ""):
        return None, "dir must be asc or desc"
    # Empty means "whatever this column's natural direction is" -- highest
    # rating, earliest date, fastest time. See _SORTS_ABILITY.
    f["dir"] = direction or table[sort][1]

    # Arbitrary date ranges are a PERFORMANCE concept. An "ability" measured
    # over two weeks is one or two races -- that IS a performance, and the
    # other board exists for it. Rejecting is honest; ignoring is not.
    if board == "ability" and (f["date_from"] or f["date_to"]):
        return None, ("date_from/date_to apply to the performance board only; "
                      "use year for the ability board")

    return f, None


def _whereClauses(f, params, with_dates):
    """Shared filters -> SQL fragment, appending bind values to `params`.

    Every value goes in as a PARAMETER, never string-formatted, so none of it
    can be SQL. An absent filter adds NO clause rather than a
    `(%(x)s IS NULL OR col = %(x)s)` pattern, which defeats index use.

    sport='both' adds no clause at all: an always-true predicate can stop
    Postgres choosing an index, and mixing sports is valid here because
    speed_rating is already 5K-equivalent and difficulty-adjusted.
    """
    parts = []

    # ! 'all' ADDS NO CLAUSE, rather than listing every pool. An always-true
    #   IN-list would stop Postgres using the pool index for the boards that
    #   do filter on it, and there is nothing to gain from naming six values
    #   when the answer is "any".
    if f["pool"] != "all":
        params["pool"] = f["pool"]
        parts.append(" AND pool = %(pool)s")
    elif f.get("gender"):
        # ! A SUFFIX TEST, NOT AN IN-LIST. The pools are hs_m / college_f and
        #   so on, so one gender is every pool ending '_m'. LIKE on a trailing
        #   two characters cannot use the pool index either way, and this
        #   stays right if a level is ever added.
        params["gender"] = f"%\_{f['gender']}"
        parts.append(" AND pool LIKE %(gender)s")

    # ★ A SEMI-JOIN, NOT A SCHOOL IN-LIST. The alternative considered was
    #   folding a unit into the list of schools it contains and filtering on
    #   that -- which is how the athlete page's chips work and needs no new
    #   anything. It also ships a several-thousand-element IN-list into every
    #   query, and the site is already slow (owner, 2026-08-30).
    #
    #   `school IN (SELECT ...)` lets the planner build ONE hash of the
    #   matching schools and probe it, instead of parsing a literal list per
    #   request. school_unit is one row per (school, sport, state) -- small
    #   enough to hash, large enough that an IN-list of its members is not.
    #
    # ! UNQUALIFIED `school` ON THE OUTER SIDE ON PURPOSE. Every other clause
    #   here is unqualified too -- the boards alias their table differently
    #   (s, p) and _whereClauses is shared -- and the subquery names its own
    #   side `u.`, so there is nothing for `school` to bind to but the board.
    #
    # ⚠ AND IT IS NOT A JOIN. A JOIN would multiply rows when a school has
    #   several school_unit rows (one per sport, plus shared names across
    #   states), silently duplicating athletes on the board. IN stops at the
    #   first match by construction.
    for _key in UNIT_FILTERS:
        if f.get(_key):
            params[_key] = f[_key]
            # ! ONE SUBQUERY PER FILTER, OR-ING ITS COLUMNS INSIDE. Splitting
            #   state_div and class into two ANDed clauses would require a
            #   school to be in both, which no school is.
            _ors = " OR ".join(f'u."{c}" = ANY(%({_key})s)'
                               for c in UNIT_COLUMNS[_key])
            parts.append(f' AND school IN (SELECT u.school FROM school_unit u'
                         f' WHERE {_ors})')

    if f.get("distance") is not None:
        # ! A RANGE, so the planner can still use an index on distance. A
        #   `abs(distance - %(d)s) <= ...` form computes per row and cannot.
        d = f["distance"]
        params["dist_lo"] = d * (1 - PR_DISTANCE_TOL)
        params["dist_hi"] = d * (1 + PR_DISTANCE_TOL)
        parts.append(" AND distance BETWEEN %(dist_lo)s AND %(dist_hi)s")

    # ★ THE COURSE SPECIFIER. parseFilters only lets it through for the
    #   performance and pr boards, whose every consumer of this clause --
    #   the board queries AND _rankInResults -- selects from unaliased
    #   ranking_results, so the qualified outer reference resolves. (The
    #   ability board aliases athlete_season; a course filter never reaches
    #   it.) Courses are XC by definition, so the sport pin comes free and
    #   keeps sport=both from mixing in TF rows that can never match.
    if f.get("course"):
        params["course"] = f["course"]
        parts.append(""" AND sport = 'XC'
            AND EXISTS (SELECT 1 FROM meets mm
                        WHERE mm.meet_id = ranking_results.meet_id
                          AND mm.div_id  = ranking_results.div_id
                          AND mm.course_name = ANY(%(course)s))""")

    if f["sport"] != "both":
        params["sport"] = f["sport"]
        parts.append(" AND sport = %(sport)s")

    # ! NO CLAUSE FOR scope='all', and no clause when an explicit state filter
    #   is already present -- naming a state IS a scope, and a second
    #   predicate on the same column only costs a plan.
    if f.get("scope", "usa") == "usa" and not f.get("state"):
        params["us_states"] = list(US_STATES)
        parts.append(" AND state = ANY(%(us_states)s)")
    # ★ = ANY(%(x)s), NOT AN INTERPOLATED IN-LIST. psycopg2 adapts a Python
    #   list to a Postgres array, so the SQL text is IDENTICAL whether the
    #   filter carries one value or fifty -- one bind parameter either way, no
    #   caller text in the query, and one plan in the statement cache instead
    #   of a new one per list length.
    #
    #   A single-element list still uses the index: Postgres rewrites
    #   `col = ANY(ARRAY['CA'])` to `col = 'CA'`.
    for name in _MULTI:
        if not f.get(name):
            continue
        if name == "year":
            # ★ TWO INDEXABLE BRANCHES, NOT A CASE ON EVERY ROW. The user
            #   names a season the way the sport does -- "2026" means autumn
            #   2026 in XC and spring 2026 in track -- so the two sports want
            #   different stored values for the same word.
            #
            #   Writing it as `CASE WHEN sport='TF' ... = ANY(...)` would be
            #   correct and would compute an expression per row, defeating the
            #   year index. An OR of two plain equalities keeps both usable.
            params["year"] = f["year"]
            params["year_tf"] = [y - 1 for y in f["year"]]
            parts.append(" AND ((sport = 'TF' AND year = ANY(%(year_tf)s))"
                         "      OR (sport <> 'TF' AND year = ANY(%(year)s)))")
        else:
            params[name] = f[name]
            parts.append(f" AND {name} = ANY(%({name})s)")

    if with_dates:
        if f["date_from"]:
            params["date_from"] = f["date_from"]
            parts.append(" AND race_date >= %(date_from)s")
        if f["date_to"]:
            params["date_to"] = f["date_to"]
            parts.append(" AND race_date <= %(date_to)s")

    return "".join(parts)


def _orderBy(f, table, tiebreak, unique_key):
    """The ORDER BY clause: a whitelist lookup plus a stable tiebreak.

    ⚠ THE TIEBREAK IS NOT DECORATION. Paging is OFFSET/LIMIT, and a sort with
      ties has no defined order between them -- so without a unique-ish second
      key, an athlete can appear on page 1 AND page 2 while another never
      appears at all. Sorting by state across 60,000 rows is exactly that case.
      Rating is the tiebreak because it is near-continuous.
    """
    expr, _ = table[f["sort"]]
    parts = [f"{expr} {f['dir']} NULLS LAST"]
    if expr != tiebreak.split()[0]:
        parts.append(tiebreak)
    # ★ A FINAL UNIQUE KEY, ALWAYS. Rating is near-continuous but not unique,
    #   and with OFFSET/LIMIT paging a tie has no defined order -- so the same
    #   athlete can appear on two pages while another appears on none. It also
    #   makes _rankByCount's comparison total, which is what lets a count
    #   replace a sort.
    parts.append(unique_key)
    return ", ".join(parts)


# ------------------------------------------------------------------ #
#  3. THE PERFORMANCE BOARD
# ------------------------------------------------------------------ #

def getPerformanceRankings(cur, f):
    """Top single races.

    THREE STAGES, and the order is what makes it fast:
      candidates  index range scan bounded by LIMIT -- the only stage that
                  touches the 61M-row table.
      deduped     one window function over the few thousand candidates.
      page        order the survivors and slice.

    Running the windows over the unbounded filtered set would sort millions of
    rows to return fifty.

    DEDUP: anet and tfrrs both carry the same physical race, tied together by
    canon_meet_id and separated by time -- the same key panels.py uses.
    COALESCE(canon_meet_id, -result_id) puts every unkeyable row in a partition
    of its own: real ids are positive, so a negated result_id cannot collide,
    and a row we cannot key is never treated as a duplicate.

    NO CAP: an athlete may hold as many rows as they earned. See
    MAX_PER_PERSON's removal -- a board of the best races that quietly omits
    some of the best races is not the board its heading describes.

    to_char, not the raw date: psycopg2 returns date objects and Flask's
    jsonify renders those as RFC-822 strings ("Sat, 30 Aug 2025 00:00:00 GMT").
    Casting in SQL gives the frontend plain ISO.
    """
    params = {
        "cand":   (f["limit"] + f["offset"]) * CANDIDATE_FACTOR,
        "limit":  f["limit"],
        "offset": f["offset"],
    }
    where = _whereClauses(f, params, with_dates=True)
    # NULLS LAST for the same reason as the candidate filter below: a
    # user-chosen sort must not be able to float unrated rows to the top.
    order = _orderBy(f, _SORTS_PERFORMANCE,
                     "p.speed_rating DESC NULLS LAST", "p.result_id")

    cur.execute(f"""
        WITH candidates AS (
            SELECT sport, result_id, person_id, pool, speed_rating,
                   race_date, year, state, school, grade,
                   meet_id, div_id, canon_meet_id, time_seconds, event_id,
                   distance
            FROM   ranking_results
            -- ⚠ NULLS SORT FIRST UNDER `DESC`, so this is not optional and
            --   it is not belt-and-braces. Since #46, ranking_results also
            --   carries TIME-ONLY rows -- sprints, which the engine
            --   deliberately does not rate -- with speed_rating NULL. Without
            --   this line every one of them would sort ABOVE the best rated
            --   race in the corpus and the top of every rating board would be
            --   blank rows.
            -- ! EXCLUDED IN THE CANDIDATE SET, not filtered afterwards, for
            --   the same reason the PR board excludes its DNF sentinel here:
            --   a later filter still lets them consume the LIMIT.
            WHERE  speed_rating IS NOT NULL {where}
            ORDER  BY speed_rating DESC
            LIMIT  %(cand)s
        ),
        deduped AS (
            SELECT *, row_number() OVER (
                       PARTITION BY sport,
                                    COALESCE(canon_meet_id, -result_id),
                                    round(time_seconds::numeric, 1)
                       ORDER BY speed_rating DESC) AS dup_rn
            FROM candidates
        )
        SELECT p.sport, p.result_id, p.person_id,
               -- pool + distance ride along for the HS-equivalent view
               -- (pool_view.stampBoardRows in the API route).
               p.pool, p.distance,
               p.speed_rating                       AS rating,
               to_char(p.race_date, 'YYYY-MM-DD')   AS race_date,
               (CASE WHEN p.sport = 'TF' THEN p.year + 1
                     ELSE p.year END)              AS year,
               p.state, p.school, p.grade,
               p.meet_id, p.div_id, p.event_id, p.time_seconds,
               COALESCE(a.name, 'Unknown')          AS name
        FROM   deduped p
        {nameLateral("p")}
        WHERE  p.dup_rn = 1
        ORDER  BY {order}
        OFFSET %(offset)s
        LIMIT  %(limit)s
    """, params)
    return cur.fetchall()


# ------------------------------------------------------------------ #
#  3b. THE PR BOARD
# ------------------------------------------------------------------ #

def getPrRankings(cur, f):
    """Fastest times at one distance -- each athlete's best, once.

    ★ ONE ROW PER ATHLETE, WHICH IS THE DIFFERENCE FROM THE PERFORMANCE BOARD.
      That board lets an athlete hold as many rows as they earned, because
      several great races are several achievements. A PR is singular:
      listing somebody's second-fastest 5000 under the heading "personal
      records" is a contradiction, and it is also how one athlete ends up
      holding half a board -- panels.py measured exactly that.

    ⚠ AND THE CANDIDATE SET IS BOUNDED BY TIME, NOT RATING. The other boards
      order candidates by speed_rating because that is what they rank. Here
      the ranked quantity is the clock, so a rating-ordered candidate set
      would pull the best-RATED times and then sort them by seconds -- which
      quietly drops a fast time run at an easy venue, the very thing a PR
      board is supposed to show.

    The dedup key is the performance board's: anet and tfrrs carry the same
    physical race, tied by canon_meet_id and separated by time.
    COALESCE(canon_meet_id, -result_id) gives every unkeyable row a partition
    of its own -- real ids are positive, so a negated result_id cannot
    collide.
    """
    params = {
        "cand":   (f["limit"] + f["offset"]) * CANDIDATE_FACTOR,
        "limit":  f["limit"],
        "offset": f["offset"],
    }
    where = _whereClauses(f, params, with_dates=True)
    order = _orderBy(f, _SORTS_PR, "p.time_seconds ASC", "p.result_id")

    cur.execute(f"""
        WITH candidates AS (
            SELECT sport, result_id, person_id, pool, speed_rating,
                   race_date, year, state, school, grade,
                   meet_id, div_id, canon_meet_id, time_seconds, distance, event_id
            FROM   ranking_results
            -- ! THE SENTINEL IS EXCLUDED HERE, not filtered out later. A
            --   later filter would still let 999999 into the candidate set
            --   and waste the LIMIT on rows that cannot rank.
            WHERE  time_seconds IS NOT NULL
              AND  time_seconds < {DNF_SENTINEL} {where}
            ORDER  BY time_seconds ASC
            LIMIT  %(cand)s
        ),
        deduped AS (
            SELECT *, row_number() OVER (
                       PARTITION BY sport,
                                    COALESCE(canon_meet_id, -result_id),
                                    round(time_seconds::numeric, 1)
                       ORDER BY speed_rating DESC NULLS LAST) AS dup_rn
            FROM candidates
        ),
        best AS (
            SELECT *, row_number() OVER (
                       PARTITION BY person_id
                       ORDER BY time_seconds ASC) AS person_rn
            FROM deduped
            WHERE dup_rn = 1
        )
        SELECT p.sport, p.result_id, p.person_id, p.pool,
               p.time_seconds,
               p.distance,
               p.speed_rating                       AS rating,
               to_char(p.race_date, 'YYYY-MM-DD')   AS race_date,
               (CASE WHEN p.sport = 'TF' THEN p.year + 1
                     ELSE p.year END)              AS year,
               p.state, p.school, p.grade,
               p.meet_id, p.div_id, p.event_id,
               COALESCE(a.name, 'Unknown')          AS name
        FROM   best p
        {nameLateral("p")}
        WHERE  p.person_rn = 1
        ORDER  BY {order}
        OFFSET %(offset)s
        LIMIT  %(limit)s
    """, params)
    return cur.fetchall()


# ------------------------------------------------------------------ #
#  4. THE ABILITY BOARD
# ------------------------------------------------------------------ #

def getAbilityRankings(cur, f):
    """Top athletes by season average.

    No dedup and no per-athlete cap: athlete_season is already one row per
    (person, pool, sport, year), so an athlete cannot repeat within a filtered
    board.

    min_races is a real floor, not decoration. A "season average" over two
    races is a performance wearing an ability's name.

    WITH sport='both' AN ATHLETE CAN STILL APPEAR TWICE -- once for XC, once
    for TF -- because the rows are per sport. That is honest (two different
    seasons), but the UI must show the sport in each row or it looks like a bug.
    """
    params = {
        "min_races": f["min_races"],
        "limit":     f["limit"],
        "offset":    f["offset"],
    }
    where = _whereClauses(f, params, with_dates=False)
    order = _orderBy(f, _SORTS_ABILITY, "s.mean_rating DESC", "s.person_id")

    cur.execute(f"""
        SELECT s.person_id, s.sport, s.pool,
               -- ! THE LABEL, NOT THE STORED VALUE. See _YEAR_LABEL.
               (CASE WHEN s.sport = 'TF' THEN s.year + 1
                     ELSE s.year END)              AS year,
               s.mean_rating                        AS rating,
               s.best_rating, s.n_races,
               s.state, s.school, s.grade,
               to_char(s.first_race, 'YYYY-MM-DD')  AS first_race,
               to_char(s.last_race,  'YYYY-MM-DD')  AS last_race,
               COALESCE(a.name, 'Unknown')          AS name
        FROM   athlete_season s
        {nameLateral("s")}
        WHERE  s.n_races >= %(min_races)s {where}
        ORDER  BY {order}
        OFFSET %(offset)s
        LIMIT  %(limit)s
    """, params)
    return cur.fetchall()


# ------------------------------------------------------------------ #
#  5. "WHERE AM I?"
# ------------------------------------------------------------------ #

def _rankInResults(cur, f, person_id):
    """Rank an athlete on the performance or PR board, by counting.

    ★ THIS BECAME POSSIBLE WHEN THE CAP CAME OFF. With a per-athlete cap, a
      row's position depended on a window function over everybody above it, so
      the only correct answer was to sort the entire filtered set for one
      click. Uncapped, "how many rows beat this one" is a COUNT over an index
      range.

    ! DEDUP STILL HAS TO BE HONOURED, and it is -- by counting only the rows
      that survive it. The key is the board's own: anet and tfrrs carry the
      same physical race, tied by canon_meet_id and separated by the rounded
      time. Counting raw rows would rank an athlete behind their own duplicate.

    ⚠ DEFAULT SORT ONLY, for the reason _rankByCount gives: the comparison has
      to reproduce _orderBy exactly, including NULLS LAST, and every extra
      sort is another chance to get that subtly wrong. The caller refuses
      anything else rather than returning a plausible number.
    """
    # performance ranks by rating (higher first); pr by time (lower first).
    is_pr = f["board"] == "pr"
    col = "time_seconds" if is_pr else "speed_rating"
    beats = "<" if is_pr else ">"

    params = {"person_id": person_id}
    where = _whereClauses(f, params, with_dates=True)

    # ! THE ATHLETE'S OWN BEST ROW UNDER THESE FILTERS, which is the row that
    #   would appear on the board. Their other races are irrelevant to where
    #   they sit.
    dnf = ("" if not is_pr
           else f" AND time_seconds < {DNF_SENTINEL}")
    cur.execute(f"""
        SELECT {col}, result_id
        FROM   ranking_results
        WHERE  person_id = %(person_id)s AND {col} IS NOT NULL {dnf} {where}
        ORDER  BY {col} {"ASC" if is_pr else "DESC"}, result_id
        LIMIT  1
    """, params)
    row = cur.fetchone()
    if not row:
        return None
    params["target"], params["target_id"] = row[0], row[1]

    # ★ COUNT THE DEDUPED SET, NOT THE RAW ONE. DISTINCT over the dedup key
    #   collapses the anet/tfrrs pair to one, which is exactly what the board
    #   shows -- so the rank it reports is the row number the user will find.
    #
    #   For the PR board the athlete key goes in too: that board is one row
    #   per person, so two of somebody else's fast times are one competitor,
    #   not two.
    key = ("person_id" if is_pr else
           "sport, COALESCE(canon_meet_id, -result_id), "
           "round(time_seconds::numeric, 1)")
    cur.execute(f"""
        SELECT count(*) FROM (
            SELECT DISTINCT {key}
            FROM   ranking_results
            WHERE  {col} IS NOT NULL {dnf} {where}
              AND  ({col} {beats} %(target)s
                    OR ({col} = %(target)s AND result_id < %(target_id)s))
        ) q
    """, params)
    return int(cur.fetchone()[0]) + 1


def _rankByCount(cur, f, person_id):
    """Rank by COUNTING what outranks the athlete. Default sort only.

    ★ WHY A SECOND IMPLEMENTATION. rankOf sorts the whole filtered set --
      correct for any sort, but a sort of a million rows for one click. For
      sort=rating, which is the default and nearly every lookup, the same
      answer is a COUNT over an index range: everyone rated higher, plus the
      ties that fall before them on the unique key. No sort at all.

    ⚠ THE COMPARISON MUST MATCH _orderBy EXACTLY, and it does only because
      this ordering is fixed and simple: mean_rating DESC, then person_id ASC,
      which is precisely what _orderBy emits for sort=rating. That is the ONLY
      case this handles. Extending it to another sort means reproducing that
      column's direction AND its NULLS LAST behaviour in the comparison, which
      is the trap that made the general version a sort in the first place.
    """
    params = {"min_races": f["min_races"], "person_id": person_id}
    where = _whereClauses(f, params, with_dates=False)

    cur.execute(f"""
        SELECT s.mean_rating
        FROM   athlete_season s
        WHERE  s.person_id = %(person_id)s
          AND  s.n_races >= %(min_races)s {where}
        ORDER  BY s.mean_rating DESC NULLS LAST
        LIMIT  1
    """, params)
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    params["target"] = row[0]

    cur.execute(f"""
        SELECT count(*)
        FROM   athlete_season s
        WHERE  s.n_races >= %(min_races)s {where}
          AND  (s.mean_rating > %(target)s
                OR (s.mean_rating = %(target)s
                    AND s.person_id < %(person_id)s))
    """, params)
    return int(cur.fetchone()[0]) + 1


def rankOf(cur, f, person_id):
    """This athlete's 1-based position on the CURRENT board, or None.

    ★ row_number() OVER THE SAME ORDER BY THE BOARD USES, rather than a
      hand-derived "count everyone above me" comparison. The comparison
      version is cheaper and I could not make it provably agree with the
      board: with a chosen sort, a rating tiebreak and NULLS LAST, the
      lexicographic OR-chain has to reproduce the null handling of every key
      in both directions, and a single mistake puts the athlete on the wrong
      page with no symptom. Reusing the identical ORDER BY string cannot
      disagree, because it IS the board's ordering.

      The cost is one sort of the filtered set. This runs on a click, not on
      every page load, so that is the right trade.

    ⚠ THIS GENERAL PATH IS ABILITY-ONLY, and the reason is the candidate
      window: getPerformanceRankings and getPrRankings both bound their input
      with LIMIT %(cand)s before deduping, so a row_number over that is a rank
      within the window rather than within the corpus.

      Those two boards get _rankInResults instead, which counts rather than
      sorts and therefore never sees a window. That only became possible when
      the per-athlete cap came off the performance board -- with a cap, a
      row's position depends on a window function over everybody above it.

    ⚠ AN ATHLETE CAN HOLD SEVERAL ROWS. athlete_season is per (person, pool,
      sport, year), so with sport='both' or no year filter one person has many.
      MIN(rn) picks their best-placed row, which is the one a person looking
      for themselves means.
    """
    # ! THE RESULT BOARDS COUNT; THE ABILITY BOARD MAY SORT. ranking_results
    #   is never fully ordered here, so a count is the only correct answer --
    #   and the caller has already refused any non-default sort on those two.
    if f["board"] in ("performance", "pr"):
        return _rankInResults(cur, f, person_id)

    # The default sort has a count-based shortcut that avoids sorting the
    # whole filtered set. Everything else needs the general version below.
    if f["sort"] == "rating" and f["dir"] == "DESC":
        return _rankByCount(cur, f, person_id)

    params = {
        "min_races": f["min_races"],
        "person_id": person_id,
    }
    where = _whereClauses(f, params, with_dates=False)
    order = _orderBy(f, _SORTS_ABILITY, "s.mean_rating DESC", "s.person_id")

    cur.execute(f"""
        WITH ranked AS (
            SELECT s.person_id,
                   row_number() OVER (ORDER BY {order}) AS rn
            FROM   athlete_season s
            {nameLateral("s")}
            WHERE  s.n_races >= %(min_races)s {where}
        )
        SELECT min(rn) FROM ranked WHERE person_id = %(person_id)s
    """, params)

    row = cur.fetchone()
    return None if not row or row[0] is None else int(row[0])