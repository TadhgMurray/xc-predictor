# Project: xc-predictor
# File:    scripts/triage_gps_suspects.py
# Purpose: READ-ONLY. 52k flagged ROWS is mostly one bad venue x thousands of
#          rows. Collapse to distinct (table, location_id), split by reason,
#          and dump one line per venue with its clue so we can judge/fix. Also
#          box-checks meets_tfrrs. Writes a per-VENUE csv; changes nothing.
# USAGE:  python scripts/triage_gps_suspects.py
# ============================================================================
import csv, os, sys
sys.path.insert(0, "scripts")
from database import getConn, initPool
from audit_gps_sanity import _flag                 # reuse the exact same test

_OUT = "gps_suspect_venues.csv"


# _distinctSuspects
# Purpose : for one table, roll the flagged rows up to distinct
#           (location_id, state, lat, lon, reason) with a row count, so 40k
#           rows become the ~handful of venues they actually are.
# Output  : list of dicts, and the table's (n_venue_suspects, n_row_suspects).
def _distinctSuspects(cur, table, name_expr):
    cur.execute(f"""
        SELECT location_id, state, gps_lat, gps_long, count(*) AS n_rows,
               max({name_expr}) AS clue
        FROM {table}
        WHERE gps_lat IS NOT NULL
        GROUP BY location_id, state, gps_lat, gps_long
    """)
    out, n_rows = [], 0
    for loc, state, lat, lon, rows, clue in cur.fetchall():
        reason = _flag(state, lat, lon)
        if reason:
            out.append({"table": table, "location_id": loc, "state": state,
                        "lat": lat, "lon": lon, "n_rows": rows,
                        "reason": reason, "clue": (clue or "")[:60]})
            n_rows += rows
    return out, n_rows


def main():
    initPool()
    # each table's best available "what is this venue" text for the dump
    name_col = {
        "meets":         "course_name",
        "meets_tf":      "meet_name",
        "meets_tf_meta": "venue_name",
        "meets_tfrrs":   "venue_name",
    }
    path = os.path.join("scripts", _OUT)
    all_suspects = []
    with getConn() as conn, conn.cursor() as cur:
        print("=== SUSPECTS BY DISTINCT VENUE (read-only) ===")
        for table, name_expr in name_col.items():
            venues, rows = _distinctSuspects(cur, table, name_expr)
            all_suspects += venues
            # split by reason for this table
            by = {}
            for v in venues:
                by[v["reason"]] = by.get(v["reason"], 0) + 1
            reason_str = ", ".join(f"{k}={n}" for k, n in sorted(by.items()))
            print(f"  {table:<13}: {len(venues):>4} bad venues "
                  f"({rows:,} rows)   [{reason_str or 'none'}]")
        conn.rollback()

    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["table", "location_id", "state",
                           "lat", "lon", "n_rows", "reason", "clue"])
        w.writeheader()
        for v in sorted(all_suspects, key=lambda x: -x["n_rows"]):
            w.writerow(v)

    print(f"\n  wrote {path}  ({len(all_suspects)} distinct bad venues, "
          f"biggest first)")
    print("  -> that's the real fix list. off-planet/null-island = re-null; "
          "out-of-state = wrong pin, re-null or hand-fix.")


if __name__ == "__main__":
    main()