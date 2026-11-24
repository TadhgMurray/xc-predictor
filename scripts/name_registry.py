"""scripts/name_registry.py -- the linkers' name registry, built once per
04a run and kept exact between its passes.

★ WHY (2026-11-10, run 20261108_155220's 04a_link_tfrrs: 1,930 s in line).
  link_uploads.gather() finds every person who carries one of the names it
  is about to judge -- "THE WHOLE REGISTRY AND BOTH RESULT TABLES, NOT A
  SURNAME PREFILTER: a namesake missed here is a link that looks unique and
  is not". So it read every distinct (person, name) pair of athletes,
  results and results_tf and computed the name key of each -- about nine
  minutes -- and three passes in one 04a (tfrrs high school, uploads,
  timers) did it three times, 552 + 537 + 539 s, for a few hundred to a few
  thousand rows each.

HOW, WITH THE SAME ANSWER. install() builds the registry once -- every
distinct (person_id, name key, name), the query gather() ran, without its
final "name in this pass's keys" filter -- as a TEMP table on the linker's
connection. Every pass between changes people: links, mints, pins. So while
04a runs, triggers on athletes, results and results_tf write the OLD and
the NEW person of every row whose person or name changes (and of every row
inserted or deleted) to lu_person_moves -- a trigger, not the linkers'
logs, so no writer can be missed (person_link_log skips a re-link under the
same rule: ON CONFLICT DO NOTHING). Before each pass refresh() rebuilds the
registry's rows for exactly those persons from their own rows (by index),
so the registry is what a full scan would read at that moment. gather()
then takes its namesakes from it by name key.
  * A writer that rolls back takes its move rows with it; a pass that
    rolls back takes its refresh with it -- the two stay in step.
  * uninstall() drops the triggers (finally, in link_tfrrs_rows.main). A
    leftover from a killed run is replaced by the next install; the
    05_backfill rebuild of results drops it in any case.
  * Anything failing in install() leaves the registry off: gather() runs
    its full query, as before. XCP_LINK_REGISTRY=0 turns it off.
  * Only link_tfrrs_rows (04a, the nightly's 04a) installs it; the linkers
    run on their own read the corpus as before.
"""
import os
import time

_READY = {}           # id(conn) -> True once the registry is live on that connection

TABLES = {
    # table: (the columns whose change moves a person's names, the pairs' SQL)
    "athletes": ("person_id, first_name, last_name", None),
    "results": ("person_id, athlete_name", None),
    "results_tf": ("person_id, athlete_name, is_relay", None),
}

TRIGGER_FN = """
CREATE OR REPLACE FUNCTION lu_note_person_move() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.person_id IS NOT NULL THEN
    INSERT INTO lu_person_moves VALUES (OLD.person_id);
  END IF;
  IF TG_OP IN ('UPDATE', 'INSERT') AND NEW.person_id IS NOT NULL THEN
    INSERT INTO lu_person_moves VALUES (NEW.person_id);
  END IF;
  RETURN NULL;
END $$"""


def _pairsSql(where_a="", where_r="", where_t=""):
    """Every (person_id, name key, name) the registry holds -- gather()'s own
    lu_p query without its name filter; the where_* narrow each source to
    some persons (refresh)."""
    from link_uploads import nameKeyTextSql
    from link_teamless import nameKeySql
    return f"""
        SELECT DISTINCT person_id, nm, name FROM (
            SELECT a.person_id, {nameKeySql('a.first_name', 'a.last_name')} AS nm,
                   concat_ws(' ', btrim(a.first_name), btrim(a.last_name)) AS name
            FROM   athletes a WHERE a.person_id IS NOT NULL {where_a}
            UNION ALL
            SELECT g.person_id, {nameKeyTextSql('g.athlete_name')}, g.athlete_name
            FROM   (SELECT DISTINCT person_id, athlete_name FROM results
                    WHERE person_id IS NOT NULL AND NULLIF(btrim(athlete_name), '') IS NOT NULL {where_r}
                    UNION
                    SELECT DISTINCT person_id, athlete_name FROM results_tf
                    WHERE person_id IS NOT NULL AND NULLIF(btrim(athlete_name), '') IS NOT NULL
                      AND COALESCE(is_relay, 0) = 0 {where_t}) g
        ) x"""


