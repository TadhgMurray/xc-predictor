"""scripts/diag_hs_to_college: the pure parts (2026-10-22)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "x")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, d))
import diag_hs_to_college as D                                   # noqa: E402


def test_movers_last_hs_then_first_college_next_year_same_sex():
    seasons = {1: [("hs_f", 2021, 140.0), ("hs_f", 2022, 149.3), ("college_f", 2023, 142.1)],
               2: [("hs_m", 2022, 150.0), ("college_m", 2024, 140.0)],          # gap year
               3: [("hs_f", 2022, 120.0)],                                     # never college
               4: [("hs_m", 2022, 130.0), ("college_f", 2023, 120.0)]}         # sex mismatch
    assert D.movers(seasons) == [(1, "hs_f", 149.3, "college_f", 142.1)]


def test_best_at_distance():
    rows = [("3200m", 640.0), ("3200 Meter Run", 655.2), ("1600m", 300.0), ("5000m", 1000.0)]
    assert D.bestAt(rows, 3200.0) == 640.0
    assert D.bestAt(rows, 5000.0) == 1000.0
    assert D.bestAt([("3200m", 9999.0)], 3200.0) is None


def test_verdict_text():
    assert "reading 1" in D.verdictText(5.0, -4.6)
    assert "reading 2" in D.verdictText(5.0, -0.5)
    assert "both" in D.verdictText(5.0, -2.5)
    assert "agree" in D.verdictText(0.4, -3.0)


def test_bands_and_the_calibration_proposal():
    """Part E (2026-11-01): five equal bands by rating, and points that add
    each band's 'too fast' on top of today's correction."""
    import diag_hs_to_college as D
    rows = [(100.0 + i, 1.0 if i < 50 else 2.0) for i in range(100)]
    b = D.bands(rows)
    assert len(b) == 5 and all(n == 20 for _r, n, _x in b)
    assert b[0][2] == 1.0 and b[-1][2] == 2.0
    assert D.bands(rows[:10]) == []
    prop = D.proposeCal(lambda r: -0.01 if r > 150 else 0.0, [(120.0, 9, 1.5), (160.0, 9, 2.0)])
    assert prop == ((120.0, 0.015), (160.0, 0.01))
