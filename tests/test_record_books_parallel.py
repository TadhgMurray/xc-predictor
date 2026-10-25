"""The school record books built side by side (build_record_books.
buildSchoolPages; owner 2026-10-25: "you can multithread/core as
necessary. You have freedom as long as it remains accurate and working").

Pinned, with a synthetic database (no Postgres): the REAL schoolBook --
school_prs.schoolPrData, the season lists, teams.getTeamRankings, the name
and meet lookups -- runs against a fake cursor that answers each query from
the school's own seeded rows, once on one session in order and once in
spawned worker processes, and the pages written are the same:
  * the same keys, the same ctx, the same fingerprint, page for page;
  * the same failed page and the same log line for it;
  * the pool really ran elsewhere (each worker opens its own session);
  * the worker count: env, default min(8, cores - 4), strict quiet is 1.
"""
import datetime
import json
import os
import random
import sys
import zlib
from contextlib import contextmanager
from decimal import Decimal

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import build_record_books as B  # noqa: E402
import record_books as RB  # noqa: E402


# ------------------------------------------------------------------ #
#  a synthetic database: every answer a pure function of the query's
#  parameters (crc32 seeds, never hash(): the workers are other processes
#  with their own hash seed), so any session gets the same rows
# ------------------------------------------------------------------ #

BROKEN = "Broken HS"
_XC_DISTS = (5000, 4828.03, 3200, 4000, 2414.02)
_TF_DISTS = (1600, 1609.34, 3200, 800, 400, 100, 3218.69)
_COURSES = ("Lane CC", "Fort Steilacoom", "Woodward Park", "Mt. SAC")
_FIELD = ("Shot Put", "Long Jump", "High Jump", "Discus", "Triple Jump")
_HURDLES = ("110m Hurdles", "100m Hurdles", "300m Hurdles", "300mH")


def _rng(*parts):
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


def _people(school):
    base = 1000 + zlib.crc32(school.encode()) % 9000 * 100
    return [base + k for k in range(14)]


def _name(pid):
    return None if pid % 7 == 0 else f"Runner {pid}"


def _poolOf(pid):
    return ("hs_m", "hs_f", "ms_m", "hs_m", "hs_f")[pid % 5]


def _running(school, sport):
    out = []
    for pid in _people(school):
        r = _rng("run", school, sport, pid)
        for k in range(r.randint(2, 9)):
            dist = r.choice(_XC_DISTS if sport == "XC" else _TF_DISTS)
            year = r.randint(2016, 2025)
            out.append({
                "result_id": pid * 100 + k, "person_id": pid, "pool": _poolOf(pid),
                "speed_rating": Decimal(str(round(r.uniform(80, 125), 2))),
                # rounded to a second: ties inside a bucket, so the
                # first-fetched-wins rule is exercised
                "time_seconds": Decimal(str(round(dist / r.uniform(4.5, 6.5)))),
                "distance": Decimal(str(dist)),
                "race_date": datetime.date(year, r.randint(8, 11), r.randint(1, 28)),
                "year": year, "grade": str(r.randint(9, 12)),
                "meet_id": r.randint(1, 40), "div_id": r.randint(1, 4),
                "event_id": r.randint(1, 9),
                "course_name": r.choice(_COURSES) if sport == "XC" else None})
    return out


def _field(school):
    out = []
    for pid in _people(school):
        r = _rng("field", school, pid)
        for k in range(r.randint(0, 4)):
            ev = r.choice(_FIELD)
            d = datetime.date(r.randint(2016, 2025), r.randint(3, 6), r.randint(1, 28))
            mark = (f"{r.randint(15, 52)}-{r.randint(0, 11):02d}.{r.choice(('00', '25', '50'))}"
                    if r.random() < 0.5 else f"{r.uniform(1.2, 16.0):.2f}m")
            out.append({"result_id": -(pid * 100 + k) if k % 3 == 0 else pid * 100 + 50 + k,
                        "person_id": pid if k % 4 else None, "athlete_id": pid,
                        "mark": mark, "grade": "11", "date": d,
                        "meet_id": r.randint(1, 40), "div_id": 1, "event_id": 20 + k,
                        "year": d.year, "event_short": ev})
    return out


def _hurdles(school):
    out = []
    for pid in _people(school):
        r = _rng("hurdle", school, pid)
        for k in range(r.randint(0, 3)):
            ev = r.choice(_HURDLES)
            d = datetime.date(r.randint(2016, 2025), r.randint(3, 6), r.randint(1, 28))
            out.append({"result_id": pid * 100 + 80 + k, "person_id": pid,
                        "athlete_id": pid, "time_seconds": Decimal(str(round(r.uniform(13, 60), 2))),
                        "mark": None, "grade": "10", "date": d,
                        "meet_id": r.randint(1, 40), "div_id": 1, "event_id": 40 + k,
                        "year": d.year, "event_short": ev})
    return out


