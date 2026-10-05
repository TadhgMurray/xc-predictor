# Project: xc-predictor / racecast
# File:    projections.py
# Purpose: "Who wins state?" -- the state cross country championship for one
#          state, one division, boys or girls, projected from this season's
#          ratings (owner, 2026-10-04, approved).
#
#              /projections                     pick a state
#              /projections/<st>                pick a division
#              /projections/<st>/<div>[?g=girls]   the projection
#
# ★ NO NEW SCORING. The race is scored by predict._score -- the same function
#   the /predictions page scores a predicted meet with: places, the
#   unattached and incomplete teams lifted out of the scoring order, five
#   scorers, two displacers, seven entered per team. A state projection is a
#   predicted race with a field built a different way, so it must read
#   exactly like one; a second scorer here would be a second set of rules
#   waiting to disagree with the first.
#
# ★ NO NEW MODEL EITHER (owner, 2026-10-04: "what are we supposed to do early
#   season, they've only run one race"). The order is the athlete's SEASON
#   rating as the boards publish it -- athlete_season.mean_rating, the 80th
#   percentile race (build_ranking_results._SEASON_Q says why it is a
#   quantile). Early in the season that number rests on one or two races and
#   is shown that way: every row carries its race count, and a rating on
#   THIN_RACES or fewer is flagged rather than shrunk, padded or blended with
#   last year. Inventing a prior here would be a model nobody validated,
#   presented as the site's rating.
#
# ⚠ WHAT IS NOT MODELLED, AND THE PAGE SAYS SO. Who actually qualifies (the
#   sectional / regional route to the state meet), who is injured or resting,
#   and division moves between seasons. The field is every school's top seven
#   in the state and division, which is a bigger field than any state meet
#   runs -- so the SCORES run higher than a real state meet's, while the ORDER
#   of the teams is the claim.
import datetime
import re

import ttlcache

# The level each gender page reads. High school only: the state meet is a
# high school championship.
GENDERS = {"boys": ("hs_m", "Boys"), "girls": ("hs_f", "Girls")}

# ★ A RATING ON THIS MANY RACES OR FEWER IS FLAGGED "thin". Two, because the
#   boards' own floor is three (season_floor) and an open season is exempt
#   from it -- these are exactly the open-season rows the floor lets through.
THIN_RACES = 2

# A team enters seven (predict.MAX_PER_TEAM); the field takes each school's
# best seven by season rating.
PER_SCHOOL = 7
# ⚠ BOUNDED. A whole large state ("all" divisions in CA or TX) is thousands
#   of athletes; past the strongest few thousand no one is scoring.
FIELD_CAP = 3000
# How many individuals the page lists; the teams list is every scoring team.
INDIVIDUAL_N = 30
# A division must hold this many schools to be offered. Fewer is a parse
# artefact (a class token that matched one school), not a championship.
MIN_SCHOOLS = 4

# the state meet's course: how many past state championships at one venue
# before the page trusts it as "the state course"
MIN_EDITIONS = 2

_TTL = 6 * 3600.0

# A middle school, junior high or youth state meet is not the high school one.
_NOT_HS_MEET = re.compile(
    r"middle|junior high|\bjr\.? high|\bjh\b|\bms\b|elementary|youth|"
    r"grade school|freshman|frosh|\bjv\b|junior varsity", re.I)
_GIRLS_RX = re.compile(r"girl|women|female|\bw\b|\bf\b", re.I)
_BOYS_RX = re.compile(r"\bboy|\bmen\b|\bmale|\bm\b", re.I)


# ------------------------------------------------------------------ #
#  divisions
# ------------------------------------------------------------------ #

def divisionSlug(value):
    """'6A' -> '6a', 'Open Small' -> 'open-small'. The URL spelling."""
    return re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")


