# Project: xc-predictor / racecast
# File:    goal.py
# Purpose: "What do I need to run?" (owner, 2026-10-10, item 4): pick a
#          goal -- make state in my division, top 25 or top 8 at state, a
#          college's typical recruit -- and a course and distance, and read
#          the time the goal takes there.
#
#              /goal?goal=qualify&state=CA&div=d1&g=boys&cid=123&dist=5000
#              /goal?goal=recruit&college=Stanford (CA)&g=girls
#              /goal?athlete=<id>        their state, division and gender
#                                        filled in, and their own time beside
#
# ★ AN EXTENSION OF WHAT IT TAKES, NOT A SECOND ONE. The athlete page's
#   "That mark is ..." line (cuts.goalTimes) already turns the next rung of
#   the athlete's own ladder into clocks at the courses they race. This page
#   is the same idea opened up: ANY rung (cuts.marksFor -- the same cached
#   marks /what-it-takes prints), or a college's typical recruit
#   (recruiting.schoolTable's weighted median -- the number the recruiting
#   page calls the median recruit), at ANY course and distance, through the
#   same cuts.toTime the athlete line and /what-it-takes use. No mark, cut
#   or conversion is computed here that the site does not already publish.
#
# ★ ONE SCALE. The state marks are high-school season ratings and the
#   recruiting medians are recruits' high-school ratings: both on the
#   same-gender HS pool's scale, so a goal is one number converted once.
#
# ! THE MARK IS LAST SEASON'S (the newest on record), and the page says
#   which season and how many runners it rests on -- a thin cell is flagged
#   exactly as /what-it-takes flags it.
from flask import Blueprint, abort, render_template, request

import cuts
import projections as P

bp = Blueprint("goal", __name__)

# goal key -> (the line in cuts' marks, how the page names it)
GOALS = (("qualify", "Make state"),
         ("top", "Top 25 at state"),
         ("podium", "Top 8 at state"),
         ("recruit", "A college's typical recruit"))
_GOAL_KEYS = tuple(k for k, _ in GOALS)
# the place lines' wording comes from cuts.PLACE_LINES, so a changed line
# (top 20 somewhere) renames itself here
_PLACE_WORDS = {key: label for key, _n, label in cuts.PLACE_LINES}

_RECRUIT_GENDER = {"boys": "m", "girls": "f"}


def goalLabel(key):
    if key in _PLACE_WORDS:
        return f"{_PLACE_WORDS[key]} at state"
    return dict(GOALS).get(key, key)


# ------------------------------------------------------------------ #
#  the goal's rating
# ------------------------------------------------------------------ #

def stateGoal(data, gender, slug, key):
    """{rating, year, n, where, label, thin, href, venue, distance} for a
    state rung, or None. `data` is cuts.marksFor's answer."""
    cell = cuts.findDivision(data, gender, slug) if slug else None
    g = (data.get("genders") or {}).get(gender) or {}
    if cell is None and len(g.get("divisions", [])) == 1 \
            and g["divisions"][0]["slug"] == "all":
        cell = g["divisions"][0]
    if not cell or not cell.get("latest"):
        return None
    ln = (cell["latest"].get("lines") or {}).get(key)
    if not ln or ln.get("mark") is None:
        return None
    st = (data.get("state") or "").lower()
    # who the percentile is of -- /what-it-takes' own words for each line
    n_place = {k: n for k, n, _lab in cuts.PLACE_LINES}.get(key)
    whose = (f"its top {n_place} finishers" if n_place
             else "the state meet's runners")
    return {"rating": float(ln["mark"]), "year": cell["latest"]["year"],
            "whose": whose,
            "n": ln.get("n"), "thin": bool(cell.get("thin")),
            "thin_reasons": cell.get("thin_reasons") or [],
            "label": cell["label"], "slug": cell["slug"],
            "href": f"/what-it-takes/{st}/{cell['slug']}"
                    + ("?g=girls" if gender == "girls" else ""),
            "venue": g.get("venue"), "distance": g.get("time_distance")}


