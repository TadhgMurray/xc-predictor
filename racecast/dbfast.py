"""
dbfast.py -- the two things that make a build over 61 million rows fast.

Nothing here changes a single number the site publishes. Both are about how
rows cross the wire and how much room Postgres is given to work in.

★ THE ROW FACTORY IS WORTH MINUTES, AND IT IS THE LAST THING ANYBODY LOOKS AT.
  Measured on a real server-side cursor over 2,000,000 rows, fetching the same
  eight columns and touching two of them:

      RealDictCursor                      11.8s
      DictCursor                           9.9s
      plain cursor + dict(zip(names,row))  4.3s
      NamedTupleCursor                     3.3s
      plain tuple cursor                   2.9s

  RealDictCursor builds an ordered-dict subclass per row. At 61.6M rows that
  is about five minutes of a build spent constructing dictionaries and
  throwing them away.

  Which replacement to use depends on what reads the rows:

      dictRows()          when the consumer wants dicts and is not this
                          module's to change -- team_rank takes dicts and its
                          tests are written in dicts. 2.7x, no contract change.
      NamedTupleCursor    when the consumer is one loop you can edit.
                          row["school"] becomes row.school. 3.6x.

⚠ AND A NAMED CURSOR CANNOT USE PARALLEL WORKERS AT ALL. Postgres will not
  run a parallel plan inside a cursor -- it cannot suspend workers mid-scan --
  so every streaming read here is single-threaded no matter what
  max_parallel_workers_per_gather says. That setting still matters for the
  ordinary statements around them: the athlete_season aggregate, the index
  builds, the panel queries. Measured on a 5M-row group-by: 1.0s at 0 workers,
  0.3s at 4.

! CHUNKING THE STREAM WAS TRIED AND WAS SLOWER. Ten range-restricted queries
  instead of one cursor, to get parallelism back: 13.1s against 5.3s, because
  each chunk re-scanned the table. Measured before it was written, which is
  why it was not written.
"""

# How many rows to pull per round trip. The cursor's own itersize governs what
# Postgres ships; this governs how many rows are turned into dicts at once.
BATCH = 50_000


def dictRows(cur, batch=BATCH):
    """Iterate a PLAIN cursor as plain dicts -- 2.7x RealDictCursor.

    The column names come from cur.description, so they are exactly the
    aliases the query returned and cannot drift from it.

    ! fetchmany, NOT iteration. Iterating a named cursor yields one row at a
      time through psycopg2's own machinery; fetchmany hands back a list and
      the zip runs in C over it.
    """
    names = None
    while True:
        rows = cur.fetchmany(batch)
        if not rows:
            return
        if names is None:
            names = [d[0] for d in cur.description]
        for row in rows:
            yield dict(zip(names, row))


# ★ WHAT A BUILD IS ALLOWED TO USE. These are per-SESSION, so they last as
#   long as the connection and touch nothing else on the server.
#
#   work_mem                          a sort or hash that does not fit spills
#                                     to disk. build_team_season sorts 10.7M
#                                     rows by (sport, year, pool).
#   maintenance_work_mem              every builder here creates its indexes
#                                     AFTER the COPY, deliberately. This is
#                                     the memory that sort gets.
#   max_parallel_workers_per_gather   the aggregates and index builds, not the
#                                     cursors -- see the note above.
#
# ⚠ NOT synchronous_commit. These builds commit a handful of times, so it
#   would buy nothing, and turning it off trades durability for that nothing.
_SETTINGS = (
    ("work_mem", "256MB"),
    ("maintenance_work_mem", "1GB"),
    ("max_parallel_workers_per_gather", "4"),
    # ⚠ A SEPARATE SETTING, AND THE ONE THAT ACTUALLY SPEEDS CREATE INDEX.
    #   max_parallel_workers_per_gather governs queries; index builds read
    #   this one, and every builder here creates its indexes after the COPY.
    #   Measured on 5M rows with maintenance_work_mem at 1GB: 20.5s at 0
    #   workers, 7.8s at 4.
    ("max_parallel_maintenance_workers", "4"),
)


def tuneSession(conn, quiet=False):
    """Give this connection room to work. Returns what was set.

    ! EVERY FAILURE IS SURVIVABLE AND IS SURVIVED. A managed Postgres may
      refuse a setting, or cap it; a build that dies because it could not ask
      for more memory would be a worse build than a slow one.
    """
    applied = []
    try:
        from database import dbSetting          # the quiet caps (XCP_DB_QUIET)
    except ImportError:                         # a caller off the project path
        def dbSetting(name, default):
            return default
    for name, value in _SETTINGS:
        value = dbSetting(name, value)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET {name} = %s", (value,))
            applied.append(f"{name}={value}")
        except Exception as exc:                      # noqa: BLE001
            conn.rollback()
            if not quiet:
                print(f"  (could not set {name}: "
                      f"{str(exc).splitlines()[0]} -- continuing)")
    conn.commit()
    if applied and not quiet:
        print(f"  session:  {', '.join(applied)}")
    return applied


