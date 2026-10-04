# Project: xc-predictor / racecast
# File:    breakouts.py
# Purpose: "Breakouts this week" -- the biggest rating jumps and the new PRs
#          from the newest meets in the data, per level, filterable by state
#          (owner, 2026-10-04, approved).
#
# ★ WHAT A BREAKOUT IS, IN ONE SENTENCE. A rated result from the window whose
#   rating beats the MEDIAN of that athlete's earlier rated races this season
#   (same level, same sport, strictly earlier race dates) by the most points
#   -- and only for athletes with at least MIN_PRIOR such races, because a
#   jump over one earlier race is a jump over a single draw, and the first
#   race of a season is a jump over nothing.
#
#   Why the median and not the previous best: a breakout is "ran well above
#   what they usually run", and the median is what they usually run. The
#   previous best is shown beside it, and a row that beat that too says so --
#   but ranking on it would put every athlete with one off day ahead of every
#   athlete who is simply better now.
#
# ★ WHAT A NEW PR IS. A result from the window at a standard distance (the
#   race page's own 0.25% band, app._REC_DIST_TOL) faster than every earlier
#   time by that athlete at that distance, any season, same sport -- the rule
#   the race page's PR badge already uses (app.stampRecordFlags), with one
#   difference: an athlete with NO earlier time at the distance is left out.
#   The race page badges a debut PR, correctly; it is not news. Ranked by how
#   much faster, as a share of the old PR.
#
# ★ THE WINDOW IS THE DATA'S WEEK, NOT THE CALENDAR'S. It ends at the newest
#   race date the level holds (never after tomorrow -- the corpus carries junk
#   years like 2223, panels.py says so) and runs WINDOW_DAYS back from there.
#   A page read on a Wednesday after a quiet Monday still shows the weekend.
#
# ⚠ THE FAST SIDE IS WHERE BAD ROWS LIVE. A mislabelled distance (a 4K
#   recorded as 5K) or two people merged into one is a "breakout" by
#   construction. Rows engine/rating_outliers.py flagged are left out, as they
#   are left out of predictions (predict._outlierFilter); what survives and is
#   still implausibly large is shown with a "check" mark rather than hidden --
#   build_ranking_results trusts the fast side ("a breakthrough is real"), and
#   this page should not quietly overrule it.
#
# ⚠ COST. ranking_results has no index on race_date. The season's rows for
#   one level come off rr_board_rating_idx (pool, sport, year, ...) -- one
#   level's season, not the corpus -- and everything else is arithmetic on
#   that set. The PR check probes idx_rr_person_cover, an index-only read.
#   One compute per level per TTL per worker (ttlcache), with a statement
#   timeout, so a slow night degrades the page to "unavailable" instead of
#   tying up a worker.
import datetime

import ttlcache

# level slug -> (pool, words)
LEVELS = {
    "hs-boys":       ("hs_m",      "HS boys"),
    "hs-girls":      ("hs_f",      "HS girls"),
    "college-men":   ("college_m", "College men"),
    "college-women": ("college_f", "College women"),
    "ms-boys":       ("ms_m",      "MS boys"),
    "ms-girls":      ("ms_f",      "MS girls"),
}
SPORTS = {"xc": "XC", "tf": "TF"}

WINDOW_DAYS = 7
WINDOW_CHOICES = (7, 14)
MIN_PRIOR = 2          # earlier rated races this season, for a breakout
MIN_JUMP = 1.0         # points over the median; less is noise
CHECK_JUMP = 20.0      # build_ranking_results._SEASON_OUTLIER_PTS, the fast side
CHECK_GAIN = 0.10      # a PR 10% under the old one is usually a distance error
NATIONAL_N = 50
STATE_N = 25
# rows the SQL returns per level, before the per-person fold
_SQL_NAT = 200
_SQL_STATE = 40

