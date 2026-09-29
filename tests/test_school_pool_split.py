"""
The school page ranks each POOL on its own, and names the same class for an
athlete as the athlete page does.

The two defects this pins (outside review of the Amherst page, 2026-09-29):

  1. Katie Greenwald (109.0, "top 26.5% of college women") ranked eighth on
     the roster between Parker Boyle and Michael Rynne. A rating is a place
     in its own pool, so a college_f number ranked among college_m numbers is
     a race nobody ran. app.rowsByPool now splits every ranked table on the
     page by pool: men and women, and each level.

  2. Harrison Dow read SR-4 on the Amherst roster and JR-3 on his own page;
     Greenwald SO-2 and FR-1. The roster was right -- it is the 2026 cross
     country season, his fourth -- and the athlete header was a year behind:
     it took its class from the season the header RATING quotes (2026 track,
     three races deep) rather than the season its TEAM comes from (2026 XC).
     Both now ask grade_label.classGrade for the academic year of the season
     that names the team.

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_school_pool_split.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env                                   # noqa: E402,F401  -- before config

import app                                    # noqa: E402
import school                                 # noqa: E402
from grade_label import classGrade, gradeLabel  # noqa: E402


def _row(pid, name, pool, rating, grade=None):
    return {"person_id": pid, "name": name, "pool": pool,
            "mean_rating": rating, "best_rating": rating, "grade": grade,
            "hs_mean_rating": None, "hs_best_rating": None,
            "n_races": 2, "last_race": "2026-09-05"}


# The top of the live Amherst (MA, college) 2026 XC roster, in its order.
AMHERST = [
    _row(23792357, "Harrison Dow",    "college_m", 112.5, "SR-4"),
    _row(29664023, "Carter Bengtson", "college_m", 112.1, "SO-2"),
    _row(23792361, "Parker Boyle",    "college_m", 109.6, "SR-4"),
    _row(29664025, "Katie Greenwald", "college_f", 109.0, "SO-2"),
    _row(21306932, "Michael Rynne",   "college_m", 107.8, "FR-1"),
    _row(21601055, "Eva Muzichenko",  "college_f", 104.3, "FR-1"),
]


# ----------------------------------------------------------------------
#  1. one ranked list per pool
# ----------------------------------------------------------------------

def test_men_and_women_are_two_lists_each_in_the_page_order():
    groups = app.rowsByPool(AMHERST)
    assert [label for label, _ in groups] == ["Men", "Women"]
    men, women = groups[0][1], groups[1][1]
    assert [r["name"] for r in men] == ["Harrison Dow", "Carter Bengtson",
                                         "Parker Boyle", "Michael Rynne"]
    assert [r["name"] for r in women] == ["Katie Greenwald", "Eva Muzichenko"]
    # nothing lost, nothing duplicated
    assert sorted(r["person_id"] for _, g in groups for r in g) == \
        sorted(r["person_id"] for r in AMHERST)


def test_no_group_ever_holds_two_pools():
    rows = AMHERST + [_row(9, "Mid", "ms_f", 120.0), _row(10, "Hs", "hs_m", 101.0),
                      _row(11, "Pipe", "hs_m|TF", 99.0)]
    for _label, rows_ in app.rowsByPool(rows):
        assert len({(r["pool"] or "").split("|")[0] for r in rows_}) == 1


def test_several_levels_name_the_level_and_the_gender_in_level_order():
    rows = [_row(1, "a", "ms_f", 130.0), _row(2, "b", "college_f", 120.0),
            _row(3, "c", "hs_m", 110.0), _row(4, "d", "hs_f", 100.0),
            _row(5, "e", "college_m", 90.0)]
    assert [label for label, _ in app.rowsByPool(rows)] == [
        "High school boys", "High school girls",
        "College men", "College women", "Middle school girls"]


def test_a_high_school_says_boys_and_girls():
    rows = [_row(1, "a", "hs_f", 130.0), _row(2, "b", "hs_m", 120.0)]
    assert [label for label, _ in app.rowsByPool(rows)] == ["Boys", "Girls"]


def test_one_pool_is_one_plain_table():
    assert app.rowsByPool([r for r in AMHERST if r["pool"] == "college_m"]) == []
    assert app.rowsByPool([]) == [] and app.rowsByPool(None) == []


def test_a_row_with_no_pool_is_kept_not_dropped():
    rows = [_row(1, "a", "hs_m", 110.0), _row(2, "b", None, 105.0)]
    groups = app.rowsByPool(rows)
    assert [label for label, _ in groups] == ["Boys", "Other"]
    assert groups[1][1][0]["person_id"] == 2


def test_the_template_ranks_each_pool_from_one():
    """Rendered, not just grouped: the rank column restarts and Greenwald's
    row is in the women's table, never between two men."""
    import re
    from flask import render_template
    roster = [dict(r) for r in AMHERST]
    app.sortByShown(roster, "mean_rating")
    top = [{"person_id": r["person_id"], "name": r["name"], "pool": r["pool"],
            "best": r["mean_rating"], "hs_best": None, "seasons": 1,
            "first_year": 2026, "last_year": 2026} for r in roster]
    with app.app.test_request_context("/school/Amherst?state=MA"):
        html = render_template(
            "school.html", school="Amherst", header={"state": "MA", "athletes": 6},
            units=[], level_chips=[], level=None, state_chips=[], state="MA",
            has_hs_view=False, years=[], year=2026, sport="XC",
            pools={"M": "college_m", "F": "college_f"}, season_rebuilding=False,
            roster=roster, roster_groups=app.rowsByPool(roster),
            meets=[], best=[], best_groups=[],
            top=top, top_groups=app.rowsByPool(top), picked=None)
    tables = re.findall(r'<h3 class="roster-level">(\w+).*?<tbody>(.*?)</tbody>',
                        html, re.S)
    # roster men, roster women, best-athletes men, best-athletes women
    assert [t[0] for t in tables] == ["Men", "Women", "Men", "Women"]
    for label, body in tables:
        ranks = re.findall(r"<tr[^>]*>\s*<td>(\d+)</td>", body)
        assert ranks == [str(i) for i in range(1, len(ranks) + 1)]
        names = re.findall(r'/athlete/\d+">([^<]+)<', body)
        want = "college_f" if label == "Women" else "college_m"
        assert names == [r["name"] for r in roster if r["pool"] == want]
    assert "Men →" in html and "Women →" in html


