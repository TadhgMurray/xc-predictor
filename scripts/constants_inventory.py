"""
constants_inventory.py -- every hand-set constant, correction and switch on
the rating path, and what the state-space engine (docs/REVIEW-ENGINE-
2026-10.md §4-§5, engine/statespace.py) does with each one. Reads source
files only: no database, no pack.

    python scripts/constants_inventory.py              # writes the doc
    python scripts/constants_inventory.py --out /tmp/x.md
    python scripts/constants_inventory.py --dump       # one line per item, to read

★ WHY IT EXISTS (owner, 2026-10-24: "too many corrections ... these things
  shouldn't need corrections over and over again"). The review counts ~460
  constants and 144 switches; this lists them one by one, from the code, so
  the count is a table anyone can check and not a number in a paragraph.
★ WHAT IT LISTS.
  1. every module-level assignment in FILES whose target is ALL_CAPS
     (leading underscore allowed, tuple targets split), with file:line, a
     short value and the comment above it as its stated purpose;
  2. every XCP_ name READ in FILES (os.environ / os.getenv) or SHELL_FILES
     (${XCP_...} / $XCP_...), with where and its default.
★ HOW IT CLASSIFIES. RULES below: (file, name) first, then the name in any
  file, then NAME_PATTERNS (naming forms: paths, SQL, regexes, caches,
  column indexes). One of CATEGORIES each. An item no rule covers is
  UNCLASSIFIED, and tests/test_constants_inventory.py fails on it -- so a
  new constant cannot reach the rating path without someone saying what it
  is for and what the new engine does with it.
⚠ CONSERVATIVE BY DESIGN. REMOVED only where §4-§5 of the review name the
  mechanism that covers it. Normalisation (distance curves, era, weather,
  geometry) stays in front of the new engine until §8 point 5, and its
  constants are classified as what they are today, not as what they might
  become.
! THE NEW ENGINE IS A PROTOTYPE. "REMOVED" means "the model has a term that
  does this job", not "deleted": §8 turns each one off one at a time, each
  scored on the forward test.
"""
import argparse
import ast
import io
import os
import re
import sys
import tokenize
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
DEFAULT_OUT = os.path.join(REPO, "docs", "REVIEW-ENGINE-2026-10-CONSTANTS.md")

# the rating path, in pipeline order: normalisation, the solve, the bracket,
# go-live, fill, the boards and the site's conversions
FILES = (
    "backfill/backfill_normalize.py",
    "engine/normalize_distance.py",
    "engine/fit_distance_exponent.py",
    "engine/distance_tables.py",
    "engine/fit_era_corrections.py",
    "engine/fit_weather_correction.py",
    "engine/fit_geometry_correction.py",
    "engine/track_geometry.py",
    "engine/pool_resolve.py",
    "engine/speed_ratings.py",
    "engine/joint_solve.py",
    "engine/run_joint.py",
    "engine/bracket_engine.py",
    "engine/bracket.py",
    "engine/xc_reference.py",
    "engine/indoor_reference.py",
    "engine/joint_golive.py",
    "engine/apply_tilt.py",
    "engine/fill_ratings.py",
    "racecast/build_ranking_results.py",
    "racecast/pool_ceiling.py",
    "racecast/pool_view.py",
    "racecast/conversions.py",
    "racecast/tilt.py",
    # the checks that judge a race's distance (owner, 2026-10-24: "it should
    # never pick one outright")
    "engine/impossible_race.py",
    "engine/own_race_shift.py",
    "engine/bad_distance.py",
    "engine/propose_distances.py",
    "engine/audit_overrides.py",
    "engine/kill_bad_overrides.py",
)
SHELL_FILES = ("deploy/run_pipeline.sh", "deploy/solve_env.sh")

CAPS = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
XCP = re.compile(r"XCP_[A-Z0-9_]+")
VALUE_W = 60
PURPOSE_W = 170

CATEGORIES = OrderedDict([
    ("REMOVED", "a correction the new engine makes unnecessary; the note names the term that does its job"),
    ("FITTED", "a number the new engine estimates from data instead; the note names the estimator"),
    ("STATED", "physics or a definition the new engine keeps, stated and printed"),
    ("DATA", "data hygiene that stays: sentinels, pace bands, scope, identity, distance resolution"),
    ("DISPLAY", "presentation only: board gates, display anchors, pool scale"),
    ("INFRA", "performance and plumbing: threads, tolerances, caches, paths, SQL, timeouts"),
    ("REVIEW", "unclear; the note says why"),
])
UNCLASSIFIED = "UNCLASSIFIED"


# ------------------------------------------------------------------ #
# EXTRACTION
# ------------------------------------------------------------------ #

def _clean(text):
    """One line, no runs of spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _short(text, width):
    text = _clean(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def commentMap(src):
    """{line: (col, text, own_line)} for every comment, from the tokenizer,
    so a '#' inside a string is never read as a comment."""
    out = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            own = not tok.line[:col].strip()
            out[row] = (col, tok.string.lstrip("#").strip(), own)
    return out


def _isRule(text):
    return not text or set(text) <= set("-=*#~ ")


def commentAbove(lines, comments, lineno):
    """The stated purpose of the assignment on 1-based `lineno`: its own
    trailing comment (and one indented continuation line), then the
    column-0 comment block above it -- or above the run of assignments it
    sits in, so HUBER_SLOW / HUBER_FAST share one header. First two lines of
    the block, cleaned.
    ! INDENTED COMMENT LINES ARE CONTINUATIONS of the trailing comment on
      the code line above them, not a header for the line below."""
    parts = []
    own = comments.get(lineno)
    if own and not own[2]:
        inline = [own[1]]
        nxt = comments.get(lineno + 1)
        if nxt and nxt[2] and nxt[0] > 0:
            inline.append(nxt[1])
        parts.append(" ".join(inline))
    i = lineno - 1                        # 1-based line just above
    hops = 0
    while i >= 1 and hops < 12:
        raw = lines[i - 1]
        c = comments.get(i)
        if not raw.strip():
            break
        if c and c[2] and c[0] == 0:
            break
        i -= 1
        hops += 1
    block = []
    while i >= 1:
        c = comments.get(i)
        if not (c and c[2] and c[0] == 0):
            break
        block.append(c[1])
        i -= 1
    block = [b for b in reversed(block) if not _isRule(b)]
    if block:
        parts.append(" ".join(block[:2]))
    return _short(" · ".join(parts), PURPOSE_W)


def _targets(node):
    """[(name, value_node_or_text)] for one module-level assignment."""
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        return []
    out = []
    for t in targets:
        if isinstance(t, ast.Name):
            out.append((t.id, value))
        elif isinstance(t, (ast.Tuple, ast.List)):
            names = [e.id if isinstance(e, ast.Name) else None for e in t.elts]
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(names):
                out += [(n, v) for n, v in zip(names, value.elts) if n]
            elif (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                  and value.func.id == "range"):
                out += [(n, str(k)) for k, n in enumerate(names) if n]
            else:
                out += [(n, value) for n in names if n]
    return out


def _moduleBody(tree):
    """Module-level statements, descending into module-level if/try/with
    (but not `if __name__ == "__main__":`, which is a script's main)."""
    stack = list(tree.body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, ast.If):
            src = ast.dump(node.test)
            if "__name__" in src:
                continue
            stack[:0] = node.body + node.orelse
        elif isinstance(node, ast.Try):
            stack[:0] = node.body + [s for h in node.handlers for s in h.body] + node.orelse
        elif isinstance(node, ast.With):
            stack[:0] = node.body
        else:
            yield node


def constantsIn(path, repo=REPO):
    """Every ALL_CAPS module-level assignment in one file, in line order."""
    src = open(os.path.join(repo, path), encoding="utf-8").read()
    lines = src.splitlines()
    comments = commentMap(src)
    tree = ast.parse(src)
    out = []
    for node in _moduleBody(tree):
        for name, value in _targets(node):
            if not CAPS.match(name):
                continue
            if isinstance(value, str):
                text = value
            else:
                text = ast.get_source_segment(src, value) or ast.unparse(value)
            out.append(dict(file=path, name=name, line=node.lineno,
                            value=_short(text, VALUE_W),
                            purpose=commentAbove(lines, comments, node.lineno)))
    return out


def _environRead(node):
    """(name, default_node_or_None) when `node` reads an XCP_ variable:
    os.environ.get / .setdefault / .pop, os.getenv, os.environ[...], and
    `"XCP_..." in os.environ`."""
    def isEnviron(n):
        return isinstance(n, ast.Attribute) and n.attr == "environ"

    def lit(n):
        return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None

    if isinstance(node, ast.Call) and node.args:
        f = node.func
        hit = (isinstance(f, ast.Attribute)
               and ((f.attr in ("get", "setdefault", "pop") and isEnviron(f.value))
                    or f.attr == "getenv"))
        name = lit(node.args[0])
        if hit and name and XCP.fullmatch(name):
            return name, (node.args[1] if len(node.args) > 1 else None)
    if isinstance(node, ast.Subscript) and isEnviron(node.value):
        name = lit(node.slice)
        if name and XCP.fullmatch(name):
            return name, None
    if (isinstance(node, ast.Compare) and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn)) and isEnviron(node.comparators[0])):
        name = lit(node.left)
        if name and XCP.fullmatch(name):
            return name, None
    return None


