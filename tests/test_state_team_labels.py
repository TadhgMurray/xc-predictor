"""A state-team race draws its states bare; an ordinary race keeps suffixes
(owner, 2026-10-07: RunningLane Track Championships read "Arkansas (AL)")."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))

import school_identity as si  # noqa: E402


def test_mostly_states_is_state_team_race():
    assert si.isStateTeamRace(["Arkansas", "Texas", "Ohio", "Brooks Beasts TC", None, ""])


def test_one_state_named_school_is_not():
    assert not si.isStateTeamRace(["Oregon", "Naperville North", "Glenbard West"])


def test_empty_race_is_not():
    assert not si.isStateTeamRace([None, ""])


def test_filters_respect_flag():
    import jinja2
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    src = open(os.path.join(os.path.dirname(__file__), "..", "racecast", "app.py")).read()
    assert "_stateTeamAware(school_identity.schoolLabelFor)" in src
    assert src.count("state_teams=school_identity.isStateTeamRace(") == 3
    env = jinja2.Environment()
    ns = {"jinja2": jinja2, "school_identity": si}
    start = src.index("def _stateTeamAware")
    exec(src[start:src.index("\n\n\n", start)], ns)
    env.filters["with_state"] = ns["_stateTeamAware"](si.withState)
    t = env.from_string("{{ s|with_state('AL') }}")
    assert t.render(s="Arkansas", state_teams=True) == "Arkansas"
    assert t.render(s="Arkansas", state_teams=False) == "Arkansas (AL)"
    assert t.render(s="Hoover", state_teams=True) == "Hoover (AL)"


def test_state_team_gets_no_crest_or_link():
    src = open(os.path.join(os.path.dirname(__file__), "..", "racecast", "app.py")).read()
    start = src.index('@app.template_filter("is_team")')
    body = src[start:src.index("\n# ", start)]
    assert "@jinja2.pass_context" in body
    assert 'ctx.get("state_teams") and school_identity.isStateName(school)' in body
