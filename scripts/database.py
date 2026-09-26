# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database
# Date: 6/6/2026
# File Title: database.py
# Purpose: Creates and manages the PostgreSQL database that stores scraped
#          meet results. Replaces the old SQLite version. All functions
#          use the same interface as before so scraper code doesn't change.

import re
import sys
import json
import psycopg2
import psycopg2.extras
import psycopg2.pool
import time
import random
from contextlib import contextmanager
from config import PG_CONFIG
import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Connection Pool
# ─────────────────────────────────────────────────────────────────────────────

# Why a pool?
# Without a pool, every save function opened a TCP connection to Cloud SQL,
# ran one query, then closed it. For a 100-result meet that's ~200 connection
# handshakes. At even 20ms each that's 4 seconds of pure overhead per meet.
# With a pool we open connections ONCE at startup and reuse them forever.

# MIN_CONN: connections opened immediately at startup.
# MAX_CONN: hard ceiling. If all connections are in use and another is
#           requested, psycopg2 raises PoolError instead of hanging forever.
#           Sized for 25 VM sessions + overhead. Easy to bump when scaling.
 
# ★ SIZED PER PROCESS, FROM THE ENVIRONMENT WHERE IT MATTERS. The site runs
#   eight gunicorn workers, each with its own pool; 8 x 250 against a
#   Postgres max_connections of 100 is how "sorry, too many clients" happens
#   (2026-09-07). The service unit sets XCP_DB_MAX_CONN small; the pipeline
#   keeps the default.
import os
MIN_CONN = int(os.environ.get("XCP_DB_MIN_CONN") or 5)
MAX_CONN = int(os.environ.get("XCP_DB_MAX_CONN") or 250)

# We add connect_timeout to PG_CONFIG here so it applies to every connection
# the pool opens. Without it, if Cloud SQL is slow to accept, psycopg2
# waits forever and the session hangs silently.
# We build a new dict so the original PG_CONFIG in config.py isn't mutated.
_PG_CONFIG_WITH_TIMEOUT = {**PG_CONFIG, "connect_timeout": 10}

# ★ THE SITE'S CONNECTIONS FAIL FAST; THE PIPELINE'S DO NOT. A page request
#   that queues behind a pipeline lock used to wait until gunicorn killed
#   the worker at 60 s, and the Postgres side of that request lived on as
#   an orphan still waiting; ninety of those filled max_connections
#   (2026-09-07). With lock_timeout a site query gives up in seconds, and
#   with statement_timeout under gunicorn's timeout the backend dies with
#   the worker. Both come from the environment, set only in the service
#   unit, because a pipeline statement legitimately runs for minutes.
#   application_name labels the site's rows in pg_stat_activity.
_opts = []
for _setting, _env in (("lock_timeout", "XCP_DB_LOCK_TIMEOUT_MS"),
                       ("statement_timeout", "XCP_DB_STATEMENT_TIMEOUT_MS")):
    _v = os.environ.get(_env)
    if _v and _v.isdigit():
        _opts.append(f"-c {_setting}={int(_v)}")
if _opts:
    _PG_CONFIG_WITH_TIMEOUT["options"] = " ".join(_opts)
_PG_CONFIG_WITH_TIMEOUT["application_name"] = os.environ.get("XCP_DB_APP") or "xcp"

# ★ THE PIPELINE YIELDS TO THE SITE INSIDE POSTGRES TOO (owner, 2026-09-14:
#   "make it so that the pipeline stops making the website super slow,
#   especially the steps past the solve"). nice and ionice on the Python
#   process (deploy/run_pipeline.sh) never touched the work that actually
#   hurt: the Postgres BACKENDS doing the pipeline's COPYs, sorts and index
#   builds run as the postgres user at normal priority, and the builders
#   asked for 2-8 GB of work memory and 4-6 parallel workers per statement
#   -- so one index build on the 61.6M-row boards table took every core
#   and evicted the site's pages from cache while a reader waited.
#
#   With XCP_DB_QUIET=1 (run_pipeline.sh sets it) every connection this
#   pool hands out is capped on the way out: no parallel workers, modest
#   work memory, and -- where the server is local and the OS allows it --
#   the backend process itself reniced and ionice'd like the Python that
#   drives it. The builders' own SET statements go through dbSetting() so
#   they cannot ask for more than the cap. The site's service unit never
#   sets the variable, so the site's connections are untouched.
_QUIET_STRICT = {
    # ! 256MB, NOT 64MB (2026-09-15): a smaller sort budget makes a
    #   61.6M-row GROUP BY SPILL to disk, and temp-file I/O on the site's
    #   disk is exactly what the mode exists to prevent. One process, no
    #   parallel workers, so a few hundred MB per sort node is bounded.
    "work_mem": "256MB",
    "maintenance_work_mem": "512MB",
    "max_parallel_workers_per_gather": "0",
    "max_parallel_maintenance_workers": "0",
    # the pipeline's writes are rebuilds; losing the last commit to a crash
    # costs a rerun, and skipping the fsync wait keeps the WAL off the
    # site's disk queue
    "synchronous_commit": "off",
}
# ★★ BALANCED IS THE DEFAULT NOW (owner, 2026-09-26: "speed up every step in
#    pipeline possible"). The strict caps made every index build serial at
#    512MB, every scan single-worker and every builder one job wide -- the
#    last full run took about 30 hours. They were set while every page
#    request reached Postgres; the site is now served from Cloudflare's edge
#    cache and the scraper that sent 1.5M requests a day is blocked, so the
#    database has room. XCP_DB_QUIET=strict puts the old caps back.
# ★ cursor_tuple_fraction = 1.0 ON EVERY PIPELINE CONNECTION. A named
#   (streaming) cursor is planned for its first 10% of rows by default,
#   which picks nested-loop index probes -- a dozen per row over 61.6M rows
#   in the ranking build. Every pipeline cursor reads its query to the end,
#   so plan for the end (panels.py found and fixed this for itself).
_QUIET_SETTINGS = {
    "work_mem": "512MB",
    "maintenance_work_mem": "2GB",
    "max_parallel_workers_per_gather": "2",
    "max_parallel_maintenance_workers": "2",
    "synchronous_commit": "off",
    "cursor_tuple_fraction": "1.0",
}


def _caps():
    """The caps in force: strict when XCP_DB_QUIET=strict, else balanced.
    Read per call, so a process that sets the variable late still gets it."""
    if os.environ.get("XCP_DB_QUIET", "0") == "strict":
        return dict(_QUIET_STRICT, cursor_tuple_fraction="1.0")
    return _QUIET_SETTINGS
_QUIET_NICE = int(os.environ.get("XCP_DB_QUIET_NICE") or 10)


def dbQuiet():
    """Is the quiet (site-first) mode on for this process? '1' is the
    balanced caps, 'strict' the old ones."""
    return os.environ.get("XCP_DB_QUIET", "0") in ("1", "strict")


def dbSetting(name, default):
    """The value a builder may SET for `name`: its own default, or the
    quiet cap when quiet mode is on and the cap is stricter by intent
    (the caps are absolute in quiet mode; a builder never out-asks it)."""
    return _caps().get(name, default) if dbQuiet() else default


def dbJobs(default, quiet=1):
    """How many database-heavy workers a builder may run side by side:
    one in strict mode, up to two in the balanced default."""
    if not dbQuiet():
        return default
    if os.environ.get("XCP_DB_QUIET") == "strict":
        return quiet
    return max(quiet, min(default, 2))


_TUNED = {}          # id(conn) -> conn, connections already capped
_QUIET_SAID = False


def _localServer():
    host = str(PG_CONFIG.get("host") or "")
    return host in ("", "localhost", "127.0.0.1", "::1") or host.startswith("/")


def _quietTune(conn):
    """Cap one connection (settings; then the backend's own priority,
    best effort). Every failure is survived: a managed server may refuse
    a SET, and renicing another user's process needs a capability the
    box may not grant."""
    global _QUIET_SAID
    applied, refused, niced = [], [], []
    with conn.cursor() as cur:
        for name, value in _caps().items():
            try:
                cur.execute(f"SET {name} = %s", (value,))
                applied.append(f"{name}={value}")
            except Exception:                                # noqa: BLE001
                conn.rollback()
                refused.append(name)
        try:
            cur.execute("SET application_name = 'xcp-pipeline'")
        except Exception:                                    # noqa: BLE001
            conn.rollback()
        pid = None
        if _localServer():
            try:
                cur.execute("SELECT pg_backend_pid()")
                pid = int(cur.fetchone()[0])
            except Exception:                                # noqa: BLE001
                conn.rollback()
    conn.commit()
    if pid:
        try:
            os.setpriority(os.PRIO_PROCESS, pid, _QUIET_NICE)
            niced.append(f"nice {_QUIET_NICE}")
        except (OSError, AttributeError):
            niced.append("nice denied")
        try:
            import shutil
            import subprocess
            if shutil.which("ionice"):
                rc = subprocess.run(["ionice", "-c2", "-n7", "-p", str(pid)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    check=False).returncode
                niced.append("ionice ok" if rc == 0 else "ionice denied")
        except Exception:                                    # noqa: BLE001
            niced.append("ionice failed")
    if not _QUIET_SAID:
        _QUIET_SAID = True
        print(f"[DB] quiet mode: {', '.join(applied) or 'no setting applied'}"
              + (f"; refused: {', '.join(refused)}" if refused else "")
              + (f"; backend {', '.join(niced)}" if niced else "; backend not local, priority untouched"))
        if any("denied" in n for n in niced):
            print("[DB] quiet mode: the backend keeps normal priority (renice of the postgres "
                  "user's process needs CAP_SYS_NICE; run the pipeline as root or grant it, "
                  "or ALTER ROLE ... SET the caps server-side). The SETs above still apply.")


# Module-level pool — created once when database.py is first imported.
# Every script that does `from database import ...` shares this same pool.
# None until initPool() is called explicitly. This is just a 
# list of open connections. It tracks who is using a connection.
_pool = None

# initPool
# Purpose: Creates the connection pool. Must be called once at startup.
#          Safe to call multiple times - if pool exists it won't do anything.
# Arguments: None.
# Output: None.
def initPool():
    global _pool

    # Giuard: don't create a second pool if one already exists.
    if _pool is not None:
        return
    
    try:
        # Creates a pool with at least MIN_CONN connections and less than MAX_CONN
        # connections. Other functions can take/open the connections as they please
        # within these bounds. PG_CONFIG_WITH_TIMEOUT is where the pool should
        # connect to and how long it should try before giving up connecting. Opens
        # 5 connections in the pool instantly.
        _pool = psycopg2.pool.ThreadedConnectionPool(
            MIN_CONN,
            MAX_CONN,
            **_PG_CONFIG_WITH_TIMEOUT
        )
        print(f"[DB] Connection pool created ({MIN_CONN}-{MAX_CONN} connections)")
    except Exception as e:
        # Pool creation failure is fatal — nothing can run without it.
        raise RuntimeError(f"[DB] Failed to create connection pool: {e}")

# closePool
# Purpose: Closes all connections in the pool.
# Arguments: None.
# Purpose: None.
def closePool():
    global _pool

    # Closes all connections in the pool.
    if _pool is not None:
        _pool.closeall()
        _pool = None
        print("[DB] Connection pool closed")

# ─────────────────────────────────────────────────────────────────────────────
# Connection context manager
# ─────────────────────────────────────────────────────────────────────────────

# getConn
# Purpose: Context manager that checks out a connection from the pool,
#          yields it to the caller, then returns it to the pool when the
#          with block exits — even if an exception was raised inside.
#
# Usage:
#   with getConn() as conn:
#       saveMeet(conn, meet_info, div)
#       saveResult(conn, result, meet_info)
#   # conn is automatically returned to pool here
#
# Why a context manager?
# Without it, every caller would need to remember to call putconn() after
# use. If an exception fires mid-meet and the caller forgets, the connection
# leaks. With a context manager, the finally block guarantees return.
#
# Why not just call pool.getconn() directly?
# Because then the caller owns the return responsibility. One missed putconn
# leaks a connection slot. When MAX_CONN slots are all leaked, the pool
# raises PoolError and the scraper dies. The context manager makes leaks
# impossible by design.
#
# Arguments: None.
# Output: Yields a psycopg2 connection object.
@contextmanager
def getConn():
    """A live connection from the pool, returned when the block ends.

    ★ CHECKED ON THE WAY OUT, DISCARDED ON THE WAY BACK (2026-09-07). The
      pool never noticed a connection whose server side had died (a
      terminated backend, a Postgres restart), so every request that drew
      that one failed with "connection already closed" until the service
      was restarted: the athlete page was a 500 while the rest of the site
      worked. One round trip before handing a connection out finds a dead
      socket, and a connection that broke inside the block is closed rather
      than put back."""
    if _pool is None:
        initPool()
    conn = None
    for _attempt in range(3):
        cand = _pool.getconn()
        if cand.closed:
            _pool.putconn(cand, close=True)
            continue
        try:
            with cand.cursor() as probe:
                probe.execute("SELECT 1")
            cand.rollback()
            conn = cand
            break
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            _pool.putconn(cand, close=True)
    if conn is None:
        raise RuntimeError("[DB] no live connection in the pool after three tries")
    if dbQuiet() and _TUNED.get(id(conn)) is not conn:
        try:
            _quietTune(conn)
        except Exception as exc:                             # noqa: BLE001
            conn.rollback()
            print(f"[DB] quiet mode: could not tune a connection ({str(exc).splitlines()[0]})")
        _TUNED[id(conn)] = conn

    broken = False
    try:
        yield conn
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        broken = True
        raise
    except Exception:
        try:
            conn.rollback()      # the caller raised: leave no open transaction
        except Exception:
            broken = True
        raise
    finally:
        if not broken:
            try:
                conn.rollback()  # no open or aborted txn rides back to the pool
            except Exception:
                broken = True
        # a broken connection is closed and dropped; the pool opens a fresh
        # one on the next request instead of handing the corpse out again
        _pool.putconn(conn, close=broken or conn.closed)

# ─────────────────────────────────────────────────────────────────────────────
# Write helpers
# ─────────────────────────────────────────────────────────────────────────────

# executeWithRetry
# Purpose: Wraps a single cursor.execute() call with retry logic. If Postgres
#          says the connection is busy or locked, waits and retries.
#          Same interface as the SQLite version so callers don't change.
# Arguments:
#           cursor: psycopg2 cursor to execute on.
#           sql: SQL string with %s placeholders.
#           params: tuple of values to substitute.
#           retries: how many times to retry on failure.
# Output: None. Raises on exhausted retries.
def executeWithRetry(cursor, sql: str, params: tuple, retries: int = 5):
    for attempt in range(retries):
        try:
            cursor.execute(sql, params)
            return
        except psycopg2.OperationalError as e:
            if attempt < retries - 1:
                # Wait a bit longer each retry — same backoff as SQLite version.
                time.sleep(0.05 + (attempt * 0.05) + random.uniform(0, 0.05))
            else:
                raise

# _flag
# Purpose: API boolean-ish -> INTEGER flag column (is_relay/is_indoor style).
#          None stays None so a MISSING key reads as "unknown", not a fake 0.
# Arguments: value: result.get("SomeFlag").
# Output: 1 truthy / 0 present-falsy / None absent.
def _flag(value):
    return None if value is None else (1 if value else 0)
 
# _videoCount
# Purpose: Pulls the video count out of a result's MediaCount, which is {} or
#          {"videos": N}. Defensive so a missing/oddly-typed value -> 0.
# Arguments: media_count: result.get("MediaCount").
# Output: integer video count (0 if none).
def _videoCount(media_count):
    if isinstance(media_count, dict):
        return media_count.get("videos", 0)
    return 0

# _toInt
# Purpose: Best-effort convert an API place/score value to int for an INTEGER
#          column, SALVAGING the leading number rather than discarding it. The
#          place arrives as a string and can carry a trailing marker: a tie
#          ("1T"), a display dot ("1."), etc. We keep the numeric part ("1T" ->
#          1, "1." -> 1) and only fall back to None when there's no leading
#          number at all ("DNF", "-", "", None) — so a tied athlete still gets
#          their real place instead of NULL. (The tie MARKER itself isn't
#          preserved here — that was the Option-B path we chose not to take.)
# Arguments:
#           value:
#               Raw value off a result dict — an int, a str like "1" / "1T", or
#               None.
# Output:
#           The leading integer when one exists; None otherwise.
def _toInt(value):
    if value is None:
        return None
    # Already an int (or int-like) — hand it straight back.
    if isinstance(value, int):
        return value
 
    s = str(value).strip()
    if not s:
        return None
    
    # Walk the leading characters, collecting digits until the first non-digit.
    # "1T" -> "1", "12." -> "12", "DNF" -> "" (no leading digit).
    lead = ""
    for ch in s:
        if ch.isdigit():
            lead += ch
        else:
            break
 
    return int(lead) if lead else None

# _toReal
# Purpose: Best-effort convert an API value to float for a REAL column. The
#          payload sends some numeric fields as strings, and crucially sends
#          EMPTY STRINGS ("") for missing values — "" cannot coerce to REAL and
#          crashes the whole execute_values batch (this is what killed meet
#          599825). Returns None for "", None, or any non-numeric value.
# Arguments:
#           value:
#               Raw value off a result dict — a float, an int, a numeric string
#               ("1.4"), an empty string, or None.
# Output:
#           float(value) when it parses; None otherwise.
def _toReal(value):
    if value is None:
        return None
    # Already numeric (and not a bool, which is an int subclass we don't want).
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None
    
# _teamIdOrNone
# Purpose: anet writes TeamID 0 for an unattached / no-team entry. Zero is not
#          an id, and anything that reads team_id as "which school is this"
#          must not see one -- a rule the pool and school-identity work leans
#          on, so it is enforced at the write instead of at every read.
# Arguments:
#           value: the raw TeamID from an anet payload.
# Output:   the int id, or None for 0 / missing / unparseable.
def _teamIdOrNone(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n or None


# _resolveSchool
# Purpose: The single source of truth for "what school is this row?". Both the
#          athlete insert and the result insert must use this so their school
#          strings always agree (the composite FK depends on them matching).
#          Mirrors the result path's existing `SchoolName or TeamName`.
# Arguments:
#           obj:
#               a raw result dict OR a buildAthleteDict output — both expose
#               SchoolName (and results also TeamName).
# Output:
#           The resolved school string, or None if neither field is set.
def _resolveSchool(obj):
    return obj.get("SchoolName") or obj.get("TeamName")



# ─────────────────────────────────────────────────────────────────────────────
# Table creation
# ─────────────────────────────────────────────────────────────────────────────

# ===================================================================== #
#  EVERY COLUMN THE DDL DECLARES, ON EVERY DATABASE                      #
# ===================================================================== #
#
# ⚠⚠ "CREATE TABLE IF NOT EXISTS" NEVER ADDS A COLUMN. A table made by last
#    season's code keeps last season's shape for ever, and the first INSERT
#    naming a newer column dies with UndefinedColumn -- on the server,
#    mid-run. THREE TIMES IN ONE DAY (2026-09-17):
#
#      results_tf.team_slug   killed link_tfrrs_to_anet, and silently
#                             degraded anet_teams' crest levels for weeks
#      results.status         would have killed whichever scraper ran first
#      meets_tf.venue_name    killed the venue backfill -- and saveMeetTF
#                             INSERTs that column, so the anet TRACK SCRAPE
#                             would have died on it too
#
#    The pattern each time: a column added to the CREATE TABLE text and to an
#    INSERT, with no hand-written _migrate* beside it. The hand-written ones
#    are the bug -- they are a thing to remember, and they get forgotten.
#
# ★ SO THE ALTERs ARE DERIVED FROM THE DDL ITSELF, which cannot drift from
#   it: add a column to a CREATE TABLE and it appears on old databases too.
#   scrape_school_logos.ensureTable has done exactly this for months and has
#   never once produced this failure; this is that idea, brought to the
#   tables everything else depends on.
#
# ! A NOT NULL WITH NO DEFAULT IS RELAXED for the ALTER, because Postgres
#   cannot add one to a table that already has rows. The column arrives
#   nullable; the DDL still states the intent for a fresh database.
#
# ! AND IT NEVER TOUCHES AN EXISTING COLUMN. ADD COLUMN IF NOT EXISTS only
#   adds; no type is changed, no default applied, no data rewritten.
_NOT_A_COLUMN = ("primary", "unique", "foreign", "check", "constraint",
                 "exclude", "like", "--")

# ⚠ IT CAPTURES THE NAME AND THE OPENING BRACKET, AND NOTHING MORE. The first
#   version ended `\((.*)` under re.S, which is greedy: the first CREATE TABLE
#   swallowed the rest of the file, finditer returned ONE match, and six of
#   _createCoreTables' seven tables were invisible. _ddlColumns stops at its
#   own closing bracket, so handing it everything after the `(` is enough.
_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.I)


def _ddlColumns(body):
    """[(name, spec)] from the inside of a CREATE TABLE.

    ⚠ COMMENTS COME OUT FIRST, BEFORE THE COMMA SPLIT. This project documents
      a column on the lines ABOVE it, and that prose contains commas --
      "(owner, 2026-09-16: ...)". Splitting on commas first therefore shreds
      one column's entry into fragments of English, and the first version of
      this function duly reported meets_tf as having columns called 'AND',
      'so', 'it' and 'for', with no venue_name. Which sent me hunting for a
      missing DDL that was never missing.

    ! THE COMMA SPLIT IS DEPTH-AWARE, so numeric(4,1) survives it.
    """
    clean = "\n".join(ln.split("--")[0] for ln in (body or "").splitlines())

    parts, depth, cur_ = [], 0, ""
    for ch in clean:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break                      # the CREATE TABLE's own closer
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur_)
            cur_ = ""
        else:
            cur_ += ch
    parts.append(cur_)

    out = []
    for part in parts:
        bits = " ".join(part.split()).strip()
        if not bits or bits.split()[0].lower() in _NOT_A_COLUMN:
            continue
        name, _, spec = bits.partition(" ")
        if spec.strip() and name.isidentifier():
            out.append((name, spec.strip()))
    return out