def enabled():
    return os.environ.get("XCP_LINK_REGISTRY", "1") not in ("0", "false", "")


def ready(conn):
    return _READY.get(id(conn), False)


def install(conn, tries=None, pause=20):
    """_installOnce, retried while the trigger DDL only lost a lock wait.
    ★ (2026-11-17; run 20261116_204106: "off (LockNotAvailable ...)", and
    04a took 1,977 s against 919 -- every pass then read the corpus in full,
    ~9 min each). CREATE TRIGGER waits behind any writer or vacuum on
    athletes / results / results_tf; a 30-second wait can lose to one that
    ends a minute later. XCP_LINK_REGISTRY_TRIES (8) tries, `pause` apart."""
    import psycopg2.errors
    tries = int(tries or os.environ.get("XCP_LINK_REGISTRY_TRIES") or 8)
    for k in range(1, tries + 1):
        try:
            return _installOnce(conn, raise_lock=(k < tries))
        except psycopg2.errors.LockNotAvailable:
            print(f"[name-registry] the trigger lock was busy (try {k}/{tries}); "
                  f"again in {pause}s", flush=True)
            time.sleep(pause)
    return False


def _installOnce(conn, raise_lock=False):
    """Triggers on, registry built, committed. False (and the registry off)
    on any failure -- gather() then reads the corpus in full, as before.
    raise_lock: a lost lock wait is raised (install retries it), not turned off."""
    if not _triggersOnce(conn, raise_lock):
        return False
    return _build(conn)


def _triggersOnce(conn, raise_lock=False):
    """The move triggers on all three tables, committed. False (triggers
    dropped again) on any failure but a lost lock wait under raise_lock."""
    if not enabled():
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '30s'")
            cur.execute("CREATE UNLOGGED TABLE IF NOT EXISTS lu_person_moves (person_id bigint)")
            cur.execute("TRUNCATE lu_person_moves")
            cur.execute(TRIGGER_FN)
            for table, (cols, _) in TABLES.items():
                cur.execute(f"DROP TRIGGER IF EXISTS lu_person_move ON {table}")
                when = " OR ".join(f"OLD.{c.strip()} IS DISTINCT FROM NEW.{c.strip()}"
                                   for c in cols.split(","))
                cur.execute(f"""CREATE TRIGGER lu_person_move AFTER UPDATE OF {cols} ON {table}
                                FOR EACH ROW WHEN ({when}) EXECUTE FUNCTION lu_note_person_move()""")
                cur.execute(f"DROP TRIGGER IF EXISTS lu_person_move_id ON {table}")
                cur.execute(f"""CREATE TRIGGER lu_person_move_id AFTER INSERT OR DELETE ON {table}
                                FOR EACH ROW EXECUTE FUNCTION lu_note_person_move()""")
        conn.commit()                       # the triggers live from here on
    except Exception as exc:                # noqa: BLE001 -- the full query still works
        conn.rollback()
        import psycopg2.errors
        if raise_lock and isinstance(exc, psycopg2.errors.LockNotAvailable):
            raise
        uninstall(conn, quiet=True)
        print(f"[name-registry] off ({type(exc).__name__}: {exc}); every pass reads the corpus "
              "in full", flush=True)
        return False
    return True


def _build(conn, uninstall_on=None, say=True):
    """lu_reg on `conn` (a TEMP table: only `conn` sees it), committed; the
    triggers must already be live. -> True, or False with the registry off
    and the triggers dropped on `uninstall_on` (default `conn`; False leaves
    that to the caller). say=False keeps the message for besideJoin to
    print, so a thread prints nothing between the passes' lines."""
    t0 = time.time()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL work_mem = '512MB'")
            cur.execute("DROP TABLE IF EXISTS lu_reg")
            cur.execute(f"CREATE TEMP TABLE lu_reg AS {_pairsSql()}")
            cur.execute("CREATE INDEX ON lu_reg (nm)")
            cur.execute("CREATE INDEX ON lu_reg (person_id)")
            cur.execute("ANALYZE lu_reg")
            cur.execute("SELECT count(*) FROM lu_reg")
            n = cur.fetchone()[0]
        conn.commit()
    except Exception as exc:                # noqa: BLE001 -- the full query still works
        conn.rollback()
        if uninstall_on is not False:
            uninstall(uninstall_on or conn, quiet=True)
        msg = (f"[name-registry] off ({type(exc).__name__}: {exc}); every pass reads the corpus "
               "in full")
        _say(msg, say)
        return False
    _READY[id(conn)] = True
    _say(f"[name-registry] built once: {n:,} (person, name) pairs ({time.time() - t0:.0f}s); "
         "the passes after this refresh only the persons that moved", say)
    return True