def envReadsPython(path, repo=REPO):
    src = open(os.path.join(repo, path), encoding="utf-8").read()
    out = []
    for node in ast.walk(ast.parse(src)):
        hit = _environRead(node)
        if hit:
            name, default = hit
            d = "" if default is None else _short(ast.get_source_segment(src, default) or "", 40)
            out.append(dict(name=name, file=path, line=node.lineno, default=d))
    return out


_SH_EXPAND = re.compile(r"\$\{(XCP_[A-Z0-9_]+)(?:(:?[-=+?])([^}]*))?\}|\$(XCP_[A-Z0-9_]+)")


def envReadsShell(path, repo=REPO):
    """${XCP_X:-d}, ${XCP_X:=d}, ${XCP_X=d}, ${XCP_X:+...} and $XCP_X on
    non-comment lines. A :+ expansion passes the value on as a flag, so its
    default is the flag's own default (run_joint's argparse)."""
    out = []
    for k, raw in enumerate(open(os.path.join(repo, path), encoding="utf-8"), start=1):
        line = raw.split("#", 1)[0] if raw.lstrip().startswith("#") else raw
        if raw.lstrip().startswith("#"):
            continue
        for m in _SH_EXPAND.finditer(line):
            name = m.group(1) or m.group(4)
            op, arg = m.group(2), m.group(3)
            if op in (":-", "-", ":=", "="):
                d = _short(arg or "", 40)
                if d == "?":                 # an echo of the setting, not a default
                    d = ""
            elif op == ":+":
                d = "flag default"
            else:
                d = ""
            out.append(dict(name=name, file=path, line=k, default=d))
    return out


def collect(repo=REPO):
    consts = []
    for f in FILES:
        consts += constantsIn(f, repo)
    reads = []
    for f in FILES:
        reads += envReadsPython(f, repo)
    for f in SHELL_FILES:
        reads += envReadsShell(f, repo)
    env = OrderedDict()
    for r in sorted(reads, key=lambda r: r["name"]):
        e = env.setdefault(r["name"], dict(name=r["name"], where=[], defaults=[]))
        loc = f"{r['file']}:{r['line']}"
        if loc not in e["where"]:
            e["where"].append(loc)
        if r["default"]:
            d = f"{r['default']} ({os.path.basename(r['file'])})"
            if d not in e["defaults"]:
                e["defaults"].append(d)
    return consts, list(env.values())



# ------------------------------------------------------------------ #
# THE RULE TABLE
# ------------------------------------------------------------------ #
# ★ THE MECHANISMS, NAMED ONCE, so every note says the same thing the same
#   way (review §4.1-§4.2, §5; engine/statespace.py).
THETA = "θ state walk (per-person Kalman fitness; walk q and seam step fitted by EM)"
RACE = "nested Student-t race effect (v_r keyed (meet, div) inside venue-day w_v)"
XC_LEVEL = "measured XC level (G per level by GLS through each runner's filter; §4.4)"
IN_LEVEL = "measured indoor level (G per level by GLS; §4.4)"
ALT = "fitted altitude (α per surface by GLS, from runners seen at several elevations)"
EMV = "EM variance (τ per surface, day and race sds)"
ROWT = "Student-t row residual (IRLS weights)"
COURSE = "one course estimate c_c with its SD (the bracket engine goes, §5)"
SDGATE = "posterior-SD gate (flag, not a rating change)"
PEAK = "taper term τ·peak by GLS (the season's last race)"
CURVE = "season curve f_ℓ by GLS, curvature by EM"
SEAM = "summer-seam step J_ℓ by GLS (fall-to-spring change measured per level, as fitness)"
EVENT = "event offsets e_ℓ,d by GLS"
TILT_ABS = "the tilt reads θ on one absolute scale (statespace: h = 1 + κ(θ − θ_ref), no rails, no pool label)"
ONE_SCALE = "one 5000 m scale in the model; pools are a display transform (§5)"
GAUGE = "the gauge: a flat outdoor 400 is 0 (§4.4, stated)"
KAPPA = "tilt κ, fitted within races by the new engine (statespace fit_kappa; §4.4, §6 table 6)"
KEPT_DIST = "kept until §8 point 5: the distance curves stay in normalisation in front of the new engine"
KEPT_WX = "kept until §8 point 5 (weather as covariates in the fit, the race effect taking what the grid misses)"
KEPT_ERA = "kept: the era curve stays in normalisation; §8 does not yet schedule it (point 5 moves weather; distance follows)"
DEAD = "already dead in production (legacy ALS solve; 07_pack runs --pack-only)"
REPORT = "report or diagnostic only: changes what is printed, not a rating"
DIST_RES = ("distance resolution stays; §5: once v_r absorbs a wrong distance most "
            "overrides become a flag list (first-timer fields still need the distance)")
LEGACY_SCALE = ("§4.2 lists rescaleToPool / anchor juggling as replaced by one scale, but "
                "a row written on another pool's curve is still mis-shifted by anchorShift "
                "unless backfill and pack agree on the pool: not covered by the model itself")


DISTSEP = ("kept: correcting a race's distance is separate work (owner, 2026-10-25: "
           "\"distance corrections should probably be entirely separate\"); the new engine takes "
           "the listed distance as given and a wrong one lands in its race's own heavy-tailed "
           "effect, out of the rating")
COURSESEP = ("kept: which keys are one physical course is separate work (owner, 2026-10-25); "
             "the new engine keys cells as the pack does")
ENDUR = ("the runner's own distance curve (statespace endurance: a fourth state, eta per unit "
         "ln(d / 5000), its variance by EM)")


def _all(names, cat, note):
    return {n: (cat, note) for n in names.split()}


