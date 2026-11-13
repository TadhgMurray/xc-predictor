"""03 beside 04a on one exported snapshot (2026-11-13).

03_pro_flag, 03b_age_bands and 03a_team_pool run while the 04a linkers move
person ids, reading the database through the snapshot scripts/snapshot_hold.py
exports (XCP_PG_SNAPSHOT_FILE, scripts/database.py). The promise: they write
EXACTLY what they wrote when they ran before 04a.

    XCP_TEST_PG_DSN="host=127.0.0.1 user=postgres password=pw dbname=x" \
        python -m pytest -q tests/test_snapshot_03_beside_04a.py

The first test needs no database (unset is the old code path). The rest need
a scratch Postgres (XCP_TEST_PG_DSN or XCP_TWIN_TEST_DSN; skipped without
one); each makes its own databases. The real scripts run as subprocesses on
a synthetic corpus:

  A  today's order: the three scripts, then the 04a-style moves;
  B  the new order: snapshot, moves committed BEFORE the scripts start and
     again and again WHILE they run (a thread), the scripts on the snapshot;
  C  the control: the moves, then the scripts on the live database.

A == B table for table (and team_pool's swapped index names); C != A for
every script's table, so the moves are ones the scripts can see.
"""
import os
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
DSN = os.environ.get("XCP_TEST_PG_DSN") or os.environ.get("XCP_TWIN_TEST_DSN")

