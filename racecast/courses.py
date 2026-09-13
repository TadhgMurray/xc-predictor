"""
courses.py -- query builders for /api/courses.

Sits beside rankings.py and teams.py and mirrors their shape: a whitelist of
sort keys, every value bound rather than interpolated, and no clause at all
for a filter the caller did not set.

★ THE BOARD RANKS THE GROUND, NOT THE RUNNERS. course_rank holds one row per
  cross country course, and the number it sorts on is the engine's own
  difficulty -- re-centred to mean zero on every iteration of the solve, so it
  reads as "how much harder than an average course this is". +0.05 means a
  field runs 5% slower here than the same field would on neutral ground.

★ AND IT IS DISTANCE-NEUTRAL BY CONSTRUCTION, which is the only reason a
  single list of courses makes sense. Difficulty is applied AFTER distance
  normalisation, so an 8k does not read as hard merely for being long, and a
  5k and a 10k belong in the same ranking.

⚠ THE SIGN IS THE THING PEOPLE GET BACKWARDS, so the board never shows a bare
  number. Positive is HARDER -- slower times, and a rating that has to give
  them back the difference. The default sort is hardest first, because "what
  is the toughest course in the country" is the question this board exists to
  answer; ascending gives the fastest ground instead.

⚠ AND A DIFFICULTY FROM FEW RACES IS AN ANECDOTE. The builder already refuses
  anything under its MIN_RESULTS, and n_results and n_athletes ride along on
  every row so the reader can weigh what is behind the number rather than
  trusting three digits.
"""

import sys

sys.path.insert(0, "racecast")
from rankings import (US_STATES, MAX_LIMIT, DEFAULT_LIMIT,
                      _boundedInt, _multiValue)

# ! EVERY SORT IS A WHITELISTED COLUMN AND ITS NATURAL DIRECTION. The natural
#   direction is what the column is FOR: hardest course first, most-raced
#   first, but a name ascending. Sending DESC blindly would list courses from
#   Z and call it a ranking.
_SORTS = {
    "difficulty": ("c.difficulty", "DESC"),
    "name":       ("lower(c.course_name)", "ASC"),
    "state":      ("c.state", "ASC"),
    "distance":   ("c.distance_m", "DESC"),
    "results":    ("c.n_results", "DESC"),
    "athletes":   ("c.n_athletes", "DESC"),
    "meets":      ("c.n_meets", "DESC"),
}

# The distances a cross country course is actually raced at, for the filter.
# Anything else is a one-off and is reachable through the search box.
DISTANCES = (3000, 3200, 4000, 4828, 5000, 6000, 6437, 8000, 8047, 10000)