# ----------------------------------------------------------------------
#  2. one class for one athlete in one season
# ----------------------------------------------------------------------

def test_class_is_the_year_s_highest_eligibility():
    # Joey Sullivan (owner, 2026-09-07): fourth XC season, third track season
    assert classGrade(["SR-4", "JR-3"], "college_m") == "SR-4"
    assert classGrade(["JR-3", "SR-4"], "college_m") == "SR-4"
    assert classGrade(["Jr", "16"], "college_f") == "16"     # stored value back
    assert classGrade(["SO-2"], "college_f") == "SO-2"
    assert classGrade([None, "", "FR-1"], "college_f") == "FR-1"
    assert classGrade([], "college_m") is None and classGrade(None) is None


def test_a_school_pool_keeps_its_own_grade():
    # "12" must not read as a second-year through the eligibility digit
    assert classGrade(["11", "12"], "hs_m") == "11"
    assert classGrade(["Unknown", "SR-4"], "college_m") == "SR-4"
    assert classGrade(["Unknown"], "college_m") == "Unknown"


class _Cur:
    """Just enough cursor for schoolRoster(carry=False): no database."""
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return [dict(r) for r in self.rows]


def test_the_roster_reads_the_other_sport_of_the_same_year():
    cur = _Cur([dict(_row(1, "Joey", "college_m", 110.0, "JR-3"),
                     year_grades=["SR-4"]),
                dict(_row(2, "Hs", "hs_m", 100.0, "11"), year_grades=["12"]),
                dict(_row(3, "New", "college_f", 99.0, "FR-1"), year_grades=[])])
    rows = school.schoolRoster(cur, "Amherst", 2025, "TF", carry=False)
    assert [r["grade"] for r in rows] == ["SR-4", "11", "FR-1"]
    assert "year_grades" in cur.sql[0] and "o.sport <> s.sport" in cur.sql[0]


def test_best_athletes_carries_its_pool_out():
    import capped
    cur = _Cur([])
    cur.fetchmany = lambda n=None: []
    try:
        school.schoolTopAthletes(cur, "Amherst", "XC", limit=5)
    except Exception:            # noqa: BLE001 -- only the SQL is under test
        pass
    outer = cur.sql[0].split("FROM (")[0]
    assert "pool" in outer, "the outer SELECT drops the pool rowsByPool needs"
    assert capped                                   # imported, not unused


def _seasons(**blocks):
    """{(label, sport): {"grade": g}} the way enrich_seasons keys them."""
    out = {}
    for k, g in blocks.items():
        sport, label = k[:2].upper(), int(k[2:])
        out[(label, sport)] = {"grade": g}
    return out


