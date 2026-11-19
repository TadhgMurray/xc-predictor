"""engine/joint_golive.writeLive (2026-11-10): the two sports' result
ratings saved side by side when XCP_GOLIVE_SAVE_MIN_GB is free, one after
the other when it is not (or XCP_GOLIVE_SAVE_PARALLEL=0); both saved
either way."""
import os
import sys
import threading
import time
import types

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

G = pytest.importorskip("joint_golive")


@pytest.fixture
def stubbed(monkeypatch):
    import speed_ratings_db as D
    import normalize_distance as N
    log = []
    live_now = [0, 0]
    lock = threading.Lock()

    def save(name, arrays, mode="rebuild", before_swap=None):
        with lock:
            live_now[0] += 1
            live_now[1] = max(live_now[1], live_now[0])
        time.sleep(0.3)
        with lock:
            live_now[0] -= 1
        if before_swap is not None:
            before_swap()                      # the swap waits here
        log.append(name)

    monkeypatch.setattr(D, "saveResultSpeedRatings", save)
    monkeypatch.setattr(D, "saveCourseDifficulties", lambda *a, **k: None)
    monkeypatch.setattr(D, "saveAthleteRatings", lambda *a, **k: None)
    pg = types.ModuleType("pair_golive")
    pg.backupSpeedRatings = lambda conn: None
    pg.restoreSql = lambda: ""
    monkeypatch.setitem(sys.modules, "pair_golive", pg)

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import database
    monkeypatch.setattr(database, "getConn", lambda: _C())
    for fn in ("writeGoliveStamp", "writeDistOffsets", "writeEngineScale", "writeSuspectDays",
               "writeCourseGuard", "writeSportGain", "writeRaceDays"):
        if hasattr(G, fn):
            monkeypatch.setattr(G, fn, lambda *a, **k: None)
    monkeypatch.setattr(N, "writeRatedRecords", lambda *a, **k: "pool")
    live = {"diffs": [], "athletes": [],
            "per_sport": {"XC": (np.arange(3),), "TF": (np.arange(9),)}}
    return live, log, live_now


def test_side_by_side_with_room(stubbed, monkeypatch):
    live, log, live_now = stubbed
    monkeypatch.setattr(G, "_memAvailableGb", lambda: 100.0)
    G.writeLive(live)
    assert sorted(log) == ["TF", "XC"] and live_now[1] == 2


def test_one_at_a_time_without_room_or_when_off(stubbed, monkeypatch):
    live, log, live_now = stubbed
    monkeypatch.setattr(G, "_memAvailableGb", lambda: 10.0)
    G.writeLive(live)
    assert sorted(log) == ["TF", "XC"] and live_now[1] == 1
    log.clear(); live_now[1] = 0
    monkeypatch.setattr(G, "_memAvailableGb", lambda: 100.0)
    monkeypatch.setenv("XCP_GOLIVE_SAVE_PARALLEL", "0")
    G.writeLive(live)
    assert log == ["XC", "TF"] and live_now[1] == 1


def test_course_and_athletes_beside_the_rebuilds_gated_at_the_swap(stubbed, monkeypatch):
    """2026-11-19: course_difficulties / athlete_ratings run on their own
    thread while the results rebuild; every results swap waits for them,
    and their failure means no results swap at all."""
    import speed_ratings_db as D
    live, log, _ = stubbed
    monkeypatch.setattr(G, "_memAvailableGb", lambda: 100.0)

    def athletes(*a, **k):
        time.sleep(0.6)                        # longer than a rebuild's 0.3 s
        log.append("athletes")
    monkeypatch.setattr(D, "saveAthleteRatings", athletes)
    t0 = time.time()
    G.writeLive(live)
    assert log[0] == "athletes" and sorted(log[1:]) == ["TF", "XC"]
    assert time.time() - t0 < 0.85             # side by side: ~0.6, not 0.9

    log.clear()

    def broken(*a, **k):
        raise RuntimeError("athlete_ratings failed")
    monkeypatch.setattr(D, "saveAthleteRatings", broken)
    with pytest.raises(RuntimeError, match="athlete_ratings failed"):
        G.writeLive(live)
    assert log == []                           # no table was swapped

    monkeypatch.setenv("XCP_GOLIVE_PRESAVE_PARALLEL", "0")
    monkeypatch.setattr(D, "saveAthleteRatings", athletes)
    t0 = time.time()
    G.writeLive(live)
    assert log[0] == "athletes" and time.time() - t0 >= 0.9   # in line, as before
