"""own_race_shift: a race stored at the wrong distance is caught by its own
runners' other races, a hard course is not condemned, and the cut is
measured from the spread rather than chosen."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import own_race_shift as S  # noqa: E402


def season(seed=3, n_people=3000, n_races=400, wrong=None, hard=None):
    """People race 4 times over ~40 days at random races; each race has a
    course+day effect (sd 4.7%), each run a personal wobble (sd 2.5%).
    `wrong`: race id whose times are really a 3200 stored as 5000 (x0.62).
    `hard`: race id on a brutal course (x1.35, genuinely slow)."""
    rng = np.random.default_rng(seed)
    effect = rng.normal(0, 0.047, n_races)
    if wrong is not None:
        effect[wrong] = math.log(0.62)
    if hard is not None:
        effect[hard] = math.log(1.35)
    race_day = rng.integers(0, 60, n_races)
    pk, day, race, lnt = [], [], [], []
    ability = rng.normal(math.log(1100), 0.12, n_people)
    for p in range(n_people):
        for r in rng.choice(n_races, 4, replace=False):
            pk.append(p); day.append(race_day[r]); race.append(r)
            lnt.append(ability[p] + effect[r] + rng.normal(0, 0.025))
    return (np.array(pk), np.array(day), np.array(race), np.array(lnt))


def run(**kw):
    pk, day, race, lnt = season(**kw)
    s, c = S.ownOtherMeans(pk, day, race, lnt, window=30)
    has = c > 0
    gap = lnt[has] - s[has] / c[has]
    return S.judge(S.raceMedians(race[has], gap))


def test_a_wrong_distance_race_is_condemned():
    stats, fast, slow = run(wrong=7, hard=11)
    assert [r for r, *_ in fast] == [7]
    assert 11 in [r for r, *_ in slow]          # reported, not condemned


def test_nothing_is_condemned_in_an_honest_season():
    stats, fast, slow = run()
    assert fast == []
    assert 0.02 < stats["s_between"] < 0.07       # measured, near the planted 4.7%


def test_implied_distance_snaps_to_a_convention():
    assert S.impliedDistance(5000, math.log(0.62)) == 3200
    assert S.impliedDistance(5000, 0.0) == 5000


def test_a_field_twice_as_slow_is_condemned_but_a_hard_course_is_not():
    # 11 is brutal (x1.35): reported only. 13 is a time in the wrong unit (x3).
    pk, day, race, lnt = season(hard=11)
    lnt = np.where(race == 13, lnt + math.log(3.0), lnt)
    s, c = S.ownOtherMeans(pk, day, race, lnt, window=30)
    has = c > 0
    stats, fast, slow = S.judge(S.raceMedians(race[has], lnt[has] - s[has] / c[has]))
    assert 13 in [r for r, *_ in fast]
    assert 11 not in [r for r, *_ in fast] and 11 in [r for r, *_ in slow]


def test_the_year_window_judges_a_race_the_month_cannot():
    # the season as usual, then one extra race 200 days later whose runners
    # ran nothing else near it: a 3200 stored as a 5000
    pk, day, race, lnt = season()
    rng = np.random.default_rng(9)
    people = rng.choice(np.unique(pk), 40, replace=False)
    base = {p: lnt[pk == p].mean() for p in people}
    extra_race = int(race.max()) + 1
    pk2 = np.concatenate([pk, people])
    day2 = np.concatenate([day, np.full(people.size, 260)])
    race2 = np.concatenate([race, np.full(people.size, extra_race)])
    lnt2 = np.concatenate([lnt, [base[p] + math.log(0.62) + rng.normal(0, 0.02) for p in people]])
    s, c = S.ownOtherMeans(pk2, day2, race2, lnt2, window=30)
    assert c[race2 == extra_race].sum() == 0, "no neighbour inside a month"
    s2, c2 = S.ownOtherMeans(pk2, day2, race2, lnt2, window=S.WIDE_WINDOW)
    has = c2 > 0
    stats, fast, slow = S.judge(S.raceMedians(race2[has], lnt2[has] - s2[has] / c2[has]))
    assert extra_race in [r for r, *_ in fast]
