#!/usr/bin/env python
"""
scripts/land_tfrrs_geometry_in_meets_tf.py
    Make tfrrs track-geometry VISIBLE to the event-keyed join by building
    meets_tf rows for tfrrs from the propagation stamps.

    DRY-RUN BY DEFAULT.  Apply:
        python scripts/land_tfrrs_geometry_in_meets_tf.py --apply

================================================================================
THE IDEA (before mechanics)
================================================================================
Propagation stamped 21,140 tfrrs meets into tfrrs_meet_geometry (MEET-level,
keyed (meet_id, sport)). But every consumer — the era loader, geometry_db,
your fuck2 probe — joins results_tf against MEETS_TF (EVENT-level, keyed
(div_id, meet_id, event_id, source)). meets_tf has ZERO tfrrs rows, so the
stamps are invisible. This script fans the meet-level stamps DOWN to the
event level and inserts them into meets_tf, so the join finally matches.

Geometry (length, banking, indoor) is a VENUE constant — identical for the
200m and the mile at the same meet — so broadcasting one meet's stamp across
all its event rows is correct. (Broadcasting a RESULT that way would be a bug,
because times differ per event. That asymmetry is the whole license for this.)

--------------------------------------------------------------------------------
THREE LANDMINES, THREE DEFENSES (all measured in the dry-run before any write)
--------------------------------------------------------------------------------
  1. meets_tf PK is (div_id, meet_id, event_id) — SOURCE NOT INCLUDED. A tfrrs
     triple could numerically collide with an existing anet row and mean
     something else (meet_id spaces differ across sources). DEFENSE:
     ON CONFLICT DO NOTHING — never overwrite an existing row; skip + count it.
  2. A tfrrs result row with NULL div_id/event_id can never get a PK partner.
     DEFENSE: excluded by the WHERE clause; counted so coverage is honest.
  3. If the stamps' meet_ids don't appear in results_tf, we'd insert orphan
     rows nothing joins to. DEFENSE: the OVERLAP count is the go/stop gate.

--------------------------------------------------------------------------------
IDEMPOTENCE / NON-DESTRUCTIVENESS
--------------------------------------------------------------------------------
Our rows are the only ones with source='tfrrs' (source is a COLUMN even though
it is not in the PK), so the write is: DELETE WHERE source='tfrrs' (0 the first
time) then INSERT. Re-running is always safe and never touches anet rows.
================================================================================
"""

import sys
import argparse

# The two discriminators this whole script pivots on (house rule: one place).
RESULTS_SOURCE = "tfrrs"   # results_tf.source is lowercase
STAMP_SPORT    = "TF"      # tfrrs_meet_geometry.sport is uppercase


# ==============================================================================
# DB ACCESS  —  reuse the project's own connector; coerce CM-or-raw to a conn
# ==============================================================================

def openConnection():
    """
    Purpose: a live connection via the project's getConn (inherits its config).
    Output : the connection object (raw or coerced from a context manager).
    """
    sys.path.insert(0, "scripts")            # let `import database` resolve
    from database import getConn
    return _coerceConn(getConn())


def _coerceConn(obj):
    """
    getConn() may hand back a raw connection OR a context manager. We only need
    something with .cursor(); if it lacks one but is a CM, enter it and keep it.
    """
    if hasattr(obj, "cursor"):
        return obj
    if hasattr(obj, "__enter__"):
        return obj.__enter__()
    return obj


def _scalar(conn, sql, params=None):
    """
    Run a SELECT returning ONE number and return it as int.
    'params or ()' guarantees execute() never receives None.
    """
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()[0]


# ==============================================================================
# THE ONE SHARED PREDICATE  —  the dry-run census and the real INSERT MUST use
#                              the same WHERE, or the dry count is a lie.
# ==============================================================================
# This fragment defines "an insertable tfrrs event row": a results_tf tfrrs row
# whose meet has a stamp AND whose PK columns are both non-null. Every census
# below and the INSERT itself embed exactly this fragment.

_INSERTABLE_FROM = """
    FROM results_tf r
    JOIN tfrrs_meet_geometry g
      ON g.meet_id = r.meet_id
     AND g.sport   = %(sport)s
    WHERE r.source   = %(src)s
      AND r.div_id   IS NOT NULL
      AND r.event_id IS NOT NULL
"""

# Params reused by every query that embeds the fragment.
def _predicateParams():
    return {"sport": STAMP_SPORT, "src": RESULTS_SOURCE}


# ==============================================================================
# CENSUS HELPERS  —  each answers ONE question; each is a few lines. (No long
#                    query blocks; the report loop calls them via _measure.)
# ==============================================================================

def _cStamps(conn):
    """Distinct meet_ids carrying a stamp — sanity vs the 21,140 we expect."""
    return _scalar(conn,
        "SELECT count(DISTINCT meet_id) FROM tfrrs_meet_geometry WHERE sport=%s",
        (STAMP_SPORT,))


