"""
teams.py -- query builders for /api/teams.

Sits beside rankings.py and mirrors its shape: a whitelist of sort keys, every
value bound rather than interpolated, and no clause at all for a filter the
caller did not set.

★ THE BOARD IS ALREADY SCORED. team_season holds the finish order of a
  hypothetical meet per (scope, pool, sport, season) -- see team_rank.py for
  why the ranking races teams instead of averaging their ratings, and
  build_team_season.py for how the boards are built. This file only chooses
  which board to serve and slices it.

★ SO `state` PICKS A BOARD, IT DOES NOT FILTER ONE. Asking for California is
  asking for California's meet, whose points are scored against Californian
  teams only -- which is also what removes the per-state rating offset that
  makes the national board approximate. One state selected serves that
  state's board; none or several serve the national one, filtered.

⚠ AND THE RANK COLUMN IS THE BOARD'S RANK, NEVER THE ROW NUMBER. Filter the
  national board to three states and the ranks read 1, 4, 11 -- that is the
  truth about where those teams stand, and renumbering them 1, 2, 3 would
  invent a championship that was never run.

★ AND WHEN THE FILTER SPANS SEASONS, THE FIELD IS RACED AGAIN. team_season
  holds one scored meet per year, so a board showing three seasons holds
  three rank-1 rows -- each true about its own year, and useless as an
  ordering. Sorting on it puts thirty first-places at the top in
  alphabetical order, which is how the best team in the country landed
  twentieth.

  The fix is NOT to renumber the rows 1, 2, 3. That invents a championship
  nobody ran and it cannot see depth: renumbering a rating sort would rank
  a top-heavy squad over a pack that would beat it, which is the exact
  mistake team_rank.py exists to avoid.

  The fix is to hold the meet. Every team the filter selected enters its
  stored top seven, the field is sorted by rating and scored with the
  ordinary rules, and the board is that finish order -- one first place,
  because one race was run. See team_rank.raceStored.

  ⚠ IT WORKS ACROSS YEARS ONLY BECAUSE RATINGS ARE ERA-ADJUSTED: 100 is the
    pool mean in 1998 and in 2025, so a 2003 squad and a 2025 squad in one
    field is a comparison that means something.

★ SO THERE ARE TWO STORED MEETS, AND EVERY QUERY NAMES ONE. team_season's
  `span` column says which:

    span='season'   the squad's own year, scored against that year's field.
                    One winner per season -- thirty of them across the
                    table, each true about its own year.

    span='alltime'  every squad of every season of a pool in ONE field,
                    raced at build time. One winner, full stop.

  ⚠ AND MIXING THEM IS THE BUG THIS COLUMN EXISTS TO PREVENT. A query that
    forgets the span reads thirty seasons of per-season boards stacked on
    top of each other, which is thirty rows numbered 1 -- the exact
    complaint that started all of this.

  The choice is mechanical: name exactly one year and the season board is
  the answer; otherwise the all-time board is, because it is the only
  stored ranking that puts different years in one order.

⚠ THE LIVE RACE IS AN IMPROVEMENT ON THAT, NOT THE SOURCE OF IT. Filtering
  the all-time board to three seasons gives ranks like 1, 6, 23 -- a true
  order with honest gaps, since the other twenty are simply not shown. When
  the filtered field is small enough (RACE_CAP), the teams are raced again
  as their own meet so the ranks read 1, 2, 3 and the points are that
  field's. Above the ceiling the stored board serves, and raced=false tells
  the page which sentence to print. Both are true; only one is contiguous.
"""

from team_rank import raceStored, rankTeams
from rankings import (US_STATES, POOLS, SPORTS, SCOPES, MAX_LIMIT,
                      DEFAULT_LIMIT, _boundedInt, _multiValue, _multiInt,
                      MAX_MULTI, UNIT_FILTERS, UNIT_COLUMNS,
                      gradeKeySql, _gradeKey, nameLateral)

# The unit columns team_season carries (build_team_season.UNIT_COLS). A
# filter key whose columns are all outside this set is skipped rather than
# silently matching nothing -- `district` and `county` are in UNIT_COLUMNS
# for the athlete boards and are not stored here.
TEAM_UNIT_COLS = frozenset(("division", "region", "conference", "league",
                            "state_div", "section_div", "class", "area",
                            "section"))

# Each entry is (expression, natural direction) -- same contract as
# rankings._SORTS_*. Rank ascends because first place is the best.
_SORTS = {
    "rank":     ("t.rank",         "ASC"),
    "points":   ("t.points",       "ASC"),
    "school":   ("t.school",       "ASC"),
    "state":    ("t.state",        "ASC"),
    "rating":   ("t.top5_mean",    "DESC"),
    "fifth":    ("t.fifth_rating", "DESC"),
    "best":     ("t.best_rating",  "DESC"),
    "athletes": ("t.n_athletes",   "DESC"),
    "year":     ("t.year", "DESC"),
}

# ! NO 'both' AND NO 'all'. A hypothetical meet is one field: cross country
#   and track teams cannot be in it together, and neither can high school
#   boys and college women, because the places would be scored across scales
#   that are not the same scale. The other boards can afford sport='both'
#   because they rank a per-row number; this one ranks a finish order.
TEAM_SPORTS = {"XC", "TF"}