RULES = {
    # -------------------------------------------------------------- backfill
    "backfill/backfill_normalize.py": {
        **_all("_SKIP_ORDER _RESULT_TABLE _ONLY_CHANGED _NEW_ONLY _WRITE_PAGE _STAGING "
               "_INDEX_JOBS _INDEX_MEM _INDEX_WORKERS SWAP_LOCK_TIMEOUT SWAP_ATTEMPTS "
               "SWAP_BACKOFF _REWRITE_MAX_ROWS _REWRITE_SAMPLE_PCT _CHANGED _TOP_N "
               "_RESERVOIR_CAP _PCTS _WX_CACHE_VERSION",
               "INFRA", "plumbing: table swap, paging, census printout, caches"),
        **_all("_ID _SRC _MEET _DIV _EVENT_ID _EVENT_SHORT _TIME _GRADE _AID _DATE "
               "_PERSON _CANON _SCHOOL _RATING_POOL", "INFRA", "row tuple positions"),
        "_WX_GRID": ("INFRA", "weather grid resolution, must match the fitter's; the weather itself is " + KEPT_WX),
        "_UPLOAD_DIV_GENDER": ("DATA", "identity: an uploaded XC row's gender is its division's"),
        "_WHEELCHAIR_RX": ("DATA", "wheelchair / seated / ambulatory rows withheld"),
        "_VIRTUAL_RX": ("DATA", "virtual meets withheld: not a race on a course"),
        "_TRACK_IN_XC_RX": ("DATA", "track time trials filed as XC withheld"),
        "_REPOST_SQL": ("DATA", "SQL that finds the reposts (REPOST_DAYS)"),
        "REPOST_DAYS": ("DATA", "a weekly re-post of the same runner's same time is a duplicate"),
        **_all("_MIN_DISTANCE_M _MAX_DISTANCE_M _POOL_MAX_DISTANCE_XC _DEFAULT_POOL_MAX_XC",
               "DATA", "distance sanity band (a race lives in 600 m..12 km; per-pool XC ceiling)"),
        **_all("_SLOW_CEILING_MIN_PER_MILE _MAX_PACE_S_PER_M _ABSURD_PACE_S_PER_M _METERS_PER_MILE",
               "DATA", "pace band: 3:00..20:00 per mile"),
        **_all("_RAW_CURVE_B _RAW_CURVE_B_SHORT _RAW_CURVE_D5K _POOL_RAW_FLOOR_5K _DEFAULT_RAW_FLOOR_5K",
               "DATA", "impossible-fast sentinel: faster than the world-record curve / the pool's floor"),
        **_all("_MIN_SANE_YEAR _MAX_SANE_YEAR", "DATA", "date sentinel (0023-style years)"),
        "_TFRRS_DISTANCE_PINS": ("DATA", "tfrrs XC distance pins; " + DIST_RES),
        **_all("_AUDIT_FAST_FLOOR_5K _AUDIT_FAST_DEFAULT _AUDIT_SLOW_CEIL_S _AUDIT_MIN_ROWS "
               "_AUDIT_MISLABEL_FRAC _CORRUPT_DROP_REASONS",
               "DATA", "suspect-division audit: prints the worklist, drops nothing; the model-side "
                       "version is the |v_r| > 5% list (§5)"),
    },
    # ---------------------------------------------------- normalize_distance
    "engine/normalize_distance.py": {
        **_all("DISTANCE_EXPONENT_BY_POOL DEFAULT_EXPONENT", "STATED",
               KEPT_DIST + " (fallback exponent 1.06 when the fitted spline is absent)"),
        "EVENT_DISTANCES_TF": ("DATA", "event name -> metres"),
        "TARGET_DISTANCE_METERS": ("STATED", "definition: the 5000 m scale y is read on (§4.1)"),
        "COMMON_ANCHOR_M": ("STATED", "definition: " + ONE_SCALE + "; anchorShift moves every row onto it"),
        "FLAT_COURSE_DIFFICULTY": ("STATED", GAUGE),
        "REFERENCE_TRACK_LENGTH": ("STATED", GAUGE + " (the geometry reference)"),
        "_SPLINES": ("FITTED", KEPT_DIST + ": fitted by fit_distance_exponent, outside the new engine"),
        "_GEOMETRY": ("REMOVED", "course cell c_c per oval: the geometry spline is already OFF "
                                 "(XCP_GEOMETRY unset), the track cell carries length and banking"),
        "_ERA": ("FITTED", KEPT_ERA + "; fitted by fit_era_corrections"),
        "_WEATHER": ("FITTED", KEPT_WX + "; fitted by fit_weather_correction (two-way FE)"),
        "_SCHOOL_LEVELS": ("DATA", "identity: school -> level"),
        **_all("DISTANCE_MODES DISTANCE_BY DISTANCE_BY_SOURCE DISTANCE_RESIDUAL "
               "DISTANCE_RESIDUAL_SOURCE", "STATED",
               KEPT_DIST + " (switch: pool curves or the ability family; §8 point 5 replaces both "
                           "with one curve and a per-runner slope)"),
        "_ABILITY": ("FITTED", KEPT_DIST + ": the distance-by-ability artifact (fit_distance_ability)"),
        **_all("_SCHOOL_ABBREV _SCHOOL_PAREN _NON_SCHOOLS _PRO_EXACT _PRO_FRAGMENTS _GRAPH_LEVELS "
               "_FOREIGN_UNIVERSITY _UNAT_PREFIX GRADE_TO_LEVEL _CLASS_ALIASES POOLABLE_LEVELS "
               "_LEVEL_RANK MAX_LEVEL_JUMP _UPPERCLASS_GRADES",
               "DATA", "identity: school, grade and level -> pool (the level ℓ still keys f, J, G, e)"),
        "_SHIFT_CACHE": ("INFRA", "cache"),
        **_all("PACE_FLOOR PACE_CEIL", "DATA", "pace band the solve admits (one definition)"),
        **_all("_MILE_METERS _YARD_METERS _EVENT_NUM_RE _HURDLE_RE _KILO_RE _YARDS_RE",
               "DATA", "unit and event-name parsing"),
        "MAX_RACE_SNOW_M": ("DATA", "weather input sentinel: a grid cell under a glacier is not a race day"),
        **_all("_WMULT_MIN _WMULT_MAX", "STATED", KEPT_WX + " (accepted weather factor band 0.75..1.25)"),
        "WEATHER_TEMP_AGGS": ("STATED", KEPT_WX + " (which temperature aggregate the fit used)"),
        **_all("_WMULT_REFUSED _WNORM_CENSUS", "INFRA", "counters for the log"),
    },
    # ------------------------------------------------- fit_distance_exponent
    "engine/fit_distance_exponent.py": {
        **_all("_OV_JOIN _OV_COALESCE _OV_DROP", "DATA", "hand-verified distance overrides, generated "
                                                         "from corrections.py; " + DIST_RES),
        "MAX_DAYS_APART": ("STATED", KEPT_DIST + " (pairs within 21 d assume fitness constant; the θ walk "
                                                 "would price that once distance is in the model)"),
        **_all("MIN_DISTANCE_DIFF_METERS MIN_LOG_RATIO MIN_DISTANCE_METERS MAX_DISTANCE_METERS "
               "MIN_PAIRS_FOR_POOL_SPLINE AGG_BIN_LOG ENDPOINT_BIN_LOG KNOT_SPACING_LOG "
               "MIN_EDGE_SPAN_LOG SE_FLOOR PRIOR_PAIR_SIGMA MIN_PAIRS_PER_EDGE TUKEY_C IRLS_MAX_ITERS "
               "STABILITY_TUKEY_PROBES STABILITY_TOL POLY_DEGREE_EDGES MIN_EDGES_FOR_POOL "
               "MIN_EPS_MATCHED_TRANSITIONS SHORT_SPAN_LOG MERGE_SPORTS DEGREE_RULE "
               "DEGREE_RULE_CHOICES POLY_DEGREE_SPAN_LOG POLY_DEGREE_LOCATIONS MIN_EDGES_ANY_DEGREE "
               "K_PRIOR PRIOR_SLOPE_WEIGHT PRIOR_SLOPE_PROBES SUPPORT_SATURATION MIN_LOCAL_EXP "
               "MONOTONE_SPORTS MIN_EXT_PAIRS EXT_SLOPE_BAND MIN_EXT_GAP_LOG EXP_SANE",
               "STATED", KEPT_DIST + " (a setting or shape rule of the distance-curve fitter)"),
        "TARGET_DISTANCE_METERS": ("STATED", "definition: the 5000 m scale"),
        "POOL_TARGET_METERS": ("DISPLAY", "per-pool anchor distance: " + ONE_SCALE),
        **_all("TIME_SANE METERS_PER_MILE PACE_FLOOR_F PACE_FLOOR_M PACE_CEILING POOL_MAX_DISTANCE_XC "
               "DEFAULT_POOL_MAX_XC SANE_YEARS OUTDOOR_MONTHS",
               "DATA", "sanity bands on the fitter's input (time, pace, distance, date, indoor/outdoor month)"),
        **_all("TRANSITION_BUCKET_M DUP_TIME_BUCKET_S", "DATA", "dedup keys for the pair sample"),
        **_all("ROBUST_AUDIT_W RESIDUAL_TOP_N OVERLAP_MIN_LOG OVERLAP_AGREE_EXP OVERLAP_PROBES",
               "INFRA", REPORT),
        **_all("ITERSIZE SORT_MEM_MB _ISO_DATE _US_DATE _CUM_DAYS CACHE_FILE_SEASON_BEST", "INFRA",
               "streaming, caches and date parsing"),
    },
    # -------------------------------------------------------- distance_tables
    "engine/distance_tables.py": _all(
        "SEGMENT_KNOTS EXPONENTS BELOW_800_EXTRA", "STATED",
        "physics: the published equivalence tables (World Athletics, Purdy) as the event-offset prior; "
        "the prototype fits e_ℓ,d with no table prior, so thin classes (600, 10k) need it until §8 point 5"),
    # ---------------------------------------------------- fit_era_corrections
    "engine/fit_era_corrections.py": {
        **_all("_OV_JOIN _OV_COALESCE _OV_DROP", "DATA", "hand-verified distance overrides (corrections.py)"),
        **_all("SANE_YEAR_MIN SANE_YEAR_MAX NORM_SANE_MIN NORM_SANE_MAX DEDUP_TIME_BUCKET _YEAR_RE "
               "_KNOWN_GRADES", "DATA", "sanity bands, dedup and parsing on the era fitter's input"),
        **_all("MAX_SEASON_GAP BENCHMARK_PERCENTILE MIN_BENCH_ATHLETES TRUST_BENCHMARK_LEVEL "
               "MIN_DELTAS_PER_STEP MIN_RESULTS_PER_SEASON MIN_SHIP_BENCH_N MAX_CLAMP_REACH "
               "UNTRUSTED_BENCHMARK_SEASONS REFERENCE_REP_FRACTION LADDER_K LADDER_RETAIN_N "
               "LADDER_MIN_FINISHERS LADDER_MAX_EDGE_GAP LADDER_UNTRUSTED_EDGE_W BRIDGE_BANDS "
               "BRIDGE_TF_DOY BRIDGE_MIN_PAIRS ERA_BORROW_MAP _SEASON_BIN_DAYS",
               "STATED", KEPT_ERA + " (a setting of the era fitter)"),
        **_all("SHOE_WINDOW PLACEBO_BANDS TREATMENT_BANDS", "INFRA", REPORT + " (the shoe-era placebo table)"),
        **_all("STREAM_ITERSIZE STREAM_WORK_MEM_MB PROGRESS_EVERY _ISO_DATE _US_DATE _CUM_DAYS",
               "INFRA", "streaming and date parsing"),
    },
    # ------------------------------------------------- fit_weather_correction
    "engine/fit_weather_correction.py": {
        "GRID_DEG": ("INFRA", "weather grid resolution (0.25°)"),
        **_all("DIST_REF RACE_LOCAL_HOURS_BY_SPORT RACE_LOCAL_HOURS MIN_RACES WX_AGG WX_AGG_BY_SPORT "
               "KNOT_Q VENUE_NORM_SHRINK_K QUERIED_FEATURES FEATURES SPLINE_FEATURES "
               "LINEAR_FEATURES_BY_SPORT LINEAR_FEATURES REFERENCE",
               "STATED", KEPT_WX + " (a setting of the weather fit: window, features, knots, 13 °C reference)"),
        **_all("DEMEAN_ITERS DEMEAN_TOL STAGE _TF_META", "INFRA", "solver tolerance and SQL staging"),
        **_all("_STEP _UNIT", "INFRA", REPORT),
    },
    # ------------------------------------------------ fit_geometry_correction
    "engine/fit_geometry_correction.py": {
        **_all("ANCHOR_WEIGHT LENGTH_SMOOTHING MIN_POINTS_2D MIN_DISTINCT_2D MIN_POINTS_1D CV_FOLDS "
               "MIN_FIT_TRACK_LENGTH SHAPE_EDGES SHAPE_LABELS MIN_BAND_N MIN_BANKING_SPLINE_N ADOPT_ASSUMED",
               "REMOVED", "course cell c_c per oval: this fitter's spline is already OFF in production "
                          "(XCP_GEOMETRY unset); the cell carries length and banking, today and in the new engine"),
        "REFERENCE_LENGTH": ("STATED", GAUGE),
    },
    # --------------------------------------------------------- track_geometry
    "engine/track_geometry.py": {
        **_all("FLAT_400 LENGTH_TOLERANCE_M BANKED UNKNOWN_LENGTH_MODES UNKNOWN_LENGTH_DEFAULT",
               "STATED", GAUGE + ": which ovals are the flat outdoor 400 (an unrecorded length is assumed 400)"),
        **_all("GEO_CLASSES GEO_SMALL_BELOW_M GEO_LARGE_FROM_M INDOOR_LEVELS_FILE",
               "REMOVED", IN_LEVEL + "; each oval's own cell c_c carries its geometry (§4.2: the geometry levels)"),
    },
    # ---------------------------------------------------------- pool_resolve
    "engine/pool_resolve.py": {
        "NO_POOL_VERDICTS": ("DATA", "grade_sanity verdicts that leave a season with no pool (one list, "
                                     "the site imports it)"),
        **_all("PRO_ABILITY_5K ABILITY_CURVE_POOL ABILITY_CURVE_SPORT", "DATA",
               "scope: the professional pool by ability (sub-14:00 / sub-15:30)"),
        **_all("US_STATES US_NAMES NATIONAL_TEAMS AMBIGUOUS_NATIONAL_TEAMS _NT_SUFFIX _PARA_SCHOOL",
               "DATA", "scope: US athletes only, national teams and adaptive competition out"),
        **_all("_SUB_HS_LEVELS _SCHOOL_LEVELS _PRO_LEVELS _LEVEL_RANK _PRO_TEAMS _PUNCT _SPACE "
               "_PRO_SEASONS UNATTACHED_TEAM_ID", "DATA", "identity: level promotions, pro teams, named pros"),
    },
    # --------------------------------------------------------- speed_ratings
    "engine/speed_ratings.py": {
        "SPORTS": ("INFRA", "sport list"),
        "DECAY_K": ("REMOVED", THETA + "; " + DEAD + " (the pack's weight column is not read by the joint solve)"),
        "DECAY_K_COURSE": ("REMOVED", "course eras + " + EMV + "; " + DEAD),
        **_all("REGION_SHRINK_K LINK_SHRINK_K RIDGE_LAMBDA ROBUST_C", "REMOVED", EMV + "; " + DEAD),
        "OUTLIER_STD_THRESHOLD": ("REMOVED", ROWT + "; " + DEAD),
        **_all("MIN_RACES MIN_COURSE_ATHLETES MIN_COURSE_LINKS", "REMOVED",
               SDGATE + "; " + DEAD + " (MIN_COURSE_ATHLETES / _LINKS are not read anywhere)"),
        **_all("ANCHOR_ON_MAPPED_ONLY REGION_REF_COUNTS_CSV TERRAIN_ANCHORS", "REMOVED",
               "the gauge + " + XC_LEVEL + "; " + DEAD),
        **_all("ABILITY_MIN ABILITY_MAX CONVERGENCE_THRESHOLD WORST_TOLERANCE SWEEP_ITERATIONS "
               "MAX_ITERATIONS PATIENCE DAMPING ROBUST_SAMPLE", "INFRA", "solver rails and iteration; " + DEAD),
        **_all("_RID _PID _NORM _GRADE _SRC _SCHOOL _DATE _SPORT _VENUE _GENDER _DIST _MEETCLASS "
               "_TIME _TEAM _SLUG _MEET _DIV", "INFRA", "pack column positions"),
        **_all("_ANET_LEVELS _CLUB_PROS _CLUB_MAJORITY _PRO_TEAMS _UNATTACHED_LEVEL _PRO_SEASONS "
               "_PRO_LEVELS _COLLEGE_FIRST _SCHOOL_LEVELS _UPPER_FIRST _GRADE_UNTRUSTED _SUB_HS_LEVELS "
               "_SEASON_LEVELS", "DATA", "identity: the pool each row is rated in (tables loaded at pack time)"),
        **_all("_SCALE_TOL _SCALE_IDENTIFY_TOL _SCALE_IDENTIFY_GAP _SCALE_SAME_SIZE _SCALE_POOLS",
               "REVIEW", "rescaleToPool: a row stored on another pool's anchor is moved at pack time. "
                         + LEGACY_SCALE),
        **_all("_BINCOUNT_WORKERS _BINCOUNT_POOL _NRACES_CACHE _LOGNORM_CACHE _COURSEIDX_CACHE "
               "_COMPACT_CACHE _ANCHOR_CACHE", "INFRA", "threads and caches"),
    },
    # ----------------------------------------------------------- joint_solve
    "engine/joint_solve.py": {
        **_all("HUBER_SLOW HUBER_FAST", "REMOVED", ROWT + " (§4.2: Huber's two constants)"),
        "TILT_K": ("FITTED", KAPPA),
        **_all("TILT_RATING_LO TILT_RATING_HI", "REMOVED", TILT_ABS),
        "SPORT_RIDGE": ("FITTED", "grass specialisation β_p ~ N(0, s_β²), s_β by EM"),
        "XC_TRACK_GAP": ("REMOVED", XC_LEVEL + "; only if the leave-one-kind-out fits agree (§5)"),
        **_all("CURVE_KNOT_DAYS CURVE_N_KNOTS CURVE_SMOOTH CURVE_SD2_START", "FITTED",
               CURVE + " (statespace knots every 15 d: a resolution, not a level)"),
        "CURVE_REF_KNOT": ("STATED", "definition: the season curve's zero at 1 October (statespace PIN_PHASE 60)"),
        "ACADEMIC_YEAR_START_DOY": ("STATED", "definition: season phase counted from 1 August (same in statespace)"),
        **_all("CURVE_GAP_WEIGHT WINTER_GAIN SPORT_GAIN_BANDS SPORT_GAIN_ANCHORS SPORT_GAIN_MIN_ATHLETES "
               "SPORT_GAIN_ROW_Q SPORT_GAIN_MIN_ROWS", "REMOVED", SEAM + " and " + XC_LEVEL),
        **_all("DIST_PRIOR_SD DIST_WALK_SD DIST_CAL_SHARE DIST_CAL_MIN_PAIRS", "FITTED",
               EVENT + " (prototype: coarse classes per level, 1000-2000 m pinned)"),
        **_all("DIST_BANDS DIST_N_BAND DIST_BAND_ANCHORS", "REMOVED",
               ENDUR + ": the event offsets by rating band were the population's guess at it"),
        "RACE_DAY_CAP": ("REMOVED", RACE + " (§5: all four of its jobs)"),
        "DIVISION_TABLE_COLS": ("INFRA", "division-guard table columns (the guard itself goes: " + RACE + ")"),
        **_all("THETA_RACE_PRIOR_N EFFORT_THETA_FLOOR EFFORT_SHORT_CUT EFFORT_INNER_ITERS "
               "EFFORT_FAST_BEFORE_DAY EFFORT_CLASS_NAMES EFFORT_GROUP_NAMES", "REMOVED",
               ROWT + " (§4.2: the effort term; off in production)"),
        **_all("_ERFC_COEF _SQRT2 _SQRT2PI _MILLS_CF_Z _MILLS_CF_TIERS", "INFRA", "numerics (erfc, Mills ratio)"),
        "SLOPE_RIDGE": ("REMOVED", ENDUR + " (the ridge becomes a fitted prior variance)"),
        **_all("LINK_WEIGHT SEASON_TIE_BANDS SEASON_TIE_MIN_PAIRS", "REMOVED", THETA + " (§5: the season tie)"),
        "TILT_SCALES": ("REMOVED", TILT_ABS + " (§5: XCP_TILT_SCALE)"),
        "ALT_FLOOR_M": ("STATED", "physics: altitude costs nothing below 600 m (statespace keeps it)"),
        "ALT_RIDGE": ("INFRA", "numerical ridge on the altitude block"),
        **_all("ALT_PRIOR_MEAN ALT_PRIOR_PEN_FIXED ALT_PRIOR_PEN_FIT", "FITTED", ALT + " (§4.2: ALT_PRIOR_MEAN)"),
        "ALT_ACCLIM": ("REMOVED", ALT + ": a resident's state is their sea-level fitness (§4.2)"),
        "ALT_DIST_KNOTS": ("STATED", "physics: the event shares of the altitude cost (NCAA tables; statespace keeps them)"),
        **_all("IMP_PRIOR_MEAN IMP_EXPECTED IMP_PRIOR_SD", "REMOVED", PEAK + " (importance term off in production)"),
        **_all("FIELD_TOP_K FIELD_UNIT FIELD_CLIP FIELD_EXPECTED", "REMOVED",
               "the runners' trajectories + " + RACE + " (field term off in production)"),
        **_all("IND_PRIOR_MEAN IND_PRIOR_SD IND_LEVEL_DEFAULT", "REMOVED",
               IN_LEVEL + "; only if the leave-one-kind-out fits agree (§5)"),
        **_all("AMP_TILT_PER_POINT AMP_FLOOR AMP_CEIL", "REMOVED",
               THETA + ": each runner's own trajectory (§4.2: the amplitude tilt and its rails)"),
        "POOL_MEAN_MIN_RACES": ("DISPLAY", "who votes in the pool mean that 100 is anchored on; "
                                           "§8 point 1 keeps rating = 100·pm/exp(perf)"),
        **_all("CG_TOL CG_MAX_ITER CG_TOL_OUTER CG_TOL_PROBE CG_MAX_ITER_PROBE _N_THREADS _PRED_POOL",
               "INFRA", "CG tolerances and threads (the new fit has no CG)"),
        **_all("ABILITY_BANDS ABILITY_W_FLOOR ABILITY_W_CEIL", "REMOVED", ROWT + " (§4.2: the ability weights)"),
        "SIGMA_U_FLOOR": ("FITTED", EMV + " (already {0, 0})"),
        "TAU_MAX_DEFAULT": ("FITTED", EMV + " (§4.2: τ caps)"),
        "ERA_YEARS_DEFAULT": ("STATED", "course eras of N years: the prototype splits by era (--era-years); "
                                        "§8 point 4 adds the walk"),
        "ERA_DRIFT_SD": ("FITTED", "the course eras' walk, cross-validated on the forward scorecard once added "
                                   "(§4.7, §8 point 4)"),
        "MEASURED_TRUE_SD": ("INFRA", REPORT + " (split-half course spread, printed beside τ)"),
    },
    # ------------------------------------------------------------- run_joint
    "engine/run_joint.py": {
        "_HERE": ("INFRA", "path"),
        **_all("_NO_RACE_TERM _RACE_KEY", "REMOVED", RACE + " (run state for the race key / race term)"),
        "DIST_REF": ("STATED", "definition: the event offset pinned at the 1600 (prototype: the 1000-2000 class)"),
        **_all("SEASON_END_DAYS SEASON_END_MIN_RACES", "REMOVED",
               PEAK + " (these define the importance term's season-end share; off in production)"),
        "SEASON_CLOSED_DAYS": ("STATED", "a season raced in within 21 days of the data's end may still "
                                         "be running; statespace.SEASON_CLOSED_DAYS keeps its newest race "
                                         "from reading as the taper"),
        "INDOOR_PAIR_MAX_DAYS": ("INFRA", REPORT + " (indoorTransitionCheck: last indoor vs first outdoor race)"),
        **_all("VENUE_DIST_SD VENUE_DIST_REF VENUE_DIST_MIN_GAP", "DATA", COURSESEP),
        "_ALT_FIT": ("FITTED", ALT + " (run state for --altitude-fit)"),
        **_all("TILT_REPORT_BANDS FIELD_REPORT_BANDS HOLDOUT_SEASON_BUCKETS CROSS_SPORT_DISTANCES _MONTH_ENDS "
               "_MONTHS DIST_GAP_BUCKETS CE_BANDS TILT_RATING_BANDS TILT_CE_BANDS", "INFRA", REPORT),
        "_TILT_REP_DIST": ("REMOVED", TILT_ABS + " (the distance the hs-scale tilt factor is read at)"),
        **_all("TRACK_POP_MIN_SHARE TRACK_POP_MIN_CELLS", "REVIEW",
               "recentres each host population's outdoor tracks (college ovals read easy via tapered finals): "
               "plausibly covered by τ·peak, v_r and one θ across pools, but §5 does not name it; "
               "check the per-population track mean on the new fit"),
        "_STATE_F32": ("INFRA", "solve state on disk"),
    },
    # -------------------------------------------------------- bracket_engine
    "engine/bracket_engine.py": {
        **_all("RACE_SAT PRIOR_GROUP PRIOR_RACES PRIOR_GROUP_BY PRIOR_ATHLETE PRIOR_ATHLETE_RANGE "
               "PRIOR_ATHLETE_MIN_ATHLETES PRIOR_TARGET PRIOR_TARGETS DAY_NOISE_DEFAULT DAY_NOISE_CHOICES "
               "PRIOR_FIT PRIOR_FIT_WARMUP PRIOR_FIT_RANGE PRIOR_FIT_MIN_COURSES",
               "REMOVED", COURSE + "; " + EMV + " (§4.2: priors stated in races, RACE_SAT)"),
        "FIELD_ADJUST": ("REMOVED", "the runners' trajectories say what a deep field should run that week; "
                                    + RACE + " (§3: field adjust vs field check vs day)"),
        **_all("GAUGE_DEFAULT GAUGE_CHOICES", "STATED", GAUGE),
        **_all("GAUGE_SCOPES GAUGE_SCOPE_DEFAULT", "REMOVED", XC_LEVEL + ": XC's zero is no longer its own see-saw"),
        **_all("PRIOR_GROUP_NAMES PG_XC PG_OUTDOOR PG_INDOOR", "INFRA", "group labels and indices"),
        **_all("INDOOR_CENTRE INDOOR_MODES INDOOR_MODE_DEFAULT INDOOR_GATES INDOOR_GATE_MODES "
               "INDOOR_GATE_MODE_DEFAULT", "REMOVED", IN_LEVEL + " (§5: indoor centre / pin / gates)"),
        **_all("XC_LEVEL_MODES XC_LEVEL_MODE_DEFAULT", "REMOVED", XC_LEVEL),
        **_all("PLACE_RADIUS_M PRIOR_PLACE", "DATA", COURSESEP + " (the 400 m place prior)"),
        **_all("SIBLING_DIST_TOL SIBLING_DEFAULT", "DATA", COURSESEP + " (Mt. SAC 4715/4828)"),
        **_all("TILT_BANDS RACE_BUCKETS WELL_KNOWN_RACES WELL_KNOWN_BUCKETS", "REMOVED",
               COURSE + "; κ stated and the course scale is not re-read (§3: 'implied = applied' re-estimates the tilt)"),
    },
    "engine/bracket.py": _all("_BIG PACK_COLUMNS", "INFRA", "composite-key arithmetic and pack column list"),
    "engine/xc_reference.py": {
        "_ROOT": ("INFRA", "path"),
        "XC_REFERENCE": ("REMOVED", XC_LEVEL + " (named XC courses asserted as the zero; empty today)"),
    },
    "engine/indoor_reference.py": {
        "_ROOT": ("INFRA", "path"),
        "_RESOLVED": ("INFRA", "cache"),
        **_all("INDOOR_REFERENCE INDOOR_REFERENCE_NAMES", "REMOVED",
               IN_LEVEL + " (Boston University's oval asserted at 0.0)"),
    },
    # ---------------------------------------------------------- joint_golive
    "engine/joint_golive.py": {
        "DAY_SOFT_TRIAL": ("INFRA", REPORT + " (dayConsistencyReport's trial thresholds)"),
        "XC_COURSE_SUSPECT": ("REMOVED", SDGATE + " (§5, §8 point 2: the course guard becomes a flag)"),
        **_all("FIELD_CHECK_MIN FIELD_CHECK_FLOOR FIELD_CHECK_CAP FIELD_CHECK_FLOOR_OPENER", "REMOVED",
               RACE + ": the race reads fast because the runners' θ say what they should run that week (§5)"),
        **_all("ISLAND_CHECK_MIN ISLAND_CHECK_FLOOR ISLAND_CHECK_CAP", "REMOVED",
               THETA + " carried through the island, and the " + SDGATE + " where nothing connects "
               "(§5; gating thin eras is the owner's call)"),
        **_all("BROAD_MIN_ROWS BROAD_MIN_DIVS BROAD_AGREE", "REMOVED",
               RACE + " (broadSlowRaces: is a slow day shared by every division)"),
    },
    # ------------------------------------------------------------ apply_tilt
    "engine/apply_tilt.py": {
        "_HERE": ("INFRA", "path"),
        "TILT_K": ("FITTED", KAPPA),
        **_all("H_MIN H_MAX", "REMOVED", TILT_ABS),
        **_all("_DIST_SNAP _PHASES REBUILD_ABOVE MIN_SHIFT DELTA_FLOOR", "INFRA",
               "join key and speed-ups (a move under 0.05 points is skipped, not approximated)"),
    },
    # ---------------------------------------------------------- fill_ratings
    "engine/fill_ratings.py": {
        **_all("_K_SAMPLED _K_EXACT", "DISPLAY",
               "the pool constant an unsolved row is priced at (the pool's median course); a display "
               "anchor while ratings keep the pool scale"),
        **_all("_TABLE _MARK _MIN_SAMPLE _VENUE_ROWS _UNRATED _TEAM_ANCHOR", "INFRA", "SQL and sampling"),
    },
    # ------------------------------------------------ build_ranking_results
    "racecast/build_ranking_results.py": {
        "_HAVE_RATING_POOL": ("INFRA", "whether the result tables carry rating_pool (set per build)"),
        **_all("_PHASES _FETCH_BATCH _COPY_BATCH _INDEX_JOBS _SWAP_LOCK_TIMEOUT_MS _SWAP_ATTEMPTS "
               "_SWAP_BACKOFF _COLUMNS _TF_DISTANCE _UNIT_COLS _UNITS _TEAM_CACHE _SCHOOL_OK _COPY_ESCAPES "
               "_GATE _TIMED_KIND _LOAD_TABLE _LOAD_SEASON _CANONICAL_INDEXES _BOARD_SIZE_SPORTS "
               "_BOARD_SIZE_BOARDS _ANCHOR _SEASON_WORK_MEM _SEASON_SHARDS _SEASON_SHARD_WORK_MEM "
               "_EVENT_ID", "INFRA", "plumbing: batches, swaps, caches, indexes, counters"),
        **_all("_RATING_MIN _RATING_MAX", "DISPLAY", "board rails: a rating outside 20..200 is not shown"),
        "RACE_MARGIN": ("DISPLAY", "board gate: one race may sit 10 above the pool ceiling; §5: an SD gate "
                                   "does it honestly, keep as a sanity net at first"),
        **_all("HS_XC_MAX_DISTANCE HS_XC_DISTANCE_TOL _HS_POOLS _SPRINT_MAX_DISTANCE", "DATA",
               "scope: HS XC over 5,250 m and sprints are not ranked (rated, not refused)"),
        **_all("_HURDLE_RX _STEEPLE_RX _STEEPLE_NUM_RX _RELAYISH_RX _WHEELCHAIR_RX _CLUB_NAME_RE", "DATA",
               "event, wheelchair and club parsing"),
        **_all("_NAME_ALIAS _SCHOOL_PIN _SCHOOL_PIN_PEOPLE", "DATA", "identity: one team, one spelling"),
        **_all("_SQL _GATE_JOINS _XC_TFRRS_DIST_SQL", "DATA",
               "SQL that carries board scope: distance overrides and tfrrs distances, grade trust, "
               "season verdict, pro flags"),
        "_ATHLETE_SEASON_SQL": ("REVIEW", "the season aggregate (see _SEASON_Q): with θ a season number "
                                          "could be the smoothed state, which the review does not decide"),
        **_all("_SEASON_Q _SEASON_OUTLIER_PTS _DECAY_K", "REVIEW",
               "the season and career numbers on the boards (80th percentile, 20-point drop, 0.996/day "
               "decay): with θ a season number could be the smoothed state, which the review does not decide"),
    },
    "racecast/pool_ceiling.py": _all(
        "POOL_CEILING DEFAULT_CEILING", "DISPLAY",
        "board ceiling per pool; §5: an SD gate does it honestly, keep as a sanity net at first"),
    "racecast/pool_view.py": {
        **_all("_FACTOR_LO _FACTOR_HI _MOVES _REP_DIST _MAP_GRID _REF", "DISPLAY",
               "the public scale (HS-equivalent factor or equal-time map): " + ONE_SCALE),
        **_all("_FACTOR_CACHE _FACTOR_WHY _FAILED _STAMPED_TIMEOUT_MS _CONST_CACHE _CONST_SAMPLE "
               "_CONST_MIN_ROWS _CONST_FILE _CONST_FILE_TTL _NONE_UNTIL _NONE_RETRY_S _REFRESHING _GROUP "
               "_GROUP_TTL _MAP_CACHE", "INFRA", "caches, samples and timeouts"),
    },
    "racecast/conversions.py": {
        **_all("_POOL_MEAN_CACHE _SAMPLE_PER_POOL _OFFSET_TTL", "INFRA", "caches and samples"),
        **_all("_DIST_BANDS _BAND_ANCHORS", "REMOVED", "mirrors joint_solve.DIST_BANDS; " + ENDUR),
        "_TILT_K": ("FITTED", KAPPA + " (mirror)"),
        **_all("_TILT_LO _TILT_HI", "REMOVED", TILT_ABS),
        "_DEFAULT_DIFFICULTY_CACHE": ("REMOVED", XC_LEVEL + " (§4.2: the conversions' XC median effect)"),
        **_all("EQUIV_RATINGS READER_XC_M READER_TF_M FIVE_K_TITLE", "DISPLAY", "the reader's time beside a rating"),
        "FIVE_K_M": ("STATED", "definition: the 5K column is at 5000 m on a flat outdoor 400 (§4.3)"),
        "XC_TRACK_M": ("REMOVED", "the 5K column becomes five_k_s = exp(perf) at 5000 m for every level, "
                                  "no curve read-back (§4.3, §8 point 1)"),
    },
    "racecast/tilt.py": {
        "TILT_K": ("FITTED", KAPPA + " (mirror)"),
        **_all("H_MIN H_MAX", "REMOVED", TILT_ABS),
        "TILT_SCALES": ("REMOVED", TILT_ABS + " (§5: XCP_TILT_SCALE)"),
    },
    # ------------------------------------------- the distance judges
    "engine/impossible_race.py": {
        "_ROOT": ("INFRA", "path"),
        **_all("PREFILTER_PACE XC_DIST TF_DIST", "DATA",
               "a time past the world record with no plausible distance is a wrong time: data "
               "hygiene that stays (a record-beating race the mixture explains is rated, not dropped)"),
        "_DDL": ("INFRA", "SQL"),
    },
    "engine/own_race_shift.py": {
        **_all("WINDOW WIDE_WINDOW SLOW_CONDEMN MIN_LINKED _WELL_LINKED FAMILY_ALPHA _CONVENTIONS",
               "DATA", DISTSEP),
        "K": ("STATED", "the distance exponent, 1.06 (Riegel)"),
        "LOAD_SQL": ("INFRA", "SQL"),
    },
    "engine/bad_distance.py": {
        **_all("_HERE _ROOT", "INFRA", "path"),
        "_CONVENTIONS": ("DATA", DISTSEP),
        "_SQL": ("INFRA", "SQL"),
    },
    "engine/propose_distances.py": {
        **_all("CSV_PATH MARKER STALE_TOL", "INFRA", "output file and its marker"),
        "K": ("STATED", "the distance exponent, 1.06 (Riegel)"),
        **_all("MIN_ROWS MIN_CLASS OUTLIER MIN_GAIN MATCH_TOL MIN_CHANGE MIN_CORROBORATE SANE "
               "WRITE_MIN_CORROBORATE WRITE_MAX_AFTER WRITE_MAX_RATIO EXTREME_ERR EXTREME_CORROBORATE "
               "MERGED_IQR MERGED_MIN_SIDE _FLOOR_CONVENTIONS",
               "DATA", DISTSEP),
        **_all("_DISTANCES _BUILD_SHIFT _SHIFT_EXTRA _HAS_SHIFT_COLUMN _LOAD _MERGED_SQL _SCALE_TABLE "
               "_VERIFY_MERGED", "INFRA", "SQL"),
    },
    "engine/audit_overrides.py": {
        **_all("PATH CSV_PATH", "INFRA", "path"),
        "K": ("STATED", "the distance exponent, 1.06 (Riegel)"),
        **_all("MIN_ROWS MIN_CLASS OUTLIER MIN_GAIN SANE_DISTANCE ADD_OUTLIER ADD_SNAP_TOL "
               "ADD_MIN_CHANGE ADD_MIN_ROWS LADDER SUPPRESS_MIN SUPPRESS_GAP ENGINE_RAIL",
               "DATA", DISTSEP),
        **_all("_DISTANCES _RATING_STATS _LOAD _ENTRY _LEFTOVER _WHY_SHIFT _WHY_SHIFT_WHO",
               "INFRA", "SQL and parsing"),
    },
    "engine/kill_bad_overrides.py": {
        "PATH": ("INFRA", "path"),
        "K": ("STATED", "the distance exponent, 1.06 (Riegel)"),
        **_all("MIN_ROWS MIN_CLASS OUTLIER MIN_GAIN SANE_DISTANCE", "DATA",
               DISTSEP),
        **_all("_BUILD_SHIFT _LOAD", "INFRA", "SQL"),
    },
}