def _cResultMeets(conn):
    """Distinct tfrrs meet_ids that HAVE results — the universe needing geometry."""
    return _scalar(conn,
        "SELECT count(DISTINCT meet_id) FROM results_tf WHERE source=%s",
        (RESULTS_SOURCE,))


def _cOverlapMeets(conn):
    """
    THE GO/STOP GATE: distinct meet_ids present in BOTH the stamps and
    results_tf. ~0 here means the stamps are keyed on an id space that never
    appears in results — inserting would just make orphans. Stop if so.
    """
    return _scalar(conn, """
        SELECT count(DISTINCT r.meet_id)
        FROM results_tf r
        JOIN tfrrs_meet_geometry g
          ON g.meet_id = r.meet_id AND g.sport = %s
        WHERE r.source = %s
    """, (STAMP_SPORT, RESULTS_SOURCE))


def _cCoveredRows(conn):
    """
    RESULT ROWS (count rows, not meets — house rule) whose meet has a stamp.
    This is what coverage actually means downstream: how many times a corrected
    result will flow into the engine/model.
    """
    return _scalar(conn, """
        SELECT count(*)
        FROM results_tf r
        JOIN tfrrs_meet_geometry g
          ON g.meet_id = r.meet_id AND g.sport = %s
        WHERE r.source = %s
    """, (STAMP_SPORT, RESULTS_SOURCE))


def _cNullKeyRows(conn):
    """
    tfrrs result rows with NULL div_id/event_id: uncoverable by meets_tf (no PK
    partner possible). A residual class, reported honestly, not a bug.
    """
    return _scalar(conn, """
        SELECT count(*)
        FROM results_tf
        WHERE source = %s
          AND (div_id IS NULL OR event_id IS NULL)
    """, (RESULTS_SOURCE,))


def _cInsertableTriples(conn):
    """
    The EXACT number of meets_tf rows the INSERT will build: distinct
    (div_id, meet_id, event_id) under the shared predicate. Same WHERE as the
    write, so this dry count equals the write's intent.
    """
    return _scalar(conn,
        "SELECT count(*) FROM (SELECT DISTINCT r.div_id, r.meet_id, r.event_id "
        + _INSERTABLE_FROM + ") t", _predicateParams())


def _cCollisions(conn):
    """
    Insertable triples that ALREADY exist in meets_tf (as anet rows) — cross
    -source numeric PK collisions. ON CONFLICT DO NOTHING will SKIP these, so
    this is the count of tfrrs rows we will silently NOT create. Small = fine.
    """
    return _scalar(conn,
        "SELECT count(*) FROM ("
        "  SELECT DISTINCT r.div_id, r.meet_id, r.event_id " + _INSERTABLE_FROM + ") t "
        "JOIN meets_tf m "
        "  ON m.div_id=t.div_id AND m.meet_id=t.meet_id AND m.event_id=t.event_id",
        _predicateParams())


def _cMetaTfrrs(conn):
    """
    Does meets_tf_meta already hold tfrrs rows? Expected 0 (schema says anet
    only). If non-zero, the target-table picture changes — flag it.
    """
    return _scalar(conn,
        "SELECT count(*) FROM meets_tf_meta WHERE source=%s", (RESULTS_SOURCE,))


# ==============================================================================
# MEASUREMENT DRIVER  —  run one census safely; a failure is DATA, not a crash
# ==============================================================================

def _measure(conn, label, note, fn):
    """
    Purpose : call one census helper, catch any failure, return a uniform row.
    Output  : dict {label, note, value|None, err|None} for the report loop.
    """
    try:
        return {"label": label, "note": note, "value": fn(conn), "err": None}
    except Exception as e:                    # e.g. tfrrs_meet_geometry absent
        return {"label": label, "note": note, "value": None, "err": str(e)}


def gatherCensus(conn):
    """Run every census in a fixed, readable order. Returns a list of rows."""
    plan = [
        ("stamps: distinct meet_ids",      "expect ~21,140",              _cStamps),
        ("tfrrs meets with results",       "the universe needing geom",   _cResultMeets),
        ("OVERLAP meets (stamp & result)", "GO/STOP gate; ~0 = orphans",  _cOverlapMeets),
        ("result ROWS covered by a stamp", "downstream coverage (rows)",  _cCoveredRows),
        ("tfrrs rows with NULL div/event", "uncoverable residual",        _cNullKeyRows),
        ("meets_tf rows to INSERT",        "= write size (same predicate)",_cInsertableTriples),
        ("...of which collide with anet",  "skipped by DO NOTHING",       _cCollisions),
        ("meets_tf_meta tfrrs rows",       "expect 0 (anet-only)",        _cMetaTfrrs),
    ]
    return [_measure(conn, lbl, note, fn) for lbl, note, fn in plan]


