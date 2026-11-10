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


def install(conn):
    """Triggers on, registry built, committed. False (and the registry off)
    on any failure -- gather() then reads the corpus in full, as before."""
    if not enabled():
        return False
    t0 = time.time()
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
        uninstall(conn, quiet=True)
        print(f"[name-registry] off ({type(exc).__name__}: {exc}); every pass reads the corpus "
              "in full", flush=True)
        return False
    _READY[id(conn)] = True
    print(f"[name-registry] built once: {n:,} (person, name) pairs ({time.time() - t0:.0f}s); "
          "the passes after this refresh only the persons that moved", flush=True)
    return True


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


def uninstall(conn, quiet=False):
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '30s'")
            for table in TABLES:
                cur.execute(f"DROP TRIGGER IF EXISTS lu_person_move ON {table}")
                cur.execute(f"DROP TRIGGER IF EXISTS lu_person_move_id ON {table}")
        conn.commit()
    except Exception as exc:                # noqa: BLE001
        conn.rollback()
        if not quiet:
            print(f"[name-registry] ! could not drop the triggers ({exc}); the next 04a "
                  "replaces them and the next backfill drops them", flush=True)
    _READY.pop(id(conn), None)
