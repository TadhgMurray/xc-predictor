#!/usr/bin/env python3
"""diag_tfrrs.py - read-only health check for the TFRRS scraper.

Checks (two you asked for):
  1. SAVE-LANDING - of meets the queue CLAIMS are done (scraped=1), how many
     actually have rows in results_tf / results, and how many have a meets_tfrrs
     metadata row. The results gap is the "marked-done-but-empty" hole.
  2. COLUMN-QUALITY - per-field non-NULL coverage, shown overall AND over the
     newest rows by scraped_at (the slice the CURRENT code wrote).

Reading it with the re-scrape NOT yet launched:
  - meets_tfrrs is ~empty -> meta-landing and meet-columns read ~0 = EXPECTED.
  - "new-code" fields read LOW overall (old bulk never captured them) -> judge
    them on the RECENT column instead.

PERFORMANCE: every per-table check is ONE pass. Column coverage is a single
multi-count query (not one query per column); save-landing is a single LEFT JOIN
against the distinct meet-id set (no IN / NOT IN). The one unavoidable cost is
scanning the source='tfrrs' slice of results_tf - an index on results_tf(source)
or (source, scraped_at) makes even that an index scan. (That index is a WRITE on
a big table, so run it yourself if it isn't already present; this script stays
read-only.)

READ-ONLY: never commits, inserts, or updates.

Assumptions (one-line fixes up top; misnamed columns print [skipped] not crash):
  source literal 'tfrrs'; sport 'TF'/'XC'; scraped=1 == done; results tables have
  source + scraped_at; meets_tfrrs keyed (meet_id, sport) with is_championship,
  teams_json, director; scripts/database.py exposes pooled getConn().
"""

import sys
sys.path.insert(0, "scripts")
from database import getConn

# --- config: every dependent literal in one place --------------------------
SOURCE_TFRRS = "tfrrs"
SPORT_TF = "TF"
SPORT_XC = "XC"
DONE = 1
SCRAPED_LEGEND = {0: "unscraped", 1: "done", 2: "failed",
                  3: "in-progress", 4: "skipped"}
RESULTS_TABLE = {SPORT_TF: "results_tf", SPORT_XC: "results"}
VALIDATED_MEETS = {SPORT_TF: 92238, SPORT_XC: 27301}
RECENT_N = 50000

# column specs: (column, label, new_code_only). new_code_only -> low overall
# coverage is expected today; judge on the recent slice.
RESULT_COLUMNS_TF = [
    ("time_seconds", "time (running)", False),
    ("mark",         "mark (field)",   False),
    ("wind",         "wind",           False),
    ("place",        "place",          True),
    ("score",        "score (SC)",     True),
    ("athlete_name", "athlete_name",   True),
    ("native_id",    "native_id",      True),
]
RESULT_COLUMNS_XC = [
    ("splits_json",  "splits",         False),
    ("place",        "place",          True),
    ("athlete_name", "athlete_name",   True),
    ("native_id",    "native_id",      True),
]
MEET_COLUMNS = [
    ("is_championship", "is_championship", True),
    ("teams_json",      "team scores",     True),
    ("director",        "director (raw)",  True),
]


# --- DB access: the only two functions that touch the database -------------

def _query(sql, params=()):
    """Run a read-only query, return all rows as a list of tuples.

    Purpose: single DB choke-point so every query borrows/returns a pooled
      connection identically (the ctx-mgr does rollback-on-error + putconn).
    Arguments:
      sql (str): a SELECT, with %s placeholders for any values. Read-only.
      params (tuple): values for the %s placeholders, in order; the driver
        quotes them safely (never hand-format values into SQL). Empty tuple
        when there are no placeholders.
    Output:
      list[tuple]: one tuple per row in SELECT-column order; [] if no match.
    """
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _scalar(sql, params=()):
    """Run a one-row, one-column query and return that single value.

    Arguments: sql (str) returning a single cell; params (tuple) as _query.
    Output: the cell's value (usually an int from count(*)); None if the
      aggregate is NULL or no row came back.
    """
    rows = _query(sql, params)
    return rows[0][0] if rows else None


def _pct(part, whole):
    """Safe percentage part/whole*100, or 0.0 when whole is 0/None.

    Arguments: part (num) numerator; whole (num) denominator.
    Output: float percentage; 0.0 on an empty denominator (no divide-by-zero).
    """
    return (part / whole * 100.0) if whole else 0.0


# --- save-landing -----------------------------------------------------------