def findCollege(rows, text):
    """The schoolTable row a typed college names: its label exactly ("Stanford
    (CA)"), else its name exactly, else the one row whose label contains the
    text. None when nothing -- or more than one thing -- matches."""
    t = " ".join(str(text or "").lower().split())
    if not t:
        return None
    for key in ("label", "school"):
        hit = [r for r in rows if (r.get(key) or "").lower() == t]
        if len(hit) == 1:
            return hit[0]
    hit = [r for r in rows if t in (r.get("label") or "").lower()]
    return hit[0] if len(hit) == 1 else None


def recruitGoal(row):
    if not row or row.get("median") is None:
        return None
    return {"rating": float(row["median"]), "n": row.get("n"),
            "label": row.get("label") or row.get("school"),
            "classes": row.get("classes"), "p25": row.get("p25"),
            "p75": row.get("p75"), "division": row.get("division"),
            "href": "/recruiting/school/" + (row.get("school") or "")
                    + (f"?state={row['state']}" if row.get("state") else "")}


# ------------------------------------------------------------------ #
#  the course
# ------------------------------------------------------------------ #

def courseCells(cur, cid):
    """(name, {distance_m: difficulty}) for a canonical course, from the
    table the course picker reads (course_difficulties, the engine's own
    canonical name); (None, {}) when it has none."""
    cur.execute("""SELECT substring(course_name from 4) AS name, distance_m,
                          difficulty, n_results
                   FROM   course_difficulties
                   WHERE  canonical_id = %s AND difficulty IS NOT NULL
                     AND  course_name LIKE 'XC:%%'
                   ORDER  BY n_results DESC NULLS LAST""", (int(cid),))
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return None, {}
    return rows[0]["name"], {int(r["distance_m"]): float(r["difficulty"]) for r in rows}


def clocks(rating, pool, distance, course=None, convert=cuts.toTime,
           track=None):
    """The goal's times: at the chosen course (when it has a rated cell at
    the distance), on a typical course at the distance, and the 1600 / 3200
    on a typical track (scale.cellTime, the cheat sheet's own cells)."""
    out = {"typical": convert(rating, pool, distance)}
    if course and course.get("difficulty") is not None:
        out["course"] = convert(rating, pool, distance, course["difficulty"],
                                course.get("canonical_id"), course.get("name"))
    if track is None:
        from scale import cellTime as track
    out["t1600"] = track(rating, pool, 1600.0, "TF")
    out["t3200"] = track(rating, pool, 3200.0, "TF")
    return out


# ------------------------------------------------------------------ #
#  the route
# ------------------------------------------------------------------ #

def _states():
    from landing import STATE_NAMES, US_STATES
    return [(c, STATE_NAMES[c]) for c in US_STATES if c in STATE_NAMES], STATE_NAMES


