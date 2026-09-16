# Project: xc-predictor / tests
# File:    test_race_school_state.py
# Purpose: a result row's school is labelled, linked and crested from the
#          ATHLETE's assignment, not from the meet's state (owner,
#          2026-09-16: "the races still say WIlliams(CA)"), and a colliding
#          name's scoring splits on the same answer ("MIT(CT) and Tufts(CT)
#          which have become diff 'schools' in races"). No database.
#
#   python -m pytest -q tests/test_race_school_state.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import meet_compile as mc                                      # noqa: E402


class _Cur:
    """Enough cursor for these two functions: a table probe, then one
    lookup answered from `rows`."""
    def __init__(self, rows, have=True):
        self.rows, self.have, self.out = rows, have, []
    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.out = [(self.have,)]
        elif "school_athlete_state" in sql:
            self.out = list(self.rows)
        else:
            self.out = []
    def fetchone(self):
        return self.out[0] if self.out else None
    def fetchall(self):
        return self.out


def test_the_row_is_stamped_with_the_athletes_own_cluster():
    rows = [{"school": "Williams", "person_id": 1},
            {"school": "Williams", "person_id": 2},
            {"school": "Williams", "person_id": 3}]      # not in the table
    cur = _Cur([("Williams", 1, "MA"), ("Williams", 2, "CA")])
    mc.stampSchoolStates(cur, rows)
    assert rows[0]["school_state"] == "MA"          # the college
    assert rows[1]["school_state"] == "CA"          # the high school
    assert "school_state" not in rows[2]            # falls back to the meet's


def test_it_is_a_no_op_without_the_table_or_without_rows():
    rows = [{"school": "Williams", "person_id": 1}]
    mc.stampSchoolStates(_Cur([], have=False), rows)
    assert "school_state" not in rows[0]
    mc.stampSchoolStates(_Cur([]), [])              # must not query at all
    mc.stampSchoolStates(_Cur([]), [{"school": None, "person_id": None}])


def test_a_trusted_state_is_not_re_judged_by_size():
    """⚠ WHY THE STAMP ALONE DID NOTHING (owner: "nOpe didn\'t work").
    contextState only accepts a state that holds CONTEXT_MIN_SHARE of the
    name's athletes -- a bar on a GUESS, to stop a stray away meet from
    labelling a school. Williams College is a few dozen athletes against a
    California high school's 1,401, so MA is under 3% and the right answer
    was discarded one function after being worked out."""
    import school_identity as si
    saved = dict(si._LABELS)
    try:
        si._LABELS.update({"loaded": True, "map": {"Williams": "CA"},
                           "clusters": {"Williams": {"CA": 0.98, "MA": 0.019}}})
        assert si.schoolLabelIn("Williams", "MA") == "Williams (CA)"
        assert si.schoolLabelIn("Williams", "MA", True) == "Williams (MA)"
        # a venue state is still a guess and still judged
        assert si.schoolLabelIn("Williams", "CT") == "Williams (CA)"
        assert si.contextState("Williams", "MA", trusted=True) == "MA"
        assert si.schoolHref("Williams", "MA", trusted=True).endswith("state=MA")
    finally:
        si._LABELS.clear(); si._LABELS.update(saved)


def test_undefined_is_not_a_trusted_state():
    """Jinja gives Undefined for a missing key, and `Undefined is not none`
    is TRUE -- which would have trusted the meet's state on every row the
    stamp could not place."""
    from jinja2 import Environment
    tpl = Environment().from_string(
        "{% set sst = row.school_state or header.state %}"
        "{% set sok = row.school_state is defined and row.school_state %}"
        "{{ sst }}|{{ 'Y' if sok else 'n' }}")
    assert tpl.render(row={"school_state": "MA"}, header={"state": "CT"}) == "MA|Y"
    assert tpl.render(row={}, header={"state": "CT"}) == "CT|n"
    assert tpl.render(row={"school_state": ""}, header={"state": "CT"}) == "CT|n"


def test_the_template_prefers_it_and_falls_back_to_the_meet():
    html = open(os.path.join(_ROOT, "racecast", "templates", "race.html")).read()
    assert "{% set sst = row.school_state or header.state %}" in html
    # the label, the link and the crest all read the same answer -- the whole
    # point of school_identity.contextState's "a mention resolves ONCE"
    cell = html[html.index("{% set sst ="):]
    cell = cell[:cell.index("</td>")]
    # the set, then the crest, the href and the label: four readers, one
    # answer -- school_identity.contextState's "a mention resolves ONCE"
    assert cell.count("sst") == 5
    assert cell.count("header.state") == 1          # only inside the set
    # and the trust flag rides with it, on all three readers
    assert "{% set sok = row.school_state is defined and row.school_state %}" in cell
    assert cell.count("sok") == 5      # the set, then four readers


def test_the_race_route_stamps_before_it_renders():
    src = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    body = src[src.index("def race_xc(meet_id, div_id):"):]
    body = body[:body.index("render_template(\"race.html\"")]
    assert "stampSchoolStates(cur, results)" in body
    assert body.index("results = get_race_results(") < body.index("stampSchoolStates(")


def test_the_scoring_split_prefers_the_assignment_over_the_home_state():
    src = open(os.path.join(_ROOT, "racecast", "meet_compile.py")).read()
    body = src[src.index("def splitCollisionTeams(cur, rows):"):]
    assert 'if pids and _has("school_athlete_state"):' in body
    # the assignment is tried first, and only then the home state and alias
    i_assigned = body.index('st = assigned.get((s, r.get("person_id")))')
    i_home = body.index('st = home.get(r.get("person_id"))')
    assert i_assigned < i_home
    # the clamp stays: nobody may vanish from scoring
    assert "if st not in clus[s]:" in body
