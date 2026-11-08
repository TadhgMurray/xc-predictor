#!/usr/bin/env python3
"""
db_health.py -- a READ-ONLY health report on the database: what it keeps that
nobody reads, what it reads the slow way, and how its settings compare with
what this box and this workload want.

    set -a; . /etc/xc-predictor.env; set +a
    python scripts/db_health.py                    # every section, a-g
    python scripts/db_health.py --only a,b,d       # some sections
    python scripts/db_health.py --dsn "dbname=xc_predictor user=postgres host=/var/run/postgresql"

    # the same report as plain SQL for psql (section d needs the repo: Python only)
    python scripts/db_health.py --sql --pg 18 | sudo -u postgres psql -X -P pager=off -d xc_predictor

Sections
    a  indexes never or rarely scanned since the stats window began (drop candidates)
    b  duplicate and overlapping indexes (one is a prefix of another)
    c  table and index bloat estimates, dead rows, (auto)vacuum/analyze, visibility map
    d  tables the site's code never names but the database still keeps and writes
    e  cache hit ratios; sequential scans of big tables
    f  temp files, checkpoints, WAL volume, I/O by process type
    g  settings now against the recommended ones; connections; the memory budget

★ SAFE ON A 600 GB DATABASE. Every number comes from the catalogue and the
  statistics views -- pg_class, pg_stats, pg_stat_*, pg_statio_*, pg_settings
  -- never from reading a table. No pgstattuple, no count(*). The session is
  read only (default_transaction_read_only) and AUTOCOMMIT: pg_relation_size
  takes a share lock on each relation it sizes, and inside one long
  transaction that lock would be held until the end and a pipeline swap
  (DROP + RENAME, AccessExclusive) would queue behind this report -- and every
  page behind the swap. One statement at a time, lock_timeout 1 s: a table
  being swapped is skipped and said, never waited for.

! THE STATS WINDOW IS SHORTER THAN IT LOOKS. Scan counters start when the
  statistics were last reset, AND a crash resets them (the OOM of 2026-11-07
  did), AND a table the pipeline rebuilds and swaps in is a new relation whose
  counters start at the swap. Section a says when the window began and marks
  each table rebuilt inside it; drop nothing on a window shorter than a full
  pipeline cycle plus a week of site traffic.

! INDEXES THE CODE LISTS ARE REBUILT BY THE CODE. An index named in
  racecast/build_ranking_results._CANONICAL_INDEXES or
  scripts/add_page_indexes.WANTED comes back on the next run however often
  it is dropped. Those are reported as "listed in code": take it out of the
  list first, then drop it.
"""
import argparse
import ast
import io
import os
import re
import sys
import time
import tokenize
import warnings

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECTIONS = "abcdefg"
GB = 1024 ** 3
MB = 1024 ** 2


# ─────────────────────────────────────────────────────────────────────────────
# The indexes the code itself builds (section a and b's "listed in code")
# ─────────────────────────────────────────────────────────────────────────────

def indexSig(ddl):
    """An index's column list and predicate with spaces and parentheses
    removed, lower case -- build_ranking_results._indexSig's rule, so a live
    index and a listed one compare the way the builder compares them."""
    i = ddl.find("(")
    tail = ddl[i:] if i >= 0 else ddl
    # ! the storage clause out, as the builder does (XCP_RR_LEAN writes
    #   WITH (fillfactor = 100); rr_layout.indexSig)
    tail = re.sub(r"\s+WITH\s*\([^()]*\)", "", tail, flags=re.I)
    return re.sub(r"[\s()]", "", tail).lower()


def _literal(path, name):
    """The literal value assigned to `name` at a module's top level, read
    without importing the module (the builders pull in the engine and need
    a database password just to import)."""
    try:
        with open(path, encoding="utf-8") as fh, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


def protectedIndexes(root=_ROOT):
    """[(table, index name, signature)] for every index the code builds."""
    out = []
    canon = _literal(os.path.join(root, "racecast", "build_ranking_results.py"),
                     "_CANONICAL_INDEXES") or {}
    for table, specs in canon.items():
        for name, cols in specs:
            out.append((table, name, indexSig(cols)))
    wanted = _literal(os.path.join(root, "scripts", "add_page_indexes.py"), "WANTED") or []
    for entry in wanted:
        if len(entry) == 4:
            table, col, name, spec = entry
            out.append((table, name, indexSig(spec or f"({col})")))
    return out


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def _protCte(protected):
    if not protected:
        return "prot(tbl, name, sig) AS (SELECT NULL::text, NULL::text, NULL::text WHERE false)"
    rows = ",\n        ".join(f"({_q(t)}, {_q(n)}, {_q(s)})" for t, n, s in protected)
    return f"prot(tbl, name, sig) AS (VALUES\n        {rows})"


# SQL that decides whether index `ix` (columns tbl, idx, def) is one the code lists
_LISTED = """EXISTS (SELECT 1 FROM prot p
                     WHERE p.tbl = {t} AND ({i} = p.name
                           OR {i} ~ ('^' || p.tbl || '(_new)*_' || p.name || '(_f)*$')
                           OR p.sig = lower(regexp_replace(substr({d}, strpos({d}, '(')), '[\\s()]', '', 'g'))))"""

_USING = "substr({d}, strpos({d}, ' USING ') + 7)"
_NOT_SYS = ("n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname !~ '^pg_toast'")


# ─────────────────────────────────────────────────────────────────────────────
# Recommended settings (section g). Each: name, proposed value, how it is
# applied, who it is for, why. The reasoning and the measurements behind each
# value are in the commit that added this file.
# ─────────────────────────────────────────────────────────────────────────────