def divisionSortKey(value):
    """Natural order: 1 < 2 < 10, 1A < 6A, A < AA < AAA."""
    s = str(value).strip().upper()
    m = re.match(r"^D?(\d+)", s)
    if m:
        return (0, int(m.group(1)), s)
    roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
    if s in roman:
        return (0, roman[s], s)
    if re.fullmatch(r"A+", s):
        return (1, len(s), s)
    return (2, 0, s)


def divisionLabel(state, value, kind):
    """The words a person says: 'CA Division 2', 'TX Class 6A'."""
    v = str(value).strip()
    if kind == "class" or re.search(r"[A-Za-z]", v):
        return f"{state} Class {v}"
    return f"{state} Division {v}"


def _divisionsUncached(cur, state):
    # ! school_unit, NOT athlete_season: the list of divisions is a fact about
    #   schools and is ~150k rows, while athlete_season is millions. The FIELD
    #   is then read off the season rows' own stamped units (see _fieldSql),
    #   which come from this same table by (school, state).
    # ★ BOTH COLUMNS, ONE FILTER: rankings.UNIT_COLUMNS["state_div"] searches
    #   state_div and class together, because the corpus writes a state's tier
    #   as either ("D2" in one state, "Class AA" in another).
    cur.execute("""
        SELECT v, kind, count(DISTINCT school) AS n FROM (
            SELECT state_div AS v, 'state_div' AS kind, school
            FROM   school_unit
            WHERE  state = %(st)s AND sport = 'XC' AND NOT is_college
              AND  state_div IS NOT NULL AND state_div <> ''
            UNION ALL
            SELECT class AS v, 'class' AS kind, school
            FROM   school_unit
            WHERE  state = %(st)s AND sport = 'XC' AND NOT is_college
              AND  class IS NOT NULL AND class <> ''
        ) t
        GROUP BY v, kind
    """, {"st": state})
    merged = {}
    for r in cur.fetchall():
        v, kind, n = _row(r, "v", "kind", "n")
        slug = divisionSlug(v)
        if not slug or slug == "all":
            continue
        got = merged.setdefault(slug, {"value": str(v).strip().upper(),
                                       "kind": kind, "n_schools": 0})
        got["n_schools"] += int(n)
        if kind == "state_div":
            got["kind"] = "state_div"
    out = [dict(d, slug=s, label=divisionLabel(state, d["value"], d["kind"]))
           for s, d in merged.items() if d["n_schools"] >= MIN_SCHOOLS]
    out.sort(key=lambda d: divisionSortKey(d["value"]))
    return out


def divisionsFor(cur, state):
    """[{slug, value, kind, label, n_schools}] for one state, natural order.
    Empty when the units are not built or the state has none."""
    def compute():
        try:
            return _divisionsUncached(cur, state)
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            return []
    val, _ = ttlcache.get(("proj-divs", state), compute, ttl=_TTL)
    return val


# ------------------------------------------------------------------ #
#  the state meet's course
# ------------------------------------------------------------------ #

