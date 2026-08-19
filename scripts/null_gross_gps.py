# Project: xc-predictor
# File:    scripts/null_gross_gps.py
# Purpose: Null the genuinely-wrong coords found by audit_gps_buffered.py
#          (centroid junk, corrupt coords, wrong-state pins). Reads
#          gps_gross_errors.csv and nulls ONLY those exact rows -- matched by
#          location_id (when present) or state, AND the exact coordinate within
#          a tiny tolerance, so nothing else is touched. A null is honest; a
#          wrong coord poisons weather. Dry-run by default; --apply commits.
# USAGE:
#   python scripts/null_gross_gps.py            # dry run: rows that would null
#   python scripts/null_gross_gps.py --apply    # commit the nulls
# ============================================================================
import argparse, csv, os, sys
sys.path.insert(0, "scripts")
from database import getConn, initPool

_CSV = os.path.join("scripts", "gps_gross_errors.csv")
_TOL = 0.0005                                   # ~55 m coordinate match tolerance


def _rows():
    with open(_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            yield (r["table"], r["location_id"].strip(), r["state"],
                   float(r["lat"]), float(r["lon"]))


def _nullOne(cur, table, loc, state, lat, lon, do_write):
    """Null one bad coord. Returns rows matched. Key = location_id if present,
       else state; always AND'd with the exact coordinate (tolerance)."""
    coord = ("gps_lat BETWEEN %s AND %s AND gps_long BETWEEN %s AND %s",
             (lat-_TOL, lat+_TOL, lon-_TOL, lon+_TOL))
    if loc:
        where = "location_id = %s AND " + coord[0]
        args = (int(loc),) + coord[1]
    else:
        where = "state = %s AND " + coord[0]
        args = (state,) + coord[1]
    cur.execute(f"SELECT count(*) FROM {table} WHERE {where}", args)
    n = cur.fetchone()[0]
    if do_write and n:
        cur.execute(f"UPDATE {table} SET gps_lat=NULL, gps_long=NULL WHERE {where}", args)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(_CSV):
        sys.exit(f"  {_CSV} not found -- run audit_gps_buffered.py first")
    initPool()

    per_table, total = {}, 0
    with getConn() as conn, conn.cursor() as cur:
        for table, loc, state, lat, lon in _rows():
            n = _nullOne(cur, table, loc, state, lat, lon, args.apply)
            per_table[table] = per_table.get(table, 0) + n
            total += n
        if args.apply:
            conn.commit()
        else:
            conn.rollback()

    verb = "nulled" if args.apply else "would null"
    for t, n in sorted(per_table.items()):
        print(f"  {t:<13}: {verb} {n:,} rows")
    print(f"  TOTAL {verb}: {total:,} rows across 71 bad venues")
    if not args.apply:
        print("  [dry-run] add --apply to commit")
    else:
        print("  done -- re-run audit_gps_buffered.py; GROSS should be 0")


if __name__ == "__main__":
    main()