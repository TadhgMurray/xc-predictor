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
