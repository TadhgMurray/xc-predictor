#!/usr/bin/env python3
"""
person_redirects.py -- when an athlete's id goes away, send its page to
where the athlete went.

    python scripts/person_redirects.py --snapshot     # pipeline 01a: where every person's rows are now
    python scripts/person_redirects.py --resolve      # pipeline 13c0: ids that vanished since -> successor
    python scripts/person_redirects.py --resolve --dry-run
    python scripts/person_redirects.py --lookup 32767048

★ WHY (owner, 2026-09-26, crawl log: Googlebot got 404s on /athlete/327...).
  A person id is not permanent: twins are merged, tfrrs rows are linked to
  an anet person, a mint is undone. The old id's page then 404s, and with
  it goes whatever Google had indexed under it and every link people had
  shared. A 301 from the old id to the new one keeps both.

HOW, WITHOUT TOUCHING ANY OF THE WRITERS. Nothing that moves person ids
records the move, and there are several of them. So this watches the rows
instead: --snapshot keeps, for every person, the first and last result id
in each sport (person_probe, two rows per person per sport -- a GROUP BY,
not a copy of 100M rows). --resolve looks those result ids up again: a
person id that no longer exists anywhere (no athletes row, no result in
either sport) whose probe rows now carry another id gets a redirect to the
id most of them carry. A redirect whose old id has come back is dropped,
so a page that exists is never redirected.

The athlete page follows person_redirect, up to five hops, before it 404s.
"""
import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS person_redirect (
    old_id   bigint PRIMARY KEY,
    new_id   bigint NOT NULL,
    n_probes int,
    made_at  timestamptz DEFAULT now()
)
"""

SNAPSHOT = """
DROP TABLE IF EXISTS person_probe_new;
CREATE TABLE person_probe_new AS
    SELECT DISTINCT person_id, sport, result_id FROM (
        SELECT person_id, 'XC'::text AS sport,
               unnest(ARRAY[min(result_id), max(result_id)]) AS result_id
          FROM results WHERE person_id IS NOT NULL GROUP BY person_id
        UNION ALL
        SELECT person_id, 'TF',
               unnest(ARRAY[min(result_id), max(result_id)])
          FROM results_tf WHERE person_id IS NOT NULL GROUP BY person_id) x;
CREATE INDEX ON person_probe_new (sport, result_id);
DROP TABLE IF EXISTS person_probe;
ALTER TABLE person_probe_new RENAME TO person_probe;
"""

# the probes whose row now belongs to someone else, and that someone
MOVED = """
    SELECT p.person_id AS old_id, r.person_id AS new_id
      FROM person_probe p JOIN results r
        ON p.sport = 'XC' AND r.result_id = p.result_id
     WHERE r.person_id IS NOT NULL AND r.person_id <> p.person_id
    UNION ALL
    SELECT p.person_id, r.person_id
      FROM person_probe p JOIN results_tf r
        ON p.sport = 'TF' AND r.result_id = p.result_id
     WHERE r.person_id IS NOT NULL AND r.person_id <> p.person_id
"""

GONE = """
    NOT EXISTS (SELECT 1 FROM athletes   a WHERE a.person_id = {c})
AND NOT EXISTS (SELECT 1 FROM results    r WHERE r.person_id = {c})
AND NOT EXISTS (SELECT 1 FROM results_tf t WHERE t.person_id = {c})
"""

RESOLVE = f"""
WITH moved AS ({MOVED}),
votes AS (
    SELECT old_id, new_id, count(*) AS n,
           row_number() OVER (PARTITION BY old_id ORDER BY count(*) DESC, new_id) AS rk
      FROM moved GROUP BY old_id, new_id
)
SELECT v.old_id, v.new_id, v.n
  FROM votes v
 WHERE v.rk = 1 AND {GONE.format(c='v.old_id')}
"""

CAME_BACK = f"DELETE FROM person_redirect pr WHERE NOT ({GONE.format(c='pr.old_id')})"


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def snapshot():
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(SNAPSHOT)
        cur.execute("SELECT count(*), count(DISTINCT person_id) FROM person_probe")
        n, p = cur.fetchone()
        conn.commit()
    print(f"person_probe: {n:,} probe rows for {p:,} people")


def resolve(dry):
    with getConn() as conn, conn.cursor() as cur:
        if not _exists(cur, "person_probe"):
            print("no person_probe yet (the snapshot step has not run): nothing to resolve")
            return
        cur.execute(DDL)
        cur.execute(RESOLVE)
        rows = cur.fetchall()
        print(f"{len(rows):,} athlete ids vanished since the snapshot and have a successor")
        for old, new, n in rows[:10]:
            print(f"  /athlete/{old} -> /athlete/{new}  ({n} of its probe rows moved there)")
        if dry:
            conn.rollback()
            print("dry run: nothing written")
            return
        from psycopg2.extras import execute_values
        if rows:
            execute_values(cur, """
                INSERT INTO person_redirect (old_id, new_id, n_probes) VALUES %s
                ON CONFLICT (old_id) DO UPDATE
                   SET new_id = EXCLUDED.new_id, n_probes = EXCLUDED.n_probes,
                       made_at = now()""", rows)
        cur.execute(CAME_BACK)
        back = cur.rowcount
        cur.execute("SELECT count(*) FROM person_redirect")
        total = cur.fetchone()[0]
        conn.commit()
    print(f"person_redirect: {total:,} redirects ({back:,} dropped: their id exists again)")


def follow(cur, person_id, hops=5):
    """The id a vanished athlete id now lives under, or None. Follows chains
    (a merged into b, b later into c) and stops on a loop."""
    seen = {person_id}
    cur_id = person_id
    for _ in range(hops):
        cur.execute("SELECT new_id FROM person_redirect WHERE old_id = %s", (cur_id,))
        row = cur.fetchone()
        if not row:
            break
        nxt = row[0] if not isinstance(row, dict) else row["new_id"]
        if nxt in seen:
            break
        seen.add(nxt)
        cur_id = nxt
    return cur_id if cur_id != person_id else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", action="store_true")
    g.add_argument("--resolve", action="store_true")
    g.add_argument("--lookup", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.snapshot:
        snapshot()
    elif a.resolve:
        resolve(a.dry_run)
    else:
        with getConn() as conn, conn.cursor() as cur:
            if not _exists(cur, "person_redirect"):
                print("no person_redirect table yet")
                return
            print(follow(cur, a.lookup))


if __name__ == "__main__":
    main()
