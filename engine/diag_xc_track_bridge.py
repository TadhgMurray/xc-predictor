#!/usr/bin/env python3
"""
diag_xc_track_bridge.py -- can the flat-400 reference anchor CROSS COUNTRY?

    python engine/diag_xc_track_bridge.py
    python engine/diag_xc_track_bridge.py --window 30 45 60

★★ THE QUESTION, AND WHY IT IS A COUNT RATHER THAN AN ARGUMENT (owner,
   2026-09-20: "give Xc an absolute anchor too sure (although shouldn't it just
   be a track still?)").

   Track now has an absolute anchor: a flat outdoor 400m oval IS 0.0, and
   indoor sits +0.3% off it by assertion. Cross country has none. The bracket
   engine groups cells by (sport, era) and pins each group's vote-weighted mean
   to zero, so XC's zero is its OWN mean -- a see-saw. If California's courses
   are heavily raced and genuinely fast, holding the mean at zero MATHEMATICALLY
   REQUIRES everything else to go negative. That is the owner's standing
   complaint, and it is a property of the constraint, not of the courses.

   Merging the gauge groups would let the track reference anchor XC directly.
   The objection on record is mu: "The seasons never overlap, so the
   fall-to-spring difference is surface PLUS six months of fitness,
   inseparably. It is a definition."

⚠ THAT OBJECTION IS ABOUT OUTDOOR TRACK, AND INDOOR IS NOT OUTDOOR. XC
  finishes in early December (NXN, Foot Locker); indoor season STARTS in
  December. The gap is weeks, not six months -- and it is inside the bracket
  window (21-45 days). Indoor is itself now pinned to the absolute scale. So
  there is a candidate chain:

      XC --(athletes racing both within the window)--> indoor --(+0.3%)--> 0.0

  Whether that chain carries any weight is the question this script answers.

★ WHAT IT MEASURES, AND WHAT EACH NUMBER DECIDES:
    bridging athlete-seasons   athletes with an XC race and a TRACK race whose
                               dates are within `window` days. These are the
                               only rows that can transmit the anchor.
    bridging pairs             the actual within-window XC/track race pairs --
                               the edges, which is what the engine's local
                               level comparison uses.
    share of XC rows reachable the fraction of XC rows belonging to an athlete
                               who bridges. A large share means XC is connected
                               to the anchored side and merging the gauge
                               groups is identified; a small one means it would
                               drift, and XC needs its own reference class
                               instead.
    indoor vs outdoor split    which side the bridge lands on. Bridging only to
                               OUTDOOR track is the six-month case mu exists
                               for and does NOT count as evidence.

! READ-ONLY. It writes nothing and changes no rating.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_WINDOWS = (21, 30, 45, 60)


def _hasColumn(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def measure(cur, window, verbose=True):
    """The bridge at one window, in one pass per side."""
    # ! is_indoor LIVES ON meets_tf, so the split is a join and not a guess.
    #   Without it the script still runs and simply cannot say which side of
    #   the bridge it found -- which it then says, rather than implying that
    #   every bridge is an indoor one.
    has_meta = _hasColumn(cur, "meets_tf", "is_indoor")
    indoor_sel = "COALESCE(m.is_indoor, false)" if has_meta else "NULL::boolean"
    indoor_join = ("LEFT JOIN meets_tf m ON m.meet_id = t.meet_id "
                   "AND m.div_id = t.div_id") if has_meta else ""
    cur.execute(f"""
        WITH xc AS (
            SELECT person_id, date::date AS d
            FROM   results
            WHERE  person_id IS NOT NULL AND date ~ '^(19|20)[0-9][0-9]-'
        ),
        tf AS (
            SELECT t.person_id, t.date::date AS d, {indoor_sel} AS indoor
            FROM   results_tf t
            {indoor_join}
            WHERE  t.person_id IS NOT NULL AND t.date ~ '^(19|20)[0-9][0-9]-'
        ),
        pairs AS (
            SELECT xc.person_id, xc.d AS xc_d, tf.d AS tf_d, tf.indoor
            FROM   xc JOIN tf
                   ON tf.person_id = xc.person_id
                  AND tf.d BETWEEN xc.d - %(w)s AND xc.d + %(w)s
        )
        SELECT count(*)                                   AS n_pairs,
               count(DISTINCT person_id)                  AS n_people,
               count(*) FILTER (WHERE indoor)             AS n_indoor,
               count(*) FILTER (WHERE indoor IS FALSE)    AS n_outdoor,
               count(*) FILTER (WHERE indoor IS NULL)     AS n_unknown
        FROM   pairs
    """, {"w": window})
    row = cur.fetchone()
    keys = ("n_pairs", "n_people", "n_indoor", "n_outdoor", "n_unknown")
    out = dict(zip(keys, [int(x or 0) for x in row]))
    out["window"] = window
    out["has_surface"] = has_meta
    return out


def xcReach(cur, window):
    """What share of XC ROWS belong to an athlete who bridges at this window.

    ★ ROWS, NOT ATHLETES, because the gauge is vote-weighted: an anchor that
      reaches a thousand athletes with one race each carries far less than one
      reaching a hundred who race twenty times.
    """
    cur.execute("""
        WITH xc AS (
            SELECT person_id, date::date AS d
            FROM   results
            WHERE  person_id IS NOT NULL AND date ~ '^(19|20)[0-9][0-9]-'
        ),
        bridged AS (
            SELECT DISTINCT xc.person_id
            FROM   xc JOIN results_tf t
                   ON t.person_id = xc.person_id
                  AND t.date ~ '^(19|20)[0-9][0-9]-'
                  AND t.date::date BETWEEN xc.d - %(w)s AND xc.d + %(w)s
        )
        SELECT count(*),
               count(*) FILTER (WHERE x.person_id IN (SELECT person_id FROM bridged))
        FROM   xc x
    """, {"w": window})
    total, reached = cur.fetchone()
    return int(total or 0), int(reached or 0)


def report(rows, reach):
    print("\n[bridge] XC rows that can carry the track anchor\n")
    print(f"  {'window':>7} {'pairs':>14} {'athletes':>12} "
          f"{'indoor':>12} {'outdoor':>12}")
    for r in rows:
        print(f"  {r['window']:>7} {r['n_pairs']:>14,} {r['n_people']:>12,} "
              f"{r['n_indoor']:>12,} {r['n_outdoor']:>12,}")
        if not r["has_surface"]:
            print("          (meets_tf has no is_indoor: the split is unknown, "
                  "so treat these as UNSPLIT)")
    print(f"\n  {'window':>7} {'XC rows':>14} {'reachable':>14} {'share':>8}")
    for w, (total, reached) in sorted(reach.items()):
        share = reached / total if total else 0.0
        print(f"  {w:>7} {total:>14,} {reached:>14,} {100 * share:>7.1f}%")

    # ★★ THE VERDICT, STATED, so the number is not left to be interpreted
    #    later by whoever is arguing for what they already wanted.
    best = max(reach.items(), key=lambda kv: kv[1][1] / max(kv[1][0], 1))
    w, (total, reached) = best
    share = reached / total if total else 0.0
    print(f"\n  BEST WINDOW {w}d reaches {100 * share:.1f}% of XC rows.")
    if share >= 0.25:
        print("  -> FAT ENOUGH. Merging the gauge groups is identified: the "
              "flat-400\n     reference can anchor XC through these athletes. "
              "Score it on the\n     holdout before it becomes the default.")
    elif share >= 0.05:
        print("  -> THIN. The bridge exists but carries little weight; merging "
              "would let\n     XC drift on a small subset. Prefer a hand-named "
              "XC reference class.")
    else:
        print("  -> ABSENT. XC cannot be anchored to the track reference by "
              "iteration.\n     It needs its own reference class (named "
              "courses held at an asserted\n     value), which is the only "
              "remaining option that breaks the see-saw.")
    ind = sum(r["n_indoor"] for r in rows)
    outd = sum(r["n_outdoor"] for r in rows)
    if ind or outd:
        print(f"\n  ! OF THE PAIRS, {100.0 * ind / max(ind + outd, 1):.1f}% "
              f"land on INDOOR track.")
        print("    Only those count as evidence: an XC-to-OUTDOOR pair is the "
              "six-month\n    fall-to-spring gap mu exists to define, not a "
              "measurement of surface.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=int, nargs="+", default=list(DEFAULT_WINDOWS),
                    help="days either side of an XC race to look for a track one")
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        rows = [measure(cur, w) for w in args.window]
        reach = {w: xcReach(cur, w) for w in args.window}
    report(rows, reach)


if __name__ == "__main__":
    main()
