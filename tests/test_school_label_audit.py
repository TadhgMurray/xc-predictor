"""school_label_audit.classify: the names a page could label with the wrong
institution's state (owner, 2026-11-05: Georgetown TX, Notre Dame WI)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import school_label_audit as A                                # noqa: E402


def test_the_cases():
    # Georgetown: a college row, primary TX (a high school), directory DC/KY
    assert A.classify("Georgetown", "college", "TX", ["DC", "KY"], {"TX": .8, "DC": .2}) \
        == "college_elsewhere"
    assert A.classify("Notre Dame", "college", "WI", ["IN"], {"WI": .6, "IN": .4}) \
        == "college_elsewhere"
    assert A.classify("Tufts", "college", "MA", ["MA"], {"MA": 1.0}) is None
    assert A.classify("Nowhere CC", "college", "IA", [], {"IA": 1.0}) == "college_unknown"
    # a high school row shown under a college's state, with its own cluster elsewhere
    assert A.classify("Georgetown", "hs", "DC", ["DC", "KY"], {"TX": .4, "DC": .6}) \
        == "school_as_college"
    assert A.classify("Georgetown", "hs", "TX", ["DC", "KY"], {"TX": .8, "DC": .2}) is None