def _relaxed(spec):
    """The column spec as an ALTER can use it: no NOT NULL without a DEFAULT,
    no PRIMARY KEY (an existing table already has one)."""
    out = re.sub(r"\bPRIMARY\s+KEY\b", "", spec, flags=re.I)
    if re.search(r"\bDEFAULT\b", out, re.I) is None:
        out = re.sub(r"\bNOT\s+NULL\b", "", out, flags=re.I)
    return " ".join(out.split())


# ⚠⚠ IT ASKS BEFORE IT ALTERS, AND THAT IS NOT AN OPTIMISATION (owner,
#    2026-09-17: two instances "both hung they're not gonna run"). The first
#    version issued ADD COLUMN IF NOT EXISTS for all 98 columns every time,
#    and `IF NOT EXISTS` still takes an ACCESS EXCLUSIVE LOCK when the column
#    is already there and nothing changes. Three mistakes compounding:
#
#      1. 98 unconditional ALTERs, ~98 exclusive locks, for a no-op;
#      2. ensureCoreColumns committed ONCE at the end, so it HELD those locks
#         on twelve tables at once -- including results (39M) and results_tf
#         (191M) -- for the whole run;
#      3. no lock_timeout, so it waited for ever behind any open reader WHILE
#         HOLDING exclusive locks, and everything touching those tables queued
#         behind it. A second instance then queued behind the first.
#
#    That is not a slow migration, it is a site outage with a progress bar.
#
# ★ SO: read information_schema first (a catalogue query, no lock at all), and
#   take a lock ONLY for a column that is genuinely missing. The common case
#   -- every column already present -- now touches nothing and finishes in
#   milliseconds.
#
# ! AND A SHORT lock_timeout, SO IT YIELDS. If a table is busy the ALTER gives
#   up in seconds and says which one; it never becomes the thing everything
#   else is waiting on. The column is added on the next run instead.
_LOCK_TIMEOUT = "5s"


# ★ ONE WAY TO PUT A REBUILT TABLE LIVE (sweep, 2026-09-26). A bare
#   `DROP TABLE x; ALTER TABLE x_new RENAME TO x` in a pipeline connection
#   (which has no lock_timeout) waits for ever behind any page still reading
#   x -- and every page query that arrives after it queues behind the DROP
#   and fails at the site's 5 s lock_timeout. Pages errored for as long as
#   one slow reader lived.
# ! SO: commit whatever came before (never hold the new table's build locks
#   into the swap), then try the swap under a SHORT lock_timeout and, when a
#   reader is in the way, give up the lock, wait, and try again. The pages
#   queue behind it for at most lock_ms at a time.
# ! AND THE INDEXES KEEP THEIR NAMES. x_new's indexes (x_new_pkey, ...) are
#   renamed to x_... so the next run's x_new can use the same names again.
def swapTable(conn, table, new, lock_ms=3000, tries=40, pause=3.0,
              verbose=True):
    """Replace `table` with `new` (DROP + RENAME) in one short transaction,
    retrying while readers hold the old table. Returns the attempts used."""
    from psycopg2 import errors as _pgerr
    conn.commit()
    for attempt in range(1, tries + 1):
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL lock_timeout = '{int(lock_ms)}ms'")
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                cur.execute(f"ALTER TABLE {new} RENAME TO {table}")
                cur.execute("""SELECT indexname FROM pg_indexes
                               WHERE schemaname = 'public' AND tablename = %s
                                 AND indexname LIKE %s""",
                            (table, new.replace("_", r"\_") + r"\_%"))
                for (ix,) in cur.fetchall():
                    want = table + ix[len(new):]
                    cur.execute("SELECT to_regclass(%s)", (f"public.{want}",))
                    if cur.fetchone()[0] is None:
                        cur.execute(f'ALTER INDEX "{ix}" RENAME TO "{want}"')
            conn.commit()
            if verbose and attempt > 1:
                print(f"[DB] {table}: swapped on attempt {attempt}", flush=True)
            return attempt
        except _pgerr.LockNotAvailable:
            conn.rollback()
            if verbose:
                print(f"[DB] {table}: busy, swap attempt {attempt}/{tries} "
                      f"yielded; retrying in {pause:.0f}s", flush=True)
            time.sleep(pause)
    raise RuntimeError(f"{table}: could not take the swap lock in {tries} "
                       f"tries; {new} is built and left in place")


def _liveColumns(cursor, table):
    """{column} the live table actually has. A catalogue read: no locks."""
    cursor.execute("""SELECT lower(column_name)
                      FROM   information_schema.columns
                      WHERE  table_schema = 'public' AND table_name = %s""",
                   (table,))
    return {r[0] for r in cursor.fetchall()}


def ddlPlan(cursor, ddl):
    """[(table, [(column, spec), ...])] -- what is genuinely missing. Read
    only: this is what makes a no-op run cost nothing."""
    plan = []
    text = ddl or ""
    for m in _CREATE_RE.finditer(text):
        table = m.group(1)
        cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cursor.fetchone()[0] is None:
            continue                       # brand new; the CREATE made it whole
        have = _liveColumns(cursor, table)
        want = [(n, sp) for n, sp in _ddlColumns(text[m.end():])
                if n.lower() not in have]
        if want:
            plan.append((table, want))
    return plan


def ensureDdlColumns(cursor, ddl, verbose=False):
    """Add whatever columns `ddl` declares and the live tables lack. Returns
    [(table, column)] added.

    ! ONE LOCK PER MISSING COLUMN, AND NONE AT ALL WHEN NOTHING IS MISSING.
      The caller should commit after each table -- see ensureCoreColumns --
      so exclusive locks are never held across tables.
    """
    added = []
    for table, want in ddlPlan(cursor, ddl):
        for name, spec in want:
            try:
                cursor.execute("SAVEPOINT ddlcol")
                cursor.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
                cursor.execute(
                    f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS '
                    f'"{name}" {_relaxed(spec)}')
                cursor.execute("RELEASE SAVEPOINT ddlcol")
            except Exception as exc:                          # noqa: BLE001
                cursor.execute("ROLLBACK TO SAVEPOINT ddlcol")
                # ⚠ SAY IT EVEN WHEN QUIET. A column skipped because the table
                #   was busy is a column the next INSERT will die on, and
                #   silence is how that becomes a mystery at 3am.
                print(f"[DB] could not add {table}.{name}: "
                      f"{type(exc).__name__} {exc}")
                continue
            added.append((table, name))
            if verbose:
                print(f"[DB]   + {table}.{name}", flush=True)
    return added


# ★ AND THE COLUMNS THE CODE WRITES THAT THE TABLE DOES NOT HAVE. The derived
#   ALTERs above can only add what the DDL DECLARES -- and the recurring fault
#   is a column added to an INSERT and never to the CREATE TABLE. meets_tf
#   INSERTs track_type, track_length, ustfccca_id, division, level_mask,
#   source and id_system, and its DDL mentions none of them.
#
# ! SO THIS REPORTS RATHER THAN ALTERS. A column the DDL never declared has no
#   type to add it with, and guessing one is how a TEXT column becomes a
#   BIGINT nobody can write to. It names them, loudly, at start-up -- which is
#   the difference between a five-minute fix and UndefinedColumn eleven hours
#   into a scrape.
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.I | re.S)


def _insertColumns(text):
    """{table: {column, ...}} for every INSERT INTO ... (cols) in `text`."""
    out = {}
    for m in _INSERT_RE.finditer(text or ""):
        # ! COMMENTS OUT PER LINE, BEFORE THE COMMA SPLIT -- the same trap
        #   _ddlColumns fell into. "(a, -- why\n b, c)" split on commas first
        #   gives " -- why\n b", whose text before the `--` is empty, and the
        #   column is silently dropped.
        body = "\n".join(ln.split("--")[0] for ln in m.group(2).splitlines())
        cols = set()
        for raw in body.split(","):
            name = " ".join(raw.split()).strip().strip('"')
            if name.isidentifier():
                cols.add(name.lower())
        if cols:
            out.setdefault(m.group(1).lower(), set()).update(cols)
    return out


