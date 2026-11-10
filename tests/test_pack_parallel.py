"""engine/speed_ratings._packParallel (2026-11-10): the two sports packed in
two forked processes give the SAME pack, byte for byte, as the one loop over
the chained stream -- codes, order, census and all."""
import os
import random
import sys
from datetime import date, timedelta

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
S = pytest.importorskip("speed_ratings")

TODAY = date(2026, 11, 10)


def _rows(sport, n, rnd):
    out = []
    for i in range(n):
        d = TODAY - timedelta(days=rnd.randint(-3, 2000))      # a few future dates
        team = rnd.choice([None, 0, 11, 12, 13, 14])            # shared across sports
        slug = rnd.choice([None, "", "oregon", "stanford"])
        venue = rnd.choice([None, "v1", "v2", "v3", f"{sport}-only"])
        out.append((
            (1 if sport == "XC" else 2) * 10_000_000 + i,       # _RID
            rnd.randint(1, 60),                                 # _PID (shared people)
            rnd.uniform(800, 1400),                             # _NORM
            rnd.choice([9, 10, 11, 12, None]),                  # _GRADE
            rnd.choice(["anet", "tfrrs"]),                      # _SRC
            rnd.choice(["Lincoln HS", "unattached", "", "Oregon"]),  # _SCHOOL
            d.isoformat(),                                      # _DATE
            sport,                                              # _SPORT
            venue,                                              # _VENUE
            rnd.choice(["m", "f", "x"]),                        # _GENDER
            rnd.choice([None, 5000.0, 1600.0, 0]),              # _DIST
            rnd.choice([None, 0, 1, 2]),                        # _MEETCLASS
            rnd.choice([None, 900.0]),                          # _TIME
            team,                                               # _TEAM
            slug,                                               # _SLUG
            rnd.choice([None, 5, 6]),                           # _MEET
            rnd.choice([None, 7, 8]),                           # _DIV
        ))
    return out


@pytest.fixture
def fake(monkeypatch, tmp_path):
    rnd = random.Random(20261110)
    data = {"XC": _rows("XC", 5000, rnd), "TF": _rows("TF", 7000, rnd)}

    def stream(sport, *a, **k):
        rows = data[sport]
        for i in range(0, len(rows), 997):
            yield rows[i:i + 997]

    def pool(grade, gender, src, school, sport, merge=False, **k):
        if gender == "x":
            return None                                         # unknown_pool
        return f"{'hs' if grade else 'college'}_{gender}" + ("" if sport == "XC" else "_tf")

    monkeypatch.setattr(S, "streamResults", stream)
    monkeypatch.setattr(S, "poolOf", pool)
    monkeypatch.setattr(S, "rowTeamFacts", lambda *a, **k: (None, False, False, False, None))
    monkeypatch.setattr(S, "rescaleToPool", lambda nt, *a, **k: (nt, None))
    monkeypatch.setattr(S, "poolBand", lambda p: (850.0, 1350.0))
    for fn in ("loadClubPros", "loadClubMajority", "loadProTeams", "loadUnattachedRaceLevel",
               "loadAnetLevels", "loadSeasonLevels", "loadProSeasons", "loadCollegeFirst",
               "loadGradeUntrusted", "loadUpperclassFirst"):
        monkeypatch.setattr(S, fn, lambda: None)
    monkeypatch.setattr(S, "_CACHE_DIR", str(tmp_path))
    return data


def _same(a, b):
    assert a.keys() == b.keys()
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, np.ndarray):
            assert x.dtype == y.dtype and np.array_equal(x, y), k
        else:
            assert x == y, k


def test_parallel_pack_is_the_one_loop_byte_for_byte(fake):
    seq = S.packResults(S.chain(*(S.streamResults(s) for s in ("XC", "TF"))), TODAY)
    state = S._packParallel(["XC", "TF"], TODAY, False)
    assert state is not None
    par = S.packResults(None, TODAY, _state=state)
    _same(seq, par)
    # the shapes the merge has to get right were all there
    assert seq["census"]["unknown_pool"] > 0 and seq["census"]["bad_or_future_date"] > 0
    assert (seq["course"] == -1).any() and (seq["team"] == -1).any()
    assert len(set(seq["team_keys"])) == len(seq["team_keys"])


def test_off_switch_and_one_sport_use_the_one_loop(fake, monkeypatch):
    assert S._packParallel(["XC"], TODAY, False) is None
    monkeypatch.setenv("XCP_PACK_PARALLEL", "0")
    assert S._packParallel(["XC", "TF"], TODAY, False) is None


def test_a_failed_child_fails_the_pack(fake, monkeypatch):
    def boom(sport, *a, **k):
        if sport == "TF":
            raise RuntimeError("TF stream broke")
        yield from ()
    monkeypatch.setattr(S, "streamResults", boom)
    with pytest.raises(RuntimeError, match="TF pack process failed"):
        S._packParallel(["XC", "TF"], TODAY, False)
