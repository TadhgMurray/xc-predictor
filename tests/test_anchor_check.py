"""The anchor audit can see the rows the boards throw away.

Owner, 2026-09-09, with the 2023 HS mile final -- eight seniors inside four
seconds, six rated 141-144 and the two Youngs rated 230:

    "The entire season is insane. That means it has to be something with
     pool. ... If we catch him we catch all of them."

★ IT IS THE POOL, AND THE ARITHMETIC SAYS SO WITHOUT ANY CODE. Within one
  race rating x time is constant per anchor: 34,918 for the six (spread
  0.12%) and 56,232 for the two (spread 0.01%). Ratio 1.6104. Every row in
  that race was normalised on the hs_m spline; six were divided by hs_m's
  pool mean (~1,219) and two by college_m's (~1,962). targetFor says hs_m
  anchors at 5,000m and college_m at 8,000m, which is where the 1.61 lives.

⚠ AND THE AUDIT COULD NOT SEE THEM. anchor_check joined ranking_results to
  learn the pool -- an INNER join -- while build_ranking_results drops any
  row over raceCeiling(pool) before writing one. A mismatch inflates a
  rating by 60%; an inflated rating is over the ceiling; an over-ceiling row
  never reaches a board. The worse the mismatch, the more certain the
  checker was to miss it. The pool now comes off the row (rating_pool,
  issue 171) and the board join is a LEFT JOIN that only reports whether the
  row made it.

    python tests/test_anchor_check.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "engine", "anchor_check.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. the audit is not gated on reaching a board --------------------- #
sql = re.search(r'_SQL = """(.*?)"""', SRC, re.S).group(1)
bare = "\n".join(ln.split("--")[0] for ln in sql.splitlines())
ok("LEFT   JOIN ranking_results" in bare or "LEFT JOIN ranking_results" in bare,
   "ranking_results must be a LEFT JOIN -- an inner one hides every row the "
   "pool ceiling dropped, which is every row worth finding")
ok(re.search(r"(?<!LEFT   )(?<!LEFT )JOIN\s+ranking_results", bare) is None,
   "no inner join to ranking_results anywhere in the query")
# ! THE POOL EXPRESSION IS BUILT IN main(), NOT INLINE IN _SQL, because it
#   depends on whether the column exists -- so look for it there.
ok("{pool_expr}" in bare, "the query must take its pool from a built expression")
ok('COALESCE(r.rating_pool, k.pool)' in SRC,
   "the pool must come off the row when it can, not out of the board")
ok("on_board" in bare and "on_board" in SRC[SRC.index("THE WORST OF THEM"):],
   "whether the row reached a board must be REPORTED -- that count is the "
   "blind spot this fixes, and hiding it would just move the blindness")

# and the catalog is asked before rating_pool is used
ok("column_name = 'rating_pool'" in SRC,
   "a database that has never run a pack has no rating_pool column; the "
   "audit must fall back rather than die")


# ---- 2. `was` must not name the wrong sport ---------------------------- #
#   hs_m anchors at 5000m in BOTH sports, so a track row normalised as hs_m
#   ties between hs_m|XC and hs_m|TF. Iterating XC first reported hs_m|XC
#   for a track row and sent the reader after a sport bug that is not there.
which = SRC[SRC.index("def whichPool"):SRC.index("#  THE AUDIT")]
ok("order = [sport]" in which,
   "whichPool must search the audited sport FIRST so ties resolve to it")
ok("for sp in order" in which, "...and actually iterate that order")


# ---- 3. the race itself, as arithmetic --------------------------------- #
#   Six athletes on one anchor, two on another, from times and ratings
#   alone. If this ever stops holding, the diagnosis above is wrong and the
#   comments in anchor_check need rewriting, not this test relaxing.
race = [("Birnbaum", 242.22, 144.2), ("Leo Young", 242.58, 231.8),
        ("Hansen", 243.63, 143.4), ("Burns", 244.24, 143.0),
        ("Lex Young", 244.60, 229.9), ("Cutting", 245.38, 142.2),
        ("Boler", 246.01, 141.9), ("Jones", 246.93, 141.4)]
six = [r * t for n, t, r in race if r < 200]
two = [r * t for n, t, r in race if r > 200]
ok(len(six) == 6 and len(two) == 2, "the fixture is eight rows, six and two")
ok(max(six) / min(six) < 1.005,
   f"the six must sit on ONE anchor: {min(six):,.0f}..{max(six):,.0f}")
ok(max(two) / min(two) < 1.005,
   f"the two must sit on ONE anchor: {min(two):,.0f}..{max(two):,.0f}")
ratio = (sum(two) / len(two)) / (sum(six) / len(six))
ok(1.60 < ratio < 1.62,
   f"the two anchors differ by {ratio:.4f}, expected ~1.61 (hs_m 5000m "
   f"against college_m 8000m)")
ok("1.6104" in SRC,
   "the measured ratio must be written down in the module, for the next "
   "reader who sees a 230")


# ---- 4. the recomputation still works, on the real splines ------------- #
#   Skipped rather than failed when the artifacts are absent: this test must
#   be runnable on a checkout that has never built them.
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
try:
    import anchor_check as ac                                  # noqa: E402
    from normalize_distance import normalizeTime, targetFor    # noqa: E402
except Exception as e:                                         # noqa: BLE001
    print(f"  (spline artifacts absent, skipping section 4: {e})")
else:
    ok(targetFor("hs_m", "TF") == 5000 and targetFor("college_m", "TF") == 8000,
       f"the anchors moved: hs_m {targetFor('hs_m', 'TF')}, college_m "
       f"{targetFor('college_m', 'TF')} -- if that is deliberate, the 1.61 "
       f"above is stale and this whole file needs re-deriving")
    nt = normalizeTime(244.60, 1609, "hs_m", "TF")
    # Lex's row: normalised as hs_m, rated as college_m
    is_bad, expected, r = ac.mismatch(244.60, 1609, nt, "college_m", "TF")
    ok(is_bad, "a row normalised as hs_m and rated as college_m must be "
               "flagged")
    was, _off = ac.whichPool(244.60, 1609, nt, ac._POOLS, "TF")
    ok(was == "hs_m|TF",
       f"the audit named {was}, but the row was normalised as hs_m on TF")
    # and a row that agrees is not flagged
    clean, _e, _r = ac.mismatch(242.22, 1609, normalizeTime(242.22, 1609,
                                                            "hs_m", "TF"),
                                "hs_m", "TF")
    ok(not clean, "a row normalised and rated on the same pool must pass")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