def auditInsertColumns(cursor, sources=None, verbose=True):
    """[(table, column)] the code INSERTs and the live table lacks."""
    if sources is None:
        import inspect
        sources = [inspect.getsource(sys.modules[__name__])]
    wanted = {}
    for text in sources:
        for table, cols in _insertColumns(text).items():
            wanted.setdefault(table, set()).update(cols)

    missing = []
    for table, cols in sorted(wanted.items()):
        cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cursor.fetchone()[0] is None:
            continue
        cursor.execute("""SELECT lower(column_name)
                          FROM   information_schema.columns
                          WHERE  table_schema = 'public' AND table_name = %s""",
                       (table,))
        have = {r[0] for r in cursor.fetchall()}
        for col in sorted(cols - have):
            missing.append((table, col))
    if missing and verbose:
        print("[DB] ⚠ THE CODE WRITES COLUMNS THIS DATABASE DOES NOT HAVE. "
              "An INSERT naming one dies mid-run:")
        for table, col in missing:
            print(f"[DB]     {table}.{col}")
        print("[DB]   Add them to the CREATE TABLE in database.py (they are "
              "then created automatically) or ALTER them in by hand.")
    return missing


# ! THE DDL IS READ FROM THE CREATOR FUNCTIONS' OWN SOURCE. It lives inside
#   them as executed strings rather than as module constants, and lifting it
#   out would be a large, risky edit to the one file every scraper depends on.
#   inspect.getsource only READS -- it cannot change what those functions do,
#   and if it ever fails the result is the old behaviour (no derived ALTERs),
#   never a broken start-up.
def _coreDdlText():
    """The CREATE TABLE text of every core table, for ensureDdlColumns."""
    import inspect
    out = []
    for fn in (_createCoreTables, _createTFTables, _createRecoveryTable,
               _createMeetsTFMetaTable):
        try:
            out.append(inspect.getsource(fn))
        except (OSError, TypeError):                       # pragma: no cover
            continue
    return out


# ! THE SAME PASS, CALLABLE ON ITS OWN. A maintenance script that touches a
#   column the DDL grew should not have to run the whole createTables (which
#   builds indexes) just to be sure the column is there -- and it should not
#   have to hope, either. scripts/backfill_tf_venues.py calls this.
def ensureCoreColumns(verbose=True):
    """Add every column the core DDL declares and the live tables lack.
    Returns [(table, column)] added.

    ⚠ COMMITS PER TABLE. The first version did the whole sweep in ONE
      transaction, so it held ACCESS EXCLUSIVE on twelve tables at once until
      the end -- results and results_tf among them -- and anything that
      touched them queued behind it. Two instances of the caller then queued
      behind each other and neither ever ran.
    """
    grew = []
    with getConn() as conn:
        cur = conn.cursor()
        plan = []
        for ddl in _coreDdlText():
            plan += ddlPlan(cur, ddl)
        conn.commit()                       # the read is over; hold nothing

        if not plan:
            if verbose:
                print("[DB] schema is current: every column the DDL declares "
                      "is already there (no locks taken)")
        else:
            n = sum(len(cols) for _t, cols in plan)
            if verbose:
                print(f"[DB] {n} column(s) missing across {len(plan)} table(s); "
                      f"adding them one table at a time", flush=True)
            for table, want in plan:
                if verbose:
                    print(f"[DB]   {table}: {', '.join(c for c, _ in want)}",
                          flush=True)
                for name, spec in want:
                    try:
                        cur.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
                        cur.execute(
                            f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS '
                            f'"{name}" {_relaxed(spec)}')
                        conn.commit()       # ! release before the next table
                        grew.append((table, name))
                    except Exception as exc:              # noqa: BLE001
                        conn.rollback()
                        print(f"[DB] could not add {table}.{name}: "
                              f"{type(exc).__name__} {exc}")
                        print(f"[DB]   (busy table -- it will be added on the "
                              f"next run; nothing is blocked waiting)")
            if grew and verbose:
                print("[DB] added: "
                      + ", ".join(f"{t}.{c}" for t, c in grew), flush=True)

        auditInsertColumns(cur, verbose=verbose)
        conn.commit()
    return grew


# createTables()
# Purpose: Creates all tables and indexes if they don't already exist.
#          Safe to run multiple times.
# Arguments: None.
# Outputs: None.
def createTables():

    try:
        with getConn() as conn:
            cursor = conn.cursor()
            # ⚠ COMMIT BETWEEN PHASES, OR TWO LAUNCHERS DEADLOCK. The whole
            #   of createTables used to be ONE transaction, so it held every
            #   ACCESS EXCLUSIVE lock it took until the end. Two instances
            #   starting together then crossed: "Process A waits for
            #   AccessExclusiveLock on relation X; Process B waits for
            #   AccessShareLock on relation Y" -- a genuine deadlock, which
            #   a lock_timeout cannot help with because neither side is
            #   merely waiting, they are waiting on each other. Committing
            #   after each phase means no lock is held across phases and
            #   there is no cycle to form.
            #   (a loop, not a nested def: a def here truncates every tool
            #   that reads this function's source, the tests included)
            for _step in (_createCoreTables,
                          _createTFTables,
                          _createRecoveryTable,
                          _migrateLocationID,
                          _migrateMeetQueueCompositeKey,
                          _migrateMeetQueueAddSource,
                          _migrateMeetExtrasAddSource,
                          _migrateResultsAddTeamSlug,
                          _migrateResultsAddStatus):
                _step(cursor)
                conn.commit()
            # ★ AND EVERY OTHER COLUMN THE DDL DECLARES. The two migrations
            #   above are hand-written, which is the habit that lost
            #   team_slug, status and venue_name in one day; this catches the
            #   ones nobody remembered to write, from the CREATE TABLE text
            #   itself. Both are kept: the hand-written pair also build
            #   indexes and backfill, which a derived ALTER cannot.
            _grew = []
            for _ddl in _coreDdlText():
                _grew += ensureDdlColumns(cursor, _ddl)
                conn.commit()          # per DDL block, same reason
            if _grew:
                print("[DB] added missing columns: "
                      + ", ".join(f"{t}.{c}" for t, c in _grew))
            # and say so about the ones no DDL declares, which cannot be added
            auditInsertColumns(cursor)
            conn.commit()
            _createMeetsTFMetaTable(cursor)
            conn.commit()
            _createIndexes(cursor)
            conn.commit()
        print("[DB] Tables ready")
    except Exception as e:
        # ⚠ AND IT IS NOT A WARNING. The scraper ran on regardless, found a
        #   queue it could not read and reported "0 processed" with no cause
        #   -- half an hour of looking at the wrong thing. A schema step that
        #   failed means the tables are not known to be right.
        print(f"[DB] ⚠ FAILED TO CREATE TABLES: {type(e).__name__} {e}")
        print("[DB]   The schema is NOT known to be correct. Re-run; if it "
              "is a deadlock, another launcher or pipeline step is starting "
              "at the same time -- start one at a time.")
        raise
 

# _createCoreTables
# Purpose: Create the XC tables: athletes, meets, results, meet_queue,
#          course_difficulties, athlete-Ratings.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _createCoreTables(cursor):

    # Athletes table — one row per athlete.
    # BIGINT instead of INTEGER because athletic.net athlete IDs
    # are large numbers that exceed SQLite's INTEGER range.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS athletes (
            athlete_id      BIGINT PRIMARY KEY,
            first_name      TEXT,
            last_name       TEXT,
            gender          TEXT,
            school          TEXT
        )
    """)

    # Meets table — one row per race division.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meets (
            div_id          BIGINT PRIMARY KEY,
            meet_id         BIGINT,
            meet_name       TEXT,
            -- ★ THE MEET'S OWN DATE (owner, 2026-09-18). It was only ever on
            --   the RESULT rows, so a meet with zero results had no date
            --   anywhere and "which meets from the last few months came back
            --   empty" could not be asked -- the one question you ask about
            --   an empty meet. meets_tf_meta.meet_date has carried it for
            --   track all along; this is the same value for cross country,
            --   from the same MeetDate field saveMeet already parses.
            meet_date       TEXT,
            course_name     TEXT,
            distance        REAL,
            gps_lat         REAL,
            gps_long        REAL,
            state           TEXT
        )
    """)

    # Results table — one row per individual XC performance.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            result_id       BIGINT PRIMARY KEY,
            athlete_id      BIGINT,
            meet_id         BIGINT,
            div_id          BIGINT,
            time_seconds    REAL,
            grade           TEXT,
            date            TEXT,
            normalized_time REAL DEFAULT NULL,
            speed_rating    REAL DEFAULT NULL,
            -- ★ WHY A ROW HAS NO TIME. DNF/DNS/DQ/SCR and friends, normalised
            --   by scripts/result_status.py. It was added by hand in
            --   _migrateResultsAddStatus and NOT declared here, so
            --   ensureCoreColumns -- which derives its ALTERs from this text
            --   -- could not know it was wanted, and the audit rightly
            --   reported the INSERT writing a column the database lacked.
            --   Declared here, it is created and back-added automatically.
            status          TEXT
        )
    """)

    # Meet queue — tracks which meets have been scraped.
    # scraped: 0=unscraped, 1=done, 2=failed, 3=in-progress, 4=skipped(TF on Linux)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meet_queue (
            meet_id     BIGINT PRIMARY KEY,
            sport       TEXT,
            scraped     INTEGER DEFAULT 0
        )
    """)

    # Course difficulties — one row per unique course name.
    # difficulty is a multiplier — 1.03 means 3% slower than flat neutral.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS course_difficulties (
            course_name     TEXT PRIMARY KEY,
            difficulty      REAL,
            n_results       INTEGER,
            n_athletes      INTEGER,
            last_updated    TEXT
        )
    """)

    # Athlete ratings — one row per (athlete_id, pool) pair.
    # pool is "hs" or "college". Higher speed_rating = faster athlete.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS athlete_ratings (
            athlete_id      BIGINT,
            pool            TEXT,
            speed_rating    REAL,
            n_races         INTEGER,
            last_updated    TEXT,
            PRIMARY KEY (athlete_id, pool)
        )
    """)

    # Per-meet auxiliary JSONB blobs from GetAllResultsData.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meet_extras (
            meet_id          BIGINT,
            sport            TEXT,
            teams_json       JSONB,
            event_types_json JSONB,
            relay_legs_json  JSONB,
            PRIMARY KEY (meet_id, sport)
        )
    """)

# _createTFTables
# Purpose: Creates the Track & Field tables: meets_tf, results_tf,
#          tf_scraped_events.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _createTFTables(cursor):

    # TF meets table — one row per event/division combo.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meets_tf (
            div_id          BIGINT,
            meet_id         BIGINT,
            meet_name       TEXT,
            -- ★ THE VENUE'S NAME, AND IT WAS NEVER MISSING FROM THE SCRAPE
            --   (owner, 2026-09-16: "make sure tf venue names go in so we
            --   can not label our id as venue"). anet's Location.Name has
            --   been landing in meets_tf_meta.venue_name all along; it was
            --   this table -- the one the engine and the site join -- that
            --   had nowhere to put it, so the TF venue key came out as
            --   'loc:<location_id>:out' and that id is what a page showed.
            --   Filled here for new rows and backfilled from meets_tf_meta
            --   for old ones: no re-scrape, it is a JOIN we never made.
            venue_name      TEXT,
            -- the meet's own page, for auditing a bad parse after the fact
            meet_url        TEXT,
            event_short     TEXT,
            event_id        BIGINT,
            distance_meters REAL,
            gps_lat         REAL,
            gps_long        REAL,
            state           TEXT,
            is_indoor       INTEGER DEFAULT 0,
            PRIMARY KEY (div_id, event_id)
        )
    """)

    # TF results table — one row per individual TF performance.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results_tf (
            result_id       BIGINT PRIMARY KEY,
            athlete_id      BIGINT,
            meet_id         BIGINT,
            div_id          BIGINT,
            event_id        BIGINT,
            event_short     TEXT,
            time_seconds    REAL,
            grade           TEXT,
            date            TEXT,
            is_relay        INTEGER DEFAULT 0,
            normalized_time REAL DEFAULT NULL,
            speed_rating    REAL DEFAULT NULL,
            -- ★ THE SAME COLUMN, FOR TRACK. saveResultsTFBulk names it in
            --   its INSERT; see the note on results.status above.
            status          TEXT
        )
    """)

    # TF scraped events — tracks which TF events were successfully scraped.
    # Used by recovery script to find missed events.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tf_scraped_events (
            meet_id         BIGINT,
            event_short     TEXT,
            div_id          BIGINT,
            scraped_at      TEXT,
            PRIMARY KEY (meet_id, event_short, div_id)
        )
    """)

# _createRecoveryTable
# Purpose: Creates tf_recovery_queue - the table that holds TF event/div
#          combos that failed during the main scrape and need to be retried.
#          One row per (meet_id, event_short, div_id) combo.
#          Status codes mirror meet_queue:
#            0 = unscraped (needs retry)
#            1 = done (successfully recovered)
#            2 = failed (gave up after retries)
#            3 = in-progress (currently being processed)
# Arguments:
#           cursor: open psycopg2 cursor from createTables.
# Output: None. Creates table if it doesn't exist.
def _createRecoveryTable(cursor):

    cursor.execute("""
        Create TABLE IF NOT EXISTS tf_recovery_queue (
            meet_id         BIGINT,
            event_short     TEXT,
            div_id          BIGINT,
            scraped         INTEGER DEFAULT 0,
            PRIMARY KEY(meet_id, event_short, div_id)
        )
    """)

# ─────────────────────────────────────────────────────────────────────────────
# meets_tf_meta — meet-level TF metadata (one row per meet_id)
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY a separate table: meets_tf is per-(meet_id, div_id, event_id) — many rows
# per meet. The fields below are meet-CONSTANT (same venue/season/address for
# every event), so storing them on meets_tf would duplicate a big GoogleData
# blob across hundreds of rows. meets_tf_meta holds them once, keyed on meet_id.
# meets_tf is left UNTOUCHED (kept stable) — its per-event geometry columns stay
# as they are; this table is additive and read by the geocoding/feature layer
# later, not by the existing engine join.
 
 
# _migrateResultsAddTeamSlug
# Purpose: Add `team_slug` to results and results_tf. TFRRS links every result
#          row to a team page whose filename IS a stable school key --
#          "CT_college_f_Conn_College" -- state, level, gender and name in one
#          token. The parser has always read it (parse_xc.py _extractTeam) and
#          the saver has always dropped it, so every downstream question about
#          "is this the same school?" has had to be answered from the display
#          name plus inference. Keeping the slug means the college half of the
#          corpus carries a real id.
#
# ! ONLY NEW AND RE-SCRAPED ROWS GET A VALUE. This is additive on purpose: the
#   column is NULL for everything already stored, and nothing may treat NULL
#   as "different school" -- absent is absent, not a distinguishing fact.
#
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None. Idempotent.
# _migrateResultsAddStatus
# Purpose: Add `status` to results AND results_tf -- WHY a row has no time, in
#          the source's own letters (DNF / DNS / DQ / SCR / NH ...).
#
# ⚠ IT EXISTED ON ONE TABLE AND WAS CREATED BY THE SAVER, NOT BY A MIGRATION.
#   `results.status` was added lazily by _ensureResultsStatus the first time
#   anet's XC saver ran; results_tf never had one at all; and the tfrrs savers
#   write both tables without going near either. So a database where tfrrs
#   scraped first met `UndefinedColumn: column "status" does not exist` -- the
#   same shape of failure results_tf.team_slug produced on the server this
#   morning. A column two writers depend on belongs in createTables.
#
# ! ONLY NEW AND RE-SCRAPED ROWS GET A VALUE. Additive on purpose: NULL for
#   everything already stored, and result_status.kind() falls back to the
#   sentinel for those, which is all they have.
def _migrateResultsAddStatus(cursor):
    for table in ("results", "results_tf"):
        cursor.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS status TEXT")