def _seasons(school, sport):
    out = []
    for pid in _people(school):
        r = _rng("season", school, sport, pid)
        pool = _poolOf(pid)
        grades = RB.LEVELS[pool.split("_")[0]][2]
        y0 = r.randint(2015, 2021)
        for i, g in enumerate(grades):
            if r.random() < 0.2:
                continue
            out.append({"person_id": pid, "pool": pool, "sport": sport, "year": y0 + i,
                        "rating": Decimal(str(round(r.uniform(85, 125), 3))),
                        "n_races": r.randint(3, 9), "state": "OR", "school": school,
                        "grade": g, "grade_key": g})
    return out


def _answer(sql, params):
    """The rows one query returns. Order matters to schoolBook (first of a
    tie wins), so every list comes out in a fixed order."""
    s = " ".join(sql.split())
    if s.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
        return []
    if "to_regclass" in s:
        return [{"to_regclass": "person_home_state"}]
    if "FROM person_home_state" in s:
        return [{"person_id": i, "state": ("OR", "WA", "OR")[i % 3]}
                for i in params["ids"] if i % 11]
    if "FROM ranking_results rr" in s:
        if params["school"] == BROKEN:
            raise RuntimeError("synthetic failure")
        return _running(params["school"], params["sport"])
    if "FROM athlete_season s" in s:
        assert params["school"] == [params["school"][0]]
        return _seasons(params["school"][0], params["sport"])
    if "FROM results_tf r" in s and "%(pat)s" in s:
        return _hurdles(params["school"])
    if "FROM results_tf r" in s:
        return _field(params["school"])
    if "btrim(athlete_name)" in s:
        return [{"person_id": i, "name": f"Feed {i}"} for i in params[0] if i % 2]
    if "FROM athletes" in s and "athlete_id = ANY" in s:
        return [{"athlete_id": i, "name": _name(i), "gender": "MF"[i % 2]}
                for i in params["ids"]]
    if "a.person_id = ANY" in s:
        return [{"person_id": i, "name": _name(i)} for i in params[0] if _name(i)]
    if "information_schema.columns" in s:
        return [{"?column?": 1}]
    if "FROM team_season t" in s:
        school = params["schools"][0]
        r = _rng("team", school, params["pool"], params["sport"], params["scope"])
        rows = []
        for k in range(min(params["limit"], r.randint(0, 6))):
            rows.append({"school": school.title(), "state": params["scope"],
                         "pool": params["pool"], "sport": params["sport"],
                         "year": 2025 - k, "scope": params["scope"], "rank": r.randint(1, 40),
                         "points": r.randint(20, 300), "n_athletes": r.randint(5, 12),
                         "top5_mean": Decimal(str(round(r.uniform(90, 120), 2))),
                         "fifth_rating": Decimal("95.5"), "best_rating": Decimal("121.25"),
                         "best_person_id": _people(school.title())[k]})
        return rows
    if "DISTINCT ON (person_id, pool, year)" in s:
        sport, ids = params
        rows = []
        for pid in sorted(ids):
            r = _rng("best", sport, pid)
            for year in range(2015, 2026):
                rows.append({"person_id": pid, "pool": _poolOf(pid), "year": year,
                             "sport": sport, "result_id": pid * 1000 + year,
                             "meet_id": r.randint(1, 40), "div_id": 1, "event_id": 3,
                             "race_date": f"{year}-10-{r.randint(10, 28)}",
                             "time_seconds": Decimal("950.5"), "distance": Decimal("5000"),
                             "rating": Decimal(str(round(r.uniform(85, 125), 2)))})
        return rows
    if "meet_name" in s:
        table = s.split(" FROM ")[1].split()[0]
        return [{"meet_id": m, "meet_name": f"{table} meet {m}"}
                for m in sorted(params[0]) if m % 9]
    raise AssertionError(f"the fake database does not know this query: {s[:160]}")


class FakeCur:
    def __init__(self):
        self._rows = []
        self.connection = FakeConn()

    def execute(self, sql, params=None):
        self._rows = _answer(sql, params)

    def fetchall(self):
        return [dict(r) for r in self._rows]

    def fetchone(self):
        return dict(self._rows[0]) if self._rows else None


class FakeConn:
    def commit(self):
        pass

    def rollback(self):
        pass


@contextmanager
def fakeOpener():
    """A worker's session (build_record_books._poolInit's `opener`), and a
    note that THIS process opened one."""
    d = os.environ.get("RB_TEST_PID_DIR")
    if d:
        open(os.path.join(d, str(os.getpid())), "w").close()
    yield FakeConn(), FakeCur()


