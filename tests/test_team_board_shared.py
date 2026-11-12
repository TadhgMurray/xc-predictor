"""/api/teams' board is computed once for the box (app._sharedTeamBoard,
2026-11-12): the same filters read the shared copy, the filters serveBoard
settled come back with it, and other filters get their own."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401


def test_one_board_per_filter_set(tmp_path, monkeypatch):
    import app as A
    import sharedcache
    import teams
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "tableOid", lambda cur, t, now=None: 3)
    calls = []

    def serve(cur, f):
        calls.append(dict(f))
        f["span"] = "projected"                    # what serveBoard settles
        f["sort"] = "rank"
        return [{"school": "Oak", "rank": 1}], {"raced": True, "field_size": 40}
    monkeypatch.setattr(teams, "serveBoard", serve)
    f1 = {"pool": "hs_m", "sport": "XC", "span": "season", "projected": True}
    rows, info = A._sharedTeamBoard(None, f1)
    rows[0]["school"] = "edited"
    f2 = {"pool": "hs_m", "sport": "XC", "span": "season", "projected": True}
    rows2, info2 = A._sharedTeamBoard(None, f2)
    assert len(calls) == 1
    assert rows2 == [{"school": "Oak", "rank": 1}] and info2 == {"raced": True, "field_size": 40}
    assert f2["span"] == "projected" and f2["sort"] == "rank"     # settled, as computed
    A._sharedTeamBoard(None, {"pool": "hs_f", "sport": "XC", "span": "season"})
    assert len(calls) == 2