# ★ NAMING FORMS: a constant whose NAME says it is plumbing. Checked after
#   RULES, so a form never overrides a named decision.
NAME_PATTERNS = (
    (re.compile(r"(_SQL|_DDL|_LATERAL|_JOIN|_JOINS|_SQL_[A-Z]+)$"), "INFRA", "SQL text"),
    (re.compile(r"^_?(HERE|ROOT)$|(_DIR|_FILE|_PATH|_ART|_TMPL|_CSV|_OUT|_CACHE)$|^(OUTPUT|SPLINE_OUT|"
                r"DEFAULT_OUT|DEFAULT_DATA_DIR|LENGTH_CSV|BANKING_CSV|APPLIED_PATH|CACHE_DIR|"
                r"ARTIFACT_TMPL|_WX_CACHE|_WEATHER_ART|_ABILITY_FILE)"), "INFRA", "path or cache"),
    (re.compile(r"^_(PG_EMPTY|GENDER_TEMP_SQL|TF_STATE_TEMP_SQL|XC_TFRRS_DIST_SQL|REPOST_SQL|SQL)$"),
     "INFRA", "SQL text"),
)

# ★ THE SWITCHES. Same categories; a switch is classified by what it turns.
ENV_RULES = {
    **_all("XCP_SPORT_LEVEL XCP_BRACKET_XC_LEVEL XCP_BRACKET_XC_LEVEL_MODE XCP_GAUGE_SCOPE XCP_MERGE_SPORTS",
           "REMOVED", XC_LEVEL),
    **_all("XCP_SPORT_LEVEL_POOLS XCP_WINTER_GAIN XCP_WINTER_GAIN_BANDS XCP_SPORT_GAIN_STAT "
           "XCP_CONVERT_SPORT_GAIN", "REMOVED", SEAM + " and " + XC_LEVEL),
    **_all("XCP_INDOOR_LEVEL XCP_NO_INDOOR XCP_BRACKET_INDOOR_CENTRE XCP_BRACKET_INDOOR_GATES "
           "XCP_BRACKET_INDOOR_LEVELS XCP_BRACKET_INDOOR_MODE", "REMOVED", IN_LEVEL),
    **_all("XCP_DIFFICULTY XCP_BRACKET_PRIOR XCP_DAY_NOISE XCP_COURSE_SCALE XCP_BRACKET_FIELD",
           "REMOVED", COURSE),
    "XCP_BRACKET_WINDOW": ("REMOVED", THETA + " (§4.2: the walk's variance is the window, fitted)"),
    **_all("XCP_BRACKET_PLACE_RADIUS XCP_BRACKET_PLACE_PRIOR XCP_BRACKET_PLACE_VENUE XCP_BRACKET_SIBLING_TOL "
           "XCP_VENUE_DIST_SD", "DATA", COURSESEP),
    **_all("XCP_RACE_KEY XCP_RACE_EFFECT_OWN XCP_DAY_DIVISION_GUARD XCP_DAY_DIVISION_SHRINK XCP_DAY_SOFT "
           "XCP_DAY_PRIOR", "REMOVED", RACE),
    "XCP_RACE_EFFECT_SPORTS": ("FITTED", "whether the track race day is credited: cross-validated on the "
                                         "forward scorecard (§4.7); the XC day is " + RACE),
    "XCP_OWN_SHIFT_SINCE": ("DATA", DISTSEP + " (the season own_race_shift starts judging from)"),
    "XCP_DAY_CENTRE": ("REMOVED", THETA + " (§5: season centring; a fast season is fitness)"),
    **_all("XCP_FIELD_CHECK XCP_FIELD_CHECK_MIN XCP_FIELD_CHECK_FLOOR XCP_FIELD_CHECK_CAP "
           "XCP_FIELD_CHECK_FLOOR_OPENER", "REMOVED", RACE + " read against the runners' θ (§5: field check)"),
    **_all("XCP_ISLAND_CHECK XCP_ISLAND_CHECK_MIN XCP_ISLAND_CHECK_FLOOR XCP_ISLAND_CHECK_CAP",
           "REMOVED", THETA + " + " + SDGATE + " (§5: island check)"),
    "XCP_XC_COURSE_SUSPECT": ("REMOVED", SDGATE + " (§5: course guard)"),
    **_all("XCP_IMPORTANCE XCP_NO_IMPORTANCE", "REMOVED", PEAK + " (off in production: none)"),
    "XCP_EFFORT": ("REMOVED", ROWT + " (off in production)"),
    **_all("XCP_FORM_TREND XCP_SEASON_TIE XCP_SPLIT_ABILITY XCP_CENTRE_CURVE", "REMOVED",
           THETA + " (§5: form trend, season tie; per-season ability)"),
    "XCP_TILT_SCALE": ("REMOVED", TILT_ABS + " (§5)"),
    "XCP_TRACK_LEVEL_BY_POOL": ("REVIEW", "see TRACK_POP_MIN_SHARE: plausibly covered, not named in §5"),
    **_all("XCP_CURVE_SMOOTH", "FITTED", CURVE),
    **_all("XCP_TAU_MAX XCP_TF_TAU", "FITTED", EMV + " (§4.2: τ caps)"),
    "XCP_ERA_YEARS": ("STATED", "course eras of N years: the prototype splits by era; §8 point 4 adds the walk"),
    "XCP_ERA_DRIFT": ("FITTED", "the course eras' walk, cross-validated once added (§4.7)"),
    "XCP_DIST_WALK": ("FITTED", EVENT),
    "XCP_NO_DIST_TABLE": ("STATED", "the published-table prior on the event offsets (distance_tables); "
                                    "kept until §8 point 5"),
    **_all("XCP_ALTITUDE XCP_ALTITUDE_FIT", "FITTED", ALT),
    **_all("XCP_GAUGE XCP_GAUGE_UNKNOWN_LENGTH XCP_GAUGE_FALLBACK", "STATED", GAUGE),
    "XCP_GEOMETRY": ("REMOVED", "course cell c_c per oval (the geometry spline is already off)"),
    **_all("XCP_DISTANCE_BY XCP_DISTANCE_RESIDUAL", "STATED", KEPT_DIST + " (which distance curve family)"),
    **_all("XCP_WEATHER_FIT XCP_MUD_SENS XCP_TF_TEMP_AGG", "STATED", KEPT_WX),
    "XCP_WEATHER_ALLOW_STALE": ("INFRA", "lets normalisation run on a stale weather artifact"),
    **_all("XCP_TEAM_POOL", "DATA", "scope: a team with under 15 athletes all-time pools professional"),
    **_all("XCP_LINK_FEED_TWINS", "DATA", "identity: feed twins merged"),
    **_all("XCP_AUDIT_OVERRIDES", "DATA", "distance overrides audited; " + DIST_RES),
    "XCP_SKIP_ANCHOR_REPAIR": ("REVIEW", "anchor_repair moves normalized_time onto the rated pool's anchor. "
                                         + LEGACY_SCALE),
    **_all("XCP_ONE_SCALE XCP_EQUAL_TIME_SCALE", "DISPLAY", "the public scale: " + ONE_SCALE),
    **_all("XCP_OUTLIER_SIGMA XCP_OUTLIER_SLOW_SIGMA XCP_OUTLIER_FLOOR XCP_OUTLIER_K", "DISPLAY",
           "board outlier rule (rank-only; " + ROWT + " keeps such rows from moving the fit)"),
    **_all("XCP_NCAA XCP_RECORD_BOOKS XCP_RECORD_SCHOOLS XCP_SOCIAL XCP_STATE_ODDS XCP_ALERTS XCP_FORECASTS",
           "DISPLAY", "site products built from ratings (optional pipeline steps)"),
    **_all("XCP_JOINT XCP_JOINT_LIVE", "INFRA", "engine selector (joint go-live vs the legacy linkage path)"),
    **_all("XCP_FROM_STATE XCP_PROBES XCP_OUTER XCP_THREADS XCP_STREAMS XCP_COURSE_SHARDS XCP_SEASON_SHARDS "
           "XCP_NICE XCP_EVIDENCE_NICE XCP_PYTHON XCP_WXPYTHON XCP_ROOT XCP_ENV XCP_DB_QUIET XCP_LIVE "
           "XCP_SWAP_ATTEMPTS XCP_PACK_PREFETCH XCP_PRECOND XCP_SCHUR XCP_CG_TRACE XCP_RANK_SEAM "
           "XCP_BACKFILL_XC XCP_RUNG_TIMEOUT XCP_IGNORE_BACKFILL_FAIL XCP_IGNORE_GOLIVE_FAIL "
           "XCP_IGNORE_PEOPLE_FAIL", "INFRA", "performance and plumbing"),
    **_all("XCP_LADDER XCP_LADDER_ALL XCP_LADDER_MIN_DAYS XCP_LADDER_ONLY XCP_LADDER_PCT XCP_LADDER_TUNE "
           "XCP_HOLDOUT_DUMP XCP_HOLDOUT_PCT XCP_HOLDOUT_VERBOSE XCP_DIST_HOLDOUT XCP_SPORT_HOLDOUT "
           "XCP_EVIDENCE XCP_EVIDENCE_BG XCP_BRACKET XCP_BACKTEST XCP_BACKTEST_DIVS XCP_LACCTIC "
           "XCP_LACCTIC_RACES XCP_KAILO XCP_SCOREBOARD XCP_PAGE_TIMING XCP_PAGE_TIMING_N XCP_SANITY_TOP "
           "XCP_WATCHDOG", "INFRA", REPORT + " (holdouts, ladder, comparisons, timing)"),
}

