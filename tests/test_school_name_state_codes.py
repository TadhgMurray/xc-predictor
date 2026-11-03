"""tfrrs's "Charleston (W.V.)" is anet's "Charleston (W.Va.)" (owner, 2026-11-02)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")


def test_any_dotted_two_letter_state_is_a_state():
    from school_name import nameKey, relation
    assert nameKey("Charleston (W.V.)") == nameKey("Charleston (W.Va.)") == "charleston"
    assert relation("Charleston (W.V.)", "Charleston (W.Va.)") == "same"
    assert nameKey("Notre Dame (Ind.)") == "notre dame"
    # two letters that are no state stay part of the name
    assert nameKey("Team (XQ)") != "team"


def test_feed_twins_read_the_school_without_its_state():
    import link_feed_twins as L
    assert L.sameSchool("Charleston (W.V.)", "Charleston (W.Va.)")
    assert L.sameSchool("Marshall", "Marshall (WV)")
    assert not L.sameSchool("Charleston (W.V.)", "Glenville State")
    assert "regexp_replace" in L._schoolWordsSql("r.school")


def test_college_person_state_prefers_the_teams_own_state():
    """Georgetown University's runners were placed in KY by where they raced
    (owner, 2026-11-03); the anet team's state and the tfrrs link win."""
    src = open(os.path.join(_ROOT, "racecast", "build_school_identity.py")).read()
    body = src[src.index("def buildCollegePersonState"):]
    assert "COALESCE(tm.state, lk.state, rc.state)" in body
    assert "si_team_state" in body and "si_link_state" in body
