# Project: xc-predictor / racecast
# File:    recruiting.py
# Purpose: Recruiting (issue 282), the part that needs no accounts: the
#          coach's search over every high-school season in the country,
#          and an athlete's recruiting profile.
#
# ★ WHAT A COACH ASKS, IN ORDER: who graduates when, how good, how fast
#   they are getting better, where. So the search is over athlete_season
#   (the boards' own table) joined to the same athlete's season a year
#   earlier, with the graduation year derived from the grade, and every
#   filter is one of those questions. The profile is the same numbers for
#   one athlete, season by season, plus where their rating would land on
#   each college division's board this season.
#
# ! THE GRADUATION YEAR IS DERIVED, NOT STORED. athlete_season keeps the
#   feed's grade spelling (12, Sr, senior); the stored year is the academic
#   year (August to July, named for the year it opens), so a senior in
#   stored year Y graduates in Y + 1 and a grade-g athlete in Y + 1 + (12
#   - g). Grades outside 5..12 are not high school and get no year.
#
# ! ONE COLLEGE SCALE PER POOL. A high-school rating placed on a college
#   board is divided by pool_view.hsFactor(college pool), the same one
#   number the HS-equivalent view multiplies college rows by, so the
#   placement here agrees with the mixed boards.
from season_floor import floorSql, DEFAULT_FLOOR

POOLS = ("hs_m", "hs_f")
SPORTS = ("XC", "TF")
SORTS = {
    "rating": "j.mean_rating DESC NULLS LAST, j.person_id",
    "gain":   "j.gain DESC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
    "best":   "j.best_rating DESC NULLS LAST, j.person_id",
    "grad":   "j.grad_year ASC NULLS LAST, j.mean_rating DESC NULLS LAST, j.person_id",
}
LIMIT, MAX_LIMIT = 100, 200
TIMEOUT_MS = 12000
COLLEGE_DIVISIONS = ("NCAA DI", "NCAA DII", "NCAA DIII", "NAIA")


def gradeNumSql(alias):
    """The grade as a number 5..12, or NULL, from the feed's spelling."""
    g = f"regexp_replace(lower(trim({alias}.grade)), '\\.$', '')"
    return (f"(CASE WHEN {g} ~ '^[0-9]+$' AND {g}::int BETWEEN 5 AND 12 THEN {g}::int "
            f"WHEN {g} IN ('fr', 'freshman') THEN 9 WHEN {g} IN ('so', 'sophomore') THEN 10 "
            f"WHEN {g} IN ('jr', 'junior') THEN 11 WHEN {g} IN ('sr', 'senior') THEN 12 END)")


def storedYear(label_year, sport):
    """The label year the page shows -> the academic year the table stores
    (the track season named 2026 is stored as 2025)."""
    return int(label_year) - 1 if sport == "TF" else int(label_year)


def labelYear(stored, sport):
    return int(stored) + 1 if sport == "TF" else int(stored)


def _ints(args, key, lo, hi):
    out = []
    for v in (args.get(key) or "").split(","):
        v = v.strip()
        if v.isdigit() and lo <= int(v) <= hi:
            out.append(int(v))
    return out