# Standard distances, metres. Exact metric/imperial pairs are DIFFERENT
# distances (1600 v 1609, 3200 v 3218), as on the PR board.
STANDARD = {
    "XC": (3000, 3200, 3219, 4000, 4828, 5000, 6000, 8000, 10000),
    "TF": (800, 1500, 1600, 1609, 3000, 3200, 3219, 5000, 10000),
}
DIST_TOL = 0.0025      # app._REC_DIST_TOL / rankings.PR_DISTANCE_TOL

_TTL = 6 * 3600.0
_TIMEOUT_MS = 25000


def _row(r, *keys):
    if isinstance(r, (tuple, list)):
        return tuple(r[:len(keys)])
    return tuple(r[k] for k in keys)


# ------------------------------------------------------------------ #
#  the pure rules (tests/test_breakouts.py)
# ------------------------------------------------------------------ #

def median(values):
    vals = sorted(float(v) for v in values)
    n = len(vals)
    if not n:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else 0.5 * (vals[mid - 1] + vals[mid])


def breakoutOf(rating, prior_ratings, min_prior=MIN_PRIOR):
    """{"base", "prev_best", "n_prior", "jump", "beat_best"} for one result,
    or None when it does not qualify (too few earlier races).

    The Python statement of what _BREAKOUT_SQL computes per row -- the tests
    pin this one, and the SQL is written to agree with it."""
    prior = [float(p) for p in prior_ratings if p is not None]
    if len(prior) < min_prior or rating is None:
        return None
    base = median(prior)
    best = max(prior)
    return {"base": base, "prev_best": best, "n_prior": len(prior),
            "jump": float(rating) - base, "beat_best": float(rating) > best}


def standardDistance(distance, sport):
    """The standard distance a race was run at, or None."""
    if not distance:
        return None
    d = float(distance)
    for std in STANDARD.get(sport, ()):
        if std * (1 - DIST_TOL) <= d <= std * (1 + DIST_TOL):
            return std
    return None


def prOf(time_seconds, earlier_times):
    """{"prev_best", "gain", "n_prev"} when time_seconds beats every earlier
    time at the distance and there IS an earlier time; else None."""
    prev = [float(t) for t in earlier_times if t and float(t) > 0]
    if not prev or not time_seconds:
        return None
    best = min(prev)
    t = float(time_seconds)
    if t >= best:
        return None
    return {"prev_best": best, "gain": (best - t) / best, "n_prev": len(prev)}


def foldPerPerson(rows, key):
    """One row per athlete -- their biggest -- in descending `key` order.
    The SQL ranks rows; an athlete with two big races this week is one
    breakout, not two lines."""
    best = {}
    for r in rows:
        pid = r["person_id"]
        if pid not in best or r[key] > best[pid][key]:
            best[pid] = r
    return sorted(best.values(), key=lambda r: (-r[key], r["person_id"]))


def pickRows(rows, key, state=None, n_nat=NATIONAL_N, n_state=STATE_N):
    """The page's slice: the national list, or one state's."""
    folded = foldPerPerson(rows, key)
    if state:
        return [r for r in folded if (r.get("state") or "").upper() == state][:n_state]
    return folded[:n_nat]


# ------------------------------------------------------------------ #
#  SQL
# ------------------------------------------------------------------ #

# The season's rows for one level, deduplicated -- anet and tfrrs carry the
# same physical race, and a duplicate would count twice toward n_prior and
# list twice on the page. The key is the board's (rankings._rankInResults):
# same person, same day, same rounded time.
_SEASON_CTE = """
    season AS MATERIALIZED (
        SELECT DISTINCT ON (rr.person_id, rr.race_date,
                            round(rr.time_seconds::numeric, 1))
               rr.result_id, rr.person_id, rr.race_date, rr.speed_rating,
               rr.time_seconds, rr.distance, rr.meet_id, rr.div_id,
               rr.event_id, rr.school, rr.state, rr.grade
        FROM   ranking_results rr
        WHERE  rr.pool = %(pool)s AND rr.sport = %(sport)s
          AND  rr.year = %(year)s
          AND  rr.speed_rating IS NOT NULL
          AND  rr.race_date <= %(tomorrow)s
          {outliers}
        ORDER  BY rr.person_id, rr.race_date,
                  round(rr.time_seconds::numeric, 1), rr.result_id
    )"""

