# Project: xc-predictor / tests
# File:    test_grass_gap_column.py
# Purpose: the pool-constant table subtracts its own two columns, and says so
#          when the answer is impossible.
#
# ★★ WHY THIS IS WORTH A TEST. C(pool, XC) and C(pool, TF) are expressed at
#    the SAME anchor -- the bare pool key decides it for both sports
#    (normalize_distance.targetFor, and its docstring is emphatic that the
#    sport-suffixed anchor was never applied to a row). So ln(C_XC / C_TF) is
#    that pool's own measurement of what grass costs.
#
#    bracket_engine.XC_TRACK_GAP is ln(1.06) = 5.83% and is a DEFINITION: the
#    seasons do not overlap, so it could not be measured there. This column is
#    the only independent read of the same quantity in the tree, and on
#    2026-09-22 it gave 5.12% for hs_m and 4.98% for hs_f -- half a percent
#    from a number nothing in this module has ever seen.
#
# ⚠ AND A NEGATIVE GAP IS PHYSICALLY IMPOSSIBLE. It says a pool's track
#   population is slower than its cross-country population at the same
#   anchor. The same run read college_m -2.02%, college_f -2.24%, pro_m
#   -0.43% -- and college_m's view factor fell to x1.15, below the sanity
#   band this module itself prints. Eight numbers had been sitting in two
#   columns for a year and nobody subtracted them.
#
#   python -m pytest -q tests/test_grass_gap_column.py
import io
import math
import os
import re
import sys
import unittest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _diagConstants(constants):
    """Run the diagnostic's constants block against canned constants and
    return its printed lines.

    ! THE BLOCK IS LIFTED FROM pool_view.py, not retyped -- a copy here
      would keep passing after the original changed, which is the whole
      failure mode this file is about one layer up."""
    src = io.open(os.path.join(_ROOT, "racecast", "pool_view.py"),
                  encoding="utf-8").read()
    i = src.index("    import math as _math\n")
    j = src.index('    print("\\n  -- spline-level ratios', i)
    block = "\n".join(line[4:] if line.startswith("    ") else line
                      for line in src[i:j].splitlines())
    out = []
    ns = {"pools": list(constants),
          "_poolConstant": lambda p, s: constants[p].get(s),
          "print": lambda *a: out.append(" ".join(str(x) for x in a))}
    exec(compile(block, "<pool_view:constants>", "exec"), ns)   # noqa: S102
    return out


def _gapOn(line):
    m = re.search(r"(-?\d+\.\d\d)%", line)
    return float(m.group(1)) if m else None


class TheGapIsComputedAndChecked(unittest.TestCase):

    def test_the_hs_gap_reproduces_the_defined_grass_cost(self):
        """★ THE OWNER'S OWN NUMBERS, 2026-09-22. 1211.5 against 1151.0 is
        5.12%, and bracket_engine DEFINES the grass cost as 5.83%."""
        out = _diagConstants({"hs_m": {"XC": 1211.5, "TF": 1151.0}})
        gap = _gapOn(out[0])
        self.assertAlmostEqual(gap, 100 * math.log(1211.5 / 1151.0), places=2)
        self.assertAlmostEqual(gap, 5.12, places=1)
        self.assertNotIn("BACKWARDS", out[0])
        self.assertNotIn("far from", out[0])

    def test_a_backwards_gap_is_called_impossible(self):
        """⚠ college_m, from the same run. No pool's runners get slower on
        a track."""
        out = _diagConstants({"college_m": {"XC": 1631.0, "TF": 1664.3}})
        self.assertLess(_gapOn(out[0]), 0)
        self.assertIn("BACKWARDS", out[0])

    def test_a_gap_far_from_the_grass_cost_is_flagged_without_being_negative(self):
        """! pro_f read +0.13% -- not impossible, but nothing like 5.83%,
        and a check that only caught the sign would have passed it."""
        out = _diagConstants({"pro_f": {"XC": 1405.7, "TF": 1403.9}})
        self.assertGreater(_gapOn(out[0]), 0)
        self.assertIn("far from", out[0])
        self.assertNotIn("BACKWARDS", out[0])

    def test_a_healthy_gap_is_not_flagged(self):
        out = _diagConstants({"hs_f": {"XC": 1476.7, "TF": 1404.9}})
        self.assertNotIn("<-", out[0])

    def test_a_missing_constant_prints_no_gap_rather_than_a_wrong_one(self):
        """! THE CASE THAT STARTED ALL THIS. Before the sampler fix, hs/TF
        was simply absent; a gap computed against a missing number would be
        worse than no gap."""
        out = _diagConstants({"hs_m": {"XC": 1211.5, "TF": None}})
        self.assertIsNone(_gapOn(out[0]))
        self.assertIn("--", out[0])

    def test_the_reference_is_bracket_engines_number_not_a_retyped_one(self):
        """! ln(1.06), the same constant bracket_engine holds. A second
        spelling of a definition is how two files start disagreeing about
        one quantity."""
        src = io.open(os.path.join(_ROOT, "racecast", "pool_view.py"),
                      encoding="utf-8").read()
        i = src.index("_GRASS = ")
        self.assertIn("log(1.06)", src[i:i + 60])
        self.assertIn("XC_TRACK_GAP", src[i:i + 120])
        be = io.open(os.path.join(_ROOT, "engine", "bracket_engine.py"),
                     encoding="utf-8").read()
        j = be.index("XC_TRACK_GAP")
        self.assertIn("1.06", be[j:j + 200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
