# Project: xc-predictor
# Author:  Tadhg Murray
# Subset:  Database
# File:    engine/merge_column.py
# Purpose: Overwrite ONE column across an entire large table, fast, without
#          losing the table's constraints.
#
# ============================================================================
# WHY NOT JUST UPDATE?
# ============================================================================
# `UPDATE results SET speed_rating = ... FROM staging WHERE ...` was measured on
# this database at ~150-290 us per row. EXPLAIN (ANALYZE, BUFFERS) on one page:
#
#     Update on results   (actual time=5538.716)
#       Buffers: shared hit=317387 read=55752 dirtied=54575 written=38290
#       ->  Nested Loop   (actual time=0.064..258.447 rows=10000)
#
# Finding the rows cost 258 ms. WRITING them cost 5,281 ms. 54,575 dirty 8 KB
# pages for 10,000 rows -- 45 KB of disk to change one 4-byte float. Postgres
# never updates in place: each UPDATE writes a new heap tuple, and because the
# table was bulk-loaded at fillfactor=100 there is no room for a HOT update, so
# EVERY index takes a fresh random-page insertion.
#
# At 30M rows that is roughly 75-100 minutes per table. Rebuilding the heap in
# one sequential pass and building each index ONCE, by sorting, took 122 seconds
# for 39M rows. That is the whole argument.
#
# ============================================================================
# THE BUG THIS MODULE EXISTS TO NOT REPEAT
# ============================================================================
# `CREATE TABLE ... AS SELECT` copies COLUMN NAMES AND TYPES. Nothing else. It
# silently drops NOT NULL, DEFAULT, CHECK, UNIQUE and FOREIGN KEY. An earlier
# rebuild did exactly this to `results` and `results_tf`: their NOT NULLs and
# their composite FK to `athletes` vanished, and nothing errored. It was found
# only by reading the catalog afterwards.
#
# So this module captures, from pg_catalog, BEFORE the rebuild:
#     * indexes         (pg_indexes)
#     * the primary key (pg_constraint, contype 'p')
#     * NOT NULL flags  (pg_attribute.attnotnull)
#     * DEFAULT exprs   (pg_attrdef)
#     * CHECK / UNIQUE / FOREIGN KEY  (pg_constraint)
#     * INBOUND foreign keys from other tables  (pg_constraint, confrelid)
# and restores every one of them onto the new table before the swap.
#
# The last item is the subtle one: `ALTER TABLE t RENAME TO t_old` does NOT move
# constraints that point AT t. An FK in another table would silently follow the
# rename and end up referencing t_old.
#
# ============================================================================
# THE CONTRACT
# ============================================================================
#   mergeColumn(conn, table, column, staging, staging_key, staging_val)
#
#   staging must hold (staging_key -> staging_val) pairs. Rows of `table` with no
#   staging match KEEP THEIR EXISTING VALUE (COALESCE), so this is an upsert of a
#   single column, not a destructive rewrite.
#
#   The BUILD runs in one transaction and COMMITS as `<table>_new`; the SWAP is
#   a second, instant transaction retried under _swapSiege, so a lost lock
#   fight cannot discard the rebuild (2026-08-25: it did -- see _swapSiege).
#   `table` itself is still never seen half-rebuilt: the swap stays atomic. The
#   old heap survives as `<table>_old` until the caller drops it -- that is the
#   undo button, and dropping it is irreversible.
#
#   REQUIRES exclusive access. Rows written to `table` by anyone else during the
#   rebuild land in `<table>_old` and are LOST. Run with the launcher OFF.

import time

import psycopg2.errors


# ================================================================== #
# CAPTURE  —  read the table's definition out of the catalog
# ================================================================== #

# _timed
# Purpose : run one statement, print how long. These take minutes; a silent
#           terminal for twenty minutes is indistinguishable from a hang.
def _timed(cur, sql, label):
    # the line BEFORE as well as after: a constraint check or an ANALYZE on
    # 121M rows is minutes of silence otherwise, and the owner read that as
    # a hang (2026-09-04)
    print(f"    ...        {label}", flush=True)
    t0 = time.time()
    cur.execute(sql)
    print(f"    [{time.time() - t0:7.1f}s] {label}", flush=True)


# _columns
# Purpose : the table's columns, in physical order.
# Why read them instead of hardcoding: `results` has grown columns twice this
#   project (person_id, canon_meet_id). A hardcoded list would silently DROP any
#   column added since this file was written -- data loss no test would catch.
def _columns(cur, table):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table,))
    return [r[0] for r in cur.fetchall()]


