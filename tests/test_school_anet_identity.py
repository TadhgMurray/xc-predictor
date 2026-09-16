# Project: xc-predictor / tests
# File:    test_school_anet_identity.py
# Purpose: two schools wearing one name are separated by anet's own team
#          ids and locations, not by where their athletes happen to race
#          (owner, 2026-09-16: "Oregon(IL) and Oregon(or) are colliding
#          despite hs vs college ... Williams (CA) vs (MA)"). No database.
#
#   python -m pytest -q tests/test_school_anet_identity.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_school_identity as bsi                            # noqa: E402

_SRC = open(os.path.join(_ROOT, "racecast", "build_school_identity.py")).read()


def test_anet_naming_both_states_means_two_schools():
    contested = {"oregon": {"IL": 1, "OR": 1}, "kingston": {"WA": 1, "MO": 1}}
    assert bsi.anetSaysTwoSchools(contested, "Oregon", "IL", "OR")
    assert bsi.anetSaysTwoSchools(contested, " oregon ", "or", "il")   # either spelling
    # a travel state anet does not name for the name is not a second school
    assert not bsi.anetSaysTwoSchools(contested, "Oregon", "IL", "CA")
    # a name anet places in one state, or does not know, never refuses
    assert not bsi.anetSaysTwoSchools(contested, "Williams", "CA", "MA")
    assert not bsi.anetSaysTwoSchools({}, "Oregon", "IL", "OR")
    assert not bsi.anetSaysTwoSchools(contested, None, "IL", "OR")


def test_the_merge_consults_it_before_the_shared_meets():
    """The refusal has to come BEFORE the threshold, because the merge
    spreads by union-find: one accepted pair pulls in every cluster
    already joined to either side."""
    body = _SRC[_SRC.index("def mergeCoRacingClusters("):
                _SRC.index("# ★ THE DIRECTORY OUTRANKS THE DATA FOR A COLLEGE")]
    assert body.index("anetSaysTwoSchools") < body.index("shared >= MERGE_MIN_SHARED")
    assert "refused += 1" in body and "continue" in body


def test_the_clusters_read_anets_state_first():
    """COALESCE(ts.state, ph.state) -- the athlete's own team's state, and
    the home-state inference only where there is no team id."""
    assert "COALESCE(ts.state, ph.state)" in _SRC
    assert "LEFT   JOIN si_team_state ts ON ts.person_id = v.person_id" in _SRC
    assert "GROUP  BY v.school, COALESCE(ts.state, ph.state)" in _SRC


def test_the_scan_is_filtered_to_contested_names_and_skips_the_zero_team():
    """⚠ One pass over results is affordable only because the name list is
    short; and team_id = 0 is not a school (pool_resolve)."""
    body = _SRC[_SRC.index("def buildTeamStates("):_SRC.index("def anetSaysTwoSchools(")]
    assert "lower(btrim(r.school)) = ANY(%s)" in body
    assert "r.team_id <> 0" in body
    assert "if not contested:" in body                     # no names, no scan
    for table in ("results", "results_tf"):
        assert f'for table in ("results", "results_tf")' in body


def test_it_all_degrades_without_anet_team():
    """No anet_team -> no contested names -> no scan, empty si_team_state,
    and every COALESCE falls through to the old answer."""
    body = _SRC[_SRC.index("def anetContestedStates("):_SRC.index("def buildTeamStates(")]
    assert "to_regclass('anet_team')" in body and "return {}" in body


def test_the_build_orders_them_before_the_clusters():
    i_anet = _SRC.index("        contested = anetContestedStates(cur)")
    i_team = _SRC.index("        buildTeamStates(cur, contested)")
    i_clusters = _SRC.index("CREATE TABLE school_identity_new AS")
    i_merge = _SRC.index("        mergeCoRacingClusters(cur, contested)")
    assert i_anet < i_team < i_clusters < i_merge
