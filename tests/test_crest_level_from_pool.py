# A crest pinned for a level shows wherever the mention's pool is that level
# (owner, 2026-10-28: the University of Washington's crest was on its school
# page but not on its runners' athlete pages, race rows or boards).
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import school_logo as L                                        # noqa: E402


def test_the_pool_picks_the_levels_crest(monkeypatch):
    monkeypatch.setitem(L._CRESTS, "map", {
        "Washington": [("WA", "hs-sha", "a.png", ""), ("WA", "uw-sha", "b.png", "college")]})
    monkeypatch.setattr("school_identity.contextState", lambda s, st, t=False: st, raising=False)
    monkeypatch.setattr("school_identity.teamState", lambda s, p, st: st, raising=False)
    assert L.crestState("Washington", "WA", pool="college_m")[1] == "uw-sha"
    assert L.crestState("Washington", "WA", pool="hs_f")[1] == "hs-sha"
    assert L.crestState("Washington", "WA")[1] == "hs-sha"
    assert L.crestState("Washington", "WA", level="college")[1] == "uw-sha"


def test_rows_stamped_for_the_browser_carry_their_level(monkeypatch):
    monkeypatch.setitem(L._CRESTS, "map", {
        "Wake Forest": [("NC", "hs-sha", "a.png", ""), ("NC", "wf-sha", "b.png", "college")]})
    monkeypatch.setattr("school_identity.contextState", lambda s, st, t=False: st, raising=False)
    rows = [{"school": "Wake Forest", "state": "NC", "pool": "college_m"},
            {"school": "Wake Forest", "state": "NC", "pool": "hs_m"}]
    L.stampCrests(rows)
    assert "wf-sha" in rows[0]["crest"] and "hs-sha" in rows[1]["crest"]


def test_a_team_row_takes_its_race_level():
    """Nuttycombe's team scores (owner, 2026-10-28): a team has no pool of
    its own; the race's most common level names its crest."""
    import app as A
    assert A._modalLevel(["college_m", "college_m|x", None, "hs_m"]) == "college"
    assert A._modalLevel([None, None]) is None
