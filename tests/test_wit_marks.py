"""What it takes, precomputed (2026-11-08): racecast/build_wit_marks.py stores
every state's cuts._computeUncached answer in wit_marks, and cuts.marksFor /
cuts.athleteLine read that row instead of computing in the web workers.

Pinned here: the stored payload comes back EXACTLY (types, key order,
dates, Decimals, tuples, floats, None, nested lists) or is refused; marksFor
serves a fresh row without computing and keeps computed_at = when the row
was computed; it falls back to the live compute when the row is missing,
older than MAX_AGE_S, built by other code, or the table does not exist; the
athlete line reads the row instead of starting a background thread; the
pipeline and the nightly run the builder. The Postgres half (builder ->
table -> marksFor, against cuts' own fixture) runs when XCP_TEST_DSN is set.
"""
import contextlib
import datetime
import decimal
import io
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import pytest  # noqa: E402

import cuts as C  # noqa: E402
import ttlcache  # noqa: E402

DSN = os.environ.get("XCP_TEST_DSN")


def _real_marks():
    """cuts' own answer for the small test state, times stamped."""
    from test_what_it_takes import _small_state
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    C.stampTimes(data, {"boys": {"course_name": "Woodward Park",
                                 "distance": 5000, "editions": 3,
                                 "difficulty": 0.0123, "canonical_id": 1}},
                 convert=lambda r, *a, **k: 2000.0 - 6.1 * r)
    data["current_year"] = 2026
    data["n_meets"] = 6
    return data


# ------------------------------------------------------------------ #
#  the payload round-trips exactly
# ------------------------------------------------------------------ #

def test_real_marks_round_trip_exactly():
    data = _real_marks()
    back = C.payloadFrom(C.payloadText(data))
    assert C.sameValue(back, data) and back == data
    # the page's own walk: a season's lines in their order
    s = C.findDivision(back, "boys", "2")["latest"]
    assert list(s["lines"]) == ["qualify", "top", "podium"]
    assert type(s["lines"]["qualify"]["t_typical"]) is float


def test_every_json_awkward_type_comes_back_as_itself():
    v = {"date": datetime.date(2025, 11, 29),
         "dt": datetime.datetime(2025, 11, 29, 10, 30, 5, 123),
         "dec": decimal.Decimal("5000.00"), "tuple": (1, "a", None),
         "floats": [0.1, 1e-07, 5000.0, -0.0, 1.0 / 3, 123456789.123],
         "ints": [0, -5, 2 ** 40], "none": None, "bools": [True, False],
         "nested": [[{"a": [1, [2.5, None]]}], []], "empty": {},
         "text": "Woodward Park — São Paulo \"quoted\"",
         "int_keys": {2025: "a", 2024: "b"},
         "tag_like": {"__wit__": "date", "v": "not a date"},
         "order": {"z": 1, "a": 2, "m": 3}}
    back = C.payloadFrom(C.payloadText(v))
    assert C.sameValue(back, v)
    assert type(back["date"]) is datetime.date and back["date"] == v["date"]
    assert type(back["dec"]) is decimal.Decimal and str(back["dec"]) == "5000.00"
    assert back["tuple"] == (1, "a", None) and type(back["tuple"]) is tuple
    assert list(back["int_keys"]) == [2025, 2024]
    assert back["tag_like"] == {"__wit__": "date", "v": "not a date"}
    assert list(back["order"]) == ["z", "a", "m"]
    # ascii text: nothing for a SQL_ASCII cluster to translate
    C.payloadText(v).encode("ascii")


def test_what_cannot_come_back_exactly_is_refused():
    for bad in ({"x": float("nan")}, {"x": float("inf")}, {"x": {1, 2}},
                {"x": object()},
                {"x": datetime.datetime(2025, 1, 1,
                                        tzinfo=datetime.timezone.utc)}):
        with pytest.raises((TypeError, ValueError)):
            C.payloadText(bad)