def _migrateResultsAddTeamSlug(cursor):
    for table in ("results", "results_tf"):
        cursor.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS team_slug TEXT")
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_team_slug "
            f"ON {table} (team_slug) WHERE team_slug IS NOT NULL")


# _createMeetsTFMetaTable
# Purpose: Create meets_tf_meta if missing. One row per TF meet_id. Idempotent
#          (CREATE TABLE IF NOT EXISTS), so safe to call from createTables every
#          run. source/id_system NOT NULL to match the rest of the schema's
#          provenance convention; the saver supplies 'anet'.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None.
def _createMeetsTFMetaTable(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meets_tf_meta (
            meet_id         BIGINT PRIMARY KEY,
            meet_name       TEXT,
            meet_date       TEXT,
            season_id       INTEGER,
            gender          TEXT,
            template        TEXT,
            level_mask      INTEGER,
            finalized       INTEGER,
            has_results     INTEGER,
            location_id     BIGINT,
            venue_name      TEXT,
            address         TEXT,
            city            TEXT,
            state           TEXT,
            postal_code     TEXT,
            country         TEXT,
            gps_lat         REAL,
            gps_long        REAL,
            track_type      TEXT,
            track_length    REAL,
            is_indoor       INTEGER,
            altitude_meters REAL,
            google_data     JSONB,
            source          TEXT NOT NULL,
            id_system       TEXT NOT NULL,
            native_id       BIGINT
        )
    """)

# _migrateLocationID
# Purpose: Adds the location_id column to meets and meets_tf if it
#          doesn't already exist. ALTER TABLE ADD COLUMN IF NOT EXISTS
#          is idempotent — safe to run every startup.
#          location_id comes from meet_info["Location"]["ID"] — the
#          venue/facility ID, distinct from meet_id. Useful for
#          deduplicating courses scraped under different meet names.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _migrateLocationID(cursor):

    # ALTER TABLE ... ADD COLUMN takes an ACCESS EXCLUSIVE lock EVEN when it
    # no-ops (column already there). That exclusive lock queues behind any
    # in-flight reader of the table and then blocks everything behind it — which
    # is what wedged the launcher at startup. So check the catalog first (a
    # lock-free SELECT) and only ALTER when the column is genuinely missing.
    for table in ("meets", "meets_tf"):
        cursor.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
              AND column_name = 'location_id'
        """, (table,))
        if cursor.fetchone() is None:
            # Only reached on a fresh DB that predates the column.
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN location_id BIGINT")

