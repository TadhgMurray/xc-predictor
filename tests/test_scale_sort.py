"""A pool=all board in the HS-equivalent view is ORDERED on the numbers it
shows, and the rank lookups agree with it. Text checks, no database.

    python tests/test_scale_sort.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = io.open(os.path.join(ROOT, "racecast", "rankings.py"), encoding="utf-8").read()
J = io.open(os.path.join(ROOT, "racecast", "static", "rankings.js"),
            encoding="utf-8").read()


def body(src, name):
    i = src.index(f"def {name}(")
    j = src.find("\ndef ", i + 1)
    return src[i:] if j == -1 else src[i:j]


fails = []
def ok(c, m):
    if not c:
        fails.append(m)

ok('"scale": ("hs"' in body(R, "parseFilters"), "parseFilters reads scale")
ok("_scaleExpr(f, expr)" in body(R, "_orderBy")
   and "_scaleExpr(f, tb_col)" in body(R, "_orderBy"),
   "_orderBy scales the sort column AND the tiebreak")
ok('f.get("pool") == "all"' in body(R, "_scaleActive"),
   "scaling only on pool=all, where two factors can interleave")
ok("repFactor(pool, sport)" in body(R, "_hsScaleCase"),
   "the CASE uses the same representative factor stampBoardRows uses")
ok("cmp = _scaleExpr(f, col)" in body(R, "_rankInResults")
   and "({cmp} {beats} %(target)s" in body(R, "_rankInResults"),
   "the performance rank compares on the scaled column")
ok("not _scaleActive(f)" in body(R, "rankOf"),
   "the count shortcut is skipped when the board is scaled")
ok('scale:  hsMode() ? "hs" : "pool"' in J, "rankings.js sends the scale")
ok("_lastBoard.data.hs_movable" in J and "load();" in J[J.index("rc-scale-change"):J.index("rc-scale-change") + 600],
   "a scale flip refetches a board whose rows move")

for f in fails:
    print("  FAIL " + f)
if fails:
    sys.exit(1)
print("  HS-equivalent boards order on what they show .... OK")