_LAST = []


def _say(msg, say):
    if say:
        print(msg, flush=True)
    else:
        _LAST.append(msg)


# ★ BUILT BESIDE PASSES 1 AND 2, ON A SECOND CONNECTION (2026-11-19; run
#   20261116_204106: 04a 919 s on a normal night, the registry's one full
#   read the largest part of it). Passes 1 (fan-out) and 2 (freshmen) never
#   read the registry -- only 2b, 4 and 5 do -- so its build need not wait
#   for them. besideStart() turns the triggers on (here, before either pass
#   writes, exactly as install() does) and builds lu_reg in a thread on its
#   own connection; besideJoin() waits for it before pass 2b and hands back
#   that connection, on which 2b, 4 and 5 then run (lu_reg is a TEMP table:
#   only its own connection sees it).
#   ! THE SAME REGISTRY. The triggers are committed before the build starts
#     and before pass 1 writes, so a move of passes 1-2 is either in the
#     build's snapshot (committed before it) or in lu_person_moves (every
#     move after the triggers, committed with its writer), and refresh()
#     rebuilds every person in lu_person_moves at 2b -- the guarantee the
#     registry always gave writers on other connections (the site's, and
#     tests/test_name_registry.py's second writer).
#   ! MEMORY: one more backend for the build's length, at work_mem 512MB --
#     what the serial build used, now beside pass 1's (1 GB) instead of
#     before it. XCP_LINK_REGISTRY_BG=0 builds it first, on the linker's own
#     connection, as before.
def background():
    return os.environ.get("XCP_LINK_REGISTRY_BG", "1") not in ("0", "false", "")


def besideStart(conn, conn2, tries=None, pause=20):
    """Triggers on `conn` (install()'s retries), then lu_reg built on `conn2`
    in a thread. -> a handle for besideJoin, or None when the triggers could
    not go on (the registry is off; the passes read the corpus in full)."""
    import threading
    import psycopg2.errors
    tries = int(tries or os.environ.get("XCP_LINK_REGISTRY_TRIES") or 8)
    on = False
    for k in range(1, tries + 1):
        try:
            on = _triggersOnce(conn, raise_lock=(k < tries))
            break
        except psycopg2.errors.LockNotAvailable:
            print(f"[name-registry] the trigger lock was busy (try {k}/{tries}); "
                  f"again in {pause}s", flush=True)
            time.sleep(pause)
    if not on:
        return None
    box = {}

    def work():
        try:
            box["ok"] = _build(conn2, uninstall_on=False, say=False)
        except BaseException as exc:        # noqa: BLE001 -- reported at the join
            box["exc"] = exc
    th = threading.Thread(target=work, name="name-registry", daemon=True)
    th.start()
    print("[name-registry] triggers on; the registry builds on a second connection "
          "beside passes 1-2", flush=True)
    return (th, box, conn2)


def besideJoin(handle, conn):
    """Wait for the side-by-side build. -> the connection passes 2b, 4 and 5
    run on: its own when the registry is ready, else `conn` (triggers dropped,
    the corpus read in full, as when install() fails)."""
    th, box, conn2 = handle
    th.join()
    while _LAST:
        print(_LAST.pop(0), flush=True)
    if box.get("ok"):
        return conn2
    if "exc" in box:
        print(f"[name-registry] off ({type(box['exc']).__name__}: {box['exc']}); every pass "
              "reads the corpus in full", flush=True)
    uninstall(conn, quiet=True)
    forget(conn2)
    return conn


def forget(conn):
    _READY.pop(id(conn), None)


