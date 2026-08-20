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

⚠ THE RACE HAS A CEILING, AND THE BOARD SAYS SO WHEN IT HITS IT. Scoring is
  linear in entrants but the constant is real -- about 600ms for 15,000
  teams, and an unfiltered national board across thirty seasons is far more
  than that. Past RACE_CAP the stored per-season board is served instead,
  ordered by top-5 average rating (the one column comparable between
  years), with the response saying raced=false so the page can explain
  itself. Falling back is not a loss of truth: those ranks are real, they
  just answer "where in its own season" instead of "where in this field".
"""

from team_rank import raceStored
from rankings import (US_STATES, POOLS, SPORTS, SCOPES, MAX_LIMIT,
                      DEFAULT_LIMIT, _boundedInt, _multiValue, _multiInt,
                      MAX_MULTI)

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
    "year":     ("(CASE WHEN t.sport = 'TF' THEN t.year + 1 ELSE t.year END)",
                 "DESC"),
}

# ! NO 'both' AND NO 'all'. A hypothetical meet is one field: cross country
#   and track teams cannot be in it together, and neither can high school
#   boys and college women, because the places would be scored across scales
#   that are not the same scale. The other boards can afford sport='both'
#   because they rank a per-row number; this one ranks a finish order.
TEAM_SPORTS = {"XC", "TF"}


def parseFilters(args):
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


def _where(f, params):
    parts = []
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

    if f["school"]:
        # ⚠ NOT AN EQUALITY. The chips come from /search/api, whose labels are
        #   the raw scraped school strings -- and the copy stored here is
        #   mode() over an athlete-season's races, which can differ in case or
        #   in trailing punctuation from the one the index happened to keep.
        #   An exact match then returns an empty board and looks like the team
        #   is missing rather than the string being spelled twice.
        params["schools"] = [s.strip().lower() for s in f["school"]]
        parts.append(" AND lower(btrim(t.school)) = ANY(%(schools)s)")

    if f["year"]:
        # Two indexable branches, not a CASE per row -- rankings._whereClauses
        # explains why. A TF season is named for the year it ENDS in.
        params["year"] = f["year"]
        params["year_tf"] = [y - 1 for y in f["year"]]
        parts.append(" AND ((t.sport = 'TF' AND t.year = ANY(%(year_tf)s))"
                     "      OR (t.sport <> 'TF' AND t.year = ANY(%(year)s)))")

    params["min_athletes"] = f["min_athletes"]
    parts.append(" AND t.n_athletes >= %(min_athletes)s")
    return "".join(parts)


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
               (CASE WHEN t.sport = 'TF' THEN t.year + 1
                     ELSE t.year END)  AS year,
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
    where = _where(f, params)
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
    where = _where(f, params)
    cur.execute(f"""
        SELECT t.school, t.state, t.pool, t.sport,
               (CASE WHEN t.sport = 'TF' THEN t.year + 1
                     ELSE t.year END)  AS year,
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


def serveBoard(cur, f):
    """One page of the team board, raced if the field fits. -> (rows, info)

    ★ THE DECISION LIVES HERE AND NOWHERE ELSE, so the route cannot serve a
      raced board while the page explains a stored one. `info` carries what
      the answer means -- whether a meet was actually run, how big the field
      was, and the sort that was used -- and the page renders from that
      rather than guessing at the same rule a second time in JavaScript.

    ⚠ THE SORT IS DECIDED AFTER THE COUNT, because the two paths want
      different defaults and only a count can tell them apart:

        raced      RANK, ascending. Every team on screen was in the same
                   field, so the finish order is the board.

        stored     TOP-5 AVERAGE RATING, descending -- the only column
                   comparable between seasons. The rank column then reads
                   "where this team finished in its own year".

      A sort the user clicked is never overridden; see parseFilters.
    """
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
            rows, total = sortAndPage(raced, f)
            return rows, {"raced": True, "field_size": len(raced),
                          "reason": None, "total": total,
                          "race_cap": RACE_CAP,
                          # Teams that entered but could not score -- fewer
                          # than five ratings stored. Should be none; said
                          # out loud so it is noticed if it ever is not.
                          "unscored": size - len(raced)}

    if not f["sort_explicit"]:
        f["sort"], f["dir"] = "rating", "DESC"
    return getTeamRankings(cur, f), {"raced": False, "field_size": size,
                                     "reason": reason, "total": None,
                                     "race_cap": RACE_CAP, "unscored": 0}
