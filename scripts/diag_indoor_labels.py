#!/usr/bin/env python3
"""
diag_indoor_labels.py -- how wrong is is_indoor, before we assert a level on it?

    python scripts/diag_indoor_labels.py
    python scripts/diag_indoor_labels.py --tells        # the slower, better test

★ WHY, BEFORE THE GAUGE CHANGE (owner, 2026-09-18, on pinning the indoor
  level: "Sure, but wouldn't difficulties still just get messed up?").

  A fair question, and this is the part of it that could make pinning actively
  harmful. Pinning the indoor group's mean to an asserted level corrects the
  SHARED component and leaves the identified per-oval deviations alone -- those
  come from athletes racing several ovals in one winter, which never involves
  the form curve. But it assumes the GROUP is the group.

⚠⚠ AND is_indoor IS KNOWN TO BE WRONG. engine/geometry_db.py:133 says so from
   its own census: "the winter pool carries mislabeled-indoor rows (measured:
   55m/60m/300m/1000m in the Dec-Feb slice)". If a chunk of the 1,575 indoor
   cells are really outdoor meets, or vice versa, then part of the measured
   -1.5% is label noise -- and pinning the group mean would push that
   correction onto the CORRECTLY labelled venues, making them wrong to fix a
   number that was never the venues' fault.

Two readings, cheap first:

  1. THE CALENDAR. Indoor season is roughly December to March. An
     indoor-flagged meet in May, or an outdoor-flagged one in January, is
     suspicious on its face. Reads meets_tf only.

  2. THE EVENT TELLS (--tells). geometry_db._INDOOR_TELL_EVENTS is a MEASURED
     list -- sub-80m dashes, their hurdle forms, short-oval relays -- that
     essentially do not exist outdoors. A meet hosting one IS indoor whatever
     its flag says. This is the better test and it scans result rows, so it is
     opt-in and guarded.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Indoor season, generously. Nov and Apr are shoulders and not counted wrong.
INDOOR_MONTHS = ("12", "01", "02", "03")
OUTDOOR_MONTHS = ("05", "06", "07", "08")


def _tells():
    """The measured tell list, from the module that owns it -- not a copy."""
    try:
        from geometry_db import _INDOOR_TELL_EVENTS
        return list(_INDOOR_TELL_EVENTS)
    except Exception:                                  # noqa: BLE001
        return []


# ⚠ meets_tf HAS NO DATE COLUMN AT ALL (owner, 2026-09-18:
#   'UndefinedColumn: column "date" does not exist ... Perhaps you meant
#   "meets_tf.state"'). Its columns are div_id, meet_id, meet_name, venue_name,
#   meet_url, event_short, event_id, distance_meters, gps_lat, gps_long, state,
#   is_indoor -- no date. The meet's date lives on meets_tf_meta.meet_date, one
#   row per meet, and the row-level date is on results_tf.date.
#
# ! SO THE COLUMNS ARE CHECKED, and a wrong assumption fails with a sentence
#   naming what is actually there instead of a raw UndefinedColumn.
def _needCols(cur, table, cols):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    have = {r[0] for r in cur.fetchall()}
    missing = [c for c in cols if c not in have]
    if missing:
        raise SystemExit(
            f"  {table} has no column(s) {missing}. It has: "
            f"{', '.join(sorted(have))}")
    return have


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2015)
    ap.add_argument("--tells", action="store_true",
                    help="also test against the measured indoor-tell events. "
                         "Scans result rows; this is the better test.")
    args = ap.parse_args()

    from database import getConn
    from pg_guard import guard
    with getConn() as conn:
        with conn.cursor() as cur:
            guard(cur)

            print(f"\n=== 1. the calendar (meets_tf + meets_tf_meta, cheap) "
                  f"===", flush=True)
            _needCols(cur, "meets_tf", ("meet_id", "is_indoor"))
            _needCols(cur, "meets_tf_meta", ("meet_id", "meet_date"))
            # ! THE DATE COMES FROM meets_tf_meta, one row per meet, so this is
            #   a keyed join and not a scan of results_tf.
            cur.execute("""
                SELECT COALESCE(m.is_indoor, 0) = 1 AS flagged_indoor,
                       substr(mm.meet_date, 6, 2)   AS mon,
                       count(*)
                FROM   meets_tf m
                JOIN   meets_tf_meta mm ON mm.meet_id = m.meet_id
                WHERE  mm.meet_date ~ '^(19|20)[0-9][0-9]-'
                  AND  substr(mm.meet_date, 1, 4)::int >= %s
                GROUP  BY 1, 2 ORDER BY 1, 2
            """, (args.since,))
            by = {}
            for flagged, mon, n in cur.fetchall():
                by[(bool(flagged), mon)] = int(n)
            if not by:
                print("    no dated meets -- meets_tf_meta.meet_date is empty "
                      "for this window.")
                conn.rollback()
                return

            ind_total = sum(n for (f, _m), n in by.items() if f)
            out_total = sum(n for (f, _m), n in by.items() if not f)
            ind_wrong = sum(n for (f, m), n in by.items()
                            if f and m in OUTDOOR_MONTHS)
            out_wrong = sum(n for (f, m), n in by.items()
                            if not f and m in INDOOR_MONTHS)
            print(f"    indoor-flagged rows:  {ind_total:>9,}   "
                  f"of which in {'/'.join(OUTDOOR_MONTHS)}: {ind_wrong:>8,} "
                  f"({100.0 * ind_wrong / max(ind_total, 1):5.2f}%)")
            print(f"    outdoor-flagged rows: {out_total:>9,}   "
                  f"of which in {'/'.join(INDOOR_MONTHS)}: {out_wrong:>8,} "
                  f"({100.0 * out_wrong / max(out_total, 1):5.2f}%)")
            print(f"\n    month distribution of indoor-flagged rows:")
            for mon in sorted({m for (_f, m) in by}):
                n = by.get((True, mon), 0)
                bar = "#" * min(60, int(60.0 * n / max(ind_total, 1) * 4))
                print(f"      {mon}  {n:>8,}  {bar}")

            # ★ WHAT IT MEANS FOR THE GAUGE, said out loud.
            noise = 100.0 * ind_wrong / max(ind_total, 1)
            print(f"\n    -> {noise:.2f}% of indoor-flagged rows sit in high "
                  f"summer.")
            if noise > 2.0:
                print(f"       That is enough label noise that anchoring on "
                      f"outdoor would push part of\n"
                      f"       the correction onto correctly labelled ovals. "
                      f"Clean the labels first, or\n"
                      f"       restrict the gauge to cells that pass the "
                      f"tells.")
            else:
                print(f"       Small enough that the -1.5% is about the level, "
                      f"not the labels, so the\n"
                      f"       outdoor gauge is measuring what it should.")

            if args.tells:
                tells = _tells()
                if not tells:
                    print("\n  (geometry_db._INDOOR_TELL_EVENTS unavailable)")
                else:
                    print(f"\n=== 2. the measured event tells "
                          f"({len(tells)} of them) ===", flush=True)
                    # ! A MEET HOSTING A TELL IS INDOOR, whatever its flag.
                    cur.execute("""
                        WITH t AS MATERIALIZED (
                            SELECT DISTINCT r.div_id
                            FROM   results_tf r
                            WHERE  lower(btrim(r.event)) = ANY(%s)
                        )
                        SELECT COALESCE(m.is_indoor, 0) = 1 AS flagged,
                               (t.div_id IS NOT NULL)      AS has_tell,
                               count(*)
                        FROM   meets_tf m
                        JOIN   meets_tf_meta mm ON mm.meet_id = m.meet_id
                        LEFT   JOIN t ON t.div_id = m.div_id
                        WHERE  mm.meet_date ~ '^(19|20)[0-9][0-9]-'
                          AND  substr(mm.meet_date, 1, 4)::int >= %s
                        GROUP  BY 1, 2 ORDER BY 1, 2
                    """, ([e.lower() for e in tells], args.since))
                    print(f"    {'flagged':<9} {'has tell':<9} {'rows':>10}")
                    for flagged, has_tell, n in cur.fetchall():
                        print(f"    {str(bool(flagged)):<9} "
                              f"{str(bool(has_tell)):<9} {n:>10,}")
                    print(f"    -> 'False / True' is an indoor meet flagged "
                          f"outdoor. 'True / False' is\n"
                          f"       weaker evidence: a real indoor meet can "
                          f"simply host no tell event.")
        conn.rollback()


if __name__ == "__main__":
    main()