def test_dow_header_and_roster_agree():
    # His career as the live athlete page lists it (2026-09-29).
    seasons = _seasons(xc2023="FR-1", xc2024="SO-2", xc2025="JR-3",
                       xc2026="SR-4", tf2024="FR-1", tf2025="SO-2",
                       tf2026="JR-3")
    # the header RATING's season: 2026 track, stored 2025, three races deep
    season_rating = {"year": 2025, "sport": "TF", "pool": "college_m",
                     "grade": "JR-3"}
    # the header TEAM's season: 2026 XC, two races so far
    latest_team = {"year": 2026, "sport": "XC", "pool": "college_m",
                   "grade": "SR-4", "school": "Amherst"}
    cls = app._headerClassSeason(season_rating, latest_team)
    assert cls is latest_team
    header = gradeLabel(app._classGrade(seasons, cls), cls["pool"])

    # the 2026 XC roster row, as schoolRoster builds it
    roster_row = school._yearClass(dict(_row(23792357, "Harrison Dow",
                                             "college_m", 112.5, "SR-4"),
                                        year_grades=[]))
    roster = gradeLabel(roster_row["grade"], roster_row["pool"])
    assert header == roster == "SR-4"


def test_greenwald_header_and_roster_agree():
    seasons = _seasons(xc2025="FR-1", xc2026="SO-2", tf2026="FR-1",
                       tf2025="12")
    season_rating = {"year": 2025, "sport": "TF", "pool": "college_f",
                     "grade": "FR-1"}
    latest_team = {"year": 2026, "sport": "XC", "pool": "college_f",
                   "grade": "SO-2"}
    cls = app._headerClassSeason(season_rating, latest_team)
    header = gradeLabel(app._classGrade(seasons, cls), cls["pool"])
    row = school._yearClass(dict(_row(29664025, "Katie Greenwald",
                                      "college_f", 109.0, "SO-2"),
                                 year_grades=[]))
    assert header == gradeLabel(row["grade"], row["pool"]) == "SO-2"


def test_the_track_roster_and_the_header_agree_on_a_split_eligibility():
    # a spring page for Joey Sullivan's year: XC SR-4, track JR-3
    seasons = _seasons(xc2025="SR-4", tf2026="JR-3")
    rated = {"year": 2025, "sport": "TF", "pool": "college_m", "grade": "JR-3"}
    cls = app._headerClassSeason(rated, dict(rated))
    header = app._classGrade(seasons, cls)
    row = school._yearClass(dict(_row(1, "Joey Sullivan", "college_m", 110.0,
                                      "JR-3"), year_grades=["SR-4"]))
    assert header == row["grade"] == "SR-4"


def test_the_header_keeps_its_own_season_across_a_pool_change():
    # Michael Rynne: header rated on 2026 track at Iona (hs_m); his newest
    # team season is Amherst college. An FR-1 spelled in hs_m would read 9.
    season_rating = {"year": 2025, "sport": "TF", "pool": "hs_m", "grade": "12"}
    latest_team = {"year": 2026, "sport": "XC", "pool": "college_m",
                   "grade": "FR-1"}
    assert app._headerClassSeason(season_rating, latest_team) is season_rating


def test_an_older_team_season_does_not_turn_the_class_back():
    season_rating = {"year": 2026, "sport": "XC", "pool": "college_m", "grade": "SR-4"}
    older = {"year": 2025, "sport": "TF", "pool": "college_m", "grade": "JR-3"}
    assert app._headerClassSeason(season_rating, older) is season_rating
    assert app._headerClassSeason(season_rating, None) is season_rating
    assert app._headerClassSeason(None, older) is None


def test_a_stray_poolless_row_does_not_change_the_other_headings():
    rows = AMHERST + [_row(99, "x", None, 90.0), _row(98, "y", "", 80.0)]
    groups = app.rowsByPool(rows)
    assert [label for label, _ in groups] == ["Men", "Women", "Other"]
    assert [r["person_id"] for r in groups[2][1]] == [99, 98]


def test_school_card_takes_one_pool():
    import cards as C
    rows = [{"pool": "college_m"}] * 5 + [{"pool": "college_f"}] * 7
    assert C.cardPool(rows) == "college_f"
    assert C.cardPool([{"pool": "hs_m"}, {"pool": "hs_f"}]) == "hs_m", "men on a tie"
    assert C.cardPool([]) is None
    assert C.poolWord("college_f") == "women" and C.poolWord("hs_m") == "boys"
    assert C.poolWord(None) == ""


def test_best_tables_cap_per_pool():
    from capped import fetchCappedPerPool

    class Cur:
        def fetchall(self):
            return ([{"pool": "college_m", "i": i} for i in range(3)]
                    + [{"pool": "college_f", "i": i} for i in range(2)])
    got = fetchCappedPerPool(Cur(), 2)
    assert [r["pool"] for r in got] == ["college_m", "college_m", "college_f", "college_f"]
    assert got.truncated