RECOMMEND = [
    # memory
    ("huge_pages", "try", "restart", "server",
     "16GB of shared_buffers in 4kB pages costs every backend up to 32MB of page "
     "tables (PageTables in /proc/meminfo) and TLB misses; set vm.nr_hugepages to "
     "shared_memory_size_in_huge_pages + 2% first, then check huge_pages_status=on"),
    ("shared_buffers", "16GB", "restart", "server",
     "keep until huge pages are on and the memory budget below has headroom; "
     "the OS page cache holds the rest of the hot set"),
    ("work_mem", "64MB", "reload", "server",
     "the server-wide default reaches every connection that does not set its own "
     "(site, ad hoc scripts); the pipeline SETs 512MB itself in quiet mode"),
    ("hash_mem_multiplier", "2", "reload", "server", "keep the default"),
    ("maintenance_work_mem", "2GB", "reload", "server",
     "ad hoc CREATE INDEX / VACUUM; the pipeline SETs its own; autovacuum workers "
     "take autovacuum_work_mem (-1 = this) each, 3 workers = 6GB worst case"),
    ("effective_cache_size", "64GB", "reload", "server",
     "planner hint only: 125GB - 16GB buffers - up to 48GB site workers leaves ~60GB of page cache"),
    # I/O
    ("effective_io_concurrency", "200", "reload", "server",
     "NVMe md RAID serves hundreds of reads in flight; drives bitmap-heap and "
     "(PG18) read-stream prefetch. PG18's default is 16, older 1"),
    ("maintenance_io_concurrency", "200", "reload", "server",
     "the same for VACUUM, ANALYZE and index builds"),
    ("io_workers", "8", "reload", "server",
     "PG18 io_method=worker: 3 workers serve every async read of 24 threads"),
    ("random_page_cost", "1.1", "reload", "server", "keep: right for NVMe"),
    ("seq_page_cost", "1", "reload", "server", "keep"),
    # parallelism / JIT
    ("max_parallel_workers_per_gather", "2", "reload", "server",
     "keep for ad hoc work; the site sets its own (XCP_DB_PARALLEL_GATHER) and the "
     "pipeline its own (4)"),
    ("jit_above_cost", "1000000", "reload", "server",
     "compile only plans estimated 10x bigger than today's threshold: a 1 s compile "
     "is paid on every estimated-big query that reads a few thousand pages"),
    ("jit_inline_above_cost", "5000000", "reload", "server", "with jit_above_cost"),
    ("jit_optimize_above_cost", "5000000", "reload", "server", "with jit_above_cost"),
    # WAL and checkpoints
    ("wal_level", "minimal", "restart", "server",
     "backups are pg_dump (deploy/backup_db.sh): no replica, no archive, no PITR. "
     "At minimal, CREATE TABLE AS / COPY into a new table / CREATE INDEX / SET LOGGED "
     "write no WAL at all -- the pipeline's swaps stop writing their data twice. "
     "Needs max_wal_senders=0 and archive_mode=off"),
    ("max_wal_senders", "0", "restart", "server", "required by wal_level=minimal"),
    ("wal_compression", "lz4", "reload", "server",
     "full-page images after each checkpoint compressed ~2-4x; cheap CPU"),
    ("max_wal_size", "32GB", "reload", "server",
     "8GB fills in a minute of bulk writes and forces checkpoints (see section f: "
     "requested vs timed); each checkpoint restarts full-page images"),
    ("checkpoint_timeout", "15min", "reload", "server",
     "fewer checkpoints, fewer full-page writes; crash recovery stays minutes on NVMe"),
    ("checkpoint_completion_target", "0.9", "reload", "server", "keep (default)"),
    ("wal_buffers", "64MB", "restart", "server",
     "only matters while WAL is written in bulk; low priority"),
    # sessions
    ("idle_in_transaction_session_timeout", "0", "reload", "server",
     "keep 0 server-wide (a pipeline cursor streams inside one long transaction); "
     "the site sets 60s on its own connections (XCP_DB_IDLE_TXN_TIMEOUT_MS)"),
    ("max_connections", "200", "restart", "server",
     "keep: 72 site + the pipeline's pools + tools"),
    # observability
    ("shared_preload_libraries", "pg_prewarm,pg_stat_statements", "restart", "server",
     "per-query totals (time, rows, temp, buffers) -- the evidence every other change "
     "here should be checked against; then CREATE EXTENSION pg_stat_statements"),
    ("track_io_timing", "on", "reload", "server",
     "I/O time in pg_stat_statements, pg_stat_io and EXPLAIN (BUFFERS); cheap on a "
     "TSC clock (check with pg_test_timing)"),
    ("log_temp_files", "64MB", "reload", "server",
     "logs each query that spills more than 64MB, with its text"),
    ("log_line_prefix", "%m [%p] %q%a@%d ", "reload", "server",
     "%a is application_name: site vs xcp-pipeline in every log line"),
    ("log_lock_waits", "on", "reload", "server", "logs any wait past deadlock_timeout (1s)"),
    ("log_autovacuum_min_duration", "1min", "reload", "server",
     "autovacuum runs that took a minute, with their I/O"),
    ("track_activity_query_size", "4096", "restart", "server",
     "pipeline statements are longer than 1kB in pg_stat_activity; low priority"),
    # vacuum
    ("autovacuum", "on", "reload", "server",
     "keep; the boards tables have it off per table and the pipeline vacuums them "
     "itself (build_ranking_results, merge_column._vacuumNew, steps 18/18c)"),
    ("default_statistics_target", "100", "reload", "server",
     "keep server-wide; raise per column (school, person_id, meet_id on the big "
     "tables) in the builders after each swap -- a LIKE copy does not carry it"),
]

# Per-connection settings for the SITE only (scripts/database.py reads these
# from the service unit's environment; unset = the server's value).
SITE_ENV = [
    ("XCP_DB_WORK_MEM", "128MB", "work_mem",
     "the restricted ability board sorts one season of a pool in memory (a 36MB "
     "sort at half the real size; below 64MB it spilled 168MB and ran ~30% slower) "
     "-- and halves today's 72-connection worst case"),
    ("XCP_DB_PARALLEL_GATHER", "0", "max_parallel_workers_per_gather",
     "measured neutral for page queries (index probes the planner does not split); "
     "0 stops a page multiplying its work_mem by 3 and leaves the 16 workers to the pipeline"),
    ("XCP_DB_IDLE_TXN_TIMEOUT_MS", "60000", "idle_in_transaction_session_timeout",
     "getConn always rolls back; anything idle in a transaction for a minute is a leak"),
    ("XCP_DB_TEMP_FILE_LIMIT", "2GB", "temp_file_limit",
     "a page query that spills 2GB will hit the 55 s statement_timeout anyway; "
     "this ends it before it fills the disk"),
]


# ─────────────────────────────────────────────────────────────────────────────
# The queries. Each builder takes the server version (server_version_num) and
# returns one SQL statement; Python mode runs it, --sql prints it for psql.
# ─────────────────────────────────────────────────────────────────────────────

def sqlWindow(ver):
    return """
SELECT current_database() AS db,
       current_setting('server_version') AS version,
       to_char(pg_postmaster_start_time(), 'YYYY-MM-DD HH24:MI') AS started,
       to_char(d.stats_reset, 'YYYY-MM-DD HH24:MI') AS stats_reset,
       date_trunc('minute', now() - greatest(coalesce(d.stats_reset, pg_postmaster_start_time()),
                                             pg_postmaster_start_time()))::text AS "window (at most)",
       pg_size_pretty(pg_database_size(current_database())) AS db_size
FROM   pg_stat_database d
WHERE  d.datname = current_database()"""


def sqlUnused(ver, protected, rare, min_bytes):
    last = "s.last_idx_scan" if ver >= 160000 else "NULL::timestamptz"
    listed = _LISTED.format(t="ix.tbl", i="ix.idx", d="ix.def")
    using = _USING.format(d="def")
    return f"""
WITH {_protCte(protected)},
ix AS (
    SELECT s.relid, s.indexrelid, s.relname AS tbl, s.indexrelname AS idx, s.idx_scan,
           {last} AS last_scan, i.indisvalid AS valid,
           pg_get_indexdef(s.indexrelid) AS def,
           pg_relation_size(s.indexrelid) AS bytes
    FROM   pg_stat_user_indexes s
    JOIN   pg_index i ON i.indexrelid = s.indexrelid
    WHERE  (s.idx_scan <= {int(rare)} OR NOT i.indisvalid)
      AND  NOT i.indisprimary AND NOT i.indisunique AND NOT i.indisexclusion
      AND  NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = s.indexrelid)
), v AS (
    SELECT ix.*, t.n_tup_ins + t.n_tup_upd + t.n_tup_del AS writes,
           (t.n_live_tup > 1000 AND t.n_tup_ins >= 0.9 * t.n_live_tup) AS rebuilt,
           CASE WHEN NOT ix.valid THEN 'INVALID: drop, rebuild if wanted'
                WHEN {listed} THEN 'listed in code: keep unless dead'
                ELSE 'DROP CANDIDATE' END AS verdict
    FROM   ix JOIN pg_stat_user_tables t ON t.relid = ix.relid
    WHERE  ix.bytes >= {int(min_bytes)} OR NOT ix.valid
)
SELECT tbl AS "table", idx AS "index", pg_size_pretty(bytes) AS size, idx_scan AS scans,
       to_char(last_scan, 'YYYY-MM-DD HH24:MI') AS last_scan,
       writes AS "table writes", CASE WHEN rebuilt THEN 'yes' ELSE '' END AS "rebuilt in window",
       verdict, left({using}, 70) AS definition
FROM   v
ORDER  BY CASE verdict WHEN 'DROP CANDIDATE' THEN 1 WHEN 'listed in code: keep unless dead' THEN 3 ELSE 0 END,
          bytes DESC
LIMIT  80"""


