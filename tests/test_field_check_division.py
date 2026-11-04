"""The field check per division (owner, 2026-11-04, Mt. SAC 2026 Race 53):
a division rated above its runners' earlier and later races is pulled down
even when its venue-day, read whole, is not."""
import os
import sys

import numpy as np

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))

import joint_golive as G                                   # noqa: E402


def _venue_day():
    ath, days, race, sub, x = [], [], [], [], []
    a = 0
    for d, bump, n in ((0, 0.0, 60), (1, 0.06, 12)):
        for _i in range(n):
            for k, dd in enumerate((30, 15, 0, -15)):        # earlier x2, HERE, later
                ath.append(a); days.append(dd + 100); race.append(0 if k == 2 else 1 + k)
                sub.append(d if k == 2 else 10 + k); x.append(bump if k == 2 else 0.0)
            a += 1
    return tuple(map(np.array, (ath, days, race, sub, x)))


def test_the_division_the_venue_day_hides_is_pulled_down_alone():
    ath, days, race, sub, x = _venue_day()
    mask = np.ones(x.size, bool)
    rc, *_ = G.fieldCheck(x, ath, days, race, int(race.max()) + 1, mask)
    dc, *_ = G.fieldCheck(x, ath, days, sub, int(sub.max()) + 1, mask)
    extra = G.divisionExtra(dc, sub, rc, race)
    assert rc[0] == 0.0                                   # diluted by the day
    assert np.allclose(extra[sub == 1], 0.06) and not extra[sub == 0].any()
    # never twice: what the venue-day took is not taken again
    assert not G.divisionExtra(dc, sub, np.full_like(rc, 0.06), race).any()


def test_wired_in_and_switchable():
    src = open(os.path.join(_ROOT, "engine", "joint_golive.py")).read()
    assert "fieldCheckDivisionOn() and sub is not None" in src
    assert "XCP_FIELD_CHECK_DIVISION" in src