def pickCourse(rows, gender):
    """The state meet's course from past state championship divisions.

    rows: [{course_name, distance, division, meet_id}] -- one per race
    division of a meet build_meet_units marked as this state's
    championship (meet_unit kind 'state').

    ★ THE VENUE MOST EDITIONS WERE RUN AT, newest edition breaking a tie --
      Fort Ord, Woodward Park, Kenosha: a state course is a habit, and the
      habit is the best guess at where this year's runs. Then the distance
      this gender ran there most often (most states run 5K for both; a few
      do not). None when no venue has MIN_EDITIONS editions -- one meet is an
      anecdote, and the page then projects on a neutral course."""
    by_course = {}
    for r in rows:
        c = (r.get("course_name") or "").strip()
        if not c:
            continue
        slot = by_course.setdefault(c, {"meets": set(), "rows": []})
        slot["meets"].add(r.get("meet_id"))
        slot["rows"].append(r)
    if not by_course:
        return None
    course, slot = max(by_course.items(),
                       key=lambda kv: (len(kv[1]["meets"]),
                                       max(m or 0 for m in kv[1]["meets"])))
    if len(slot["meets"]) < MIN_EDITIONS:
        return None
    rx_mine = _GIRLS_RX if gender == "girls" else _BOYS_RX
    rx_other = _BOYS_RX if gender == "girls" else _GIRLS_RX

    def mode(rs):
        counts = {}
        for r in rs:
            d = r.get("distance")
            if d and 1000 <= float(d) <= 12000:
                key = int(round(float(d)))
                counts[key] = counts.get(key, 0) + 1
        return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0] \
            if counts else None

    mine = [r for r in slot["rows"]
            if rx_mine.search(r.get("division") or "")
            and not rx_other.search(r.get("division") or "")]
    # ★ THE NEWEST EDITION'S DISTANCE, NOT THE MODE OF EVERY EDITION (owner,
    #   2026-10-05: CA projected at Woodward Park over 6437 m). A course is a
    #   habit, a distance is a rule that changes: the mode over decades of
    #   editions -- and over whatever else the state tag caught -- can be a
    #   distance the meet no longer runs. The newest edition (highest meet id,
    #   the same "newest" the venue tie-break uses) says what it runs now;
    #   the old mode is only the fallback when that edition has no distance.
    newest = max((r.get("meet_id") or 0) for r in slot["rows"])
    latest_mine = [r for r in mine if (r.get("meet_id") or 0) == newest]
    latest_all = [r for r in slot["rows"] if (r.get("meet_id") or 0) == newest]
    dist = mode(latest_mine) or mode(latest_all) or mode(mine) or mode(slot["rows"])
    return {"course_name": course, "distance": dist,
            "editions": len(slot["meets"])}


def _courseRows(cur, state):
    cur.execute("""
        SELECT m.course_name, m.distance, m.division, m.meet_id, m.meet_name
        FROM   meet_unit u
        JOIN   meets m ON m.meet_id = u.meet_id
        WHERE  u.sport = 'XC' AND u.kind = 'state' AND u.unit = %(st)s
          AND  m.course_name IS NOT NULL AND m.course_name <> ''
    """, {"st": state})
    out = []
    for r in cur.fetchall():
        course, dist, div, mid, name = _row(r, "course_name", "distance",
                                            "division", "meet_id", "meet_name")
        if _NOT_HS_MEET.search(name or "") or _NOT_HS_MEET.search(div or ""):
            continue
        out.append({"course_name": course, "distance": dist,
                    "division": div, "meet_id": mid})
    return out


def stateCourse(cur, state, gender):
    """{course_name, distance, editions} or None (neutral course)."""
    def compute():
        try:
            return pickCourse(_courseRows(cur, state), gender)
        except Exception:                               # noqa: BLE001
            # no meet_unit yet (step 10e not run), or no meets table
            cur.connection.rollback()
            return None
    val, _ = ttlcache.get(("proj-course", state, gender), compute, ttl=_TTL)
    return val


# ------------------------------------------------------------------ #
#  the field
# ------------------------------------------------------------------ #

def topPerSchool(rows, k=PER_SCHOOL):
    """Each school's best k by rating, the rest of the field untouched.

    ! A RUNNER WITH NO TEAM IS NOT CAPPED. predict._score never scores the
      unattached together, so seven of them are not a squad, and capping
      them at seven would drop an individual contender for being teamless.
    The SQL already ranks per school; this is the same rule for rows built
    any other way (and for the tests)."""
    from meet_compile import isTeam
    seen, out = {}, []
    for r in sorted(rows, key=lambda r: (-(r.get("rating") or 0),
                                         r.get("person_id") or 0)):
        school = r.get("school")
        if isTeam(school):
            n = seen.get(school, 0)
            if n >= k:
                continue
            seen[school] = n + 1
        out.append(r)
    return out


