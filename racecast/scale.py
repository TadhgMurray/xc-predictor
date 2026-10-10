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
# ★ THE ROWS SPAN WHERE THE ATHLETES ARE, NOT A RANGE SOMEONE PICKED. Each
#   pool's rows run from the leader of its current national board down to
#   the board's 1st percentile (athlete_season, the boards' own table, behind
#   the boards' own filters: rated, over the floor, US), rounded outward to
#   a whole row. When the field gets faster the sheet grows a row by itself.
#
# ★ WHY THE 1st PERCENTILE AT THE BOTTOM AND THE LEADER AT THE TOP (owner,
#   2026-10-10: the high-school boys sheet ran to 35, "a 57-minute 5K, which
#   is silly"). The slow tail of a board is where the non-races live -- a
#   jog, a walk-in after an injury, a mis-linked time -- and its last one in
#   a hundred is a few dozen such seasons stretching the sheet by twenty
#   rows; dropping them keeps 99 of every 100 athletes on the board. The top
#   is the opposite: the leaders are real, reviewed, and exactly who a
#   reader looks up, so the sheet runs to the board's No. 1 rather than
#   cutting the fastest hundredth (about a thousand high-school boys).
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
import math

from flask import Blueprint, abort, render_template, request

import ttlcache
from season_floor import DEFAULT_FLOOR, OPEN_FROM, POOL_WORDS

bp = Blueprint("scale", __name__)

# Pools in the order the boards list them; the labels are the boards' own.
POOLS = (("hs_m", "HS boys"), ("hs_f", "HS girls"),
         ("college_m", "College men"), ("college_f", "College women"),
         ("ms_m", "MS boys"), ("ms_f", "MS girls"))
_POOL_KEYS = tuple(p for p, _ in POOLS)

# ★ THE EVENTS EACH LEVEL ACTUALLY RACES (owner, 2026-10-10: "why no 800m";
#   ratings exist from 600 m up, so the 800 is a rated event). (key, label,
#   metres, sport). School pools race the 800, 1600 and 3200 on the track and
#   5K cross country; college races the 800, 1500, mile and 5000 on the track
#   and its championship cross country distance -- NCAA women 6000 m, men
#   8000 m. normalize_distance.targetFor is NOT used for that -- under one
#   scale (XCP_DISTANCE_BY=ability) it answers 5000 for every pool, which is
#   the anchor, not the race.
SCHOOL_COLUMNS = (("800", "800", 800.0, "TF"),
                  ("1600", "1600", 1600.0, "TF"),
                  ("3200", "3200", 3200.0, "TF"),
                  ("xc5k", "XC 5K", 5000.0, "XC"))
_COLLEGE_TRACK = (("800", "800", 800.0, "TF"),
                  ("1500", "1500", 1500.0, "TF"),
                  ("mile", "Mile", 1609.344, "TF"),
                  ("5000", "5000", 5000.0, "TF"))
COLLEGE_COLUMNS = {"college_m": _COLLEGE_TRACK + (("xc8k", "XC 8K", 8000.0, "XC"),),
                   "college_f": _COLLEGE_TRACK + (("xc6k", "XC 6K", 6000.0, "XC"),)}

# ★ FIVE POINTS A ROW, FIXED (owner, 2026-10-10: the row-step buttons
#   confused more than they helped). Five is about the noise of ONE race
#   (About: "one race is noisy by about four points"), so neighbouring rows
#   are outcomes a runner can actually tell apart -- a finer step would
#   print differences no single race can show -- and a pool's whole board
#   fits a printed page.
STEP = 5
# the bottom of the sheet: the board's 1st percentile (see the header)
LOW_Q = 0.01

_TTL = 6 * 3600.0


def columnsFor(pool):
    return list(COLLEGE_COLUMNS.get(pool, SCHOOL_COLUMNS))


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


_BOARD_WHERE = """
    pool = %(p)s AND sport = %(s)s AND year = %(y)s
    AND mean_rating IS NOT NULL
    AND (n_races >= %(floor)s OR year >= %(open)s)
    AND state = ANY(%(us)s)"""

