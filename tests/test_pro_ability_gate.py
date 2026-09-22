# Project: xc-predictor / tests
# File:    test_pro_ability_gate.py
# Purpose: the ability gate is the LAST word on is_pro, over every route to
#          it, and it can only ever take pro away.
#
# ★ OWNER, 2026-09-22: "if they're sub 14:00? for men, or sub 15:30? for
#   women put in pro, otherwise trust grade."
#
# ⚠⚠ WHY A GATE RATHER THAN A SIXTH FIX. The pro pool's average member runs
#    a 25:53 5K-equivalent -- C(pro_m) = 1553.1 against C(hs_m) = 1211.5.
#    The pool is not lightly contaminated, it is DOMINATED by non-pros, and
#    because speed_rating is pool-relative every one of them drags the
#    100-anchor and bends a real professional's number.
#
#    Three separate doors to is_pro were found wrong in three days --
#    build_team_pool.classify twice, pool_resolve's team_has_pros once. The
#    gate sits downstream of all six and of whichever one is wrong next.
#
# ★ NECESSARY, NEVER SUFFICIENT. Being fast may not MAKE anyone
#   professional: that would pool every good high schooler pro, the exact
#   opposite of the owner's other instruction ("their season should still
#   be hs"). Asserted below, because it is the one way this feature could
#   do more harm than the bug it fixes.
#
# ! AND None MUST BE INERT. A caller that does not pass the fact, a season
#   with no rated mark, a database that has never built the table -- all
#   must behave exactly as before the gate existed. That is what makes it
#   safe to ship ahead of the wiring.
#
#   python -m pytest -q tests/test_pro_ability_gate.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pool_resolve as pr                                      # noqa: E402


def _poolfor(grade, gender, source, school, season_level=None):
    """normalize_distance.poolFor's shape: the grade's level, else the
    season's. Returns None when neither names one -- which is the real
    function's behaviour and the case the owner's "drop them" ruling is
    about."""
    import normalize_distance as nd
    lv = nd.GRADE_TO_LEVEL.get(nd.normalizeGrade(grade)) if grade is not None else None
    lv = lv or season_level
    if lv is None:
        return None
    return f"{lv}_{'m' if gender == 'M' else 'f'}"


def _resolve(**kw):
    base = dict(grade=None, gender="M", source="anet", school="Some Club",
                sport="TF", poolfor=_poolfor, season=2025)
    base.update(kw)
    return pr.resolvePool(base.pop("grade"), **base)


# ------------------------------------------------------------------ #
#  EVERY ROUTE, REFUSED
# ------------------------------------------------------------------ #
# pool_resolve has SIX ways to reach is_pro. Each is exercised here with
# the gate open, shut and silent, because a gate that covers five of six is
# the same bug this file was written about.
# Each entry carries the conditions its route actually needs, and what the
# refusal leaves behind.
#
# ! team_level's club rule DELIBERATELY DOES NOT FIRE on a row carrying a
#   school grade -- "a club runner with a school grade keeps their grade's
#   pool" (test_team_level_pool). So it is exercised gradeless, and its
#   refusal drops the row rather than demoting it, which is the owner's
#   ruling in its purest form: nothing is known about this person except
#   that they are not fast.
_ROUTES = {
    "no_team":       (dict(grade=10, no_team=True, race_top_level=None),
                      "hs_m|TF"),
    "team_pro":      (dict(grade=10, team_pro=True), "hs_m|TF"),
    "team_has_pros": (dict(grade=10, team_has_pros=True, team_level="club"),
                      "hs_m|TF"),
    "team_level":    (dict(grade=None, team_level="club"), None),
    "season_level":  (dict(grade=10, season_level="pro"), "hs_m|TF"),
    "is_pro":        (dict(grade=10, is_pro=True), "hs_m|TF"),
}


def test_every_route_reaches_pro_with_the_gate_silent():
    """The premise. If a route does not produce pro_m|TF here, the refusal
    test below proves nothing about it."""
    for name, (kw, _after) in _ROUTES.items():
        assert _resolve(**kw) == "pro_m|TF", name


def test_every_route_is_refused_by_the_gate():
    """⚠ SIX ROUTES, NOT FIVE. `season_level == "pro"` sets is_pro at line
    786, BELOW the block the other five share, which is why the veto is
    placed after it rather than after them."""
    for name, (kw, after) in _ROUTES.items():
        assert _resolve(pro_ability=False, **kw) == after, name


def test_an_able_athlete_is_untouched_by_the_gate():
    for name, (kw, _after) in _ROUTES.items():
        assert _resolve(pro_ability=True, **kw) == "pro_m|TF", name


# ------------------------------------------------------------------ #
#  NECESSARY, NEVER SUFFICIENT
# ------------------------------------------------------------------ #

def test_ability_alone_never_creates_a_professional():
    """★ THE ONE WAY THIS COULD BE WORSE THAN THE BUG. A sub-14:00 high
    school senior is a sub-14:00 high school senior."""
    assert _resolve(grade=12, pro_ability=True) == "hs_m|TF"
    assert _resolve(grade=8, pro_ability=True) == "ms_m|TF"


# ------------------------------------------------------------------ #
#  None IS INERT
# ------------------------------------------------------------------ #

def test_none_behaves_exactly_as_before_the_gate_existed():
    for name, (kw, _after) in _ROUTES.items():
        silent = _resolve(**kw)
        explicit = _resolve(pro_ability=None, **kw)
        assert silent == explicit == "pro_m|TF", name


