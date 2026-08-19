# Project: xc-predictor
# File:    scripts/report_tf_deadends.py
# Purpose: READ-ONLY, ONE-TIME. The 247 meets_tf location_ids with no gps have
#          no venue name (only meet_name = the EVENT), no gps twin in the XC
#          tables, and no self-fill -- so nothing can geocode them automatically.
#          This dumps everything we DO know about each, grouped by location_id,
#          so you can hand-identify the ones whose meet names reveal a venue.
#          Writes a TSV for review; changes nothing in the DB.
#
# USAGE
#   python scripts/report_tf_deadends.py
#   python scripts/report_tf_deadends.py --out scripts
# ============================================================================

import argparse
import os
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool

_OUT = "tf_deadend_venues.tsv"


# ================================================================== #
# CHUNK 1 -- GATHER
# ================================================================== #

# _gather
# Purpose : for each null-gps TF location_id, collect the distinct meet names,
#           the state(s), the div count, and a date range -- everything that
#           might help a human recognize the venue. Grouped by location_id since
#           that is the venue identity (even without a name).
def _gather(cur):
    cur.execute("""
        SELECT location_id,
               count(*)                                   AS n_divs,
               count(DISTINCT meet_id)                    AS n_meets,
               array_agg(DISTINCT state)                  AS states,
               array_agg(DISTINCT meet_name)              AS meet_names,
               array_agg(DISTINCT is_indoor)              AS indoor_flags
        FROM meets_tf
        WHERE gps_lat IS NULL AND location_id IS NOT NULL
        GROUP BY location_id
        ORDER BY count(*) DESC
    """)
    return cur.fetchall()


# _cleanNames
# Purpose : meet_name lists are noisy (year-prefixed repeats of one event). Trim
#           to the few most informative distinct names so the report is readable.
def _cleanNames(names, limit=4):
    seen = [n for n in (names or []) if n and n.strip()]
    # de-dup case-insensitively, keep first occurrence
    out, lowered = [], set()
    for n in seen:
        key = n.strip().lower()
        if key not in lowered:
            lowered.add(key)
            out.append(n.strip())
        if len(out) >= limit:
            break
    return out


# ================================================================== #
# CHUNK 2 -- WRITE REPORT
# ================================================================== #

def _write(rows, out_dir):
    path = os.path.join(out_dir, _OUT)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# meets_tf DEAD-END venues (no venue name, no gps twin). "
                "Fill gps_lat/gps_long by hand where the meet names reveal a place.\n")
        f.write("# location_id\tn_divs\tn_meets\tstates\tsample_meet_names"
                "\tgps_lat\tgps_long\n")
        for loc, n_divs, n_meets, states, names, indoor in rows:
            st = ",".join(s for s in (states or []) if s) or "?"
            nm = " | ".join(_cleanNames(names))
            f.write(f"{loc}\t{n_divs}\t{n_meets}\t{st}\t{nm}\t\t\n")
    return path


# ================================================================== #
# CHUNK 3 -- DRIVER
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="One-time report of meets_tf dead-end venues. READ-ONLY.")
    ap.add_argument("--out", default="scripts")
    ap.add_argument("--show", type=int, default=1000,
                    help="how many to also print to the console")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '256MB'")
        rows = _gather(cur)
        conn.rollback()

    path = _write(rows, args.out)
    print(f"  {len(rows)} dead-end TF location_ids -> {path}")

    # console preview: the biggest ones first (most divisions = most worth fixing)
    print(f"\n  top {min(args.show, len(rows))} by division count "
          f"(these matter most if fixed):")
    print(f"    {'loc_id':>12} {'divs':>5} {'st':>6}  meet names")
    for loc, n_divs, n_meets, states, names, indoor in rows[:args.show]:
        st = ",".join(s for s in (states or []) if s) or "?"
        nm = " | ".join(_cleanNames(names, limit=2))
        print(f"    {loc:>12} {n_divs:>5} {st:>6}  {nm[:60]}")

    # honesty: how many are indoor (indoor TF has no meaningful outdoor gps/weather)
    indoor_only = sum(1 for r in rows
                      if r[5] and all(x is True for x in r[5] if x is not None))
    if indoor_only:
        print(f"\n  note: {indoor_only} of these appear INDOOR-only -- outdoor gps/"
              f"weather is meaningless for them, so leaving them null is arguably "
              f"correct regardless.")


if __name__ == "__main__":
    main()