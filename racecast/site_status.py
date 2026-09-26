"""
site_status.py -- the owner's one-page answer to "is everything alright?"

  Served at /account/status (admins only; see accounts.isAdmin and
  XCP_ADMIN_EMAILS) and printed by scripts/print_status.py.

★ WHY (owner, 2026-09-25). Every check below was a separate command run by
  hand on the server after something looked wrong on the site: the pipeline
  summary, diag_tfrrs_identity, requeue_blank_athletes' dry run,
  slow_queries, peek_signups. This puts their answers on one page so a
  problem is seen before a reader finds it.

! EVERY BLOCK STANDS ALONE. Each database block runs under its own
  statement_timeout and its own error catch, so a slow count or a missing
  table costs that block, never the page. A block that failed says why.
! READ-ONLY, apart from resolveReport.
"""
import datetime
import glob
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_ROOT = os.environ.get("XCP_LOG_ROOT") or os.path.join(ROOT, "logs")
BLOCK_TIMEOUT_MS = 8000
TAIL_LINES = 12

_STEP_LINE = re.compile(
    r"^\s+(\S+) (ok|FAILED)(?: after)? \(?(\d+)s(?:, ([a-z ]+))?\)?")


def academicYear(today=None):
    today = today or datetime.date.today()
    return today.year if today.month >= 8 else today.year - 1


# ---- the pipeline, from its logs ---------------------------------------

def parseSummary(text):
    """[{"step", "ok", "seconds", "how"}] from a summary.log's lines."""
    out = []
    for line in (text or "").splitlines():
        m = _STEP_LINE.match(line)
        if m:
            out.append({"step": m.group(1), "ok": m.group(2) == "ok",
                        "seconds": int(m.group(3)), "how": m.group(4) or ""})
    return out


def _tail(path, n=TAIL_LINES):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return lines[-n:]
    except OSError:
        return []


def runDirs(log_root=LOG_ROOT):
    """Pipeline run directories, newest first (their names are timestamps)."""
    dirs = [d for d in glob.glob(os.path.join(log_root, "2*"))
            if os.path.isdir(d)]
    return sorted(dirs, reverse=True)


def pipelineRun(path, now=None):
    """One run: its finished steps, failed steps with the end of their log,
    and any step whose log exists but has no summary line -- running now,
    or killed mid-step."""
    now = now or time.time()
    try:
        with open(os.path.join(path, "summary.log"), encoding="utf-8",
                  errors="replace") as f:
            steps = parseSummary(f.read())
    except OSError:
        steps = []
    done = {s["step"] for s in steps}
    for s in steps:
        if not s["ok"]:
            s["tail"] = _tail(os.path.join(path, s["step"] + ".log"))
    open_steps = []
    for log in sorted(glob.glob(os.path.join(path, "*.log"))):
        name = os.path.basename(log)[:-4]
        if name == "summary" or name in done:
            continue
        open_steps.append({"step": name,
                           "idle_s": int(now - os.path.getmtime(log)),
                           "tail": _tail(log, 4)})
    started = None
    m = re.match(r"(\d{8})_(\d{6})", os.path.basename(path))
    if m:
        started = datetime.datetime.strptime(m.group(1) + m.group(2),
                                             "%Y%m%d%H%M%S")
    return {"dir": os.path.basename(path), "started": started,
            "steps": steps, "open": open_steps,
            "failed": [s["step"] for s in steps if not s["ok"]],
            "seconds": sum(s["seconds"] for s in steps
                           if "parallel" not in s["how"]
                           and "background" not in s["how"])}


def pipeline(log_root=LOG_ROOT, runs=6):
    dirs = runDirs(log_root)
    if not dirs:
        return {"error": f"no run logs under {log_root}"}
    latest = pipelineRun(dirs[0])
    recent = []
    for d in dirs[1:runs]:
        r = pipelineRun(d)
        row = {k: r[k] for k in ("dir", "started", "failed", "seconds")}
        row.update(n=len(r["steps"]), open=len(r["open"]))
        recent.append(row)
    return {"latest": latest, "recent": recent}


