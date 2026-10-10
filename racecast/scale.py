# Project: xc-predictor / racecast
# File:    scale.py
# Purpose: The public rating -> time cheat sheet (owner, 2026-10-10, item 5):
#          one printable, linkable table per pool of ratings against the
#          1600, the 3200 and the cross country 5K (and the college
#          championship distance), from the conversions page's own maths.
#
#              /scale                 high-school boys
#              /scale?pool=college_f  one pool
#              /scale?pool=all        every pool, one after another (to print)
#
# ★ THE ROWS SPAN THE BOARDS, NOT A RANGE SOMEONE PICKED. Each pool's first
#   and last row are the top and the bottom of that pool's current national
#   board (athlete_season, the boards' own table, behind the boards' own
#   filters: rated, over the floor, US), rounded outward to a whole step. A
#   pool whose board runs 38 to 171 gets rows 35 to 175; when the field gets
#   faster the sheet grows a row by itself.
#
# ★ ON THE SCALE THE SITE SHOWS. Every rating on the site reads as an
#   HS-equivalent by default (issue #50), so the rows are round HS-equivalent
#   numbers -- the number a reader copies off a college athlete's page finds
#   its row -- with the pool's own number beside it. The time is converted on
#   the pool's own scale (own = HS / factor), exactly as the athlete header
#   converts a season rating, so the sheet and the header can never disagree.
#
# ★ THE CONVERSIONS PAGE'S ALGEBRA, NOTHING OF ITS OWN. A cell is
#   conversions._norm_from_rating then normalized_to_time at a typical venue
#   of the column's sport (no course named = that sport's default
#   difficulty), the pair fiveKForRating and /api/equivalence use.
#
# ! COMPUTED ONCE PER POOL PER SIX HOURS (ttlcache), like /what-it-takes:
#   the board's ends are two index-ordered LIMIT 1 reads per sport, the cells
#   a few hundred pure conversions. Nothing here runs per row of a board.
from flask import Blueprint, abort, render_template, request

import ttlcache
from season_floor import DEFAULT_FLOOR, OPEN_FROM, POOL_WORDS

bp = Blueprint("scale", __name__)

# Pools in the order the boards list them; the labels are the boards' own.
POOLS = (("hs_m", "HS boys"), ("hs_f", "HS girls"),
         ("college_m", "College men"), ("college_f", "College women"),
         ("ms_m", "MS boys"), ("ms_f", "MS girls"))
_POOL_KEYS = tuple(p for p, _ in POOLS)

# (key, label, metres, sport): the three every pool gets (owner's list).
BASE_COLUMNS = (("1600", "1600", 1600.0, "TF"),
                ("3200", "3200", 3200.0, "TF"),
                ("xc5k", "XC 5K", 5000.0, "XC"))
# ★ THE COLLEGE CHAMPIONSHIP DISTANCES (owner: "and 6K/8K for college"):
#   NCAA women race 6000 m and men 8000 m through the regular season and the
#   conference meets. normalize_distance.targetFor is NOT used for this --
#   under one scale (XCP_DISTANCE_BY=ability) it answers 5000 for every pool,
#   which is the anchor, not the race.
COLLEGE_XC = {"college_m": ("xc8k", "XC 8K", 8000.0, "XC"),
              "college_f": ("xc6k", "XC 6K", 6000.0, "XC")}

# ★ FIVE POINTS A ROW: about the noise of ONE race (About: "one race is noisy
#   by about four points"), so neighbouring rows are outcomes a runner can
#   actually tell apart, and a high-school pool's whole board fits a printed
#   page. ?step= takes 1, 2, 5 or 10 for a finer or coarser sheet.
STEP = 5
STEPS = (1, 2, 5, 10)

_TTL = 6 * 3600.0


def columnsFor(pool):
    cols = list(BASE_COLUMNS)
    if pool in COLLEGE_XC:
        cols.append(COLLEGE_XC[pool])
    return cols


def roundedSpan(lo, hi, step):
    """(first, last) row: the board's ends rounded OUTWARD to the step, so
    the top and the bottom runner both have a row at or past them."""
    import math
    if lo is None or hi is None:
        return None
    lo, hi = float(min(lo, hi)), float(max(lo, hi))
    first = int(math.floor(lo / step) * step)
    last = int(math.ceil(hi / step) * step)
    return max(step, first), max(step, last)