# _notNulls
# Purpose : columns flagged NOT NULL. attnum > 0 skips system columns (ctid,
#           xmin); NOT attisdropped skips the catalog ghosts left by DROP COLUMN.
def _notNulls(cur, table):
    cur.execute("""
        SELECT attname FROM pg_attribute
        WHERE attrelid = %s::regclass AND attnum > 0
          AND NOT attisdropped AND attnotnull
        ORDER BY attnum
    """, (table,))
    return [r[0] for r in cur.fetchall()]


# _defaults
# Purpose : (column, default_expression) for every column that has one.
# Syntax  : defaults live in a SIDE table, pg_attrdef, joined on (adrelid, adnum).
#           pg_get_expr() renders the stored parse tree back into SQL text.
def _defaults(cur, table):
    cur.execute("""
        SELECT a.attname, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (table,))
    return cur.fetchall()


# _primaryKeyColumns
# Purpose : the PK's columns, so we can re-add it as a CONSTRAINT (which also
#           restores NOT NULL on them for free).
# Syntax  : conkey is a smallint[] of attnums. `unnest ... WITH ORDINALITY`
#           preserves the PK's column ORDER, which matters for a composite key.
def _primaryKeyColumns(cur, table):
    cur.execute("""
        SELECT a.attname
        FROM pg_constraint c
        CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.conrelid = %s::regclass AND c.contype = 'p'
        ORDER BY k.ord
    """, (table,))
    return [r[0] for r in cur.fetchall()]


# _indexes
# Purpose : every non-PK index, as the CREATE statement that rebuilds it.
# Syntax  : pg_indexes.indexdef IS the full DDL. We rename both the index and the
#           table inside it, because the new table is <table>_new and index names
#           are unique per SCHEMA, not per table.
# Indexes that BACK a constraint (unique/exclusion) are skipped: re-adding the
#   constraint recreates them, and creating the index first would collide.
def _indexes(cur, table):
    cur.execute("""
        SELECT i.indexname, i.indexdef
        FROM pg_indexes i
        WHERE i.schemaname = 'public' AND i.tablename = %s
          AND NOT EXISTS (
              SELECT 1 FROM pg_constraint c
              WHERE c.conrelid = i.tablename::regclass
                AND c.conindid = (i.schemaname||'.'||i.indexname)::regclass)
        ORDER BY i.indexname
    """, (table,))
    out = []
    for name, ddl in cur.fetchall():
        ddl = ddl.replace(f" {name} ON ", f" {name}_new ON ", 1)
        ddl = ddl.replace(f".{table} USING", f".{table}_new USING", 1)
        ddl = ddl.replace(f" {table} USING", f" {table}_new USING", 1)
        out.append((name, ddl))
    return out


# Constraint types this module rebuilds by name. An ALLOWLIST, deliberately.
#
# The first version used `contype <> 'p'` -- "everything except the primary key".
# PostgreSQL 18 then added `contype = 'n'` for NOT NULL constraints, which had
# never appeared in pg_constraint before. The rebuild duly tried to ADD them as
# named constraints, AFTER _restoreShape had already set NOT NULL on the same
# columns, and died:
#
#   ObjectNotInPrerequisiteState: cannot create not-null constraint
#   "results_id_system_not_null_new" on column "id_system"
#   DETAIL: A not-null constraint named "results_new_id_system_not_null"
#           already exists for this column.
#
# A blocklist means "everything I did not think of". A future Postgres will add
# another contype, and an allowlist will ignore it instead of crashing.
#   'c' CHECK   'u' UNIQUE   'f' FOREIGN KEY   'x' EXCLUSION
# Excluded on purpose: 'p' PRIMARY KEY (re-added by _restoreShape),
#                      'n' NOT NULL    (re-added by _restoreShape via attnotnull),
#                      't' CONSTRAINT TRIGGER (belongs to a trigger, not a table).
_REBUILDABLE_CONTYPES = ("c", "u", "f", "x")


# _constraints
# Purpose : the constraints we rebuild by name, as ADD CONSTRAINT clauses.
# Syntax  : `= ANY(%s)` compares against a Python list psycopg2 renders as a
#           Postgres array -- safer and clearer than an interpolated IN (...).
def _constraints(cur, table):
    cur.execute("""
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = %s::regclass AND contype = ANY(%s)
        ORDER BY contype, conname
    """, (table, list(_REBUILDABLE_CONTYPES)))
    # Belt and braces: if a future contype slips through the allowlist, a
    # definition beginning "NOT NULL" is _restoreShape's job, never ours.
    return [(n, d) for n, d in cur.fetchall() if not d.startswith("NOT NULL")]


# _inboundForeignKeys
# Purpose : FKs in OTHER tables that reference this one.
# The silent one. A rename does not move them; they follow the OID and end up
#   pointing at <table>_old. Nothing errors. The child then enforces its key
#   against a stale snapshot, and DROP TABLE <table>_old refuses or cascades.
def _inboundForeignKeys(cur, table):
    cur.execute("""
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE confrelid = %s::regclass AND contype = 'f'
        ORDER BY 1, 2
    """, (table,))
    return cur.fetchall()


# _capture
# Purpose : everything needed to rebuild the table faithfully, read ONCE, before
#           anything is changed.
def _capture(cur, table):
    return {
        "columns":  _columns(cur, table),
        "notnulls": _notNulls(cur, table),
        "defaults": _defaults(cur, table),
        "pk":       _primaryKeyColumns(cur, table),
        "indexes":  _indexes(cur, table),
        "cons":     _constraints(cur, table),
        "inbound":  _inboundForeignKeys(cur, table),
    }


# ================================================================== #
# BUILD
# ================================================================== #

# _selectList
# Purpose : every column verbatim, EXCEPT `column`, which comes from staging.
#
# TWO SEMANTICS, and choosing wrong leaves fossils in your data:
#
#   preserve_unmatched=True   ->  COALESCE(s.val, r.col)
#       A row staging never mentions KEEPS its existing value. This is an UPSERT
#       of one column. Right when the writer is an INCREMENTAL pass that only
#       has an opinion about some rows.
#
#   preserve_unmatched=False  ->  s.val
#       A row staging never mentions becomes NULL. This is a FULL RECOMPUTE.
#       Right when the writer considered every row and DECLINED to rate some.
#
# This distinction cost real data. speed_ratings.py recomputes every result, and
# its merge ran with COALESCE. Rows the engine declined -- because their
# normalized_time was 20.6s, far outside the [600,3600] band -- kept a
# speed_rating of 7528 written by an engine run from years earlier. The count
# came out 4,216 ABOVE the loader's own output, which is arithmetically
# impossible for a single run, and that surplus was the fossil.
#
# A stale value is worse than a missing one. NULL says "we do not know". 7528
# says "this athlete ran a 20-second 5K".
def _selectList(columns, column, val, preserve_unmatched=True, extra=()):
    # extra: ((table_column, staging_column), ...) merged in the same
    # rebuild, the same COALESCE rule as the main column (2026-09-06:
    # rating_pool rides with speed_rating)
    extra_map = dict(extra)
    parts = []
    for c in columns:
        if c == column:
            parts.append(f"COALESCE(s.{val}, r.{c}) AS {c}"
                         if preserve_unmatched else f"s.{val} AS {c}")
        elif c in extra_map:
            sv = extra_map[c]
            parts.append(f"COALESCE(s.{sv}, r.{c}) AS {c}"
                         if preserve_unmatched else f"s.{sv} AS {c}")
        else:
            parts.append(f"r.{c}")
    return ",\n               ".join(parts)


# _buildNewTable
# Purpose : one sequential scan of `table`, one hash join against the small
#           staging table, sequential heap writes into a brand new table with NO
#           indexes on it. Nothing is maintained during the write. That is the
#           entire speedup.
# LOGGED, not UNLOGGED: `ALTER TABLE ... SET LOGGED` would REWRITE the heap AND
#   every index we just built. Unlogged-then-convert is two full rewrites.
# work_mem sizes the hash table for the join; at the 4MB default a 30M-row hash
#   spills to disk in hundreds of batches.
def _dbSetting(name, default):
    """database.dbSetting when scripts/ is importable, else the default."""
    try:
        from database import dbSetting
    except ImportError:
        return default
    return dbSetting(name, default)


def _buildNewTable(cur, table, staging, cap, column, key, val,
                   preserve_unmatched=True, extra=()):
    # SET LOCAL, not SET. A plain SET survives COMMIT and rides back into the
    # connection POOL, so the next caller silently inherits a 2GB-per-sort-node
    # budget. SET LOCAL is scoped to the transaction and evaporates at commit.
    # Verified: plain SET + commit -> `SHOW work_mem` still says 2GB.
    # ★ THROUGH THE QUIET CAPS (XCP_DB_QUIET): the pipeline's rebuild of
    #   results must not take every core and 2 GB per sort node from under
    #   the site (owner, 2026-09-14).
    cur.execute(f"SET LOCAL work_mem = '{_dbSetting('work_mem', '2GB')}'")
    cur.execute(f"SET LOCAL max_parallel_workers_per_gather = "
                f"{_dbSetting('max_parallel_workers_per_gather', 4)}")
    _timed(cur, f"""
        CREATE TABLE {table}_new AS
        SELECT {_selectList(cap['columns'], column, val, preserve_unmatched, extra)}
        FROM {table} r
        LEFT JOIN {staging} s ON s.{key} = r.{key}
    """, f"CREATE TABLE {table}_new AS SELECT ... (heap rebuild)")


# _restoreShape
# Purpose : NOT NULL, DEFAULTs, PRIMARY KEY -- the things CREATE TABLE AS drops.
# PK first: ADD PRIMARY KEY sets NOT NULL on its columns, so re-issuing those is
#   harmless but noisy. We skip them.
# ★ EVERY NOT NULL IN ONE ALTER TABLE (2026-11-07, owner-approved). Each
#   `ALTER COLUMN ... SET NOT NULL` scans the whole new heap to prove it, so
#   N columns were N full scans of a 40-58 GB table; one ALTER TABLE with all
#   N subcommands proves them in ONE scan. The catalog ends the same: the
#   same columns NOT NULL, the same defaults (catalog-only, no scan).
def _shapeSql(table, cap):
    """The one ALTER TABLE for every NOT NULL (the PK's columns excepted:
    ADD PRIMARY KEY set them) and every DEFAULT; None when there are none."""
    pk = set(cap["pk"])
    subs = [f"ALTER COLUMN {col} SET NOT NULL"
            for col in cap["notnulls"] if col not in pk]
    subs += [f"ALTER COLUMN {col} SET DEFAULT {expr}"
             for col, expr in cap["defaults"]]
    if not subs:
        return None
    return f"ALTER TABLE {table}_new " + ", ".join(subs)


def _restoreShape(cur, table, cap):
    if cap["pk"]:
        cols = ", ".join(cap["pk"])
        _timed(cur, f"ALTER TABLE {table}_new ADD PRIMARY KEY ({cols})",
               f"PRIMARY KEY ({cols})")
    sql = _shapeSql(table, cap)
    if sql:
        _timed(cur, sql, f"NOT NULL / DEFAULT on {table}_new (one statement)")
    if cap["notnulls"]:
        print(f"    restored NOT NULL on {len(cap['notnulls'])} columns")
    if cap["defaults"]:
        print(f"    restored {len(cap['defaults'])} column defaults")


# _restoreIndexes
# Purpose : build each index ONCE, on the finished heap, by sorting.
# Postgres builds an index bottom-up from a sort -- sequential I/O. The UPDATE
#   path could never do that: it inserted one random key at a time.
def _restoreIndexes(cur, table, cap):
    # SET LOCAL: see _buildNewTable. These would otherwise leak into the pool.
    cur.execute(f"SET LOCAL maintenance_work_mem = '{_dbSetting('maintenance_work_mem', '8GB')}'")
    cur.execute(f"SET LOCAL max_parallel_maintenance_workers = "
                f"{_dbSetting('max_parallel_maintenance_workers', 6)}")
    for name, ddl in cap["indexes"]:
        _timed(cur, ddl, f"index {name}_new")


# ★ THE SECONDARY INDEXES, THREE AT A TIME (2026-11-07, owner-approved; the
#   pattern is backfill_normalize._buildSecondaries'). One CREATE INDEX per
#   pooled connection, each committed there -- which is why mergeColumn now
#   COMMITS <table>_new (heap, primary key, NOT NULLs, defaults) before
#   them: another session cannot see an uncommitted table. CREATE INDEX takes
#   a SHARE lock, so builds on one table run side by side; each index is the
#   same definition (pg_indexes.indexdef) whoever builds it. Memory goes
#   through the quiet caps per build (dbSetting), so three builds ask for
#   three times one build's budget, never three times 8GB.
#   XCP_MERGE_INDEX_JOBS=1 builds them one by one on the merge's connection.
_INDEX_MEM = "2GB"          # per build when several run
_INDEX_WORKERS = 2          # per build when several run


def _indexJobs(n):
    try:
        import os
        want = int(os.environ.get("XCP_MERGE_INDEX_JOBS", "3"))
    except ValueError:
        want = 3
    try:
        from database import dbJobs
        want = dbJobs(want)
    except ImportError:
        pass
    return max(1, min(want, n))


def _buildOneIndex(connect, name, ddl):
    t0 = time.time()
    with connect() as c:
        with c.cursor() as cur:
            # SET LOCAL: the pooled connection goes back as it came
            cur.execute(f"SET LOCAL maintenance_work_mem = "
                        f"'{_dbSetting('maintenance_work_mem', _INDEX_MEM)}'")
            # ! NEVER MORE THAN _INDEX_WORKERS PER BUILD (2026-11-09: the
            #   go-live's results rebuild ran three builds at the quiet cap's
            #   4 workers each -- quiet mode handed a side-by-side build MORE
            #   than the 2 it gets without it; fifteen sorting processes and
            #   their temp files took the box to load 39, and a meet page's
            #   one-meet count waited 42 s on the disk). The cap still wins
            #   when it is stricter (strict mode's 0).
            workers = min(int(_dbSetting('max_parallel_maintenance_workers', _INDEX_WORKERS)),
                          _INDEX_WORKERS)
            cur.execute(f"SET LOCAL max_parallel_maintenance_workers = {workers}")
            cur.execute(ddl)
        c.commit()
    return name, time.time() - t0


def _restoreIndexesParallel(conn, table, cap, connect, jobs):
    """Every secondary index of `cap`, `jobs` at a time on their own
    connections. <table>_new must be committed. On the first failure the
    builds not yet started are cancelled, the running ones finish, and the
    failure is raised."""
    import concurrent.futures as cf
    idx = cap["indexes"]
    print(f"    building {len(idx)} indexes on {table}_new, {jobs} at a time", flush=True)
    pool = cf.ThreadPoolExecutor(max_workers=jobs)
    try:
        futs = [pool.submit(_buildOneIndex, connect, name, ddl) for name, ddl in idx]
        cf.wait(futs, return_when=cf.FIRST_EXCEPTION)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    for f in futs:
        if not f.cancelled() and f.exception() is not None:
            raise f.exception()
    for f in futs:
        name, dt = f.result()
        print(f"    [{dt:7.1f}s] index {name}_new", flush=True)


def _connector():
    """database.getConn, or None where scripts/ is not importable."""
    try:
        from database import getConn
    except ImportError:
        return None
    return getConn


def _dropNew(conn, table):
    """A failed build after the commit: put things back where a rolled-back
    build left them (no <table>_new), best effort."""
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}_new")
        conn.commit()
        print(f"    build failed: dropped {table}_new ({table} untouched)")
    except Exception as exc:                       # noqa: BLE001
        print(f"    build failed, and {table}_new could not be dropped "
              f"({type(exc).__name__}); the next merge drops it (_clearLeftovers)")


# _restoreConstraints
# Purpose : CHECK / UNIQUE / FOREIGN KEY, renamed to avoid colliding with the
#           still-living originals on <table>.
# FKs are added NOT VALID: the data came from a table that already satisfied
#   them, so the scan is pure cost. (A VALIDATE pass can be run later if you want
#   the catalog to say `validated`.)
def _restoreConstraints(cur, table, cap):
    for name, defn in cap["cons"]:
        suffix = " NOT VALID" if defn.startswith("FOREIGN KEY") else ""
        _timed(cur, f"ALTER TABLE {table}_new ADD CONSTRAINT {name}_new {defn}{suffix}",
               f"constraint {name}_new")


# ================================================================== #
# SWAP
# ================================================================== #

# _swap
# Purpose : replace the old table with the new one, atomically.
# ORDER MATTERS, and the reason is not obvious. `ALTER TABLE t RENAME TO t_old`
#   does NOT rename t's indexes or constraints. After that statement t_old still
#   OWNS the names `t_pkey`, `idx_t_x`, ... and those names are unique per SCHEMA.
#   Claiming them for the new table collides:
#       ERROR: relation "results_pkey" already exists
#   So: move the OLD names out of the way FIRST, then claim them.
def _swap(cur, table, cap):
    cur.execute(f"ALTER TABLE {table} RENAME TO {table}_old")

    # step the old names aside
    if cap["pk"]:
        cur.execute(f"ALTER INDEX {table}_pkey RENAME TO {table}_old_pkey")
    for name, _ddl in cap["indexes"]:
        cur.execute(f"ALTER INDEX {name} RENAME TO {name}_old")
    for name, _defn in cap["cons"]:
        cur.execute(f"ALTER TABLE {table}_old RENAME CONSTRAINT {name} TO {name}_old")

    # claim them
    cur.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    if cap["pk"]:
        cur.execute(f"ALTER INDEX {table}_new_pkey RENAME TO {table}_pkey")
    for name, _ddl in cap["indexes"]:
        cur.execute(f"ALTER INDEX {name}_new RENAME TO {name}")
    for name, _defn in cap["cons"]:
        cur.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {name}_new TO {name}")

    # repoint anything that referenced the old table. An FK's target is
    # immutable, so the constraint must be dropped and re-added.
    for child, name, defn in cap["inbound"]:
        fixed = defn.replace(f"REFERENCES {table}_old(", f"REFERENCES {table}(")
        fixed = fixed.replace(f"REFERENCES public.{table}_old(",
                              f"REFERENCES public.{table}(")
        cur.execute(f"ALTER TABLE {child} DROP CONSTRAINT {name}")
        cur.execute(f"ALTER TABLE {child} ADD CONSTRAINT {name} {fixed} NOT VALID")
        print(f"    repointed {child}.{name} -> {table}")

    print(f"    swapped: {table}_new -> {table};  old kept as {table}_old")


# _blockers
# Purpose : who is holding ANY lock on `table` right now.
# The swap's ACCESS EXCLUSIVE conflicts with every lock class, so every holder
#   counts -- a plain SELECT's ACCESS SHARE is enough to beat the rename.
def _blockers(cur, table):
    cur.execute("""
        SELECT DISTINCT a.pid, a.application_name, a.state,
               left(coalesce(a.query, ''), 140)
        FROM pg_locks l
        JOIN pg_stat_activity a ON a.pid = l.pid
        WHERE l.relation = %s::regclass
          AND l.pid <> pg_backend_pid()
    """, (table,))
    return cur.fetchall()


# _swapSiege
# Purpose : run _swap in its OWN short transaction, and refuse to lose.
#
# The swap needs ACCESS EXCLUSIVE on `table` for a metadata instant, and ANY
# concurrent lock beats it -- a page view's SELECT, an EXPLAIN, a CREATE INDEX
# CONCURRENTLY queued hours earlier. 2026-08-25 that cost the whole night:
# 08_golive's rename on `results` waited out its 2-minute lock_timeout five
# hours in, and the one-transaction design took the finished rebuild down with
# it. Two changes follow. The build is COMMITTED before this runs, so a lost
# siege keeps the heap. And the siege ESCALATES instead of dying:
#
#   rounds 1..14   wait 30s each, naming the blockers -- ~7 minutes of
#                  patience, enough for any query to finish
#   rounds 15..20  pg_terminate_backend the blockers first. The owner's rule
#                  for unattended runs, applied to the database: the pipeline
#                  never stops because app.py or a stray index build is
#                  running. A terminated CREATE INDEX CONCURRENTLY leaves an
#                  INVALID index behind; scripts/add_page_indexes.py already
#                  detects those and rebuilds them.
_SIEGE_ROUNDS = 20
_SIEGE_POLITE = 14
_SIEGE_WAIT_S = 30


def _swapSiege(conn, table, cap):
    # ★ QUIET TRIES FIRST, THE SIEGE ONLY AS THE FALLBACK (sweep 2026-10-10,
    #   D5). The flag used to go up before round 1 and stay up through all
    #   twenty -- ~11 minutes of 503 on `results` behind one slow reader, for
    #   a rename that takes milliseconds once it has the lock. Now
    #   maintenance.swapQuietlyFirst makes many 1 s tries with the site up
    #   (under the site's 5 s lock_timeout, so a page queued behind a try
    #   never fails on it); only when every one loses does the site answer
    #   503 (issue 300) for the siege below, terminations and all.
    from maintenance import swapQuietlyFirst

    def once(lock_ms):
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL lock_timeout = '{int(lock_ms)}ms'")
            _swap(cur, table, cap)
        conn.commit()

    return swapQuietlyFirst(conn, once, table,
                            lambda: _swapSiegeRounds(conn, table, cap))


def _swapSiegeRounds(conn, table, cap):
    from maintenance import heartbeat
    for rnd in range(1, _SIEGE_ROUNDS + 1):
        # the flag EXPIRES so a killed swap cannot 503 the site forever
        # (engine/maintenance.py); a live siege says so every round
        heartbeat()
        if rnd > _SIEGE_POLITE:
            try:
                with conn.cursor() as cur:
                    for pid, app, state, q in _blockers(cur, table):
                        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                        print(f"    ! terminated pid {pid} "
                              f"({app or 'no app name'}, {state}): {q}")
                conn.commit()
            except Exception as exc:                    # noqa: BLE001
                conn.rollback()
                print(f"    ! could not terminate blockers: {exc}")
            time.sleep(2)      # a beat for the postmaster to reap them
        try:
            with conn.cursor() as cur:
                # per-ROUND timeout; short, because the retry is the patience
                # ! 3 s, UNDER THE SITE'S 5 s (D6, 2026-10-10): it was 10 s,
                #   so a page already in flight when the flag went up and
                #   queued behind this round failed at its own timeout first
                cur.execute("SET LOCAL lock_timeout = '3s'")
                _swap(cur, table, cap)
            conn.commit()
            return
        # ⚠ AND DEADLOCK TOO. A swap that renames two tables a reader
        #   touches in the other order deadlocks rather than timing out, and
        #   the deadlock detector fires first. Same meaning, same rollback,
        #   same fix -- see build_ranking_results.swapIn.
        except (psycopg2.errors.LockNotAvailable,
                psycopg2.errors.DeadlockDetected):
            conn.rollback()
            with conn.cursor() as cur:
                who = _blockers(cur, table)
            conn.rollback()    # hold nothing while sleeping
            print(f"    swap round {rnd}/{_SIEGE_ROUNDS}: "
                  f"{table} is locked by:")
            if not who:
                print("      (blocker gone already; racing a busy queue)")
            for pid, app, state, q in who:
                print(f"      pid {pid} ({app or 'no app name'}, {state}): {q}")
            if rnd < _SIEGE_ROUNDS:
                nxt = ("terminating blockers next round"
                       if rnd + 1 > _SIEGE_POLITE
                       else f"retrying in {_SIEGE_WAIT_S}s")
                print(f"      {nxt}", flush=True)
                time.sleep(_SIEGE_WAIT_S)
    raise RuntimeError(
        f"could not take the swap lock on {table} after {_SIEGE_ROUNDS} "
        f"rounds, terminations included. {table}_new is committed and kept; "
        f"rerun this step once the blocker is gone (the next run drops and "
        f"rebuilds it).")


# ================================================================== #
# ENTRY POINT
# ================================================================== #

# _clearLeftovers
# Purpose : deal with <table>_old / <table>_new leftovers at second zero, not
#   at swap time ~15 minutes of rebuild later (observed once, for real: a
#   pre-existing _old raised DuplicateTable at the swap and discarded 834s).
# _old is a HARD STOP: it is the previous swap's undo copy, and only a human
#   (or the pipeline's 02_drop_old, which owns that judgment) may drop it.
# _new is DROPPED here: nothing ever reads it -- the swap is its only
#   consumer -- so an existing one is the abandoned build of a run that lost
#   its swap siege, and the rebuild about to happen replaces it anyway.
def _clearLeftovers(cur, table):
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}_old",))
    if cur.fetchone()[0] is not None:
        raise RuntimeError(
            f"{table}_old already exists. The swap would fail AFTER the "
            f"rebuild. Verify it is disposable, then DROP TABLE {table}_old "
            f"and rerun.")
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}_new",))
    if cur.fetchone()[0] is not None:
        print(f"    dropping {table}_new left by an earlier run's failed swap")
        cur.execute(f"DROP TABLE {table}_new")


# mergeColumn
# Purpose : overwrite `column` of `table` from `staging`, rebuilding the heap.
# Arguments:
#   conn    -- a psycopg2 connection with exclusive access to `table`
#   table   -- 'results' | 'results_tf'
#   column  -- the column to overwrite, e.g. 'speed_rating'
#   staging -- a small table of (key, val)
#   key     -- the join column, present in BOTH tables. Usually 'result_id'.
#   val     -- the value column in `staging`
#   preserve_unmatched -- True: rows absent from staging keep their old value
#                         (an upsert).  False: they become NULL (a full
#                         recompute). See _selectList; getting this wrong leaves
#                         fossil values from previous runs.
# Output   : None. Prints one timed line per step.
# Safety   : the old heap survives as <table>_old. Verify, then drop it by hand.
# ★ THE NEW TABLE'S VISIBILITY MAP, BEFORE ANYONE READS IT (2026-11-07). A
#   table built by CREATE TABLE AS has no page marked all-visible, so every
#   index-only read the pages make on results / results_tf -- the athlete
#   page's places, the race page's feed list (idx_results_meet_src_cover) --
#   went to the heap row by row until a vacuum came by; and pipeline step
#   18c's throttled vacuum (~10 MB/s) of two freshly rebuilt 40-58 GB heaps
#   would hold the end of a full run for hours. One unthrottled VACUUM here,
#   committed, before the swap: the pages were just written and are still in
#   the page cache, nothing reads {table}_new yet, and 18c then skips every
#   page already marked. Never fails the go-live: a refusal is printed and
#   the table swaps in as it always did. XCP_MERGE_VACUUM=0 skips it.
def _vacuumNew(conn, table):
    import os
    if os.environ.get("XCP_MERGE_VACUUM", "1") == "0":
        return
    old = conn.autocommit
    try:
        conn.autocommit = True                     # VACUUM cannot run in a transaction
        with conn.cursor() as cur:
            cur.execute("SET vacuum_cost_delay = 0")
            _timed(cur, f"VACUUM {table}_new", f"VACUUM {table}_new (visibility map)")
    except Exception as exc:                       # noqa: BLE001
        print(f"    VACUUM {table}_new skipped: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            conn.autocommit = old
        except Exception:                          # noqa: BLE001
            pass


def mergeColumn(conn, table, column, staging, key="result_id", val="val",
                preserve_unmatched=True, extra=(), connect=None):
    print("-" * 70)
    print(f"MERGE: rebuilding {table}.{column} from {staging}")
    with conn.cursor() as cur:
        # Print the server version. The catalog's shape depends on it -- PG18
        # records NOT NULL in pg_constraint and PG<=17 does not -- and a version
        # gap between the machine this was tested on and the machine it runs on
        # is exactly how _constraints' blocklist bug shipped.
        cur.execute("SHOW server_version")
        print(f"    postgres {cur.fetchone()[0]}")
        _clearLeftovers(cur, table)
        # the BUILD transaction only -- the swap sets its own per-round
        # timeout inside _swapSiege
        cur.execute("SET LOCAL lock_timeout = '2min'")   # fail loudly, do not hang
        _timed(cur, f"ANALYZE {staging}", f"ANALYZE {staging} (size the hash join)")

        # ! IS THERE ANYTHING TO DO AT ALL? A rebuild of results_tf copies
        #   191M rows to change a column that exists on 28M of them, and a run
        #   where nothing in that sport's pooling moved changes NOTHING. The
        #   count below is one hash join against an already-ANALYZEd staging
        #   table, seconds against minutes, and it turns a no-op run into a
        #   no-op.
        #
        #   IS DISTINCT FROM, not <>: a NULL on either side means the value
        #   changed, and <> would silently report those as unchanged.
        # ! ONLY WHEN THE ANSWER CAN SKIP SOMETHING (2026-09-26). With
        #   preserve_unmatched=False (the go-live's mode) a zero delta is not
        #   a no-op, so the rebuild runs whatever the count says -- and the
        #   count was a full join of the staging table against the live one,
        #   per table, bought only for a print.
        changed = None
        if preserve_unmatched:
            _timed(cur, f"""
                SELECT count(*)
                FROM   {staging} s
                JOIN   {table} t ON t.{key} = s.{key}
                WHERE  t.{column} IS DISTINCT FROM s.{val}
            """, f"count changed rows in {table}")
            changed = cur.fetchone()[0]

        cur.execute(f"SELECT count(*) FROM {staging}")
        staged = cur.fetchone()[0]
        print(f"    {staged:,} staged rows" + (
            f", {changed:,} differ" if changed is not None else
            " (changed-row count skipped: this mode rebuilds regardless)"))

        if changed == 0 and preserve_unmatched:
            # preserve_unmatched=False would also NULL every unmatched row, so
            # a zero delta does not mean a no-op in that mode.
            print(f"    nothing to change; skipping the rebuild of {table}")
            print("-" * 70)
            return

        cap = _capture(cur, table)
        print(f"    {len(cap['columns'])} columns, {len(cap['indexes'])} indexes, "
              f"{len(cap['cons'])} constraints, {len(cap['inbound'])} inbound FKs")

        mode = "upsert (unmatched keep old value)" if preserve_unmatched \
            else "FULL RECOMPUTE (unmatched -> NULL)"
        print(f"    {column}: {mode}")
        _buildNewTable(cur, table, staging, cap, column, key, val,
                       preserve_unmatched, extra)
        _restoreShape(cur, table, cap)
        connect = connect or _connector()
        jobs = _indexJobs(len(cap["indexes"])) if connect is not None else 1
        if jobs <= 1:
            _restoreIndexes(cur, table, cap)
            _restoreConstraints(cur, table, cap)
    if jobs > 1:
        # ! THE NEW TABLE IS COMMITTED BEFORE ITS INDEXES (see
        #   _restoreIndexesParallel): the builders are other sessions. Nobody
        #   reads <table>_new; a run killed from here leaves it, without
        #   some indexes, for the next merge's _clearLeftovers to drop, as a
        #   lost swap always has. A failure drops it now.
        conn.commit()
        try:
            _restoreIndexesParallel(conn, table, cap, connect, jobs)
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '2min'")   # as the build
                _restoreConstraints(cur, table, cap)
        except BaseException:
            _dropNew(conn, table)
            raise

    # ! COMMIT BEFORE THE SWAP. The build is minutes of work; the swap is a
    #   metadata flip that needs an ACCESS EXCLUSIVE instant. When both shared
    #   one transaction, one stray reader at the rename aborted the lot --
    #   2026-08-25, five hours of pipeline died at 08_golive's lock timeout.
    #   Committed, {table}_new outlives a lost siege. Nobody reads it, so
    #   nothing observes a half-done state; `table` still flips atomically.
    conn.commit()
    _vacuumNew(conn, table)
    _swapSiege(conn, table, cap)                   # the swap becomes visible HERE

    with conn.cursor() as cur:
        _timed(cur, f"ANALYZE {table}", f"ANALYZE {table} (fresh planner stats)")
    conn.commit()
    print(f"MERGE COMPLETE. Verify, then:  DROP TABLE {table}_old;")
    print("-" * 70)