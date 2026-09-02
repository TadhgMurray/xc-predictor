"""The times/marks board (owner, 2026-09-02): hurdles and the steeple enter
ranking_results timed and unrated with their kind stamped, field events
enter by parsed mark, and the API's filters understand all three.

    python tests/test_marks_board.py
"""
import io
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# stubs so the build module and the rankings module import without a database
for name in ("database", "psycopg2", "psycopg2.extras", "psycopg2.errors"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["database"].getConn = lambda: (_ for _ in ()).throw(
    RuntimeError("no db"))
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]
sys.modules["psycopg2.errors"].UndefinedColumn = type("UndefinedColumn",
                                                      (Exception,), {})

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


# ---------------------------------------------------------------- #
# 1. the kind reader
# ---------------------------------------------------------------- #
import build_ranking_results as B                               # noqa: E402

cases = {
    "110H": ("hurdles", 110.0), "110m Hurdles": ("hurdles", 110.0),
    "Men's 110 Meter Hurdles": ("hurdles", 110.0), "300 Hurdles": ("hurdles", 300.0),
    "400mh": ("hurdles", 400.0), "55H": ("hurdles", 55.0),
    "100m Hurdles": ("hurdles", 100.0), "60 HH": ("hurdles", 60.0),
    "3000m Steeplechase": ("steeple", 3000.0), "3k steeple": ("steeple", 3000.0),
    "Women's 2000 Meter Steeplechase": ("steeple", 2000.0),
    "3000 SC": ("steeple", 3000.0),
    "1600m": (None, None), "100m": (None, None), "4x100 Relay": (None, None),
    "Shuttle Hurdle Relay": (None, None), "Shot Put": (None, None),
    "": (None, None), None: (None, None),
}
for ev, want in cases.items():
    got = B.timedEventKind(ev)
    ok(got == want, f"timedEventKind({ev!r}) = {got}, want {want}")

# ---------------------------------------------------------------- #
# 2. the published tuples line up with _COLUMNS
# ---------------------------------------------------------------- #
ok(B._COLUMNS[-2:] == ("event_kind", "mark"),
   "event_kind and mark are the last two columns, after the units")
src = read("racecast", "build_ranking_results.py")
body = src[src.index("if row.speed_rating is None:"):
           src.index("# ★ AND A RATING OUTSIDE ITS OWN POOL")]
ok(body.count("*_unitsOf(school, row.state)") == 3,
   "field, hurdle/steeple and sprint returns all carry the units")
rated_tail = src[src.index("# ★ AND A RATING OUTSIDE ITS OWN POOL"):]
ok(rated_tail.count("*_unitsOf(school, row.state)") == 1
   and "None, None)" in rated_tail, "and so does the rated return")
ok("key, float(metres))" in body and "kind, None)" in body
   and "None, None)" in body,
   "every return ends with (event_kind, mark) in that order")
ok("saneMark(key, metres)" in body and '"field_refused"' in body,
   "a mark outside its event's range is refused and counted")
ok("ADD COLUMN IF NOT EXISTS event_kind text" in src
   and "ADD COLUMN IF NOT EXISTS mark real" in src
   and "ALTER COLUMN time_seconds DROP NOT NULL" in src,
   "the live table is migrated before the shadow is shaped from it")
ok("OR COALESCE(r.is_field, 0) = 1)" in src and
   "AND COALESCE(r.is_field, 0) = 0\n" not in src[src.index('"TF": f"""'):],
   "the TF query admits field rows and no longer excludes them")
ok("rr_board_mark_idx" in src, "the marks board has an index")

# fill_ratings inverts the new WHERE
fr = read("engine", "fill_ratings.py")
tf_mark, tf_inv = __import__("fill_ratings")._MARK["TF"]
ok(tf_mark in src, "fill_ratings' TF mark still matches the build's WHERE")
ok("COALESCE(r.is_field, 0) = 0" in tf_inv,
   "and its inverse keeps field rows out of the pricing step")

# ---------------------------------------------------------------- #
# 3. the API's filter parser
# ---------------------------------------------------------------- #
import rankings as RK                                            # noqa: E402

RK._EVENT_KIND_PRESENT = True


def parse(**kw):
    args = {"board": "pr", "pool": "all", "sport": "TF", "gender": "m"}
    args.update(kw)
    return RK.parseFilters(args)


f, err = parse(distance="5000")
ok(err is None and f["event"] is None and f["sort"] == "time", f"flat 5000: {err}")
f, err = parse(distance="110", event="hurdles")
ok(err is None and f["event"] == "hurdles" and f["distance"] == 110,
   f"110 hurdles: {err}")
f, err = parse(distance="3000", event="steeple")
ok(err is None and f["event"] == "steeple", f"3000 steeple: {err}")
f, err = parse(event="shot_put")
ok(err is None and f["event"] == "shot_put" and f["distance"] is None
   and f["sort"] == "mark" and f["dir"] == "DESC",
   f"shot put ranks the mark, descending: {err} {f and f.get('sort')}")
_f, err = parse(distance="5000", event="hurdles")
ok(err is not None, "5000 m hurdles is refused")
_f, err = parse(distance="100", event="shot_put")
ok(err is not None, "a field event with a distance is refused")
_f, err = parse(event="pentathlon")
ok(err is not None, "an unknown event is refused")

# the WHERE pins the kind
params = {}
f, _ = parse(distance="110", event="hurdles")
w = RK._whereClauses(f, params, with_dates=True)
ok("event_kind = %(event_kind)s" in w and params["event_kind"] == "hurdles",
   "a hurdles board filters on event_kind")
params = {}
f, _ = parse(distance="100")
w = RK._whereClauses(f, params, with_dates=True)
ok("event_kind IS NULL" in w, "a flat 100 m board excludes the 100 m hurdles")
RK._EVENT_KIND_PRESENT = False
params = {}
w = RK._whereClauses(f, params, with_dates=True)
ok("event_kind" not in w, "and does not mention the column before the rebuild")
params = {}
f, _ = parse(event="shot_put")
w = RK._whereClauses(f, params, with_dates=True)
ok("distance BETWEEN" not in w and "event_kind = %(event_kind)s" in w,
   "a field board has no distance band")

# the performance board's flat distance never pins event_kind
RK._EVENT_KIND_PRESENT = True
f, err = RK.parseFilters({"board": "performance", "pool": "hs_m",
                          "sport": "TF", "distance": "1600"})
params = {}
w = RK._whereClauses(f, params, with_dates=True)
ok(err is None and "event_kind" not in w,
   "the performance board is untouched by the event axis")

# the template offers what the API accepts, and only there
html = read("racecast", "templates", "rankings.html")
for key in RK.PR_FIELD_EVENTS:
    ok(f'value="field:{key}"' in html, f"the selector offers {key}")
for d in RK.PR_HURDLE_DISTANCES:
    ok(f'value="{d}|hurdles"' in html, f"the selector offers {d} m hurdles")
ok(html.count("pr-only-opt") >= len(RK.PR_FIELD_EVENTS)
   + len(RK.PR_HURDLE_DISTANCES) + len(RK.PR_STEEPLE_DISTANCES),
   "every new option is pr-only")
ok("Best times/marks" in html, "the tab says marks")
js = read("racecast", "static", "rankings.js")
ok("function parseEventValue" in js and "fmtMark" in js
   and 'q.set("event", sel.event)' in js,
   "the script splits the selection and renders marks")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_marks_board: all checks passed")