# ---- the database -------------------------------------------------------

def _block(conn, fn, timeout_ms=BLOCK_TIMEOUT_MS):
    """Run one block under a timeout (0 = none); its rows, or
    {"error": why}."""
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
            got = fn(cur)
        conn.rollback()
        return got
    except Exception as exc:                            # noqa: BLE001
        conn.rollback()
        msg = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        return {"error": f"{type(exc).__name__}: {msg}"[:200]}


_IDENTITY_SQL = """
    SELECT count(*), count(person_id),
           count(*) FILTER (WHERE person_id IS NULL AND native_id IS NOT NULL),
           count(normalized_time), count(speed_rating)
    FROM   {table}
    WHERE  source = 'tfrrs' AND date >= %s AND date < %s
"""


def identity(conn, seasons, timeout_ms=BLOCK_TIMEOUT_MS):
    """tfrrs rows per season: how many have a person, and are rated. The
    gap diag_tfrrs_identity measured, which link_tfrrs_rows closes."""
    def run(cur):
        out = []
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            for y in seasons:
                cur.execute(_IDENTITY_SQL.format(table=table),
                            (f"{y}-08-01", f"{y + 1}-08-01"))
                n, wp, nat, norm, rated = cur.fetchone()
                out.append({"sport": sport, "season": y, "rows": n,
                            "person": wp, "native_only": nat,
                            "normalized": norm, "rated": rated})
        return out
    return _block(conn, run, timeout_ms)


def rated(conn, season, timeout_ms=BLOCK_TIMEOUT_MS):
    """This season's rows and rated rows, per sport and source."""
    def run(cur):
        out = []
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            cur.execute(f"""
                SELECT source, count(*), count(speed_rating), max(date)
                FROM   {table} WHERE date >= %s
                GROUP  BY source ORDER BY source
            """, (f"{season}-08-01",))
            for source, n, r, last in cur.fetchall():
                out.append({"sport": sport, "source": source, "rows": n,
                            "rated": r, "latest": last})
        return out
    return _block(conn, run, timeout_ms)


def blankAthletes(conn, timeout_ms=BLOCK_TIMEOUT_MS):
    """anet athletes with no name on ANY of their rows -- the same count
    scripts/requeue_blank_athletes.py prints, so the two agree -- and the
    anet meets waiting to be scraped (requeued ones included)."""
    def run(cur):
        cur.execute("""
            SELECT count(*) FROM (
                SELECT athlete_id FROM athletes WHERE source = 'anet'
                GROUP  BY athlete_id
                HAVING bool_and(COALESCE(btrim(first_name), '') = ''
                                AND COALESCE(btrim(last_name), '') = '')) b
        """)
        n = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM meet_queue "
                    "WHERE source = 'anet' AND scraped = 0")
        return {"blank": n, "queued_meets": cur.fetchone()[0]}
    return _block(conn, run, timeout_ms)


def activeQueries(conn, min_seconds=5):
    """Every statement running longer than min_seconds, oldest first --
    what slow_queries.py shows, for the pipeline and the site alike."""
    def run(cur):
        cur.execute("""
            SELECT pid, COALESCE(application_name, ''), state,
                   extract(epoch FROM now() - query_start)::int,
                   COALESCE(wait_event_type || ':' || wait_event, ''),
                   left(regexp_replace(query, '\\s+', ' ', 'g'), 200)
            FROM   pg_stat_activity
            WHERE  pid <> pg_backend_pid() AND state <> 'idle'
              AND  datname = current_database()
              AND  now() - query_start > make_interval(secs => %s)
            ORDER  BY query_start
        """, (min_seconds,))
        return [{"pid": p, "app": a, "state": s, "seconds": sec,
                 "wait": w, "query": q}
                for p, a, s, sec, w, q in cur.fetchall()]
    return _block(conn, run)


