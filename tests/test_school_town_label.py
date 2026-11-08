"""athletic.net's short town brackets (owner, 2026-11-08: "San Marcos (SB)?")."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import school_identity as SI  # noqa: E402


def test_two_letters_name_a_state_only_when_they_are_one():
    assert not SI.namesItsState("San Marcos (SB)")
    assert SI.namesItsState("La Jolla (CA)") and SI.namesItsState("St. Joseph's (Me.)")
    assert SI.withState("San Marcos (SB)", "CA") == "San Marcos (SB) (CA)"


def test_the_town_replaces_the_bracket(monkeypatch):
    monkeypatch.setitem(SI._LABELS, "qual_city", {"San Marcos (SB)": "Santa Barbara"})
    assert SI.withTown("San Marcos (SB)", "CA") == "San Marcos (Santa Barbara, CA)"
    assert SI.withTown("San Marcos (SB)", None) == "San Marcos (Santa Barbara)"
    assert SI.withTown("Oak", "OR") == "Oak (OR)"
