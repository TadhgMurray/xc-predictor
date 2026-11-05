"""--merge-core-name: one course under spellings that differ by filler
merges when the solve's difficulties agree; a loop that reads differently,
or a protected '(rain course)', stays its own (owner, 2026-11-05)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import build_course_canonical as B                            # noqa: E402

NAMES = ["LaVern Gibson Course", "LaVern Gibson Championship Course"]


def _rows():
    # one cluster (cid 1) holding two venues already mapped to two ids
    return [(NAMES[0], 39.4, -87.4, 1, NAMES[0], 2, 900),
            (NAMES[1], 39.41, -87.41, 1, NAMES[0], 2, 400)]


def _old():
    return {(NAMES[0], 39.4, -87.4): (10, NAMES[0]), (NAMES[1], 39.41, -87.41): (11, NAMES[1])}


def test_spellings_that_agree_take_one_id():
    diffs = {NAMES[0]: (0.038, 5000), NAMES[1]: (0.041, 3000)}
    out, _nv, _ni = B.keepIds(_rows(), _old(), merge_core=True, diffs=diffs)
    assert {r[3] for r in out} == {10}


def test_a_loop_that_reads_differently_stays_apart():
    diffs = {NAMES[0]: (0.038, 5000), NAMES[1]: (0.075, 3000)}
    out, _nv, _ni = B.keepIds(_rows(), _old(), merge_core=True, diffs=diffs)
    assert {r[3] for r in out} == {10, 11}
    assert B.keepIds.refused and "spread" in B.keepIds.refused[0][1]


def test_off_by_default_and_rain_is_protected():
    out, _nv, _ni = B.keepIds(_rows(), _old(), diffs={})
    assert {r[3] for r in out} == {10, 11}
    assert B.protectedSplit({"name": "Mt. San Antonio College (rain course)"},
                            {"name": "Mt. San Antonio College"})


def test_facilities_numbers_and_small_fits_keep_courses_apart():
    """owner's dry run, 2026-11-05."""
    assert B.coreConflict(["Fort Snelling Golf Course", "Fort Snelling State Park"])
    assert B.coreConflict(["Buena Vista Golf Course", "Buena Vista Lake"])
    assert B.coreConflict(["IHSAA Cross Country Sectional #6", "IHSAA Cross Country Sectional #7"])
    assert B.coreConflict(["Norris Dam", "Norris Dam State Park"]) is None
    assert B.coreConflict(["Valley View Country Club", "Valley View Golf Course"]) is None
    assert B.coreConflict(["HOT Soccer Complex", "HOT Soccer Fields"]) is None
    ok, _why = B.coreAgree(["Red Bud Country Club", "Red Bud Sports Complex"],
                           {"Red Bud Country Club": (0.100, 550), "Red Bud Sports Complex": (0.056, 186)})
    assert not ok