# ★ WHAT A CONSTANT IS FOR, where the comment above it says nothing (or the
#   wrong thing: a block that heads several constants).
PURPOSE = {
    ("engine/joint_solve.py", "XC_TRACK_GAP"): "★★ the one number the data cannot measure: XC vs track, "
                                               "asserted at ln(1.06) from coaching practice",
    ("engine/joint_solve.py", "CURVE_KNOT_DAYS"): "the form curve: knots every 30 days from 1 August",
    ("engine/joint_solve.py", "CURVE_N_KNOTS"): "the form curve: 13 knots",
    ("engine/joint_solve.py", "DIST_CAL_MIN_PAIRS"): "athlete-seasons that ran both events before the "
                                                     "season-best pairs decide an event offset",
    ("engine/joint_solve.py", "CG_TOL"): "CG tolerance of the final solve",
    ("engine/joint_solve.py", "CG_MAX_ITER"): "CG iteration cap",
    ("engine/apply_tilt.py", "TILT_K"): "the tilt per 10 rating points (mirror of joint_solve.TILT_K)",
    ("engine/normalize_distance.py", "DEFAULT_EXPONENT"): "Riegel-style exponent when no spline is fitted",
    ("engine/normalize_distance.py", "TARGET_DISTANCE_METERS"): "the default normalisation distance",
    ("engine/normalize_distance.py", "FLAT_COURSE_DIFFICULTY"): "a flat course's difficulty: zero",
    ("engine/normalize_distance.py", "DISTANCE_BY"): "XCP_DISTANCE_BY resolved: distance curve by pool or by ability",
    ("engine/normalize_distance.py", "DISTANCE_BY_SOURCE"): "where DISTANCE_BY came from",
    ("engine/normalize_distance.py", "DISTANCE_RESIDUAL"): "XCP_DISTANCE_RESIDUAL resolved: per-pool residual on the ability curve",
    ("engine/normalize_distance.py", "DISTANCE_RESIDUAL_SOURCE"): "where DISTANCE_RESIDUAL came from",
    ("engine/normalize_distance.py", "_ABILITY"): "the distance-by-ability artifact, when XCP_DISTANCE_BY=ability",
    ("engine/run_joint.py", "INDOOR_PAIR_MAX_DAYS"): "indoor-vs-outdoor check: last indoor and first outdoor "
                                                    "race within 42 days",
    ("engine/run_joint.py", "_ALT_FIT"): "run state: altitude coefficient fitted (--altitude-fit)",
    ("engine/run_joint.py", "CROSS_SPORT_DISTANCES"): "standard distances of the cross-sport holdout breakdown",
    ("engine/track_geometry.py", "UNKNOWN_LENGTH_MODES"): "how an unrecorded track length is read",
    ("engine/track_geometry.py", "UNKNOWN_LENGTH_DEFAULT"): "an unrecorded length is a 400 (XCP_GAUGE_UNKNOWN_LENGTH)",
    ("engine/bracket_engine.py", "TILT_BANDS"): "rating bands of the tilt-by-band table the course scale is fitted from",
    ("engine/bracket_engine.py", "RACE_BUCKETS"): "courses bucketed by races run (the course scale reads 4+)",
    ("engine/fit_weather_correction.py", "GRID_DEG"): "weather grid resolution",
    ("engine/fill_ratings.py", "_K_EXACT"): "per-pool constant, exact pass for thin pools",
    ("engine/speed_ratings.py", "SPORTS"): "the two sports",
}