def sqlDuplicates(ver, protected):
    listed = _LISTED.format(t="a_tbl", i="a_idx", d="a_def")
    return f"""
WITH {_protCte(protected)},
ix AS (
    SELECT i.indexrelid, i.indrelid, i.indnkeyatts AS nk, i.indnatts AS na, c.relam,
           (i.indisunique OR i.indisprimary OR i.indisexclusion
            OR EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)) AS special,
           string_to_array(i.indkey::text, ' ')::int[]    AS cols,
           string_to_array(i.indclass::text, ' ')::oid[]  AS ops,
           string_to_array(i.indoption::text, ' ')::int[] AS opts,
           coalesce(pg_get_expr(i.indexprs, i.indrelid), '') AS exprs,
           coalesce(pg_get_expr(i.indpred, i.indrelid), '')  AS pred
    FROM   pg_index i
    JOIN   pg_class c ON c.oid = i.indexrelid
    JOIN   pg_namespace n ON n.oid = c.relnamespace
    WHERE  {_NOT_SYS} AND i.indisvalid
), pairs AS (
    SELECT a.indexrelid AS a_oid, b.indexrelid AS b_oid,
           a.indrelid::regclass::text AS a_tbl, ca.relname AS a_idx,
           pg_get_indexdef(a.indexrelid) AS a_def, pg_get_indexdef(b.indexrelid) AS b_def,
           CASE WHEN a.nk = b.nk AND a.na = b.na THEN 'same columns'
                WHEN a.nk = b.nk THEN 'same key, cover adds INCLUDE'
                ELSE 'prefix of a longer key' END AS kind
    FROM   ix a
    JOIN   ix b ON b.indrelid = a.indrelid AND b.indexrelid <> a.indexrelid
                AND b.relam = a.relam AND b.pred = a.pred AND b.exprs = a.exprs
                AND a.nk <= b.nk
                AND a.cols[1:a.nk] = b.cols[1:a.nk]
                AND a.ops[1:a.nk]  = b.ops[1:a.nk]
                AND a.opts[1:a.nk] = b.opts[1:a.nk]
                AND (a.na = a.nk OR a.cols[a.nk + 1:a.na] <@ b.cols)
    JOIN   pg_class ca ON ca.oid = a.indexrelid
    WHERE  NOT a.special
      -- a prefix is only a prefix for btree
      AND  (a.nk = b.nk OR a.relam = (SELECT oid FROM pg_am WHERE amname = 'btree'))
      -- two identical plain indexes: list the pair once
      AND  NOT (a.nk = b.nk AND a.na = b.na AND a.cols = b.cols AND NOT b.special
                AND a.indexrelid < b.indexrelid)
)
SELECT p.a_tbl AS "table", p.a_idx AS "redundant?", p.b_oid::regclass AS "covered by", p.kind,
       pg_size_pretty(pg_relation_size(p.a_oid)) AS size,
       sa.idx_scan AS scans, sb.idx_scan AS "cover scans",
       CASE WHEN {listed} THEN 'listed in code' ELSE '' END AS "code",
       left({_USING.format(d="p.a_def")}, 55) AS "redundant def",
       left({_USING.format(d="p.b_def")}, 55) AS "cover def"
FROM   pairs p
LEFT JOIN pg_stat_user_indexes sa ON sa.indexrelid = p.a_oid
LEFT JOIN pg_stat_user_indexes sb ON sb.indexrelid = p.b_oid
ORDER  BY pg_relation_size(p.a_oid) DESC
LIMIT  60"""


def sqlTables(ver, min_bytes):
    return f"""
WITH st AS (
    SELECT s.schemaname, s.tablename, sum((1 - s.null_frac) * s.avg_width) AS w,
           max(s.null_frac) AS maxnull, count(*) AS ncols_stats
    FROM   pg_stats s WHERE NOT s.inherited GROUP BY 1, 2
), t AS (
    SELECT c.oid, n.nspname, c.relname, c.relpages, c.reltuples, c.relallvisible, c.relpersistence,
           c.reloptions, age(c.relfrozenxid) AS xid_age, pg_relation_size(c.oid) AS bytes,
           (SELECT count(*) FROM pg_attribute a
            WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS natts,
           coalesce((SELECT substring(o FROM 'fillfactor=([0-9]+)')::int
                     FROM unnest(c.reloptions) o WHERE o LIKE 'fillfactor=%'), 100) AS ff
    FROM   pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE  c.relkind IN ('r', 'm') AND {_NOT_SYS}
), e AS (
    SELECT t.*, st.ncols_stats, current_setting('block_size')::int AS bs,
           CASE WHEN st.w IS NULL OR t.reltuples <= 0 THEN NULL
                ELSE ceil(t.reltuples
                          * (ceil((23 + CASE WHEN st.maxnull > 0 THEN ceil(t.natts / 8.0) ELSE 0 END) / 8.0) * 8
                             + ceil(st.w / 8.0) * 8 + 4)
                          / ((current_setting('block_size')::int - 24) * t.ff / 100.0)) END AS est_pages
    FROM   t LEFT JOIN st ON st.schemaname = t.nspname AND st.tablename = t.relname
), b AS (
    SELECT e.*, CASE WHEN e.est_pages IS NULL THEN NULL
                     ELSE greatest(e.bytes - e.est_pages * e.bs, 0)::bigint END AS bloat
    FROM   e
)
SELECT b.relname AS "table", pg_size_pretty(b.bytes) AS heap,
       coalesce(pg_size_pretty(b.bloat), '?') AS "est bloat",
       CASE WHEN b.bloat IS NOT NULL AND b.bytes > 0 THEN round(100.0 * b.bloat / b.bytes) END AS "bloat%",
       s.n_live_tup AS live, s.n_dead_tup AS dead, s.n_mod_since_analyze AS "changed since analyze",
       CASE WHEN b.relpages > 0 THEN round(100.0 * b.relallvisible / b.relpages) END AS "all-visible%",
       to_char(greatest(s.last_vacuum, s.last_autovacuum), 'MM-DD HH24:MI') AS vacuumed,
       to_char(greatest(s.last_analyze, s.last_autoanalyze), 'MM-DD HH24:MI') AS analyzed,
       b.xid_age AS "xid age",
       concat_ws('; ',
         CASE WHEN b.reloptions::text LIKE '%autovacuum_enabled=false%' THEN 'autovacuum OFF' END,
         CASE WHEN b.relpersistence = 'u' THEN 'UNLOGGED: a crash empties it' END,
         CASE WHEN b.reltuples < 0 OR b.ncols_stats IS NULL THEN 'NO PLANNER STATS (never analyzed)' END,
         CASE WHEN s.n_dead_tup > 10000 AND s.n_dead_tup > 0.1 * greatest(s.n_live_tup, 1) THEN 'dead > 10%' END,
         CASE WHEN s.n_mod_since_analyze > 10000
               AND s.n_mod_since_analyze > 0.2 * greatest(s.n_live_tup, 1) THEN 'stats stale' END,
         CASE WHEN b.bytes > {GB} AND b.relpages > 0 AND b.relallvisible < 0.5 * b.relpages
              THEN 'visibility map < 50%: index-only scans read the heap' END,
         CASE WHEN b.bloat > {GB} AND b.bloat > 0.3 * b.bytes THEN 'BLOATED' END,
         CASE WHEN b.xid_age > 150000000 THEN 'xid age near autovacuum_freeze_max_age' END) AS flags
FROM   b JOIN pg_stat_user_tables s ON s.relid = b.oid
WHERE  b.bytes >= {int(min_bytes)}
ORDER  BY b.bytes DESC
LIMIT  50"""


