"""app._season_state: the most-raced state the school has a school in
(owner, 2026-10-11: Jesuit (LA) seasons labelled Jesuit (OR))."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: F401,E402

import pytest  # noqa: E402

pytest.importorskip("flask")


def test_a_travelling_season_resolves_in_its_home_state(monkeypatch):
    import app as A
    import school_identity as SI
    monkeypatch.setitem(SI._LABELS, "clusters", {"Jesuit": {"OR": 0.5, "LA": 0.2, "CA": 0.2}})
    races = [{"state": s} for s in ("AL", "AL", "NC", "TN", "TN", "LA", "LA")]
    assert A._season_state(races, "Jesuit") == "LA"
    # a name with no cluster anywhere it raced keeps the plain mode
    assert A._season_state(races, "Nowhere High") in ("AL", "TN")
    assert A._season_state([], "Jesuit") is None