def _seasonHasUnits(cur):
    """Does athlete_season carry state_div/class WITH VALUES? Probed through
    rankings._rowHasUnit, the same probe the ability board's division filter
    uses, so this page and that filter read the same membership."""
    try:
        from rankings import _rowHasUnit
        return [c for c in ("state_div", "class")
                if _rowHasUnit(c, "athlete_season")]
    except Exception:                                   # noqa: BLE001
        return []


def _divisionSchools(cur, state, value):
    cur.execute("""
        SELECT DISTINCT school FROM school_unit
        WHERE  state = %(st)s AND sport = 'XC' AND NOT is_college
          AND  (upper(state_div) = %(v)s OR upper(class) = %(v)s)
    """, {"st": state, "v": value})
    return sorted({_row(r, "school")[0] for r in cur.fetchall()})


def _fieldUncached(cur, pool, year, state, division):
    from rankings import nameLateral
    params = {"pool": pool, "year": year, "st": state, "k": PER_SCHOOL,
              "cap": FIELD_CAP}
    div_sql = ""
    if division is not None:
        cols = _seasonHasUnits(cur)
        params["v"] = division["value"]
        if cols:
            # ★ THE ROW'S OWN STAMPED UNIT (rankings._whereClauses, owner
            #   2026-09-06: "the top one needs to always follow the bottom
            #   one") -- the same membership the board's filter and the
            #   athlete page's chips read.
            div_sql = " AND (" + " OR ".join(
                f'upper(s."{c}") = %(v)s' for c in cols) + ")"
        else:
            schools = _divisionSchools(cur, state, division["value"])
            if not schools:
                return []
            params["schools"] = schools
            div_sql = " AND s.school = ANY(%(schools)s)"
    # ★ RANKED PER SCHOOL IN THE DATABASE, named only after the cut: the
    #   name lookup is a lateral per row, and a big state's division has ten
    #   times more athletes than it has places at seven a school.
    # ! A ROW WITH NO TEAM PARTITIONS ON ITS OWN person_id, so "Unattached"
    #   is never a seven-runner squad that crowds an individual out.
    # ! STATE ON THE ROW TOO. A school name with a row in another state
    #   (two Oregons) must not bring that state's runners into this one.
    cur.execute(f"""
        WITH ranked AS (
            SELECT s.person_id, s.school, s.state, s.grade,
                   s.mean_rating, s.best_rating, s.n_races, s.last_race,
                   row_number() OVER (
                       PARTITION BY COALESCE(NULLIF(s.school, ''),
                                             s.person_id::text)
                       ORDER BY s.mean_rating DESC, s.person_id) AS k
            FROM   athlete_season s
            WHERE  s.pool = %(pool)s AND s.sport = 'XC' AND s.year = %(year)s
              AND  s.state = %(st)s
              AND  s.mean_rating IS NOT NULL
              {div_sql}
        ), cut AS (
            SELECT * FROM ranked WHERE k <= %(k)s
            ORDER  BY mean_rating DESC, person_id
            LIMIT  %(cap)s
        )
        SELECT s.person_id, s.school, s.state, s.grade,
               s.mean_rating, s.best_rating, s.n_races,
               to_char(s.last_race, 'YYYY-MM-DD') AS last_race,
               COALESCE(a.name, 'Unknown') AS name
        FROM   cut s
        {nameLateral("s")}
        ORDER  BY s.mean_rating DESC, s.person_id
    """, params)
    out = []
    for r in cur.fetchall():
        d = dict(r) if not isinstance(r, (tuple, list)) else dict(zip(
            ("person_id", "school", "state", "grade", "mean_rating",
             "best_rating", "n_races", "last_race", "name"), r))
        out.append({"person_id": d["person_id"], "name": d["name"],
                    "school": d["school"], "school_state": d["state"],
                    "grade": d["grade"], "pool": pool,
                    "rating": float(d["mean_rating"]),
                    "best_rating": (float(d["best_rating"])
                                    if d["best_rating"] is not None else None),
                    "n_races": int(d["n_races"] or 0),
                    "last_race": d["last_race"]})
    return topPerSchool(out)


