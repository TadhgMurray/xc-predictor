"""place_unplaced_meets.pickPlace: a course name places a race only when it
is one place (owner, 2026-11-03: every timer and upload race had none)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
import place_unplaced_meets as P  # noqa: E402


def test_pick_place():
    places = {("zahler park", "MN"): (44.9, -93.5, 5), ("franklin park", ""): (42.3, -71.1, 9),
              ("lake park", "WI"): (43.0, -87.9, 3), ("lake park", "MN"): (45.0, -93.0, 4)}
    assert P.pickPlace(places, "Zahler Park", "MN") == (44.9, -93.5, 5)
    assert P.pickPlace(places, "Zahler Park", "IA") is None          # another state: not this place
    assert P.pickPlace(places, "Franklin Park", "") == (42.3, -71.1, 9)
    assert P.pickPlace(places, "Lake Park", None) is None            # two places of that name
    assert P.pickPlace(places, "", "MN") is None


def test_runs_before_the_canonical_ids():
    for f in ("deploy/nightly_update.sh", "deploy/run_pipeline.sh"):
        s = open(os.path.join(_ROOT, f)).read()
        assert s.index("place_unplaced_meets.py") < s.index("06b_course_canonical"), f