def classify(item):
    """(category, note) for one constant: RULES[file][name], then naming
    forms, else UNCLASSIFIED."""
    hit = RULES.get(item["file"], {}).get(item["name"])
    if hit:
        return hit
    for rx, cat, note in NAME_PATTERNS:
        if rx.search(item["name"]):
            return cat, note
    return UNCLASSIFIED, "no rule: add one to scripts/constants_inventory.py RULES"


def classifyEnv(name):
    return ENV_RULES.get(name, (UNCLASSIFIED, "no rule: add one to ENV_RULES"))


# ------------------------------------------------------------------ #
# THE DOCUMENT
# ------------------------------------------------------------------ #

def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _code(text):
    return "`" + str(text).replace("`", "'").replace("|", "\\|") + "`" if text != "" else ""


def merged(consts):
    """One row per (file, name): a name assigned twice (a fallback, an
    if/else) is one constant with several lines."""
    out = OrderedDict()
    for c in consts:
        key = (c["file"], c["name"])
        if key in out:
            out[key]["lines"].append(c["line"])
            if not out[key]["purpose"] and c["purpose"]:
                out[key]["purpose"] = c["purpose"]
        else:
            out[key] = dict(c, lines=[c["line"]])
    for (f, n), c in out.items():
        c["purpose"] = PURPOSE.get((f, n)) or c["purpose"]
        c["category"], c["note"] = classify(c)
    return list(out.values())