def openReports(conn, limit=30):
    def run(cur):
        cur.execute("""
            SELECT report_id, created_at, kind, page, detail, email
            FROM   issue_reports WHERE NOT resolved
            ORDER  BY created_at DESC LIMIT %s
        """, (limit,))
        rows = [{"id": i, "at": at, "kind": k, "page": p, "detail": d,
                 "email": e} for i, at, k, p, d, e in cur.fetchall()]
        cur.execute("SELECT count(*) FROM issue_reports WHERE NOT resolved")
        return {"rows": rows, "open": cur.fetchone()[0]}
    return _block(conn, run)


def resolveReport(conn, report_id, note=None):
    with conn.cursor() as cur:
        cur.execute("UPDATE issue_reports SET resolved = true, "
                    "note = COALESCE(%s, note) WHERE report_id = %s",
                    (note, int(report_id)))
        n = cur.rowcount
    conn.commit()
    return n


# ★ THE SLOW COUNTS ARE A SNAPSHOT (owner's first run, 2026-09-26: both
#   tfrrs blocks hit the 8 s page timeout). Counting a season of results is
#   a scan of tens of millions of rows -- fine for a script, not for a page
#   load. scripts/print_status.py counts them with no time limit and saves
#   the answer here; the page shows the saved copy and how old it is. The
#   pipeline, the running queries and the reports stay live.
HEAVY = ("identity", "rated", "blank")
_SNAP_DDL = """CREATE TABLE IF NOT EXISTS site_status_snapshot (
                   taken_at timestamptz NOT NULL DEFAULT now(),
                   data     jsonb NOT NULL)"""


def heavy(conn, season, timeout_ms=0):
    return {"identity": identity(conn, [season - 1, season], timeout_ms),
            "rated": rated(conn, season, timeout_ms),
            "blank": blankAthletes(conn, timeout_ms)}


def saveSnapshot(conn, data):
    import json
    with conn.cursor() as cur:
        cur.execute(_SNAP_DDL)
        cur.execute("INSERT INTO site_status_snapshot (data) VALUES (%s)",
                    (json.dumps(data, default=str),))
        # the last few are plenty; this is a status page, not a history
        cur.execute("""DELETE FROM site_status_snapshot WHERE taken_at <
                       (SELECT min(taken_at) FROM (SELECT taken_at
                        FROM site_status_snapshot ORDER BY taken_at DESC
                        LIMIT 20) k)""")
    conn.commit()


def loadSnapshot(conn):
    """(taken_at, {identity, rated, blank}) of the newest snapshot, or
    (None, None)."""
    def run(cur):
        cur.execute("SELECT to_regclass('public.site_status_snapshot')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT taken_at, data FROM site_status_snapshot "
                    "ORDER BY taken_at DESC LIMIT 1")
        return cur.fetchone()
    got = _block(conn, run)
    if not got or isinstance(got, dict):
        return None, None
    return got[0], got[1]


def gather(conn, log_root=LOG_ROOT, live_heavy=False):
    """Everything the page shows. live_heavy counts the slow blocks now
    (with no time limit) instead of reading the last snapshot."""
    ay = academicYear()
    out = {"now": datetime.datetime.now(),
           "season": ay,
           "pipeline": pipeline(log_root),
           "queries": activeQueries(conn),
           "reports": openReports(conn),
           "snapshot_at": None}
    if live_heavy:
        out.update(heavy(conn, ay))
    else:
        at, data = loadSnapshot(conn)
        missing = {"error": "not counted yet -- run scripts/print_status.py"}
        for k in HEAVY:
            out[k] = (data or {}).get(k) or missing
        out["snapshot_at"] = at
    return out


def ago(seconds):
    """3s / 4m / 2h 5m / 3d -- how long, in the fewest honest words."""
    s = int(seconds or 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d"