# ★ LANDMARK ROWS, READ OFF THE SAME BOARD (owner: "only if the data exists
#   cheaply"): the 100th athlete nationally and the middle of the board. Each
#   is one index-ordered OFFSET read on the boards' (pool, sport, year,
#   mean_rating) index, the same as the ends; no state-by-state marks (those
#   are /what-it-takes, one state at a time).
TOP_N = 100


def boardEnds(cur, pool):
    """(lo, hi, info) -- the board's 1st percentile and its leader on the
    pool's current cross country board (the season the boards default to),
    on the OWN scale, with info {year, n, top100, median}; None when the
    pool has no board. Every read is the boards' own filter set."""
    from rankings import US_STATES
    cur.execute("""SELECT max(year) AS y FROM athlete_season
                   WHERE pool = %s AND sport = 'XC' AND mean_rating IS NOT NULL""",
                (pool,))
    y = _val(cur.fetchone(), "y")
    if y is None:
        return None
    p = {"p": pool, "s": "XC", "y": int(y), "floor": int(DEFAULT_FLOOR),
         "open": int(OPEN_FROM), "us": list(US_STATES)}
    cur.execute(f"SELECT count(*) AS n FROM athlete_season WHERE {_BOARD_WHERE}", p)
    n = int(_val(cur.fetchone(), "n") or 0)
    if not n:
        return None

    def at(offset):
        """the rating `offset` places down the board (0 = the leader)"""
        cur.execute(f"""SELECT mean_rating AS r FROM athlete_season
                        WHERE {_BOARD_WHERE}
                        ORDER BY mean_rating DESC OFFSET %(o)s LIMIT 1""",
                    dict(p, o=int(max(0, min(n - 1, offset)))))
        v = _val(cur.fetchone(), "r")
        return None if v is None else float(v)

    import math
    hi = at(0)
    lo = at(n - 1 - int(math.floor(LOW_Q * n)))
    info = {"year": int(y), "n": n,
            "top100": at(TOP_N - 1) if n > TOP_N else None,
            "median": at(n // 2)}
    if hi is None or lo is None:
        return None
    return lo, hi, info


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
    for one pool, fastest first. `ends` is boardEnds' (lo, hi, info) on
    the OWN scale; `factor` the pool's HS factor (None = no HS twin, and the
    rows are then own-scale numbers)."""
    f = float(factor) if factor else 1.0
    cols = columnsFor(pool)
    out = {"pool": pool, "label": dict(POOLS).get(pool, pool),
           "words": POOL_WORDS.get(pool, pool), "factor": factor,
           "columns": [{"key": k, "label": lab, "distance": d, "sport": s}
                       for k, lab, d, s in cols],
           "rows": [], "lo": None, "hi": None, "board": {}}
    if not ends:
        return out
    lo, hi, info = ends
    out.update(lo=round(lo * f, 1), hi=round(hi * f, 1), board=info,
               n=(info or {}).get("n"))
    # each landmark marks the row it falls in: the row whose rating is at or
    # under it, within one step
    marks = {}
    # (key, the tag beside the rating, its full words for the hover)
    for key, tag, words in (("top100", f"Top {TOP_N}", f"No. {TOP_N} nationally"),
                            ("median", "Median", "The middle of the board")):
        v = (info or {}).get(key)
        if v is not None:
            marks.setdefault(int(math.floor(v * f / step) * step), []).append((tag, words))
    span = roundedSpan(lo * f, hi * f, step)
    if not span:
        return out
    first, last = span
    for hs in range(last, first - 1, -step):
        own = hs / f
        times = {k: convert(own, pool, d, s) for k, _lab, d, s in cols}
        if not any(times.values()):
            continue
        out["rows"].append({"hs": hs, "own": round(own, 1), "times": times,
                            "mark": " · ".join(t for t, _w in marks.get(hs, [])),
                            "mark_words": "; ".join(w for _t, w in marks.get(hs, []))})
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
                           pools=POOLS)


@bp.app_template_filter("scale_time")
def _scaleTime(seconds):
    """4:31 / 15:02 / 1:02:03 -- a conversion is a headline, not a result:
    whole seconds, as the athlete header's 5K equivalent."""
    from season_floor import clockFor
    return clockFor(seconds) or ""