def _saveLanding(sport, source):
    """One-pass save-landing: (done, have_results, have_meta) for a sport.

    Purpose: test the queue's "done" CLAIM against reality in a SINGLE scan of
      the results slice. Replaces the old IN + NOT IN pair (two scans) with one
      LEFT JOIN to the DISTINCT tfrrs meet-id set; the meta check is a second
      small join to meets_tfrrs.
    Arguments:
      sport (str): 'TF'/'XC' - selects the results table (RESULTS_TABLE) and is
        pinned so the two sports never mix.
      source (str): 'tfrrs' - filtered on BOTH queue and results, because the
        results tables also hold anet rows that share the meet_id integer
        (disjoint pools); without it we'd credit tfrrs for anet rows.
    Output:
      tuple(done:int, have_results:int, have_meta:int). done = scraped=1 rows;
        have_results = those with >=1 results row; have_meta = those with a
        meets_tfrrs row. (done - have_results) is the real hole; have_meta ~0 is
        expected until the re-scrape runs.
    """
    table = RESULTS_TABLE[sport]       # from our dict, not user input -> safe
    sql = f"""
        SELECT
          count(*) FILTER (WHERE q.scraped = %s)                           AS done,
          count(*) FILTER (WHERE q.scraped = %s AND r.meet_id IS NOT NULL) AS have_results,
          count(*) FILTER (WHERE q.scraped = %s AND m.meet_id IS NOT NULL) AS have_meta
        FROM meet_queue q
        LEFT JOIN (SELECT DISTINCT meet_id FROM {table} WHERE source = %s) r
               ON r.meet_id = q.meet_id
        LEFT JOIN (SELECT DISTINCT meet_id FROM meets_tfrrs WHERE sport = %s) m
               ON m.meet_id = q.meet_id
        WHERE q.sport = %s AND q.source = %s
    """
    return _query(sql, (DONE, DONE, DONE, source, sport, sport, source))[0]


def _missingSample(sport, source, limit=20):
    """A few 'done' meet_ids with NO results - for hand-inspection.

    Purpose: turn the hole count into concrete ids to open on tfrrs.org and
      decide empty-upstream (correctly-absent) vs save bug (broken-absent).
      Anti-join via LEFT JOIN ... IS NULL (NOT the slow NOT IN). Only call when
      a hole exists.
    Arguments: sport, source (str) as above; limit (int) sample size.
    Output: list[int] of scraped=1 meet_ids absent from the results table.
    """
    table = RESULTS_TABLE[sport]
    sql = f"""
        SELECT q.meet_id
        FROM meet_queue q
        LEFT JOIN (SELECT DISTINCT meet_id FROM {table} WHERE source = %s) r
               ON r.meet_id = q.meet_id
        WHERE q.sport = %s AND q.source = %s AND q.scraped = %s AND r.meet_id IS NULL
        LIMIT %s
    """
    return [row[0] for row in _query(sql, (source, sport, source, DONE, limit))]


def _queueCountsByCode(sport, source):
    """meet_queue row counts by scraped code for a (sport, source).

    Arguments: sport ('TF'/'XC'), source ('tfrrs') - both pinned (queue holds
      all sports/sources; disjoint pools).
    Output: dict[int,int] code(0..4) -> count; absent code means 0.
    """
    sql = """
        SELECT scraped, count(*) FROM meet_queue
        WHERE sport = %s AND source = %s GROUP BY scraped ORDER BY scraped
    """
    return {code: n for code, n in _query(sql, (sport, source))}


# --- column quality ---------------------------------------------------------

def _coverageRow(table, columns, source, recent_n=None):
    """Non-NULL counts for MANY columns in ONE pass.

    Purpose: the speed fix - one multi-count query instead of one query per
      column. Overall (recent_n=None) is a single WHERE-source scan; the recent
      slice is a CTE that takes the newest recent_n rows once, then counts.
    Arguments:
      table (str): results table (our constant, not user input).
      columns (list): the spec tuples (column, label, new_only); only the
        column name is interpolated, and it comes from our own specs (safe).
      source (str): 'tfrrs' - excludes anet rows from the denominator.
      recent_n (int|None): None = all tfrrs rows; else the newest recent_n by
        scraped_at (the current-code slice).
    Output:
      tuple(total:int, pcts:dict). total = rows in the slice. pcts maps column
        -> (pct_non_null, label, new_only). pct is 0.0 when total is 0.
    """
    counts = ", ".join(f"count({c})" for c, _l, _n in columns)   # safe: own specs
    if recent_n is None:
        sql = f"SELECT count(*), {counts} FROM {table} WHERE source = %s"
    else:
        sql = f"""
            WITH recent AS (
                SELECT * FROM {table} WHERE source = %s
                ORDER BY scraped_at DESC NULLS LAST LIMIT {int(recent_n)}
            )
            SELECT count(*), {counts} FROM recent
        """
    row = _query(sql, (source,))[0]
    total = row[0]
    pcts = {c: (_pct(row[i], total), label, new_only)
            for i, (c, label, new_only) in enumerate(columns, start=1)}
    return total, pcts