def sqlIndexBloat(ver, min_bytes):
    return f"""
WITH ic AS (
    SELECT i.indexrelid, i.indrelid, c.reltuples, pg_relation_size(i.indexrelid) AS bytes,
           coalesce((SELECT substring(o FROM 'fillfactor=([0-9]+)')::int
                     FROM unnest(c.reloptions) o WHERE o LIKE 'fillfactor=%'), 90) AS ff,
           string_to_array(i.indkey::text, ' ')::int[] AS cols
    FROM   pg_index i
    JOIN   pg_class c ON c.oid = i.indexrelid
    JOIN   pg_am am ON am.oid = c.relam AND am.amname = 'btree'
    JOIN   pg_namespace n ON n.oid = c.relnamespace
    WHERE  {_NOT_SYS} AND i.indexprs IS NULL AND c.reltuples > 0
), w AS (
    SELECT ic.indexrelid, ic.indrelid, ic.bytes, ic.reltuples, ic.ff,
           sum((1 - s.null_frac) * s.avg_width) AS w, bool_or(s.null_frac > 0) AS hasnull,
           count(s.attname) AS nstats, count(*) AS ncols
    FROM   ic
    CROSS  JOIN LATERAL unnest(ic.cols) AS k(attnum)
    JOIN   pg_attribute a ON a.attrelid = ic.indrelid AND a.attnum = k.attnum
    JOIN   pg_class tc ON tc.oid = ic.indrelid
    JOIN   pg_namespace tn ON tn.oid = tc.relnamespace
    LEFT   JOIN pg_stats s ON s.schemaname = tn.nspname AND s.tablename = tc.relname
                          AND s.attname = a.attname AND NOT s.inherited
    WHERE  ic.bytes >= {int(min_bytes)}
    GROUP  BY 1, 2, 3, 4, 5
), e AS (
    SELECT w.*, current_setting('block_size')::int AS bs,
           ceil(w.reltuples * (ceil((8 + CASE WHEN w.hasnull THEN 4 ELSE 0 END + w.w) / 8.0) * 8 + 4)
                / ((current_setting('block_size')::int - 24 - 16) * w.ff / 100.0)) * 1.01 + 1 AS est_pages
    FROM   w WHERE w.nstats = w.ncols
)
SELECT e.indrelid::regclass AS "table", e.indexrelid::regclass AS "index", pg_size_pretty(e.bytes) AS size,
       pg_size_pretty(greatest(e.bytes - e.est_pages * e.bs, 0)::bigint) AS "est bloat",
       round(100.0 * greatest(e.bytes - e.est_pages * e.bs, 0) / e.bytes) AS "bloat%"
FROM   e
WHERE  e.bytes - e.est_pages * e.bs > 0.2 * e.bytes
ORDER  BY e.bytes - e.est_pages * e.bs DESC
LIMIT  25"""


def sqlCacheTables(ver):
    return """
SELECT relname AS "table",
       pg_size_pretty(heap_blks_read * current_setting('block_size')::bigint) AS "heap read",
       round(100.0 * heap_blks_hit / nullif(heap_blks_hit + heap_blks_read, 0), 2) AS "heap hit%",
       pg_size_pretty(coalesce(idx_blks_read, 0) * current_setting('block_size')::bigint) AS "index read",
       round(100.0 * idx_blks_hit / nullif(idx_blks_hit + idx_blks_read, 0), 2) AS "index hit%",
       pg_size_pretty(coalesce(toast_blks_read, 0) * current_setting('block_size')::bigint) AS "toast read"
FROM   pg_statio_user_tables
WHERE  heap_blks_read + coalesce(idx_blks_read, 0) > 0
ORDER  BY heap_blks_read + coalesce(idx_blks_read, 0) + coalesce(toast_blks_read, 0) DESC
LIMIT  20"""


def sqlCacheIndexes(ver):
    return """
SELECT relname AS "table", indexrelname AS "index",
       pg_size_pretty(idx_blks_read * current_setting('block_size')::bigint) AS "read",
       round(100.0 * idx_blks_hit / nullif(idx_blks_hit + idx_blks_read, 0), 2) AS "hit%"
FROM   pg_statio_user_indexes
WHERE  idx_blks_read > 0
ORDER  BY idx_blks_read DESC
LIMIT  20"""


def sqlSeqScans(ver, min_rows):
    last = "to_char(s.last_seq_scan, 'MM-DD HH24:MI')" if ver >= 160000 else "NULL::text"
    return f"""
SELECT s.relname AS "table", pg_size_pretty(pg_relation_size(s.relid)) AS heap,
       c.reltuples::bigint AS "rows", s.seq_scan AS "seq scans",
       (s.seq_tup_read / nullif(s.seq_scan, 0))::bigint AS "rows per scan",
       s.idx_scan AS "index scans", {last} AS "last seq scan",
       round(100.0 * s.seq_scan / nullif(s.seq_scan + coalesce(s.idx_scan, 0), 0), 1) AS "seq%",
       CASE WHEN s.seq_scan >= 50 AND c.reltuples >= 1000000
                 AND s.seq_tup_read / nullif(s.seq_scan, 0) > 0.5 * c.reltuples
            THEN 'MANY FULL SCANS: a page or a loop, not the pipeline?' ELSE '' END AS note
FROM   pg_stat_user_tables s JOIN pg_class c ON c.oid = s.relid
WHERE  c.reltuples >= {int(min_rows)} AND s.seq_scan > 0
ORDER  BY s.seq_tup_read DESC
LIMIT  25"""


def sqlStatements(ver, order):
    col = {"time": "total_exec_time", "temp": "temp_blks_written", "read": "shared_blks_read"}[order]
    return f"""
SELECT round(total_exec_time::numeric / 1000) AS "total s", calls,
       round((total_exec_time / nullif(calls, 0))::numeric, 1) AS "mean ms",
       rows / nullif(calls, 0) AS "rows/call",
       pg_size_pretty(shared_blks_read * current_setting('block_size')::bigint) AS "read",
       pg_size_pretty(temp_blks_written * current_setting('block_size')::bigint) AS "temp written",
       left(regexp_replace(query, '\\s+', ' ', 'g'), 110) AS query
FROM   pg_stat_statements
WHERE  dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND  {col} > 0
ORDER  BY {col} DESC
LIMIT  15"""


def sqlTemp(ver):
    return """
SELECT datname AS db, temp_files AS "temp files", pg_size_pretty(temp_bytes) AS "temp bytes",
       pg_size_pretty((temp_bytes / greatest(extract(epoch FROM now() - coalesce(stats_reset,
                       pg_postmaster_start_time())) / 86400, 1.0 / 24))::bigint) AS "temp per day",
       round(100.0 * blks_hit / nullif(blks_hit + blks_read, 0), 2) AS "hit%",
       xact_commit AS commits, xact_rollback AS rollbacks, deadlocks,
       to_char(stats_reset, 'YYYY-MM-DD HH24:MI') AS since
FROM   pg_stat_database
WHERE  datname IS NOT NULL AND (temp_bytes > 0 OR datname = current_database())
ORDER  BY temp_bytes DESC"""