TODO = [
    ("Jesuit", "TF", "OR", "OR", "fp-a"),
    ("Jesuit", "TF", "WA", "OR", "fp-b"),          # the second cluster
    ("Jesuit", "XC", "OR", "OR", "fp-c"),
    ("Summit", "XC", None, None, "fp-d"),           # no identity cluster
    (BROKEN, "XC", None, None, "fp-e"),
    ("Central Catholic", "TF", None, None, "fp-f"),
    ("Central Catholic", "XC", "OR", "OR", "fp-g"),
    ("Kingston", "XC", "WA", "WA", "fp-h"),
    ("Kingston", "TF", "MO", "WA", "fp-i"),
    ("Newbury Park", "XC", None, None, "fp-j"),
    ("Newbury Park", "TF", None, None, "fp-k"),
]


def _run(monkeypatch, capsys, workers, opener=None):
    written = []

    def fake_write(conn, cur, kind, rows, sig, replace=True):
        assert kind == "school" and sig is None and replace is False
        assert len(rows) <= 100
        written.extend(rows)
    monkeypatch.setattr(B, "writePart", fake_write)
    capsys.readouterr()
    built, failed = B.buildSchoolPages(FakeConn(), FakeCur(), TODO, workers,
                                       opener=opener)
    log = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  ! ")]
    return built, failed, written, log


def test_parallel_pages_are_the_serial_pages(monkeypatch, capsys, tmp_path):
    import rankings
    # the probe the running query asks; answered here so no session is opened
    monkeypatch.setattr(rankings, "_EVENT_KIND_PRESENT", True)
    monkeypatch.setenv("RB_TEST_PID_DIR", str(tmp_path))

    s_built, s_failed, s_rows, s_log = _run(monkeypatch, capsys, 1)
    assert not os.listdir(tmp_path)                  # one session: no pool at all
    p_built, p_failed, p_rows, p_log = _run(monkeypatch, capsys, 3, opener=fakeOpener)

    assert (s_built, s_failed) == (len(TODO) - 1, 1)
    assert (p_built, p_failed) == (s_built, s_failed)
    assert s_log == p_log == [f"  ! {BROKEN} (None) XC: RuntimeError: synthetic failure"]
    # serial writes in todo order; the pool in whatever order pages finish
    assert [r[0] for r in s_rows] == [RB.schoolKey(s, st, sp) for s, sp, st, _p, _f in TODO
                                      if s != BROKEN]
    serial = {k: (ctx, fp) for k, ctx, fp in s_rows}
    pooled = {k: (ctx, fp) for k, ctx, fp in p_rows}
    assert len(pooled) == len(p_rows) == len(s_rows)
    assert pooled.keys() == serial.keys()
    for key in serial:
        assert pooled[key] == serial[key], key
        # and byte for byte as the jsonb would get it
        assert (json.dumps(pooled[key][0], default=str, ensure_ascii=False)
                == json.dumps(serial[key][0], default=str, ensure_ascii=False)), key
    # the synthetic pages are not trivially empty: every part is exercised
    tf = serial[RB.schoolKey("Jesuit", "OR", "TF")][0]
    kinds = {e.get("kind") for e in tf["events"]}
    assert {"running", "field", "hurdles"} <= kinds
    assert tf["grades"] and tf["improved"] and tf["teams"]
    # the pool ran in other processes, each with its own session
    pids = {int(p) for p in os.listdir(tmp_path)}
    assert pids and os.getpid() not in pids and len(pids) <= 3


def test_one_worker_never_starts_a_pool(monkeypatch, capsys):
    import rankings
    monkeypatch.setattr(rankings, "_EVENT_KIND_PRESENT", True)

    def boom(*a, **k):
        raise AssertionError("the one-session path started a pool")
    monkeypatch.setattr(B, "_pooledPages", boom)
    built, failed, _rows, _log = _run(monkeypatch, capsys, 1)
    assert (built, failed) == (len(TODO) - 1, 1)


def test_worker_count(monkeypatch):
    monkeypatch.delenv("XCP_RECORD_WORKERS", raising=False)
    monkeypatch.setenv("XCP_DB_QUIET", "1")
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    assert B.schoolWorkers() == 8                   # min(8, 24 - 4)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert B.schoolWorkers() == 4
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert B.schoolWorkers() == 1                   # never under one
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    monkeypatch.setenv("XCP_DB_QUIET", "strict")
    assert B.schoolWorkers() == 1                   # site-first: one session
    monkeypatch.setenv("XCP_RECORD_WORKERS", "6")
    assert B.schoolWorkers() == 6                   # the env says
    assert B.schoolWorkers(2) == 2                  # --workers says
    monkeypatch.setenv("XCP_RECORD_WORKERS", "0")
    assert B.schoolWorkers() == 1