# ------------------------------------------------------------------ #
#  scoring -- predict._score, fed the season rating
# ------------------------------------------------------------------ #

# ★ THE ORDER IS THE RATING, AND _score ORDERS BY `seconds`. So the key handed
#   to it is a strictly decreasing function of the rating: the higher the
#   rating, the earlier the finish, ties broken by the input order (sorted()
#   is stable). Every runner is in one pool, so one rating scale; within a
#   pool the rating-to-time conversion is itself monotone, so ordering by the
#   converted time would give this same order -- this just cannot be broken
#   by a conversion that has no answer for one athlete.
# ! THE KEY NEVER REACHES THE PAGE. _score copies it onto each row as
#   `seconds`; scoreField takes it straight back off and the page shows the
#   estimated time at the state course (est_seconds) instead.
_RANK_BASE = 100000.0


def scoreField(field):
    """(teams, finishers) for a field of rated rows, via predict._score.

    field rows: {person_id, name, school, school_state, grade, pool, rating,
    n_races, ...}. Finishers come back in projected order with `place` and
    `score_place` as _score numbers them, plus n_races / thin / best_rating
    carried over; teams as _score returns them, plus `thin` (how many of
    their five scorers are on a thin rating) and `top5` (the scorers'
    mean rating)."""
    from predict import _score
    rows = [f for f in field if f.get("rating") is not None]
    preds = [{"seconds": _RANK_BASE - float(f["rating"]),
              "basis": "season"} for f in rows]
    teams, finishers = _score(rows, preds)
    # ★ A TIE IS BROKEN BY THE SIXTH RUNNER, as the real rules break it (and
    #   as _score's own comment says, though its sort is by score alone):
    #   the team whose sixth finished ahead wins, and a team with no sixth
    #   loses to one that has one. Python's sort is stable, so everything
    #   that is not tied keeps _score's order exactly.
    def sixth(t):
        placed = [r["score_place"] for r in t["runners"] if r.get("score_place")]
        return placed[5] if len(placed) > 5 else float("inf")
    teams.sort(key=lambda t: (t["score"] is None, t["score"] or 0, sixth(t)))
    by_id = {f["person_id"]: f for f in rows}
    for r in finishers:
        src = by_id.get(r["person_id"], {})
        r.pop("seconds", None)
        r["n_races"] = src.get("n_races")
        r["best_rating"] = src.get("best_rating")
        r["last_race"] = src.get("last_race")
        r["thin"] = isThin(src.get("n_races"))
    fin_by_id = {r["person_id"]: r for r in finishers}
    for t in teams:
        scorers = []
        for ru in t["runners"]:
            ru.pop("seconds", None)
            src = fin_by_id.get(ru["person_id"], {})
            ru["rating"] = src.get("rating")
            ru["n_races"] = src.get("n_races")
            ru["thin"] = src.get("thin", False)
            ru["grade"] = src.get("grade")
            if ru.get("score_place"):
                scorers.append(ru)
        places = [s["score_place"] for s in scorers]
        t["scorer_places"] = places[:5]
        t["displacer_places"] = places[5:7]
        scorers = scorers[:5]
        t["thin"] = sum(1 for s in scorers if s["thin"])
        rated = [s["rating"] for s in scorers if s.get("rating") is not None]
        t["top5"] = round(sum(rated) / len(rated), 1) if rated else None
    return teams, finishers


def isThin(n_races):
    return n_races is not None and int(n_races) <= THIN_RACES


# ------------------------------------------------------------------ #
#  the estimated time at the state course
# ------------------------------------------------------------------ #

def _courseSpec(cur, course, year):
    """predict's own target spec for a manual race at the state course: the
    course's difficulty and identity, resolved the way /predictions resolves
    a course override."""
    if not course or not course.get("distance"):
        return None
    try:
        from predict import _targetSpec
        return _targetSpec(cur, {
            "mode": "manual", "sport": "XC",
            "date": f"{year}-11-07", "distance": course["distance"],
            "course": course["course_name"]})
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
        return None


