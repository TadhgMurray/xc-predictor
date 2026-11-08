"""bench_db_json.py -- Postgres builds the board JSON vs Python builds it
(2026-11-08, the "json in db" experiment; numbers in that commit message).

    XCP_DB_NAME=<a scratch copy> python scripts/bench_db_json.py
    ROWS_ONLY=1 REPS=50 ...           # just the row serialisation


Three paths for the /api/rankings board, end to end through the real route
(test_client, the worker's board cache off so every request computes):

  python  today: RealDictCursor rows -> stamping -> app.json
  hybrid  the board query wrapped in json_agg(...)::text, json.loads into the
          same dicts, then the same stamping and serialisation (the only DB
          JSON shape that can keep pool_view.stampBoardRows & co.)
and, for the rows alone (no stamping, which must stay in Python):
  rows-py     execute + fetchall (RealDictCursor) + json.dumps
  rows-db     execute json_agg(...)::text + fetch the one string

Byte identity is checked for every board (python vs hybrid full responses;
rows-py vs rows-db texts). Memory: tracemalloc peak per request.
Same for the search dropdown's row shape (kind, label, sublabel, link).
"""
import json
import os
import statistics
import sys
import time
import tracemalloc

# the database is the caller's (XCP_DB_*): a scratch copy with the board
# tables, never the live site during a pipeline run. Every request computes:
# the worker's board copy is off.
os.environ.update(XCP_PREWARM="0", XCP_BOARD_API_TTL="0")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
for d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(ROOT, d))
import contextlib
with contextlib.redirect_stdout(sys.stderr):
    import app as A
import psycopg2.extras
import rankings as R
import db_timing

REPS = int(os.environ.get("REPS", "30"))
BOARDS = [
    "board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs&limit=50&offset=0&sort=rating",
    "board=ability&pool=college_f&sport=TF&scope=usa&scale=hs&limit=50&offset=0&sort=rating",
    "board=ability&pool=hs_f&sport=XC&scope=usa&scale=hs&limit=200&offset=0&sort=rating",
    "board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs&limit=50&offset=5000&sort=rating",
    "board=performance&pool=hs_m&sport=XC&scope=usa&scale=hs&limit=50&offset=0&sort=rating&distance=5000",
    "board=performance&pool=ms_f&sport=XC&scope=usa&scale=hs&limit=200&offset=0&sort=rating",
]


