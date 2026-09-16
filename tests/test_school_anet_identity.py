# Project: xc-predictor / tests
# File:    test_school_anet_identity.py
# Purpose: two schools wearing one name are separated by the states NAMED
#          schools actually sit in -- anet's teams and the college
#          directory -- not by where their athletes happen to race.
#          (owner, 2026-09-16: "Oregon(IL) and Oregon(or) are colliding
#          despite hs vs college ... Williams (CA) vs (MA)".) No database.
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

# what the first run measured, and what the fix has to change
_CONTESTED = {"oregon": {"IL", "OR"}, "williams": {"CA", "MA"}}


def test_a_clusters_state_is_authoritative_only_when_a_named_school_sits_there():
    assert bsi.authStatesOf(_CONTESTED, "Oregon", "IL") == {"IL"}
    assert bsi.authStatesOf(_CONTESTED, " oregon ", "or") == {"OR"}
    assert bsi.authStatesOf(_CONTESTED, "Oregon", "TX") == set()   # a travel state
    assert bsi.authStatesOf(_CONTESTED, "Kingston", "WA") == set() # not contested
    assert bsi.authStatesOf({}, "Oregon", "IL") == set()
    assert bsi.authStatesOf(_CONTESTED, None, "IL") == set()


def test_the_guard_is_on_the_group_because_the_merge_is_transitive():
    """⚠ THE BUG IN THE FIRST VERSION. Refusing the IL-OR pair does not keep
    IL and OR apart: IL merges with CA, CA merges with OR, and all 36 of
    Oregon's home states end in one group with the pair never tested. 737
    refusals fired and Oregon still came out as a single cluster."""
    assert bsi.wouldMergeTwoSchools({"IL"}, {"OR"})
    # a travel state may join a named one, and travel states may join freely
    assert not bsi.wouldMergeTwoSchools({"IL"}, set())
    assert not bsi.wouldMergeTwoSchools(set(), set())
    # ...and once a group HOLDS a named state it carries it: that is what
    # makes the guard transitive
    assert bsi.wouldMergeTwoSchools({"IL"} | set(), {"OR"})


def test_the_merge_carries_each_groups_named_states_and_asks_before_uniting():
    body = _SRC[_SRC.index("def mergeCoRacingClusters("):
                _SRC.index("# ★ THE DIRECTORY OUTRANKS THE DATA FOR A COLLEGE")]
    # the question is asked of find(a)/find(b), not of (s1, s2)
    assert "wouldMergeTwoSchools(authOf(a), authOf(b))" in body
    # and the union is recorded, or the guard would forget on the next pair
    assert "auth[a] = authOf(a) | authOf(b)" in body
    assert body.index("a, b = find(") < body.index("wouldMergeTwoSchools")


def test_a_group_holding_a_named_school_resolves_to_that_state():
    """Without this the group holding OR resolves by row counts, and a
    college's rows are mostly away -- which is how "Oregon (CA)" and
    "Furman (FL)" happened (school_identity.teamState's docstring)."""
    body = _SRC[_SRC.index("    rows, alias = [], []"):]
    assert "if len(named) == 1:" in body
    assert body.index("named = {st for st in states") < body.index("host = hosted.get(sc)")
    assert body.index("if len(named) == 1:") < body.index("elif host and host[1] >= 2")


def test_the_assignment_is_written_down_so_the_page_and_the_counts_agree():
    """⚠ THE HALF THE FIRST ATTEMPT MISSED. The clusters were COUNTED from
    the COALESCE, but the site re-derived membership from person_home_state
    alone (stateFilterSql), so /school/Oregon?state=OR listed the athletes
    who RACE in Oregon, not the ones who run for the university."""
    src = open(os.path.join(_ROOT, "racecast", "school_identity.py")).read()
    assert "def stateFilterSql(alias, state, primary, school=None):" in src
    body = src[src.index('    if not state:\n        return "", {}'):
               src.index("def homeStates(")]
    assert body.index("school_athlete_state") < body.index("person_home_state")
    assert '_LABELS.get("athlete_state")' in body      # absent table: old clause
    assert '_LABELS["athlete_state"] = _tableExists(cur, "school_athlete_state")' in src
    # the four school-page queries pass the school, or the clause cannot fire
    school_py = open(os.path.join(_ROOT, "racecast", "school.py")).read()
    assert school_py.count("stateFilterSql(") == 4          # all four queries
    assert "stateFilterSql(\"s\", state, primary, school)" in school_py
    assert "stateFilterSql(\"rr\", state, primary, school)" in school_py
    # and it is built from si_assign AFTER the merge and the directory folded
    assert "def buildAthleteState(cur, contested):" in _SRC
    assert "LEFT   JOIN school_state_alias_new al" in _SRC
    assert _SRC.index("buildSchoolLevel(cur)\n        conn.commit()\n\n        buildAthleteState")
    assert "\"school_athlete_state\"):" in _SRC            # swapped with the rest


