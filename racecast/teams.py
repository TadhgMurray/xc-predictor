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

★ POINTS ONLY MEAN SOMETHING INSIDE ONE SEASON, AND THE SORT KNOWS IT.
  team_season holds one scored meet per year, so a board showing every
  season shows one rank-1 per season -- which is TRUE, each of those teams
  did win its own year, and hiding them would be hiding the history. What
  is not true is ordering them by that rank: it puts thirty first-places at
  the top in alphabetical order, which is how the best team in the country
  landed twentieth.

  So the default sort depends on the question being asked:

    every season      TOP-5 AVERAGE RATING, descending. It is the only
                      column comparable across years -- pool-relative and
                      era-adjusted, 100 is the pool mean in 1998 and in
                      2025. The rank column then reads as "where this team
                      finished in its own season", which is what it is.

    one season chosen POINTS, ascending, which is rank order. Now every
                      team on screen raced the same field, so the lowest
                      score really is first.

  Neither is a filter change: every season is shown unless the caller
  names years, exactly like the athlete boards.
"""

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

    # ! THE DEFAULT DEPENDS ON WHETHER ONE SEASON IS ON SCREEN. See the
    #   module docstring: rank is only meaningful within a season, and the
    #   top-5 average is the only column that travels between them.
    sort = args.get("sort") or ("rank" if f["year"] and len(f["year"]) == 1
                                else "rating")
    if sort not in _SORTS:
        return None, f"sort must be one of {sorted(_SORTS)}"
    f["sort"] = sort

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