# _createIndexes
# Purpose: Creates Indexes for fast lookups on the above tables.
#          Indexes speed up SELECT queries on the indexed column but slightly
#          slow down INSERTs - worth it for our read patterns.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _createIndexes(cursor):

    # Indexes for fast lookups.
    indexes = [
        # XC results — fast lookup by athlete or meet.
        ("idx_results_athlete_id",      "results",    "athlete_id"),
        ("idx_results_meet_id",         "results",    "meet_id"),
        ("idx_results_normalized_time", "results",    "normalized_time"),
        # TF results — same pattern.
        ("idx_results_tf_athlete_id",      "results_tf", "athlete_id"),
        ("idx_results_tf_meet_id",         "results_tf", "meet_id"),
        ("idx_results_tf_normalized_time", "results_tf", "normalized_time"),
        # Recovery Table for track events. Index on scraped so we can
        # get unscraped rows quickly.
        ("idx_tf_recovery_scraped", "tf_recovery_queue ", "scraped"),
    ]

    # Postgres syntax for IF NOT EXISTS on indexes is different from SQLite.
    for name, table, column in indexes:
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})
        """)


# ─────────────────────────────────────────────────────────────────────────────
# Save functions
# ─────────────────────────────────────────────────────────────────────────────
#
# IMPORTANT INTERFACE CHANGE from old database.py:
# Every save function now takes `conn` as its first argument.
# The caller is responsible for:
#   1. Getting a connection from the pool via `with getConn() as conn:`
#   2. Passing that conn to every save function for one meet
#   3. Calling conn.commit() once after all saves for that meet
#
# This means one meet = one connection checkout = one commit.
# Previously: one meet = ~200 connection open/close cycles.
#
# The caller (scrape_results.py) looks like:
#
#   with getConn() as conn:
#       saveMeet(conn, meet_info, div)
#       for athlete in athletes:
#           saveAthlete(conn, athlete)
#       for result in results:
#           saveResult(conn, result, meet_info)
#       conn.commit()
#
# ─────────────────────────────────────────────────────────────────────────────

# saveAthlete
# Purpose: Inserts one athlete into the athletes table.
#          ON CONFLICT DO NOTHING skips duplicates safely.
# Arguments:
#           conn: connection from the pool (passed in by caller).
#           athleteData: dict with keys AthleteID, FirstName, LastName,
#                        Gender, SchoolName.
# Output: None.
def saveAthlete(conn, athleteData: dict):

    # Guard — if no AthleteID, we can't insert a useful row.
    if not athleteData.get("AthleteID"):
        return

    cursor = conn.cursor()

    executeWithRetry(cursor, """
        INSERT INTO athletes (athlete_id, first_name, last_name, gender, school)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (athlete_id, school) DO NOTHING
    """, (
        athleteData.get("AthleteID"),
        athleteData.get("FirstName"),
        athleteData.get("LastName"),
        athleteData.get("Gender"),
        athleteData.get("SchoolName") or "Unknown"
    ))

# saveMeet
# Purpose: Upsert one XC meets row (one row per division / div_id). Now also
#          stores the division metadata unlocked by the 6/22 captures
#          (Division, LevelMask, CourseId) and is backfill-safe via ON
#          CONFLICT — re-scraping an existing div_id now refreshes its
#          enrichment fields instead of crashing on a duplicate-key error.
# Arguments:
#           conn:
#               Open DB connection (caller owns the transaction/commit).
#           meetData:
#               Meet-level dict from getMeetData — provides ID, Name, and the
#               nested Location{} (Name=venue, Lat, Long, State, ID=venue key).
#           divData:
#               One xcDivisions[] entry — provides IDMeetDiv (div_id/PK),
#               Meters (this division's distance), Division, LevelMask,
#               CourseId.
# Output:
#           None. Inserts or updates exactly one meets row.
def saveMeet(conn, meetData: dict, divData: dict):

    cursor = conn.cursor()
 
    # Location may be absent on malformed responses — default to {} so the
    # .get() calls below can't raise. (The original indexed Location["Name"]
    # directly, which would KeyError on a meet missing Location.)
    location = meetData.get("Location", {}) or {}

    # Same parse as saveResult's, from the same field.
    _raw = meetData.get("MeetDate", "") or ""
    meet_date = _raw.split("T")[0] if _raw else None

    executeWithRetry(cursor, """
        INSERT INTO meets (
            div_id,
            meet_id,
            meet_name,
            meet_date,
            course_name,
            distance,
            gps_lat,
            gps_long,
            state,
            location_id,
            division,
            level_mask,
            course_id,
            source,
            id_system
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (div_id) DO UPDATE SET
            meet_date   = COALESCE(EXCLUDED.meet_date, meets.meet_date),
            course_name = EXCLUDED.course_name,
            distance    = EXCLUDED.distance,
            gps_lat     = EXCLUDED.gps_lat,
            gps_long    = EXCLUDED.gps_long,
            state       = EXCLUDED.state,
            location_id = EXCLUDED.location_id,
            division    = EXCLUDED.division,
            level_mask  = EXCLUDED.level_mask,
            course_id   = EXCLUDED.course_id
    """, (
        divData["IDMeetDiv"],          # PK — must exist; bracket access on purpose
        meetData["ID"],
        meetData["Name"],
        meet_date,
        location.get("Name"),          # course_name = venue name (6/22: course identity not in API)
        divData.get("Meters"),         # distance VARIES per division — never assume 5000
        location.get("Lat"),
        location.get("Long"),
        location.get("State"),
        location.get("ID"),            # location_id = stable venue key (Location.ID)
        divData.get("Division"),       # e.g. "Varsity", "Junior Varsity"
        divData.get("LevelMask"),      # 4=HS, 8=college, 12=both
        divData.get("CourseId"),       # usually 0; stored as-is in case it's ever populated
        "anet",                        # source     — NOT NULL
        "anet",                        # id_system  — NOT NULL
    ))

# saveResult
# Purpose: Inserts one XC result into the results table.
# Arguments:
#           conn: connection from the pool.
#           resultData: dict with result-level info from getMeetResults.
#           meetData: dict with meet-level info.
# Output: None.
def saveResult(conn, resultData: dict, meetData: dict, school: str = None):

    cursor = conn.cursor()

    meet_date_raw = meetData.get("MeetDate", "")
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

    # school_source records WHERE school came from:
    #   'scraped'  - real per-result value, passed in by caller
    #   NULL       - no school passed (legacy call site, or
    #                 athlete had no resolvable school at all) —
    #                 marks this row as a future re-scrape target.
    school_source = "scraped" if school else None

    executeWithRetry(cursor, """
        INSERT INTO results (
            result_id,
            athlete_id,
            meet_id,
            div_id,
            time_seconds,
            grade,
            date,
            school,
            school_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        -- If result is re-scraped for school/school_source
        -- update them.
        ON CONFLICT (result_id) DO UPDATE SET
            school        = EXCLUDED.school,
            school_source = EXCLUDED.school_source,
            scraped_at    = now()
        WHERE results.school_source IS NULL
    """, (
        resultData.get("IDResult"),
        resultData.get("AthleteID"),
        meetData.get("ID"),
        resultData.get("IDDiv"),
        resultData.get("SortValue"),
        resultData.get("Grade", ""),
        meet_date,
        school,
        school_source
    ))

# saveMeetTF
# Purpose: Inserts one TF event/division row into meets_tf. Now also writes
#          division (competition tier / multi / para, from the flatEvents
#          wrapper) and level_mask (meet level: 4=HS, 8=college, 12=both).
#          ON CONFLICT (div_id, event_id) DO NOTHING — prelim and final share
#          that key, so one metadata row per event/div is correct.
# Arguments:
#           conn: connection from the pool.
#           meet_info: dict from getMeetDataTF (ID/Name/Location/LevelMask).
#           div_id: division ID.
#           event_id: numeric event ID.
#           event_short: event code string e.g. "1mile".
#           distance_meters: float, or None/-1 for field/failed events.
#           division: division name string, or None (per-div fallback path).
# Output: None.
# ★ THE MEET'S OWN PAGE (owner, 2026-09-16: approved with the venue
#   columns). Worth one text column: when a parse comes out wrong -- a
#   column shift, a distance that cannot be right -- the first question is
#   always "what did the page actually say", and reconstructing the URL from
#   an id months later is guesswork about a URL scheme that has changed.
def meetUrlTF(meet_info):
    """anet's own URL for a TF meet, or None."""
    mid = meet_info.get("ID")
    if mid in (None, ""):
        return None
    season = meet_info.get("SeasonID") or ""
    url = f"https://www.athletic.net/TrackAndField/meet/{mid}/results"
    return f"{url}?season={season}" if season else url


# ★ AND THE OLD ROWS NEED NO SCRAPE AT ALL. meets_tf_meta already holds
#   venue_name for every meet it has seen, so the backfill is one UPDATE
#   ... FROM. Idempotent, and it only touches rows whose name is still
#   missing.
#
# ⚠⚠ AND IT WAS NEVER CALLED. Written, committed, wired to nothing -- so the
#    engine went on labelling a LOCATION ID as a venue while the names sat in
#    meets_tf_meta. (The same thing happened to school_team_link, which was
#    built and read by nothing until 2026-09-17.)
#
# ★ THE SECOND PASS IS THE OWNER'S (2026-09-17: "make sure it backfills any
#   meets where that venue currently has no name but id and such match"). A
#   meet whose own meta row never carried a name can still be at a place we
#   HAVE named from another meet: two meets at location 8891 are at the same
#   venue whether or not both of them said so. The name is taken from the
#   meets that DO have one at that location, and a rescrape is not needed for
#   any of it.
#
# ! THE MODAL NAME, NOT AN ARBITRARY ONE. A location can carry a couple of
#   spellings across the years; the most common one, ties broken
#   alphabetically, is stable across runs so a rerun does not churn the
#   column.
#
# ! AND ONLY EVER INTO A NULL. Nothing here overwrites a name a meet stated
#   for itself -- the meet knows its own venue better than its neighbours do.
def backfillMeetsTFVenueNames(conn, verbose=True, chunk=100000, passes=(1, 2, 3)):
    """Fill meets_tf.venue_name where it is missing: from the meet's own meta
    row, then from other meets at the same location. Returns (from_meta,
    from_location, from_gps).

    ★ passes= EXISTS SO A SCRAPE CAN RUN THE CHEAP HALF (owner, 2026-09-18:
      "did we ever fix the scraper not getting venue name (also when it does
      get the venue name does it update it for everything there)").

      The answer to the first half was "the scraper captures it, into
      meets_tf_meta -- but meets_tf, which the engine labels courses with, is
      only filled by THIS function, and this function was called by one manual
      script." So a fresh scrape put the name in meets_tf_meta and left
      meets_tf NULL until somebody remembered.

      PASS 1 is the one a scrape needs: it joins each meet to its OWN
      meets_tf_meta row, which is exactly where a just-scraped meet's name is.
      It is a keyed join, cheap, and chunked.

      PASSES 2 AND 3 are the expensive ones -- they aggregate all of meets_tf
      into a temp table of named locations and named coordinates -- and they
      answer the second half of the question: yes, a venue named once names
      every meet at that location_id (pass 2) or at those coordinates (pass 3).
      Those stay a deliberate, occasional job.

    ⚠ ALL THREE ONLY FILL NULLS (`AND t.venue_name IS NULL`). None of them ever
      corrects a name that is already there, right or wrong.
    """
    cursor = conn.cursor()

    import time as _t

    # ⚠ CHUNKED, BECAUSE EACH PASS USED TO BE ONE STATEMENT OVER 16.2M ROWS
    #   (owner, 2026-09-18: "backfill tf venues is hanging"). It was not
    #   hanging. Pass 1 rewrites up to 14.8M row versions in a single UPDATE
    #   inside a single transaction, and the progress line was printed
    #   BEFORE the statement started -- so the terminal showed "pass 1/3"
    #   and then nothing for hours, which is indistinguishable from a hang
    #   and, for a job you cannot watch, is the same thing.
    #
    # ! AND IT IS RESUMABLE BY CONSTRUCTION. Every pass only touches rows
    #   where venue_name IS NULL and commits per chunk, so killing this at
    #   any point loses nothing and re-running picks up where it stopped.
    #   The old single transaction threw away every row it had filled.
    cursor.execute("SELECT min(meet_id), max(meet_id) FROM meets_tf")
    lo_id, hi_id = cursor.fetchone()
    if lo_id is None:
        if verbose:
            print("  meets_tf is empty -- nothing to backfill")
        return 0, 0, 0

    def _chunks():
        lo = lo_id
        while lo <= hi_id:
            yield lo, lo + chunk
            lo += chunk

    n_chunks = max(1, (hi_id - lo_id) // chunk + 1)

    def _run(label, sql, params=()):
        """One pass, chunk by chunk, committing and reporting as it goes."""
        total, t0, done = 0, _t.time(), 0
        for lo, hi in _chunks():
            cursor.execute(sql, params + (lo, hi))
            total += cursor.rowcount
            conn.commit()
            done += 1
            if verbose and (done % 20 == 0 or done == n_chunks):
                pct = 100.0 * done / n_chunks
                print(f"    {label}: {pct:5.1f}%  ({done:,}/{n_chunks:,} "
                      f"id blocks, {total:,} filled, "
                      f"{_t.time() - t0:.0f}s)", flush=True)
        if verbose:
            print(f"    {label}: {total:,} filled "
                  f"({_t.time() - t0:.0f}s)", flush=True)
        return total

    if verbose:
        print(f"  meets_tf meet_id {lo_id:,}..{hi_id:,} in {n_chunks:,} "
              f"blocks of {chunk:,}", flush=True)
        print("  pass 1/3: the meet's own meets_tf_meta row...", flush=True)
    from_meta = 0
    if 1 in passes:
        from_meta = _run("pass 1", """
            UPDATE meets_tf t SET venue_name = m.venue_name
            FROM   meets_tf_meta m
            WHERE  m.meet_id = t.meet_id
              AND  t.venue_name IS NULL
              AND  m.venue_name IS NOT NULL AND btrim(m.venue_name) <> ''
              AND  t.meet_id >= %s AND t.meet_id < %s
        """)

    # ★ THE SOURCE IS BUILT ONCE, NOT PER CHUNK. The aggregate below scans
    #   all of meets_tf; running it inside each chunk's UPDATE would turn
    #   one full scan into one per block. Materialised and indexed, then
    #   joined -- which is also why this is a temp table and not a CTE.
    from_location = from_gps = 0
    if 2 not in passes and 3 not in passes:
        conn.commit()
        if verbose:
            print(f"  meets_tf venue names: {from_meta:,} from the meet's own "
                  f"meta row (passes 2-3 skipped)", flush=True)
        return from_meta, from_location, from_gps
    if verbose:
        print("  pass 2/3: another meet at the same location_id...",
              flush=True)
    cursor.execute("DROP TABLE IF EXISTS vn_loc")
    cursor.execute("""
        CREATE TEMP TABLE vn_loc AS
        SELECT location_id,
               mode() WITHIN GROUP (ORDER BY btrim(venue_name)) AS venue_name
        FROM   meets_tf
        WHERE  location_id IS NOT NULL
          AND  venue_name IS NOT NULL AND btrim(venue_name) <> ''
        GROUP  BY location_id
    """)
    cursor.execute("CREATE INDEX ON vn_loc (location_id)")
    cursor.execute("ANALYZE vn_loc")
    conn.commit()
    if verbose:
        cursor.execute("SELECT count(*) FROM vn_loc")
        print(f"    {cursor.fetchone()[0]:,} named locations", flush=True)
    from_location = _run("pass 2", """
        UPDATE meets_tf t SET venue_name = src.venue_name
        FROM   vn_loc src
        WHERE  src.location_id = t.location_id
          AND  t.venue_name IS NULL
          AND  t.meet_id >= %s AND t.meet_id < %s
    """)

    if verbose:
        print("  pass 3/3: the same coordinates, for rows with no location "
              "id...", flush=True)
    cursor.execute("DROP TABLE IF EXISTS vn_gps")
    cursor.execute("""
        CREATE TEMP TABLE vn_gps AS
        SELECT round(gps_lat::numeric, 5)  AS la,
               round(gps_long::numeric, 5) AS lo,
               mode() WITHIN GROUP (ORDER BY btrim(venue_name)) AS venue_name
        FROM   meets_tf
        WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
          AND  venue_name IS NOT NULL AND btrim(venue_name) <> ''
        GROUP  BY 1, 2
    """)
    cursor.execute("CREATE INDEX ON vn_gps (la, lo)")
    cursor.execute("ANALYZE vn_gps")
    conn.commit()
    if verbose:
        cursor.execute("SELECT count(*) FROM vn_gps")
        print(f"    {cursor.fetchone()[0]:,} named coordinates", flush=True)
    from_gps = _run("pass 3", """
        UPDATE meets_tf t SET venue_name = src.venue_name
        FROM   vn_gps src
        WHERE  t.venue_name IS NULL
          AND  t.gps_lat IS NOT NULL AND t.gps_long IS NOT NULL
          AND  round(t.gps_lat::numeric, 5) = src.la
          AND  round(t.gps_long::numeric, 5) = src.lo
          AND  t.meet_id >= %s AND t.meet_id < %s
    """)

    conn.commit()
    if verbose:
        cursor.execute("""SELECT count(*) FILTER (WHERE venue_name IS NULL),
                                 count(*) FROM meets_tf""")
        left, total = cursor.fetchone()
        print(f"  meets_tf venue names: {from_meta:,} from the meet's own "
              f"meta row, {from_location:,} from another meet at the same "
              f"location, {from_gps:,} from the same coordinates; "
              f"{left:,} of {total:,} rows still have none", flush=True)
    return from_meta, from_location, from_gps


def saveMeetTF(conn, meet_info: dict, div_id: int, event_id: int,
               event_short: str, distance_meters, division=None):

    cursor = conn.cursor()

    location = meet_info.get("Location", {})

    executeWithRetry(cursor, """
        INSERT INTO meets_tf (div_id, meet_id, meet_name, event_short,
                            event_id, distance_meters, gps_lat, gps_long,
                            state, is_indoor, location_id,
                            track_type, track_length, ustfccca_id,
                            division, level_mask,
                            venue_name, meet_url,
                            source, id_system)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s)
        ON CONFLICT (meet_id, div_id, event_id) DO NOTHING
    """, (
        div_id,
        meet_info.get("ID"),
        meet_info.get("Name"),
        event_short,
        event_id,
        distance_meters,
        location.get("Lat"),
        location.get("Long"),
        location.get("State"),
        1 if location.get("Indoor") else 0,
        location.get("ID"),
        location.get("TrackType"),
        location.get("TrackLength"),
        meet_info.get("UstfcccaID"),
        division,
        meet_info.get("LevelMask"),
        # ★ Location.Name IS THE VENUE NAME. saveMeetTFMeta has been storing
        #   it as venue_name since it landed; this is the same value, in the
        #   table its consumers actually read.
        location.get("Name"),
        meetUrlTF(meet_info),
        "anet",                        # source     — NOT NULL
        "anet",                        # id_system  — NOT NULL
    ))

# saveResultTF
# Purpose: Inserts one TF result into results_tf.
# Arguments:
#           conn: connection from the pool.
#           result: dict from resultsTF array.
#           meet_info: dict from getMeetDataTF.
#           div_id: division ID.
#           event_id: numeric event ID.
#           event_short: event code string.
#           is_relay: 1 if relay, 0 if individual.
#           school: string of the school name this result was run by.
# Output: None.
def saveResultTF(conn, result: dict, meet_info: dict, div_id: int,
                event_id: int, event_short: str, is_relay: int, school: str = None):
    
    # Milliseconds to seconds; a non-finish sentinel (anet TF writes
    # 20,000,000) is no time -- see result_status.TF_SENTINEL_MS.
    time_seconds = _timeFromSortInt(result.get("SortInt"))

    # Gets the meet date including the time, then splits off
    # and saves the mm/dd/yy.
    meet_date_raw = meet_info.get("MeetDate", "")
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

    cursor = conn.cursor()

    school_source = "scraped" if school else None

    executeWithRetry(cursor, """
        INSERT INTO results_tf (
            result_id,
            athlete_id,
            meet_id,
            div_id,
            event_id,
            event_short,
            time_seconds,
            grade,
            date,
            is_relay,
            school,
            school_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (result_id) DO UPDATE SET
            school        = EXCLUDED.school,
            school_source = EXCLUDED.school_source 
        WHERE results.school_source IS NULL
    """, (
        result.get("IDResult"),
        result.get("AthleteID"),
        meet_info.get("ID"),
        div_id,
        event_id,
        event_short,
        time_seconds,
        result.get("Grade", ""),
        meet_date,
        is_relay,
        school,
        school_source
    ))

# saveMeetQueue
# Purpose: Inserts a meet into the scraping queue.
# Arguments:
#           meet_id: athletic.net meet ID.
#           sport: "XC" or "TF".
# Output: None.
def saveMeetQueue(meet_id: int, sport: str):

    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor, """
            INSERT INTO meet_queue (meet_id, sport)
            VALUES (%s, %s)
            ON CONFLICT (meet_id, sport, source) DO NOTHING
        """, (meet_id, sport))
        conn.commit()

# saveMeetTeams
# Purpose: Upserts one meet's raw teams[] roster (the school/conference
#          reference list that rides along in every GetResultsData3 response)
#          into meet_teams as JSONB. One row per (meet_id, sport); re-scraping
#          a meet refreshes that row instead of inserting a duplicate. Takes
#          the caller's already-open connection so the write lands inside the
#          same transaction as the rest of the meet's save (saveMeetTF, the
#          athlete/result bulks), matching saveMeetTF/saveResultsTFBulk's
#          conn-taking pattern — never opens its own connection.
# Arguments:
#           conn: open psycopg2 connection from the caller's getConn() block.
#                 Passed in (not created here) to stay atomic with the meet.
#           meet_id: athletic.net meet ID. First half of the composite PK.
#           sport: "xc" or "tf". Second half of the PK, so a single meet_id
#                  can hold a separate roster per sport.
#           teams_array: the Python list parsed from the response's "teams"
#                        key. Wrapped in psycopg2.extras.Json so it stores as
#                        real JSONB (queryable) rather than a stringified blob.
#                        Caller guarantees it's non-empty (see _saveTFMeet).
# Output: None. Side effect only — one upserted row in meet_teams.
#
# Storage note: this keeps the roster for EVERY meet that has one (~a dozen
# teams on a dual, hundreds on a state meet). If that ever bloats, gate the
# call in _saveTFMeet on size, e.g. `if teams_array and len(teams_array) > 30`,
# to keep only the big statewide rosters and drop the redundant per-meet ones.
def saveMeetTeams(conn, meet_id, sport, teams_array):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO meet_teams (meet_id, sport, teams_json)
        VALUES (%s, %s, %s)
        ON CONFLICT (meet_id, sport) DO UPDATE
            SET teams_json = EXCLUDED.teams_json
        """,
        # ⚠ psycopg2.extras.Json, NOT a bare Json. This module imports
        #   psycopg2.extras and never binds the name, so the bare call was a
        #   NameError the first time saveMeetTeams ran -- pre-existing, found
        #   by sweeping every file this session touched for names used and
        #   never imported (the sweep that found the missing `import sys`).
        (meet_id, sport, _Utf8Json(_cleanJson(teams_array))),
    )

# saveMeetTFMeta
# Purpose: Upsert the ONE meet-level row for a TF meet into meets_tf_meta, from
#          the GetMeetData(sport=tf) payload's meet dict. Reads meet.Location for
#          venue/address/geometry and the meet-level scalars (season/gender/etc).
#          Conn-taking (runs inside the caller's transaction). ON CONFLICT
#          refreshes everything so a re-scrape updates in place.
# Arguments:
#           conn:      open connection from the caller's getConn() block.
#           meet_info: the `meet` dict from getMeetDataTF (carries ID, Name,
#                      SeasonID, Gender, Template, LevelMask, Finalized,
#                      HasResults, MeetDate, and the nested Location{}).
# Output:   None. One upserted row in meets_tf_meta.
def saveMeetTFMeta(conn, meet_info: dict):
 
    cursor = conn.cursor()
 
    # Location may be absent/None on malformed responses — default to {} so the
    # .get() calls can't raise (same guard saveMeet uses).
    location = meet_info.get("Location", {}) or {}
 
    # MeetDate as YYYY-MM-DD (strip the ISO time half).
    meet_date_raw = meet_info.get("MeetDate", "") or ""
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else None
 
    # Finalized is a timestamp string when finalized, null otherwise -> 1/0 flag.
    finalized = 1 if meet_info.get("Finalized") else 0
 
    # GoogleData is a nested dict (the embedded Places geocode). Wrap in Json so
    # it stores as real JSONB; _cleanJson strips any NULs. None -> SQL NULL.
    google_data = location.get("GoogleData")
    google_json = (_Utf8Json(_cleanJson(google_data))
                   if google_data is not None else None)
 
    executeWithRetry(cursor, """
        INSERT INTO meets_tf_meta (
            meet_id, meet_name, meet_date, season_id, gender, template,
            level_mask, finalized, has_results,
            location_id, venue_name, address, city, state, postal_code, country,
            gps_lat, gps_long, track_type, track_length, is_indoor,
            altitude_meters, google_data,
            source, id_system, native_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meet_id) DO UPDATE SET
            meet_name      = EXCLUDED.meet_name,
            meet_date      = EXCLUDED.meet_date,
            season_id      = EXCLUDED.season_id,
            gender         = EXCLUDED.gender,
            template       = EXCLUDED.template,
            level_mask     = EXCLUDED.level_mask,
            finalized      = EXCLUDED.finalized,
            has_results    = EXCLUDED.has_results,
            location_id    = EXCLUDED.location_id,
            venue_name     = EXCLUDED.venue_name,
            address        = EXCLUDED.address,
            city           = EXCLUDED.city,
            state          = EXCLUDED.state,
            postal_code    = EXCLUDED.postal_code,
            country        = EXCLUDED.country,
            gps_lat        = EXCLUDED.gps_lat,
            gps_long       = EXCLUDED.gps_long,
            track_type     = EXCLUDED.track_type,
            track_length   = EXCLUDED.track_length,
            is_indoor      = EXCLUDED.is_indoor,
            google_data    = EXCLUDED.google_data
            -- altitude_meters intentionally NOT refreshed: it's filled by the
            -- elevation backfill, not this scrape, so don't null it back out.
    """, (
        meet_info.get("ID"),
        meet_info.get("Name"),
        meet_date,
        meet_info.get("SeasonID"),
        meet_info.get("Gender"),
        meet_info.get("Template"),
        meet_info.get("LevelMask"),
        finalized,
        meet_info.get("HasResults"),
        location.get("ID"),
        location.get("Name"),
        location.get("Address"),
        location.get("City"),
        location.get("State"),
        location.get("PostalCode"),
        location.get("Country"),
        location.get("Lat"),
        location.get("Long"),
        location.get("TrackType"),
        location.get("TrackLength"),
        1 if location.get("Indoor") else 0,
        None,                              # altitude_meters — elevation backfill
        google_json,
        "anet",                            # source     — NOT NULL
        "anet",                            # id_system  — NOT NULL
        meet_info.get("ID"),               # native_id  — matches migration (=meet_id)
    ))

# ─────────────────────────────────────────────────────────────────────────────
# Bulk save helpers
# ─────────────────────────────────────────────────────────────────────────────
#
# These take a list of rows and insert them all in ONE query using
# execute_values. Much faster than calling saveAthlete/saveResult in a loop
# when you have hundreds of rows ready to go.
# Used by the collect-then-save pattern in scrape_results.py.

# saveAthletesBulk
# Purpose: Inserts a list of athlete dics in one query.
# Arguments:
#           conn: connection from the pool.
#           athletes: list of athlete dicts (same format as saveAthlete).
# Output: None.
def saveAthletesBulk(conn, athletes: list):

    if not athletes:
        return
    
    # Builds a list of tuples, one per athlete, for execute_values
    rows = []
    for a in athletes:
        if not a.get("AthleteID"):
            continue
        row = (
            a.get("AthleteID"),
            a.get("FirstName"),
            a.get("LastName"),
            a.get("Gender"),
            _resolveSchool(a) or "Unknown",
            "anet",    # source
            "anet",    # id_system
            a.get("AthleteID"),   # person_id: seeded = athlete_id AT INSERT
        )
        cleaned = []
        for v in row:
            cleaned.append(_clean(v))
        rows.append(tuple(cleaned))


    if not rows:
        return
    cursor = conn.cursor()

    # execute_values sends all rows in one round trip to Postgres
    # %s in the template is filled by execute_)values based on the order
    # of the tuple in the list. Stops duplicates with ON CONFLICT of athlete
    # id DO NOTHING.
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO athletes (athlete_id, first_name, last_name, gender, school,
                              source, id_system, person_id)
        VALUES %s
        -- a blank placeholder (see saveResultsBulk) is filled, a real row kept
        ON CONFLICT (athlete_id, school) DO UPDATE SET
            first_name = CASE WHEN COALESCE(btrim(athletes.first_name), '') = ''
                              THEN EXCLUDED.first_name ELSE athletes.first_name END,
            last_name  = CASE WHEN COALESCE(btrim(athletes.last_name), '') = ''
                              THEN EXCLUDED.last_name ELSE athletes.last_name END,
            gender     = CASE WHEN COALESCE(btrim(athletes.gender), '') = ''
                              THEN EXCLUDED.gender ELSE athletes.gender END
        WHERE COALESCE(btrim(athletes.first_name), '') = ''
           OR COALESCE(btrim(athletes.last_name), '') = ''
           OR COALESCE(btrim(athletes.gender), '') = ''
    """, rows)

# saveResultsBulk
# Purpose: Bulk-upsert XC results, now capturing the full per-result set the
#          6/22 console work surfaced (place, score, exhibition, official,
#          team_id, is_pr, is_sr, has_splits, video_count, age_grade) in
#          addition to the existing fields.
#
#          BEHAVIOR CHANGE (ON CONFLICT): previously this only updated
#          school/school_source on conflict, and ONLY where school_source was
#          still NULL. That guard blocked enrichment backfill — an
#          already-schooled row could never receive its new capture fields.
#          Now, on conflict we refresh ALL capture columns plus time_seconds
#          (a re-scrape's SortValue repairs the historic corrupt MM:SS-as-
#          seconds times), and protect school with COALESCE so a re-scrape can
#          never null out a good school value.
# Arguments:
#           conn:
#               Open DB connection (caller owns the transaction/commit).
#           results:
#               List of (resultData, meetData, school) tuples, where
#               resultData is the raw resultsXC[] dict — every capture field
#               below is read straight off it.
# Output:
#           None. Inserts/updates one row per result.
# ★ THE NON-FINISH, PRESERVED (issue 59). Athletic.net writes one sentinel
#   SortValue for every kind of non-finish, so DNF, DNS and DQ were the same
#   999999 by the time anything read them. The feed's own text carries the
#   letters; this keeps them in a `status` column beside the sentinel time,
#   so the page can say which. Only the vocabulary below is stored -- a
#   result string that is a real time is not a status.
# ! THE VOCABULARY MOVED TO result_status, WHOLE. It was defined here and
#   used by the XC saver only, so the TF saver beside it captured nothing and
#   every READER went on inferring a non-finish from the sentinel in five
#   different spellings. One module now, shared by both savers, both tfrrs
#   parsers and every consumer -- see scripts/result_status.py.
from result_status import fromFields as _statusFields   # noqa: E402
from result_status import timeFromSortInt as _timeFromSortInt   # noqa: E402


def _statusOf(resultData: dict):
    """The non-finish token anet wrote, or None. It puts it in whichever of
    these fields it feels like that season, so all four are offered."""
    return _statusFields(*(resultData.get(k) for k in
                           ("Result", "ShortCode", "Status", "ResultText")))


_RESULTS_STATUS_READY = set()


def _ensureResultsStatus(conn, table="results"):
    """<table>.status, added once per process per table. IF NOT EXISTS, so a
    database that already has it is untouched.

    ⚠ results_tf WAS NEVER GIVEN ONE. The column has existed on `results`
      since issue 59 and the TF saver sitting beside it captured nothing, so
      every anet TRACK non-finish is still just the sentinel -- and track is
      the bigger table (191M rows against 39M).
    """
    if table in _RESULTS_STATUS_READY:
        return
    cur = conn.cursor()
    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS status TEXT")
    _RESULTS_STATUS_READY.add(table)


def saveResultsBulk(conn, results: list):
    _ensureResultsStatus(conn)

    if not results:
        return
 
    # One timestamp for the whole batch, captured before the loop so every
    # row in this batch shares it (used for future local -> Fly incremental sync).
    scraped_at = datetime.datetime.now(datetime.timezone.utc)
 
    rows = []
    # 4-tuple now: div_id rides alongside the result (set by the two XC collectors
    # from the division's IDMeetDiv). This is the correct, non-NULL division id;
    # it REPLACES the old resultData.get("IDDiv") read below, which always
    # returned None for XC (wrong key, and absent on result rows) and is the whole
    # reason 6M XC rows saved with div_id NULL.
    for i, (resultData, meetData, school, div_id) in enumerate(results):

        # Meet date as YYYY-MM-DD (strip the time half of the ISO string).
        meet_date_raw = meetData.get("MeetDate", "")
        meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""
 
        # school_source records WHERE school came from:
        #   'scraped' - real per-result value from this scrape
        #   NULL      - no school resolved -> marks this row as a future
        #               re-scrape target.
        school_source = "scraped" if school else None
 
        rows.append((
            resultData.get("IDResult"),
            resultData.get("AthleteID"),
            meetData.get("ID"),
            div_id,                                            # from the 4-tuple (division's IDMeetDiv); replaces broken IDDiv read
            resultData.get("SortValue"),                       # XC time = SortValue (NOT SortInt)
            resultData.get("Grade", ""),
            meet_date,
            school,
            school_source,
            scraped_at,
            # ---- 6/22 capture columns ----
            resultData.get("Place"),
            resultData.get("Score"),                           # 0 = non-scoring / displaced
            _flag(resultData.get("Exhibition")),
            _flag(resultData.get("Official")),
            _teamIdOrNone(resultData.get("TeamID")),
            _flag(resultData.get("isPr")),
            _flag(resultData.get("isSr")),
            _flag(resultData.get("hasSplitsSeries")),
            _videoCount(resultData.get("MediaCount")),
            resultData.get("AgeGrade"),
            "anet",    # source
            "anet",    # id_system
            resultData.get("AthleteID"),   # person_id: seeded = athlete_id AT INSERT
            _statusOf(resultData),         # the letters behind a sentinel time (issue 59)
        ))
    
    # Insert every athlete this batch references, under the SAME school the
    # result carries. An athlete can have multiple schools across years (e.g.
    # middle school then high school) - each (athlete_id, school) is its own
    # row, so this adds the ones not already present. Built from `rows` so the
    # school is byte-identical to what the result writes.
    # ⚠⚠ WITH THE NAME AND GENDER THE RESULT CARRIES (owner, 2026-09-25: a
    #    dual meet listing every freshman as "Unknown", each rated ~25 points
    #    above named runners with the same time). This is the ONLY athletes
    #    write on the XC save path -- _saveXCMeet never calls saveAthletesBulk
    #    -- and it wrote "", "", "": every athlete first seen in a cross
    #    country meet had no name and no gender, so no name on any page and
    #    the unknown-gender pool (a different mean) for their ratings. The
    #    result JSON has FirstName / LastName / Gender on every row.
    # ! AND A BLANK ROW IS FILLED, never a real one overwritten: the conflict
    #   update only touches names/gender that are empty.
    names = {}
    for resultData, _m, _s, _d in results:
        aid = resultData.get("AthleteID")
        if aid is not None and aid not in names:
            names[aid] = (_clean(resultData.get("FirstName")) or "",
                          _clean(resultData.get("LastName")) or "",
                          _clean(resultData.get("Gender")) or "")
    seen = set()
    athlete_rows = []
    for row in rows:
        aid, school = row[1], row[7]
        if aid is None:
            continue
        pair = (aid, school)
        if pair in seen:
            continue
        seen.add(pair)
        first, last, gender = names.get(aid, ("", "", ""))
        athlete_rows.append((aid, first, last, gender, school,
                             "anet", "anet", aid))   # last aid = person_id seed

    if athlete_rows:
        acur = conn.cursor()
        psycopg2.extras.execute_values(acur, """
            INSERT INTO athletes (athlete_id, first_name, last_name, gender, school,
                              source, id_system, person_id)
            VALUES %s
            ON CONFLICT (athlete_id, school) DO UPDATE SET
                first_name = CASE WHEN COALESCE(btrim(athletes.first_name), '') = ''
                                  THEN EXCLUDED.first_name ELSE athletes.first_name END,
                last_name  = CASE WHEN COALESCE(btrim(athletes.last_name), '') = ''
                                  THEN EXCLUDED.last_name ELSE athletes.last_name END,
                gender     = CASE WHEN COALESCE(btrim(athletes.gender), '') = ''
                                  THEN EXCLUDED.gender ELSE athletes.gender END
            WHERE COALESCE(btrim(athletes.first_name), '') = ''
               OR COALESCE(btrim(athletes.last_name), '') = ''
               OR COALESCE(btrim(athletes.gender), '') = ''
        """, athlete_rows)

    # ON CONFLICT refreshes capture columns + time_seconds on every conflict
    # (enables backfill). school/school_source use COALESCE(EXCLUDED, existing)
    # so a re-scrape that somehow lacks a school can't overwrite a good one.
    # "results" (the table name, not an alias) in the SET targets is required
    # by Postgres; EXCLUDED refers to the row we tried to insert.
    cursor = conn.cursor()
    try:
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO results (
                result_id, athlete_id, meet_id, div_id,
                time_seconds, grade, date, school, school_source, scraped_at,
                place, score, exhibition, official, team_id,
                is_pr, is_sr, has_splits, video_count, age_grade,
                source, id_system, person_id, status
            )
            VALUES %s
            ON CONFLICT (result_id) DO UPDATE SET
            athlete_id    = EXCLUDED.athlete_id,
            status        = COALESCE(EXCLUDED.status, results.status),
            -- fill person_id only if missing; NEVER overwrite one dedup wrote
            person_id     = COALESCE(results.person_id, EXCLUDED.person_id),
            -- fill div_id only if missing; a re-scrape of an EXISTING orphan row
            -- (div_id NULL, keyed by result_id) hits this UPDATE path, not the
            -- INSERT, so without this line the collector/saver div_id fix could
            -- never reach already-stored rows. COALESCE (not bare EXCLUDED)
            -- mirrors person_id above: backfill a NULL, but never clobber a good
            -- stored div_id with a re-scrape that happened to yield NULL.
            div_id        = COALESCE(results.div_id, EXCLUDED.div_id),
            time_seconds  = EXCLUDED.time_seconds,
            place         = EXCLUDED.place,
            score         = EXCLUDED.score,
            exhibition    = EXCLUDED.exhibition,
            official      = EXCLUDED.official,
            team_id       = EXCLUDED.team_id,
            is_pr         = EXCLUDED.is_pr,
            is_sr         = EXCLUDED.is_sr,
            has_splits    = EXCLUDED.has_splits,
            video_count   = EXCLUDED.video_count,
            age_grade     = EXCLUDED.age_grade,
            school        = COALESCE(EXCLUDED.school, results.school),
            school_source = COALESCE(EXCLUDED.school_source, results.school_source),
            scraped_at    = EXCLUDED.scraped_at
        """, rows)
    except Exception as e:
        print(f"[FK-FAIL] {e}", flush=True)
        for r in rows:
            if r[1] == 23636800:
                print(f"[HIM] full row: {r!r}", flush=True)
        print(f"[HIM-COUNT] {sum(1 for r in rows if r[1]==23636800)} rows with him", flush=True)
        raise

# _clean
# Purpose: Strip NUL (0x00) bytes from a string before it goes to Postgres -
#          Postgres text columns reject 0x00 even though Python allows it, and a
#          single embedded NUL from anet's data crashes the whole batch insert.
#          Pass-through for non-strings (None, ints, etc.).
# Arguments:
#           value: any field value headed for a text column.
# Output:   the value with NULs removed if it's a string, else unchanged.
def _clean(value):
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value

# saveResultsTFBulk
# Purpose: Bulk-inserts a list of TF result rows in one query. Field events
#          store their mark (Result) with time_seconds NULL; running events
#          parse time from SortInt. Writes the full per-result capture set
#          (exhibition/official/wind/place/score/round/heat/has_splits/
#          is_field/mark/team_id/event_type_id/video_count/age_grade/pr/sr).
#          ON CONFLICT only refreshes school/school_source/scraped_at on
#          still-legacy rows; the capture columns populate on INSERT only.
# Arguments:
#           conn: connection from the pool.
#           results: list of 8-tuples
#                    (result, meet_info, div_id, event_id, event_short,
#                     is_relay, school, is_field).
# Output: None.
def saveResultsTFBulk(conn, results: list):

    if not results:
        return

    # ⚠ THE TRACK TABLE HAD NO status COLUMN AT ALL. `results` has had one
    #   since issue 59; results_tf, sitting beside it, kept the letters in
    #   `mark` -- the same column a FIELD event's real mark lives in -- so
    #   "NH" could be a no-height or a genuine result and nothing could tell.
    #   191M rows, against 39M for XC.
    _ensureResultsStatus(conn, "results_tf")
 
    scraped_at = datetime.datetime.now(datetime.timezone.utc)
 
    rows = []
    for result, meet_info, div_id, event_id, event_short, is_relay, school, is_field in results:
 
        # Field events carry a mark, not a time: keep the mark string, leave
        # time_seconds NULL (the engine filters on is_field so these never enter
        # the time-based ratings). Running events parse time from SortInt/1000.
        if is_field:
            time_seconds = None
            mark = result.get("Result")
        else:
            # ! 20,000,000 IS anet TF's NON-FINISH, not a 5:33:20 --
            #   result_status.timeFromSortInt knows every sentinel.
            time_seconds = _timeFromSortInt(result.get("SortInt"))
            # a running non-finish keeps its letters in `mark`, the text
            # column the page already reads (issue 59); a real time keeps
            # mark NULL as before
            mark = _statusOf(result) if time_seconds is None else None
 
        # Meet date as YYYY-MM-DD.
        meet_date_raw = meet_info.get("MeetDate", "")
        meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""
 
        # school_source: 'scraped' if we resolved a school, else NULL (re-scrape target).
        school_source = "scraped" if school else None

        # ★ THE STATUS, IN ITS OWN COLUMN, FOR EVERY EVENT KIND. _statusOf
        #   returns None for a real result, so a field event's genuine mark is
        #   untouched and an "NH" is recorded as the no-height it is. `mark`
        #   keeps whatever it held, so no existing reader changes.
        status = _statusOf(result)

        row = (                          # <-- this assignment must exist
            result.get("IDResult"),
            None if is_relay else result.get("AthleteID"),
            meet_info.get("ID"),
            div_id,
            event_id,
            event_short,
            time_seconds,
            result.get("Grade", ""),
            meet_date,
            is_relay,
            school,
            school_source,
            scraped_at,
            _flag(result.get("Exhibition")),
            _flag(result.get("Official")),
            _toReal(result.get("Wind")),
            _toInt(result.get("Place")),
            _toInt(result.get("Score")),
            result.get("Round"),
            result.get("Heat"),
            _flag(result.get("hasSplitsSeries")),
            is_field,
            mark,
            _teamIdOrNone(result.get("TeamID")),
            result.get("EventTypeID"),
            _videoCount(result.get("MediaCount")),
            _toReal(result.get("AgeGrade")),
            result.get("pr"),
            result.get("sr"),
            "anet",    # source
            "anet",    # id_system
            None if is_relay else result.get("AthleteID"),   # person_id: seed at insert; relays have no person
            status,
        )


        # For each row cleans it and appends it to rows.
        cleaned = []
        for v in row:
            cleaned.append(_clean(v))
        rows.append(tuple(cleaned))

    deduped = {}
    for row in rows:
        deduped[row[0]] = row
    rows = list(deduped.values())
 
    cursor = conn.cursor()
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO results_tf (result_id, athlete_id, meet_id, div_id,
                            event_id, event_short, time_seconds, grade,
                            date, is_relay, school, school_source, scraped_at,
                            exhibition, official, wind, place, score, round,
                            heat, has_splits, is_field, mark, team_id,
                            event_type_id, video_count, age_grade, pr, sr,
                            source, id_system, person_id, status)
        VALUES %s
        ON CONFLICT (result_id) DO UPDATE SET
            athlete_id    = EXCLUDED.athlete_id,
            -- fill person_id only if missing; NEVER overwrite one dedup wrote
            person_id     = COALESCE(results_tf.person_id, EXCLUDED.person_id),
            time_seconds  = EXCLUDED.time_seconds,
            exhibition    = EXCLUDED.exhibition,
            official      = EXCLUDED.official,
            wind          = EXCLUDED.wind,
            place         = EXCLUDED.place,
            score         = EXCLUDED.score,
            round         = EXCLUDED.round,
            heat          = EXCLUDED.heat,
            has_splits    = EXCLUDED.has_splits,
            is_field      = EXCLUDED.is_field,
            mark          = EXCLUDED.mark,
            team_id       = EXCLUDED.team_id,
            event_type_id = EXCLUDED.event_type_id,
            video_count   = EXCLUDED.video_count,
            age_grade     = EXCLUDED.age_grade,
            pr            = EXCLUDED.pr,
            sr            = EXCLUDED.sr,
            school        = COALESCE(EXCLUDED.school, results_tf.school),
            school_source = COALESCE(EXCLUDED.school_source, results_tf.school_source),
            -- ! COALESCE, LIKE school. A re-scrape that comes back without the
            --   letters must not erase the ones we already have.
            status        = COALESCE(EXCLUDED.status, results_tf.status),
            scraped_at    = EXCLUDED.scraped_at
    """, rows)