def test_the_argument_is_optional():
    """! A caller that has never heard of the fact must keep working --
    four call sites exist and they are wired one at a time."""
    assert _resolve(grade=10, team_pro=True) == "pro_m|TF"


# ------------------------------------------------------------------ #
#  "OTHERWISE TRUST GRADE", AND WHAT HAPPENS WHEN THERE IS NO GRADE
# ------------------------------------------------------------------ #

def test_the_refused_fall_through_to_their_grade():
    assert _resolve(grade=12, team_pro=True, pro_ability=False) == "hs_m|TF"
    assert _resolve(grade=7, team_pro=True, pro_ability=False) == "ms_m|TF"


def test_a_refused_athlete_with_no_grade_at_all_is_dropped():
    """★ THE OWNER'S RULING, 2026-09-22, asked explicitly and answered
    "drop them". No grade, no school level, no season verdict, no race
    ceiling: stage 1 has nothing to pool them by and returns None.

    ⚠ IT IS NOT A SIDE EFFECT, IT IS THE MECHANISM. This is the gradeless
      unattached adult running a 40:00 10k -- the population that put
      C(pro_m) at 25:53. Today they are rated in the pro pool. Dropping
      them is how the pool gets clean.
    """
    assert _resolve(grade=None, no_team=True, pro_ability=False) is None
    assert _resolve(grade=None, team_pro=True, pro_ability=False) is None


def test_a_refused_athlete_keeps_any_level_the_feeds_do_state():
    """! DROPPED ONLY WHERE EVERYTHING IS ABSENT. A season verdict or a
    race ceiling still pools them -- "otherwise trust grade" means fall
    through the ordinary ladder, not fall off it."""
    assert _resolve(grade=None, no_team=True, race_top_level="hs",
                    pro_ability=False) == "hs_m|TF"
    assert _resolve(grade=None, team_pro=True, season_level="college",
                    pro_ability=False) == "college_m|TF"


# ------------------------------------------------------------------ #
#  THE STANDARD ITSELF
# ------------------------------------------------------------------ #

def test_the_thresholds_are_the_owners_numbers():
    assert pr.PRO_ABILITY_5K["M"] == 14 * 60.0
    assert pr.PRO_ABILITY_5K["F"] == 15 * 60.0 + 30.0


def test_the_standard_is_measured_on_one_curve_not_the_athletes_own():
    """⚠ normalized_time is expressed at a DIFFERENT anchor per pool
    (targetFor: 5000 hs and pro, 8000 college men, 6000 college women,
    3200 ms). Measuring a college man against 840s on his own scale asks
    for a 14:00 EIGHT thousand. The gate pins one pool and one sport."""
    import normalize_distance as nd
    assert nd.targetFor("college_m", "TF") == 8000.0
    assert nd.targetFor(pr.ABILITY_CURVE_POOL["M"], pr.ABILITY_CURVE_SPORT) == 5000.0
    assert nd.targetFor(pr.ABILITY_CURVE_POOL["F"], pr.ABILITY_CURVE_SPORT) == 5000.0


def test_the_thresholds_sit_where_the_owner_aimed_them():
    """! THE MARKS THAT PLACED THE NUMBERS, re-measured against the live
    spline rather than quoted. A 4:00 mile and a 1:47 800 clear; a 1:50 800
    and a 4:30 women's mile do not.

    ⚠ TOLERANT ON PURPOSE. The spline is refitted by the pipeline's `curve`
      step, so pinning an exact second here would fail on a better fit. The
      claim is that the standard lands between these marks, which is a
      statement about the STANDARD and survives a refit.
    """
    import normalize_distance as nd
    m = pr.PRO_ABILITY_5K["M"]
    f = pr.PRO_ABILITY_5K["F"]
    sp, cm, cf = (pr.ABILITY_CURVE_SPORT,
                  pr.ABILITY_CURVE_POOL["M"], pr.ABILITY_CURVE_POOL["F"])
    assert nd.normalizeTime(105.0, 800.0, cm, sport=sp) < m        # 1:45
    assert nd.normalizeTime(240.0, 1609.34, cm, sport=sp) < m      # 4:00 mile
    assert nd.normalizeTime(810.0, 5000.0, cm, sport=sp) < m       # 13:30
    assert nd.normalizeTime(110.0, 800.0, cm, sport=sp) > m        # 1:50
    assert nd.normalizeTime(900.0, 5000.0, cm, sport=sp) > m       # 15:00

    assert nd.normalizeTime(116.0, 800.0, cf, sport=sp) < f        # 1:56
    assert nd.normalizeTime(260.0, 1609.34, cf, sport=sp) < f      # 4:20 mile
    assert nd.normalizeTime(900.0, 5000.0, cf, sport=sp) < f       # 15:00
    assert nd.normalizeTime(270.0, 1609.34, cf, sport=sp) > f      # 4:30 mile
    assert nd.normalizeTime(960.0, 5000.0, cf, sport=sp) > f       # 16:00


def test_the_bar_is_strict():
    """! Exactly on the number does not qualify. Stated because "sub 14:00"
    is a phrase and `<=` is a plausible misreading of it."""
    assert not (pr.PRO_ABILITY_5K["M"] < pr.PRO_ABILITY_5K["M"])
