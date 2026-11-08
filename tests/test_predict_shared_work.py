"""The prediction's work shared between the page's requests (2026-11-07):
the same answers, made once.

    python -m pytest -q tests/test_predict_shared_work.py
    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres" \\
        python -m pytest -q tests/test_predict_shared_work.py   # + Postgres

1. /api/predict/field?pick=predicted (byPrediction) then /api/predict/team
   (_servedTimes), possibly on two workers: inside shareWork the second
   reads no rating rows and runs no model for a runner the first did, and
   serves exactly what it serves without sharing.
2. /api/predict/individual: predictIndividuals(ids) is
   [predictIndividual(id) for id in ids], with one target, one history
   check, one ratings read and one model-history read for all of them.
3. Per request: _exactField and _meetState read once; dropStopped's squads
   read once per (gender, level), each school's as it reads alone.
"""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import pytest                                                   # noqa: E402

import predict as P                                             # noqa: E402
import sharedcache                                              # noqa: E402


SPEC = {"sport": "XC", "is_xc": True, "date": "2026-11-14", "meet_id": 77,
        "source": "anet", "distance_meters": 5000.0, "course_name": "Somewhere"}

# rated history (date, rating, pool, sport, metres, meet), newest first;
# 6 and 7 have no rating before the race (the model's runners); 2 races in
# another pool, so a field's modal pool is not their own
ROWS = {1: [("2026-10-30", 120.0, "hs_m", "XC", None, None),
            ("2026-10-10", 118.0, "hs_m", "XC", None, None)],
        2: [("2026-10-25", 105.0, "college_m", "XC", None, None)],
        3: [("2026-10-31", 112.0, "hs_m", "XC", None, None),
            ("2026-09-20", 110.0, "hs_m", "XC", None, None)],
        4: [("2026-10-02", 99.0, "hs_m", "XC", None, None)],
        5: [("2026-10-17", 130.0, "hs_m", "XC", None, None)]}
UNRATED = (6, 7)