def parseFilters(args, default_year=None):
    """request.args -> (filters, error or None). Same contract as rankings."""
    sport = args.get("sport", "XC")
    if sport not in TEAM_SPORTS:
        return None, f"sport must be one of {sorted(TEAM_SPORTS)}"

    pool = args.get("pool", "hs_m")
    if pool not in POOLS:
        return None, f"pool must be one of {sorted(POOLS)}"

    scope = args.get("scope", "usa")
    if scope not in SCOPES:
        return None, f"scope must be one of {sorted(SCOPES)}"

    states = _multiValue(args, "state", upper=True)
    if states:
        unknown = [s for s in states if s not in US_STATES]
        if unknown:
            return None, f"unknown state code: {', '.join(unknown)}"

    f = {
        "sport": sport,
        "pool": pool,
        "scope": scope,
        "state": states,
        "school": _multiValue(args, "school"),
        "year": _multiInt(args, "year", 1990, 2100),
        # ★ ONE STATE MEANS THAT STATE'S MEET; anything else means the
        #   national one. See the module docstring.
        "board_scope": states[0] if states and len(states) == 1 else "usa",
        "min_athletes": _boundedInt(args, "min_athletes", 5, 5, 50),
        "limit": _boundedInt(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
        "offset": _boundedInt(args, "offset", 0, 0, 100000),
    }

    # ★ THE UNIT FILTERS WERE ON SCREEN AND NOWHERE ELSE (owner, 2026-09-08:
    #   "when you do like a div/section filter the place numbers should
    #   update"). rankings.js shows the division / region / conference /
    #   section combos by POOL, not by board (syncUnitRows keys off
    #   $("pool").value alone), so the Teams tab has always offered them --
    #   and parseFilters never read them. Picking Division III did nothing
    #   at all, silently, which is also why the ranks looked untouched.
    for key in UNIT_FILTERS:
        f[key] = _multiValue(args, key)

    # ★ RETURNING TEAMS (owner, 2026-09-08): drop the grades that are
    #   leaving and the board is next year's squad. See raceReturning.
    # ★ THE EVENT WINDOW, ON TEAMS TOO (owner, 2026-09-14: "Team rankings
    #   need to have the same event thing as ability"). A track team's
    #   rating is the top five of an aggregate over everything its athletes
    #   ran, so an 800 squad and a 5000 squad are scored on one number and
    #   neither projects an autumn. Restricting the aggregate asks the
    #   question a preseason cross-country guess is actually asking -- and
    #   it is the same question the ability board's control asks, of the
    #   same athlete-seasons, so it is the same two parameters in metres.
    #
    # ⚠ IT NEEDS A YEAR, for the reason removing grades needs one: there is
    #   no prebuilt board for a restricted window, so the field is raced
    #   live, and an all-time restricted board is thirty seasons of squads
    #   in one meet. One season is a meet; thirty is a pile.
    for key in ("dist_min", "dist_max"):
        raw = args.get(key)
        if raw in (None, ""):
            f[key] = None
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None, f"{key} must be a distance in metres"
        if not 0 < val <= 200000:
            return None, f"{key} must be between 0 and 200000 metres"
        f[key] = val
    if (f["dist_min"] is not None and f["dist_max"] is not None
            and f["dist_min"] > f["dist_max"]):
        return None, "dist_min is greater than dist_max: no event can match"
    f["exclude_grade"] = _multiValue(args, "exclude_grade")
    # the same list the athlete boards use, read here as a SELECTION: these
    # grades make the squad. See teamGradeSql for the two directions.
    f["grade"] = _multiValue(args, "grade")

    # ★ THESE FILTERS NEED ONE SEASON, AND NOW THEY PICK ONE (owner,
    #   2026-09-15: "for teams choosing events still causes an error. same
    #   with graduating"). They do genuinely need a single year -- a
    #   restricted board is raced live, and an all-time board with the
    #   seniors removed is a field of squads from thirty different seasons,
    #   which is not a question anybody is asking. But refusing was the
    #   wrong way to say so: the control cannot express the requirement, so
    #   every reader who touched it got an error instead of a board.
    #
    #   Now the requirement fills itself from `default_year` -- the current
    #   season, which is what "next year's squad" and "the 3200 board" both
    #   mean anyway -- and `year_defaulted` tells the page to show which
    #   season it landed on, so the answer is never silently about a year
    #   nobody chose.
    needs_one_year = (f["dist_min"] is not None or f["dist_max"] is not None
                      or bool(f["exclude_grade"]) or bool(f.get("grade")))
    if needs_one_year and len(f["year"] or ()) != 1:
        if len(f["year"] or ()) > 1:
            return None, ("an event or grade filter needs ONE season "
                          "selected: the board is raced live, and several "
                          "seasons at once is a field of squads from "
                          "different years")
        if not default_year:
            return None, ("an event or grade filter needs a season selected, "
                          "and no current season is known")
        f["year"] = [int(default_year)]
        f["year_defaulted"] = True

    # ★ ONE YEAR MEANS THAT YEAR'S MEET; ANYTHING ELSE MEANS THE ALL-TIME
    #   ONE. Not a preference -- the season boards cannot be stacked (thirty
    #   first places) and the all-time board answers a single-season question
    #   with the wrong field. See the module docstring.
    # ! `or ()` BECAUSE _multiInt RETURNS None, NOT AN EMPTY LIST, when the
    #   parameter is absent -- which is every default page load, so len() on
    #   it raised a TypeError on the one request that has to work.
    f["span"] = "season" if len(f["year"] or ()) == 1 else "alltime"

    # ★ THE COURSE SPECIFIER, and it changes what the board IS. team_season
    #   ranks seasons; a course has no season teams, it has team RACES. With
    #   a course set the route serves getCoursePerformances instead: every
    #   race there where a school put five rated finishers across the line,
    #   by top-5 average rating. The season machinery (year, min_athletes,
    #   scope, the raced/stored spans) has no meaning for a single race, so
    #   the filters that drive it are refused rather than silently dropped.
    f["course"] = _multiValue(args, "course")
    f["distance"] = None
    dist_raw = args.get("distance")
    if f["course"]:
        if sport != "XC":
            return None, "course team rankings are cross country; set sport=XC"
        if f["year"]:
            return None, ("year does not apply with a course; the course "
                          "view spans all seasons")
        if states:
            return None, ("state does not apply with a course; a course is "
                          "one place")
        if dist_raw:
            try:
                dist = int(dist_raw)
            except ValueError:
                return None, "distance must be a number of metres"
            # Wider than PR_DISTANCES on purpose: a course's own odd 2900m
            # is a real distance THERE, not a data-entry artefact.
            if not 1000 <= dist <= 20000:
                return None, "distance must be between 1000 and 20000 metres"
            f["distance"] = dist
    elif dist_raw:
        return None, "distance applies only when a course is set"

    # ! THE DEFAULT IS NOT KNOWN YET -- it depends on whether the field
    #   turns out to be raceable, which takes a row count. serveBoard picks
    #   it; this only records that the caller left the choice open, so a
    #   sort somebody actually clicked is never overridden.
    explicit = args.get("sort")
    sort = explicit or "rating"
    if sort not in _SORTS:
        return None, f"sort must be one of {sorted(_SORTS)}"
    f["sort"] = sort
    f["sort_explicit"] = bool(explicit)

    direction = (args.get("dir") or "").upper()
    if direction not in ("ASC", "DESC", ""):
        return None, "dir must be asc or desc"
    f["dir"] = direction or _SORTS[sort][1]
    return f, None


def _fieldWhere(f, params):
    """The predicates that say WHO IS IN THE MEET.

    ⚠ SCHOOL IS NOT ONE OF THEM, and the distinction is the whole reason this
      is two functions. A state and a season describe a POPULATION of teams
      that could plausibly line up together, so narrowing them narrows the
      field and the race is still a race. A school name is a SUBJECT: asking
      for Newbury Park is asking where Newbury Park stands, and that question
      presupposes a field for them to stand in.

      Race the school filter and every school on the site comes first. Filter
      to one team and only their own seasons enter, so their best year wins a
      meet of three and the board reads #1 -- for a squad that might have
      been fortieth in the country. That is not a subtle error; it is the
      board confidently saying the opposite of the truth.
    """
    parts = []
    # ! FIRST AND ALWAYS. Every other predicate here narrows a board; this one
    #   chooses which board is being read at all, and leaving it out reads
    #   both at once -- thirty per-season winners interleaved with the
    #   all-time ranking, which is nonsense in a way that still renders.
    params["span"] = f["span"]
    parts.append(" AND t.span = %(span)s")

    params["scope"] = f["board_scope"]
    parts.append(" AND t.scope = %(scope)s")

    params["pool"] = f["pool"]
    parts.append(" AND t.pool = %(pool)s")

    params["sport"] = f["sport"]
    parts.append(" AND t.sport = %(sport)s")

    # ! ONLY WHEN THE NATIONAL BOARD IS SERVING. On a state board the scope
    #   has already narrowed it, and a second predicate on the same column
    #   only costs a plan.
    if f["state"] and f["board_scope"] == "usa":
        params["states"] = f["state"]
        parts.append(" AND t.state = ANY(%(states)s)")

    if f["year"]:
        # ★ THE STORED ACADEMIC YEAR (2026-09-08). The Teams tab shares the
        #   /rankings page's one Year control with the athlete boards, so it
        #   moved with them -- see rankings._YEAR_LABEL. Splitting them would
        #   put two meanings behind one combo.
        params["year"] = f["year"]
        parts.append(" AND t.year = ANY(%(year)s)")

    params["min_athletes"] = f["min_athletes"]
    parts.append(" AND t.n_athletes >= %(min_athletes)s")

    # ★ A DIVISION IS A FIELD, NOT A SUBJECT -- which is what makes the
    #   ranks come out 1, 2, 3 without renumbering anything. The module
    #   docstring refuses to renumber a sliced board ("that invents a
    #   championship that was never run") and names the alternative: hold
    #   the meet. So DIII goes in HERE, with state and season, and
    #   serveBoard races the teams it selected -- one first place, because
    #   one race was run. Renumbering was never needed; the filter just
    #   had to reach the field.
    #
    # ! CONTRAST WITH school, WHICH STAYS A SUBJECT (see _subjectWhere). A
    #   division is a population that plausibly lines up together; a school
    #   name is a question about where one team stands, and racing it would
    #   put every filtered squad first.
    #
    # ★ ON THE TEAM'S OWN STAMPED COLUMN, NOT ON A LIST OF SCHOOL NAMES
    #   (owner, 2026-09-08: "DI school in DIII filter: Washington WA ...
    #   NCAA DI"). The first cut resolved a division to school NAMES out of
    #   school_unit and matched t.school against them, because team_season
    #   had no unit columns. Names are shared -- "Washington" is DI in WA
    #   and DIII in MO -- so a DIII filter pulled the DI team in, which is
    #   the same class of error the (school, state) team key exists to
    #   prevent. team_season now carries the units athlete_season already
    #   stamped per (school, state), so this is the identical column
    #   comparison the ATHLETE boards make and the two cannot disagree.
    for key in UNIT_FILTERS:
        if not f.get(key):
            continue
        cols = [c for c in UNIT_COLUMNS[key] if c in TEAM_UNIT_COLS]
        if not cols:
            continue                     # a unit this table does not carry
        params[f"{key}_vals"] = list(f[key])
        ors = " OR ".join(f't."{c}" = ANY(%({key}_vals)s)' for c in cols)
        parts.append(f" AND ({ors})")
    return "".join(parts)


def _subjectWhere(f, params):
    """The predicates that say WHICH OF THEM TO SHOW. See _fieldWhere."""
    if not f["school"]:
        return ""
    # ⚠ NOT AN EQUALITY. The chips come from /search/api, whose labels are the
    #   raw scraped school strings taken from results/results_tf -- and the
    #   copy stored here is mode() over an athlete-season's races, which can
    #   differ in case or in trailing punctuation from the one the index
    #   happened to keep. An exact match then returns an empty board, which
    #   looks like the team is missing rather than the string being spelled
    #   twice.
    params["schools"] = [s.strip().lower() for s in f["school"]]
    return " AND lower(btrim(t.school)) = ANY(%(schools)s)"


def matchesSubject(row, f):
    """The Python twin of _subjectWhere, for a board raced in memory.

    ! ONE NORMALISATION, WRITTEN TWICE, WHICH IS A RISK -- so keep them
      identical: lower, then strip. A board that answers a school search
      differently depending on whether the field was raced is worse than one
      that gets it wrong consistently.
    """
    if not f["school"]:
        return True
    wanted = {s.strip().lower() for s in f["school"]}
    return (row.get("school") or "").strip().lower() in wanted


def _where(f, params):
    """Both halves, for the stored path -- which slices a board rather than
    racing one, so there is no field to protect."""
    return _fieldWhere(f, params) + _subjectWhere(f, params)


def getTeamRankings(cur, f):
    """One page of a team board."""
    params = {"limit": f["limit"], "offset": f["offset"]}
    where = _where(f, params)
    expr, _ = _SORTS[f["sort"]]
    # A stable unique tail, for the reason rankings._orderBy gives: with
    # OFFSET paging a tie has no defined order, so a team can appear on two
    # pages while another appears on none.
    # ! THE TIE-BREAK IS POINTS THEN RANK, NOT rank ALONE. Sorting an
    #   all-seasons board by rank first is precisely the bug this file's
    #   docstring describes; as a TIE-break it is harmless and keeps paging
    #   stable, but points comes first so equal ratings order by how the
    #   teams actually scored.
    order = (f"{expr} {f['dir']} NULLS LAST, "
             "t.points ASC, t.rank ASC, t.year DESC, t.school ASC")

    cur.execute(f"""
        SELECT t.school, t.state, t.pool, t.sport,
               t.year,
               t.scope, t.rank, t.points, t.n_athletes,
               t.top5_mean, t.fifth_rating, t.best_rating
        FROM   team_season t
        WHERE  TRUE {where}
        ORDER  BY {order}
        OFFSET %(offset)s
        LIMIT  %(limit)s
    """, params)
    return cur.fetchall()


# ------------------------------------------------------------------ #
#  RACING THE FILTERED FIELD
# ------------------------------------------------------------------ #

# ⚠ THE CEILING ON A LIVE RACE, in teams. Scoring is linear in entrants and
#   this is seven entrants per team: measured on this machine, 5,000 teams
#   take 145ms, 15,000 take ~600ms and 30,000 take 1.3s. 600ms is already at
#   the edge of what belongs on a page load, so the board stops there and
#   says so rather than quietly taking a second and a half.
#
# ! ONE NATIONAL SEASON IS USUALLY UNDER IT, which is the case that matters:
#   the common cross-season queries -- a state across all history, two or
#   three years of one pool -- race. An unfiltered thirty-season national
#   board does not, and does not need to: the stored board already answers
#   the only question you can ask of it.
RACE_CAP = 15000

_FIELD_SQL_TAIL = """
        FROM   team_season t
        WHERE  TRUE {where}
"""


def countField(cur, f):
    """How many teams would enter the meet this filter describes?

    ! ASKED BEFORE THE ROWS ARE PULLED, not after. The alternative is to
      fetch RACE_CAP + 1 rows and throw them away when the field is too big,
      which spends the most bandwidth on exactly the query that was already
      going to be the slowest.
    """
    params = {}
    where = _fieldWhere(f, params)
    cur.execute("SELECT count(*) AS n" + _FIELD_SQL_TAIL.format(where=where),
                params)
    row = cur.fetchone()
    # RealDictCursor here, a tuple cursor in a test -- accept both rather
    # than assuming, which is the bug that made this route answer with an
    # HTML debug page once already.
    return int(row["n"] if isinstance(row, dict) else row[0])


def getTeamField(cur, f):
    """Every team the filter selects, with the seven ratings that race.

    NO ORDER AND NO PAGING. The whole field has to be in hand before it can
    be scored -- score page two on its own and its teams race each other
    instead of the field, and the winner of page two comes out first.
    """
    params = {"cap": RACE_CAP}
    where = _fieldWhere(f, params)
    cur.execute(f"""
        SELECT t.school, t.state, t.pool, t.sport,
               t.year,
               t.scope, t.rank, t.points, t.n_athletes,
               t.top5_mean, t.fifth_rating, t.best_rating, t.ratings
        {_FIELD_SQL_TAIL.format(where=where)}
        LIMIT %(cap)s
    """, params)
    return cur.fetchall()


# The Python half of _SORTS: same columns, same natural directions, used when
# the board was raced in memory and there is no ORDER BY to do the work.
# ! NONE IS SORTED LAST IN BOTH DIRECTIONS, matching SQL's NULLS LAST -- so a
#   team with no fifth runner does not lead the board when you sort ascending.
_SORT_KEYS = {
    "rank":     lambda r: r["rank"],
    "points":   lambda r: r["points"],
    "school":   lambda r: (r["school"] or "").lower(),
    "state":    lambda r: r["state"] or "",
    "rating":   lambda r: r["top5_mean"],
    "fifth":    lambda r: r["fifth_rating"],
    "best":     lambda r: r["best_rating"],
    "athletes": lambda r: r["n_athletes"],
    "year":     lambda r: r["year"],
}


def sortAndPage(rows, f):
    """Order a raced board the way the SQL path would, then cut one page.

    ! NULLS ARE SPLIT OUT RATHER THAN ENCODED IN THE KEY. The obvious
      `(value is None, value)` trick breaks under reverse=True, which flips
      the flag too and floats every team with no fifth runner to the top of
      a descending sort -- the opposite of the NULLS LAST the SQL path gets.

    ! AND THE TIE-BREAK IS A FIRST PASS, NOT PART OF THE KEY, because half
      these columns are strings and cannot be negated into a descending
      composite. Python's sort is stable and its reverse=True does not
      reorder equal elements, so sorting by the tie-break and then by the
      column leaves the tie-break intact underneath -- the same order SQL's
      `ORDER BY <col> <dir>, points, rank, year DESC, school` produces.
    """
    keyOf = _SORT_KEYS[f["sort"]]
    descending = f["dir"] == "DESC"

    def tieBreak(r):
        return (r["points"], r["rank"], -(r["year"] or 0), r["school"] or "")

    present = sorted((r for r in rows if keyOf(r) is not None), key=tieBreak)
    missing = sorted((r for r in rows if keyOf(r) is None), key=tieBreak)
    present.sort(key=keyOf, reverse=descending)

    ordered = present + missing
    start = f["offset"]
    return ordered[start:start + f["limit"]], len(ordered)


# ------------------------------------------------------------------ #
#  RETURNING TEAMS -- the board with some grades taken out
# ------------------------------------------------------------------ #
#
# ★ WHY (owner, 2026-09-08): "I think you should be able to get team
#   rankings by removing certain grades." Drop the seniors and what is left
#   is next year's squad, which is the question a coach asks in September
#   and the stored board cannot answer.
#
# ⚠ AND THE STORED BOARD REALLY CANNOT. team_season.ratings is seven bare
#   numbers -- no person, no grade -- so there is nothing to take a senior
#   OUT of. Racing the stored seven minus "the last two" would be a guess
#   about which runners the grades belonged to. So this path goes back to
#   athlete_season, where a row is one athlete and carries their grade,
#   and re-scores from there with the same rankTeams the build uses.
#
# ! WHICH MAKES IT A DIFFERENT SHAPE OF QUERY, and the cap is on ATHLETES
#   rather than teams. rankTeams is linear in entrants and this hands it
#   every eligible athlete-season, not a pre-trimmed seven per team.
RETURN_CAP = 300000

# ! PINNED TO THE BUILD BY TEST. build_team_season uses this floor when it
#   decides who is eligible for a team; a different one here would rank a
#   different squad from the one the ordinary board shows.
TEAM_MIN_RACES = 2          # build_team_season.MIN_RACES


def gradeExcluded(f):
    """Is the board being asked for a squad built from, or minus, some
    grades? Either way team_season cannot answer it -- that table stores
    finished squads with no person and no grade behind them -- so both
    send the board down the live athlete_season path."""
    return bool(f.get("exclude_grade")) or bool(f.get("grade"))


# ⚠ athlete_season DOES NOT RELIABLY CARRY THE UNIT COLUMNS, and this path
#   is the only one that reads them off it. team_season is built with all
#   nine, so _fieldWhere can name them unconditionally; athlete_season gets
#   them from a migration that has run on some databases and not others --
#   which is exactly why the ability board probes (rankings._rowHasUnit)
#   instead of trusting them. The returning board named all nine in its
#   SELECT list with no probe at all, so on a database missing one, every
#   "Graduating (removed)" request is an UndefinedColumn 500 (owner,
#   2026-09-14: "The teams ranking page graduating thing doesn't work
#   (errors)").
#
# ! PRESENCE, NOT POPULATED-NESS. _rowHasUnit also demands values, which is
#   right for a FILTER -- an all-NULL column matches nobody and the semi-join
#   it replaced would have answered. For a SELECT list the only question is
#   whether naming the column raises, so this is the weaker test on purpose.
_ATHLETE_COLS = {"at": 0.0, "cols": frozenset()}
_ATHLETE_COLS_TTL = 600


def athleteUnitCols():
    """The TEAM_UNIT_COLS that athlete_season actually has. Cached for ten
    minutes, so a column that arrives with a rebuild is picked up without a
    restart; an unreachable database answers "none" rather than raising."""
    import time as _t
    if _t.time() - _ATHLETE_COLS["at"] <= _ATHLETE_COLS_TTL:
        return _ATHLETE_COLS["cols"]
    cols = frozenset()
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public'
                             AND table_name = 'athlete_season'""")
            present = {r[0] if not isinstance(r, dict) else r["column_name"]
                       for r in cur.fetchall()}
        cols = frozenset(TEAM_UNIT_COLS & present)
    except Exception:                                     # noqa: BLE001
        pass
    _ATHLETE_COLS.update({"at": _t.time(), "cols": cols})
    return cols


def _athleteFieldWhere(f, params):
    """The same population _fieldWhere selects, expressed against
    athlete_season -- which is where the grades are."""
    parts = [" AND s.pool = %(pool)s", " AND s.sport = %(sport)s",
             " AND s.mean_rating IS NOT NULL",
             " AND s.n_races >= %(min_races)s"]
    params["pool"] = f["pool"]
    params["sport"] = f["sport"]
    params["min_races"] = TEAM_MIN_RACES

    # ! ONE YEAR, ALWAYS. parseFilters refuses the filter without one --
    #   "next year's team" is a question about a season, and an all-time
    #   board with the seniors removed is not a thing anybody wants.
    params["year"] = f["year"]
    parts.append(" AND s.year = ANY(%(year)s)")

    for key in UNIT_FILTERS:
        if not f.get(key):
            continue
        # the columns this table HAS -- naming one it does not is a 500,
        # and a unit it cannot answer is better skipped than fatal
        have = athleteUnitCols()
        cols = [c for c in UNIT_COLUMNS[key]
                if c in TEAM_UNIT_COLS and c in have]
        if not cols:
            continue
        params[f"{key}_vals"] = list(f[key])
        ors = " OR ".join(f's."{c}" = ANY(%({key}_vals)s)' for c in cols)
        parts.append(f" AND ({ors})")

    # ★ EXCLUDED BY MEANING, NOT BY SPELLING. The feeds write a senior as
    #   12, 12th, Sr, Senior, SR-4 or 16, and rankings._gradeKey is the one
    #   place that already knows they are the same person. Comparing raw
    #   text would leave every senior a feed spelled "Sr" in the returning
    #   squad -- the same bug the athlete board's grade filter had
    #   (owner, 2026-09-07: "grade filter is removing ppl it shouldn't").
    grade_sql = gradeKeySql("s")

    # ★ ONE GRADE CONTROL, TWO DIRECTIONS (owner, 2026-09-15: "grade and
    #   graduating should be the same [control] ... You should be able to
    #   press for example all freshman teams"). Selecting grades BUILDS the
    #   squad from them -- tick 9 and the board is every school's freshman
    #   team -- and "next year's squad" is the same control used the other
    #   way, by not ticking the seniors. exclude_grade stays for the links
    #   already in the wild.
    if f.get("grade"):
        params["in_grade_keys"] = [_gradeKey(v) for v in f["grade"]]
        # ⚠ AND HERE A ROW WITH NO GRADE GOES OUT, which is the opposite of
        #   the rule below and is right for the opposite reason. "Every
        #   freshman team" is a claim about who IS a freshman; an unknown
        #   grade is not evidence of one, and letting it in would pad every
        #   squad with whoever the feed forgot to grade.
        parts.append(f" AND {grade_sql} = ANY(%(in_grade_keys)s)")

    if f.get("exclude_grade"):
        # ★ EXCLUDED BY MEANING, NOT BY SPELLING. The feeds write a senior as
        #   12, 12th, Sr, Senior, SR-4 or 16, and rankings._gradeKey is the one
        #   place that already knows they are the same person. Comparing raw
        #   text would leave every senior a feed spelled "Sr" in the returning
        #   squad -- the same bug the athlete board's grade filter had
        #   (owner, 2026-09-07: "grade filter is removing ppl it shouldn't").
        params["ex_grade_keys"] = [_gradeKey(v) for v in f["exclude_grade"]]
        # ! A ROW WITH NO GRADE STAYS IN. An unknown grade is not evidence that
        #   the athlete is leaving, and dropping them would quietly shrink every
        #   team whose feed is thin on grades -- which is most older seasons.
        parts.append(f" AND ({grade_sql} IS NULL"
                     f"      OR {grade_sql} <> ALL(%(ex_grade_keys)s))")
    return "".join(parts)


def eventRestricted(f):
    """Is the board being asked for a slice of the season's events?"""
    return f.get("dist_min") is not None or f.get("dist_max") is not None


def _athleteSource(f, params):
    """(FROM fragment aliased `s`, the unit columns it exposes).

    ★ athlete_season HAS ALREADY AGGREGATED THE EVENTS AWAY, so it cannot
      answer an event question -- the ability board solved this once and
      rankings._abilitySource is that solution: the same season estimator
      recomputed over ranking_results, which carries a distance per row.
      Reusing it is what keeps a restricted TEAM board scored on the same
      numbers as the restricted ATHLETE board; a second aggregate here
      would be a second answer.

    ! THE UNIT COLUMNS COME FROM WHICHEVER SHAPE IS IN USE. The prebuilt
      table's set is presence-probed (athleteUnitCols); the subquery's is
      whatever _abilitySource chose to emit, and naming one it left out is
      the same UndefinedColumn this file just learned about."""
    if not eventRestricted(f):
        return "athlete_season s", athleteUnitCols()
    from rankings import _abilitySource, _rowHasUnit
    # ! UNQUALIFIED, because that is what _abilitySource's inner query
    #   takes -- it filters `r` and these predicates are reused verbatim.
    inner = (" AND pool = %(pool)s AND sport = %(sport)s"
             " AND year = ANY(%(year)s)")
    have = frozenset(c for c in TEAM_UNIT_COLS
                     if _rowHasUnit(c, "athlete_season"))
    return _abilitySource(f, params, inner), have


def getReturningField(cur, f):
    """Athlete-seasons for the selected teams, minus the excluded grades."""
    params = {"cap": RETURN_CAP}
    where = _athleteFieldWhere(f, params)
    # ! NULL FOR A COLUMN THIS DATABASE HAS NOT GOT, not a missing key: every
    #   row rankTeams sees has the same shape whichever database it came
    #   from, so a downstream reader cannot start KeyError-ing on one box.
    source, have = _athleteSource(f, params)
    units = "".join((f', s."{c}"' if c in have else f', NULL::text AS "{c}"')
                    for c in sorted(TEAM_UNIT_COLS))
    cur.execute(f"""
        SELECT s.person_id, s.school, s.state, s.grade, s.pool,
               s.mean_rating AS rating{units},
               COALESCE(a.name, 'Unknown') AS name
        FROM   {source}
        {nameLateral("s")}
        WHERE  TRUE {where}
        LIMIT  %(cap)s
    """, params)
    return cur.fetchall()


def raceReturning(cur, f):
    """The returning board: (rows, field_size) or (None, n) when too big."""
    rows = [dict(r) for r in getReturningField(cur, f)]
    if len(rows) >= RETURN_CAP:
        return None, len(rows)

    # ★ THE SAME STEPS THE BUILD TAKES, IN THE SAME ORDER, or this board keys
    #   its teams differently from the one beside it: teamState moves each row
    #   from the state it RACED in to the one its school belongs to. Skip it
    #   and BYU is three teams here and one on the ordinary board.
    #
    # ⚠ AND THE POOL CEILING IS GONE FROM BOTH, TOGETHER (owner, 2026-09-09:
    #   "There probably shouldn't be a straight 150 cap btw ... just remove it
    #   for now flag if an issue later"). It used to drop implausible runners
    #   here exactly as build_team_season.railCheckedRows dropped them there.
    #   Removing it from one alone is worse than leaving it in both: the
    #   returning board would score a squad on five runners while the
    #   ordinary board scored it on six, and the two would disagree about a
    #   team that had not changed. If the rail comes back, it comes back in
    #   both places on the same day.
    from school_identity import teamState, loadLabels
    from database import getConn
    loadLabels(getConn)
    field = []
    for r in rows:
        r["state"] = teamState(r.get("school"), r.get("pool"), r.get("state"))
        field.append(r)

    # a state board, or the national one narrowed to some states
    if f["board_scope"] != "usa":
        field = [r for r in field if r["state"] == f["board_scope"]]
    elif f["state"]:
        wanted = set(f["state"])
        field = [r for r in field if r["state"] in wanted]

    return rankTeams(field), len(field)


def serveBoard(cur, f):
    """One page of the team board, raced if the field fits. -> (rows, info)

    ★ THE DECISION LIVES HERE AND NOWHERE ELSE, so the route cannot serve a
      raced board while the page explains a stored one. `info` carries what
      the answer means -- whether a meet was actually run, how big the field
      was, and the sort that was used -- and the page renders from that
      rather than guessing at the same rule a second time in JavaScript.

    ⚠ THE SORT IS RANK EITHER WAY, and that is only safe because both
      stored spans are single rankings -- see the module docstring. When the
      stack of per-season boards was being served as one board, rank order
      meant thirty first places sorted alphabetically, and the default had
      to be top-5 average rating to avoid it. The span column removed the
      reason for that workaround.

      A sort the user clicked is never overridden; see parseFilters.
    """
    # ★ THE RETURNING BOARD IS ITS OWN PATH, decided first. It cannot come
    #   from team_season at all -- see raceReturning -- so the stored-board
    #   fallback below has nothing to fall back TO, and a field too big is
    #   an honest refusal rather than a board of the wrong squads.
    # ! AND AN EVENT WINDOW TAKES THE SAME PATH, for the same reason: there
    #   is no prebuilt board for a restricted window either. team_season
    #   holds one scored meet per season computed on the WHOLE season's
    #   ratings, so serving it under an event filter would answer a
    #   question nobody asked with numbers that ignore the filter.
    if gradeExcluded(f) or eventRestricted(f):
        raced, size = raceReturning(cur, f)
        note = {"returning": gradeExcluded(f),
                "event_window": eventRestricted(f),
                "dist_min": f.get("dist_min"), "dist_max": f.get("dist_max")}
        if raced is None:
            return [], {"raced": False, "field_size": size,
                        "shown_of_field": None, "span": f["span"],
                        "reason": "cap", "total": None,
                        "race_cap": RETURN_CAP, "unscored": 0, **note}
        if not f["sort_explicit"]:
            f["sort"], f["dir"] = "rank", "ASC"
        shown = [r for r in raced if matchesSubject(r, f)]
        rows, total = sortAndPage(shown, f)
        return rows, {"raced": True, "field_size": len(raced),
                      "shown_of_field": len(shown), "span": f["span"],
                      "reason": None, "total": total,
                      "race_cap": RETURN_CAP, "unscored": 0,
                      # the page says which grades came out, so a reader
                      # cannot mistake this for the ordinary board
                      "excluded_grades": list(f["exclude_grade"]), **note}

    size = countField(cur, f)
    # ⚠ TWO DIFFERENT REASONS TO FALL BACK, and the page has to tell them
    #   apart: "narrow your filter" is useless advice when the real problem
    #   is that the table has no ratings column yet and no filter will help.
    reason = "cap" if size > RACE_CAP else None

    if size <= RACE_CAP:
        raced = raceStored(getTeamField(cur, f))
        # None means the table predates the ratings column. Not an error --
        # the stored board below is still a true answer, and telling somebody
        # to rebuild is better done in the note than in a 500.
        if raced is None:
            reason = "unbuilt"
        else:
            if not f["sort_explicit"]:
                f["sort"], f["dir"] = "rank", "ASC"
            # ★ RACED FIRST, FILTERED SECOND. The whole field runs the meet
            #   and only then are the searched schools picked out of it, so
            #   the rank a search returns is the rank in the field -- not the
            #   rank among the rows that happened to match the search.
            shown = [r for r in raced if matchesSubject(r, f)]
            rows, total = sortAndPage(shown, f)
            return rows, {"raced": True, "field_size": len(raced),
                          "shown_of_field": len(shown), "span": f["span"],
                          "reason": None, "total": total,
                          "race_cap": RACE_CAP,
                          # Teams that entered but could not score -- fewer
                          # than five ratings stored. Should be none; said
                          # out loud so it is noticed if it ever is not.
                          "unscored": size - len(raced)}

    # ! RANK, ASCENDING, ON BOTH STORED BOARDS -- which it could not be
    #   before there was an all-time one. Sorting a stack of per-season
    #   boards by rank is what put thirty first places at the top in
    #   alphabetical order; a span is a single ranking, so its own order is
    #   the right one. Filtering it leaves gaps, and gaps are the truth.
    if not f["sort_explicit"]:
        f["sort"], f["dir"] = "rank", "ASC"
    return getTeamRankings(cur, f), {"raced": False, "field_size": size,
                                     "shown_of_field": None, "span": f["span"],
                                     "reason": reason, "total": None,
                                     "race_cap": RACE_CAP, "unscored": 0}


# ------------------------------------------------------------------ #
#  SINGLE RACES AT A COURSE
# ------------------------------------------------------------------ #

def getCoursePerformances(cur, f):
    """Every team race at a course: one row per (race, school) where the
    school put five rated finishers across the line, ranked by top-5
    average speed rating. The course page's team tables show each school's
    best; this is their view-all.

    Runs on ranking_results, same as the individual course boards, so the
    pool filter means what it means everywhere else -- and the same
    cross-feed dedup applies: anet and tfrrs both carry the same physical
    race, keyed by canon_meet_id, so the same squad's same race is kept
    once (rounded top-5 mean breaks the tie the same way the performance
    board rounds time_seconds).
    """
    params = {"course": f["course"], "dist": f.get("distance"),
              "pool": f["pool"], "limit": f["limit"], "offset": f["offset"]}
    school_sql = ""
    if f["school"]:
        # The same lookup-not-a-filter posture as _subjectWhere, and the
        # same normalisation.
        params["schools"] = [s.strip().lower() for s in f["school"]]
        school_sql = " AND lower(btrim(school)) = ANY(%(schools)s)"

    cur.execute(f"""
        WITH finishers AS (
            SELECT meet_id, div_id, school, speed_rating, race_date,
                   canon_meet_id, result_id,
                   round(distance)::int AS distance,
                   row_number() OVER (PARTITION BY meet_id, div_id, school
                                      ORDER BY speed_rating DESC) AS tn
            FROM   ranking_results
            WHERE  sport = 'XC'
              AND  pool = %(pool)s
              AND  speed_rating IS NOT NULL
              AND  NULLIF(btrim(school), '') IS NOT NULL
              AND  EXISTS (SELECT 1 FROM meets mm
                           WHERE mm.meet_id = ranking_results.meet_id
                             AND mm.div_id  = ranking_results.div_id
                             AND mm.course_name = ANY(%(course)s))
              AND  (%(dist)s::int IS NULL
                    OR round(distance)::int = %(dist)s)
              {school_sql}
        ),
        squads AS (
            SELECT meet_id, div_id, school,
                   avg(speed_rating) FILTER (WHERE tn <= 5) AS top5_mean,
                   min(race_date) AS race_date,
                   min(distance)  AS distance,
                   -- min() skips NULLs, so any keyed feed's id wins; a race
                   -- no feed keyed gets a negative stand-in that cannot
                   -- collide with a real id.
                   COALESCE(min(canon_meet_id), -min(result_id)) AS race_key
            FROM   finishers
            GROUP  BY meet_id, div_id, school
            HAVING count(*) >= 5
        ),
        deduped AS (
            SELECT *, row_number() OVER (
                       PARTITION BY school, race_key,
                                    round(top5_mean::numeric, 1)
                       ORDER BY top5_mean DESC) AS dup_rn
            FROM squads
        )
        SELECT s.school, s.top5_mean, s.distance, s.meet_id, s.div_id,
               to_char(s.race_date, 'YYYY-MM-DD') AS date,
               (SELECT min(mm.meet_name) FROM meets mm
                 WHERE mm.meet_id = s.meet_id
                   AND mm.div_id  = s.div_id) AS meet_name
        FROM   deduped s
        WHERE  s.dup_rn = 1
        ORDER  BY s.top5_mean DESC NULLS LAST, s.race_date DESC, s.school
        OFFSET %(offset)s
        LIMIT  %(limit)s
    """, params)
    return cur.fetchall()