_BREAKOUT_SQL = """
WITH {season},
anchor AS (SELECT max(race_date) AS d FROM season),
recent AS (
    SELECT s.* FROM season s, anchor a
    WHERE  s.race_date > a.d - %(days)s
),
prior AS (
    -- every earlier race of the same athlete this season; "earlier" is a
    -- strictly earlier DATE, so two races on one day do not judge each other
    SELECT r.result_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p.speed_rating) AS base,
           max(p.speed_rating) AS prev_best,
           count(*) AS n_prior
    FROM   recent r
    JOIN   season p ON p.person_id = r.person_id
                   AND p.race_date < r.race_date
    GROUP  BY r.result_id
    HAVING count(*) >= %(min_prior)s
),
scored AS (
    SELECT r.*, p.base, p.prev_best, p.n_prior,
           r.speed_rating - p.base AS jump,
           row_number() OVER (ORDER BY r.speed_rating - p.base DESC,
                                       r.result_id) AS nat_rank,
           row_number() OVER (PARTITION BY r.state
                              ORDER BY r.speed_rating - p.base DESC,
                                       r.result_id) AS st_rank
    FROM   recent r
    JOIN   prior p USING (result_id)
    WHERE  r.speed_rating - p.base >= %(min_jump)s
)
SELECT result_id, person_id, to_char(race_date, 'YYYY-MM-DD') AS race_date,
       speed_rating, time_seconds, distance, meet_id, div_id, event_id,
       school, state, grade, base, prev_best, n_prior, jump,
       (SELECT to_char(d, 'YYYY-MM-DD') FROM anchor) AS anchor
FROM   scored
WHERE  nat_rank <= %(nat_n)s OR st_rank <= %(st_n)s
"""

# A tiny query of its own for the anchor when nothing qualified -- the page
# still says which week it looked at.
_ANCHOR_SQL = """
WITH {season}
SELECT to_char(max(race_date), 'YYYY-MM-DD') FROM season
"""

_PR_SQL = """
WITH cand AS (
    SELECT DISTINCT ON (rr.person_id, rr.race_date,
                        round(rr.time_seconds::numeric, 1))
           rr.result_id, rr.person_id, rr.race_date, rr.speed_rating,
           rr.time_seconds, rr.distance, rr.meet_id, rr.div_id, rr.event_id,
           rr.school, rr.state, rr.grade, d.std
    FROM   ranking_results rr
    JOIN   unnest(%(stds)s::real[]) AS d(std)
           ON rr.distance BETWEEN d.std * (1 - %(tol)s) AND d.std * (1 + %(tol)s)
    WHERE  rr.pool = %(pool)s AND rr.sport = %(sport)s AND rr.year = %(year)s
      AND  rr.race_date > %(anchor)s::date - %(days)s
      AND  rr.race_date <= %(anchor)s::date
      AND  rr.time_seconds > 0 AND rr.time_seconds < 19999
      {flat}
      {outliers}
    ORDER  BY rr.person_id, rr.race_date,
              round(rr.time_seconds::numeric, 1), rr.result_id
),
judged AS (
    SELECT c.*, h.prev_best, h.n_prev
    FROM   cand c
    -- ★ INDEX-ONLY: idx_rr_person_cover carries (person_id) INCLUDE (sport,
    --   race_date, year, distance, time_seconds) for exactly this read --
    --   the race page's PR badge asks the same question of a whole field.
    CROSS  JOIN LATERAL (
        SELECT min(x.time_seconds) AS prev_best, count(*) AS n_prev
        FROM   ranking_results x
        WHERE  x.person_id = c.person_id AND x.sport = %(sport)s
          AND  x.race_date < c.race_date
          AND  x.distance BETWEEN c.std * (1 - %(tol)s) AND c.std * (1 + %(tol)s)
          AND  x.time_seconds > 0 AND x.time_seconds < 19999
          {flat_x}
    ) h
    WHERE  h.n_prev >= 1 AND c.time_seconds < h.prev_best
),
ranked AS (
    SELECT j.*, (j.prev_best - j.time_seconds) / j.prev_best AS gain,
           row_number() OVER (ORDER BY (j.prev_best - j.time_seconds)
                                       / j.prev_best DESC, j.result_id) AS nat_rank,
           row_number() OVER (PARTITION BY j.state
                              ORDER BY (j.prev_best - j.time_seconds)
                                       / j.prev_best DESC, j.result_id) AS st_rank
    FROM   judged j
)
SELECT result_id, person_id, to_char(race_date, 'YYYY-MM-DD') AS race_date,
       speed_rating, time_seconds, distance, meet_id, div_id, event_id,
       school, state, grade, std, prev_best, n_prev, gain
FROM   ranked
WHERE  nat_rank <= %(nat_n)s OR st_rank <= %(st_n)s
"""