def _meetColumnCoverage(sport, column):
    """Non-NULL fraction of a meets_tfrrs column for one sport (table is small).

    Arguments: sport ('TF'/'XC'); column (str) from MEET_COLUMNS.
    Output: tuple(non_null:int, total:int, pct:float).
    """
    scope = f"SELECT {column} AS c FROM meets_tfrrs WHERE sport = %s"
    non_null, total = _query(f"SELECT count(c), count(*) FROM ({scope}) s", (sport,))[0]
    return non_null, total, _pct(non_null, total)


# --- printing: one tiny function per report section ------------------------

def _printQueueState(sport, source):
    """Print meet_queue breakdown by scraped code."""
    counts = _queueCountsByCode(sport, source)
    print(f"  queue [{sport}/{source}]:")
    for code in range(5):
        print(f"    {code} {SCRAPED_LEGEND[code]:<12} {counts.get(code, 0):>12,}")


def _printSaveLanding(sport, source):
    """Print the save-landing verdict (results + meta backing for 'done')."""
    done, have_results, have_meta = _saveLanding(sport, source)
    hole = done - have_results
    print(f"  save-landing [{sport}/{source}]: done = {done:,}")
    print(f"    have results : {have_results:>12,}  ({_pct(have_results, done):5.1f}%)")
    print(f"    MISSING res  : {hole:>12,}  ({_pct(hole, done):5.1f}%)  <- the real hole")
    print(f"    have meta    : {have_meta:>12,}  ({_pct(have_meta, done):5.1f}%)")
    if have_meta == 0:
        print("       ^ meets_tfrrs empty = EXPECTED (re-scrape not launched yet).")
    if hole > 0:
        print(f"    sample missing meet_ids (eyeball on tfrrs.org): "
              f"{_missingSample(sport, source)}")


def _printValidatedMeet(sport, source):
    """Re-probe the audit-validated meet for this sport (new-code proof)."""
    meet_id = VALIDATED_MEETS[sport]
    table = RESULTS_TABLE[sport]
    n = _scalar(f"SELECT count(*) FROM {table} WHERE meet_id=%s AND source=%s",
                (meet_id, source))
    print(f"  validated meet {sport} {meet_id}: {n:,} result rows (expect > 0)")


def _printColumnQuality(table, columns, source):
    """Print overall-vs-recent coverage. Fast multi-count; falls back per-column
    only if that query fails (e.g. a misnamed column), so one bad name doesn't
    blank the section."""
    try:
        total_all, cov_all = _coverageRow(table, columns, source, None)
        total_rec, cov_rec = _coverageRow(table, columns, source, RECENT_N)
    except Exception as e:
        print(f"  column-quality [{table}/{source}]: fast path failed "
              f"({type(e).__name__}: {e}); per-column fallback:")
        _printColumnQualitySlow(table, columns, source)
        return
    print(f"  column-quality [{table}/{source}]   "
          f"overall(n={total_all:,}) | recent(n={total_rec:,})")
    for c, label, new_only in columns:
        tag = "  (new-code -> judge on recent)" if new_only else ""
        print(f"    {label:<16} {cov_all[c][0]:6.1f}% | {cov_rec[c][0]:6.1f}%{tag}")


def _printColumnQualitySlow(table, columns, source):
    """Resilient per-column fallback (one query per column, each wrapped)."""
    for c, label, new_only in columns:
        try:
            total, pcts = _coverageRow(table, [(c, label, new_only)], source, None)
            print(f"    {label:<16} {pcts[c][0]:6.1f}%")
        except Exception as e:
            print(f"    {label:<16}  [skipped: {type(e).__name__}: {e}]")


def _printMeetColumns(sport):
    """Print meets_tfrrs column coverage (expected ~empty pre-re-scrape)."""
    print(f"  meet-columns [meets_tfrrs/{sport}]:")
    for column, label, _new in MEET_COLUMNS:
        try:
            non_null, total, pct = _meetColumnCoverage(sport, column)
            print(f"    {label:<16} {pct:6.1f}%   ({non_null:,}/{total:,})")
        except Exception as e:
            print(f"    {label:<16}  [skipped: {type(e).__name__}: {e}]")


# --- entry point ------------------------------------------------------------

def main():
    """Run all checks; read-only, safe to re-run."""
    print("=== TFRRS scraper diagnostic (read-only) ===")
    for sport in (SPORT_TF, SPORT_XC):
        print(f"\n--- {sport} ---")
        _printQueueState(sport, SOURCE_TFRRS)
        _printSaveLanding(sport, SOURCE_TFRRS)
        _printValidatedMeet(sport, SOURCE_TFRRS)
    print("\n--- column quality: result rows ---")
    _printColumnQuality("results_tf", RESULT_COLUMNS_TF, SOURCE_TFRRS)
    _printColumnQuality("results", RESULT_COLUMNS_XC, SOURCE_TFRRS)
    print("\n--- column quality: meet rows (meets_tfrrs) ---")
    for sport in (SPORT_TF, SPORT_XC):
        _printMeetColumns(sport)
    print("\n=== done ===")


if __name__ == "__main__":
    main()