"""fit_distance_exponent --season-best: the TF pair sample is best-against-
best per athlete-season (issue #109), one pair per rung transition, the
earlier race is race 1, and the mode has its own cache.

No database: `database` and `corrections` are stubbed before import; the
functions under test are pure.

    python tests/test_season_best_pairs.py
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# ---- stubs: the module builds its SQL from these at import ------------- #
_db = types.ModuleType("database")
_db.getConn = lambda: (_ for _ in ()).throw(RuntimeError("no db in tests"))
sys.modules["database"] = _db
_corr = types.ModuleType("corrections")
_corr.distanceOverrideSQL = lambda *a, **kw: ("", "")
_corr.distanceDropSQL = lambda *a, **kw: ""
sys.modules["corrections"] = _corr

import fit_distance_exponent as fde                            # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def rec(ord_, dist, time, season=2025, pool="hs_m", canon=None):
    return {"ord": ord_, "season": season, "dist": float(dist),
            "time": float(time), "canon": canon, "pool": pool}


def ledger():
    return {k: 0 for k in ("pairs", "dup_result", "near_equal_ratio",
                           "rung_under_min")}


# ---------------------------------------------------------------- #
# 1. best per rung, one pair per transition, earlier race is race 1
# ---------------------------------------------------------------- #
# A season: three 1600s (best 4:30 on day 40), two 3200s (best 9:50 on day
# 20), one 800 on day 60. Windowed pairing would take the 1600/3200 raced
# closest together (day 10's 4:45 against day 20's 10:10) -- the dual-meet
# double. Season-best takes 4:30 against 9:50.
season = [rec(10, 1600, 285), rec(10, 3200, 610),
          rec(20, 3200, 590), rec(40, 1600, 270), rec(55, 1600, 275),
          rec(60, 800, 125)]
out, led = [], ledger()
fde._flushAthleteSeasonBest(season, out, led)
ok(len(out) == 3 and led["pairs"] == 3,
   f"three rungs -> three pairs, got {len(out)}")
by_key = {(p["distance1"], p["distance2"]): p for p in out}
p = by_key.get((3200.0, 1600.0)) or by_key.get((1600.0, 3200.0))
ok(p is not None and {p["time1"], p["time2"]} == {270.0, 590.0},
   "the 1600/3200 pair must be the season's two BESTS (4:30, 9:50), not the "
   "same-day double")
ok(p is not None and p["distance1"] == 3200.0 and p["time1"] == 590.0,
   "race 1 is the EARLIER race (the day-20 3200), matching the windowed path")
ok(all(q["pool"] == "hs_m" for q in out), "pool rides on race 1")

# ---------------------------------------------------------------- #
# 2. seasons do not pair across; the 100m bucket merges 1600 and 1609
# ---------------------------------------------------------------- #
two = [rec(10, 1600, 280, season=2024), rec(300, 3200, 600, season=2025)]
out, led = [], ledger()
fde._flushAthleteSeasonBest(two, out, led)
ok(len(out) == 0, "a 2024 1600 never pairs with a 2025 3200")

mile = [rec(10, 1609, 281), rec(20, 1600, 279), rec(30, 3200, 600)]
out, led = [], ledger()
fde._flushAthleteSeasonBest(mile, out, led)
ok(len(out) == 1 and out[0]["time1"] == 279.0 or
   (len(out) == 1 and out[0]["time2"] == 279.0),
   "1609 and 1600 are one rung; its best (279) is what pairs")

# ---------------------------------------------------------------- #
# 3. --min-per-rung drops a once-raced rung and counts it
# ---------------------------------------------------------------- #
out, led = [], ledger()
fde._flushAthleteSeasonBest(season, out, led, min_per_rung=2)
ok(len(out) == 1 and led["rung_under_min"] == 1,
   f"with min_per_rung=2 only 1600 (x3) and 3200 (x2) survive -> 1 pair, "
   f"got {len(out)}, dropped {led['rung_under_min']}")

# ---------------------------------------------------------------- #
# 4. the stream consumer takes the flush; the mode has its own cache
# ---------------------------------------------------------------- #
rows = [("a", 1), ("a", 2), ("b", 3)]
seen = []


def prep(row, led):
    return rec(row[1], 1600 if row[1] % 2 else 3200, 300 - row[1])


def flush(buffer, pairs, led):
    seen.append(len(buffer))


fde._consumePairStream(rows, prep, ledger(), flush)
ok(seen == [2, 1], f"flush per athlete, in stream order: {seen}")
ok(fde._cachePath(True) != fde._cachePath(False)
   and fde._cachePath(False) == fde.CACHE_FILE,
   "season-best pairs never share the windowed cache")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_season_best_pairs: all checks passed")