_COLS_B = ("result_id", "person_id", "race_date", "speed_rating",
           "time_seconds", "distance", "meet_id", "div_id", "event_id",
           "school", "state", "grade", "base", "prev_best", "n_prior", "jump",
           "anchor")
_COLS_P = ("result_id", "person_id", "race_date", "speed_rating",
           "time_seconds", "distance", "meet_id", "div_id", "event_id",
           "school", "state", "grade", "std", "prev_best", "n_prev", "gain")


_PROBES = {}


def _scalar(row):
    if row is None:
        return None
    return row[0] if isinstance(row, (tuple, list)) else list(row.values())[0]


def _probe(cur, key, sql):
    """A yes/no about the schema, asked once per process."""
    if key not in _PROBES:
        try:
            cur.execute(sql)
            _PROBES[key] = bool(_scalar(cur.fetchone()))
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            _PROBES[key] = False
    return _PROBES[key]


def _outlierSql(cur, sport, alias="rr"):
    """predict._outlierFilter's anti-join, on this query's alias."""
    if not _probe(cur, "outliers",
                  "SELECT to_regclass('public.rating_outlier') IS NOT NULL AS t"):
        return ""
    return (" AND NOT EXISTS (SELECT 1 FROM rating_outlier ro "
            f"WHERE ro.sport = '{sport}' AND ro.result_id = {alias}.result_id)")


def _hasEventKind(cur):
    return _probe(cur, "event_kind", """
        SELECT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'ranking_results'
                         AND column_name = 'event_kind') AS t""")


