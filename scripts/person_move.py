#!/usr/bin/env python3
"""
person_move.py -- which rows a person merge must leave where they are.

★ WHY (owner, 2026-10-08, link_feed_twins --write):
    UniqueViolation idx_results_tf_nodup
    Key (meet_id, div_id, event_id, person_id, round,
         round(time_seconds::numeric, 1)) = (663826, 4, 22, 29567439, F, 165.5)
  The database carries per-person unique indexes on the result tables (the
  server's own, not created by this repo). A merge repoints a person's rows
  to another person; a row the target already holds (the same race scraped
  twice, once under each id) would then exist twice under one person, and
  the whole UPDATE fails. Such a row is a copy, not a result the target is
  missing, so it stays with the old id and the rest move.

HOW. The caller fills a temp table pm_move (result_id, new_id) with the rows
it means to move; dropCollisions(cur, table) deletes from it every row that
would break any unique index of that table naming person_id -- read from
the catalog, so an index added later is honoured without a code change. A
row collides with a row already under the target (which wins) or with
another moving row (the lowest result_id wins). A key holding a NULL never
collides (Postgres' default), and a partial index only covers its rows.
"""

MOVE_DDL = """
DROP TABLE IF EXISTS pm_move;
CREATE TEMP TABLE pm_move (result_id bigint PRIMARY KEY, new_id bigint NOT NULL)
"""


def _personIndexes(cur, table):
    """[(name, [key expression], predicate or None)] for the unique,
    non-primary indexes of table whose key names person_id."""
    cur.execute("""
        SELECT c.relname, i.indexrelid, i.indnkeyatts,
               pg_get_expr(i.indpred, i.indrelid)
        FROM   pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        WHERE  i.indrelid = %s::regclass AND i.indisunique AND NOT i.indisprimary
        ORDER  BY c.relname""", (table,))
    out = []
    for name, oid, natts, pred in cur.fetchall():
        exprs = []
        for k in range(1, natts + 1):
            cur.execute("SELECT pg_get_indexdef(%s, %s, true)", (oid, k))
            exprs.append(cur.fetchone()[0])
        if any("person_id" in e for e in exprs):
            out.append((name, exprs, pred))
    return out


def dropCollisions(cur, table):
    """Delete from pm_move the rows of table whose move would duplicate a
    unique key under the target person. Returns {index name: rows kept back}."""
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = %s AND table_schema = ANY(current_schemas(false))
                     AND column_name <> 'person_id'
                   ORDER BY ordinal_position""", (table,))
    cols = ", ".join(f'r."{c[0]}"' for c in cur.fetchall())
    kept = {}
    for name, exprs, pred in _personIndexes(cur, table):
        keys = ", ".join(f"({e})" for e in exprs)
        notnull = " AND ".join(f"({e}) IS NOT NULL" for e in exprs)
        where = notnull + (f" AND ({pred})" if pred else "")
        # the moving rows as they would read under the target, and the
        # target's rows that stay; bare column names in the index's
        # expressions resolve against x
        cur.execute(f"""
            DELETE FROM pm_move WHERE result_id IN (
                SELECT result_id FROM (
                    SELECT result_id, pm_moving,
                           row_number() OVER (PARTITION BY {keys}
                                              ORDER BY pm_moving, result_id) AS rn
                    FROM (
                        SELECT {cols}, mv.new_id AS person_id, true AS pm_moving
                        FROM   {table} r JOIN pm_move mv ON mv.result_id = r.result_id
                        UNION ALL
                        SELECT {cols}, r.person_id, false
                        FROM   {table} r
                        JOIN   (SELECT DISTINCT new_id FROM pm_move) t
                               ON r.person_id = t.new_id
                        WHERE  NOT EXISTS (SELECT 1 FROM pm_move mv
                                           WHERE mv.result_id = r.result_id)
                    ) x
                    WHERE {where}
                ) q
                WHERE pm_moving AND rn > 1)""")
        if cur.rowcount:
            kept[name] = cur.rowcount
    return kept