def refresh(cur):
    """Rebuild the registry's rows of every person a row moved to or from
    since the last refresh. In the caller's transaction."""
    cur.execute("CREATE TEMP TABLE IF NOT EXISTS lu_dirty (person_id bigint PRIMARY KEY)")
    cur.execute("DELETE FROM lu_dirty")
    cur.execute("INSERT INTO lu_dirty SELECT DISTINCT person_id FROM lu_person_moves "
                "ON CONFLICT DO NOTHING")
    cur.execute("DELETE FROM lu_person_moves")
    cur.execute("SELECT count(*) FROM lu_dirty")
    n = cur.fetchone()[0]
    if n:
        cur.execute("DELETE FROM lu_reg g USING lu_dirty d WHERE g.person_id = d.person_id")
        d = "AND person_id IN (SELECT person_id FROM lu_dirty)"
        cur.execute(f"INSERT INTO lu_reg {_pairsSql('AND a.' + d[4:], d, d)}")
        cur.execute("ANALYZE lu_reg")
    return n


def _ourTriggers(cur):
    cur.execute("SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE t.tgname IN ('lu_person_move', 'lu_person_move_id') AND c.relname = ANY(%s) "
                "ORDER BY 1, 2", (list(TABLES),))
    return cur.fetchall()


def uninstall(conn, quiet=False, lock_wait="30s"):
    """The move triggers OFF: disabled first, then dropped if the tables can
    be had.
    ★ DISABLE BEFORE DROP (2026-11-24; run 20261122_220225's
      04a_registry_off FAILED after its 5-minute wait, as on 11-13 and
      11-16). DROP TRIGGER takes ACCESS EXCLUSIVE, so ANY reader of
      results/results_tf (01c_level_graph's scan, the site) blocks it -- and
      queues the site behind it while it waits. ALTER TABLE ... DISABLE
      TRIGGER takes SHARE ROW EXCLUSIVE, which readers do not block. A
      disabled trigger never fires, which is all 'off' has to mean; the drop
      after it is tidying and gets a few seconds. The next install
      (_triggersOnce) drops and recreates them, enabled."""
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL lock_timeout = '{lock_wait}'")
            for table, name in _ourTriggers(cur):
                cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER {name}")
        conn.commit()
    except Exception as exc:                # noqa: BLE001
        conn.rollback()
        if not quiet:
            print(f"[name-registry] ! could not disable the triggers ({exc}); the next 04a "
                  "replaces them and the next backfill drops them", flush=True)
        _READY.pop(id(conn), None)
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            for table, name in _ourTriggers(cur):
                cur.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        conn.commit()
    except Exception:                       # noqa: BLE001 -- disabled is off
        conn.rollback()
        if not quiet:
            print("[name-registry] triggers disabled (the tables were busy, so they stay "
                  "in the catalogue, off; the next 04a replaces them)", flush=True)
    _READY.pop(id(conn), None)


def main():
    """--uninstall: drop the triggers on their own (pipeline 04a_registry_off).
    ★ (2026-11-16; runs 20261113_150959 and 20261116_154245 both logged "could
    not drop the triggers (lock timeout)"): DROP TRIGGER needs the table to
    itself, and while the 03 group's snapshot readers (or the site) hold
    athletes/results open, 04a's 30-second try fails -- leaving athletes'
    triggers logging every later person change into lu_person_moves. Run
    again once the 03 group is collected, with a longer wait."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--lock-wait", default="5min")
    a = ap.parse_args()
    if not a.uninstall:
        ap.error("nothing to do (--uninstall)")
    from database import getConn
    with getConn() as conn:
        uninstall(conn, lock_wait=a.lock_wait)
        with conn.cursor() as cur:
            # enabled ones only: a disabled trigger is off (see uninstall)
            cur.execute("SELECT count(*) FILTER (WHERE tgenabled <> 'D'), count(*) "
                        "FROM pg_trigger WHERE tgname LIKE 'lu_person_move%%'")
            left, anyleft = cur.fetchone()
            if not anyleft:
                cur.execute("DROP TABLE IF EXISTS lu_person_moves")
        conn.commit()
    print(f"[name-registry] triggers left on: {left}")
    return 1 if left else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