needs_pg = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def test_unset_is_the_old_code_path():
    """No XCP_PG_SNAPSHOT_FILE: the pool's plain connection class, no hook."""
    code = (
        "import os, sys; sys.path.insert(0, 'scripts');"
        "os.environ.pop('XCP_PG_SNAPSHOT_FILE', None);"
        "os.environ.setdefault('XCP_DB_PASSWORD', 'x');"
        "import database as d;"
        "assert d.snapshotId() is None;"
        "assert not d.snapshotActive(object());"
        "d._importSnapshot(object());"   # a plain connection: a no-op
        "print('ok')"
    )
    out = subprocess.run([PY, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    src = open(os.path.join(ROOT, "scripts", "database.py")).read()
    # the pool only takes the snapshot class when the variable names a file
    assert 'if snapshotId() is not None:\n            kw["connection_factory"] = SnapshotConnection' in src


# --------------------------------------------------------------------- pg

def _parts():
    import psycopg2.extensions
    return psycopg2.extensions.parse_dsn(DSN)


def _dsn(db):
    p = dict(_parts(), dbname=db)
    return " ".join(f"{k}={v}" for k, v in p.items())


def _connect(db):
    import psycopg2
    return psycopg2.connect(_dsn(db))


def _fresh(db):
    import psycopg2
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {db}")
    admin.close()


def _env(db, snapshot=None):
    p = _parts()
    env = dict(os.environ)
    env.update({"XCP_DB_HOST": p.get("host", "127.0.0.1"),
                "XCP_DB_PORT": str(p.get("port", 5432)),
                "XCP_DB_USER": p.get("user", "postgres"),
                "XCP_DB_PASSWORD": p.get("password", ""),
                "XCP_DB_NAME": db,
                "XCP_DB_QUIET": "1",               # as the pipeline runs them
                "XCP_ANET_LEVELS": "4=hs,2=ms,8=college,16=club",
                "PYTHONPATH": os.pathsep.join([ROOT, os.path.join(ROOT, "scripts"),
                                               os.path.join(ROOT, "engine")])})
    env.pop("XCP_DB_READ_DSN", None)
    env.pop("XCP_PG_SNAPSHOT_FILE", None)
    if snapshot:
        env["XCP_PG_SNAPSHOT_FILE"] = snapshot
    return env


# the corpus ------------------------------------------------------------

NIKE, HS_A, HS_B, CLUB31, SMALL40, COLLEGE = 900, 11, 12, 31, 40, 50
PROS = list(range(100, 108))
COLLEGIAN = 500
CLUB31_PEOPLE = list(range(200, 215))     # exactly 15: one merge -> 14 -> pro
KIDS = list(range(300, 330))
YOUTH = list(range(400, 412))


def _seed(cur):
    cur.execute("""
        CREATE TABLE results (result_id bigserial PRIMARY KEY, person_id bigint,
            meet_id bigint, div_id bigint, date text, grade text, school text,
            team_id bigint, athlete_name text);
        CREATE TABLE results_tf (result_id bigserial PRIMARY KEY, person_id bigint,
            meet_id bigint, div_id bigint, event_id bigint, date text,
            grade text, school text, team_id bigint, athlete_name text);
        CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text,
                            division text);
        CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint,
                               meet_name text, division text, event_short text);
        CREATE TABLE meets_tfrrs (meet_id bigint, meet_name text);
        CREATE TABLE grade_fix (person_id bigint, season int, grade text,
                                level text, method text, trust text);
        CREATE TABLE anet_team (team_id bigint, level int, school text,
                                state text, name text);
        CREATE TABLE athletes (athlete_id bigint, person_id bigint, name text);
        CREATE TABLE person_redirect (old_id bigint, new_id bigint);
        -- last run's tables, which the scripts replace
        CREATE TABLE pro_athlete_season (person_id bigint NOT NULL,
            season int NOT NULL, pro_races int, round int,
            PRIMARY KEY (person_id, season));
        INSERT INTO pro_athlete_season VALUES (999, 2001, 9, 0), (998, 2002, 9, 0);
        CREATE TABLE team_pool (team_id int PRIMARY KEY, kind text NOT NULL,
            reason text NOT NULL, n_athletes int NOT NULL DEFAULT 0,
            n_rows bigint NOT NULL DEFAULT 0, level text,
            n_pros int NOT NULL DEFAULT 0, built date NOT NULL DEFAULT current_date);
        CREATE INDEX team_pool_kind_idx ON team_pool (kind);
        INSERT INTO team_pool (team_id, kind, reason) VALUES (777, 'pro', 'last run');
    """)
    cur.executemany("INSERT INTO anet_team VALUES (%s,%s,%s,%s,%s)", [
        (NIKE, 16, "Nike", "OR", "Nike"), (HS_A, 4, "Herriman", "UT", "Herriman"),
        (HS_B, 4, "Lone Peak", "UT", "Lone Peak"), (CLUB31, 16, "Club 31", "CA", "Club 31"),
        (SMALL40, None, "Small 40", "CA", "Small 40"), (COLLEGE, 8, "Oregon", "OR", "Oregon")])
    tf, xc = [], []
    meets_tf = [(101, 1, 1, "Rome Diamond League", "Men", "1500m"),
                (102, 1, 1, "Oslo Diamond League", "Men", "Mile"),
                (103, 1, 1, "Paris Diamond League", "Men", "1500m"),
                (200, 1, 1, "HOKA Festival of Miles", "Pro Men", "Mile"),
                (300, 1, 1, "UHSAA 6A State Championships", "Boys", "1600m"),
                (500, 1, 1, "NCAA Division I Outdoor Championships", "Men", "1500m"),
                (600, 1, 1, "USATF Youth Regional", "Youth", "800m"),
                (610, 1, 1, "Club 31 Summer Series", "Open", "1500m")]
    for p in PROS:
        for m in (101, 102, 103):
            tf.append((p, m, 1, 1, "2026-06-01", None, "Nike", NIKE, f"Pro {p}"))
        tf.append((p, 200, 1, 1, "2026-07-18", None, "Nike", NIKE, f"Pro {p}"))
        tf.append((p, 200, 1, 1, "2025-07-18", None, "Nike", NIKE, f"Pro {p}"))
    for m in (101, 102, 103):
        tf.append((COLLEGIAN, m, 1, 1, "2026-06-01", "JR", "Oregon", COLLEGE, "C"))
    tf.append((COLLEGIAN, 500, 1, 1, "2026-06-12", "JR", "Oregon", COLLEGE, "C"))
    tf.append((COLLEGIAN, 500, 1, 1, "2024-06-12", "FR", "Oregon", COLLEGE, "C"))
    for k in KIDS:
        team = HS_A if k % 2 else HS_B
        tf.append((k, 300, 1, 1, "2026-05-23", "12", "Herriman" if k % 2 else "Lone Peak",
                   team, f"Kid {k}"))
        xc.append((k, 410, 1, "2025-10-15", "11", "Herriman" if k % 2 else "Lone Peak",
                   team, f"Kid {k}"))
    for i, p in enumerate(CLUB31_PEOPLE):
        tf.append((p, 610, 1, 1, "2026-07-01", None, "Club 31", CLUB31, f"Club {p}"))
        xc.append((p, 420, 1, "2025-09-01", None, "Club 31", CLUB31, f"Club {p}"))
    # a pro raced for Club 31: the club rule (n_pros) reads 03's table
    tf.append((PROS[0], 610, 1, 1, "2026-07-01", None, "Club 31", CLUB31, "Pro 100"))
    for p in range(450, 470):                      # Small 40: 20 people, no level
        xc.append((p, 430, 1, "2025-09-08", "10", "Small 40", SMALL40, f"S {p}"))
    # the youth meet: age bands beside banded grades, both sports
    bands = ["13-14", "15-16", "13-14", "11-12", "11-12", "9-10", "7-8", "17-18",
             "11-12", "15-16", "9-10", "11-12"]
    for p, g in zip(YOUTH, bands):
        tf.append((p, 600, 1, 1, "2026-06-20", g, "Youth TC", None, f"Y {p}"))
        xc.append((p, 440, 1, "2025-11-01", g, "Youth TC", None, f"Y {p}"))
    cur.executemany("INSERT INTO meets_tf VALUES (%s,%s,%s,%s,%s,%s)", meets_tf)
    cur.executemany("INSERT INTO meets VALUES (%s,%s,%s,%s)", [
        (410, 1, "Region 3 Championships", "Boys Varsity"),
        (420, 1, "Club 31 XC Open", "Open"),
        (430, 1, "Small 40 Invite", "Varsity"),
        (440, 1, "USATF Youth XC", "Youth")])
    cur.executemany("INSERT INTO meets_tfrrs VALUES (%s,%s)", [(600, "Youth Regional")])
    cur.executemany("""INSERT INTO results_tf (person_id, meet_id, div_id, event_id,
                       date, grade, school, team_id, athlete_name)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", tf)
    cur.executemany("""INSERT INTO results (person_id, meet_id, div_id, date,
                       grade, school, team_id, athlete_name)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", xc)
    people = sorted({r[0] for r in tf} | {r[0] for r in xc})
    cur.executemany("INSERT INTO athletes VALUES (%s,%s,%s)",
                    [(p, p, f"P {p}") for p in people])
    cur.execute("INSERT INTO grade_fix VALUES (301, 2025, '12', NULL, 'm', 'high')")


def _moves(cur, k=0):
    """What the 04a chain does to rows 03 reads: merges, mints, renames,
    pins. k varies the in-flight moves; k=0 is the final state."""
    if k % 2 == 0:                       # the final state, every time
        cur.execute("UPDATE results SET person_id = 204 WHERE person_id = 205")
        cur.execute("UPDATE results_tf SET person_id = 204 WHERE person_id = 205")
        cur.execute("UPDATE athletes SET person_id = 204 WHERE person_id = 205")
        cur.execute("UPDATE results_tf SET person_id = 9001 WHERE person_id = 101")
        cur.execute("UPDATE results SET person_id = 9002 WHERE person_id = 403")
        cur.execute("UPDATE results_tf SET person_id = 9002 WHERE person_id = 403")
        cur.execute("UPDATE results SET person_id = 9003 WHERE person_id = 460")
        cur.execute("UPDATE results SET athlete_name = upper(athlete_name)")
        cur.execute("UPDATE grade_fix SET grade = '11' WHERE person_id = 301")
        cur.execute("INSERT INTO person_redirect VALUES (205, 204), (101, 9001)")
    else:                                # a different, transient state
        cur.execute("UPDATE results_tf SET person_id = 101 WHERE person_id = 9001")
        cur.execute("UPDATE results_tf SET person_id = 103 WHERE person_id = 102")
        cur.execute("UPDATE results SET person_id = 300 WHERE person_id = 301")
        cur.execute("UPDATE results_tf SET person_id = 300 WHERE person_id = 301")
        cur.execute("DELETE FROM results_tf WHERE person_id = 106")


OUTPUTS = {
    "pro_athlete_season": "SELECT person_id, season, pro_races, round FROM pro_athlete_season",
    "ncaa_first_season": "SELECT person_id, first_season FROM ncaa_first_season",
    "age_band_div": "SELECT sport, meet_id, div_id, n_age, n_band, n_rows FROM age_band_div",
    "age_band_result": "SELECT sport, result_id, person_id, grade FROM age_band_result",
    "team_pool": ("SELECT team_id, kind, reason, n_athletes, n_rows, level, n_pros "
                  "FROM team_pool"),
    "team_pool_indexes": ("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                          "AND tablename IN ('team_pool', 'team_pool_new')"),
}


def _outputs(db):
    conn = _connect(db)
    out = {}
    with conn.cursor() as cur:
        for name, sql in OUTPUTS.items():
            cur.execute(sql)
            out[name] = sorted(cur.fetchall(), key=repr)
    conn.close()
    return out


STEPS = {"03_pro_flag": ["engine/pro_flag.py", "--skip-dist", "--write"],
         "03b_age_bands": ["engine/age_band_grades.py", "--write"],
         "03a_team_pool": ["engine/build_team_pool.py", "--write"]}


def _run(db, snapshot=None):
    """The 03 group as the pipeline runs it: 03 and 03b side by side, then 03a."""
    env = _env(db, snapshot)
    group = [subprocess.Popen([PY, "-u"] + STEPS[n], cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
             for n in ("03_pro_flag", "03b_age_bands")]
    logs = [p.communicate(timeout=600)[0] for p in group]
    for n, p, log in zip(("03_pro_flag", "03b_age_bands"), group, logs):
        assert p.returncode == 0, f"{n} failed:\n{log}"
    p = subprocess.run([PY, "-u"] + STEPS["03a_team_pool"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, f"03a failed:\n{p.stdout}\n{p.stderr}"
    return "\n".join(logs) + p.stdout


def _holder(db, tmp_path):
    snap = str(tmp_path / f"{db}.snapshot")
    release = str(tmp_path / f"{db}.release")
    proc = subprocess.Popen([PY, "-u", "scripts/snapshot_hold.py", snap,
                             "--release", release, "--max-seconds", "900",
                             "--dsn", _dsn(db)],
                            cwd=ROOT, env=_env(db), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    for _ in range(300):
        if os.path.exists(snap):
            break
        assert proc.poll() is None, proc.stdout.read()
        time.sleep(0.05)
    assert os.path.exists(snap)
    return proc, snap, release


def _release(proc, release):
    open(release, "w").close()
    out = proc.communicate(timeout=30)[0]
    assert proc.returncode == 0, out
    return out


@needs_pg
def test_03_beside_04a_writes_what_03_before_04a_wrote(tmp_path):
    # A: today's order
    _fresh("xcp_snap_a")
    conn = _connect("xcp_snap_a")
    with conn.cursor() as cur:
        _seed(cur)
    conn.commit()
    _run("xcp_snap_a")
    with conn.cursor() as cur:
        _moves(cur)
    conn.commit()
    conn.close()
    want = _outputs("xcp_snap_a")
    # the corpus exercises every script
    assert want["pro_athlete_season"] and want["ncaa_first_season"]
    assert want["age_band_result"] and want["age_band_div"]
    assert any(r[6] for r in want["team_pool"]), "no team read 03's pros"
    assert {r[0] for r in want["team_pool_indexes"]} == {"team_pool_pkey",
                                                         "team_pool_kind_idx"}

    # B: the snapshot, then 04a moving rows before AND while the 03 group runs
    _fresh("xcp_snap_b")
    conn = _connect("xcp_snap_b")
    with conn.cursor() as cur:
        _seed(cur)
    conn.commit()
    proc, snap, release = _holder("xcp_snap_b", tmp_path)
    with conn.cursor() as cur:
        _moves(cur)                                     # committed before
    conn.commit()
    stop = threading.Event()
    churn = {"n": 0}

    def mover():
        c = _connect("xcp_snap_b")
        k = 1
        while not stop.is_set():
            with c.cursor() as cur:
                _moves(cur, k)
            c.commit()
            churn["n"] += 1
            k += 1
            time.sleep(0.01)
        c.close()

    t = threading.Thread(target=mover)
    t.start()
    try:
        log = _run("xcp_snap_b", snapshot=snap)
    finally:
        stop.set()
        t.join()
    held = _release(proc, release)
    assert churn["n"] > 3, "the mover never ran beside the scripts"
    assert "released" in held
    got = _outputs("xcp_snap_b")
    for name in OUTPUTS:
        assert got[name] == want[name], f"{name} differs on the snapshot path"

    # C: the control -- the same moves, read live, change every output
    _fresh("xcp_snap_c")
    conn = _connect("xcp_snap_c")
    with conn.cursor() as cur:
        _seed(cur)
        _moves(cur)
    conn.commit()
    conn.close()
    _run("xcp_snap_c")
    live = _outputs("xcp_snap_c")
    for name in ("pro_athlete_season", "age_band_result", "team_pool"):
        assert live[name] != want[name], f"the moves do not reach {name}: no teeth"
    assert log


_INPROC = r'''
import os, sys, psycopg2, psycopg2.extras
sys.path.insert(0, "scripts")
import database as d
other = psycopg2.connect(os.environ["OTHER_DSN"]); other.autocommit = True
def bump(n):
    with other.cursor() as c:
        c.execute("UPDATE t SET v = %s", (n,))
def val(conn, **kw):
    with conn.cursor(**kw) as c:
        c.execute("SELECT v FROM t")
        r = c.fetchone()
        return r["v"] if isinstance(r, dict) else r[0]
with d.getConn() as conn:
    assert type(conn) is d.SnapshotConnection, type(conn)
    assert val(conn) == 1
    bump(2)
    assert val(conn) == 1                         # same transaction
    conn.commit()
    assert val(conn) == 1                         # committed, then a NEW one
    conn.rollback()
    assert val(conn, cursor_factory=psycopg2.extras.RealDictCursor) == 1
    conn.commit()
    with conn.cursor("named") as c:               # a server-side cursor
        c.execute("SELECT v FROM t"); assert c.fetchone()[0] == 1
    conn.commit()
    with conn.cursor() as c:                      # execute_values / COPY
        psycopg2.extras.execute_values(c, "SELECT v FROM t JOIN (VALUES %s) q(x) ON q.x = t.v", [(1,), (2,)])
        assert [r[0] for r in c.fetchall()] == [1]
    conn.commit()
    import io
    buf = io.StringIO()
    with conn.cursor() as c:
        c.copy_expert("COPY t TO STDOUT", buf)
    assert buf.getvalue().strip() == "1", buf.getvalue()
    conn.commit()
    with conn.cursor() as c:
        c.execute("SHOW transaction_isolation"); assert c.fetchone()[0] == "repeatable read"
    conn.commit()
    # writes inside the snapshot transaction are allowed (own tables)
    with conn.cursor() as c:
        c.execute("CREATE TABLE mine AS SELECT v FROM t");
    conn.commit()
    # autocommit cannot import a snapshot: an error, not a live read
    conn.autocommit = True
    try:
        val(conn); raise SystemExit("autocommit read did not fail")
    except RuntimeError:
        pass
    conn.autocommit = False
with d.getConn() as conn:                         # another pool connection
    assert val(conn) == 1
with d.liveConn() as conn:                        # the explicit way out
    assert val(conn) == 2
with d.getConn() as conn:                         # and back on the snapshot
    assert val(conn) == 1
    with conn.cursor() as c:
        d.shadowLive(c, "t")                       # a live copy, this txn only
        c.execute("SELECT v FROM t"); assert c.fetchone()[0] == 2
    conn.commit()
    assert val(conn) == 1
print("inproc ok")
'''


@needs_pg
def test_every_transaction_reads_the_snapshot_and_unset_reads_live(tmp_path):
    db = "xcp_snap_d"
    _fresh(db)
    conn = _connect(db)
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (v int); INSERT INTO t VALUES (1)")
    conn.commit()
    proc, snap, release = _holder(db, tmp_path)
    env = _env(db, snap)
    env["OTHER_DSN"] = _dsn(db)
    p = subprocess.run([PY, "-c", _INPROC], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "inproc ok" in p.stdout

    # unset: the plain class, and the live value
    plain = ("import sys; sys.path.insert(0, 'scripts'); import database as d, psycopg2\n"
             "with d.getConn() as c:\n"
             "    assert type(c) is psycopg2.extensions.connection, type(c)\n"
             "    cur = c.cursor(); assert type(cur) is psycopg2.extensions.cursor\n"
             "    cur.execute('SHOW transaction_isolation'); assert cur.fetchone()[0] == 'read committed'\n"
             "    cur.execute('SELECT v FROM t'); print(cur.fetchone()[0])\n")
    p = subprocess.run([PY, "-c", plain], cwd=ROOT, env=_env(db),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip().splitlines()[-1] == "2"

    # the holder gone, a new transaction on the snapshot fails loudly
    _release(proc, release)
    gone = ("import sys; sys.path.insert(0, 'scripts'); import database as d\n"
            "with d.getConn() as c:\n"
            "    c.cursor().execute('SELECT 1')\n")
    open(snap, "w").write(open(snap).read() if os.path.exists(snap) else "00000003-00000002-1")
    p = subprocess.run([PY, "-c", gone], cwd=ROOT, env=_env(db, snap),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    assert "snapshot" in (p.stdout + p.stderr).lower()


@needs_pg
def test_a_missing_snapshot_file_is_an_error(tmp_path):
    db = "xcp_snap_d"
    code = ("import sys; sys.path.insert(0, 'scripts'); import database as d\n"
            "with d.getConn() as c: pass\n")
    p = subprocess.run([PY, "-c", code], cwd=ROOT,
                       env=_env(db, str(tmp_path / "nope")),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    assert "cannot read the snapshot file" in p.stderr