# _cleanJson
# Purpose: Recursively strip NUL (0x00) from every string inside a JSON-able
#          structure (dict/list/str), since psycopg2's Json() adapter will
#          serialize an embedded NUL straight into the JSONB value and Postgres
#          rejects it. Non-strings pass through.
# Arguments:
#           obj: a dict, list, str, or scalar headed for a JSONB column.
# Output:   the same shape with all NULs removed from string values/keys.
# ⚠ THE SERVER ENCODING IS SQL_ASCII, AND psycopg2's Json USES
#   ensure_ascii=True (owner, 2026-09-18: "Save failed for TF meet 629415:
#   unsupported Unicode escape sequence ... Unicode escape value could not be
#   translated to the server's encoding SQL_ASCII", on an athlete named
#   Sad\u00e9). json.dumps escapes every non-ASCII character as \uXXXX, and
#   jsonb REJECTS a \uXXXX above ASCII when the server encoding is SQL_ASCII
#   -- it has no encoding to translate the codepoint into. The whole meet's
#   save was rolled back for one accented name.
#
# ★ ensure_ascii=False SENDS THE CHARACTER, NOT AN ESCAPE. The literal UTF-8
#   bytes go over the wire and SQL_ASCII stores any byte sequence as-is, so
#   the name round-trips and nothing is lost. This is why the TEXT columns
#   were never affected: only jsonb parses \u escapes.
#
# ! NOT A REPLACE-THE-CHARACTER FIX. Stripping accents would quietly rename
#   real athletes, which is worse than the crash it fixes.
class _Utf8Json(psycopg2.extras.Json):
    def dumps(self, obj):
        return json.dumps(obj, ensure_ascii=False)