def sqlCheckpoints(ver):
    if ver >= 170000:
        done = "num_done AS done, " if ver >= 180000 else ""
        return f"""
SELECT num_timed AS timed, num_requested AS requested, {done}
       round(100.0 * num_requested / nullif(num_timed + num_requested, 0)) AS "requested%",
       round(write_time / 1000) AS "write s", round(sync_time / 1000) AS "sync s",
       pg_size_pretty(buffers_written * current_setting('block_size')::bigint) AS written,
       to_char(stats_reset, 'YYYY-MM-DD HH24:MI') AS since
FROM   pg_stat_checkpointer"""
    backend = "pg_size_pretty(buffers_backend * current_setting('block_size')::bigint) AS \"by backends\","
    return f"""
SELECT checkpoints_timed AS timed, checkpoints_req AS requested,
       round(100.0 * checkpoints_req / nullif(checkpoints_timed + checkpoints_req, 0)) AS "requested%",
       round(checkpoint_write_time / 1000) AS "write s", round(checkpoint_sync_time / 1000) AS "sync s",
       pg_size_pretty(buffers_checkpoint * current_setting('block_size')::bigint) AS "by checkpoints",
       pg_size_pretty(buffers_clean * current_setting('block_size')::bigint) AS "by bgwriter",
       {backend}
       maxwritten_clean AS "bgwriter stops",
       to_char(stats_reset, 'YYYY-MM-DD HH24:MI') AS since
FROM   pg_stat_bgwriter"""


def sqlBgwriter(ver):
    if ver < 170000:
        return None                              # folded into sqlCheckpoints
    return """
SELECT pg_size_pretty(buffers_clean * current_setting('block_size')::bigint) AS "cleaned",
       maxwritten_clean AS "stops at bgwriter_lru_maxpages",
       pg_size_pretty(buffers_alloc * current_setting('block_size')::bigint) AS "allocated",
       to_char(stats_reset, 'YYYY-MM-DD HH24:MI') AS since
FROM   pg_stat_bgwriter"""


def sqlWal(ver):
    if ver < 140000:
        return None
    return """
SELECT wal_records AS records, wal_fpi AS "full-page images",
       round(100.0 * wal_fpi / nullif(wal_records, 0), 1) AS "fpi%",
       pg_size_pretty(wal_bytes) AS "WAL written",
       pg_size_pretty((wal_bytes / greatest(extract(epoch FROM now() - stats_reset) / 86400, 1.0 / 24))::bigint)
           AS "WAL per day",
       wal_buffers_full AS "wal_buffers full",
       to_char(stats_reset, 'YYYY-MM-DD HH24:MI') AS since
FROM   pg_stat_wal"""


def sqlWalDir(ver):
    return """
SELECT count(*) AS "WAL files", pg_size_pretty(sum(size)) AS "pg_wal size",
       current_setting('max_wal_size') AS max_wal_size, current_setting('wal_level') AS wal_level,
       current_setting('wal_compression') AS wal_compression
FROM   pg_ls_waldir()"""


def sqlIo(ver):
    if ver < 160000:
        return None
    if ver >= 180000:
        rd, wr, ex = "read_bytes", "write_bytes", "extend_bytes"
    else:
        rd, wr, ex = "reads * op_bytes", "writes * op_bytes", "extends * op_bytes"
    return f"""
SELECT backend_type, object, context,
       pg_size_pretty(sum(coalesce({rd}, 0))::bigint) AS "read",
       pg_size_pretty(sum(coalesce({wr}, 0))::bigint) AS "written",
       pg_size_pretty(sum(coalesce({ex}, 0))::bigint) AS "extended",
       sum(coalesce(hits, 0)) AS hits, sum(coalesce(evictions, 0)) AS evictions,
       sum(coalesce(fsyncs, 0)) AS fsyncs
FROM   pg_stat_io
GROUP  BY 1, 2, 3
HAVING sum(coalesce({rd}, 0)) + sum(coalesce({wr}, 0)) + sum(coalesce({ex}, 0)) > 0
ORDER  BY sum(coalesce({rd}, 0)) + sum(coalesce({wr}, 0)) + sum(coalesce({ex}, 0)) DESC
LIMIT  20"""


def sqlSettings(ver):
    rows = ",\n        ".join(f"({_q(n)}, {_q(v)}, {_q(a)}, {_q(w)})"
                              for n, v, a, _who, w in RECOMMEND)
    return f"""
WITH rec(name, proposed, apply, why) AS (VALUES
        {rows})
SELECT r.name AS setting, coalesce(current_setting(r.name, true), '(not in this version)') AS now,
       r.proposed AS proposed,
       CASE WHEN current_setting(r.name, true) IS NULL THEN ''
            WHEN lower(replace(current_setting(r.name, true), ' ', '')) = lower(replace(r.proposed, ' ', ''))
            THEN 'ok' ELSE 'CHANGE' END AS "state",
       coalesce(p.context, '') AS context,
       CASE WHEN p.pending_restart THEN 'PENDING RESTART' ELSE '' END AS pending,
       r.apply AS apply, left(r.why, 90) AS why
FROM   rec r LEFT JOIN pg_settings p ON p.name = r.name
ORDER  BY CASE WHEN current_setting(r.name, true) IS NULL THEN 2
               WHEN lower(replace(current_setting(r.name, true), ' ', '')) = lower(replace(r.proposed, ' ', ''))
               THEN 1 ELSE 0 END, r.apply, r.name"""


def sqlSiteEnv(ver):
    rows = ",\n        ".join(f"({_q(e)}, {_q(v)}, {_q(s)}, {_q(w)})" for e, v, s, w in SITE_ENV)
    return f"""
SELECT env AS "site service unit", proposed, setting, current_setting(setting) AS "server value now",
       left(why, 90) AS why
FROM   (VALUES
        {rows}) AS t(env, proposed, setting, why)"""


def sqlConnections(ver):
    return """
SELECT coalesce(nullif(application_name, ''), backend_type) AS "who", state, count(*) AS n,
       max(date_trunc('second', now() - coalesce(xact_start, backend_start)))::text AS "oldest"
FROM   pg_stat_activity
WHERE  pid <> pg_backend_pid()
GROUP  BY 1, 2
ORDER  BY 3 DESC"""


def sqlMemory(ver):
    huge = ("current_setting('shared_memory_size', true) AS shared_memory, "
            "current_setting('shared_memory_size_in_huge_pages', true) AS \"needs vm.nr_hugepages\", ")
    status = "current_setting('huge_pages_status', true) AS huge_pages_status, " if ver >= 170000 else ""
    return f"""
SELECT current_setting('shared_buffers') AS shared_buffers, current_setting('huge_pages') AS huge_pages,
       {status}{huge}
       current_setting('work_mem') AS work_mem, current_setting('hash_mem_multiplier') AS hash_mem_multiplier,
       current_setting('maintenance_work_mem') AS maintenance_work_mem,
       current_setting('autovacuum_max_workers') AS av_workers,
       current_setting('max_connections') AS max_connections,
       current_setting('max_parallel_workers') AS max_parallel_workers,
       current_setting('max_worker_processes') AS max_worker_processes"""


# ─────────────────────────────────────────────────────────────────────────────
# Section d: what the site's code names, from the code
# ─────────────────────────────────────────────────────────────────────────────