# ★ ONE COURSE HAS ONE URL, AND course_difficulties DOES NOT HOLD IT.
#   That table is keyed the way the ENGINE keys a cell -- "XC:<venue>",
#   and since --era-years also "XC:<venue>@e2" once per two-year era. The
#   site links to the bare venue name. Anything building a URL or a search
#   row off that table has to come through here or it emits a URL the site
#   never links to, once per era (2026-09-13: the sitemap was doing both,
#   and the search box was listing every course several times).
def courseDisplayName(key):
    """"XC:Crystal Springs@e3" -> "Crystal Springs". The name the site's
    own links use, and the only thing that belongs in a URL."""
    name = str(key or "").strip()
    for prefix in ("XC:", "TF:"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.partition("@e")[0].strip()


def parseFilters(args):
    """Read the query string into a bound-parameter dict, or (None, error)."""
    states = _multiValue(args, "state", upper=True)
    if states:
        bad = [s for s in states if s not in US_STATES]
        if bad:
            return None, f"unknown state: {', '.join(sorted(bad)[:5])}"

    f = {
        "state": states,
        "name": (args.get("name") or "").strip() or None,
        # ⚠ A DISTANCE FILTER IS A BAND, NOT AN EQUALITY. A "5k" course is
        #   stored as 5000 at one meet and 4989 or 5030 at another, because
        #   the value is scraped per meet and the modal one wins by a nose.
        #   An equality on 5000 would drop courses everybody calls a 5k.
        "distance": _boundedInt(args, "distance", 0, 0, 100000) or None,
        "min_results": _boundedInt(args, "min_results", 0, 0, 10 ** 7),
        "limit": _boundedInt(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
        "offset": _boundedInt(args, "offset", 0, 0, 100000),
    }

    sort = args.get("sort") or "difficulty"
    if sort not in _SORTS:
        return None, f"sort must be one of {sorted(_SORTS)}"
    f["sort"] = sort
    direction = (args.get("dir") or "").upper()
    if direction not in ("ASC", "DESC", ""):
        return None, "dir must be asc or desc"
    f["dir"] = direction or _SORTS[sort][1]
    return f, None


# How far from the named distance a course may sit and still count as that
# distance. 3% of 5000 is 150m -- wider than any scrape wobble, narrower than
# the gap to the next rung of the ladder (5000 to 6000 is 20%).
_DISTANCE_TOL = 0.03


def _band(d):
    """The (lo, hi) a course must sit in to count as the distance `d`.

    ⚠ 3% IS TOO WIDE FOR ONE PAIR OF RUNGS AND ONLY ONE: 8000 and 8047 are
      0.6% apart, so a flat band around 5 miles swallows every 8k and the
      "5 miles" filter answers with Terre Haute. Two real, differently-raced
      distances are not one bucket.

    ★ SO THE BAND IS CLAMPED AT THE MIDPOINT TO EACH NEIGHBOURING RUNG rather
      than narrowed everywhere. Narrowing the tolerance to split 8000 from
      8047 would put it under 0.3% -- tighter than the scrape wobble it exists
      to absorb -- and would then drop 5k courses stored as 4989. The midpoint
      rule gives every rung of DISTANCES its own bucket, adjacent buckets
      never overlap, and a course is claimed by whichever named distance it is
      nearer to. Away from a close neighbour the 3% still governs.
    """
    lo, hi = d * (1 - _DISTANCE_TOL), d * (1 + _DISTANCE_TOL)
    below = [x for x in DISTANCES if x < d]
    above = [x for x in DISTANCES if x > d]
    if below:
        lo = max(lo, (max(below) + d) / 2.0)
    if above:
        hi = min(hi, (min(above) + d) / 2.0)
    return lo, hi


def _where(f, params):
    parts = []
    if f["state"]:
        params["states"] = f["state"]
        parts.append(" AND c.state = ANY(%(states)s)")
    if f["name"]:
        # ! CASE-INSENSITIVE CONTAINS, not a prefix. Course names carry the
        #   park before the town as often as after -- "Agri Park" and
        #   "Fayetteville, Agri Park" are the same ground -- so anchoring at
        #   the front would miss half the ways somebody types it.
        params["name"] = f"%{f['name'].lower()}%"
        parts.append(" AND lower(c.course_name) LIKE %(name)s")
    if f["distance"]:
        params["dist_lo"], params["dist_hi"] = _band(f["distance"])
        parts.append(" AND c.distance_m BETWEEN %(dist_lo)s AND %(dist_hi)s")
    if f["min_results"]:
        params["min_results"] = f["min_results"]
        parts.append(" AND c.n_results >= %(min_results)s")
    return "".join(parts)


def getCourseRankings(cur, f):
    """One page of the course board."""
    params = {"limit": f["limit"], "offset": f["offset"]}
    where = _where(f, params)
    expr, _natural = _SORTS[f["sort"]]

    # ! THE RANK IS COMPUTED OVER THE FILTERED SET, and that is right here in
    #   a way it is not on the teams board. A course's difficulty does not
    #   depend on which other courses are on screen -- it is measured against
    #   the whole corpus either way -- so "3rd hardest in Oregon" is a true
    #   sentence, where "3rd best team" among a filtered set would not be.
    cur.execute(f"""
        SELECT row_number() OVER (ORDER BY {expr} {f['dir']} NULLS LAST,
                                  c.n_results DESC, lower(c.course_name) ASC)
                   AS rank,
               c.course_name, c.canonical_id, c.state, c.distance_m,
               c.difficulty,
               c.n_results, c.n_athletes, c.n_meets,
               -- ! WHEN TWO VENUES ANSWER TO ONE NAME, THE PAGE SAYS SO.
                --   Otherwise the board shows "Firth Mud Run" twice with two
                --   different difficulties and no explanation, which reads as
                --   a bug in the ranking rather than as two real courses.
               c.n_same_name
        FROM   course_rank c
        WHERE  TRUE {where}
        ORDER  BY {expr} {f['dir']} NULLS LAST,
                  c.n_results DESC, lower(c.course_name) ASC
        LIMIT  %(limit)s OFFSET %(offset)s
    """, params)
    return cur.fetchall()


def countCourses(cur, f):
    """How many courses the filter selects, for an exact pager."""
    params = {}
    where = _where(f, params)
    cur.execute(f"SELECT count(*) AS n FROM course_rank c WHERE TRUE {where}",
                params)
    row = cur.fetchone()
    # RealDictCursor here, a tuple cursor in a test -- accept both rather than
    # assuming, which is the bug that once made a route answer with an HTML
    # debug page.
    return int(row["n"] if isinstance(row, dict) else row[0])