def _cleanJson(obj):
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, list):
        out = []
        for item in obj:
            out.append(_cleanJson(item))
        return out
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            out[_cleanJson(key)] = _cleanJson(value)
        return out
    return obj


# saveMeetExtras
# Purpose: Upserts (update + insert) one meet's auxiliary JSONB blobs. One row
#          per (meet_id, sport). Conn-taking, runs inside the caller's
#          transaction.
#
#          CHANGED: added the team_scores_array param + the team_scores_json
#          column. XC passes its raw teamScores[] blob here; TF leaves it None
#          (TF reconstructs team scores from per-result Score + TeamID).
# Arguments:
#           conn:
#               Open connection from the caller's getConn() block.
#           meet_id:
#               athletic.net meet ID (first half of the PK).
#           sport:
#               "xc" or "tf" (second half of the PK).
#           teams_array:
#               teams[] roster list, or None.
#           event_types_array:
#               eventTypes[] implement-spec catalog list, or None. Structurally
#               None for XC (no implements).
#           relay_legs_array:
#               relayLegs[] composition list, or None. Structurally None for XC
#               (no relays).
#           team_scores_array:
#               teamScores[] list, or None. Populated for XC; None for TF.
#               (Each array is Json-wrapped -> JSONB; None -> SQL NULL.)
# Output:
#           None. One upserted row in meet_extras.
def saveMeetExtras(conn, meet_id, sport, teams_array, event_types_array,
                   relay_legs_array, team_scores_array=None):
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO meet_extras (
            meet_id, sport,
            teams_json, event_types_json, relay_legs_json, team_scores_json,
            source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meet_id, sport, source) DO UPDATE
            SET teams_json       = EXCLUDED.teams_json,
                event_types_json = EXCLUDED.event_types_json,
                relay_legs_json  = EXCLUDED.relay_legs_json,
                team_scores_json = EXCLUDED.team_scores_json

        """,
        (
            meet_id,
            sport,
            _Utf8Json(_cleanJson(teams_array))       if teams_array       is not None else None,
            _Utf8Json(_cleanJson(event_types_array)) if event_types_array is not None else None,
            _Utf8Json(_cleanJson(relay_legs_array))  if relay_legs_array  is not None else None,
            _Utf8Json(_cleanJson(team_scores_array)) if team_scores_array is not None else None,
            "anet",
        ),
    )

# ─────────────────────────────────────────────────────────────────────────────
# Recovery Tracking
# ─────────────────────────────────────────────────────────────────────────────


# logScrapedEventsTFBulk
# Purpose: Records all successfully scraped TF event/div combos in one query.
#          Called after saveResultsTFBulk so we only log events whose results
#          actually made it to the DB.
# Arguments:
#           events: list of (meet_id, event_short, div_id) tuples.
# Output: None. Inserts rows into tf_scraped_events, skips duplicates.
def logScrapedEventsTFBulk(events: list):

    if not events:
        return

    now = datetime.datetime.utcnow().isoformat()[:19]

    # Add scraped_at timestamp to every tuple.
    rows = [(meet_id, event_short, div_id, now) for meet_id, event_short, div_id in events]

    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    with getConn() as conn:
        cursor = conn.cursor()
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO tf_scraped_events (meet_id, event_short, div_id, scraped_at)
            VALUES %s
            ON CONFLICT (meet_id, event_short, div_id) DO NOTHING
        """, rows)
        conn.commit()

# populateRecoveryQueue
# Purpose: Bulk-inserts failed event/div combos into tf_recovery_queue.
#          Safe to call multiple times.
# Arguments:
#           rows: list of (meet_id, event_short, div_id) tuples.
# Output: None. Returns count of newly inserted rows for logging.
def populateRecoveryQueue(rows: list) -> int:
 
    if not rows:
        return 0
 
    with getConn() as conn:
        cursor = conn.cursor()
        
        # Uses execute_values for bulk insert.
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO tf_recovery_queue (meet_id, event_short, div_id, scraped)
            VALUES %s
            ON CONFLICT (meet_id, event_short, div_id) DO NOTHING
        """, [(meet_id, event_short, div_id, 0)
              for meet_id, event_short, div_id in rows])
 
        # rowcount tells us how many rows were actually inserted
        # (vs skipped by ON CONFLICT). Useful for logging.
        inserted = cursor.rowcount
        conn.commit()
 
    return inserted

# ─────────────────────────────────────────────────────────────────────────────
# Queue management
# ─────────────────────────────────────────────────────────────────────────────


# getBatchUnscrapedMeets
# Purpose: Claims up to batch_size meet_id/sport pairs from the shared
#          meet_queue pool and marks them status=3 (in-progress) so no
#          other session claims the same work. Replaces the old session-
#          stride sweep — sessions now pull from one shared pool instead
#          of each owning a disjoint range.
#
#          Unlike the old version, this does NOT generate a candidate
#          range or LEFT JOIN against it — meet_queue is assumed to
#          already contain a status=0 row for every (meet_id, sport) in
#          range, via the one-time backfill script. That's what makes
#          this a single fast atomic claim instead of an expensive
#          range-generation query on every call.
#
# Arguments:
#           batch_size: how many (meet_id, sport) pairs to claim.
# Output: dict mapping meet_id (int) → list of sports still needing
#         work, e.g. {101: ["XC", "TF"], 5601: ["TF"]}.
#         Empty dict means no status=0 work remains — scraping is done.
def getBatchUnscrapedMeets(batch_size: int, sport: str = None,
                           states=(0,)) -> dict:
    
    with getConn() as conn:
        cursor = conn.cursor()
        rows = _claimMeetBatch(cursor, batch_size, sport, states)
        conn.commit()  # commit the claim so other sessions see status=3 immediately
 
    return _buildMeetSportDict(rows)

# _claimMeetBatch
# Purpose: Atomically claims up to batch_size rows from meet_queue that
#          are still status=0 (queued, never touched), flipping them to
#          status=3 (in-progress) in the same statement that selects
#          them. This is the standard "claim a job from a queue table"
#          pattern.
#
#          The inner SELECT ... FOR UPDATE SKIP LOCKED finds status=0
#          rows and locks them. SKIP LOCKED means: if another session's
#          claim query already has a lock on a row (mid-claim right now),
#          this query skips it instead of waiting — so two sessions
#          calling this at the same instant partition the available rows
#          between them instead of racing or blocking each other.
#
#          The outer UPDATE then flips exactly those locked rows to
#          status=3 and returns them — one round trip, no separate
#          SELECT-then-UPDATE pair, so there's no gap between "found it"
#          and "claimed it" for another session to slip into.
#
# Arguments:
#           cursor: open DB cursor.
#           batch_size: max number of (meet_id, sport) rows to claim.
# Output: list of (meet_id, sport) tuples actually claimed this call.
#         May be shorter than batch_size if fewer than batch_size rows
#         are left at status=0 (i.e. scraping is nearly done).
def _claimMeetBatch(cursor, batch_size, sport=None, states=(0,)):
    # ⚠⚠ WHICH STATES TO CLAIM IS THE CALLER'S, AND A RETRY MUST NOT GO
    #    THROUGH STATE 0 (owner, 2026-09-18: "I think you're checking more ids
    #    than just the failed ones. I'm not even sure you're resetting the ids
    #    at all for the failed ones").
    #
    #    Both halves of that are right, and they are the same fault. The first
    #    retry-only mode reset states 2 and 3 to 0 and then claimed state 0 --
    #    which is EVERY due row, including the thousands an earlier forward
    #    walk had seeded and never scraped. So the run was an ordinary scrape
    #    night wearing a retry flag, and no output could tell you whether the
    #    reset had happened, because the reset made the failures identical to
    #    everything else.
    #
    # ★ SO A RETRY CLAIMS 2 AND 3 DIRECTLY AND RESETS NOTHING. The set it
    #   works on is then exactly the failures, by construction rather than by
    #   hoping the queue was empty.

    # ⚠ THERE WAS NO ORDER BY, AND THAT IS WHY A RUN CAN BE ALL ONE SPORT
    #   (owner, 2026-09-18: "I swear I didn't see a single xc meet"). Without
    #   one, Postgres returns whatever the scan reaches first -- physical row
    #   order -- so whichever sport's rows happen to sit earlier in the table
    #   are claimed first and the other waits until they are gone. Ordering by
    #   meet_id interleaves nothing either, but it makes the run REPEATABLE
    #   and it is `sport` below that answers the actual want.
    #
    # ! sport= SCOPES A RUN TO ONE SPORT (ANET_SPORT in the launcher). The
    #   honest way to see cross country tonight is to ask for cross country,
    #   not to hope the planner offers it.
    # ★ FAIR ACROSS SPORTS WHEN NO SPORT IS NAMED (owner, 2026-09-18: "can
    #   you make it so it does xc and tf so I don't gotta unautomate"). A
    #   single ORDER BY meet_id would hand back ALL of one sport before any of
    #   the other, because anet's XC and TF id spaces are disjoint -- XC tops
    #   out near 276,000 and TF near 671,000, so ordering by id is ordering by
    #   sport. Half the batch each, so both advance together and one sport
    #   stalling does not park the other.
    #
    # ! AND A SPORT WITH NOTHING DUE GIVES ITS HALF BACK, so the batch stays
    #   full once one sport finishes rather than halving throughput.
    if sport:
        return _claimOneSport(cursor, batch_size, sport, states)

    half = max(1, batch_size // 2)
    rows = _claimOneSport(cursor, half, "XC", states)
    rows += _claimOneSport(cursor, batch_size - len(rows), "TF", states)
    if len(rows) < batch_size:
        rows += _claimOneSport(cursor, batch_size - len(rows), "XC", states)
    return rows


def _claimOneSport(cursor, limit, sport, states=(0,)):
    if limit <= 0:
        return []
    cursor.execute(
        """
        UPDATE meet_queue
        SET scraped = 3
        WHERE (meet_id, sport, source) IN (
            SELECT meet_id, sport, source
            FROM meet_queue
            WHERE scraped = ANY(%s) AND source = 'anet' AND sport = %s
            ORDER BY meet_id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        RETURNING meet_id, sport
        """,
        (list(states), sport, limit),
    )
    return list(cursor.fetchall())

# _buildMeetSportDict
# Purpose: Reshapes flat (meet_id, sport) rows into a dict grouping all
#          sports needing work per meet_id. Pure Python — no DB call.
#          e.g. rows=[(101,"XC"),(101,"TF"),(5601,"TF")] →
#               {101: ["XC", "TF"], 5601: ["TF"]}
#
# Arguments:
#           rows: list of (meet_id, sport) tuples, as returned by
#                 _claimMeetBatch.
# Output: dict mapping meet_id (int) → list of sport strings.
def _buildMeetSportDict(rows):
    result = {}

    for meet_id, sport in rows:
        # setdefault(meet_id, []) creates an empty list the first time
        # we see a meet_id so we can have up to two sports as the value.
        result.setdefault(meet_id, []).append(sport)

    return result

# _migrateMeetQueueCompositeKey
# Purpose: Changes meet_queue's primary key from (meet_id) to
#          (meet_id, sport) — a meet_id can now have separate rows for
#          XC and TF, since athletic.net meets can have both. Existing
#          rows are unaffected (each meet_id had exactly one sport
#          before, so (meet_id, sport) is still unique for them).
#          Idempotent — checks if the old single-column PK still exists
#          before doing anything.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _migrateMeetQueueCompositeKey(cursor):

     # Finds the current primary key constraint name on meet_queue.
    cursor.execute("""
        -- Finds the constraint on table (conrelid) meet_queue where
        -- the constraint type (contype) is primary ('p').
        -- Names this constraint conname. pg_constraint
        -- is Postgres's built-in system catalogue table
        -- (tables, constraints, indexes, etc).
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'meet_queue'::regclass and contype = 'p'
    """)
    row = cursor.fetchone()

    # If the PK is already the 3-column (meet_id, sport, source) form, the
    # source migration has run — do NOT try to reassert the 2-col PK (it now
    # fails on legitimate anet+tfrrs duplicate (meet_id, sport) pairs).
    cursor.execute("""
        SELECT array_length(conkey, 1) FROM pg_constraint
        WHERE conrelid = 'meet_queue'::regclass AND contype = 'p'
    """)
    existing = cursor.fetchone()
    if existing is not None and existing[0] == 3:
        return
 
    if row is None:
        # No primary key at all, add the composite one
        cursor.execute("""
            ALTER TABLE meet_queue ADD PRIMARY KEY (meet_id, sport)
        """)
        return
 
    constraint_name = row[0]
 
    # If it's already composite (2 columns), nothing to do.
    cursor.execute("""
        -- conkey is a column in pg_constraint that stores which columns
        -- this constraint covers, returned as an array of column
        -- position numbers, e.g. {1, 2, 3} if it covers columns 1, 2, and 3.
        -- The , 1 means check only the 1st dimension. This gets how many
        -- columns the primary key takes up.
        SELECT array_length(conkey, 1) FROM pg_constraint
        WHERE conname = %s
    """, (constraint_name,))
    n_cols = cursor.fetchone()[0]
 
    if n_cols == 2:
        return  # already migrated
 
    # Drop the old single-column PK and add the composite one.
    cursor.execute(f"ALTER TABLE meet_queue DROP CONSTRAINT {constraint_name}")
    cursor.execute("ALTER TABLE meet_queue ADD PRIMARY KEY (meet_id, sport)")

# getQueueStatus
# Purpose: Looks up the scraped status for one (meet_id, sport) pair.
# Arguments:
#           meet_id: athletic.net meet ID.
#           sport: "XC" or "TF".
# Output: Integer scraped status (0=unscraped, 1=done, 2=failed) if a
#         row exists for this (meet_id, sport), or None if no row
#         exists at all — meaning we've never attempted this combo.
def getQueueStatus(meet_id: int, sport: str):

    with getConn() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT scraped FROM meet_queue
            WHERE meet_id = %s AND sport = %s
        """, (meet_id, sport))

        # fetchone() returns either a one-element tuple like (1,)
        # or None if no row matched the WHERE clause.
        row = cursor.fetchone()

    # row[0] unpacks the scraped value out of the tuple.
    # If row is None (no match), we return None to the caller —
    # this is the "never attempted" signal.
    return row[0] if row is not None else None

