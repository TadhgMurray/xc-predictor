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

# ! A FLOOR ON THE YEARS, because the first run should not be a research
#   project. The question is about how the corpus behaves now; 1990s rows
#   cannot inform a gauge decision and they double the scan.
DEFAULT_SINCE = 2010

# ⚠ THE DATE COLUMNS ARE TEXT, and `date::date` throws on anything that is not
#   a real date. The regex guard and the cast must not be separable by the
#   planner, which is what AS MATERIALIZED buys (PG12+; the server is 18).
#   engine/fit_weather_correction.py casts the same column the same way.
_DATE_OK = r"^(19|20)[0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def _hasColumn(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def measure(cur, window, since=DEFAULT_SINCE):
    """Every number for one window, in ONE pass over each side.

    ⚠⚠ DISTINCT (person, DAY) BEFORE THE JOIN, AND THAT IS NOT A TIDY-UP.
       The first version joined results to results_tf on person_id with a date
       range and counted the pairs. An athlete with fifty XC rows and fifty
       track rows inside the window contributes two and a half THOUSAND pairs,
       and the join is 225M x 121M before the range narrows it -- a query that
       would still be running tomorrow. Reducing each side to distinct race
       DAYS first bounds it: an athlete has at most a couple of dozen race days
       a season, so the pair count is a small multiple of the athlete count and
       the answer is identical, because a second race on a day already bridged
       adds no evidence about whether the sports connect.

    ⚠ is_indoor IS AN INTEGER, NOT A BOOLEAN (the owner's traceback,
      2026-09-20: "COALESCE types integer and boolean cannot be matched").
      `::int` first makes this correct whether the column is an int or a bool,
      so the same expression survives a schema that changes underneath it.
    """
    have_meta = _hasTable(cur, "meets_tf") and _hasColumn(cur, "meets_tf", "is_indoor")
    if have_meta:
        indoor_sel = "COALESCE(m.is_indoor::int, 0) <> 0"
        indoor_join = ("LEFT JOIN meets_tf m ON m.meet_id = t.meet_id "
                       "AND m.div_id = t.div_id")
    else:
        indoor_sel, indoor_join = "NULL::boolean", ""

    cur.execute(f"""
        WITH xc AS MATERIALIZED (
            SELECT DISTINCT person_id, date::date AS d
            FROM   results
            WHERE  person_id IS NOT NULL
              AND  date ~ %(re)s
              AND  substr(date, 1, 4)::int >= %(since)s
        ),
        tf AS MATERIALIZED (
            SELECT DISTINCT t.person_id, t.date::date AS d,
                   {indoor_sel} AS indoor
            FROM   results_tf t
            {indoor_join}
            WHERE  t.person_id IS NOT NULL
              AND  t.date ~ %(re)s
              AND  substr(t.date, 1, 4)::int >= %(since)s
        ),
        pairs AS (
            SELECT xc.person_id, xc.d AS xc_d, tf.indoor
            FROM   xc JOIN tf
                   ON tf.person_id = xc.person_id
                  AND tf.d BETWEEN xc.d - %(w)s AND xc.d + %(w)s
        ),
        -- the XC race DAYS that can carry the anchor, and the people they
        -- belong to: the two grains the report needs.
        bridged_day AS (
            SELECT DISTINCT person_id, xc_d FROM pairs
        ),
        bridged_person AS (
            SELECT DISTINCT person_id FROM pairs
        ),
        -- ★ ROWS, NOT ATHLETES. The gauge is VOTE-WEIGHTED: an anchor that
        --   reaches a thousand athletes with one race each carries far less
        --   than one reaching a hundred who race twenty times. So the share
        --   that decides this is a share of ROWS.
        xc_rows AS (
            SELECT r.person_id, r.date::date AS d
            FROM   results r
            WHERE  r.person_id IS NOT NULL
              AND  r.date ~ %(re)s
              AND  substr(r.date, 1, 4)::int >= %(since)s
        )
        SELECT (SELECT count(*) FROM pairs),
               (SELECT count(*) FROM bridged_person),
               (SELECT count(*) FROM pairs WHERE indoor),
               (SELECT count(*) FROM pairs WHERE indoor IS FALSE),
               (SELECT count(*) FROM pairs WHERE indoor IS NULL),
               (SELECT count(*) FROM xc_rows),
               (SELECT count(*) FROM xc_rows x
                 WHERE EXISTS (SELECT 1 FROM bridged_day b
                                WHERE b.person_id = x.person_id AND b.xc_d = x.d)),
               (SELECT count(*) FROM xc_rows x
                 WHERE EXISTS (SELECT 1 FROM bridged_person b
                                WHERE b.person_id = x.person_id))
    """, {"w": window, "since": since, "re": _DATE_OK})
    row = [int(v or 0) for v in cur.fetchone()]
    keys = ("n_pairs", "n_people", "n_indoor", "n_outdoor", "n_unknown",
            "xc_rows", "rows_own_day", "rows_same_person")
    out = dict(zip(keys, row))
    out["window"] = window
    out["has_surface"] = have_meta
    return out


