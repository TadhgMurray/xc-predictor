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

⚠ WHICH IS EXACTLY WHY A SEASON IS ALWAYS PINNED. team_season holds one
  scored meet PER YEAR, so a board with no year filter is thirty meets
  stacked on top of each other -- thirty teams ranked 1st, and since the
  tie-break is the school name, the best team in the country sorts wherever
  the alphabet puts it among them. Measured on the first real run: about
  twenty teams shown in first place and Newbury Park down at twentieth. An
  unfiltered year is meaningless here in a way it is not on the athlete
  boards, which rank a continuous number that means the same thing in every
  season. So when the caller names no year, the newest season present is
  chosen for them.
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
        # Filled by resolveSeason when the caller named no year. STORED, not
        # the label -- it comes straight out of the table.
        "season": None,
        "limit": _boundedInt(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
        "offset": _boundedInt(args, "offset", 0, 0, 100000),
    }

    sort = args.get("sort") or "rank"
    if sort not in _SORTS:
        return None, f"sort must be one of {sorted(_SORTS)}"
    f["sort"] = sort

    direction = (args.get("dir") or "").upper()
    if direction not in ("ASC", "DESC", ""):
        return None, "dir must be asc or desc"
    f["dir"] = direction or _SORTS[sort][1]
    return f, None


def resolveSeason(cur, f):
    """Pin the board to a season when the caller named none.

    ★ THE NEWEST SEASON THAT ACTUALLY HAS TEAMS, not the newest on the wall
      clock. Asking for the current calendar year would serve an empty board
      every summer, and an empty board reads as "the feature is broken"
      rather than "that season has not been run yet".

    Sets f["season"] to the STORED year. The caller's own `year` filter, when
    present, stays in charge -- it arrives as a LABEL and _where converts it.
    """
    if f["year"]:
        return
    cur.execute("""
        SELECT max(year) FROM team_season
        WHERE  scope = %(scope)s AND pool = %(pool)s AND sport = %(sport)s
    """, {"scope": f["board_scope"], "pool": f["pool"], "sport": f["sport"]})
    row = cur.fetchone()
    f["season"] = row[0] if row else None


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
    elif f.get("season") is not None:
        # ! ALREADY THE STORED YEAR -- resolveSeason read it from the table,
        #   so it needs no label conversion.
        params["season"] = f["season"]
        parts.append(" AND t.year = %(season)s")

    params["min_athletes"] = f["min_athletes"]
    parts.append(" AND t.n_athletes >= %(min_athletes)s")
    return "".join(parts)


def getTeamRankings(cur, f):
    """One page of a team board."""
    resolveSeason(cur, f)
    params = {"limit": f["limit"], "offset": f["offset"]}
    where = _where(f, params)
    expr, _ = _SORTS[f["sort"]]
    # A stable unique tail, for the reason rankings._orderBy gives: with
    # OFFSET paging a tie has no defined order, so a team can appear on two
    # pages while another appears on none.
    order = f"{expr} {f['dir']} NULLS LAST, t.rank ASC, t.school ASC"

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