# ==============================================================================
# VERDICT  —  pure function of the counts: is the write safe, and how big?
# ==============================================================================

def _valueOf(census, needle):
    """Fetch one census value by a substring of its label (None if missing/err)."""
    for row in census:
        if needle in row["label"]:
            return row["value"]
    return None


def decideVerdict(census):
    """
    Purpose : turn the census into GO / STOP / WARN with reasons.
    Argument: census — gatherCensus output.
    Output  : (verdict_str, [reason_str, ...]).
    """
    overlap    = _valueOf(census, "OVERLAP")
    insertable = _valueOf(census, "to INSERT")
    collisions = _valueOf(census, "collide")

    reasons = []
    if overlap is None or insertable is None:
        return "STOP", ["a required count failed to run — see ERR lines above"]
    if overlap == 0:
        return "STOP", ["overlap is 0 — stamps target an id space absent from results_tf"]
    if insertable == 0:
        return "STOP", ["nothing insertable — no stamped tfrrs rows with non-null keys"]

    reasons.append(f"will build {insertable:,} meets_tf rows for tfrrs")
    if collisions:
        reasons.append(f"{collisions:,} collide with existing anet rows -> skipped (anet untouched)")
    return "GO", reasons


# ==============================================================================
# REPORT
# ==============================================================================

def _emitCensus(census):
    """Evidence-first: label, number (or ERR), then the note."""
    print("== census (same predicate as the write) ==")
    for row in census:
        shown = "ERR" if row["value"] is None else f"{row['value']:,}"
        print(f"  {row['label']:<34} {shown:>14}   <- {row['note']}")
        if row["err"]:
            print(f"      err: {row['err']}")


def _emitVerdict(verdict, reasons):
    print("\n== verdict ==")
    print(f"  {verdict}")
    for r in reasons:
        print(f"    - {r}")


# ==============================================================================
# WRITE PATH  —  DELETE(scoped by source) + INSERT(...ON CONFLICT DO NOTHING)
#                + read-back. Rows, not intentions, printed at every step.
# ==============================================================================

def applyInsert(conn):
    """
    Purpose: create the tfrrs meets_tf rows. Non-destructive to anet by two
             independent guarantees: DELETE is scoped to source='tfrrs', and
             INSERT uses ON CONFLICT DO NOTHING so no existing row is overwritten.
    """
    params = _predicateParams()
    with conn.cursor() as cur:
        # 1) Scope-delete our own prior rows so re-runs are clean (0 first time).
        cur.execute("DELETE FROM meets_tf WHERE source=%s", (RESULTS_SOURCE,))
        print(f"  deleted {cur.rowcount:,} prior tfrrs rows")

        # 2) Build event rows. GROUP BY makes id_system deterministic (min) and
        #    collapses the many result rows per event down to one meets_tf row.
        cur.execute("""
            INSERT INTO meets_tf
                (div_id, meet_id, event_id, source, id_system,
                 track_type, track_length, is_indoor, location_id)
            SELECT r.div_id, r.meet_id, r.event_id,
                   %(src)s, min(r.id_system),
                   g.track_type, g.track_length, g.is_indoor, g.location_id
        """ + _INSERTABLE_FROM + """
            GROUP BY r.div_id, r.meet_id, r.event_id,
                     g.track_type, g.track_length, g.is_indoor, g.location_id
            ON CONFLICT (div_id, meet_id, event_id) DO NOTHING
        """, params)
        print(f"  inserted (rowcount, may under-report on batched plans): {cur.rowcount:,}")

        conn.commit()

        # 3) Read-back is the truth (rowcount can lie on some plans).
        n = _scalar(conn, "SELECT count(*) FROM meets_tf WHERE source=%s",
                    (RESULTS_SOURCE,))
        print(f"  read-back: {n:,} tfrrs rows now in meets_tf")


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Build meets_tf rows for tfrrs from the geometry stamps.")
    ap.add_argument("--apply", action="store_true",
                    help="write (default: dry-run census only)")
    args = ap.parse_args()

    conn = openConnection()
    print("=" * 74)
    print("land tfrrs geometry into meets_tf   (dry-run by default)")
    print("=" * 74)

    census = gatherCensus(conn)
    _emitCensus(census)

    verdict, reasons = decideVerdict(census)
    _emitVerdict(verdict, reasons)

    if not args.apply:
        print("\n(dry run — nothing written; re-run with --apply once the "
              "verdict is GO and collisions look tolerable)")
        return

    if verdict != "GO":
        print("\nrefusing to --apply: verdict is not GO. Fix the cause above first.")
        return

    print("\n== applying ==")
    applyInsert(conn)
    print("\ndone — re-run scripts/fuck2.py; check B's tfrrs line should leave 0%.")


if __name__ == "__main__":
    main()