class JsonAggCursor:
    """A cursor whose SELECTs come back as Postgres-built JSON: the query is
    wrapped in json_agg(...)::text and the one string is parsed."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        s = sql.strip()
        if s.upper().startswith(("SELECT", "WITH")) and "json_agg" not in s:
            s = f"SELECT coalesce(json_agg(t), '[]')::text FROM ({s}) t"
        return self._cur.execute(s, params)

    def fetchall(self):
        rows = self._cur.fetchall()
        if len(rows) == 1 and len(rows[0]) == 1:
            v = list(rows[0].values())[0] if isinstance(rows[0], dict) else rows[0][0]
            if isinstance(v, str) and v.startswith("["):
                return json.loads(v)
        return rows

    def __getattr__(self, k):
        return getattr(self._cur, k)


def timed(fn, reps=REPS):
    fn()                                    # warm
    ts = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1e3)
    return statistics.median(ts), min(ts)


def peak(fn):
    tracemalloc.start()
    fn()
    p = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return p / 1024


client = A.app.test_client()


def route(qs):
    r = client.get("/api/rankings?" + qs, base_url="https://racecast.co",
                   headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200, (qs, r.status_code)
    return r.get_data()


fns = {name: getattr(R, name) for name in ("getAbilityRankings", "getPerformanceRankings", "getPrRankings")}


def hybrid(on):
    for name, fn in fns.items():
        if on:
            setattr(R, name, (lambda f: lambda cur, flt: f(JsonAggCursor(cur), flt))(fn))
        else:
            setattr(R, name, fn)
    # the route reads them through the module's names it imported at load
    for name in fns:
        if hasattr(A, name):
            setattr(A, name, getattr(R, name))


print(f"{'board':<92} {'path':<7} {'median ms':>9} {'min ms':>7} {'peak KiB':>8}  identical")
for qs in ([] if os.environ.get("ROWS_ONLY") else BOARDS):
    hybrid(False)
    base = route(qs)
    py_t = timed(lambda: route(qs))
    py_m = peak(lambda: route(qs))
    hybrid(True)
    hy = route(qs)
    hy_t = timed(lambda: route(qs))
    hy_m = peak(lambda: route(qs))
    hybrid(False)
    same = "yes" if hy == base else f"NO ({sum(a != b for a, b in zip(hy, base))}+ bytes differ, {len(hy)} vs {len(base)})"
    print(f"{qs:<92} {'python':<7} {py_t[0]:>9.2f} {py_t[1]:>7.2f} {py_m:>8.0f}")
    print(f"{'':<92} {'hybrid':<7} {hy_t[0]:>9.2f} {hy_t[1]:>7.2f} {hy_m:>8.0f}  {same}")


# ---- the rows alone: what the database could take over -------------------- #
from database import getConn
from werkzeug.datastructures import MultiDict
from urllib.parse import parse_qsl


class Grab:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return [{}] * 1000

    def fetchone(self):
        return None


_CTX = A.app.app_context()
_CTX.push()


def dumps_like_flask(rows):
    # exactly what jsonify writes (sorted keys, compact, ASCII, trailing newline)
    return A.app.json.response(rows).get_data(as_text=True)


print()
print(f"{'rows only':<92} {'path':<7} {'median ms':>9} {'min ms':>7} {'peak KiB':>8}  identical text")
with getConn() as conn:
    for qs in BOARDS:
        f, err = A.parseFilters(MultiDict(parse_qsl(qs)))
        g = Grab()
        {"ability": R.getAbilityRankings, "performance": R.getPerformanceRankings,
         "pr": R.getPrRankings}[f["board"]](g, f)
        sql, params = g.calls[-1]
        wrapped = f"SELECT coalesce(json_agg(t), '[]')::text FROM ({sql}) t"
        # the key order and spacing Flask's provider writes: sorted keys, no spaces
        def py():
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return dumps_like_flask(cur.fetchall())

        def db():
            with conn.cursor() as cur:
                cur.execute(wrapped, params)
                return cur.fetchone()[0]

        def db_ordered():
            # the closest Postgres gets to Flask's text: keys sorted (jsonb
            # sorts by length first, so build the object by hand), no spaces
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM ({sql}) t LIMIT 0", params)
                cols = sorted(d.name for d in cur.description)
                if os.environ.get("DBG"):
                    print("COLS", cols, file=sys.stderr)
                obj = "'{' || " + " || ',' || ".join(
                    f"to_json('{c}'::text)::text || ':' || coalesce(to_json(t.\"{c}\")::text, 'null')"
                    for c in cols) + " || '}'"
                cur.execute(f"SELECT '[' || coalesce(string_agg({obj}, ','), '') || ']' FROM ({sql}) t",
                            params)
                return cur.fetchone()[0]

        a, b, c = py(), db(), db_ordered()
        t_py, t_db, t_dbo = timed(py), timed(db), timed(db_ordered)
        m_py, m_db = peak(py), peak(db)
        rows_a, rows_b = json.loads(a), json.loads(b)
        val_same = rows_a == rows_b
        print(f"{qs:<92} {'py':<7} {t_py[0]:>9.2f} {t_py[1]:>7.2f} {m_py:>8.0f}")
        print(f"{'':<92} {'db':<7} {t_db[0]:>9.2f} {t_db[1]:>7.2f} {m_db:>8.0f}  "
              f"text {'yes' if a == b else 'NO'}; values {'equal' if val_same else 'DIFFER'}")
        print(f"{'':<92} {'db-sort':<7} {t_dbo[0]:>9.2f} {t_dbo[1]:>7.2f} {'':>8}  "
              f"text {'yes' if a.strip() == c else 'NO'}")
        if a.strip() != c:
            nd = sum(1 for x, y in zip(json.loads(a), json.loads(c)) if json.dumps(x) != json.dumps(y))
            # the first difference, to name the cause
            i = next((i for i, (x, y) in enumerate(zip(a, c)) if x != y), min(len(a), len(c)))
            print(f"{'':<92}           first difference at {i}: py {a[max(0, i - 30):i + 30]!r}")
            print(f"{'':<92}                                db {c[max(0, i - 30):i + 30]!r}")
