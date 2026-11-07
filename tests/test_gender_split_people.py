"""gender_split_people (owner, 2026-11-06, /athlete/26140046: Katrina
Coogan's page held a male runner's boys' races, rated as a girl's)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import gender_split_people as G                                 # noqa: E402


def test_a_boys_races_on_a_girls_page_are_moved():
    rows = ([(i, "F", f"2010-09-{10 + i}", 150.0 + i * 0.2) for i in range(6)]
            + [(100 + i, "M", f"2010-10-{10 + i}", 162.0) for i in range(4)])
    move, rids, why = G.verdict(rows, "F")
    assert move == "M" and rids == [100, 101, 102, 103] and "apart" in why


def test_both_races_on_one_day_is_two_people_even_at_one_level():
    rows = ([(i, "F", f"2013-0{i + 1}-01", 128.0) for i in range(4)]
            + [(10 + i, "M", f"2013-0{i + 1}-01", 128.5) for i in range(3)])
    move, rids, _w = G.verdict(rows, "F")
    assert move == "M" and len(rids) == 3


def test_a_girl_in_boys_fields_at_her_own_level_is_left():
    rows = ([(i, "F", f"2020-09-{10 + i}", 120.0) for i in range(5)]
            + [(100 + i, "M", f"2020-10-{10 + i}", 121.0) for i in range(4)])
    assert G.verdict(rows, "F") is None
    assert G.verdict(rows[:7], "F") is None          # 5 and 2: under MIN_SIDE


def test_the_persons_own_gender_decides_which_side_moves():
    rows = ([(i, "F", f"2010-09-{10 + i}", 150.0) for i in range(3)]
            + [(100 + i, "M", f"2010-10-{10 + i}", 163.0) for i in range(4)])
    assert G.verdict(rows, "F")[0] == "M"            # more boys' rows, still hers are the girls'
    assert G.verdict(rows, None)[0] == "F"           # no gender: the smaller side
    even = rows[:3] + rows[3:6]
    assert G.verdict(even, None) is None             # a tie with no gender: left alone


def test_one_shared_day_is_two_people_with_few_rows():
    """owner, 2026-11-07: Coogan -- a men's 10K on the day of her women's
    6K; the boy's rows 3% from her level, too few for the gap rule."""
    rows = ([(i, "F", f"2013-09-{10 + i}", 158.0) for i in range(6)]
            + [(100, "M", "2010-10-16", 165.0), (101, "M", "2013-09-12", 161.0)])
    move, rids, why = G.verdict(rows, "F")
    assert move == "M" and rids == [100, 101] and "1 day" in why


def test_a_strong_girl_near_the_front_of_a_boys_race_is_left():
    """owner, 2026-11-07: "a strong girl lowk could even with more, it
    happens" -- placing is no evidence; with no shared day, one boys' race
    is hers."""
    rows = ([(i, "F", f"2020-09-{10 + i}", 140.0, 1, 60) for i in range(5)]
            + [(100, "M", "2020-10-10", 141.0, 2, 180)])
    assert G.verdict(rows, "F") is None