def test_the_directory_places_a_college_season_because_anet_cannot():
    """★ WHY THE FIRST RUN CHANGED NOTHING: every hs-vs-college collision is
    one anet school and one tfrrs school, tfrrs XC rows carry no anet team
    id, and `results` has no team_slug -- so the half being placed was the
    half that was already right."""
    assert "LEFT   JOIN si_dir_state ds ON ds.school = v.school" in _SRC
    assert "CASE WHEN v.college THEN ds.state END" in _SRC
    # anet's own team first, the directory second, the inference last
    start = _SRC.index("            CREATE TEMP TABLE si_assign AS")
    cte = _SRC[start:_SRC.index("CREATE TABLE school_identity_new AS")]
    assert cte.index("ts.state") < cte.index("ds.state") < cte.index("ph.state")
    # and the college-season flag comes from the pool, without a LIKE pattern
    assert "starts_with(rr.pool, 'college')" in _SRC


def test_contested_names_only_so_every_other_name_is_unchanged():
    body = _SRC[_SRC.index("def buildDirStates("):_SRC.index("# ⚠ AND THE PAIRWISE")]
    assert "if key in contested and key in dir_states:" in body
    team = _SRC[_SRC.index("def buildTeamStates("):_SRC.index("# ⚠ AND THE PAIRWISE")]
    assert "lower(btrim(r.school)) = ANY(%s)" in team and "r.team_id <> 0" in team


def test_it_all_degrades_without_either_source():
    body = _SRC[_SRC.index("def authoritativeStates("):_SRC.index("def _schoolNames(")]
    assert "to_regclass('anet_team')" in body
    assert "except Exception as exc" in body          # no directory: keep going
    assert "if len(v) >= 2" in body


def test_the_build_orders_them_before_the_clusters():
    i_auth = _SRC.index("        contested, dir_states = authoritativeStates(cur)")
    i_team = _SRC.index("        buildTeamStates(cur, contested)")
    i_dir = _SRC.index("        buildDirStates(cur, contested, dir_states)")
    i_clusters = _SRC.index("CREATE TABLE school_identity_new AS")
    i_merge = _SRC.index("        mergeCoRacingClusters(cur, contested)")
    assert i_auth < i_team < i_dir < i_clusters < i_merge


def test_the_crest_queue_uses_the_same_assignment():
    """★ THE LOGO (owner, 2026-09-16: "the logos are still the old logo (for
    oregon) ... Williams worked perfectly, they just have no logo anymore").
    school_logo is keyed on school_identity's (school, state) pairs, and
    anet_teams.teams decides which anet TEAM answers for each pair. It read
    person_home_state alone -- where the athlete RACES -- so the modal team
    for (Oregon, OR) was decided by whoever happens to race in Oregon."""
    src = open(os.path.join(_ROOT, "scripts", "anet_teams.py")).read()
    body = src[src.index("def teams(cur"):src.index("def parseTeam(")]
    assert 'LEFT JOIN school_athlete_state sa' in body
    assert body.index("school_athlete_state") < body.index("person_home_state h")
    # and both are optional, so an older database still builds a queue
    assert body.count('_tableExists(cur, "school_athlete_state")') == 1
    assert body.count('_tableExists(cur, "person_home_state")') == 1


def test_the_college_level_is_the_majority_not_any_race():
    """★ OWNER, 2026-09-16: "Oregon (OR) contains hsers still". bool_or meant
    ANY college-pooled season under the name placed the athlete at the
    college -- and the pooling is the thing still being fixed, so one
    mis-pooled race moved an Illinois high schooler onto the university's
    roster."""
    assert "bool_or(starts_with" not in _SRC
    body = _SRC[_SRC.index("            WITH levels AS ("):
                _SRC.index("CREATE TABLE school_identity_new AS")]
    assert "sum(rr.n_races) AS n" in body
    assert "DISTINCT ON (school, person_id)" in body
    assert "ORDER BY school, person_id, n DESC, college DESC" in body


def test_an_athlete_with_no_racing_state_is_still_placed_by_their_team():
    """They had no si_assign row at all, so the site's COALESCE fell through
    to the PRIMARY cluster -- which for a contested name is now sometimes
    the college."""
    body = _SRC[_SRC.index("            CREATE TEMP TABLE si_assign AS"):
                _SRC.index("CREATE TABLE school_identity_new AS")]
    assert "LEFT   JOIN person_home_state_new ph USING (person_id)" in body
    assert "IS NOT NULL" in body.split("WHERE")[-1]