# ------------------------------------------------------------------ #
#  THE SWAP -- one short transaction, a bounded wait, never a queue
# ------------------------------------------------------------------ #
#
# ★ WHY EVERY BUILDER SWAPS THROUGH HERE (owner, 2026-09-13: "some parts of
#   pipeline make website super slow (this is a must fix)"). DROP TABLE and
#   ALTER TABLE RENAME take ACCESS EXCLUSIVE. Postgres queues locks in
#   order, so a bare DROP behind one long site read makes EVERY later site
#   query on that table wait behind the DROP -- and when the DROP was
#   followed by ten index builds inside the same transaction
#   (build_school_units), the site's filtered boards queued for the whole
#   build, hit their 5 s lock_timeout, and rendered as 500s.
#
#   The rule: build and index the shadow table with the live one untouched;
#   then, in its own transaction with `SET LOCAL lock_timeout`, drop the
#   live table and rename the shadow in -- milliseconds under the lock --
#   retrying while a reader is in flight, so a reader that holds the table
#   delays the swap instead of the swap delaying every reader.
SWAP_TRIES = 24
SWAP_WAIT_S = 5.0


def swapTable(conn, name, new=None, renames=(), tries=SWAP_TRIES, wait=SWAP_WAIT_S,
              analyze=True, quiet=False):
    """Replace `name` with `new` (default `<name>_new`): ANALYZE the shadow
    (outside the lock), then DROP the live table, RENAME the shadow in and
    rename its indexes (`renames`: (old, new) pairs), all in one short
    transaction that waits at most `wait` seconds for the lock, `tries`
    times. Raises RuntimeError when the lock never came; the shadow is
    left built for a rerun."""
    import time
    import psycopg2
    new = new or f"{name}_new"
    with conn.cursor() as cur:
        if analyze:
            cur.execute(f"ANALYZE {new}")
        conn.commit()
        for attempt in range(1, tries + 1):
            try:
                cur.execute("SET LOCAL lock_timeout = %s", (f"{int(wait * 1000)}ms",))
                cur.execute(f"DROP TABLE IF EXISTS {name}")
                cur.execute(f"ALTER TABLE {new} RENAME TO {name}")
                for old_ix, new_ix in renames:
                    cur.execute(f"ALTER INDEX IF EXISTS {old_ix} RENAME TO {new_ix}")
                conn.commit()
                return attempt
            except psycopg2.errors.LockNotAvailable:
                conn.rollback()
                if not quiet:
                    print(f"  {name}: swap try {attempt}/{tries} -- being read; "
                          f"retrying in {wait:g}s", flush=True)
                time.sleep(wait)
    raise RuntimeError(f"{name}: could not take the lock for the swap in "
                       f"{tries * wait:.0f}s; {new} is built, rerun to swap")


# ------------------------------------------------------------------ #
#  SELF-CHECK -- `python racecast/dbfast.py` (needs no database)
# ------------------------------------------------------------------ #

def _selfCheck():
    bad = 0

    def check(label, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}"
              + ("" if ok else f"  (want {want!r})"))

    class FakeCur:
        description = [("a",), ("b",)]

        def __init__(self, rows):
            self.rows = list(rows)

        def fetchmany(self, n):
            out, self.rows = self.rows[:n], self.rows[n:]
            return out

    print("dictRows")
    check("every row, in order",
          list(dictRows(FakeCur([(1, 2), (3, 4), (5, 6)]), batch=2)),
          [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}])
    check("an empty result is an empty iterator",
          list(dictRows(FakeCur([]))), [])
    check("the keys are the query's aliases",
          sorted(next(dictRows(FakeCur([(1, 2)])))), ["a", "b"])
    check("they are plain dicts, which is the whole point",
          type(next(dictRows(FakeCur([(1, 2)])))), dict)

    print("\ntuneSession survives a server that refuses")
    class Refuses:
        def cursor(self):
            raise RuntimeError("permission denied to set parameter")
        def rollback(self): pass
        def commit(self): pass
    check("nothing set, nothing raised", tuneSession(Refuses(), quiet=True), [])

    print("\nall cases pass" if not bad else f"\n{bad} FAILURES")
    return bad


if __name__ == "__main__":
    raise SystemExit(_selfCheck())
