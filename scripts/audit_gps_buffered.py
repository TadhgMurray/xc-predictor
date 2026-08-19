# Project: xc-predictor
# File:    scripts/audit_gps_buffered.py
# Purpose: READ-ONLY. The tight-box audit over-reports: it flags correct venues
#          within the 44km border band that refix/recover deliberately kept.
#          This measures with the SAME buffered box, per DISTINCT venue, and
#          splits by distance-outside so we see what's truly wrong:
#            border  (<= ~44km outside tight box) -> correct, kept on purpose
#            GROSS   (outside the buffered box)   -> actually wrong, the fix list
#          Dumps only the GROSS ones. Changes nothing.
# USAGE:  python scripts/audit_gps_buffered.py
# ============================================================================
import csv, math, os, sys
sys.path.insert(0, "scripts")
from database import getConn, initPool
from audit_gps_sanity import _BBOX

_MARGIN = 0.4                                   # same buffer the fix used
_TABLES = ("meets", "meets_tf", "meets_tf_meta", "meets_tfrrs")


def _degOutside(state, lat, lon):
    """Degrees the point sits outside its state box (0 if inside). None if
       state isn't boxed (overseas)."""
    b = _BBOX.get((state or "").strip().upper())
    if not b or lat is None:
        return None
    a, c, d, e = b
    dlat = max(a - lat, lat - c, 0.0)
    dlon = max(d - lon, lon - e, 0.0)
    return math.hypot(dlat, dlon)


def _audit(cur, table, writer):
    cur.execute(f"""SELECT location_id, state, gps_lat, gps_long
                    FROM {table} WHERE gps_lat IS NOT NULL
                    GROUP BY location_id, state, gps_lat, gps_long""")
    border = gross = 0
    for loc, state, lat, lon in cur.fetchall():
        d = _degOutside(state, lat, lon)
        if d is None or d == 0:
            continue
        if d <= _MARGIN:
            border += 1                          # correct, inside buffered box
        else:
            gross += 1                           # outside buffer -> really wrong
            writer.writerow([table, loc, state, lat, lon, round(d*111)])
    print(f"  {table:<13}: {border} border venues (OK), {gross} GROSS (wrong)")
    return gross


def main():
    initPool()
    path = os.path.join("scripts", "gps_gross_errors.csv")
    total = 0
    with getConn() as conn, conn.cursor() as cur, \
         open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table", "location_id", "state", "lat", "lon", "km_outside"])
        print("=== BUFFERED AUDIT (distinct venues) ===")
        for t in _TABLES:
            total += _audit(cur, t, w)
            conn.rollback()
    print(f"\n  GROSS (genuinely wrong) venues total: {total}")
    print(f"  wrote {path}")
    if total == 0:
        print("  => every flagged coord is a correct border venue. GPS is clean.")


if __name__ == "__main__":
    main()