def boardEnds(cur, pool):
    """(lo, hi, year_by_sport) -- the lowest and highest season rating on the
    pool's current national boards, over both sports; None when the pool has
    no board. Each end is one index-ordered row (the boards' (pool, sport,
    year, mean_rating) index), behind the boards' own filters: a rating, the
    floor (or an open season), and a US state."""
    from rankings import US_STATES
    lo = hi = None
    years = {}
    for sport in ("XC", "TF"):
        cur.execute("""SELECT max(year) AS y FROM athlete_season
                       WHERE pool = %s AND sport = %s AND mean_rating IS NOT NULL""",
                    (pool, sport))
        row = cur.fetchone()
        y = _val(row, "y")
        if y is None:
            continue
        years[sport] = int(y)
        for order in ("DESC", "ASC"):
            cur.execute(f"""
                SELECT mean_rating AS r FROM athlete_season
                WHERE  pool = %(p)s AND sport = %(s)s AND year = %(y)s
                  AND  mean_rating IS NOT NULL
                  AND  (n_races >= %(floor)s OR year >= %(open)s)
                  AND  state = ANY(%(us)s)
                ORDER  BY mean_rating {order} LIMIT 1""",
                        {"p": pool, "s": sport, "y": int(y),
                         "floor": int(DEFAULT_FLOOR), "open": int(OPEN_FROM),
                         "us": list(US_STATES)})
            r = _val(cur.fetchone(), "r")
            if r is None:
                continue
            r = float(r)
            lo = r if lo is None else min(lo, r)
            hi = r if hi is None else max(hi, r)
    if lo is None:
        return None
    return lo, hi, years


def _val(row, key):
    if row is None:
        return None
    return row[key] if isinstance(row, dict) else row[0]


def cellTime(own_rating, pool, distance, sport):
    """Seconds at a typical venue of `sport` over `distance` for a rating on
    `pool`'s own scale; None when it does not convert."""
    import conversions as C
    try:
        norm = C._norm_from_rating(float(own_rating), pool, 0.0, sport)
        if not norm:
            return None
        t = C.normalized_to_time(norm, {"distance": float(distance),
                                        "pool": pool, "sport": sport})
        return round(float(t), 1) if t and t > 0 else None
    except Exception:                                   # noqa: BLE001
        return None


def buildTable(pool, ends, factor, step=STEP, convert=cellTime):
    """{pool, label, columns, rows: [{hs, own, times: {key: sec}}], lo, hi}
    for one pool, fastest first. `ends` is boardEnds' (lo, hi, years) on
    the OWN scale; `factor` the pool's HS factor (None = no HS twin, and the
    rows are then own-scale numbers)."""
    f = float(factor) if factor else 1.0
    cols = columnsFor(pool)
    out = {"pool": pool, "label": dict(POOLS).get(pool, pool),
           "words": POOL_WORDS.get(pool, pool), "factor": factor,
           "columns": [{"key": k, "label": lab, "distance": d, "sport": s}
                       for k, lab, d, s in cols],
           "rows": [], "lo": None, "hi": None, "years": {}}
    if not ends:
        return out
    lo, hi, years = ends
    out.update(lo=round(lo * f, 1), hi=round(hi * f, 1), years=years)
    span = roundedSpan(lo * f, hi * f, step)
    if not span:
        return out
    first, last = span
    for hs in range(last, first - 1, -step):
        own = hs / f
        times = {k: convert(own, pool, d, s) for k, _lab, d, s in cols}
        if not any(times.values()):
            continue
        out["rows"].append({"hs": hs, "own": round(own, 1), "times": times})
    return out


def _factor(pool):
    try:
        from pool_view import repFactor
        return repFactor(pool, "XC")
    except Exception:                                   # noqa: BLE001
        return None


def tableFor(cur, pool, step=STEP):
    """The cached table for one pool and step."""
    def compute():
        ends = boardEnds(cur, pool)
        return buildTable(pool, ends, _factor(pool), step)
    val, stamp = ttlcache.get(("scale", pool, int(step)), compute, ttl=_TTL,
                              ttl_of=lambda v: _TTL if v["rows"] else 300.0)
    return dict(val, computed_at=stamp)


# ------------------------------------------------------------------ #
#  the route
# ------------------------------------------------------------------ #

@bp.route("/scale")
def scale_page():
    want = (request.args.get("pool") or "hs_m").strip().lower()
    if want != "all" and want not in _POOL_KEYS:
        abort(404)
    step = request.args.get("step", type=int) or STEP
    if step not in STEPS:
        step = STEP
    pools = list(_POOL_KEYS) if want == "all" else [want]
    import psycopg2.extras
    from database import getConn
    tables = []
    try:
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for p in pools:
                    try:
                        tables.append(tableFor(cur, p, step))
                    except Exception as exc:            # noqa: BLE001
                        conn.rollback()
                        print(f"scale {p}: {type(exc).__name__}: {exc}", flush=True)
                        tables.append(buildTable(p, None, None, step))
    except Exception as exc:                            # noqa: BLE001
        # ! NEVER A 500: an explanatory page with the database away says so
        print(f"scale: {type(exc).__name__}: {exc}", flush=True)
        tables = [buildTable(p, None, None, step) for p in pools]
    return render_template("scale.html", tables=tables, want=want,
                           pools=POOLS, step=step, steps=STEPS,
                           default_step=STEP)


@bp.app_template_filter("scale_time")
def _scaleTime(seconds):
    """4:31 / 15:02 / 1:02:03 -- a conversion is a headline, not a result:
    whole seconds, as the athlete header's 5K equivalent."""
    from season_floor import clockFor
    return clockFor(seconds) or ""