def _float(args, key):
    v = (args.get(key) or "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def parseFilters(args, season_years):
    """request.args -> (filters, error). season_years: {"XC": label,
    "TF": label} from the homepage meta, the default season per sport."""
    sport = (args.get("sport") or "XC").strip().upper()
    if sport not in SPORTS:
        return None, "sport must be XC or TF"
    pool = (args.get("pool") or "hs_m").strip().lower()
    if pool not in POOLS:
        return None, "pool must be hs_m or hs_f"
    year = (args.get("year") or "").strip()
    if year and not (year.isdigit() and 1990 <= int(year) <= 2100):
        return None, "year must be a season"
    label = int(year) if year else season_years.get(sport)
    if not label:
        return None, "no current season is known"
    states = [s.strip().upper() for s in (args.get("state") or "").split(",") if s.strip()]
    if any(len(s) != 2 or not s.isalpha() for s in states):
        return None, "state must be two letters"
    schools = [s.strip() for s in (args.get("school") or "").split(",") if s.strip()][:20]
    sort = (args.get("sort") or "rating").strip().lower()
    if sort not in SORTS:
        return None, f"sort must be one of {sorted(SORTS)}"
    limit = _ints(args, "limit", 1, MAX_LIMIT)
    offset = _ints(args, "offset", 0, 100000)
    min_races = _ints(args, "min_races", 1, 200)
    return {
        "sport": sport, "pool": pool, "label": label, "year": storedYear(label, sport),
        "grad": _ints(args, "grad", 1990, 2100),
        "states": states, "schools": schools,
        "min_rating": _float(args, "min_rating"), "max_rating": _float(args, "max_rating"),
        "min_gain": _float(args, "min_gain"),
        "min_races": min_races[0] if min_races else DEFAULT_FLOOR,
        "min_races_explicit": bool(min_races),
        "sort": sort,
        "limit": limit[0] if limit else LIMIT,
        "offset": offset[0] if offset else 0,
    }, None


def searchRecruits(cur, f):
    """The rows for one search: this season's rating, last season's, the
    gain, the graduation year, the best race and the race count."""
    from rankings import nameLateral
    params = {"pool": f["pool"], "sport": f["sport"], "year": f["year"],
              "min_races": f["min_races"], "limit": f["limit"], "offset": f["offset"]}
    where = []
    if f["states"]:
        params["states"] = f["states"]
        where.append("AND s.state = ANY(%(states)s)")
    if f["schools"]:
        params["schools"] = f["schools"]
        where.append("AND s.school = ANY(%(schools)s)")
    if f["min_rating"] is not None:
        params["min_rating"] = f["min_rating"]
        where.append("AND s.mean_rating >= %(min_rating)s")
    if f["max_rating"] is not None:
        params["max_rating"] = f["max_rating"]
        where.append("AND s.mean_rating <= %(max_rating)s")
    grad_where = ""
    if f["grad"]:
        params["grad"] = f["grad"]
        grad_where = "WHERE c.grade_num IS NOT NULL AND c.year + 13 - c.grade_num = ANY(%(grad)s)"
    gain_where = ""
    if f["min_gain"] is not None:
        params["min_gain"] = f["min_gain"]
        gain_where = "WHERE (g.mean_rating - p.mean_rating) >= %(min_gain)s"
    # a search too wide to answer in time says so rather than holding the
    # page: the savepoint keeps the connection usable after a cancel
    cur.execute("SAVEPOINT recruit_search")
    cur.execute(f"SET LOCAL statement_timeout = {int(TIMEOUT_MS)}")
    cur.execute(f"""
        WITH cur AS (
            SELECT s.person_id, s.school, s.state, s.grade, s.year,
                   s.mean_rating, s.best_rating, s.n_races,
                   {gradeNumSql('s')} AS grade_num
            FROM   athlete_season s
            WHERE  s.pool = %(pool)s AND s.sport = %(sport)s AND s.year = %(year)s
              AND  s.state IS NOT NULL AND s.mean_rating IS NOT NULL
              AND  {floorSql(f['min_races_explicit'])}
              {' '.join(where)}
        ),
        g AS (
            SELECT c.*, c.year + 13 - c.grade_num AS grad_year
            FROM   cur c
            {grad_where}
        ),
        j AS (
            SELECT g.*, p.mean_rating AS prev_rating,
                   (g.mean_rating - p.mean_rating)::real AS gain
            FROM   g
            LEFT JOIN athlete_season p
                   ON p.person_id = g.person_id AND p.pool = %(pool)s
                  AND p.sport = %(sport)s AND p.year = %(year)s - 1
            {gain_where}
        )
        SELECT j.*, a.name
        FROM   j
        {nameLateral('j')}
        ORDER  BY {SORTS[f['sort']]}
        LIMIT  %(limit)s OFFSET %(offset)s
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute("RELEASE SAVEPOINT recruit_search")
    return rows


def searchOrTimeout(cur, f):
    """(rows, None) or (None, message) when the search timed out."""
    try:
        return searchRecruits(cur, f), None
    except Exception as exc:                          # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT recruit_search")
        if "canceling statement" in str(exc) or "timeout" in str(exc).lower():
            return None, "That search is too wide to answer in time. Add a state, a graduation year or a rating floor."
        raise


def collegePlacements(cur, sport, hs_pool, rating, label_year):
    """Where a high-school rating would rank on each college division's
    board for the season: [{division, rank, total, board_url}]. The board
    is the ability board with the division filter, the same one the
    rankings page shows, so a coach can click through and see the list."""
    from urllib.parse import urlencode
    from werkzeug.datastructures import MultiDict
    from rankings import parseFilters as boardFilters, countOf
    from pool_view import hsFactor
    if rating is None:
        return []
    college = "college_m" if hs_pool.endswith("_m") else "college_f"
    factor = hsFactor(college, sport, None)
    if not factor:
        return []
    equiv = float(rating) / float(factor)
    # the latest college season on the board at or before this one
    cur.execute("""SELECT max(year) AS y FROM athlete_season
                   WHERE pool = %s AND sport = %s AND year <= %s""",
                (college, sport, storedYear(label_year, sport)))
    row = cur.fetchone()
    stored = (row["y"] if isinstance(row, dict) else row[0]) if row else None
    if stored is None:
        return []
    from rankings import _whereClauses
    out = []
    for div in COLLEGE_DIVISIONS:
        args = {"board": "ability", "pool": college, "sport": sport,
                # the boards take the stored academic year (rankings.
                # boardYear); labelYear is for what a reader is SHOWN
                "year": str(stored), "division": div}
        f, err = boardFilters(MultiDict(args))
        if err:
            continue
        params = {"min_races": f["min_races"], "equiv": equiv}
        where = _whereClauses(f, params, with_dates=False)
        try:
            # a plain cursor: the board machinery reads rows by position
            with cur.connection.cursor() as plain:
                plain.execute("SAVEPOINT recruit_place")
                plain.execute(f"""SELECT count(*) FROM athlete_season s
                                  WHERE {floorSql(f['min_races_explicit'])} {where}
                                    AND s.mean_rating > %(equiv)s""", params)
                above = plain.fetchone()[0]
                total = countOf(plain, f)
                plain.execute("RELEASE SAVEPOINT recruit_place")
        except Exception as exc:                    # noqa: BLE001
            print(f"recruit: placement {div} failed ({type(exc).__name__}: {exc})", flush=True)
            try:
                cur.execute("ROLLBACK TO SAVEPOINT recruit_place")
            except Exception:                       # noqa: BLE001
                cur.connection.rollback()
            continue
        if not total:
            continue
        out.append({"division": div, "rank": int(above) + 1, "total": int(total),
                    "equiv": equiv, "year": labelYear(stored, sport),
                    "board_url": "/rankings?" + urlencode(args)})
    return out


def recruitProfile(cur, person_id):
    """One athlete's recruiting numbers: every high-school season with its
    gain, the graduation year, and the college placements of the latest
    season in each sport. None when the athlete has no HS season."""
    from rankings import nameLateral
    from school_identity import schoolLabelIn
    cur.execute(f"""
        SELECT s.pool, s.sport, s.year, s.grade, s.school, s.state,
               s.mean_rating, s.best_rating, s.n_races, s.last_race,
               {gradeNumSql('s')} AS grade_num, a.name
        FROM   athlete_season s
        {nameLateral('s')}
        WHERE  s.person_id = %s AND s.pool IN ('hs_m', 'hs_f')
        ORDER  BY s.year, s.sport
    """, (person_id,))
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return None
    seasons = []
    prev = {}
    for r in rows:
        p = prev.get(r["sport"])
        gain = (float(r["mean_rating"]) - float(p["mean_rating"])) if p and r["mean_rating"] is not None \
            and p["mean_rating"] is not None and p["year"] == r["year"] - 1 else None
        seasons.append({**r, "label": labelYear(r["year"], r["sport"]), "gain": gain,
                        "grad_year": (r["year"] + 13 - r["grade_num"]) if r["grade_num"] else None})
        prev[r["sport"]] = r
    latest = max(seasons, key=lambda s: (s["year"], s.get("last_race") or ""))
    grad = next((s["grad_year"] for s in reversed(seasons) if s["grad_year"]), None)
    placements = []
    for sport in SPORTS:
        last = next((s for s in reversed(seasons) if s["sport"] == sport and s["mean_rating"] is not None), None)
        if last:
            placements.append({"sport": sport, "label": last["label"], "rating": float(last["mean_rating"]),
                               "rows": collegePlacements(cur, sport, last["pool"], last["mean_rating"], last["label"])})
    return {"person_id": person_id, "name": latest.get("name") or "Unknown",
            "school": latest.get("school"), "state": latest.get("state"),
            "school_label": schoolLabelIn(latest.get("school"), latest.get("state")) if latest.get("school") else "",
            "pool": latest["pool"], "grad_year": grad, "seasons": seasons, "placements": placements}