# markScraped
# Purpose: Updates a meet's scraped status in the queue.
# Arguments:
#           meet_id: meet to update.
#           sport: "XC" or "TF" — which sport's row to write.
#           status: 1=done, 2=failed, 0=reset, 3=in-progress, 4=skipped
# Output: None.
def markScraped(meet_id: int, sport: str, status: int = 1):

    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor, """
            INSERT INTO meet_queue (meet_id, sport, scraped, source)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (meet_id, sport, source)
            DO UPDATE SET scraped = EXCLUDED.scraped
        """, (meet_id, sport, status, "anet"))
        conn.commit()

# resetInProgress
# Purpose: Resets any meets stuck in-progress (scraped=3) back to
#          unscraped (scraped=0).
# Arguments: None.
# Output: None.
def resetInProgress():
    
    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    # ⚠ IT RESET 2, NOT 3, AND IT IS CALLED resetInProgress (owner,
    #   2026-09-18: "Reset 0 in-progress meets" printed while 308 rows sat at
    #   in-progress). 3 is in-progress; 2 is failed. So a batch claimed by a
    #   session that then died stayed at 3 FOREVER -- never claimed again,
    #   never reported, just gone from the queue's working set. Every crashed
    #   session leaked its batch, permanently.
    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE meet_queue SET scraped = 0 "
                       "WHERE scraped = 3 AND source = 'anet'")
        stranded = cursor.rowcount
        # 2 is failed, and re-trying failures on every start is the existing
        # behaviour -- kept, but counted separately so the two are legible.
        cursor.execute("UPDATE meet_queue SET scraped = 0 "
                       "WHERE scraped = 2 AND source = 'anet'")
        failed = cursor.rowcount
        conn.commit()
    print(f"[DB] Requeued {stranded} stranded in-progress and {failed} "
          f"failed meets")


# getUnscrapedRecoveryEvents
# Purpose: Atomically fetches and locks a batch of unscraped recovery events.
#          Same UPDATE...RETURNING pattern as getUnscrapedMeets — marks rows
#          as in-progress (scraped=3) and returns them in one query so two
#          sessions can never grab the same event.
# Arguments:
#           limit: max rows to return. Default 200 — recovery events are
#                  slower than meets since each needs a JWT fetch.
# Output: List of (meet_id, event_short, div_id) tuples.
def getUnscrapedRecoveryEvents(limit: int = 200) -> list[tuple[int, str, int]]:
 
    with getConn() as conn:
        cursor = conn.cursor()
 
        # UPDATE...RETURNING atomically marks rows in-progress and
        # returns them. No other session can grab the same rows because
        # Postgres row-locks them during the UPDATE.
        cursor.execute("""
            UPDATE tf_recovery_queue SET scraped = 3
            -- Select these values where they exist and are unscraped.
            WHERE (meet_id, event_short, div_id) IN (
                SELECT meet_id, event_short, div_id
                FROM tf_recovery_queue
                Where scraped = 0
                Limit %s
            )
            -- This means we get back what we locked to the table in one query.
            RETURNING meet_id, event_short, div_id
        """, (limit,))
 
        rows = cursor.fetchall()
        conn.commit()
 
    return rows
 
 
# markRecoveryEventScraped
# Purpose: Updates the scraped status of one event in tf_recovery_queue.
# Arguments:
#           meet_id: athletic.net meet ID.
#           event_short: event code e.g. "100m", "1mile".
#           div_id: division ID.
#           status: 1=done, 2=failed.
# Output: None.
def markRecoveryEventScraped(meet_id: int, event_short: str,
                              div_id: int, status: int):
 
    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor, """
            UPDATE tf_recovery_queue
            SET scraped = %s
            WHERE meet_id = %s AND event_short = %s AND div_id = %s
        """, (status, meet_id, event_short, div_id))
        conn.commit()

# getLegacySchoolMeets
# Purpose: Finds every meet_id that has at least one result with
#          school_source IS NULL — i.e. scraped before the
#          per-result school fix. Feed this list into the
#          existing scrapeMeetBySport(page, meet_id, sport, label)
#          to re-scrape and backfill school/school_source via the
#          ON CONFLICT ... DO UPDATE ... WHERE school_source IS NULL
#          clause in saveResult/saveResultTF above (which only
#          touches still-legacy rows, so partial progress is safe
#          to re-run).
# Arguments:
#           conn: connection from the pool.
#           sport: "xc" or "tf" — which table to check.
# Output: list of meet_ids (ints) needing re-scrape for this sport.
def getLegacySchoolMeets(conn, sport: str) -> list[int]:
 
    table = "results" if sport == "xc" else "results_tf"
 
    cursor = conn.cursor()
 
    # DISTINCT — many results share a meet_id; we only need each
    # meet once for the re-scrape list.
    cursor.execute(f"""
        SELECT DISTINCT meet_id
        FROM {table}
        WHERE school_source IS NULL
    """)
 
    # fetchall() returns a list of single-element tuples, e.g.
    # [(101,), (102,), ...] — the [0] pulls the int out of each.
    return [row[0] for row in cursor.fetchall()]

# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────────────────────

# countRows
# Purpose: Prints row counts for main tables. Used in final summary.
# Arguments: None.
# Output: None.
def countRows():

    with getConn() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM athletes")
        athletes = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM results")
        results = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM meets")
        meets = cursor.fetchone()[0]

    print(f"[DB] Athletes: {athletes} | Results: {results} | Meets: {meets}")


# countQueue
# Purpose: Prints the total number of meets in the queue. Used for diagnostics.
# Arguments: None.
# Output: None.
def countQueue():

    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM meet_queue")
        n = cursor.fetchone()[0]

    print(f"[DB] Meet queue: {n} meets")

# countRecoveryRemaining
# Purpose: Returns count of unscraped rows in tf_recovery_queue.
# Arguments: None.
# Output: Integer count.
def countRecoveryRemaining() -> int:
 
    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM tf_recovery_queue WHERE scraped = 0
        """)
        n = cursor.fetchone()[0]
 
    return n

# _migrateMeetQueueAddSource
# Purpose: Add a `source` column to meet_queue and widen the PK to
#          (meet_id, sport, source), so anet and tfrrs can share the numeric
#          id space without colliding. Existing rows are all anet, so the
#          column defaults to 'anet' — every current row is tagged correctly
#          in one shot. Idempotent: re-running is a no-op (checks the PK arity
#          the same way _migrateMeetQueueCompositeKey does).
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None.
def _migrateMeetQueueAddSource(cursor):

    # Add the column if missing. DEFAULT 'anet' backfills every existing row
    # (all anet today) in the same statement; NOT NULL so the PK can include it.
    cursor.execute("""
        ALTER TABLE meet_queue
        ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'anet'
    """)

    # Find the current PK constraint name (same catalog lookup as the composite
    # migration). If the PK already covers 3 columns, we've run before -> stop.
    cursor.execute("""
        SELECT conname, array_length(conkey, 1)
        FROM pg_constraint
        WHERE conrelid = 'meet_queue'::regclass AND contype = 'p'
    """)
    row = cursor.fetchone()

    if row is not None and row[1] == 3:
        return  # already (meet_id, sport, source)

    # Drop the old PK (whatever its arity) and add the 3-column one. Quoting the
    # name with the f-string is safe: it came straight from the catalog.
    if row is not None:
        cursor.execute(f"ALTER TABLE meet_queue DROP CONSTRAINT {row[0]}")
    cursor.execute("ALTER TABLE meet_queue ADD PRIMARY KEY (meet_id, sport, source)")

# _migrateMeetExtrasAddSource
# Purpose: Same migration for meet_extras: add `source` + widen the PK to
#          (meet_id, sport, source). This is what lets the tfrrs driver write
#          team_scores_json under its own row instead of overwriting the anet
#          meet that shares the id. Existing rows are all anet -> DEFAULT 'anet'.
#          Idempotent (checks PK arity). Touches only source/PK — independent of
#          which JSON columns the table has.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None.
def _migrateMeetExtrasAddSource(cursor):
 
    cursor.execute("""
        ALTER TABLE meet_extras
        ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'anet'
    """)
 
    cursor.execute("""
        SELECT conname, array_length(conkey, 1)
        FROM pg_constraint
        WHERE conrelid = 'meet_extras'::regclass AND contype = 'p'
    """)
    row = cursor.fetchone()
 
    if row is not None and row[1] == 3:
        return  # already (meet_id, sport, source)
 
    if row is not None:
        cursor.execute(f"ALTER TABLE meet_extras DROP CONSTRAINT {row[0]}")
    cursor.execute("ALTER TABLE meet_extras ADD PRIMARY KEY (meet_id, sport, source)")

# ─────────────────────────────────────────────────────────────────────────────
# Tfrrs Queue management
# ─────────────────────────────────────────────────────────────────────────────


# claimTFRRSMeetBatch
# Purpose: TFRRS analog of getBatchUnscrapedMeets, but (a) scoped to
#          source='tfrrs' so it never claims anet rows sharing the id space, and
#          (b) returns a flat list of (meet_id, sport) TUPLES — the shape the
#          drain loop iterates — not anet's {meet_id: [sports]} dict. Opens and
#          commits its OWN connection (the driver does NOT pass one in).
# Arguments:
#           batch_size: max rows to claim this round.
# Output:   list of (meet_id, sport) tuples actually claimed (status 0 -> 3).
#           Empty list = no tfrrs work left.
def claimTFRRSMeetBatch(batch_size: int, states=(0,)) -> list:
    with getConn() as conn:
        cursor = conn.cursor()
        rows = _claimTFRRSBatch(cursor, batch_size, states)
        conn.commit()   # publish the 0->3 flip so a concurrent run can't re-grab
    return rows
 
 
# _claimTFRRSBatch
# Purpose: The atomic claim itself — same UPDATE ... WHERE (...) IN (SELECT ...
#          FOR UPDATE SKIP LOCKED LIMIT n) RETURNING pattern as anet's
#          _claimMeetBatch, but keyed on the FULL TRIPLE (meet_id, sport, source)
#          so it can never touch the anet sibling at the same (meet_id, sport).
# Arguments:
#           cursor:     open cursor.
#           batch_size: max rows to claim.
# Output:   list of (meet_id, sport) tuples.
# ! states= IS THE RETRY PATH, exactly as in _claimMeetBatch: a retry claims
#   2 and 3 themselves rather than resetting them to 0 and claiming everything.
def _claimTFRRSBatch(cursor, batch_size, states=(0,)):
    cursor.execute(
        """
        UPDATE meet_queue
        SET scraped = 3
        WHERE (meet_id, sport, source) IN (
            SELECT meet_id, sport, source
            FROM meet_queue
            WHERE scraped = ANY(%s) AND source = 'tfrrs'
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        RETURNING meet_id, sport
        """,
        (list(states), batch_size),
    )
    return cursor.fetchall()
 
 
# resetTFRRSInProgress
# Purpose: At launch, flip any tfrrs rows stranded in-progress (scraped=3) back
#          to 0 — scoped to source='tfrrs' so a crashed tfrrs run resumes WITHOUT
#          disturbing anet's queue. Mirrors resetInProgress, source-filtered.
# Arguments: None.
# Output:   None (prints the count).
def resetTFRRSInProgress():
    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE meet_queue SET scraped = 0 WHERE scraped = 3 AND source = 'tfrrs'"
        )
        count = cursor.rowcount
        conn.commit()
    print(f"[DB] Reset {count} in-progress tfrrs meets to unscraped")
 

if __name__ == "__main__":
    initPool()
    createTables()
    closePool()