@bp.route("/goal")
def goal_page():
    import psycopg2.extras
    from database import getConn
    import recruiting as R

    args = request.args
    goal = (args.get("goal") or "qualify").strip().lower()
    if goal not in _GOAL_KEYS:
        goal = "qualify"
    gender = (args.get("g") or "").strip().lower()
    state = (args.get("state") or "").strip().upper()
    slug = P.divisionSlug(args.get("div")) if args.get("div") else ""
    college = (args.get("college") or "").strip()
    cid = args.get("cid", type=int)
    dist = args.get("dist", type=float)
    athlete_id = args.get("athlete", type=int)
    states, names = _states()
    if state and state not in names:
        abort(404)

    ctx = {"goal": goal, "goals": [(k, goalLabel(k)) for k in _GOAL_KEYS],
           "states": states, "state": state, "slug": slug, "college": college,
           "cid": cid, "dist": dist, "athlete": None, "target": None,
           "times": None, "course": None, "divisions": [], "colleges": [],
           "error": None, "state_name": names.get(state),
           "mark_pct": int(round(100 * (1 - cuts.MARK_Q)))}
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # ★ THE ATHLETE FILLS THE FORM: their season's state, division
            #   and gender, exactly the ones their "What it takes" line uses
            season = None
            if athlete_id:
                try:
                    season = cuts._athleteSeason(cur, athlete_id, P.currentYear(cur))
                except Exception:                       # noqa: BLE001
                    conn.rollback()
                    season = None
                if season:
                    gender = gender or cuts._POOL_GENDER.get(season.get("pool"), "")
                    state = state or (season.get("state") or "").upper()
                    div_raw = season.get("state_div") or season.get("class")
                    slug = slug or (P.divisionSlug(div_raw) if div_raw else "")
                    from rankings import nameLateral
                    cur.execute(f"SELECT a.name FROM (SELECT %s::bigint AS person_id) s "
                                f"{nameLateral('s')}", (athlete_id,))
                    row = cur.fetchone()
                    ctx["athlete"] = {"person_id": athlete_id,
                                      "name": (row or {}).get("name"),
                                      "rating": season.get("mean_rating"),
                                      "pool": season.get("pool"),
                                      "year": season.get("year")}
            gender = gender if gender in cuts.GENDERS else "boys"
            pool = cuts.GENDERS[gender][0]
            ctx.update(gender=gender, state=state, slug=slug,
                       gender_words=cuts.GENDERS[gender][1],
                       state_name=names.get(state))

            target = None
            if goal == "recruit":
                rows = R.schoolTable(cur, _RECRUIT_GENDER[gender], "XC")
                ctx["colleges"] = [r["label"] for r in rows if r.get("label")]
                if college:
                    target = recruitGoal(findCollege(rows, college))
                    if target is None:
                        ctx["error"] = (f"No college with recruits on record matches "
                                        f"“{college}”. Pick one from the list.")
            elif state:
                data = cuts.marksFor(cur, state)
                g = (data.get("genders") or {}).get(gender) or {}
                ctx["divisions"] = [(d["slug"], d["short"]) for d in g.get("divisions", [])]
                if not slug and len(ctx["divisions"]) == 1:
                    slug = ctx["slug"] = ctx["divisions"][0][0]
                if slug:
                    target = stateGoal(data, gender, slug, goal)
                    if target is None:
                        ctx["error"] = (f"No {goalLabel(goal).lower()} mark is on record "
                                        f"for that division.")

            # ★ THE COURSE AND THE DISTANCE: the picked course at a distance
            #   it has a rated cell at (the one asked, else the state meet's,
            #   else its most-raced); with no course, a state goal reads the
            #   state meet's usual course (cuts' venue, as /what-it-takes
            #   does) and anything else a typical course. The distance with
            #   nothing to go on is the pool's anchor (targetFor: 5000 for
            #   high school), the race the rating is a time at.
            from normalize_distance import targetFor
            want = dist or (target or {}).get("distance")
            course = None
            if cid:
                try:
                    name, cells = courseCells(cur, cid)
                except Exception:                       # noqa: BLE001
                    conn.rollback()
                    name, cells = None, {}
                if name:
                    key = int(round(float(want) / 100.0) * 100) if want else None
                    if key not in cells:
                        key = next(iter(cells))         # most results first
                    want = float(key)
                    course = {"canonical_id": cid, "name": name,
                              "distances": sorted(cells), "difficulty": cells[key]}
            elif target and target.get("venue") \
                    and target["venue"].get("difficulty") is not None:
                v = target["venue"]
                want = want or v.get("distance")
                if not dist or int(round(float(want))) == int(round(float(v.get("distance") or 0))):
                    course = {"canonical_id": v.get("canonical_id"),
                              "name": v.get("course_name"),
                              "distances": [int(v["distance"])] if v.get("distance") else [],
                              "difficulty": v["difficulty"], "state_course": True}
            dist = float(want or targetFor(pool, "XC") or 5000.0)
            ctx.update(course=course, dist=dist, target=target)

            if target:
                ctx["times"] = clocks(target["rating"], pool, dist, course)
                a = ctx["athlete"]
                if a and a.get("rating") is not None:
                    a["times"] = clocks(float(a["rating"]), pool, dist, course)
                    a["gap"] = round(float(target["rating"]) - float(a["rating"]), 1)
            conn.rollback()
    return render_template("goal.html", **ctx)


@bp.app_template_filter("goal_dist")
def _goalDist(metres):
    """5000 -> '5K', 4800 -> '4,800 m': whole kilometres read as a race."""
    try:
        m = int(round(float(metres)))
    except (TypeError, ValueError):
        return ""
    return f"{m // 1000}K" if m % 1000 == 0 else f"{m:,} m"