def test_same_value_is_strict_about_types_and_order():
    assert not C.sameValue({"a": 1}, {"a": 1.0})
    assert not C.sameValue({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert not C.sameValue([1, 2], (1, 2))
    assert not C.sameValue(decimal.Decimal("5000"), 5000.0)
    assert C.sameValue({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]})


# ------------------------------------------------------------------ #
#  marksFor: the stored row first, the live compute behind it
# ------------------------------------------------------------------ #

class _Conn:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


class _Cur:
    """Answers the wit_marks read with `row` (or raises `exc`)."""
    def __init__(self, row=None, exc=None):
        self.row, self.exc, self.sql = row, exc, []
        self.connection = _Conn()
        self._last = None

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        if "FROM" in sql and C.TABLE in sql:
            if self.exc is not None:
                raise self.exc
            self._last = self.row
        else:
            self._last = None

    def fetchone(self):
        return self._last


def _row(data, age_s=100.0, code=None):
    return {"payload": C.payloadText(data),
            "at": decimal.Decimal(str(round(time.time() - age_s, 3))),
            "code": C.CODE if code is None else code}


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """Empty caches, a private shared-cache dir, and a live compute that
    records each call."""
    import sharedcache
    ttlcache.clear()
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setitem(C._NO_TABLE, "until", 0.0)
    calls = []

    def live(cur, state):
        calls.append(state)
        return {"state": state, "genders": {}, "years": [], "unplaced": 0,
                "live": True}
    monkeypatch.setattr(C, "_computeUncached", live)
    yield calls
    ttlcache.clear()


def test_marks_for_serves_the_stored_row_without_computing(fresh, tmp_path):
    data = _real_marks()
    row = _row(data, age_s=3600.0)
    cur = _Cur(row)
    got = C.marksFor(cur, "CA")
    assert fresh == []                                  # nothing computed
    assert not os.path.exists(tmp_path / "shared") or not os.listdir(tmp_path / "shared")
    assert got["computed_at"] == pytest.approx(float(row["at"]))
    rest = {k: v for k, v in got.items() if k != "computed_at"}
    assert C.sameValue(rest, data)                      # exactly the live answer
    # one primary-key read, under a savepoint
    reads = [s for s in cur.sql if C.TABLE in s]
    assert len(reads) == 1 and "WHERE state = %s" in reads[0]
    assert cur.sql[0] == "SAVEPOINT wit_stored"
    # the next request is served from memory: no second read
    C.marksFor(cur, "CA")
    assert len([s for s in cur.sql if C.TABLE in s]) == 1


@pytest.mark.parametrize("why", ["missing", "stale", "other_code", "no_table",
                                 "future", "bad_payload"])
def test_marks_for_falls_back_to_the_live_compute(fresh, why):
    data = _real_marks()
    cur = {
        "missing": lambda: _Cur(None),
        "stale": lambda: _Cur(_row(data, age_s=C.MAX_AGE_S + 60)),
        "other_code": lambda: _Cur(_row(data, code="0000000000000000")),
        "future": lambda: _Cur(_row(data, age_s=-3600.0)),
        "bad_payload": lambda: _Cur(dict(_row(data), payload="{not json")),
        "no_table": lambda: _Cur(exc=type("UndefinedTable", (Exception,),
                                          {"pgcode": "42P01"})()),
    }[why]()
    got = C.marksFor(cur, "CA")
    assert fresh == ["CA"] and got["live"] is True
    assert "_src" not in got and "_shared_at" not in got
    if why == "no_table":
        # rolled back to the savepoint, and not asked again for a while
        assert "ROLLBACK TO SAVEPOINT wit_stored" in cur.sql
        assert C._NO_TABLE["until"] > time.time()


def test_a_stored_copy_is_kept_an_hour_and_never_past_its_age():
    now = time.time()
    v = {"_src": C._TABLE_SRC, "_shared_at": now - 60}
    assert C._ttlOf(v) == pytest.approx(C._STORED_TTL, abs=2)
    old = {"_src": C._TABLE_SRC, "_shared_at": now - C.MAX_AGE_S + 600}
    assert C._ttlOf(old) == pytest.approx(600, abs=2)
    # the live copy keeps the 6 h it always had
    assert C._ttlOf({"_shared_at": now - 3600}) == pytest.approx(C._TTL - 3600, abs=2)
    assert C._ttlOf({"error": "X"}) == 300.0


def test_max_age_is_a_nightly_plus_the_old_tolerance():
    # 24 h between nightly builds + the 6 h the page always tolerated
    assert C.MAX_AGE_S == 24 * 3600 + C._TTL


def test_the_athlete_line_reads_the_row_instead_of_warming(fresh, monkeypatch):
    data = _real_marks()
    cur = _Cur(_row(data))
    warmed = []
    monkeypatch.setattr(C, "_warm", warmed.append)
    monkeypatch.setattr(C.P, "currentYear", lambda cur: 2026)
    q = C.findDivision(data, "boys", "2")["latest"]["lines"]["qualify"]["mark"]
    monkeypatch.setattr(C, "_athleteSeason", lambda cur, pid, year: {
        "pool": "hs_m", "state": "CA", "mean_rating": q - 1.0,
        "state_div": "2", "section": None, "section_div": None})
    monkeypatch.setattr(C, "_lineTimes", lambda *a, **k: [])
    monkeypatch.setattr(C, "stampClocks", lambda line: line)
    line = C.athleteLine(cur, 1)
    assert warmed == [] and fresh == []
    assert line["text"] == "last year's state qualifying mark"
    # and without a row: no line now, the background warm as before
    ttlcache.clear()
    line = C.athleteLine(_Cur(None), 1)
    assert line is None and warmed == ["CA"] and fresh == []


# ------------------------------------------------------------------ #
#  the builder and the pipeline
# ------------------------------------------------------------------ #

def test_builder_stores_cuts_own_compute(monkeypatch):
    import build_wit_marks as B
    data = _real_marks()
    monkeypatch.setattr(C, "_computeUncached",
                        lambda cur, st: data if st == "CA" else 1 / 0)

    class Conn:
        def cursor(self, **k):
            return contextlib.nullcontext(object())

        def rollback(self):
            pass

    @contextlib.contextmanager
    def getConn():
        yield Conn()
    logs = []
    rows = B.computeAll(["CA", "OR"], 2, getConn, log=logs.append)
    assert list(rows) == ["CA"]                         # OR failed: no row
    assert C.sameValue(C.payloadFrom(rows["CA"][0]), data)
    assert any(line.startswith("  ! OR: ZeroDivisionError") for line in logs)


def _src(*parts):
    return io.open(os.path.join(_ROOT, *parts), encoding="utf-8").read()


def test_pipeline_and_nightly_run_the_builder_after_its_inputs():
    p = _src("deploy", "run_pipeline.sh")
    line = 'step 13f2_wit_marks   "$PY" -u racecast/build_wit_marks.py'
    assert line in p
    i = p.index(line)
    for before in ("step 10_rankings_finish", "10d_school_units",
                   "bgjoin 10e_meet_units", "steps2 13_panels_xc",
                   "step 13f_breakouts"):
        assert p.index(before) < i, before
    n = _src("deploy", "nightly_update.sh")
    line = 'step 13f2_wit_marks "$PY" -u racecast/build_wit_marks.py'
    assert line in n
    assert n.index("step 10_rankings_finish") < n.index(line)
    assert n.index("13_panels_xc") < n.index(line)
    # before the --no-publish exit: every night, whatever publishing did
    assert n.index(line) < n.index('if [ "$PUBLISH" -eq 0 ]; then\n  TOTAL')
    assert '"wit_marks"' in _src("scripts", "cull_db.py")


# ------------------------------------------------------------------ #
#  Postgres: builder -> table -> marksFor, on cuts' own fixture
# ------------------------------------------------------------------ #

@pytest.mark.skipif(not DSN, reason="set XCP_TEST_DSN to a throwaway Postgres")
def test_builder_to_table_to_page_against_postgres(monkeypatch, tmp_path):
    import psycopg2
    import psycopg2.extras
    import sharedcache
    import build_wit_marks as B
    from test_what_it_takes import buildFixture
    import predict
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setitem(C._NO_TABLE, "until", 0.0)
    conn = psycopg2.connect(DSN)
    try:
        buildFixture(conn)
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS wit_marks, wit_marks_new")
        conn.commit()
        predict._SEASON_CACHE.clear()

        @contextlib.contextmanager
        def getConn():
            c = psycopg2.connect(DSN)
            try:
                yield c
            finally:
                c.close()
        rows = B.computeAll(["CA", "OR"], 2, getConn, log=lambda *a: None)
        assert sorted(rows) == ["CA", "OR"]
        B.publish(conn, rows, 2)

        dc = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        live = C._computeUncached(dc, "CA")
        conn.rollback()
        assert live["genders"]["boys"]["divisions"]   # a real answer
        payload, at = C.storedMarks(dc, "CA")
        assert C.sameValue(payload, live)
        assert at == pytest.approx(rows["CA"][1], abs=0.01)

        # the page: the row, nothing computed, computed_at = the build's
        ttlcache.clear()
        real = C._computeUncached
        monkeypatch.setattr(C, "_computeUncached",
                            lambda *a: pytest.fail("computed live"))
        got = C.marksFor(dc, "CA")
        assert got["computed_at"] == pytest.approx(at)
        assert C.sameValue({k: v for k, v in got.items() if k != "computed_at"},
                           live)
        # the connection is still usable after the savepoint
        dc.execute("SELECT 1 AS ok")
        assert dc.fetchone()["ok"] == 1
        monkeypatch.setattr(C, "_computeUncached", real)

        # --state rewrites the named state in place, the others stay
        dc.execute("SELECT state, built_at FROM wit_marks ORDER BY state")
        before = {r["state"]: r["built_at"] for r in dc.fetchall()}
        conn.rollback()
        one = B.computeAll(["CA"], 1, getConn, log=lambda *a: None)
        B.publish(conn, one, 1, in_place=True)
        dc.execute("SELECT state, built_at FROM wit_marks ORDER BY state")
        after = {r["state"]: r["built_at"] for r in dc.fetchall()}
        conn.rollback()
        assert after["OR"] == before["OR"] and after["CA"] > before["CA"]

        # a build that lost most states is not swapped in
        with pytest.raises(SystemExit):
            B.publish(conn, one, 51)
        dc.execute("SELECT count(*) AS n FROM wit_marks")
        assert dc.fetchone()["n"] == 2
        conn.rollback()
    finally:
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS wit_marks, wit_marks_new")
            conn.commit()
        finally:
            conn.close()
        ttlcache.clear()