class Calls:
    def __init__(self):
        self.rows, self.model, self.hist, self.spec = [], [], [], 0


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Every read stubbed, recording what it was asked for."""
    calls = Calls()
    monkeypatch.delenv("XCP_PREDICT_BASIS", raising=False)
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(P, "_MODEL_STAMP", ("model.pt", 1.0, 2))
    monkeypatch.setattr(P, "modelStatus", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(P, "_outlierFilter", lambda cur, sport: "")
    # a rating is a time on the target's clock (the conversions are not
    # what is under test; _formRating, _formShape and the guard are real)
    monkeypatch.setattr(P, "_ratingToSeconds",
                        lambda rating, pool, spec, season:
                        1000.0 * 100.0 / rating * (1.03 if pool.startswith("college") else 1.0))

    def target_spec(cur, target):
        calls.spec += 1
        return dict(SPEC)

    def rows_read(cur, ids, lo, hi):
        calls.rows.append(sorted(ids))
        return {p: list(ROWS[p]) for p in ids if p in ROWS}

    def history_rows(cur, ids):
        calls.hist.append(sorted(ids))
        return {p: [{"date": "2026-10-01", "pid": p}] for p in ids}

    class Hist:
        wait_s = merge_s = 0.0

        def __init__(self, ids):
            self.ids, self.kept = list(ids), None

        def keep(self, wanted):
            self.kept = list(wanted)

        def finish(self, cur):
            return history_rows(cur, self.kept if self.kept is not None else self.ids)

    def predict_times(cur, ids, target, spec=None, history=None):
        calls.model.append(list(ids))
        h = history if history is not None else history_rows(cur, ids)
        # deterministic per runner, as the network is (eval mode)
        return [{"seconds": 1200.0 + p, "lo": 1150.0 + p, "hi": 1250.0 + p,
                 "sigma_pct": 3.0 + (p == 7) * 20.0, "is_race_time": True,
                 "time_pool": "hs_m", "n_races": len(h.get(p, []))}
                for p in ids]

    monkeypatch.setattr(P, "_targetSpec", target_spec)
    monkeypatch.setattr(P, "_ratingRowsRead", rows_read)
    monkeypatch.setattr(P, "_historyRows", history_rows)
    monkeypatch.setattr(P, "_historyStart", lambda cur, ids: Hist(ids))
    monkeypatch.setattr(P, "_predictTimes", predict_times)
    P._LOCAL.clear()
    yield calls
    P._LOCAL.clear()


SQUADS = {"A": [{"person_id": i} for i in (1, 3, 4, 6)],
          "B": [{"person_id": i} for i in (2, 5, 7)]}
TARGET = {"mode": "rerun", "meet_id": 77, "sport": "XC", "source": "anet",
          "date": "2026-11-14", "weather": "none"}


def _fieldThenTeam():
    """Without sharing: byPrediction's squads, then the team request's
    served times for the lineup it picked; (squads, preds)."""
    squads = {k: [dict(e) for e in v] for k, v in SQUADS.items()}
    squads, _note = P.byPrediction(None, squads, {"A": 3, "B": 3}, dict(TARGET))
    ids = [e["person_id"] for sch in sorted(squads) for e in squads[sch][:3]]
    return squads, P._servedTimes(None, ids, dict(TARGET))


@pytest.mark.parametrize("basis", ["rating", "guard", "model"])
@pytest.mark.parametrize("new_worker", [False, True])
def test_the_team_request_reuses_the_field_requests_work(world, monkeypatch,
                                                         basis, new_worker):
    monkeypatch.setenv("XCP_PREDICT_BASIS", basis)
    plain = _fieldThenTeam()
    plain_calls = (list(world.rows), list(world.model))
    P._LOCAL.clear()
    world.rows.clear(), world.model.clear()

    with P.shareWork():
        squads = {k: [dict(e) for e in v] for k, v in SQUADS.items()}
        got_squads, _ = P.byPrediction(None, squads, {"A": 3, "B": 3}, dict(TARGET))
    first = (list(world.rows), list(world.model))
    world.rows.clear(), world.model.clear()
    if new_worker:
        P._LOCAL.clear()
    with P.shareWork():
        ids = [e["person_id"] for sch in sorted(got_squads)
               for e in got_squads[sch][:3]]
        got_preds = P._servedTimes(None, ids, dict(TARGET))

    # the same squads and the same served times as without sharing
    assert got_squads == plain[0]
    assert got_preds == plain[1]
    # and the second request read no rating rows and ran no model: every
    # runner in it was read (and, where needed, modelled) by the first
    assert world.rows == [] and world.model == []
    assert first[0] and plain_calls[0]


def test_only_the_runners_not_held_are_read(world):
    with P.shareWork():
        a = P._ratingRows(None, [1, 2, 3], None, "2026-11-14")
    P._LOCAL.clear()
    with P.shareWork():
        b = P._ratingRows(None, [2, 3, 4, 9], None, "2026-11-14")
        # another cut is another key
        c = P._ratingRows(None, [2], None, "2026-11-01")
    assert world.rows == [[1, 2, 3], [4, 9], [2]]
    assert a == {p: ROWS[p] for p in (1, 2, 3)}
    assert b == {p: ROWS[p] for p in (2, 3, 4)}       # 9 has none: absent
    assert c == {2: ROWS[2]}


def test_outside_sharework_nothing_is_kept(world, tmp_path):
    P._ratingRows(None, [1, 2], None, "2026-11-14")
    P._ratingRows(None, [1, 2], None, "2026-11-14")
    assert world.rows == [[1, 2], [1, 2]]
    assert not os.path.exists(str(tmp_path / "shared")) or \
        not os.listdir(str(tmp_path / "shared"))


def test_a_held_entry_is_never_handed_out_itself(world):
    with P.shareWork():
        one = P._modelTimes(None, [6], TARGET, dict(SPEC))
        one[0]["seconds"] = None                    # the guard edits in place
        two = P._modelTimes(None, [6], TARGET, dict(SPEC))
    assert two[0]["seconds"] == 1206.0 and world.model == [[6]]


def test_every_model_input_is_in_the_key(world):
    with P.shareWork():
        base = P._modelBucket(TARGET, SPEC)
        assert base is not None
        for change in ({"weather": "normal"}, {"mode": "rerun_exact"},
                       {"history_before": "2026-06-01"}):
            assert P._modelBucket(dict(TARGET, **change), SPEC) != base
        for change in ({"date": "2026-11-15"}, {"distance_meters": 8000.0},
                       {"course_name": "Elsewhere"}, {"source": "tfrrs"},
                       {"meet_id": 78}):
            assert P._modelBucket(TARGET, dict(SPEC, **change)) != base
    assert P._modelBucket(TARGET, SPEC) is None      # not inside shareWork


def test_the_spec_key_reads_every_target_field_the_spec_does():
    import inspect
    import re
    src = inspect.getsource(P._targetSpec) + inspect.getsource(P._applyCourse)
    read = set(re.findall(r'target(?:\.get\("|\[")(\w+)', src))
    assert read <= set(P._SPEC_INPUTS), read - set(P._SPEC_INPUTS)


# ---------------------------------------------------------------- 2 --

class HistCur:
    """_athleteHistory's and _withRatedRaces' reads over ROWS, plus 8 (rated
    in the corpus, no rating row before the race) and 6/7 (history only)."""
    HAS = set(ROWS) | {8, 6, 7}

    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "unnest" in sql:
            self._rows = [{"person_id": p} for p in params[0] if p in self.HAS]
        else:
            pid = params[0]
            self._rows = ([{"sport": "XC", "date": "2026-10-01",
                            "normalized_time": 1000.0, "speed_rating": 100.0}]
                          if pid in self.HAS else [])

    def fetchall(self):
        return self._rows


@pytest.mark.parametrize("basis", ["rating", "guard", "blend", "model", "race"])
@pytest.mark.parametrize("share", [False, True])
def test_individuals_are_each_athlete_alone(world, monkeypatch, basis, share):
    monkeypatch.setenv("XCP_PREDICT_BASIS", basis)
    import race_model
    monkeypatch.setattr(race_model, "predictRace",
                        lambda cur, ids, spec, cut, rated, which: (
                            {p: {"seconds": rated[p]["seconds"] - 1.0, "race_basis": len(ids)}
                             for p in ids if p in rated} if len(ids) >= 3 else {}))
    ids = [3, 1, 2, 99, 6, 7, 8, 1, 5]               # 99: never raced; 1 twice
    cur = HistCur()
    one = [P.predictIndividual(cur, p, dict(TARGET)) for p in ids]
    one_calls = len(world.rows), len(world.model)
    P._LOCAL.clear()
    world.rows.clear(), world.model.clear(), world.hist.clear()
    cur = HistCur()
    if share:
        with P.shareWork():
            many = P.predictIndividuals(cur, ids, dict(TARGET))
    else:
        many = P.predictIndividuals(cur, ids, dict(TARGET))
    assert many == one                               # same dicts, same order
    assert many[3] == {"available": False,
                       "reason": "No rated races found for this athlete."}
    # one history check, one ratings read, one model-history read; the
    # network one runner at a time, as the one-athlete calls ran it
    assert sum("unnest" in s for s in cur.sql) == 1 and len(cur.sql) == 1
    reads = 0 if basis == "model" else 1           # "model" reads no ratings
    assert len(world.rows) == reads and one_calls[0] == reads * (len(ids) - 1)
    assert all(len(m) == 1 for m in world.model)
    assert len(world.hist) <= 1


def test_a_field_is_not_each_athlete_alone(world):
    """Why the individuals do not go through _servedTimes(ids): the race's
    pool is the field's (_stampRaceRatings), so 2 (college) read alone and
    read among hs runners differ."""
    alone = P._servedTimes(None, [2], dict(TARGET))[0]
    field = P._servedTimes(None, [1, 2, 3], dict(TARGET))[1]
    each = P._servedEach(None, [1, 2, 3], dict(TARGET))[1]
    assert field["race_pool"] == "hs_m" and alone["race_pool"] == "college_m"
    assert each == alone


def test_a_failed_ratings_read_falls_back_to_one_by_one(world, monkeypatch):
    seen = []

    def boom(cur, ids, spec, cut):
        seen.append(list(ids))
        raise RuntimeError("db down")
    monkeypatch.setattr(P, "_ratingTimes", boom)
    out = P.predictIndividuals(HistCur(), [1, 2], dict(TARGET))
    # the batch failed once, then each athlete's own call took its fallback
    assert seen == [[1, 2], [1], [2]]
    assert [o["seconds"] for o in out] == [1201.0, 1202.0]


@pytest.fixture
def pg():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    for t in ("results", "results_tf"):
        cur.execute(f"CREATE TEMP TABLE {t} (person_id integer, date text,"
                    " normalized_time real, speed_rating real)")
    cur.execute("""INSERT INTO results VALUES (1, '2026-10-01', 900, 101),
                   (2, '2026-10-01', 900, NULL), (4, '2026-09-01', 950, 99),
                   (NULL, '2026-09-01', 950, 99)""")
    cur.execute("""INSERT INTO results_tf VALUES (3, '2026-04-01', 600, 88),
                   (2, '2026-04-01', 600, NULL), (4, '2026-04-02', 610, 90)""")
    yield cur
    conn.rollback()
    conn.close()


def test_the_history_check_is_athlete_history_on_postgres(pg):
    ids = [1, 2, 3, 4, 5]
    have = P._withRatedRaces(pg, ids)
    assert have == {p for p in ids if P._athleteHistory(pg, p)} == {1, 3, 4}


# ---------------------------------------------------------------- 3 --

def test_the_field_is_read_once_per_request(monkeypatch):
    reads = []

    def read(cur, meet_id, div_id, sport, source=None, event_id=None,
             started_only=False):
        reads.append((meet_id, div_id, started_only))
        return [{"person_id": 1, "school": "A", "name": "X"}]
    monkeypatch.setattr(P, "_exactFieldRead", read)
    with P.shareWork():
        a = P._exactField(None, 5, 2, "XC", source="anet")
        a[0]["school_state"] = "MA"               # a caller stamps its copy
        b = P._exactField(None, 5, 2, "XC", source="anet")
        P._exactField(None, 5, 2, "XC", source="anet", started_only=True)
    P._exactField(None, 5, 2, "XC", source="anet")
    assert b == [{"person_id": 1, "school": "A", "name": "X"}]
    assert reads == [(5, 2, False), (5, 2, True), (5, 2, False)]


def test_the_meet_state_is_read_once_and_a_none_again(monkeypatch):
    reads = []
    answers = {1: "MA", 2: None}

    def read(cur, meet_id, sport, source=None):
        reads.append(meet_id)
        return answers[meet_id]
    monkeypatch.setattr(P, "_meetStateRead", read)
    with P.shareWork():
        assert [P._meetState(None, m, "XC", "anet") for m in (1, 1, 2, 2)] == \
            ["MA", "MA", None, None]
    assert reads == [1, 2, 2]


def _dropWorld(monkeypatch, fail_batch):
    calls = []
    squads = {"A": [{"person_id": p, "rating": 100 - p} for p in (11, 12, 13, 14, 15, 16)],
              "B": [{"person_id": p, "rating": 100 - p} for p in (21, 22, 23, 24, 25, 26)],
              "C": [{"person_id": p, "rating": 100 - p} for p in (31, 32, 33, 34, 35, 36)]}

    def current(cur, schools, sport, year, gender=None, levels=None,
                isolated=False, **_kw):
        calls.append((tuple(schools), gender, tuple(sorted(levels or ())), isolated))
        if fail_batch and len(schools) > 1:
            raise RuntimeError("statement timeout")
        return {s: [dict(r, pool=f"{next(iter(levels))}_{gender.lower()}")
                    for r in squads[s]] for s in schools}
    monkeypatch.setattr(P, "_currentSquads", current)
    monkeypatch.setattr(P, "_stoppedRacing", lambda cur, by, sport, yr: {12: 3, 22: 3, 33: 3})
    monkeypatch.setattr(P, "isTeam", lambda s: True)
    monkeypatch.setattr(P, "_rollback", lambda cur: None)
    entries = ([{"person_id": p, "school": "A", "pool": "hs_m", "rating": 100 - p}
                for p in (11, 12, 13, 14, 15)]
               + [{"person_id": p, "school": "B", "pool": "hs_m", "rating": 100 - p}
                  for p in (21, 22, 23, 24, 25)]
               + [{"person_id": p, "school": "C", "pool": "hs_f", "rating": 100 - p}
                  for p in (31, 32, 33, 34, 35)])
    return P.dropStopped(None, copy.deepcopy(entries), "XC", 2026), calls


def test_the_refill_squads_are_read_per_gender_and_level(monkeypatch):
    batched, calls = _dropWorld(monkeypatch, fail_batch=False)
    one_by_one, calls1 = _dropWorld(monkeypatch, fail_batch=True)
    assert batched == one_by_one
    assert calls == [(("A", "B"), "M", ("hs",), True), (("C",), "F", ("hs",), False)]
    assert calls1[1:] == [(("A",), "M", ("hs",), False), (("B",), "M", ("hs",), False),
                          (("C",), "F", ("hs",), False)]
    # each team keeps its number: 12, 22 and 33 replaced by their next best
    assert sorted(e["person_id"] for e in batched if e.get("refill")) == [16, 26, 36]


def test_shared_buckets(tmp_path):
    d = str(tmp_path / "c")
    t = [1000.0]
    now = lambda: t[0]                                          # noqa: E731
    sharedcache.putMany(("b", 1), {1: "one", 2: "two"}, 60, now=now, path=d)
    t[0] += 30
    sharedcache.putMany(("b", 1), {3: "three"}, 60, now=now, path=d)
    assert sharedcache.peekMany(("b", 1), [1, 2, 3, 4], 60, now=now, path=d) == {
        1: ("one", 1000.0), 2: ("two", 1000.0), 3: ("three", 1030.0)}
    t[0] += 40                                                  # 1 and 2 expire
    assert sharedcache.peekMany(("b", 1), [1, 2, 3], 60, now=now, path=d) == {
        3: ("three", 1030.0)}
    assert sharedcache.peekMany(("b", 2), [3], 60, now=now, path=d) == {}
    os.chmod(d, 0o777)                                          # not ours alone
    assert sharedcache.peekMany(("b", 1), [3], 60, now=now, path=d) == {}


def test_shared_bucket_cap_and_sweep(tmp_path, monkeypatch):
    d = str(tmp_path / "c")
    t = [5000.0]
    now = lambda: t[0]                                          # noqa: E731
    monkeypatch.setattr(sharedcache, "_last_sweep", [0.0])
    sharedcache.putMany(("cap",), {1: "a", 2: "b"}, 600, now=now, path=d, max_entries=3)
    t[0] += 1
    sharedcache.putMany(("cap",), {3: "c", 1: "a2"}, 600, now=now, path=d, max_entries=3)
    t[0] += 1
    sharedcache.putMany(("cap",), {4: "d"}, 600, now=now, path=d, max_entries=3)
    # 2 was the oldest; 1 was refreshed so it stays
    got = sharedcache.peekMany(("cap",), [1, 2, 3, 4], 600, now=now, path=d)
    assert sorted(got) == [1, 3, 4] and got[1][0] == "a2"
    # a bucket nobody touches for SWEEP_AGE is removed by a later put
    old = [f for f in os.listdir(d) if f.startswith("many-")]
    t[0] += sharedcache.SWEEP_AGE + sharedcache.SWEEP_EVERY + 10
    for f in old:
        os.utime(os.path.join(d, f), (5000.0, 5000.0))
    sharedcache.putMany(("other",), {9: "z"}, 600, now=now, path=d)
    left = [f for f in os.listdir(d) if f.startswith("many-")]
    assert not set(old) & set(left)
    assert sharedcache.peekMany(("other",), [9], 600, now=now, path=d) == {9: ("z", t[0])}


def test_a_held_lock_is_waited_for_only_so_long(tmp_path, monkeypatch):
    """! 2026-11-07: a worker waited on another's compute without limit,
    holding its database connection; now it computes for itself."""
    import fcntl
    d = str(tmp_path / "c")
    sharedcache._safeDir(d)
    monkeypatch.setattr(sharedcache, "LOCK_WAIT", 0.3)
    base = os.path.join(d, sharedcache._name("k"))
    with open(base + ".lock", "a+") as other:
        fcntl.flock(other, fcntl.LOCK_EX)              # another worker computing
        import time as _t
        t0 = _t.monotonic()
        v, _at = sharedcache.get("k", lambda: 42, ttl=60, path=d)
        assert v == 42 and _t.monotonic() - t0 < 2
        assert not os.path.exists(base + ".pkl")       # the lock holder shares
        sharedcache.putMany(("b",), {1: 1}, 60, path=d)  # a held bucket is skipped, fast
    v, _at = sharedcache.get("k", lambda: 43, ttl=60, path=d)
    assert v == 43 and os.path.exists(base + ".pkl")


def test_a_full_pool_is_waited_for_not_failed(monkeypatch):
    import threading
    import time as _t
    import psycopg2.pool
    import database as D

    class C:
        closed = 0

        def cursor(self):
            class K:
                def __enter__(s): return s
                def __exit__(s, *a): pass
                def execute(s, *a): pass
            return K()

        def rollback(self):
            pass

    class P:
        used = 0

        def getconn(s):
            if s.used >= 2:
                raise psycopg2.pool.PoolError("exhausted")
            s.used += 1
            return C()

        def putconn(s, c, close=False):
            s.used -= 1

    monkeypatch.setattr(D, "_pool", P())
    monkeypatch.setattr(D, "_slots", threading.BoundedSemaphore(2))
    monkeypatch.setattr(D, "POOL_WAIT", 3.0)
    monkeypatch.setattr(D, "dbQuiet", lambda: False)

    def hold():
        with D.getConn():
            _t.sleep(0.5)
    ts = [threading.Thread(target=hold) for _ in range(2)]
    [t.start() for t in ts]
    _t.sleep(0.1)
    with D.getConn():                                 # waits ~0.4 s, no PoolError
        pass
    [t.join() for t in ts]
    try:
        with D.getConn():
            raise ValueError
    except ValueError:
        pass
    assert D._pool.used == 0 and D._slots._value == 2


def test_a_failed_graph_read_is_retried_not_kept(monkeypatch):
    """! 2026-11-07: a worker whose pool was full at boot kept {} for life."""
    import normalize_distance as ND
    import database as D
    from contextlib import contextmanager
    calls = []

    @contextmanager
    def failing():
        calls.append(1)
        raise RuntimeError("connection pool exhausted")
        yield
    monkeypatch.setattr(D, "getConn", failing)
    monkeypatch.setattr(ND, "_GRAPH_LEVELS", None)
    monkeypatch.setattr(ND, "_GRAPH_RETRY_AT", 0.0)
    assert ND.loadGraphLevels() == {}
    assert ND._GRAPH_LEVELS is None and ND._GRAPH_RETRY_AT > 0
    assert ND.loadGraphLevels() == {} and len(calls) == 1   # no retry inside the minute
    monkeypatch.setattr(ND, "_GRAPH_RETRY_AT", 0.0)
    ND.loadGraphLevels()
    assert len(calls) == 2                                  # and then it reads again


def test_the_entrants_cache_is_bounded_by_rows(monkeypatch):
    """! 2026-11-08: 512 whole fields per worker was gigabytes a worker."""
    monkeypatch.setattr(P, "_ENTRANTS_CACHE", {})
    monkeypatch.setattr(P, "_ENTRANTS_MAX_ROWS", 10)
    P._entrantsKeep("a", [1] * 4, 100.0)
    P._entrantsKeep("b", [1] * 4, 101.0)
    P._entrantsKeep("c", [1] * 4, 102.0)                   # 12 > 10: "a" goes
    assert list(P._ENTRANTS_CACHE) == ["b", "c"]
    P._entrantsKeep("big", [1] * 11, 103.0)                 # alone past the cap
    assert "big" not in P._ENTRANTS_CACHE
    P._entrantsKeep("d", [1], 102.0 + P._ENTRANTS_TTL_S + 1)     # b and c expired
    assert list(P._ENTRANTS_CACHE) == ["d"]
