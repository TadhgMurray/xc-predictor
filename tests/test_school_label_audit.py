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
    # school rows are not this audit's: a Washington high school in WA is right
    assert A.classify("Washington", "hs", "WA", ["WA"], {"WA": 1.0}) is None
    # a club or an unattached entry is no college
    assert A.classify("Penn State University Club", "college", "PA", [], {}) is None
    assert A.classify("Unattached", "college", "CA", [], {}) is None



def test_race_pages_label_a_college_row_by_its_pool():
    for t in ("race.html", "compiled.html"):
        src = open(os.path.join(_ROOT, "racecast", "templates", t)).read()
        assert "school_label_in(" in src
        assert src.count("pool=(row.rating_pool or row.label_pool)") >= 3, t
