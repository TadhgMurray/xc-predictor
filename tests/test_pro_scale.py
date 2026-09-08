"""A professional is rated on the COLLEGE scale.

Owner, 2026-09-08: "pro athletes still get crazy low speed ratings. They
should honestly be incorporated to college speed pool mechanically but not
ranked as such."

A rating is 100 * pool_mean / exp(a), and 100 is the mean of your OWN pool.
The pro pool's mean is a professional, so an elite professional reads just
over 100 -- Graham Blanks' 29:41 at the USATF trials came out 96.4 beside
his own college rows at 146. Nothing is wrong in the solve: the two numbers
are on different scales and only one of them is the scale a reader has in
their head.

★ THE ANCHOR MOVES, NOT THE POOL. Repooling pros as college would fold
  professional abilities INTO the college mean and shift every college
  rating down. This takes the college pool's mean and applies it to pro
  rows; the college mean is still computed over collegians alone. Pros keep
  pool 'pro_m'/'pro_f', so rankings.POOLS still keeps them off every board.

    python tests/test_pro_scale.py
"""
import io
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# pair_write_results needs pair_ratings._meanBy and a database-free import,
# so the one function it uses is stubbed and the module's own source is
# executed. That keeps the REAL arithmetic under test.
_pr = types.ModuleType("pair_ratings")


def _meanBy(vals, group, n, mask):
    tot = np.bincount(group[mask], weights=vals[mask], minlength=n)
    cnt = np.bincount(group[mask], minlength=n)
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan), cnt


_pr._meanBy = _meanBy
sys.modules["pair_ratings"] = _pr

SRC = io.open(os.path.join(ROOT, "engine", "pair_write_results.py"),
              encoding="utf-8").read()
_ns = {"np": np, "pr": _pr}
exec(compile(SRC[SRC.index("_PRO_SCALE_PREFIX"):SRC.index("# CHUNK 2")],
             "pair_write_results", "exec"), _ns)
poolMeanPerGroup = _ns["poolMeanPerGroup"]
_proScaleMap = _ns["_proScaleMap"]


# ! ability HERE IS TIME-LIKE: rating = 100 * pool_mean / ability, so a
#   SMALLER value is a faster athlete. Pros are faster than collegians.
def _run(names, ability, pool, season=None):
    ability = np.asarray(ability, float)
    pool = np.asarray(pool)
    season = np.full(pool.size, 2025) if season is None else np.asarray(season)
    attrs = {"pool": pool, "pool_names": names, "season": season}
    return poolMeanPerGroup(ability, attrs, np.ones(pool.size, bool))


NAMES = ["hs_m", "college_m", "pro_m", "college_f", "pro_f"]
ABILITY = [1.30, 1.00, 0.95, 1.05, 0.80, 0.78]
POOL = [0, 1, 1, 1, 2, 2]

career, seasonal = _run(NAMES, ABILITY, POOL)
ab = np.array(ABILITY)
pl = np.array(POOL)
college_mean = ab[pl == 1].mean()
pro_mean = ab[pl == 2].mean()

# ---- 1. a pro is anchored on the college mean ------------------------- #
ok(np.allclose(career[pl == 2], college_mean),
   f"pro rows anchor on {career[pl == 2]}, want the college mean "
   f"{college_mean}")
ok(np.allclose(seasonal[pl == 2], college_mean),
   "the seasonal half must redirect too, or the athlete page and the race "
   "row disagree about what 100 means")

# ---- 2. and the college mean is NOT moved by them --------------------- #
#   The whole reason the anchor moves rather than the pool.
ok(np.allclose(career[pl == 1], college_mean),
   "college rows must still be anchored on collegians alone")
ok(not np.isclose(college_mean, pro_mean),
   "the fixture is pointless if the two means are equal")

# ---- 3. the ratings actually rise ------------------------------------- #
before = 100.0 * pro_mean / ab[pl == 2]
after = 100.0 * career[pl == 2] / ab[pl == 2]
ok((after > before).all(),
   f"pro ratings did not rise: {np.round(before,1)} -> {np.round(after,1)}")
college_ratings = 100.0 * college_mean / ab[pl == 1]
ok(after.min() > college_ratings.max(),
   f"a pro faster than every collegian should out-rate them all: "
   f"pro {np.round(after,1)} vs college {np.round(college_ratings,1)}")

# ---- 4. other pools are untouched ------------------------------------- #
ok(np.allclose(career[pl == 0], ab[pl == 0].mean()),
   "the high-school pool must keep its own anchor")


# ---- 5. the map itself -------------------------------------------------#
m = _proScaleMap(NAMES)
ok(m[NAMES.index("pro_m")] == NAMES.index("college_m"), "pro_m -> college_m")
ok(m[NAMES.index("pro_f")] == NAMES.index("college_f"), "pro_f -> college_f")
for keep in ("hs_m", "college_m", "college_f"):
    ok(m[NAMES.index(keep)] == NAMES.index(keep),
       f"{keep} must map to itself")

# ⚠ NO COLLEGE POOL OF THAT GENDER: keep the pro's own anchor rather than
#   rating against a pool that is not there.
only = ["pro_m"]
ok(_proScaleMap(only)[0] == 0,
   "with no college pool present a pro keeps its own anchor")
c2, _ = _run(only, [0.80, 0.78], [0, 0])
ok(np.allclose(c2, np.mean([0.80, 0.78])),
   "and the arithmetic still works in that case")


# ---- 6. a season with no collegians keeps its own anchor -------------- #
#   The seasonal redirect is a lookup, not an index remap: the (college,
#   season) code may simply not exist.
names = ["college_m", "pro_m"]
ability = [1.00, 1.10, 0.80, 0.78]
pool = [0, 0, 1, 1]
season = [2025, 2025, 2024, 2024]        # pros race a year the college did not
c3, s3 = _run(names, ability, pool, season)
ab3 = np.array(ability, float)
pl3 = np.array(pool)
ok(np.allclose(s3[pl3 == 1], ab3[pl3 == 1].mean()),
   "a pro season with no college season of the same year must fall back to "
   "its own seasonal mean, not to a code that does not exist")
ok(np.allclose(c3[pl3 == 1], ab3[pl3 == 0].mean()),
   "the CAREER anchor still redirects: it is per pool, not per season")


# ---- 7. pros are still not on a board -------------------------------- #
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from rankings import POOLS                                       # noqa: E402
ok(not any(p.startswith("pro") for p in POOLS),
   "rated on the college scale, NOT ranked as college -- a pro pool must "
   "not become board-eligible")


if __name__ == "__main__":
    for msg in failed:
        print("FAIL:", msg)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
