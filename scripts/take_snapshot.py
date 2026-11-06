#!/usr/bin/env python3
"""
take_snapshot.py -- freeze this season's ratings and every course difficulty
as a dated snapshot, downloadable at /data (racecast/data_snapshots.py).

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/take_snapshot.py --label "2026 cross country, end of season"
    $P scripts/take_snapshot.py --label "Week of Nov 9" --sport XC --year 2026
    $P scripts/take_snapshot.py --list
    $P scripts/take_snapshot.py --delete 3

Take one after a full pipeline run, so the copy holds the week's solved
ratings. A snapshot never changes once taken.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label")
    ap.add_argument("--sport", choices=("XC", "TF"), default="XC")
    ap.add_argument("--year", type=int, help="athlete_season.year (default: the newest)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--delete", type=int, metavar="SNAP_ID")
    a = ap.parse_args(argv)
    from database import getConn
    import data_snapshots as D
    with getConn() as conn, conn.cursor() as cur:
        D.ensure(cur)
        if a.list:
            cur.execute("SELECT snap_id, label, sport, year, taken_at, n_athletes, n_courses "
                        "FROM data_snapshot ORDER BY snap_id")
            for r in cur.fetchall():
                print("  " + "  ".join(str(x) for x in r))
            conn.rollback()
            return 0
        if a.delete:
            for t in ("data_snapshot_athlete", "data_snapshot_course", "data_snapshot"):
                cur.execute(f"DELETE FROM {t} WHERE snap_id = %s", (a.delete,))
            conn.commit()
            print(f"[snapshot] deleted {a.delete}")
            return 0
        if not a.label:
            ap.error("--label is required to take a snapshot")
        year = a.year
        if year is None:
            cur.execute("SELECT max(year) FROM athlete_season WHERE sport = %s", (a.sport,))
            year = cur.fetchone()[0]
        snap, n_a, n_c = D.take(cur, a.label, a.sport, int(year))
        conn.commit()
        print(f"[snapshot] {snap}: '{a.label}', {a.sport} {year}: {n_a:,} athletes, "
              f"{n_c:,} courses -- /data/{snap}/athletes.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
