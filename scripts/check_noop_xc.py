# Project: xc-predictor
# File:    scripts/check_noop_xc.py
# Purpose: READ-ONLY. Decide whether the 426 proposed XC repairs would change
#          anything at all, before spending 50 minutes finding out.
#
# 2026-07-15 FIX: the previous version hard-coded `r.distance` and died with
# "column r.distance does not exist". It GUESSED the schema. This version reads
# information_schema first; if it cannot find a distance column it PRINTS the
# candidates and stops rather than inventing one.
#
# THE TRAP IT CHECKS FOR
#   adjudicate's _isNoop diffs a proposal against _DISTANCE_OVERRIDES_XC. All
#   426 verdicts are STORED ("no override exists"), every key is NEW, so it
#   reports CHANGE=426.
#
#   But backfill_normalize.py:1096 says:
#       override = _DISTANCE_OVERRIDES_BY_SPORT[cfg.sport].get(key)  # beats stored dist
#   "beats stored dist" -- with NO override the backfill ALREADY falls back to
#   a stored distance. If that fallback equals the proposal, writing the
#   override is a no-op in the ruler -- and _isNoop cannot see it, because it
#   diffs against the overrides dict rather than the EFFECTIVE distance.
#
#   Same shape as the _DISTANCE_OVERRIDES_BY_SPORT orphan: the tool checks one
#   thing, the engine reads another.
#
# USAGE (~30 sec)
#   python scripts\check_noop_xc.py

import importlib.util
import os
import sys

sys.path.insert(0, "scripts")
from database import getConn, initPool

_SAME = 1.0          # metres; proposed vs actual within this = the same number


# ================================================================== #
# CHUNK 1 -- READ THE SCHEMA, DO NOT GUESS IT
# ================================================================== #

def _columnsOf(cur, table):
    """Purpose : the real column list. Everything else keys off this."""
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = %s AND table_schema = 'public'
        ORDER BY ordinal_position
    """, (table,))
    return cur.fetchall()


def _findDistanceCol(cols):
    """
    Purpose : which column holds the distance the ruler normalised with?
    Arguments: cols -- [(name, type), ...] from _columnsOf.
    Output  : the column name, or None.
    Note    : returns None rather than guessing wrong. main() then prints every
              candidate so a human names it. Guessing `distance` is what broke
              the last version.
    """
    names = [c for c, _t in cols]
    for want in ("distance", "distance_m", "dist", "race_distance",
                 "distance_meters", "event_distance", "meters"):
        if want in names:
            return want
    return None


# ================================================================== #
# CHUNK 2 -- THE PROPOSALS
# ================================================================== #

def _loadAdditions(path):
    """Purpose : the repairs adjudicate just wrote."""
    if not os.path.exists(path):
        sys.exit(f"{path} not found -- run adjudicate_regressions.py first")
    spec = importlib.util.spec_from_file_location("_adds", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod._DISTANCE_OVERRIDES_ADDITIONS)


# ================================================================== #
# CHUNK 3 -- WHAT THE RULER ACTUALLY USED
# ================================================================== #

def _rowDistances(cur, pairs, col):
    """
    Purpose : the distance on the ROWS. min and max so a division carrying
              mixed distances is visible instead of averaged away.
    Arguments: col -- the distance column, DISCOVERED not assumed.
    Output  : dict (meet, div) -> (min, max, n_rows).
    """
    values = ",".join(cur.mogrify("(%s,%s)", p).decode() for p in pairs)
    cur.execute(f"""
        SELECT r.meet_id, r.div_id, min(r.{col}), max(r.{col}), count(*)
        FROM results r
        JOIN (VALUES {values}) v(m, d) ON v.m = r.meet_id AND v.d = r.div_id
        WHERE r.{col} IS NOT NULL
        GROUP BY r.meet_id, r.div_id
    """)
    return {(m, d): (lo, hi, n) for m, d, lo, hi, n in cur.fetchall()}


# ================================================================== #
# CHUNK 4 -- THE VERDICT
# ================================================================== #

def _compare(adds, rows):
    """Purpose : split proposals into ones that move the ruler and ones that
    do not. Output : (movers, noops, missing)."""
    movers, noops, missing = [], [], []
    for key, proposed in sorted(adds.items()):
        if key not in rows or rows[key][0] is None:
            missing.append((key, proposed, None))
            continue
        lo, hi, _n = rows[key]
        if abs(lo - proposed) < _SAME and abs(hi - proposed) < _SAME:
            noops.append((key, proposed, lo))
        else:
            movers.append((key, proposed, lo))
    return movers, noops, missing


def _report(total, movers, noops, missing):
    print(f"\n  proposed repairs                     : {total:>6,}")
    print(f"  WOULD MOVE the ruler                 : {len(movers):>6,}")
    print(f"  NO-OP (row already at that distance) : {len(noops):>6,}")
    print(f"  no distance on rows                  : {len(missing):>6,}")

    if movers:
        print("\n  sample REAL changes (row -> proposed):")
        for (m, d), prop, actual in movers[:8]:
            print(f"    {m:>8} {d:>8}   {actual:>9.1f} -> {prop:>9.1f}")
    if noops:
        print("\n  sample NO-OPS (writing this changes nothing):")
        for (m, d), prop, actual in noops[:8]:
            print(f"    {m:>8} {d:>8}   {actual:>9.1f} == {prop:>9.1f}")

    print("\n" + "=" * 62)
    if total and len(movers) < 0.1 * total:
        print("  !! Almost nothing moves. The rows ALREADY carry the stored\n"
              "     distance and still flag ~-20%. So stored is WRONG for these\n"
              "     and the 4.4x physics band is too loose to notice.\n"
              "     DO NOT run the cycle. These need the event, not metadata.")
    else:
        print(f"  -> {len(movers):,} real changes. The cycle is worth 50 min.")
    print("=" * 62)


def main():
    adds = _loadAdditions(os.path.join("scripts",
                                       "adjudicated_overrides_xc.py"))
    print(f"loaded {len(adds):,} proposed repairs")
    if not adds:
        sys.exit("nothing proposed")

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        cols = _columnsOf(cur, "results")
        col = _findDistanceCol(cols)

        if not col:
            print("\n  !! no distance column on `results`. Every column:")
            for c, t in cols:
                print(f"      {c:<28} {t}")
            conn.rollback()
            sys.exit("\n  Tell me which column holds the distance the backfill "
                     "normalised with, and I'll wire it in.")

        print(f"  distance column on `results`: {col}")
        rows = _rowDistances(cur, list(adds), col)
        conn.rollback()                   # read-only, always

    print(f"  row distances found for {len(rows):,}/{len(adds):,}")
    movers, noops, missing = _compare(adds, rows)
    _report(len(adds), movers, noops, missing)


if __name__ == "__main__":
    main()