def render(items, env):
    cats = list(CATEGORIES) + [UNCLASSIFIED]
    by_file = OrderedDict((f, [i for i in items if i["file"] == f]) for f in FILES)
    for e in env:
        e["category"], e["note"] = classifyEnv(e["name"])
    tot = Counter(i["category"] for i in items)
    tot_env = Counter(e["category"] for e in env)
    kept = sum(1 for i in items if i["note"].startswith("kept"))
    dead = sum(1 for i in items if "already dead" in i["note"])
    used = [c for c in cats if tot[c] or tot_env[c]]

    L = ["# Engine review, October 2026: every constant and switch",
         "",
         "*Generated by `python scripts/constants_inventory.py` from the code; do not edit by hand. "
         "Companion to [REVIEW-ENGINE-2026-10.md](REVIEW-ENGINE-2026-10.md): §2 maps the engine, "
         "§4-§5 say what replaces what. `tests/test_constants_inventory.py` fails when a module-level "
         "constant on the rating path has no rule here.*",
         "",
         f"**{len(items)} module-level constants** in {len(FILES)} files and **{len(env)} `XCP_` switches** "
         "read by them or by `deploy/run_pipeline.sh` / `deploy/solve_env.sh`.",
         "",
         "What each category means for the state-space engine:",
         ""]
    for c in CATEGORIES:
        L.append(f"- **{c}**: {CATEGORIES[c]}.")
    L += ["",
          f"Two things the counts hide. {kept} STATED or FITTED items are *kept*: normalisation "
          "(distance curves, era, weather) stays in front of the new engine until §8 point 5, so its "
          "settings stay too; the note starts with \"kept\". "
          f"{dead} items are in code that production no longer runs (the legacy ALS solve after "
          "`07_pack --pack-only`); their note says so. The review's §2.6 count (~460 constants, 144 "
          "switches) left out private plumbing names and names only mentioned in comments; this one "
          "counts every ALL_CAPS assignment and every switch actually read.",
          "",
          "| file | " + " | ".join(used) + " | total |",
          "|---|" + "---:|" * (len(used) + 1)]
    for f, rows in by_file.items():
        n = Counter(r["category"] for r in rows)
        L.append(f"| `{f}` | " + " | ".join(str(n[c] or "") for c in used) + f" | {len(rows)} |")
    L.append("| **all constants** | " + " | ".join(f"**{tot[c]}**" for c in used) + f" | **{len(items)}** |")
    L.append("| **`XCP_` switches** | " + " | ".join(f"**{tot_env[c]}**" for c in used) + f" | **{len(env)}** |")
    L += ["",
          "Columns below: the name, its line(s), its value (first 60 characters), what the comment "
          "above it says it is for, and what the state-space engine does with it.",
          ""]
    for f, rows in by_file.items():
        L += [f"### {f}", "",
              "| name | line | value | what it was for | under the new engine |",
              "|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| `{r['name']}` | {', '.join(map(str, r['lines']))} | {_code(r['value'])} | "
                     f"{_cell(r['purpose'] or '—')} | **{r['category']}** {_cell(r['note'])} |")
        L.append("")
    L += ["### XCP_ switches", "",
          "Read with `os.environ` / `os.getenv` in the files above, or expanded as `${XCP_...}` in "
          "`deploy/run_pipeline.sh` and `deploy/solve_env.sh`. A `:+` expansion in run_pipeline passes "
          "the value on as a `run_joint.py` flag, so unset means that flag's own default; solve_env's "
          "value is what production runs with.",
          "",
          "| switch | read at | default | under the new engine |",
          "|---|---|---|---|"]
    for e in env:
        where = ", ".join(e["where"][:4]) + (f" (+{len(e['where']) - 4})" if len(e["where"]) > 4 else "")
        L.append(f"| `{e['name']}` | {_cell(where)} | {_cell('; '.join(e['defaults']) or '—')} | "
                 f"**{e['category']}** {_cell(e['note'])} |")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=DEFAULT_OUT, help="the Markdown file to write")
    ap.add_argument("--repo", default=REPO, help="the checkout to read")
    ap.add_argument("--dump", action="store_true", help="one line per item to stdout, no file")
    args = ap.parse_args()
    consts, env = collect(args.repo)
    items = merged(consts)
    doc = render(items, env)
    if args.dump:
        for i in items:
            print(f"{i['category']:<8} {i['file']}:{i['lines'][0]} {i['name']} = {i['value']}"
                  f"  || {i['purpose']}  => {i['note']}")
        for e in env:
            print(f"{e['category']:<8} {e['name']}  => {e['note']}")
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    allc = Counter(i["category"] for i in items) + Counter(e["category"] for e in env)
    print(f"[inventory] {len(items)} constants, {len(env)} switches -> {args.out}")
    print("[inventory] " + "  ".join(f"{c} {allc[c]}" for c in list(CATEGORIES) + [UNCLASSIFIED] if allc[c]))
    bad = [f"{i['file']}:{i['name']}" for i in items if i["category"] == UNCLASSIFIED] + \
          [e["name"] for e in env if e["category"] == UNCLASSIFIED]
    if bad:
        print(f"[inventory] ⚠ {len(bad)} UNCLASSIFIED: " + ", ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
