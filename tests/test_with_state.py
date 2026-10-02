"""One "(ST)" per label (2026-10-02: NXN 2024's team scores read
"Xavier (NY) (NY)")."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
from school_identity import withState                              # noqa: E402


def test_appends_once():
    assert withState("Xavier", "NY") == "Xavier (NY)"
    assert withState("Xavier (NY)", "NY") == "Xavier (NY)"
    assert withState("Xavier (ny) ", "NY") == "Xavier (ny) "
    # a different state in the name is a different fact: still appended
    assert withState("Downers Grove (North)", "IL") == "Downers Grove (North) (IL)"


def test_nothing_to_add():
    assert withState("Xavier", None) == "Xavier"
    assert withState("", "NY") == ""
    assert withState(None, "NY") is None