_SITE_DIRS = ("racecast", "scripts", "engine")      # gunicorn's --pythonpath
_OTHER_DIRS = ("racecast", "scripts", "engine", "model", "sources", "tfrrs", "backfill", "tools")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _moduleIndex(root):
    idx = {}
    for d in _SITE_DIRS:
        full = os.path.join(root, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(os.listdir(full)):
            p = os.path.join(full, f)
            if f.endswith(".py"):
                idx.setdefault(f[:-3], p)
            elif os.path.isfile(os.path.join(p, "__init__.py")):
                idx.setdefault(f, os.path.join(p, "__init__.py"))
    return idx


def _parse(path):
    try:
        with open(path, encoding="utf-8") as fh, warnings.catch_warnings():
            warnings.simplefilter("ignore")              # a stray backslash in someone's string
            src = fh.read()
            return src, ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return None, None


def _isScript(path, src):
    """A pipeline script rather than a site library: a builder by name, or a
    file under scripts/ or engine/ that runs as __main__."""
    base = os.path.basename(path)
    if base.startswith("build_") or base == "merge_column.py":
        return True
    if src is None:
        src, _tree = _parse(path)
    return (os.path.basename(os.path.dirname(path)) in ("scripts", "engine")
            and base != "database.py" and "__main__" in (src or ""))


def _topDefs(tree, names):
    """The top-level defs, classes and assignments of `names` in a module."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
            out.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in names for t in targets):
                out.append(node)
    return out


def _importsIn(nodes):
    """(modules imported whole, {module: names imported from it})."""
    whole, partial = [], {}
    for top in nodes:
        for node in ast.walk(top):
            if isinstance(node, ast.Import):
                whole += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mod = node.module.split(".")[0]
                partial.setdefault(mod, set()).update(a.name for a in node.names if a.name != "*")
                whole += [a.name for a in node.names if a.name != "*"]   # from pkg import module
            elif (isinstance(node, ast.Call)
                  and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("import_module", "__import__")
                  and node.args and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str)):
                whole.append(node.args[0].value.split(".")[0])
    return whole, partial


def siteModules(root=_ROOT, entry="app"):
    """{module: (path, names)} for every repo module the site can import --
    the closure of racecast/app.py's imports (top level and inside
    functions, and importlib.import_module("x")) over racecast/, scripts/,
    engine/, gunicorn's --pythonpath.

    ! A PIPELINE SCRIPT THE SITE BORROWS ONE HELPER FROM IS NOT THE SITE.
      app.py does `from build_ranking_results import timedEventKind`; read
      whole, that builder would make every table it writes look read by the
      site. A script (_isScript) reached only by `from X import names` counts
      for those names' own definitions (names is that set); anything else is
      read whole (names is None)."""
    idx = _moduleIndex(root)
    got = {}                                      # module -> None (whole) or a set of names
    todo = [(entry, None)]
    while todo:
        m, names = todo.pop()
        if m not in idx:
            continue
        path = idx[m]
        if names is not None and not _isScript(path, None):
            names = None                          # a library: read whole
        if m in got:
            prev = got[m]
            if prev is None or (names is not None and names <= prev):
                continue                          # nothing new to read
            names = None if names is None else names | prev
        got[m] = names
        _src, tree = _parse(path)
        if tree is None:
            continue
        nodes = [tree] if names is None else _topDefs(tree, names)
        whole, partial = _importsIn(nodes)
        todo += [(mod, set(ns)) for mod, ns in partial.items()]
        todo += [(w, None) for w in whole if w not in partial]
    return {m: (idx[m], ns) for m, ns in got.items()}


def stringWords(path, names=None):
    """(words, prefixes): identifier-shaped words inside a file's string
    literals (SQL lives in strings; comments do not count), and the
    fragments that end in '_' (a table named f"{base}_{suffix}"). With
    `names`, only those top-level definitions are read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return set(), set()
    if not path.endswith(".py"):
        return set(_WORD.findall(src)), set()
    if names is not None:
        _src, tree = _parse(path)
        if tree is None:
            return set(), set()
        src = "\n\n".join(ast.get_source_segment(src, n) or "" for n in _topDefs(tree, names))
    parts = []
    kinds = {tokenize.STRING}
    for extra in ("FSTRING_MIDDLE", "TSTRING_MIDDLE"):
        if hasattr(tokenize, extra):
            kinds.add(getattr(tokenize, extra))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in kinds:
                parts.append(tok.string)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        parts = [src]
    text = "\n".join(parts)
    words = set(_WORD.findall(text))
    prefixes = {w for w in words if w.endswith("_") and len(w) >= 6}
    return words, prefixes


def codeReferences(root=_ROOT):
    """(site_words, site_prefixes, other: {word: [files]})."""
    site = siteModules(root)
    site_paths = {p for p, names in site.values() if names is None}
    site_words, site_prefixes = set(), set()
    for p, names in site.values():
        w, pre = stringWords(p, names)
        site_words |= w
        site_prefixes |= pre
    tdir = os.path.join(root, "racecast", "templates")
    if os.path.isdir(tdir):
        for dirpath, _d, files in os.walk(tdir):
            for f in files:
                w, _pre = stringWords(os.path.join(dirpath, f))
                site_words |= w
    other = {}
    paths = []
    for d in _OTHER_DIRS:
        full = os.path.join(root, d)
        if os.path.isdir(full):
            paths += [os.path.join(full, f) for f in sorted(os.listdir(full)) if f.endswith(".py")]
    ddir = os.path.join(root, "deploy")
    if os.path.isdir(ddir):
        paths += [os.path.join(ddir, f) for f in sorted(os.listdir(ddir)) if f.endswith(".sh")]
    for p in paths:
        if p in site_paths:
            continue
        w, _pre = stringWords(p)
        rel = os.path.relpath(p, root)
        for word in w:
            other.setdefault(word, []).append(rel)
    return site_words, site_prefixes, other


_LEFTOVER = re.compile(r"(_new|_old|_bak|_backup|_tmp|_copy|_prev|_\d{6,8})$|^(tmp_|temp_|old_|bak_)")


def sqlTableList(ver):
    return """
SELECT c.relname, pg_total_relation_size(c.oid) AS bytes, c.reltuples::bigint AS nrows,
       coalesce(s.n_tup_ins + s.n_tup_upd + s.n_tup_del, 0) AS writes,
       coalesce(s.seq_scan, 0) + coalesce(s.idx_scan, 0) AS scans
FROM   pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT   JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE  c.relkind IN ('r', 'm', 'p') AND n.nspname = 'public' AND NOT c.relispartition"""