def _dicts(rows, cols):
    out = []
    for r in rows:
        d = dict(zip(cols, r)) if isinstance(r, (tuple, list)) else dict(r)
        for k in ("speed_rating", "time_seconds", "distance", "base",
                  "prev_best", "jump", "gain", "std"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        out.append(d)
    return out


def _timeout(cur):
    """The transaction's settings for one of these queries.

    ! JIT OFF: on a 600k-row season the planner's cost estimate crosses the
      JIT threshold and compiling the expressions took a second of a 3.7 s
      query, for a statement that runs once per level per six hours.
    ! AND ROOM TO SORT IN MEMORY: the season's dedup sorts every row of one
      level's season, and the default work_mem spilled it to disk. SET LOCAL,
      so it ends with this transaction."""
    cur.execute(f"SET LOCAL statement_timeout = {_TIMEOUT_MS}")
    cur.execute("SET LOCAL jit = off")
    cur.execute("SET LOCAL work_mem = '128MB'")


def _breakoutRows(cur, sport, pool, year, days):
    season = _SEASON_CTE.format(outliers=_outlierSql(cur, sport))
    params = {"pool": pool, "sport": sport, "year": year, "days": days,
              "tomorrow": (datetime.date.today()
                           + datetime.timedelta(days=1)).isoformat(),
              "min_prior": MIN_PRIOR, "min_jump": MIN_JUMP,
              "nat_n": _SQL_NAT, "st_n": _SQL_STATE}
    _timeout(cur)
    cur.execute(_BREAKOUT_SQL.format(season=season), params)
    rows = _dicts(cur.fetchall(), _COLS_B)
    anchor = rows[0]["anchor"] if rows else None
    if anchor is None:
        cur.execute(_ANCHOR_SQL.format(season=season), params)
        anchor = _scalar(cur.fetchone())
    for r in rows:
        r["beat_best"] = r["speed_rating"] > (r["prev_best"] or 0)
        r["check"] = r["jump"] >= CHECK_JUMP
    return rows, anchor


def _prRows(cur, sport, pool, year, days, anchor):
    flat = flat_x = ""
    if _hasEventKind(cur):
        flat = " AND rr.event_kind IS NULL"
        # ! TRACK ONLY on the history side: a steeple or hurdles time at the
        #   same metres is not a flat time, and event_kind is not in the
        #   covering index -- cross country never carries one, so XC keeps
        #   its index-only read.
        if sport == "TF":
            flat_x = " AND x.event_kind IS NULL"
    sql = _PR_SQL.format(flat=flat, flat_x=flat_x,
                         outliers=_outlierSql(cur, sport))
    _timeout(cur)
    cur.execute(sql, {"pool": pool, "sport": sport, "year": year,
                      "days": days, "anchor": anchor,
                      "stds": list(STANDARD[sport]), "tol": DIST_TOL,
                      "nat_n": _SQL_NAT, "st_n": _SQL_STATE})
    rows = _dicts(cur.fetchall(), _COLS_P)
    for r in rows:
        r["check"] = r["gain"] >= CHECK_GAIN
    return rows


def _fillNames(cur, rows):
    """Athlete names and race names, for the rows the page can show."""
    ids = sorted({r["person_id"] for r in rows})
    names = {}
    if ids:
        from rankings import nameLateral
        cur.execute(f"""
            SELECT p.person_id, a.name
            FROM   unnest(%s::bigint[]) AS p(person_id)
            {nameLateral("p")}
        """, (ids,))
        names = {pid: nm for pid, nm in (_row(r, "person_id", "name")
                                         for r in cur.fetchall())}
    for r in rows:
        r["name"] = names.get(r["person_id"]) or "Unknown"
    return rows


def _fillMeets(cur, sport, rows):
    if not rows:
        return rows
    got = {}
    try:
        if sport == "XC":
            divs = sorted({r["div_id"] for r in rows if r.get("div_id")})
            cur.execute("""SELECT div_id, meet_name FROM meets
                           WHERE div_id = ANY(%s)""", (divs,))
            by_div = {d: n for d, n in (_row(x, "div_id", "meet_name")
                                        for x in cur.fetchall())}
            mids = sorted({r["meet_id"] for r in rows if r.get("meet_id")})
            by_meet = {}
            try:
                cur.execute("""SELECT meet_id, meet_name FROM meets_tfrrs
                               WHERE sport = 'XC' AND meet_id = ANY(%s)""",
                            (mids,))
                by_meet = {m: n for m, n in (_row(x, "meet_id", "meet_name")
                                             for x in cur.fetchall())}
            except Exception:                           # noqa: BLE001
                cur.connection.rollback()
            for r in rows:
                got[r["result_id"]] = (by_div.get(r.get("div_id"))
                                       or by_meet.get(r.get("meet_id")))
        else:
            divs = sorted({r["div_id"] for r in rows if r.get("div_id")})
            cur.execute("""SELECT DISTINCT ON (div_id) div_id, meet_name
                           FROM meets_tf WHERE div_id = ANY(%s)""", (divs,))
            by_div = {d: n for d, n in (_row(x, "div_id", "meet_name")
                                        for x in cur.fetchall())}
            for r in rows:
                got[r["result_id"]] = by_div.get(r.get("div_id"))
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
    for r in rows:
        r["meet_name"] = got.get(r["result_id"]) or "Race"
        r["race_href"] = raceHref(sport, r)
    return rows


def raceHref(sport, r):
    if not r.get("meet_id") or not r.get("div_id"):
        return None
    if sport == "XC":
        return f"/race/xc/{r['meet_id']}/{r['div_id']}"
    if r.get("event_id") is None:
        return f"/meet/tf/{r['meet_id']}"
    return f"/race/tf/{r['meet_id']}/{r['event_id']}/{r['div_id']}"


# ------------------------------------------------------------------ #
#  which sport, which season
# ------------------------------------------------------------------ #

def latestDates(cur):
    """{'XC': 'YYYY-MM-DD', 'TF': ...} -- the newest meet date per sport from
    homepage_recent (panels step 9c), a few hundred precomputed rows. Junk
    future years are cut off the same way panels cuts them."""
    hi = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    try:
        cur.execute("""SELECT sport, max(date) AS d FROM homepage_recent
                       WHERE date <= %s GROUP BY sport""", (hi,))
        return {s: d for s, d in (_row(r, "sport", "d")
                                  for r in cur.fetchall()) if d}
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
        return {}


def defaultSport(latest, today=None):
    """The sport whose newest meet is newer; by the calendar when neither
    says (Aug-Nov is cross country)."""
    xc, tf = latest.get("XC"), latest.get("TF")
    if xc and tf and xc != tf:
        return "XC" if xc > tf else "TF"
    if xc and not tf:
        return "XC"
    if tf and not xc:
        return "TF"
    month = (today or datetime.date.today()).month
    return "XC" if 8 <= month <= 11 else "TF"


def seasonFor(sport, latest):
    from season_year import seasonYearFromIso
    d = latest.get(sport) or datetime.date.today().isoformat()
    return seasonYearFromIso(sport, d)


# ------------------------------------------------------------------ #
#  the page's one call
# ------------------------------------------------------------------ #

def compute(cur, sport, level, days):
    """Everything for one sport + level + window, cached TTL. The state
    filter is a slice of this (pickRows) and costs nothing."""
    pool, _ = LEVELS[level]

    def run():
        latest = latestDates(cur)
        year = seasonFor(sport, latest)
        out = {"sport": sport, "level": level, "year": year, "days": days,
               "anchor": None, "breakouts": [], "prs": [],
               "error": None, "pr_error": None}
        try:
            rows, anchor = _breakoutRows(cur, sport, pool, year, days)
            out["anchor"] = anchor
            out["breakouts"] = rows
        except Exception as exc:                        # noqa: BLE001
            cur.connection.rollback()
            out["error"] = type(exc).__name__
            return out
        cur.connection.rollback()        # end the SET LOCAL transaction
        if out["anchor"]:
            try:
                out["prs"] = _prRows(cur, sport, pool, year, days,
                                     out["anchor"])
            except Exception as exc:                    # noqa: BLE001
                cur.connection.rollback()
                out["pr_error"] = type(exc).__name__
            cur.connection.rollback()
        # names and race links for every row the page could show, any state
        keep = out["breakouts"] + out["prs"]
        try:
            _fillNames(cur, keep)
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            for r in keep:
                r.setdefault("name", "Unknown")
        _fillMeets(cur, sport, keep)
        if out["anchor"]:
            a = datetime.date.fromisoformat(out["anchor"])
            out["window_lo"] = (a - datetime.timedelta(days=days - 1)).isoformat()
        return out

    # ! A FAILED OR HALF-FAILED COMPUTE IS KEPT FIVE MINUTES, NOT SIX HOURS:
    #   a statement timeout on a busy night should cost the next reader one
    #   more try, not an afternoon of "unavailable".
    val, stamp = ttlcache.get(
        ("breakouts", sport, level, days), run, ttl=_TTL,
        ttl_of=lambda v: _TTL if not (v["error"] or v["pr_error"]) else 300.0)
    return dict(val, computed_at=stamp)