def report(rows, since):
    print(f"\n[bridge] can the flat-400 reference reach CROSS COUNTRY? "
          f"(seasons {since}+)\n")
    print(f"  {'window':>7} {'bridging pairs':>16} {'athletes':>11} "
          f"{'indoor':>12} {'outdoor':>12}")
    for r in rows:
        print(f"  {r['window']:>7} {r['n_pairs']:>16,} {r['n_people']:>11,} "
              f"{r['n_indoor']:>12,} {r['n_outdoor']:>12,}")
    if rows and not rows[0]["has_surface"]:
        print("\n  ⚠ meets_tf has no is_indoor here, so the indoor/outdoor "
              "split is UNKNOWN.\n    Treat the verdict below as an upper "
              "bound: an XC-to-OUTDOOR pair is the\n    six-month gap mu "
              "exists to define, not evidence about surface.")

    print(f"\n  {'window':>7} {'XC rows':>14} {'row bridges':>14} {'share':>8}"
          f"   {'athlete bridges':>16} {'share':>8}")
    for r in rows:
        t = max(r["xc_rows"], 1)
        print(f"  {r['window']:>7} {r['xc_rows']:>14,} "
              f"{r['rows_own_day']:>14,} {100.0 * r['rows_own_day'] / t:>7.1f}%"
              f"   {r['rows_same_person']:>16,} "
              f"{100.0 * r['rows_same_person'] / t:>7.1f}%")
    print("\n  row bridges     = this XC row's OWN day has a track race within "
          "the window\n  athlete bridges = the row belongs to an athlete who "
          "bridges somewhere")

    # ★★ THE VERDICT, STATED AGAINST THRESHOLDS WRITTEN BEFORE THE NUMBER WAS
    #    SEEN, so it is not reinterpreted later by whoever is arguing for what
    #    they already wanted. The strict grain decides: a row can only transmit
    #    the anchor through its own athlete's nearby races.
    best = max(rows, key=lambda r: r["rows_same_person"] / max(r["xc_rows"], 1))
    share = best["rows_same_person"] / max(best["xc_rows"], 1)
    ind, outd = best["n_indoor"], best["n_outdoor"]
    print(f"\n  BEST WINDOW {best['window']}d: "
          f"{100.0 * share:.1f}% of XC rows belong to a bridging athlete.")
    if share >= 0.25:
        print("  -> FAT ENOUGH. Merging the gauge groups is identified: the "
              "flat-400\n     reference can reach XC through these athletes. "
              "Score it on the holdout\n     before it becomes the default.")
    elif share >= 0.05:
        print("  -> THIN. The bridge exists but carries little weight; merging "
              "would let XC\n     drift on a small subset. Prefer a "
              "hand-named XC reference class.")
    else:
        print("  -> ABSENT. XC cannot be anchored to the track reference by "
              "iteration. It\n     needs its own reference class -- named "
              "courses held at an asserted\n     value -- which is the only "
              "remaining option that breaks the see-saw.")
    if ind or outd:
        print(f"\n  ! {100.0 * ind / max(ind + outd, 1):.1f}% of the bridging "
              f"pairs land on INDOOR track.")
        print("    Only those are evidence about surface. An XC-to-OUTDOOR "
              "pair spans the\n    fall-to-spring gap, which is fitness plus "
              "surface inseparably -- the\n    reason mu is a definition. If "
              "this share is low the bridge is weaker\n    than the headline "
              "suggests.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=int, nargs="+", default=list(DEFAULT_WINDOWS),
                    help="days either side of an XC race to look for a track one")
    ap.add_argument("--since", type=int, default=DEFAULT_SINCE,
                    help=f"earliest season to count (default {DEFAULT_SINCE}); "
                         f"lower it for the whole corpus and a longer run")
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        rows = []
        for w in args.window:
            print(f"  ... window {w}d", flush=True)
            rows.append(measure(cur, w, args.since))
    report(rows, args.since)


if __name__ == "__main__":
    main()
