"""The ability board restricted to a slice of a season's events.

Owner, 2026-09-08: "for best athletes I think you should be able to filter
out certain events out of those ratings. So you can get the best athlete
ratings for like longer distance events in pre season to guess for xc."

A season's rating is an aggregate over everything the athlete ran, so
athlete_season cannot answer this -- it has aggregated the events away.
The restricted board recomputes from ranking_results, which carries a
distance per row.

⚠ THE RISK THIS FILE EXISTS FOR. That recomputation DUPLICATES the
  estimator in build_ranking_results._ATHLETE_SEASON_SQL: the 80th
  percentile, and the season-median-minus-20 outlier guard. If the build
  changes its estimator and rankings does not, the restricted board puts a
  different statistic beside the unfiltered one and nothing complains.
  So the constants are pinned against the build's own source here.

    python tests/test_event_filter.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


import rankings                                                  # noqa: E402


class _Args(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)

    def getlist(self, k):
        v = dict.get(self, k)
        return [v] if v is not None else []


# ---- 1. the estimator matches the build, constant for constant -------- #
BRR = io.open(os.path.join(ROOT, "racecast", "build_ranking_results.py"),
              encoding="utf-8").read()
build_q = float(re.search(r"^_SEASON_Q = ([\d.]+)", BRR, re.M).group(1))
build_out = float(re.search(r"^_SEASON_OUTLIER_PTS = ([\d.]+)", BRR,
                            re.M).group(1))
ok(rankings._SEASON_Q == build_q,
   f"rankings._SEASON_Q is {rankings._SEASON_Q}, the build uses {build_q} -- "
   f"the restricted board would rank a different statistic")
ok(rankings._SEASON_OUTLIER_PTS == build_out,
   f"rankings._SEASON_OUTLIER_PTS is {rankings._SEASON_OUTLIER_PTS}, the "
   f"build uses {build_out}")

# and the SQL really uses them, rather than a hardcoded 0.8 / avg()
params = {}
src = rankings._abilitySource({"dist_min": 3000.0, "dist_max": None}, params)
ok(f"percentile_cont({rankings._SEASON_Q})" in src,
   "the restricted board must use the season quantile, not a mean -- a mean "
   "punishes the athlete who raced more (build_ranking_results says why)")
ok(f"med.m - {rankings._SEASON_OUTLIER_PTS}" in src,
   "the restricted board must apply the same outlier guard as the build")
ok("avg(" not in src, "no plain average: that is the estimator the build rejected")


# ---- 2. unrestricted stays on the prebuilt table ---------------------- #
p0 = {}
ok(rankings._abilitySource({"dist_min": None, "dist_max": None}, p0)
   == "athlete_season s",
   "with no event window the board must read the prebuilt table")
ok(p0 == {}, "an unrestricted board must bind nothing extra")


# ---- 3. the window binds, both ends ----------------------------------- #
p1 = {}
s1 = rankings._abilitySource({"dist_min": 3000.0, "dist_max": None}, p1)
ok(p1.get("dist_min") == 3000.0 and "dist_max" not in p1,
   "an open-ended window must bind only the end it has")
ok("r.distance >= %(dist_min)s" in s1, "dist_min must reach the SQL")
p2 = {}
s2 = rankings._abilitySource({"dist_min": 800.0, "dist_max": 1600.0}, p2)
ok("r.distance <= %(dist_max)s" in s2, "dist_max must reach the SQL")
ok("r.distance IS NOT NULL" in s2,
   "a race whose distance nobody parsed is not evidence and must be excluded")


# ---- 3b. the board's filters are pushed INTO the aggregate ------------ #
#   ⚠ Left to the outer WHERE alone this aggregates every rated row in the
#     corpus at or above the distance -- millions, per page load -- and then
#     keeps one pool and season out of it.
p3 = {}
s3 = rankings._abilitySource({"dist_min": 3000.0, "dist_max": None}, p3,
                             " AND pool = %(pool)s")
inner = s3[s3.index("FROM   ranking_results r"):s3.index("med AS")]
ok("AND pool = %(pool)s" in inner,
   "the board's filters must be inside the aggregate, not only outside it")
# ...and kept outside too: dropping them there would be a correctness bug
# the first time the helper is called without a where.
ok(rankings._abilitySource({"dist_min": 3000.0, "dist_max": None}, {})
   .count("ranking_results") == 1,
   "the helper must still work with no where at all")


# ---- 3c. a column athlete_season has and ranking_results lacks -------- #
#   _whereClauses probes athlete_season and will NAME such a column; the
#   subquery is built from ranking_results. It has to appear either way or
#   the restricted board alone raises UndefinedColumn.
_real = rankings._rowHasUnit
rankings._rowHasUnit = (lambda c, t="ranking_results":
                        not (c == "county" and t == "ranking_results"))
s4 = rankings._abilitySource({"dist_min": 3000.0, "dist_max": None}, {})
ok('NULL::text AS "county"' in s4,
   "a unit athlete_season carries but ranking_results does not must come "
   "through as NULL, not be omitted -- the outer clause names it")
ok('ORDER BY e."division"' in s4,
   "a unit both tables carry must come through as the real value")
rankings._rowHasUnit = _real


# ---- 4. parse and refuse ---------------------------------------------- #
f, err = rankings.parseFilters(_Args(board="ability", dist_min="3000"))
ok(err is None and f["dist_min"] == 3000.0, f"ability dist_min rejected: {err}")
ok(rankings.eventRestricted(f), "eventRestricted must see the window")

f2, err2 = rankings.parseFilters(_Args(board="ability", dist_min="5000",
                                       dist_max="1500"))
ok(f2 is None and err2, "an inverted window must be refused, not silently empty")

f3, err3 = rankings.parseFilters(_Args(board="ability", dist_min="nonsense"))
ok(f3 is None and err3, "a non-numeric window must be refused")

f4, err4 = rankings.parseFilters(_Args(board="ability"))
ok(err4 is None and not rankings.eventRestricted(f4),
   "no window means no restriction")


# ---- 5. a restricted board is NOT the default board ------------------- #
#   ⚠ storedBoardSize would otherwise return the count the build wrote for
#     the WHOLE board, so a 3000m+ board of four thousand would report
#     itself "of 812,940" and every percentile on it would be wrong.
ok(rankings.isDefaultBoard(f4) is True,
   "the plain ability board should still take the prebuilt count")
ok(rankings.isDefaultBoard(f) is False,
   "an event-restricted board must not read the whole board's stored count")


# ---- 6. every ability query reads the same source --------------------- #
RK = io.open(os.path.join(ROOT, "racecast", "rankings.py"),
             encoding="utf-8").read()
ok("FROM   athlete_season s" not in RK,
   "an ability query still names athlete_season directly; it would ignore "
   "the event window while its neighbours honour it")
ok(RK.count("FROM   {source}") == 5,
   f"expected 5 ability queries on the shared source, found "
   f"{RK.count('FROM   {source}')}")
ok(RK.count("source = _abilitySource(f, params, where)") == 4,
   "every site that builds a WHERE for the ability board must also build "
   "its source")


# ---- 7. the UI sends it only where it is accepted --------------------- #
JS = io.open(os.path.join(ROOT, "racecast", "static", "rankings.js"),
             encoding="utf-8").read()
i = JS.index('q.set("dist_min"')
ok('state.board === "ability"' in JS[max(0, i - 400):i],
   "dist_min must be sent from the ability board only -- the API refuses "
   "it elsewhere rather than ignoring it")
HTML = io.open(os.path.join(ROOT, "racecast", "templates", "rankings.html"),
               encoding="utf-8").read()
ok('id="events"' in HTML, "the events control must exist")
i2 = HTML.index('id="events"')
ok("ability-only" in HTML[max(0, i2 - 400):i2],
   "the events control belongs to the ability board only")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