def unreadTables(tables, site_words, site_prefixes, other, min_bytes):
    """Rows for section d: tables the site's code never names."""
    out = []
    for name, nbytes, nrows, writes, scans in tables:
        if name in site_words:
            continue
        dynamic = sorted(p for p in site_prefixes if name.startswith(p))
        if nbytes < min_bytes and not _LEFTOVER.search(name):
            continue
        writers = other.get(name, [])
        if _LEFTOVER.search(name):
            verdict = "leftover copy? (name)"
        elif dynamic:
            verdict = "maybe read by name built at run time"
        elif not writers:
            verdict = "named by no code at all"
        else:
            verdict = "pipeline-only? confirm"
        out.append((name, _pretty(nbytes), nrows, writes, scans,
                    ", ".join(dynamic[:2]), verdict,
                    ", ".join(writers[:3]) + (f" +{len(writers) - 3}" if len(writers) > 3 else "")))
    out.sort(key=lambda r: -_unpretty(r[1]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Host memory (section g, Python mode, only when run on the database's box)
# ─────────────────────────────────────────────────────────────────────────────

_MEMINFO_KEYS = ("MemTotal", "MemAvailable", "Cached", "Shmem", "PageTables", "Committed_AS",
                 "AnonHugePages", "HugePages_Total", "HugePages_Free", "HugePages_Rsvd", "Hugepagesize")


def hostMemory():
    rows = []
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                if k in _MEMINFO_KEYS:
                    rows.append((k, v.strip()))
    except OSError:
        return []
    try:
        with open("/sys/kernel/mm/transparent_hugepage/enabled") as fh:
            rows.append(("transparent_hugepage", fh.read().strip()))
    except OSError:
        pass
    return rows


def _bytes(setting):
    """'16GB' / '256MB' / '4096kB' -> bytes."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*(B|kB|MB|GB|TB)?\s*", setting or "")
    if not m:
        return 0
    mult = {"B": 1, "kB": 1024, "MB": MB, "GB": GB, "TB": 1024 * GB, None: 1}[m.group(2)]
    return int(float(m.group(1)) * mult)


def _pretty(n):
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _unpretty(s):
    m = re.fullmatch(r"([0-9.]+) (B|kB|MB|GB|TB)", s or "")
    return _bytes(f"{m.group(1)}{m.group(2)}") if m else 0


def memoryBudget(settings, site_conns=72, pipe_jobs=4, pipe_workers=4,
                 pipe_work_mem="512MB", pipe_maint="2GB", site_work_mem=None, site_nodes=2):
    """Worst cases, in bytes, as (label, bytes) rows. Not a forecast: what
    the settings PERMIT if every connection used its budget at once."""
    sb = _bytes(settings.get("shared_buffers"))
    wm = _bytes(site_work_mem or settings.get("work_mem"))
    hm = float(settings.get("hash_mem_multiplier") or 2)
    pwm = _bytes(pipe_work_mem)
    avw = int(settings.get("autovacuum_max_workers") or 3)
    maint = _bytes(settings.get("maintenance_work_mem"))
    site = site_conns * site_nodes * wm * hm
    pipe = pipe_jobs * (1 + pipe_workers) * pwm * hm
    pipe_idx = pipe_jobs * _bytes(pipe_maint)
    av = avw * maint
    rows = [
        ("shared_buffers", sb),
        (f"site: {site_conns} conns x {site_nodes} sort/hash nodes x work_mem x hash_mem_multiplier", site),
        (f"pipeline: {pipe_jobs} jobs x (1 + {pipe_workers} workers) x one hash node at {pipe_work_mem}"
         f" x hash_mem_multiplier", pipe),
        (f"pipeline: {pipe_jobs} index builds x maintenance_work_mem {pipe_maint}", pipe_idx),
        (f"autovacuum: {avw} workers x maintenance_work_mem", av),
    ]
    rows.append(("total (every budget at once)", sum(b for _l, b in rows)))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Running and printing
# ─────────────────────────────────────────────────────────────────────────────

def _cell(v, width=72):
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        v = v.isoformat(sep=" ", timespec="minutes") if hasattr(v, "hour") else v.isoformat()
    s = str(v).replace("\n", " ")
    return s if len(s) <= width else s[:width - 1] + "~"


def table(cols, rows, out=sys.stdout, indent="  "):
    if not rows:
        print(f"{indent}(none)", file=out)
        return
    cells = [[_cell(v) for v in r] for r in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(cols)]
    print(indent + "  ".join(c.ljust(w) for c, w in zip(cols, widths)).rstrip(), file=out)
    print(indent + "  ".join("-" * w for w in widths), file=out)
    for r in cells:
        print(indent + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip(), file=out)


class Report:
    """One read-only, autocommit session and the sections run on it."""

    def __init__(self, conn, out=sys.stdout, root=_ROOT, min_mb=64, rare=50):
        self.conn, self.out, self.root = conn, out, root
        self.min_bytes, self.rare = int(min_mb * MB), rare
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
            cur.execute("SET statement_timeout = '120s'")
            cur.execute("SET lock_timeout = '1s'")
            cur.execute("SET jit = off")
            cur.execute("SHOW server_version_num")
            self.ver = int(cur.fetchone()[0])
        self.protected = protectedIndexes(root)

    def say(self, *a):
        print(*a, file=self.out)

    def run(self, title, sql, retries=1):
        """Run one query and print it as a table; a failure is printed, never raised."""
        if sql is None:
            return None
        if title:
            self.say(f"\n-- {title}")
        for attempt in range(retries + 1):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql)
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                table(cols, rows, self.out)
                return cols, rows
            except Exception as exc:                          # noqa: BLE001
                msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
                if "lock timeout" in msg and attempt < retries:
                    time.sleep(3)
                    continue
                self.say(f"  (skipped: {msg})")
                return None
        return None

    def scalar(self, sql):
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                r = cur.fetchone()
                return r[0] if r else None
        except Exception:                                     # noqa: BLE001
            return None

    # sections ---------------------------------------------------------------

    def a(self):
        self.say("\n== (a) indexes scanned at most %d times in the stats window "
                 "(no PK/unique/constraint indexes) ==" % self.rare)
        self.run("stats window", sqlWindow(self.ver))
        self.say(f"  {len(self.protected)} indexes are listed in code "
                 "(build_ranking_results._CANONICAL_INDEXES, add_page_indexes.WANTED)")
        self.run(f"candidates (>= {self.min_bytes // MB} MB, or invalid)",
                 sqlUnused(self.ver, self.protected, self.rare, self.min_bytes))
        self.say("  'rebuilt in window' = the table was loaded inside the window, so its counters"
                 " start at the swap.\n  To drop one (not done here): DROP INDEX CONCURRENTLY <name>;"
                 "  -- a 'listed in code' one comes back unless the list changes first")

    def b(self):
        self.say("\n== (b) duplicate and overlapping indexes (same leading columns) ==")
        self.run(None, sqlDuplicates(self.ver, self.protected))
        self.say("  'prefix': the longer index answers every lookup the shorter one does (a little"
                 " bigger to read);\n  a shorter UNIQUE/constraint index is never listed. Equal"
                 " definitions in both directions are listed once.")

    def c(self):
        self.say("\n== (c) tables: size, estimated bloat, dead rows, vacuum/analyze, visibility map ==")
        self.say("  bloat is estimated from pg_stats widths and reltuples (no scan), +-20%;"
                 " '?' = no stats to estimate from")
        self.run(f"tables >= {self.min_bytes // MB} MB", sqlTables(self.ver, self.min_bytes))
        self.run("btree indexes over 20% larger than their estimated size",
                 sqlIndexBloat(self.ver, self.min_bytes))
        self.say("  vacuum/analyze times reset with the statistics (a crash clears them);"
                 " 'NO PLANNER STATS' reads pg_class/pg_stats, which survive")

    def d(self):
        self.say("\n== (d) tables the site's code never names ==")
        try:
            site_words, site_prefixes, other = codeReferences(self.root)
        except Exception as exc:                              # noqa: BLE001
            self.say(f"  (skipped: could not read the code under {self.root}: {exc})")
            return
        mods = siteModules(self.root)
        part = sorted(m for m, (_p, ns) in mods.items() if ns is not None)
        self.say(f"  site = racecast/app.py and the {len(mods) - 1} repo modules it can import"
                 f" ({len(part)} pipeline scripts read only for the helpers it borrows: {', '.join(part[:6])});"
                 "\n  a table counts as read when its name is in their SQL/strings or the templates")
        try:
            with self.conn.cursor() as cur:
                cur.execute(sqlTableList(self.ver))
                tables = cur.fetchall()
        except Exception as exc:                              # noqa: BLE001
            self.say(f"  (skipped: {str(exc).strip().splitlines()[0]})")
            return
        rows = unreadTables(tables, site_words, site_prefixes, other, self.min_bytes)
        table(["table", "total size", "rows", "writes", "scans", "site prefix", "verdict",
               "named by (not the site)"], rows, self.out)
        self.say("  scans/writes are since the stats window began (both pipeline and site)."
                 "  Confirm each before dropping anything:\n  a table read only through a"
                 " name built at run time shows 'maybe read'; one the pipeline writes and"
                 " nothing reads is pure cost.")

    def e(self):
        self.say("\n== (e) cache and sequential scans ==")
        self.run("database hit ratio", """
SELECT round(100.0 * blks_hit / nullif(blks_hit + blks_read, 0), 3) AS "hit%",
       pg_size_pretty(blks_read * current_setting('block_size')::bigint) AS "read from OS/disk"
FROM pg_stat_database WHERE datname = current_database()""")
        self.run("tables by blocks read outside shared_buffers (OS cache or disk)", sqlCacheTables(self.ver))
        self.run("indexes by blocks read outside shared_buffers", sqlCacheIndexes(self.ver))
        self.run("sequential scans of tables with >= 100k rows", sqlSeqScans(self.ver, 100000))
        if self.scalar("SELECT to_regclass('pg_stat_statements') IS NOT NULL"):
            self.run("pg_stat_statements: top by total time", sqlStatements(self.ver, "time"))
            self.run("pg_stat_statements: top by temp written", sqlStatements(self.ver, "temp"))
            self.run("pg_stat_statements: top by blocks read", sqlStatements(self.ver, "read"))
        else:
            self.say("\n  pg_stat_statements is not installed: per-query evidence needs it (section g)")

    def f(self):
        self.say("\n== (f) temp files, checkpoints, WAL, I/O ==")
        self.run("temp files per database", sqlTemp(self.ver))
        self.run("checkpoints (requested% high = max_wal_size too small for the write bursts)",
                 sqlCheckpoints(self.ver))
        self.run("background writer", sqlBgwriter(self.ver))
        self.run("WAL generated", sqlWal(self.ver))
        self.run("WAL on disk", sqlWalDir(self.ver))
        self.run("I/O by process type (client backend writes/evictions = buffers under pressure)",
                 sqlIo(self.ver))

    def g(self):
        self.say("\n== (g) settings: now vs recommended ==")
        self.run("server settings (ALTER SYSTEM SET name = 'value'; then SELECT pg_reload_conf()"
                 " or restart per 'apply')", sqlSettings(self.ver))
        self.run("site-only settings (systemd: Environment= in the site's service unit;"
                 " scripts/database.py sends them per connection)", sqlSiteEnv(self.ver))
        self.run("connections now", sqlConnections(self.ver))
        got = self.run("memory settings", sqlMemory(self.ver))
        mem = hostMemory()
        if mem:
            self.say("\n-- this host (/proc/meminfo; only meaningful when run on the database's box)")
            table(["key", "value"], mem, self.out)
        if got:
            settings = dict(zip(got[0], got[1][0]))
            self.say("\n-- memory budget: what the settings PERMIT at once (not a forecast)")
            for label, site_wm in (("server work_mem for the site", None),
                                   ("site at XCP_DB_WORK_MEM=128MB", "128MB")):
                self.say(f"   {label}:")
                table(["budget", "bytes"], [(lbl, _pretty(b)) for lbl, b in
                                            memoryBudget(settings, site_work_mem=site_wm)], self.out, "     ")


def sqlScript(ver, protected, min_mb=64, rare=50):
    """The whole report (but section d, which needs the repo) as psql input."""
    mb = int(min_mb * MB)
    parts = ["\\pset pager off", "\\pset footer off",
             "SET default_transaction_read_only = on;", "SET statement_timeout = '120s';",
             "SET lock_timeout = '1s';", "SET jit = off;"]

    def q(title, sql):
        if sql is None:
            return
        parts.append(f"\\echo '-- {title.replace(chr(39), '')}'")
        parts.append(sql.strip() + ";")

    parts.append("\\echo '== (a) rarely scanned indexes =='")
    q("stats window", sqlWindow(ver))
    q("candidates", sqlUnused(ver, protected, rare, mb))
    parts.append("\\echo '== (b) duplicate and overlapping indexes =='")
    q("pairs", sqlDuplicates(ver, protected))
    parts.append("\\echo '== (c) tables, bloat, vacuum =='")
    q("tables", sqlTables(ver, mb))
    q("btree index bloat", sqlIndexBloat(ver, mb))
    parts.append("\\echo '== (d) needs the repo: run python scripts/db_health.py --only d =='")
    parts.append("\\echo '== (e) cache and sequential scans =='")
    q("tables by blocks read", sqlCacheTables(ver))
    q("indexes by blocks read", sqlCacheIndexes(ver))
    q("sequential scans", sqlSeqScans(ver, 100000))
    parts.append("SELECT to_regclass('pg_stat_statements') IS NOT NULL AS has_pss \\gset")
    parts.append("\\if :has_pss")
    q("pg_stat_statements by time", sqlStatements(ver, "time"))
    q("pg_stat_statements by temp", sqlStatements(ver, "temp"))
    parts.append("\\endif")
    parts.append("\\echo '== (f) temp, checkpoints, WAL, I/O =='")
    q("temp files", sqlTemp(ver))
    q("checkpoints", sqlCheckpoints(ver))
    q("bgwriter", sqlBgwriter(ver))
    q("WAL", sqlWal(ver))
    q("WAL dir", sqlWalDir(ver))
    q("I/O", sqlIo(ver))
    parts.append("\\echo '== (g) settings =='")
    q("server settings", sqlSettings(ver))
    q("site-only settings", sqlSiteEnv(ver))
    q("connections", sqlConnections(ver))
    q("memory", sqlMemory(ver))
    return "\n".join(parts) + "\n"


def _connect(dsn):
    if dsn:
        import psycopg2
        return psycopg2.connect(dsn, application_name="xcp-health"), None
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    os.environ.setdefault("XCP_DB_QUIET", "0")          # a report, not the pipeline
    os.environ.setdefault("XCP_DB_APP", "xcp-health")
    from database import getConn                        # noqa: E402
    cm = getConn()
    return cm.__enter__(), cm


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=SECTIONS, help="sections to run, e.g. a,b,d (default all)")
    ap.add_argument("--dsn", default=None, help="connect with this libpq DSN instead of scripts/database.py")
    ap.add_argument("--sql", action="store_true", help="print the report as SQL for psql and exit")
    ap.add_argument("--pg", type=int, default=18, help="server major version for --sql (default 18)")
    ap.add_argument("--min-mb", type=float, default=64, help="smallest relation listed (default 64 MB)")
    ap.add_argument("--rare", type=int, default=50, help="an index scanned at most this often is listed (default 50)")
    ap.add_argument("--repo", default=_ROOT, help="the checkout section d reads (default this one)")
    args = ap.parse_args(argv)

    if args.sql:
        sys.stdout.write(sqlScript(args.pg * 10000, protectedIndexes(args.repo), args.min_mb, args.rare))
        return 0
    want = [s for s in args.only.replace(",", "") if s in SECTIONS]
    conn, cm = _connect(args.dsn)
    try:
        rep = Report(conn, root=args.repo, min_mb=args.min_mb, rare=args.rare)
        print(f"db_health: PostgreSQL {rep.ver // 10000}, sections {''.join(want)}, read only")
        for s in want:
            getattr(rep, s)()
    finally:
        try:
            conn.autocommit = False
        except Exception:                                    # noqa: BLE001
            pass
        if cm is not None:
            cm.__exit__(None, None, None)
        else:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
