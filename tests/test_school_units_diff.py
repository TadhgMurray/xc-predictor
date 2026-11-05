"""build_school_units --dry-run --diff: what a rebuild would change."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import build_school_units as B                                  # noqa: E402


def _row(school, st, conf, region, div, college=True, votes=9):
    cells = {c: None for c in B._COLS}
    cells.update(conference=conf, region=region, division=div)
    return [school, st, "XC"] + [cells[c] for c in B._COLS] + [college, False, votes, "2025"]


def test_changed_lost_and_gained():
    rows = [_row("Calvin", "MI", "MIAA", "GREAT LAKES", "NCAA DIII"),
            _row("Hope", "MI", None, "GREAT LAKES", "NCAA DIII"),
            _row("Calvin", "OK", None, None, None, college=False)]
    live = {("Calvin", "MI", "XC"): ("CIAA", "GREAT LAKES VALLEY", "NCAA DIII"),
            ("Hope", "MI", "XC"): ("MIAA", "GREAT LAKES", "NCAA DIII"),
            ("Gone", "OH", "XC"): ("OAC", None, None)}
    d = B.diffRows(rows, live)
    assert [x[4:] for x in d["conference"]["changed"]] == [("CIAA", "MIAA")]
    assert {x[1] for x in d["conference"]["lost"]} == {"Hope", "Gone"}
    assert [x[4:] for x in d["region"]["changed"]] == [("GREAT LAKES VALLEY", "GREAT LAKES")]
    assert not d["division"]["changed"]
