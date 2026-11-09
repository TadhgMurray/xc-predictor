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
    # ! THE STORED ACADEMIC YEAR, which is also what the column shows on
    #   this page -- so it sorts the way it reads. See _YEAR_LABEL.
    "year":    ("s.year", "DESC"),
    "name":    ("a.name",           "ASC"),
    "school":  ("s.school",         "ASC"),
    "state":   ("s.state",          "ASC"),
    "grade":   ("s.grade",          "ASC"),
    "first":   ("s.first_race",     "ASC"),
    "last":    ("s.last_race",      "DESC"),
}

# the sorts an unnarrowed board may use: its index reads them in order
_INDEXED_SORTS = ("rating", "best")

_SORTS_PERFORMANCE = {
    "rating":  ("p.speed_rating",   "DESC"),
    "date":    ("p.race_date",      "DESC"),
    "time":    ("p.time_seconds",   "ASC"),
    "year":    ("p.year", "DESC"),
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
    # ★ FIELD EVENTS RANK THE MARK, longest or highest first. parseFilters
    #   maps the frontend's "time" onto this when the event is a field event,
    #   so the header the user clicks stays "Time / Mark" on both.
    "mark":    ("p.mark",          "DESC"),

    "date":    ("p.race_date",     "DESC"),
    "rating":  ("p.speed_rating",  "DESC"),
    "year":    ("p.year", "DESC"),
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
# ⚠ THE BOARDS SHOW THE ACADEMIC YEAR, THE REST OF THE SITE DOES NOT
#   (owner, 2026-09-08: "year should be academic year" ... "Just change for
#   rankings"). season_year.py stores one clock -- August to July, named for
#   the year it opens in -- and the /rankings page now shows and filters
#   exactly that, for both sports, on all four boards.
#
#   The athlete page, the school page and the share cards keep the +1 for
#   track (app.season_label, school.py, cards.py): spring 2026 reads "2026
#   TF" there, because that is what an athlete calls the season. So the
#   same season is "2025" on a board and "2026 TF" on a page, deliberately.
#   That divergence was raised before the change and chosen anyway; it is
#   not an oversight, and closing it means moving those three together.
#
# ! THE FILTER AND THE DISPLAY MOVED TOGETHER, which is the only part that
#   is not a preference. Showing one and filtering the other is how a Year
#   chip silently selects the wrong season.
# ! KEPT AS THE ONE PLACE THIS RULE IS WRITTEN DOWN, even though it is now
#   the bare column: every comment about the boards' year points here, and a
#   future change back to a label has one definition to edit.
_YEAR_LABEL = "year"


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
HS_UNITS = ("state_div", "section", "section_div", "area", "league")


UNIT_FILTERS = COLLEGE_UNITS + HS_UNITS

# One filter key -> the school_unit columns it searches.
UNIT_COLUMNS = {
    "division":    ("division",),
    "region":      ("region",),
    "conference":  ("conference",),
    "state_div":   ("state_div", "class"),
    "section":     ("section",),
    "section_div": ("section_div",),
    "area":        ("area",),
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
# ★ THE EVENT AXIS OF THE TIMES BOARD (owner, 2026-09-02: "Best times should
#   be Best times/marks, and include steeple, hurdles and field events").
#   ranking_results.event_kind is NULL for a flat race, 'hurdles' or
#   'steeple' for a timed non-flat race (distance carries the metres), or a
#   field key from racecast/marks.normalizeFieldEvent for a field event,
#   where `mark` carries the parsed metres and time_seconds is NULL.
PR_HURDLE_DISTANCES = (55, 60, 65, 100, 110, 300, 400)
PR_STEEPLE_DISTANCES = (2000, 3000)
PR_FIELD_EVENTS = ("high_jump", "pole_vault", "long_jump", "triple_jump",
                   "shot_put", "discus", "javelin", "hammer", "weight_throw")
PR_EVENTS = ("hurdles", "steeple") + PR_FIELD_EVENTS

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
# ★ HOW DEEP A BOARD PAGES (2026-11-08). The default boards seek from a
#   stored position (board_seek, see "DEEP PAGES" below), so any page of
#   them costs what page one does and the whole board can be walked: the
#   ceiling is MAX_OFFSET. Everything that still pages live keeps the old
#   ceiling, LEGACY_MAX_OFFSET (a deeper offset reads as that one, as it
#   always has), and a FILTERED live board stops at LIVE_DEEP_MAX with a
#   message (deepPageRefusal) instead of ranking millions of rows.
MAX_OFFSET = 10_000_000
LEGACY_MAX_OFFSET = 100000
LIVE_DEEP_MAX = 10000
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
PR_FIRST_FACTOR = 4      # the PR board's first, smaller try (getPrRankings)


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
# ★ AND THE NAME ON THE RESULT ROWS WHEN `athletes` HAS NONE (owner,
#   2026-10-06: "Unknown" heading the CA and national track boards). A
#   person split off by engine/unlink.py or person_collision.py gets a
#   fresh id from SPLIT_BASE (2e9) up, and no athletes row comes with it --
#   but every one of their result rows still carries athlete_name, which is
#   what the athlete page already falls back to (app._exactField's
#   row_name). Only consulted when the athletes lookup is empty; one
#   indexed probe per row of a 50-row page.
# ★ NO "UNKNOWN" ON A BOARD (owner, 2026-11-06, /athlete/1009420043: #2 on
#   the college board as "Unknown", Snow College, one race). A placeholder in
#   any of the three name sources is no name; a row with no name anywhere is
#   left off the boards (UNNAMED_OUT), where nobody can tell who it is.
UNKNOWN_NAMES = ("unknown", "unknown unknown", "n/a", "na", "tbd", "tba", "-", "none",
                 "no name", "unknown athlete", "unknown runner", "athlete unknown")
_UNKNOWN_SQL = "(" + ", ".join(f"'{n}'" for n in UNKNOWN_NAMES) + ")"


def namedName(expr):
    """SQL: expr, or NULL when it is blank or a placeholder."""
    return (f"CASE WHEN lower(btrim(COALESCE({expr}, ''))) IN ('', "
            + ", ".join(f"'{n}'" for n in UNKNOWN_NAMES) + f") THEN NULL ELSE btrim({expr}) END")


UNNAMED_OUT = " AND a.name IS NOT NULL"
# ★ THE PAGE FIRST, THEN ITS NAMES (2026-11-06: /api/rankings 51.8 s, the
#   ability board sorted by best, offset 50). A WHERE on the name made the
#   database look up a name for EVERY row of the board before it could sort
#   and take fifty. The page is cut from the board first -- with UNNAMED_SLACK
#   rows to spare, so a nameless one dropped still leaves a full page -- and
#   only those rows are named. A sort BY name still names everything first.
UNNAMED_SLACK = 25


def pageThenName(alias, base_from, base_where, order):
    """The FROM ... LIMIT tail of a board query (see the note above)."""
    lat = nameLateral(alias)
    if "a.name" in order:
        return (f"FROM {base_from} {lat} WHERE {base_where}{UNNAMED_OUT} "
                f"ORDER BY {order} OFFSET %(offset)s LIMIT %(limit)s")
    return (f"FROM (SELECT {alias}.* FROM {base_from} WHERE {base_where} ORDER BY {order} "
            f"OFFSET %(offset)s LIMIT %(limit)s + {UNNAMED_SLACK}) {alias} {lat} "
            f"WHERE a.name IS NOT NULL ORDER BY {order} LIMIT %(limit)s")

_NAME_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT COALESCE(
                 -- ★ "Unknown" IS NO NAME (owner, 2026-11-02: Matthew Meli,
                 --   person 1009162084, "Unknown" on the board while his own
                 --   rows say Matthew Meli): an athletes row a linker left with
                 --   the placeholder sorted first as a name, and the result
                 --   rows' real name below was never asked
                 (SELECT """ + namedName("concat_ws(' ', a.first_name, a.last_name)") + """
                  FROM   athletes a
                  -- ⚠ every profile of the person, as the athlete page reads
                  --   them (2026-10-07): the profile whose id IS the person
                  --   id can be blank while another carries the name
                  WHERE  a.person_id = {alias}.person_id OR a.athlete_id = {alias}.person_id
                  ORDER  BY (""" + namedName("concat_ws(' ', a.first_name, a.last_name)") + """ IS NOT NULL) DESC,
                            (a.gender IN ('M', 'F')) DESC
                  LIMIT  1),
                 (SELECT btrim(x.athlete_name) FROM results x
                  WHERE  x.person_id = {alias}.person_id
                    AND  lower(btrim(COALESCE(x.athlete_name, ''))) NOT IN ('', """ + _UNKNOWN_SQL[1:] + """ LIMIT 1),
                 (SELECT btrim(x.athlete_name) FROM results_tf x
                  WHERE  x.person_id = {alias}.person_id
                    AND  lower(btrim(COALESCE(x.athlete_name, ''))) NOT IN ('', """ + _UNKNOWN_SQL[1:] + """ LIMIT 1)
               ) AS name
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


from season_floor import floorSql, seasonQuantileSql   # the race-count floor and the season quantile: one rule


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
    event = None
    if board == "pr":
        # ⚠ THE COLUMN IS NEWER THAN THE TABLE. `distance` is written by
        #   build_ranking_results, so between deploying this and the next
        #   rebuild the query would raise UndefinedColumn -- and Flask answers
        #   an unhandled exception with an HTML debug page, which the frontend
        #   reports as "Unexpected token '<'". Saying so plainly is better
        #   than a stack trace rendered as a parse error.
        # ★ event= SELECTS THE KIND: absent is a flat race at `distance`;
        #   hurdles / steeple need a distance from their own lists; a field
        #   event takes no distance and ranks the mark.
        event = (args.get("event") or "").strip().lower() or None
        if event is not None and event not in PR_EVENTS:
            return None, f"event must be one of {list(PR_EVENTS)}"
        raw = args.get("distance")
        if event in PR_FIELD_EVENTS:
            if raw:
                return None, f"{event} is a field event and takes no distance"
        else:
            if raw is None:
                return None, ("the pr board needs a distance; one of "
                              f"{list(PR_DISTANCES)}")
            try:
                distance = int(raw)
            except ValueError:
                return None, "distance must be a whole number of metres"
            allowed_d = (PR_HURDLE_DISTANCES if event == "hurdles" else
                         PR_STEEPLE_DISTANCES if event == "steeple" else
                         PR_DISTANCES)
            if distance not in allowed_d:
                return None, (f"distance must be one of {list(allowed_d)}"
                              + (f" for {event}" if event else ""))

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

    # ★ THE EVENT WINDOW ON THE ABILITY BOARD (owner, 2026-09-08). Metres,
    #   inclusive both ends: dist_min=3000 asks "how good are they over
    #   3000m and up", which is the question a preseason cross-country
    #   projection is actually asking. The other boards already rank one
    #   event at a time through `distance`, so this belongs to ability
    #   alone -- and is REFUSED elsewhere rather than ignored, the same
    #   posture date_from takes on the ability board.
    dist_min = dist_max = None
    for key in ("dist_min", "dist_max"):
        raw = args.get(key)
        if raw in (None, ""):
            continue
        if board != "ability":
            return None, (f"{key} applies only to the Athletes board; the "
                          f"other boards rank one event through `distance`")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None, f"{key} must be a distance in metres"
        if not 0 < val <= 200000:
            return None, f"{key} must be between 0 and 200000 metres"
        if key == "dist_min":
            dist_min = val
        else:
            dist_max = val
    if (dist_min is not None and dist_max is not None
            and dist_min > dist_max):
        return None, "dist_min is greater than dist_max: no event can match"

    f = {
        "dist_min": dist_min,
        "dist_max": dist_max,
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
        # ★ THREE (owner, 2026-09-02), on every board with a floor. The old
        #   20 TF / 8 XC pair matches rankings.js's defaultMinRaces, which
        #   is what an unconfigured board actually sends.
        "min_races": _boundedInt(args, "min_races", 3, 1, 200),
        # ★ TYPED OR DEFAULTED. The default floor exempts open seasons
        #   (season_floor: none from 2026 XC on); a minimum the reader
        #   typed applies to every row. rankings.js sends the field only
        #   when the box was touched, so absent means default.
        "min_races_explicit": (args.get("min_races") or "").strip() != "",
        "limit":     _boundedInt(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
        "offset":    _boundedInt(args, "offset", 0, 0, MAX_OFFSET),
        # ★ THE SCALE THE READER IS LOOKING AT. "hs" means the rows are shown
        #   as HS-equivalents, so the ORDER must be on those numbers or a
        #   pool=all board reads unsorted (owner, 2026-09-02). Anything else
        #   is the own-pool scale, which is also what an old URL means.
        "scale": ("hs" if (args.get("scale") or "").strip().lower() == "hs"
                  else "pool"),
    }

    table = {"ability": _SORTS_ABILITY,
             "performance": _SORTS_PERFORMANCE,
             "pr": _SORTS_PR}[board]
    # ! THE PR BOARD DEFAULTS TO TIME. Every other board is about rating, and
    #   a PR board that opened sorted by rating would be the performance board
    #   with extra steps.
    sort = args.get("sort") or ("time" if board == "pr" else "rating")
    # ! A FIELD-EVENT BOARD RANKS THE MARK. The frontend's header key stays
    #   "time" (one column, "Time / Mark"), so the mapping lives here.
    if board == "pr" and event in PR_FIELD_EVENTS and sort == "time":
        sort = "mark"
    if sort not in table:
        return None, f"sort must be one of {sorted(table)}"
    f["sort"] = sort
    f["event"] = event


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

    # ★ A SORT NO INDEX SERVES NEEDS A NARROWER BOARD (audit 2026-11-09: the
    #   all-time hs_m ability board sorted by name answered 503 after 55 s,
    #   19 s with a year; grade 7.5 s, races 5.7 s, last 4.7 s). The rating
    #   sorts read an index in order; every other sort orders the whole
    #   filtered pool -- millions of seasons -- for one page. Those sorts need
    #   a state, school, grade, unit, course or date filter first.
    if (board == "ability" and sort not in _INDEXED_SORTS
            and not any(f.get(k) for k in _SEEK_NARROWING)):
        return None, ("Sorting the whole country by this column is too slow. "
                      "Pick a state, school or conference first, or sort by rating.")

    return f, None


# The stored grade -> one comparable key, in SQL. Numbers keep their
# number (9th -> 9), except 13..16 in a college pool, which are the class
# words; a class word is fr/so/jr/sr in a college pool and 9..12 in a
# school pool. Anything else compares as itself, lower-cased.
# ! QUALIFIABLE, because a second caller needs it against an aliased table
#   (teams._athleteFieldWhere, the returning board). The expression names
#   TWO columns -- grade and pool -- so a caller cannot safely qualify it by
#   string replacement: patching only "grade" leaves "pool" bare, and
#   replacing the word "pool" would also hit the 'college%' literal beside
#   it. gradeKeySql(alias) builds it correctly instead.
def boardYear(sport, label):
    """A season LABEL as the boards want it: the stored academic year.

    ★ THE ONE CONVERSION, IN ONE PLACE (2026-09-08). The boards filter the
      stored academic year; the athlete page, the school page, the share
      cards and the homepage meta all still speak the label (year + 1 for
      track). Every link that crosses from one of those into a board has to
      convert, and each one that forgot came out as an EMPTY board rather
      than an error -- the landing pages, the home page's View-all, and the
      athlete page's rank line all silently asked for the season after the
      one on screen.

    ! None PASSES THROUGH, because "no year" is a real state on every one of
      those callers and must not become year 1.
    """
    if label in (None, ""):
        return None
    n = int(label)
    return n - 1 if (sport or "").upper() == "TF" else n


# ★ A GRADUATION YEAR IS A GRADE FOR ONE SEASON (sweep 2026-10-10, A12).
#   Some feeds write the class year in the grade column ("2026"). The digit
#   branch below read it as left('2026', 3) = 202 -- a key that is never
#   '12', so the runner never graduated off a carried roster, and a grade
#   filter for 12 never found them. The class of G is a senior in the
#   academic year that ENDS in G: stored year Y (the academic year's opening
#   calendar year, both sports -- boardYear) has seniors of class Y+1, so
#   the high-school grade is 12 - (G - (Y+1)) = 13 + Y - G.
#     - G <= Y+1: '12' -- the graduating class, or past it (a class year the
#       season has already passed is terminal; it is never "13").
#     - a college pool: the class word, sr/jr/so/fr for 0..3 years left
#       (four years is the class's whole span); further out than that is no
#       college class at all and keys as the raw text, never a guessed word.
#   ! THE YEAR COLUMN IS THE ROW'S OWN (`year`, athlete_season and
#     ranking_results both), qualified with the alias like grade and pool.
#   ! ANCHORED ^(19|20)dd$: "12", "2", "SR-4" and an ordinal can never
#     match it, so every other branch keys exactly as before.
_CLASS_YEAR_RE = "^(19|20)[0-9][0-9]$"


def gradeKeySql(alias="", year_col="year"):
    """The grade key expression, optionally qualified: gradeKeySql("s")."""
    q = f"{alias}." if alias else ""
    g = f"lower(trim({q}grade))"
    gn = f"left(regexp_replace({g}, '[^0-9]', '', 'g'), 3)::int"
    gw = (f"(CASE WHEN {g} LIKE 'fr%%' THEN 'fr' WHEN {g} LIKE 'so%%' THEN 'so' "
          f"WHEN {g} LIKE 'j%%' THEN 'jr' "
          f"WHEN {g} LIKE 'sr%%' OR {g} LIKE 'se%%' THEN 'sr' END)")
    # years until graduation, counted from the row's own season
    left = f"({g}::int - ({q}{year_col} + 1))"
    return f"""(CASE
    WHEN {g} ~ '{_CLASS_YEAR_RE}' THEN
        CASE WHEN {q}pool LIKE 'college%%' THEN
                  CASE WHEN {left} <= 0 THEN 'sr'
                       WHEN {left} <= 3
                       THEN (ARRAY['jr','so','fr'])[{left}]
                       ELSE {g} END
             WHEN {left} <= 0 THEN '12'
             ELSE (12 - {left})::text END
    WHEN {g} ~ '^[0-9]' THEN
        CASE WHEN {q}pool LIKE 'college%%' AND {gn} BETWEEN 13 AND 16
             THEN (ARRAY['fr','so','jr','sr'])[{gn} - 12]
             ELSE {gn}::text END
    WHEN {gw} IS NOT NULL THEN
        CASE WHEN {q}pool LIKE 'college%%' THEN {gw}
             ELSE (ARRAY['9','10','11','12'])[array_position(ARRAY['fr','so','jr','sr'], {gw})] END
    ELSE {g} END)"""


_G = "lower(trim(grade))"
_GN = f"left(regexp_replace({_G}, '[^0-9]', '', 'g'), 3)::int"
# the class word from any spelling: fr, freshman, so, soph, jr, junior,
# sr, sr-4, senior
_GW = (f"(CASE WHEN {_G} LIKE 'fr%%' THEN 'fr' WHEN {_G} LIKE 'so%%' THEN 'so' "
       f"WHEN {_G} LIKE 'j%%' THEN 'jr' WHEN {_G} LIKE 'sr%%' OR {_G} LIKE 'se%%' THEN 'sr' END)")
GRADE_KEY_SQL = gradeKeySql()


def _gradeKey(value):
    """The same key for a requested grade: '12' -> '12', 'Sr' -> 'sr'."""
    v = str(value or "").strip().lower()
    if v[:1].isdigit():
        digits = "".join(ch for ch in v if ch.isdigit())[:3]
        return str(int(digits)) if digits else v
    for word, key in (("fr", "fr"), ("so", "so"), ("j", "jr"), ("sr", "sr"), ("se", "sr")):
        if v.startswith(word):
            return key
    return v


# ------------------------------------------------------------------ #
#  THE ABILITY BOARD, RESTRICTED TO A SET OF EVENTS
# ------------------------------------------------------------------ #
#
# ★ WHY (owner, 2026-09-08): "for best athletes I think you should be able
#   to filter out certain events out of those ratings. So you can get the
#   best athlete ratings for like longer distance events in pre season to
#   guess for xc." A track season's rating is an aggregate over everything
#   the athlete ran, so an 800 specialist and a 5000 runner sit on one
#   board and neither number projects a cross-country autumn. Restricting
#   the aggregate to 3000m and up asks the question that does.
#
# ★ THE SAME ESTIMATOR, OR THE BOARD IS NOT COMPARABLE. athlete_season's
#   mean_rating is the 80th percentile of a season's rated races, computed
#   over rows that survive the season-median outlier guard -- not a mean,
#   for the reasons build_ranking_results sets out at length (a mean
#   punishes the athlete who raced more). Recomputing it with a plain
#   avg() would put a different statistic beside the unfiltered board's and
#   invite exactly the comparison it cannot support. So this reproduces
#   build_ranking_results._ATHLETE_SEASON_SQL: same percentile, same
#   median-minus-20 guard, same mode() columns.
#
# ! WHICH IS A DUPLICATED DEFINITION, AND THAT IS THE RISK HERE. If the
#   build's estimator changes and this does not, the two boards silently
#   disagree. tests/test_event_filter.py pins the constants against
#   build_ranking_results so the pair cannot drift unnoticed.
_EVENT_KEYS = ("dist_min", "dist_max")

# ! COPIED FROM build_ranking_results, AND PINNED BY TEST. These three are
#   the estimator: the season quantile, the outlier guard, and the unit
#   columns a row carries. tests/test_event_filter.py reads the build's own
#   source and fails if either number moves without this one following.
_SEASON_Q = 0.80                       # build_ranking_results._SEASON_Q
_SEASON_OUTLIER_PTS = 20.0             # ..._SEASON_OUTLIER_PTS
_UNIT_ROW_COLS = ("division", "region", "conference", "league",
                  "state_div", "section_div", "district", "county",
                  "class", "area", "section")


def eventRestricted(f):
    """Is the ability board being asked for a subset of events?"""
    return any(f.get(k) is not None for k in _EVENT_KEYS)


# ★ THE EVENTS PRESETS, PRECOMPUTED (owner, 2026-11-06: "build it"). The four
#   windows the board offers (rankings.html's Events select) are built once
#   per run into athlete_season_ev by build_season_events.py, with this
#   module's own _abilitySource query over every pool, sport and year at the
#   default scope. A board reads that table only when nothing else changes
#   which RACES count (_evPreset) and only while the table was built from
#   the live ranking_results (_evTableFresh); anything else runs live.
EV_PRESETS = (800, 1500, 3000, 5000)
EV_TABLE = "athlete_season_ev"
EV_GRADE_TABLE = "athlete_season_ev_grade"
_EV_FRESH = {"at": 0.0, "ok": False}
_EV_GRADE_FRESH = {"at": 0.0, "ok": False}
_EV_FRESH_TTL = 60.0


def _evPreset(f):
    """The preset (metres) a board can read from athlete_season_ev, or None.
    Every filter that picks RACES -- a state, a school, a grade, a unit, the
    scope beyond the default, a dist_max -- changes the seasons themselves,
    and those boards run live (one grade: _evGradePreset)."""
    if f.get("grade"):
        return None
    return _evWindow(f)


# ★ ONE GRADE, PRECOMPUTED TOO (owner, 2026-11-08: the grade-10 Events board
#   at 3-22 s). The grade filter picks RACES by the row's own grade key
#   (GRADE_KEY_SQL on ranking_results' grade/pool/year, inside
#   _abilitySource's `ev`), so a grade-10 season is the aggregate over the
#   races run as a 10th grader -- a different season, not a slice of the
#   all-grades one. athlete_season_ev_grade holds exactly that:
#   _abilitySource(by_grade=True) adds the row's key to every window
#   partition and to the GROUP BY, and the group whose key is k IS the live
#   query's season under grade=k (same rows in, same SQL over them).
#   ! ONE KEY ONLY. grade=10,11 aggregates the union of both grades' races,
#     and a percentile of a union is not a function of its parts: several
#     keys run live. Spellings of one key (10, 10th) are one key.
#   ! EVERY NON-NULL KEY IS BUILT, not a list of grades, so a key no row
#     carries is an empty board here exactly as it is live. A NULL grade
#     never matches `= ANY(...)`, so no NULL key is built.
def _evGradePreset(f):
    """(preset, grade key) a one-grade board can read from
    athlete_season_ev_grade, or None: _evPreset's window, one grade key."""
    g = f.get("grade")
    if not g:
        return None
    keys = {_gradeKey(v) for v in g}
    if len(keys) != 1:
        return None
    d = _evWindow(f)
    return None if d is None else (d, keys.pop())


def _evWindow(f):
    """The preset when dist_min is one and no filter but grade (the
    callers' to judge), pool, sport, gender and year narrows the races."""
    d = f.get("dist_min")
    if d is None or f.get("dist_max") is not None:
        return None
    try:
        d = float(d)
    except (TypeError, ValueError):
        return None
    if d not in tuple(float(p) for p in EV_PRESETS):
        return None
    if f.get("scope", "usa") != "usa":
        return None
    if any(f.get(k) for k in ("state", "school")):
        return None
    if any(f.get(k) for k in UNIT_FILTERS):
        return None
    return int(d)


def _evTableFresh(now=None, table=EV_TABLE):
    """athlete_season_ev (or athlete_season_ev_grade) exists and was built
    from the live ranking_results (its comment names that table's oid).
    Asked once a minute per worker and table."""
    import time as _t
    slot = _EV_GRADE_FRESH if table == EV_GRADE_TABLE else _EV_FRESH
    now = _t.time() if now is None else now
    if now - slot["at"] < _EV_FRESH_TTL:
        return slot["ok"]
    ok = False
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as c:
            c.execute("""SELECT obj_description(to_regclass(%s), 'pg_class'),
                                'ranking_results'::regclass::oid""", (table,))
            desc, oid = c.fetchone()
            ok = desc is not None and desc == f"rr_oid={int(oid)}"
    except Exception as exc:                            # noqa: BLE001
        print(f"{table} check: {type(exc).__name__}: {exc}", flush=True)
    slot.update(at=now, ok=ok)
    return ok


def _abilitySource(f, params, where="", precomputed=False, by_grade=False):
    """The FROM fragment for the ability board, aliased `s`.

    Unrestricted this is the prebuilt table. Restricted it is the same
    aggregate over ranking_results, which carries distance per row --
    athlete_season cannot answer an event question because it has already
    aggregated the events away.

    ! EVERY COLUMN THE BOARD, rankOf, countOf AND _whereClauses TOUCH has
      to come out of both shapes under the same name, or a filter that
      works on one board 500s on the other. That is why the subquery names
      the unit columns explicitly rather than SELECT *: ranking_results
      carries them (they are stamped per school+state at build time, same
      as on athlete_season), and leaving one out would make a division
      filter fail only in the restricted case.

    by_grade (build_season_events only) adds the race's grade key, gkey, to
    every window partition and to the GROUP BY: one season per (person,
    pool, sport, year, gkey), the season the live query makes of grade=gkey.
    """
    if not eventRestricted(f):
        return "athlete_season s"
    # ! precomputed is the CALLER's word that `where` is _whereClauses(f) for
    #   this board -- the team board passes its own inner filter (no scope),
    #   whose seasons the table does not hold, and never sets it
    if precomputed and not by_grade:
        ev = _evPreset(f)
        if ev is not None and _evTableFresh():
            params["ev_dist"] = ev
            return "(SELECT * FROM athlete_season_ev WHERE dist_min = %(ev_dist)s) s"
        # ! the outer `where` keeps its grade clause; on these rows it is
        #   always true (s.grade is the mode of races carrying that key)
        evg = _evGradePreset(f)
        if evg is not None and _evTableFresh(table=EV_GRADE_TABLE):
            params["ev_dist"], params["ev_gkey"] = evg
            return ("(SELECT * FROM athlete_season_ev_grade"
                    " WHERE dist_min = %(ev_dist)s AND gkey = %(ev_gkey)s) s")

    dist = []
    if f.get("dist_min") is not None:
        params["dist_min"] = float(f["dist_min"])
        dist.append("r.distance >= %(dist_min)s")
    if f.get("dist_max") is not None:
        params["dist_max"] = float(f["dist_max"])
        dist.append("r.distance <= %(dist_max)s")
    # ! A ROW WITH NO DISTANCE CANNOT BE JUDGED and is left out rather than
    #   let in: the question is "how good over these events", and a race
    #   whose distance nobody parsed is not evidence either way.
    dist.append("r.distance IS NOT NULL")
    where_dist = " AND ".join(dist)

    # ⚠ THE SUBQUERY MUST EXPOSE WHAT _whereClauses WILL NAME, AND THE TWO
    #   PROBE DIFFERENT TABLES. For the ability board _whereClauses asks
    #   _rowHasUnit(col, "athlete_season") -- that is the table it thinks it
    #   is filtering -- and emits `"division" = ANY(...)` against whatever
    #   that probe allows. This source is built from ranking_results. Emit
    #   only the intersection and the outer clause names a column that is
    #   not there: UndefinedColumn, on the restricted board alone, for a
    #   filter that works on every other board.
    #
    #   So: one column per unit the OUTER clause may name, taken from
    #   ranking_results where it has one and NULL where it does not. A NULL
    #   column makes that one filter match nothing here, which is
    #   wrong-but-empty rather than a 500 -- and it should not arise, since
    #   the same build stamps both tables in one pass.
    cols = [c for c in _UNIT_ROW_COLS if _rowHasUnit(c, "athlete_season")]
    have = {c for c in cols if _rowHasUnit(c, "ranking_results")}
    # ★ THE BOARD'S OWN FILTERS GO INSIDE, NOT JUST OUTSIDE. Left to the
    #   outer WHERE alone this aggregates every rated row in the corpus at
    #   or above the distance -- millions of them, per page load -- and
    #   then throws away all but one pool and season. _whereClauses emits
    #   UNQUALIFIED predicates (`AND pool = %(pool)s`), which is exactly
    #   what lets them be reused here against `r`. They stay on the outer
    #   query too: duplicated they cost a plan node, dropped they would be
    #   a correctness bug the first time this helper is called without
    #   them.
    # ★ THE MEDIAN BY WINDOWS, NOT BY A SELF-JOIN (2026-11-06: the events
    #   board at 26-55 s, the statement timeout, for dist_min=3000 on hs_m and
    #   ms_m). `med` was a GROUP BY over the `ev` CTE joined back to `ev` on
    #   the season's key; the planner cannot estimate a CTE's rows, guessed
    #   ~34 groups and one row, and joined them with a NESTED LOOP -- every
    #   season against every race, quadratic (a 2M-row stand-in ran past ten
    #   minutes). Now one sorted pass: each race's rank and its season's race
    #   count by window, the median as the mid-point of the middle one or two --
    #   exactly percentile_cont(0.5), which interpolates halfway between
    #   them, and exact in double precision for real-valued ratings -- and the
    #   outlier cut on the row itself. No join, so no join to misplan.
    #  ! ONLY THE COLUMNS THE AGGREGATE READS, not r.*: ranking_results'
    #    rows are wide and this scan reads a season of a pool.
    ev_cols = ", ".join(["r.person_id", "r.pool", "r.sport", "r.year", "r.speed_rating",
                         "r.race_date", "r.state", "r.school", "r.grade"]
                        + [f'r."{c}"' for c in cols if c in have])
    # the season's key; by_grade adds the race's grade key -- the very
    # GRADE_KEY_SQL _whereClauses compares for grade=, on the same r columns
    part = "person_id, pool, sport, year"
    gsel = ""
    if by_grade:
        gk = gradeKeySql("r")
        ev_cols += f", {gk} AS gkey"
        where_dist += f" AND {gk} IS NOT NULL"
        part += ", gkey"
        gsel = " e.gkey,"
    return f"""(
        WITH ev AS (
            SELECT {ev_cols}
            FROM   ranking_results r
            WHERE  r.speed_rating IS NOT NULL AND {where_dist} {where}
        ),
        ranked AS (
            SELECT ev.*,
                   row_number() OVER (PARTITION BY {part}
                                      ORDER BY speed_rating) AS rn_,
                   count(*) OVER (PARTITION BY {part}) AS n_
            FROM   ev
        ),
        withmed AS (
            SELECT ranked.*,
                   -- the lower and upper middle (the same race when n_ is
                   -- odd), halved in double precision: percentile_cont(0.5)
                   (max(speed_rating::float8) FILTER (WHERE rn_ = (n_ + 1) / 2)
                        OVER (PARTITION BY {part})
                    + max(speed_rating::float8) FILTER (WHERE rn_ = (n_ + 2) / 2)
                        OVER (PARTITION BY {part})) / 2 AS m_
            FROM   ranked
        )
        SELECT e.person_id, e.pool, e.sport, e.year,{gsel}
               {seasonQuantileSql("e.speed_rating", _SEASON_Q)}::real
                                                      AS mean_rating,
               max(e.speed_rating)::real              AS best_rating,
               count(*)                               AS n_races,
               min(e.race_date)                       AS first_race,
               max(e.race_date)                       AS last_race,
               mode() WITHIN GROUP (ORDER BY e.state)  AS state,
               mode() WITHIN GROUP (ORDER BY e.school) AS school,
               mode() WITHIN GROUP (ORDER BY e.grade)  AS grade,
               {"".join((f'mode() WITHIN GROUP (ORDER BY e."{c}") AS "{c}", '
                          if c in have else f'NULL::text AS "{c}", ')
                         for c in cols)[:-2] or "NULL AS _no_units"}
        FROM   withmed e
        WHERE  e.speed_rating >= e.m_ - {_SEASON_OUTLIER_PTS}
        GROUP  BY e.person_id, e.pool, e.sport, e.year{", e.gkey" if by_grade else ""}
    ) s"""


# ★ ONE VALUE IS AN EQUALITY, NOT AN ARRAY (2026-11-07). `col = ANY(%s)` with
#   a one-element list does NOT become `col = x` in Postgres 16: on a
#   column after the index's first, an array condition cannot be an index
#   condition of an ORDERED scan, so the planner keeps the board's order and
#   demotes the column to a filter -- year=2024 walked every year of the
#   pool in rating order (872 pages against 4 on a stand-in), state=OK every
#   state. The rows are the same either way; the list still sits in
#   params[name] for anything that reads it. Several values keep ANY.
def _anyOrEq(name, values, params):
    vals = list(values) if isinstance(values, (list, tuple)) else [values]
    if len(vals) == 1:
        params[f"{name}_one"] = vals[0]
        return f" AND {name} = %({name}_one)s"
    return f" AND {name} = ANY(%({name})s)"


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
            if _key == "area" and not _hasSchoolUnitArea():
                continue                 # column not built yet: a no-op filter
            # ★ THE SCHOOL LIST FIRST, THEN THE BOARD (2026-09-05). The
            #   correlated `school IN (SELECT ... FROM school_unit ...)` left
            #   the planner guessing how many schools a division holds, and
            #   the division / section boards took many seconds. The list is
            #   a few hundred to a few thousand names, fetched once per
            #   filter value and cached ten minutes; `= ANY(array)` lets the
            #   planner use idx_rr_school or hash it, either way in one pass.
            # ★ THE ROW'S OWN UNIT, WHEN IT CARRIES ONE (owner, 2026-09-06:
            #   a Nevada runner under NCS because his school's NAME has a
            #   California row in school_unit; "the top one needs to always
            #   follow the bottom one"). Every row carries the unit columns
            #   stamped per (school, state) at build time; filtering on them
            #   cannot disagree with the athlete page, which reads the same
            #   rows. The name semi-join stays for a column the rows do not
            #   have yet (section arrives with the next step 10).
            # with_dates says which table: True is ranking_results (the
            # performance and PR boards, their ranks and counts), False is
            # athlete_season (ability, rankOf, countOf)
            _tbl = "ranking_results" if with_dates else "athlete_season"
            row_cols = [c for c in UNIT_COLUMNS[_key] if _rowHasUnit(c, _tbl)]
            if row_cols:
                params[f"{_key}_vals"] = list(f[_key])
                ors = " OR ".join(f'"{c}" = ANY(%({_key}_vals)s)' for c in row_cols)
                parts.append(f" AND ({ors})")
                continue
            schools = _unitSchools(_key, f[_key])
            if not schools:
                parts.append(" AND FALSE")           # no school has that unit
                continue
            params[f"{_key}_schools"] = schools
            parts.append(f" AND school = ANY(%({_key}_schools)s)")

    if f.get("distance") is not None:
        # ! A RANGE, so the planner can still use an index on distance. A
        #   `abs(distance - %(d)s) <= ...` form computes per row and cannot.
        d = f["distance"]
        params["dist_lo"] = d * (1 - PR_DISTANCE_TOL)
        params["dist_hi"] = d * (1 + PR_DISTANCE_TOL)
        parts.append(" AND distance BETWEEN %(dist_lo)s AND %(dist_hi)s")

    # ★ THE EVENT KIND. A named kind is an equality; a flat request on the
    #   pr board pins event_kind IS NULL so the 100 m board never lists the
    #   100 m hurdles (same distance, different race). Only the pr board
    #   pins it -- the performance board is rating-ranked and rates nothing
    #   but flat races anyway -- and only once the column exists, so a board
    #   served between this deploy and the next step-10 rebuild keeps
    #   working instead of raising UndefinedColumn on every request.
    ev = f.get("event")
    if ev is not None:
        params["event_kind"] = ev
        parts.append(" AND event_kind = %(event_kind)s")
    elif f.get("board") == "pr" and _hasEventKind():
        parts.append(" AND event_kind IS NULL")


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
        # ★ AND tfrrs ROWS, WHICH CARRY NO STATE (owner, 2026-09-26: "not all
        #   best performances show"). ranking_results.state comes from the
        #   anet meet tables, so every tfrrs row stores NULL and the USA
        #   board dropped most college racing. tfrrs is US collegiate
        #   racing; its result ids are the negative ones (app._ridArg). A
        #   NULL-state anet row is still left out: that is the foreign meet
        #   this scope exists to exclude.
        # ⚠⚠ ONLY WHERE THERE IS A result_id (2026-09-28: every ability
        #   board, the default board included, answered "column result_id
        #   does not exist"). The season boards read athlete_season, which
        #   has no result ids; with_dates is True exactly for the per-result
        #   sources (ranking_results), so it says which kind this is.
        if with_dates:
            parts.append(" AND (state = ANY(%(us_states)s)"
                         " OR (state IS NULL AND result_id < 0))")
        else:
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
            # ★ THE STORED ACADEMIC YEAR, DIRECTLY (2026-09-08). This used to
            #   be an OR of two branches, because the boards showed the label
            #   -- year + 1 for track -- and the filter had to accept what was
            #   shown. The boards show the academic year now (see _YEAR_LABEL),
            #   so the filter is one plain equality on an indexed column and
            #   the two sports need no separate branch.
            params["year"] = f["year"]
            parts.append(_anyOrEq("year", f["year"], params))
        elif name == "grade":
            # ★ THE GRADE IS MATCHED ON WHAT IT MEANS, NOT ON ITS SPELLING
            #   (owner, 2026-09-07: "grade filter is removing ppl it
            #   shouldn't"). The table keeps the feed's spelling: 12, 12th,
            #   Sr, Senior, SR-4, 16. A filter for grade 12 that compared
            #   the raw text dropped every senior a feed spelled Sr. Both
            #   sides go through one key: a number for a school pool (Sr is
            #   12 there), the class word for a college pool (16 is sr).
            params["grade_keys"] = [_gradeKey(v) for v in f["grade"]]
            parts.append(f" AND {GRADE_KEY_SQL} = ANY(%(grade_keys)s)")
        else:
            params[name] = f[name]
            parts.append(_anyOrEq(name, f[name], params))

    if with_dates:
        if f["date_from"]:
            params["date_from"] = f["date_from"]
            parts.append(" AND race_date >= %(date_from)s")
        if f["date_to"]:
            params["date_to"] = f["date_to"]
            parts.append(" AND race_date <= %(date_to)s")

    return "".join(parts)


# ------------------------------------------------------------------ #
#  SORT ON WHAT THE READER SEES
# ------------------------------------------------------------------ #
#
# ★ THE HS-EQUIVALENT VIEW USED TO BE DISPLAY-ONLY, AND A pool=all BOARD READ
#   UNSORTED. The SQL ordered by the own-pool rating; the API then stamped
#   hs_<key> = rating x repFactor(pool, sport) per row; the client showed the
#   stamped number. Two pools with different factors -- college_m onto hs_m
#   against college_f onto hs_f -- therefore interleave out of order, and it
#   shows whenever a board crosses gender (owner, 2026-09-02).
#
#   The fix is to order by the same product the row displays. The factors are
#   a handful of constants per (pool, sport), so they fold into the ORDER BY
#   as a CASE, and the rank lookups reuse the identical expression so "where
#   am I" cannot disagree with the page.
#
# ! ONLY WHEN IT CAN MATTER: scale=hs AND pool=all. A single-pool board
#   multiplies every row by one constant, which changes no order, and the
#   own-pool view is the stored number. hs_m and hs_f are 1.0 by definition.
#
# ⚠ THE PERFORMANCE BOARD'S CANDIDATE WINDOW IS STILL CUT ON THE RAW RATING
#   (an index range), then paged on the scaled one. Factors sit within a few
#   percent of 1, and the window is CANDIDATE_FACTOR pages deep, so a row can
#   only fall outside it on a deep page at a factor boundary. The trade for
#   keeping the 61M-row scan an index range.
_SCALED_COLS = ("mean_rating", "best_rating", "speed_rating")
# ★★ AND pro_m / pro_f ARE IN IT (owner, 2026-09-20: "when ppl are in pro pool
#    they are not able to be hs-equivalent. We should make them able to be
#    hs-equivalent"). They were not, and nothing else was wrong: pool_view has
#    served a pro factor since 2026-09-08 -- hsFactor falls a pro pool back to
#    the same-gender college factor when pro_m has too few rated rows for a
#    constant of its own -- and pool_view's own diagnostic lists pro_m and
#    pro_f. This tuple is what the BOARD's CASE is built from, so a pro row
#    fell to the `ELSE 1.0` arm and kept its raw pro-scale number while every
#    row beside it was converted.
#
# ⚠ WHICH IS WORSE THAN NO CONVERSION, because the board ORDERS on the scaled
#   expression. A pro row at 141 on the pro scale sorted against high
#   schoolers at 193 on the HS scale is not merely mislabelled, it is in the
#   wrong place -- the exact failure the scale exists to prevent on a
#   pool=all board ("a pool=all board reads unsorted").
#
# ! hs_m / hs_f ARE STILL ABSENT ON PURPOSE. Their factor is 1.0 by
#   construction and `ELSE 1.0` already covers them; naming them would add two
#   CASE arms that cannot change a number.
_SCALE_POOLS = ("elem_m", "elem_f", "ms_m", "ms_f", "college_m", "college_f",
                "pro_m", "pro_f")


def _scaleActive(f):
    return f.get("scale") == "hs" and f.get("pool") == "all"


def _mapSql(expr, knots):
    """`expr` through one pool's public-scale knots as SQL: a power curve
    per stretch (pool_view.mapSegments), the number toHs gives."""
    from pool_view import mapSegments

    def arm(a, b):
        return f"{expr} * {a:.6f}" if abs(b - 1.0) < 1e-9 else f"{a:.6f} * power({expr}, {b:.6f})"
    segs = mapSegments(knots)
    if len(segs) == 1:
        return arm(segs[0][1], segs[0][2])
    whens = " ".join(f"WHEN {expr} < {hi:.4f} THEN {arm(a, b)}" for hi, a, b in segs[:-1])
    return f"CASE {whens} ELSE {arm(segs[-1][1], segs[-1][2])} END"


def _hsScaleCase(prefix, expr):
    """`expr` on the public scale as one CASE over (pool, sport) -- each arm
    the pool's own map (pool_view.hsMap: knots, a power curve per stretch)
    -- or None when no pool moves. `prefix` is the table alias with its
    dot, or ''. Switch off, each map is the representative factor."""
    from pool_view import hsMap
    arms = []
    for pool in _SCALE_POOLS:
        maps = {}
        for sport in ("XC", "TF"):
            m = hsMap(pool, sport)
            if m is not None and any(abs(y / x - 1.0) > 1e-9 for x, y in m):
                maps[sport] = m
        if not maps:
            continue
        if len(maps) == 2 and maps["XC"] == maps["TF"]:
            arms.append(f"WHEN {prefix}pool = '{pool}' THEN {_mapSql(expr, maps['XC'])}")
            continue
        for sport, m in maps.items():
            arms.append(f"WHEN {prefix}pool = '{pool}' AND {prefix}sport = '{sport}' "
                        f"THEN {_mapSql(expr, m)}")
    if not arms:
        return None
    # ! a rating of 0 or below has no power curve (power() of a negative
    #   number raises); it is not a rating, so it passes unchanged
    return f"(CASE WHEN {expr} IS NULL OR {expr} <= 0 THEN {expr} " + " ".join(arms) + f" ELSE {expr} END)"


def _scaleExpr(f, expr):
    """`expr` as the board displays it: on the public scale when the scale
    is active and `expr` is a rating column; otherwise unchanged."""
    if not _scaleActive(f):
        return expr
    alias, dot, col = expr.rpartition(".")
    if col not in _SCALED_COLS:
        return expr
    pre = f"{alias}." if dot else ""
    # ★ pool_view.hsMap's knots: a board sorts on exactly the number the
    #   page shows
    case = _hsScaleCase(pre, expr)
    return expr if case is None else case


_EVENT_KIND_PRESENT = None
_UNIT_AREA_PRESENT = None


_UNIT_SCHOOL_CACHE = {}          # (key, values) -> (until, [schools])


_ROW_UNIT_COLS = {}      # table -> {at, cols}, see _rowHasUnit


def _rowHasUnit(col, table="ranking_results"):
    """Does this table carry this unit column WITH VALUES IN IT? Probed
    every ten minutes per table, so a column that arrives with a rebuild
    is used within minutes and never raises before it exists.

    ⚠ PER TABLE (2026-09-06). The ability board, the rank line and the
      counts read athlete_season; the probe only asked ranking_results,
      so every ability query with a unit filter raised UndefinedColumn.

    ⚠ AND POPULATED, NOT MERELY PRESENT (2026-09-06, the same evening).
      athlete_season on the server DOES carry the older unit columns: an
      earlier createShadow migration added them before it was keyed to
      ranking_results (2026-09-01), and nothing ever wrote them. All
      NULL, so `"state_div" = ANY(...)` matched nobody, no error, and
      every unit scope on every athlete page read "not on board" while
      the semi-join it replaced would have answered. The column counts
      only when pg_stats says it holds values (null_frac < 1); with no
      statistics yet, one bounded probe for a non-NULL row decides."""
    import time as _t
    slot = _ROW_UNIT_COLS.setdefault(table, {"at": 0.0, "cols": set()})
    if _t.time() - slot["at"] > 600:
        cols = set()
        try:
            from database import getConn
            with getConn() as conn, conn.cursor() as c:
                c.execute("""SELECT column_name FROM information_schema.columns
                             WHERE table_schema = 'public' AND table_name = %s""",
                          (table,))
                present = {r[0] for r in c.fetchall()}
                wanted = present & {c2 for cs in UNIT_COLUMNS.values() for c2 in cs}
                if wanted:
                    c.execute("""SELECT attname, null_frac FROM pg_stats
                                 WHERE schemaname = 'public' AND tablename = %s
                                   AND attname = ANY(%s)""", (table, list(wanted)))
                    stats = dict(c.fetchall())
                    for col2 in wanted:
                        nf = stats.get(col2)
                        # ⚠ NO STATISTICS MEANS NOT USED. The first cut asked the
                        #   table (`WHERE col IS NOT NULL LIMIT 1`), and on a
                        #   column that is all NULL that is a full scan of 13M
                        #   rows, nine columns, every worker, every ten
                        #   minutes: the athlete page went slow and the restart
                        #   hung behind the scans (owner, 2026-09-07). The build
                        #   analyses the table after stamping, so a populated
                        #   column always has statistics; a column without them
                        #   falls back to the school semi-join, which is safe.
                        if nf is not None and nf < 1.0:
                            cols.add(col2)
        except Exception as exc:                     # noqa: BLE001
            print(f"row_units: probe of {table} failed ({exc})", flush=True)
            cols = set()
        slot["cols"] = cols
        slot["at"] = _t.time()
    return col in slot["cols"]


def _unitSchools(key, values):
    """The schools whose school_unit row carries any of `values` for this
    filter (issue 179); cached ten minutes per (key, values)."""
    import time as _t
    ck = (key, tuple(sorted(values)))
    hit = _UNIT_SCHOOL_CACHE.get(ck)
    if hit and _t.time() < hit[0]:
        return hit[1]
    ors = " OR ".join(f'u."{c}" = ANY(%(v)s)' for c in UNIT_COLUMNS[key])
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as c:
            c.execute(f"SELECT DISTINCT u.school FROM school_unit u WHERE {ors}",
                      {"v": list(values)})
            schools = [r[0] for r in c.fetchall() if r[0]]
    except Exception as exc:                         # noqa: BLE001
        # said in the log: an empty list here is "AND FALSE" on the board
        print(f"unit_schools: {key}={list(values)} failed "
              f"({type(exc).__name__}: {str(exc).splitlines()[0]})", flush=True)
        return []
    # ! SWEPT AND CAPPED (2026-11-08 memory audit): the key is the request's
    #   filter values, and expired entries were never removed
    now = _t.time()
    if len(_UNIT_SCHOOL_CACHE) >= 500:
        for k in [k for k, v in _UNIT_SCHOOL_CACHE.items() if v[0] <= now]:
            _UNIT_SCHOOL_CACHE.pop(k, None)
        while len(_UNIT_SCHOOL_CACHE) >= 500:
            _UNIT_SCHOOL_CACHE.pop(next(iter(_UNIT_SCHOOL_CACHE)), None)
    _UNIT_SCHOOL_CACHE[ck] = (now + 600, schools)
    return schools


def _hasSchoolUnitArea():
    """Does school_unit carry `area` yet? Same posture as _hasEventKind: the
    column arrives with step 10d, and a filter on it before then is a
    no-op rather than a 500."""
    global _UNIT_AREA_PRESENT
    # ! A "NO" IS RE-ASKED. The probe used to cache False for the life of
    #   the process, and a worker that probed while step 10d was rebuilding
    #   school_unit answered "no area column" forever after: the area filter
    #   silently did nothing (owner, 2026-09-05). True is cached; False is
    #   retried every five minutes.
    import time as _t
    if _UNIT_AREA_PRESENT is True:
        return True
    if isinstance(_UNIT_AREA_PRESENT, float) and _t.time() < _UNIT_AREA_PRESENT:
        return False
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as c:
            c.execute("""SELECT 1 FROM information_schema.columns
                         WHERE table_name = 'school_unit'
                           AND column_name = 'area'""")
            present = c.fetchone() is not None
    except Exception:                                # noqa: BLE001
        present = False
    _UNIT_AREA_PRESENT = True if present else _t.time() + 300
    return present


def _hasEventKind(cur=None):
    """Does ranking_results carry event_kind yet? Probed once per process.

    Returns False on any failure, which only costs the flat-only pin until
    the next request after a rebuild; a wrong True would 400 every board."""
    global _EVENT_KIND_PRESENT
    if _EVENT_KIND_PRESENT is None:
        try:
            from database import getConn
            with getConn() as conn, conn.cursor() as c:
                c.execute("""SELECT 1 FROM information_schema.columns
                             WHERE table_name = 'ranking_results'
                               AND column_name = 'event_kind'""")
                _EVENT_KIND_PRESENT = c.fetchone() is not None
        except Exception:                            # noqa: BLE001
            return False
    return _EVENT_KIND_PRESENT


def _orderBy(f, table, tiebreak, unique_key):

    """The ORDER BY clause: a whitelist lookup plus a stable tiebreak.

    ⚠ THE TIEBREAK IS NOT DECORATION. Paging is OFFSET/LIMIT, and a sort with
      ties has no defined order between them -- so without a unique-ish second
      key, an athlete can appear on page 1 AND page 2 while another never
      appears at all. Sorting by state across 60,000 rows is exactly that case.
      Rating is the tiebreak because it is near-continuous.
    """
    expr, _ = table[f["sort"]]
    expr = _scaleExpr(f, expr)
    tb_col, _sp, tb_rest = tiebreak.partition(" ")
    tb_col = _scaleExpr(f, tb_col)
    parts = [f"{expr} {f['dir']} NULLS LAST"]
    if expr != tb_col:
        parts.append(f"{tb_col} {tb_rest}".strip())
    # ★ A FINAL UNIQUE KEY, ALWAYS. Rating is near-continuous but not unique,
    #   and with OFFSET/LIMIT paging a tie has no defined order -- so the same
    #   athlete can appear on two pages while another appears on none. It also
    #   makes _rankByCount's comparison total, which is what lets a count
    #   replace a sort.
    parts.append(unique_key)
    return ", ".join(parts)


# ------------------------------------------------------------------ #
#  2c. DEEP PAGES: SEEK FROM A STORED POSITION
# ------------------------------------------------------------------ #
#
# ★★ WHY A DEEP PAGE WAS SLOW (2026-11-08: /api/rankings 25.4 s for
#    board=performance&pool=hs_m&sport=XC&distance=5000&offset=150850, 6.6 s
#    for the ability board at offset 44,750; a crawler walks these). OFFSET
#    has no shortcut: Postgres produces every row before the page and throws
#    them away. The performance board is worse than that -- its candidate
#    stage reads (offset + limit) x CANDIDATE_FACTOR rows (4M at offset
#    100,000), sorts them all into the dedup window and then sorts the
#    survivors again. Page n cost n times page one.
#
#  ★ THE BOARD'S ORDER IS A KEY, SO A POSITION CAN BE STORED. A default board
#    (seekFamily: a pool, a sport, at most one season and one distance, the
#    rating order, nothing else) is ordered on (rating, unique id). Every
#    SEEK_EVERY-th row's key is written to board_seek by the pipeline
#    (build_board_seek.py, beside the Events presets); a page at offset X
#    starts from the stored row at or before X -- an index range read of
#    the rows after that key -- and skips fewer than SEEK_EVERY rows, so page
#    3,000 reads what page one reads plus at most a thousand index entries.
#
#  ! EXACTLY THE LIVE BOARD, ROW FOR ROW. The stored key is the key of the
#    row at that position of the board the live query builds, from the same
#    filters (_whereClauses), the same floor (floorSql), the same dedup key
#    (PERF_DUP_KEY) and the same order; the seek reads the rows at or past
#    it with the same WHERE and ORDER BY, so it returns board[pos:] and the
#    same OFFSET / LIMIT / name tail runs on it (pageThenName).
#    - THE DEDUP: the live board keeps a race's best copy among the
#      candidates (row_number over PERF_DUP_KEY, best rating first, then the
#      lower result_id). The seek keeps a row exactly when no copy of the
#      same race under the same filters beats it in that order (NOT EXISTS
#      on person_id, an indexed probe) -- the same rows, wherever the
#      better copy sits on the board.
#    - TIES: a key is only stored where it begins (a row whose key differs
#      from the one before it), so "key at or past the stored one" is
#      precisely board[pos:]. Rows with the very same key (one person's two
#      seasons at one mean on an all-years board; an XC and a TF race with
#      the same id and rating on sport=both) have no order on the live
#      board either; they occupy the same positions on both.
#    - NEVER STALE: the table says which ranking_results and athlete_season
#      it was built from (their oids) and which code (seekSig: the filters,
#      floors, distances and order it stored); anything else and every page
#      runs live, exactly as before. Past the end of a board the stored size
#      answers with no rows at all.
#
#  ⚠ AND A FILTERED BOARD STOPS AT LIVE_DEEP_MAX. A state, a school, a grade,
#    a unit, a date range, several seasons, another sort: those boards have
#    no stored positions and page live, so their cost still grows with the
#    offset. Past LIVE_DEEP_MAX rows deepPageRefusal answers with a message
#    (the route sends it as the board's error) rather than letting a crawler
#    queue up queries that each rank millions of rows. The default boards --
#    the ones people and crawlers page through -- are never refused.

SEEK_TABLE = "board_seek"
SEEK_EVERY = 1000
SEEK_VERSION = 1
_SEEK_FRESH = {"at": 0.0, "ok": False}
_SEEK_FRESH_TTL = 60.0
_SEEK_SIG = {}

# the performance board's dedup key: one physical race, its anet and tfrrs
# copies (the live query, its seek and build_board_seek all read this one)
PERF_DUP_KEY = ("sport", "person_id", "COALESCE(canon_meet_id, -result_id)",
                "round(time_seconds::numeric, 1)")

# filters that pick RACES or ROWS beyond a default board's (seekFamily)
_SEEK_NARROWING = ("state", "school", "grade", "course", "date_from", "date_to",
                   "gender", "event", "dist_min", "dist_max") + UNIT_FILTERS


def _oneYear(f):
    """(ok, year): a default board has no year or exactly one."""
    years = f.get("year")
    if not years:
        return True, None
    if isinstance(years, (list, tuple)):
        if len(years) != 1:
            return False, None
        years = years[0]
    return True, int(years)


def seekFamilyKey(board, pool, sport, year, distance, floor):
    """board_seek.fam for one default board. build_board_seek writes the
    same text from SQL ('|'-joined, '*' for no year / no distance)."""
    return "|".join((board, pool, sport,
                     "*" if year is None else str(int(year)),
                     "*" if distance is None else str(int(distance)),
                     floor))


def seekFamily(f):
    """The board_seek family this board's pages can seek in, or None.

    A DEFAULT BOARD: performance or ability, one pool, a sport (or both),
    the US scope, at most one season, at most one distance (performance),
    the rating order in its own direction, and the ability floor either the
    default one ('d') or a typed minimum of DEFAULT_FLOOR ('e3', what the
    board's own links send). Anything else returns None and pages live."""
    from season_floor import DEFAULT_FLOOR
    board = f.get("board")
    if board not in ("performance", "ability"):
        return None
    if f.get("pool") not in POOLS or f.get("sport") not in SPORTS:
        return None
    if f.get("scope", "usa") != "usa" or _scaleActive(f):
        return None
    if any(f.get(k) for k in _SEEK_NARROWING):
        return None
    if f.get("sort") != "rating" or f.get("dir") != "DESC":
        return None
    ok, year = _oneYear(f)
    if not ok:
        return None
    dist = f.get("distance")
    if board == "performance":
        if dist is not None and int(dist) not in PR_DISTANCES:
            return None
        floor = "-"
    else:
        if dist is not None or f.get("min_races") != DEFAULT_FLOOR:
            return None
        floor = "e3" if f.get("min_races_explicit") else "d"
    return seekFamilyKey(board, f["pool"], f["sport"], year, dist, floor)


def seekSig():
    """A digest of everything a stored position depends on in THIS code: the
    filters a default board emits, its floors, its order, the dedup key, the
    distance bands, SEEK_EVERY. build_board_seek stamps it on the table; a
    deploy that changes any of them makes the table stale (pages run live)
    until the next build, instead of seeking with another board's
    positions."""
    if "sig" in _SEEK_SIG:
        return _SEEK_SIG["sig"]
    import hashlib
    parts = [SEEK_VERSION, SEEK_EVERY, US_STATES, PR_DISTANCES, PR_DISTANCE_TOL,
             UNRATED_BELOW, PERF_DUP_KEY, floorSql(True), floorSql(False)]
    for board, extra in (("performance", {"distance": 5000}), ("performance", {}),
                         ("ability", {})):
        f = {"board": board, "pool": "hs_m", "sport": "XC", "scope": "usa",
             "year": [2020], "distance": None, "sort": "rating", "dir": "DESC",
             "min_races": 3, "min_races_explicit": False, "scale": "pool",
             "date_from": None, "date_to": None, **extra}
        params = {}
        parts.append(_whereClauses(f, params, with_dates=(board != "ability")))
        parts.append(sorted((k, repr(v)) for k, v in params.items()))
        parts.append(_orderBy(f, _SORTS_ABILITY, "s.mean_rating DESC", "s.person_id")
                     if board == "ability" else
                     _orderBy(f, _SORTS_PERFORMANCE, "p.speed_rating DESC NULLS LAST",
                              "p.result_id"))
    _SEEK_SIG["sig"] = hashlib.sha1(repr(parts).encode()).hexdigest()[:16]
    return _SEEK_SIG["sig"]


def seekComment(rr_oid, as_oid):
    """The table comment a fresh board_seek carries (see _seekFresh)."""
    return f"rr_oid={int(rr_oid)};as_oid={int(as_oid)};sig={seekSig()}"


def _vals(row):
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


def _seekFresh(cur, now=None):
    """board_seek exists and was built from the live ranking_results and
    athlete_season by this code. Asked once a minute per worker."""
    import time as _t
    now = _t.time() if now is None else now
    if now - _SEEK_FRESH["at"] < _SEEK_FRESH_TTL:
        return _SEEK_FRESH["ok"]
    # ! NAMED COLUMNS: a dict cursor keeps one of two same-named ones
    cur.execute("""SELECT obj_description(to_regclass(%s), 'pg_class') AS comment,
                          to_regclass('ranking_results')::oid           AS rr_oid,
                          to_regclass('athlete_season')::oid            AS as_oid""",
                (SEEK_TABLE,))
    desc, rr, sea = _vals(cur.fetchone())
    ok = (desc is not None and rr is not None and sea is not None
          and desc == seekComment(rr, sea))
    _SEEK_FRESH.update(at=now, ok=ok)
    return ok


def _seekPoint(cur, f):
    """Where this page can start, or None to page live:
       ("end",)               the board is shorter than the offset: no rows
       ("seek", pos, k1, k2)  the stored row at position pos (<= offset):
                              its rating (or sprint time) and unique id."""
    off = f.get("offset") or 0
    if off < SEEK_EVERY:
        return None                  # the first pages are cheap live
    fam = seekFamily(f)
    if fam is None or not _seekFresh(cur):
        return None
    # ! THE STAMP AGAIN, IN THE SAME STATEMENT: _seekFresh is a minute old,
    #   and a swap inside that minute must not seek with the old positions
    #   (the cached answer only spares a query of a table that is not there)
    cur.execute(f"""
        SELECT n.k2 AS n, c.pos AS pos, c.k1 AS k1, c.k2 AS k2
        FROM   (SELECT k2 FROM {SEEK_TABLE}
                WHERE  fam = %(fam)s AND pos = -1
                  AND  obj_description(%(tbl)s::regclass, 'pg_class')
                       = 'rr_oid=' || 'ranking_results'::regclass::oid::text
                         || ';as_oid=' || 'athlete_season'::regclass::oid::text
                         || ';sig=' || %(sig)s) n
        LEFT   JOIN LATERAL (
               SELECT pos, k1, k2 FROM {SEEK_TABLE}
               WHERE  fam = %(fam)s AND pos > 0 AND pos <= %(off)s
               ORDER  BY pos DESC LIMIT 1) c ON TRUE
    """, {"fam": fam, "off": off, "tbl": SEEK_TABLE, "sig": seekSig()})
    row = cur.fetchone()
    if row is None:
        return None                  # not a board it built, or no longer fresh
    n, pos, k1, k2 = _vals(row)
    if off >= int(n):
        return ("end",)
    if pos is None:
        return None                  # shorter than SEEK_EVERY, or no key start
    return ("seek", int(pos), float(k1), int(k2))


def _liveOffset(f):
    """A live board keeps the old ceiling: a deeper offset reads as
    LEGACY_MAX_OFFSET, as it did when parseFilters clamped it."""
    if (f.get("offset") or 0) > LEGACY_MAX_OFFSET:
        f["offset"] = LEGACY_MAX_OFFSET


def _pagedDefault(f):
    """A board people and crawlers page through: never refused."""
    if f.get("board") in ("performance", "ability"):
        return seekFamily(f) is not None
    if f.get("board") != "pr":
        return False
    # the best-times board: a pool (or all levels, by gender), a sport, an
    # event / distance, at most one season, the clock (or mark) order
    if f.get("scope", "usa") != "usa":
        return False
    if any(f.get(k) for k in ("state", "school", "grade", "course",
                              "date_from", "date_to") + UNIT_FILTERS):
        return False
    if not _oneYear(f)[0]:
        return False
    natural = "mark" if f.get("event") in PR_FIELD_EVENTS else "time"
    return f.get("sort") == natural and f.get("dir") == _SORTS_PR[natural][1]


def deepPageRefusal(f):
    """The message for a page too deep to rank live, or None.

    Only a board that is NOT a default one (_pagedDefault), and only past
    LIVE_DEEP_MAX rows. No ratings in it: nothing here needs the scale."""
    if (f.get("offset") or 0) <= LIVE_DEEP_MAX or _pagedDefault(f):
        return None
    return (f"This board stops at row {LIVE_DEEP_MAX:,}: with this filter or sort it "
            f"is ranked from scratch for every page, and deeper pages take too long. "
            f"Narrow it (a state, a school, a season) to see more of it, or clear "
            f"the extra filters and sort - the plain boards page all the way down.")


# ------------------------------------------------------------------ #
#  3. THE PERFORMANCE BOARD
# ------------------------------------------------------------------ #

UNRATED_BELOW = 600     # metres: the engine rates nothing shorter (a sprint ranks by time)


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

    DEEP PAGES of a default board start from a stored position instead
    (_seekPoint, "DEEP PAGES" above): same rows, an index range instead of
    offset x CANDIDATE_FACTOR candidates.
    """
    seek = _seekPoint(cur, f)
    if seek is not None and seek[0] == "end":
        return []
    if seek is None:
        _liveOffset(f)
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
    # ★ A SPRINT IS RANKED BY TIME (slow-request mail, 2026-11-05: the hs_m
    #   200 m performance board, 40 s). Ratings start at UNRATED_BELOW, so
    #   "speed_rating IS NOT NULL ... ORDER BY speed_rating DESC" walked the
    #   whole rating index for rows that cannot exist and returned nothing.
    #   Below it the candidates are the fastest times and the board their order.
    sprint = (f.get("distance") is not None and float(f["distance"]) < UNRATED_BELOW)
    # ! result_id BREAKS A TIE IN THE DEDUP (2026-11-08): two copies of one
    #   race at the same rating had no order, so which one the board showed
    #   (its result_id, meet, state) followed the plan. The lower id now, on
    #   the live board, its seek and build_board_seek alike.
    cand_where, cand_order, dup_order = (
        ("time_seconds > 0", "time_seconds ASC", "time_seconds ASC, result_id") if sprint else
        ("speed_rating IS NOT NULL", "speed_rating DESC", "speed_rating DESC, result_id"))
    if sprint:
        order = "p.time_seconds ASC, p.result_id"
    cand_cols = """sport, result_id, person_id, pool, speed_rating,
                   race_date, year, state, school, grade,
                   meet_id, div_id, canon_meet_id, time_seconds, event_id,
                   distance"""
    select = """
        SELECT p.sport, p.result_id, p.person_id,
               -- pool + distance ride along for the HS-equivalent view
               -- (pool_view.stampBoardRows in the API route).
               p.pool, p.distance,
               p.speed_rating                       AS rating,
               to_char(p.race_date, 'YYYY-MM-DD')   AS race_date,
               p.year,
               p.state, p.school, p.grade,
               p.meet_id, p.div_id, p.event_id, p.time_seconds,
               COALESCE(a.name, 'Unknown')          AS name"""

    if seek is not None:
        _tag, pos, k1, k2 = seek
        # the inner read skips to the page; the name tail then starts at 0
        params.update(seek_skip=f["offset"] - pos, seek_k1=k1, seek_k2=k2, offset=0)
        source = _perfSeekSource(cand_cols, cand_where, where, sprint)
        cur.execute(f"{select} {pageThenName('p', source, 'TRUE', order)}", params)
        return cur.fetchall()

    cur.execute(f"""
        WITH candidates AS (
            SELECT {cand_cols}
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
            WHERE  {cand_where} {where}
            ORDER  BY {cand_order}
            LIMIT  %(cand)s
        ),
        deduped AS (
            SELECT *, row_number() OVER (
                       -- ! AND THE ATHLETE (owner, 2026-09-26: "not all best
                       --   performances show"). The key is there to fold the
                       --   anet and tfrrs copies of ONE run; without the
                       --   person it also folded two DIFFERENT runners who
                       --   tied at the same meet, and one of them vanished.
                       PARTITION BY {", ".join(PERF_DUP_KEY)}
                       ORDER BY {dup_order}) AS dup_rn
            FROM candidates
        )
        {select}
        {pageThenName("p", "deduped p", "p.dup_rn = 1", order)}
    """, params)
    return cur.fetchall()


def _perfSeekSource(cols, cand_where, where, sprint):
    """The performance board from a stored key on: the rows at or past
    (%(seek_k1)s, %(seek_k2)s) in the board's order that survive the dedup,
    the first %(seek_skip)s skipped, a page and the name slack kept. Aliased
    `p`, for pageThenName.

    ! THE DEDUP AS A TEST, NOT A WINDOW: a row stays unless another copy of
      the same race (PERF_DUP_KEY) that this board's filters also keep comes
      before it in the dedup order -- exactly the row_number() = 1 of the
      live query, whose window sees every such copy (they rate higher, so
      they are among its candidates). `{where}` is unqualified, so inside the
      NOT EXISTS it binds to x, outside to r, as in the live query.
    ! `>= key` FROM A KEY THAT STARTS ITS TIE GROUP (build_board_seek stores
      no other), so the rows read are board[pos:] whatever the ties."""
    if sprint:
        at = ("r.time_seconds >= %(seek_k1)s::float8 AND (r.time_seconds > %(seek_k1)s::float8"
              " OR r.result_id >= %(seek_k2)s)")
        better = ("(x.time_seconds < r.time_seconds OR (x.time_seconds = r.time_seconds"
                  " AND x.result_id < r.result_id))")
        inner = "time_seconds ASC, result_id"
    else:
        at = ("r.speed_rating <= %(seek_k1)s::float8 AND (r.speed_rating < %(seek_k1)s::float8"
              " OR r.result_id >= %(seek_k2)s)")
        better = ("(x.speed_rating > r.speed_rating OR (x.speed_rating = r.speed_rating"
                  " AND x.result_id < r.result_id))")
        # ! DESC as the rating indexes store it (nulls first); the rows are
        #   all rated, so it is the board's DESC NULLS LAST, in index order
        inner = "speed_rating DESC, result_id"
    same_race = ("x.person_id = r.person_id"
                 " AND x.sport IS NOT DISTINCT FROM r.sport"
                 " AND COALESCE(x.canon_meet_id, -x.result_id)"
                 " = COALESCE(r.canon_meet_id, -r.result_id)"
                 " AND round(x.time_seconds::numeric, 1)"
                 " IS NOT DISTINCT FROM round(r.time_seconds::numeric, 1)")
    assert PERF_DUP_KEY == ("sport", "person_id", "COALESCE(canon_meet_id, -result_id)",
                            "round(time_seconds::numeric, 1)"), "same_race follows PERF_DUP_KEY"
    return f"""(
            SELECT {cols}
            FROM   ranking_results r
            WHERE  {cand_where} {where}
              AND  {at}
              AND  NOT EXISTS (
                     SELECT 1 FROM ranking_results x
                     WHERE  {same_race}
                       AND  {better}
                       AND  {cand_where} {where})
            ORDER  BY {inner}
            OFFSET %(seek_skip)s
            LIMIT  %(limit)s + {UNNAMED_SLACK}
        ) p"""


# ------------------------------------------------------------------ #
#  3b. THE PR BOARD
# ------------------------------------------------------------------ #

# ★★ THE PR BOARD'S CANDIDATES, ONE STORED DISTANCE AT A TIME (2026-11-06:
#    /api/rankings?board=pr&sport=XC&pool=hs_m&distance=5000 took 12.6-16.9 s
#    on the live site, every time -- MISS after MISS). The board ranks the
#    clock at a distance give or take PR_DISTANCE_TOL, so the candidate query
#    is `pool = ? AND sport = ? AND distance BETWEEN lo AND hi ORDER BY
#    time_seconds LIMIT n`. rr_pr_sport_time_idx (pool, sport, distance,
#    time_seconds) cannot hand that back in time order: a RANGE on distance
#    sits between the equalities and the ordering, so Postgres read every
#    hs_m XC 5K in the table (a parallel seq scan, ~205k pages on a 6M-row
#    stand-in) and top-N sorted it.
#
#  ★ THE RANGE HOLDS A HANDFUL OF STORED VALUES (5000, 4999.9, 5001 ...), and
#    for each ONE the index IS in time order. So: find the stored distances in
#    the range with a skip over the index (one probe each, the recursive CTE
#    below), take each one's fastest n rows in index order, and keep the
#    fastest n of those. The fastest n overall are among the per-distance
#    fastest n, so the candidate set is the same one the single query chose
#    (ties at the cut aside, which were never ordered).
#
#  ! ONLY ON THE CLOCK BOARDS -- a field event ranks by mark, through its
#    own index. pool=all walks the pools first (both indexes lead on pool).
def _prPerDistance(f, is_field):
    return (not is_field and f.get("distance") is not None
            and f.get("pool") is not None)


def _prCandidatesPerDistance(f, cols, cand_where, where, cand_order):
    """The candidates CTE body (see above). Binds the same params the plain
    query does: pool (unless 'all'), sport (unless 'both'), dist_lo,
    dist_hi, cand.

    ★ pool=all (2026-11-06: the all-levels PR boards at 4.7-7.6 s) walks the
      pools first -- each the next pool after the last, one probe on the
      pool-leading index -- then each pool's stored distances, so every
      (pool, distance) is read in time order. A row with NO pool cannot be
      walked to and is read by its own branch (pool IS NULL is an index
      condition too); it is there only so the set cannot differ from the
      plain query's."""
    sport = "" if f["sport"] == "both" else " AND sport = %(sport)s"
    if f["pool"] != "all":
        walk = f"""
            dists(p, d) AS (
                SELECT %(pool)s::text, (SELECT min(distance) FROM ranking_results
                        WHERE  pool = %(pool)s{sport}
                          AND  distance BETWEEN %(dist_lo)s AND %(dist_hi)s)
                UNION ALL
                SELECT p, (SELECT min(distance) FROM ranking_results
                           WHERE  pool = dists.p{sport} AND distance > dists.d
                             AND  distance <= %(dist_hi)s)
                FROM   dists WHERE dists.d IS NOT NULL
            )"""
        no_pool = ""
    else:
        walk = f"""
            pools(p) AS (
                SELECT min(pool) FROM ranking_results
                UNION ALL
                SELECT (SELECT min(pool) FROM ranking_results WHERE pool > pools.p)
                FROM   pools WHERE pools.p IS NOT NULL
            ),
            dists(p, d) AS (
                SELECT p, (SELECT min(distance) FROM ranking_results
                           WHERE  pool = pools.p{sport}
                             AND  distance BETWEEN %(dist_lo)s AND %(dist_hi)s)
                FROM   pools WHERE pools.p IS NOT NULL
                UNION ALL
                SELECT p, (SELECT min(distance) FROM ranking_results
                           WHERE  pool = dists.p{sport} AND distance > dists.d
                             AND  distance <= %(dist_hi)s)
                FROM   dists WHERE dists.d IS NOT NULL
            )"""
        no_pool = f"""
                UNION ALL
                (SELECT {cols}
                 FROM   ranking_results
                 WHERE  {cand_where} {where} AND pool IS NULL
                 ORDER  BY {cand_order}
                 LIMIT  %(cand)s)"""
    return f"""
            WITH RECURSIVE {walk}
            SELECT u.*
            FROM (
                SELECT c.*
                FROM   dists
                CROSS  JOIN LATERAL (
                    SELECT {cols}
                    FROM   ranking_results
                    WHERE  {cand_where} {where}
                      AND  pool = dists.p AND distance = dists.d
                    ORDER  BY {cand_order}
                    LIMIT  %(cand)s
                ) c
                WHERE  dists.d IS NOT NULL{no_pool}
            ) u
            ORDER  BY u.{cand_order}
            LIMIT  %(cand)s"""


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
    _liveOffset(f)          # pages live: the old offset ceiling
    params = {
        "cand":   (f["limit"] + f["offset"]) * CANDIDATE_FACTOR,
        "limit":  f["limit"],
        "offset": f["offset"],
    }
    where = _whereClauses(f, params, with_dates=True)
    # ★ A FIELD EVENT RANKS THE MARK, DESCENDING, and has no time. Same
    #   shape of query with the ranked column swapped: the candidate set is
    #   bounded by the ranked quantity, the dedup key rounds it, the best
    #   per person orders by it.
    is_field = f.get("event") in PR_FIELD_EVENTS
    if is_field:
        ranked, cand_where, cand_order, dedup_key = (
            "mark", "mark IS NOT NULL", "mark DESC",
            "round(mark::numeric, 2)")
        order = _orderBy(f, _SORTS_PR, "p.mark DESC", "p.result_id")
        mark_col = "p.mark"
    else:
        ranked, cand_where, cand_order, dedup_key = (
            "time_seconds",
            # ! NO PACE FLOOR HERE. A time nobody in this pool has run is
            #   kept OUT OF ranking_results by build_ranking_results'
            #   impossibleRow gate, which is where a row that is not a
            #   performance belongs -- once. Re-checking it per candidate
            #   row on every page load costs a piecewise log-interpolated
            #   curve per row on the site's slowest board, and buys
            #   nothing the build has not already done (owner, 2026-09-14).
            f"time_seconds IS NOT NULL AND time_seconds < {DNF_SENTINEL}",
            # ! result_id BREAKS A TIE (2026-11-06): two rows at one time --
            #   the candidate cut, an athlete's two races at the same PR --
            #   had no order, so which one the board kept followed the plan
            #   (a parallel scan's workers, or the per-distance read).
            "time_seconds ASC, result_id", "round(time_seconds::numeric, 1)")
        order = _orderBy(f, _SORTS_PR, "p.time_seconds ASC", "p.result_id")
        # ! NULL, NOT p.mark: an older ranking_results has no mark column,
        #   and a flat board must keep serving until step 10 rebuilds it.
        mark_col = "NULL::real"

    cand_cols = f"""sport, result_id, person_id, pool, speed_rating,
                   race_date, year, state, school, grade,
                   meet_id, div_id, canon_meet_id, time_seconds, distance,
                   event_id{", mark" if is_field else ""}"""
    # ! THE SENTINEL IS EXCLUDED IN cand_where, not filtered out later. A
    #   later filter would still let 999999 into the candidate set and waste
    #   the LIMIT on rows that cannot rank.
    if _prPerDistance(f, is_field):
        candidates = _prCandidatesPerDistance(f, cand_cols, cand_where, where,
                                              cand_order)
    else:
        candidates = f"""
            SELECT {cand_cols}
            FROM   ranking_results
            WHERE  {cand_where} {where}
            ORDER  BY {cand_order}
            LIMIT  %(cand)s"""
    sql = f"""
        WITH RECURSIVE candidates AS ({candidates}
        ),
        deduped AS (
            SELECT *, row_number() OVER (
                       PARTITION BY sport,
                                    COALESCE(canon_meet_id, -result_id),
                                    {dedup_key}
                       ORDER BY speed_rating DESC NULLS LAST) AS dup_rn
            FROM candidates
        ),
        best AS (
            SELECT *, row_number() OVER (
                       PARTITION BY person_id
                       ORDER BY {cand_order}) AS person_rn
            FROM deduped
            WHERE dup_rn = 1
        )
        SELECT p.sport, p.result_id, p.person_id, p.pool,
               p.time_seconds,
               {mark_col}                           AS mark,
               p.distance,

               p.speed_rating                       AS rating,
               to_char(p.race_date, 'YYYY-MM-DD')   AS race_date,
               p.year,
               p.state, p.school, p.grade,
               p.meet_id, p.div_id, p.event_id,
               COALESCE(a.name, 'Unknown')          AS name
        {pageThenName("p", "best p", "p.person_rn = 1", order)}
    """
    # ★ A SMALL CANDIDATE SET FIRST, ON THE DEFAULT ORDER (2026-11-06: the
    #   live plan read ~10k pages for 2,000 candidates, which held 1,061
    #   athletes for a page of 50). The candidates are the fastest rows, so
    #   the athletes among any N of them are the N-row prefix of the whole
    #   fastest-first list of athletes: when that prefix fills the page, the
    #   page is exactly the one the full set gives. When it does not (deep
    #   offsets, a pool of many-race athletes), the full CANDIDATE_FACTOR
    #   set runs, as before. Only for the clock ordered fastest first --
    #   every other sort re-orders the candidates, so it keeps the full set.
    if (not is_field and f.get("sort") == "time" and f.get("dir") == "ASC"
            and PR_FIRST_FACTOR < CANDIDATE_FACTOR):
        cur.execute(sql, dict(params, cand=(f["limit"] + f["offset"]) * PR_FIRST_FACTOR))
        rows = cur.fetchall()
        if len(rows) >= f["limit"]:
            return rows
    cur.execute(sql, params)
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

    DEEP PAGES of a default board start from a stored position (_seekPoint,
    "DEEP PAGES" above): the same WHERE and ORDER BY from a stored key on,
    in the order of the board's index, so the OFFSET skips fewer than
    SEEK_EVERY rows.
    """
    seek = _seekPoint(cur, f)
    if seek is not None and seek[0] == "end":
        return []
    if seek is None:
        _liveOffset(f)
    params = {
        "min_races": f["min_races"],
        "limit":     f["limit"],
        "offset":    f["offset"],
    }
    where = _whereClauses(f, params, with_dates=False)
    source = _abilitySource(f, params, where, precomputed=True)
    order = _orderBy(f, _SORTS_ABILITY, "s.mean_rating DESC", "s.person_id")
    base_where = floorSql(f['min_races_explicit']) + " " + where
    if seek is not None:
        # ! FROM A KEY THAT STARTS ITS TIE GROUP: the rows at or past it in
        #   (mean_rating DESC NULLS LAST, person_id) are board[pos:]. The
        #   floor keeps every unrated season out, so no NULL is skipped.
        _tag, pos, k1, k2 = seek
        params.update(offset=f["offset"] - pos, seek_k1=k1, seek_k2=k2)
        base_where += (" AND s.mean_rating <= %(seek_k1)s::float8"
                       " AND (s.mean_rating < %(seek_k1)s::float8"
                       " OR s.person_id >= %(seek_k2)s)")

    cur.execute(f"""
        SELECT s.person_id, s.sport, s.pool,
               -- ! THE STORED ACADEMIC YEAR, shown as it is stored: the
               --   boards stopped adding one for track (see _YEAR_LABEL),
               --   so there is nothing left to convert here.
               s.year,
               s.mean_rating                        AS rating,
               s.best_rating, s.n_races,
               s.state, s.school, s.grade,
               to_char(s.first_race, 'YYYY-MM-DD')  AS first_race,
               to_char(s.last_race,  'YYYY-MM-DD')  AS last_race,
               COALESCE(a.name, 'Unknown')          AS name
        {pageThenName("s", source, base_where, order)}
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
    # performance ranks by rating (higher first); pr by time (lower first),
    # or by MARK (higher first) when the pr board is a field event.
    is_pr = f["board"] == "pr"
    is_field = is_pr and f.get("event") in PR_FIELD_EVENTS
    col = ("mark" if is_field else "time_seconds" if is_pr
           else "speed_rating")
    # The comparison column as the board ORDERS it: scaled when the reader
    # is on the HS-equivalent view of a pool=all board, else the column.
    cmp = _scaleExpr(f, col)
    ascending = is_pr and not is_field
    beats = "<" if ascending else ">"

    params = {"person_id": person_id}
    where = _whereClauses(f, params, with_dates=True)

    # ! THE ATHLETE'S OWN BEST ROW UNDER THESE FILTERS, which is the row that
    #   would appear on the board. Their other races are irrelevant to where
    #   they sit.
    dnf = ("" if not ascending
           else f" AND time_seconds < {DNF_SENTINEL}")
    cur.execute(f"""
        SELECT {cmp}, result_id
        FROM   ranking_results
        WHERE  person_id = %(person_id)s AND {col} IS NOT NULL {dnf} {where}
        ORDER  BY {cmp} {"ASC" if ascending else "DESC"}, result_id
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
           "sport, person_id, COALESCE(canon_meet_id, -result_id), "
           "round(time_seconds::numeric, 1)")
    cur.execute(f"""
        SELECT count(*) FROM (
            SELECT DISTINCT {key}
            FROM   ranking_results
            WHERE  {col} IS NOT NULL {dnf} {where}
              AND  ({cmp} {beats} %(target)s
                    OR ({cmp} = %(target)s AND result_id < %(target_id)s))
        ) q
    """, params)
    return int(cur.fetchone()[0]) + 1


def countOf(cur, f):
    """How many rows the ability board holds under these filters -- the
    denominator for 'top 2%' on the athlete header. Same floor and WHERE
    as the board and rankOf, so the fraction is of the board it links to."""
    params = {"min_races": f["min_races"]}
    where = _whereClauses(f, params, with_dates=False)
    source = _abilitySource(f, params, where, precomputed=True)
    cur.execute(f"""
        SELECT count(*)
        FROM   {source}
        WHERE  {floorSql(f['min_races_explicit'])} {where}
    """, params)
    return int(cur.fetchone()[0])


# ★ THE DEFAULT BOARDS ARE COUNTED AT BUILD TIME. A board narrowed only by
#   pool and sport is most of what anyone presses Last on, and on the live
#   corpus that count is a scan of twenty million rows -- the button just
#   hung (owner, 2026-09-02). build_ranking_results writes board_size after
#   every swap; countRows reads it when the filters are exactly the default
#   and only counts live, under a time limit, for everything else.
_DEFAULT_NEUTRAL = ("state", "year", "school", "grade", "course", "event",
                    "date_from", "date_to", "gender")
COUNT_TIMEOUT_MS = 8000


def isDefaultBoard(f):
    """True when nothing but board, pool and sport narrows the rows."""
    if f.get("scope", "usa") != "usa" or f.get("min_races_explicit"):
        return False
    if f["board"] not in ("ability", "performance"):
        return False
    for k in _DEFAULT_NEUTRAL:
        if f.get(k):
            return False
    if f["board"] == "performance" and f.get("distance") is not None:
        return False
    # ⚠ AN EVENT-RESTRICTED ABILITY BOARD IS NOT THE DEFAULT ONE, and this
    #   line is load-bearing: storedBoardSize would otherwise hand back the
    #   count build_ranking_results wrote for the WHOLE board, so a 3000m+
    #   board of four thousand athletes would say "of 812,940" and every
    #   percentile on it would be wrong by two orders of magnitude.
    if eventRestricted(f):
        return False
    return True


def storedBoardSize(cur, f):
    """board_size row for a default board, or None when the table or the
    row is absent (an older build, or a pool nobody counted)."""
    try:
        cur.execute("""
            SELECT n FROM board_size
            WHERE  board = %(b)s AND pool = %(p)s AND sport = %(s)s
        """, {"b": f["board"], "p": f["pool"], "s": f["sport"]})
        row = cur.fetchone()
    except Exception:                                # noqa: BLE001
        cur.connection.rollback()
        return None
    if row is None:
        return None
    return int(row[0] if isinstance(row, (tuple, list)) else list(row.values())[0])


def countRows(cur, f, timeout_ms=COUNT_TIMEOUT_MS):
    """How long the board is under these filters, for the pager's Last
    button (?count=1), or None when it cannot be known in time.

    Each board counts what it ranks: ability counts athlete-seasons above
    the floor, performances counts rated rows, best times counts PEOPLE
    with a usable mark or time -- one row per athlete is what that board
    shows. The default boards come from board_size; the rest are counted
    live inside a statement timeout, and a board too wide to count in
    time answers None rather than holding the page."""
    if isDefaultBoard(f):
        n = storedBoardSize(cur, f)
        if n is not None:
            return n
    cur.execute("SAVEPOINT board_count")
    try:
        if timeout_ms:
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        n = _countLive(cur, f)
        cur.execute("RELEASE SAVEPOINT board_count")
        return n
    except Exception as exc:                         # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT board_count")
        if "canceling statement" in str(exc) or "timeout" in str(exc).lower():
            return None
        raise


def _countLive(cur, f):
    board = f["board"]
    if board == "ability":
        return countOf(cur, f)
    params = {}
    where = _whereClauses(f, params, with_dates=True)
    if board == "performance":
        cur.execute(f"""
            SELECT count(*) FROM ranking_results
            WHERE  speed_rating IS NOT NULL {where}
        """, params)
        return int(cur.fetchone()[0])
    if board == "pr":
        is_field = f.get("event") in PR_FIELD_EVENTS
        cand_where = ("mark IS NOT NULL" if is_field else
                      f"time_seconds IS NOT NULL AND time_seconds < {DNF_SENTINEL}")
        cur.execute(f"""
            SELECT count(DISTINCT person_id) FROM ranking_results
            WHERE  {cand_where} {where}
        """, params)
        return int(cur.fetchone()[0])
    raise ValueError(f"no count for board {board!r}")


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
    source = _abilitySource(f, params, where, precomputed=True)

    cur.execute(f"""
        SELECT s.mean_rating
        FROM   {source}
        WHERE  s.person_id = %(person_id)s
          AND  {floorSql(f['min_races_explicit'])} {where}
        ORDER  BY s.mean_rating DESC NULLS LAST
        LIMIT  1
    """, params)
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    params["target"] = row[0]

    cur.execute(f"""
        SELECT count(*)
        FROM   {source}
        WHERE  {floorSql(f['min_races_explicit'])} {where}
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
    # ! NOT WHEN THE SCALE IS ACTIVE: _rankByCount compares s.mean_rating
    #   itself, and the board is then ordered on the scaled product. The
    #   general path below reuses _orderBy, so it cannot disagree.
    if f["sort"] == "rating" and f["dir"] == "DESC" and not _scaleActive(f):
        return _rankByCount(cur, f, person_id)

    params = {
        "min_races": f["min_races"],
        "person_id": person_id,
    }
    where = _whereClauses(f, params, with_dates=False)
    source = _abilitySource(f, params, where, precomputed=True)
    order = _orderBy(f, _SORTS_ABILITY, "s.mean_rating DESC", "s.person_id")

    cur.execute(f"""
        WITH ranked AS (
            SELECT s.person_id,
                   row_number() OVER (ORDER BY {order}) AS rn
            FROM   {source}
            {nameLateral("s") if "a.name" in order else ""}
            WHERE  {floorSql(f['min_races_explicit'])} {where}
        )
        SELECT min(rn) FROM ranked WHERE person_id = %(person_id)s
    """, params)

    row = cur.fetchone()
    return None if not row or row[0] is None else int(row[0])