def estSeconds(rating, pool, spec, year):
    """A season rating as a time at the state course -- the same algebra
    predict._ratingTimes uses for every rating-served prediction
    (conversions._norm_from_rating, then predict._raceSeconds). None when
    there is no conversion for this pool; the page then shows the rating
    alone, which is what the order was decided on anyway."""
    if rating is None or not spec or not spec.get("distance_meters"):
        return None
    try:
        import conversions
        from predict import _raceSeconds
        norm = conversions._norm_from_rating(float(rating), pool, 0.0, "XC")
        if not norm:
            return None
        return _raceSeconds(norm, {
            "distance": float(spec["distance_meters"]), "pool": pool,
            "sport": "XC", "season": year,
            "difficulty": spec.get("course_difficulty"),
            "canonical_id": spec.get("canonical_id"),
            "location_id": spec.get("location_id"),
            "is_indoor": spec.get("is_indoor"),
            "course": spec.get("course_name")})
    except Exception:                                   # noqa: BLE001
        return None


# ------------------------------------------------------------------ #
#  the page's one call
# ------------------------------------------------------------------ #

def _row(r, *keys):
    if isinstance(r, (tuple, list)):
        return tuple(r[:len(keys)])
    return tuple(r[k] for k in keys)


def currentYear(cur):
    """The XC season the boards show (predict._currentSeason)."""
    try:
        from predict import _currentSeason
        return int(_currentSeason(cur, "XC"))
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
        from season_year import academicYear
        return academicYear(datetime.date.today())


def project(cur, state, division, gender):
    """Everything the projection page renders, cached TTL per key.

    division: one of divisionsFor()'s dicts, or None for the whole state."""
    pool, _words = GENDERS[gender]
    key = ("proj", state, division["slug"] if division else "all", gender)

    def compute():
        year = currentYear(cur)
        field = _fieldUncached(cur, pool, year, state, division)
        course = stateCourse(cur, state, gender)
        teams, finishers = scoreField(field)
        spec = _courseSpec(cur, course, year)
        shown_ids = {r["person_id"] for r in finishers[:INDIVIDUAL_N]}
        for t in teams:
            if t.get("score") is not None:
                shown_ids.update(ru["person_id"] for ru in t["runners"])
        est = {}
        if spec:
            for r in finishers:
                if r["person_id"] in shown_ids:
                    est[r["person_id"]] = estSeconds(r.get("rating"), pool,
                                                     spec, year)
        for r in finishers:
            r["est_seconds"] = est.get(r["person_id"])
        for t in teams:
            for ru in t["runners"]:
                ru["est_seconds"] = est.get(ru["person_id"])
        scored = [t for t in teams if t.get("score") is not None]
        dates = [r["last_race"] for r in finishers if r.get("last_race")]
        n = len(finishers)
        n_thin = sum(1 for r in finishers if r.get("thin"))
        return {
            "year": year, "pool": pool,
            "teams": scored,
            "incomplete": len(teams) - len(scored),
            "individuals": finishers[:INDIVIDUAL_N],
            "n_field": n, "n_thin": n_thin,
            "thin_share": (n_thin / n) if n else 0.0,
            "as_of": max(dates) if dates else None,
            "course": course,
            "course_resolved": bool(spec and spec.get("canonical_id")),
            "distance": (spec or {}).get("distance_meters")
            or (course or {}).get("distance"),
        }

    val, stamp = ttlcache.get(key, compute, ttl=_TTL)
    return dict(val, computed_at=stamp)


def fmtTime(seconds):
    """3:55.2 / 15:42 / 1:02:03 -- a projected time is not a measured one, so
    whole seconds past a minute."""
    if seconds is None:
        return ""
    s = int(round(float(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
