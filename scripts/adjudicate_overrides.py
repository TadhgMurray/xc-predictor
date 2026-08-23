"""When the rebuild and the old file disagree, which distance does the data pick?

    python scripts/adjudicate_overrides.py pass1.py
    python scripts/adjudicate_overrides.py pass1.py --lost      # the other half
    python scripts/adjudicate_overrides.py pass1.py --worst 60

Run from the PROJECT ROOT. READ ONLY.

★ THE TEST, AND IT IS NOT AN OPINION ABOUT WHICH FILE IS OLDER.

  A distance is right when it puts the field back on its own heads. So for a
  division where the two sources disagree, evaluate BOTH candidates against
  the same evidence:

      gap(d) = median over the field of
               [ rating_db * (d / d_built_at) ** K  -  that athlete's median ]

  rating scales as d**K, so moving a division to a candidate distance is one
  multiply. Whichever candidate leaves |gap| closer to zero is the one the
  corpus supports. compare_overrides counts the disagreements; this one
  settles them.

! d_built_at IS THE OLD OVERRIDE, and that is a measured fact rather than an
  assumption -- census_override_sources.py --verify recomputed
  normalized_time at both candidates and found 97.8% of rows match the
  override. Re-run it if anything upstream has moved since.

⚠ SO THE OLD CANDIDATE IS THE ONE THE RATINGS WERE BUILT AT, and its gap is
  therefore whatever the corpus says it is -- NOT zero by construction. If
  the old override were right the field would already sit on its own heads,
  which is exactly the reading: gap_old near zero means the old value was
  right and the pass that replaced it is wrong.

⚠ THE ATHLETE MEDIANS SHIFT A LITTLE TOO and this does not model that. One
  division is a small part of an athlete's history, so the effect is second
  order -- but it is why a verdict inside a point or two of a tie is reported
  as a tie rather than as a winner.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from psycopg2.extras import RealDictCursor, execute_values   # noqa: E402
from database import getConn                                 # noqa: E402
from audit_overrides import K                                # noqa: E402
from rebuild_overrides import buildGap, MIN_OWN_RACES        # noqa: E402
from compare_overrides import loadDicts, newestBackup        # noqa: E402

# A verdict inside this many rating points is a tie, not a winner. The
# per-athlete noise floor measured on this corpus is sigma ~ 4.5 and the SE of
# a field median is 1.25*sigma/sqrt(N); at a field of 30 that is about 1.0.
# Two points is comfortably outside that and still refuses to call a coin toss.
TIE = 2.0

_GAPS = """
SELECT g.meet_id, g.div_id,
       count(*)                                           AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY g.gap) AS med_gap,
       avg(g.med)                                         AS base
FROM   reb_gap g
JOIN   _adj a ON a.meet_id = g.meet_id AND a.div_id = g.div_id
GROUP  BY 1, 2
"""


def verdict(med_gap, base, d_built, d_new):
    """(gap at the old distance, gap at the new one, winner).

    gap_old is the measured one: the ratings were built at d_built.
    gap_new moves every rating by (d_new/d_built)**K, so the median gap moves
    with the median rating -- base + med_gap is that rating.
    """
    gap_old = med_gap
    rating_old = base + med_gap
    gap_new = rating_old * (d_new / d_built) ** K - base
    if abs(gap_old) - abs(gap_new) > TIE:
        return gap_old, gap_new, "rebuilt"
    if abs(gap_new) - abs(gap_old) > TIE:
        return gap_old, gap_new, "old"
    return gap_old, gap_new, "tie"


def main():
    ap = argparse.ArgumentParser(
        description="Which distance does the field support -- the rebuilt one "
                    "or the old one?")
    ap.add_argument("proposal")
    ap.add_argument("--against", default=None)
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--lost", action="store_true",
                    help="adjudicate the OLD overrides the rebuild did not "
                         "re-propose, against their scraped distance, instead "
                         "of the disagreements")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--worst", type=int, default=30)
    ap.add_argument("--min-own-races", type=int, default=MIN_OWN_RACES,
                    dest="min_own_races")
    args = ap.parse_args()

    old_path = args.against or newestBackup()
    if not old_path:
        print("\n  no backup found; pass --against.\n")
        return 1
    new = loadDicts(args.proposal, args.sport)
    old = loadDicts(old_path, args.sport)

    if args.lost:
        # ! AGAINST THE SCRAPE, because that is what a wipe would leave. The
        #   question here is not "which of two overrides" but "was the old
        #   override doing anything?" -- so the rival candidate is the value
        #   the division reverts to.
        cases = {k: (v, None) for k, v in old.items() if k not in new}
        label = (f"OLD OVERRIDES THE REBUILD DROPPED ({len(cases):,}) -- "
                 f"override vs the scraped distance")
    else:
        cases = {k: (old[k], v) for k, v in new.items()
                 if k in old and abs(v - old[k]) / old[k] > args.tol}
        label = (f"WHERE THEY DISAGREE ({len(cases):,}) -- "
                 f"rebuilt vs old")
    if not cases:
        print("\n  nothing to adjudicate.\n")
        return 0

    print(f"\n{label}\n")
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # ⚠ NO --as-if-wiped. These ratings must be the ones on disk,
            #   because d_built_at is the distance they were built at.
            buildGap(cur, args.sport, args.min_own_races)
            cur.execute("DROP TABLE IF EXISTS _adj")
            cur.execute("CREATE TEMP TABLE _adj "
                        "(meet_id bigint, div_id bigint)")
            execute_values(cur, "INSERT INTO _adj VALUES %s",
                           list(cases.keys()))
            cur.execute("CREATE INDEX ON _adj (meet_id, div_id)")
            cur.execute("ANALYZE _adj")
            if args.lost:
                cur.execute("""
                    SELECT a.meet_id, a.div_id,
                           (SELECT min(m.distance) FROM meets m
                             WHERE m.meet_id = a.meet_id
                               AND m.div_id = a.div_id
                               AND m.distance > 0) AS scraped
                    FROM _adj a""")
                for r in cur.fetchall():
                    key = (r["meet_id"], r["div_id"])
                    if key in cases and r["scraped"]:
                        cases[key] = (cases[key][0], float(r["scraped"]))
            cur.execute(_GAPS)
            rows = cur.fetchall()

    tally = {"rebuilt": 0, "old": 0, "tie": 0}
    detail, unrated = [], len(cases) - len(rows)
    for r in rows:
        key = (r["meet_id"], r["div_id"])
        d_built, d_new = cases[key]
        if not d_new or not d_built:
            continue
        g_old, g_new, who = verdict(float(r["med_gap"]), float(r["base"]),
                                    d_built, d_new)
        tally[who] += 1
        detail.append((abs(g_old) - abs(g_new), key, r["n"], d_built, d_new,
                       g_old, g_new, who))

    judged = sum(tally.values())
    if not judged:
        print("  none of these divisions has a judgeable field "
              f"({unrated:,} had too few rated rows).")
        return 0

    print(f"  {'the rebuilt distance fits better':<38}{tally['rebuilt']:>8,}"
          f"{100.0 * tally['rebuilt'] / judged:>8.1f}%")
    print(f"  {'the OLD distance fits better':<38}{tally['old']:>8,}"
          f"{100.0 * tally['old'] / judged:>8.1f}%")
    print(f"  {'too close to call':<38}{tally['tie']:>8,}"
          f"{100.0 * tally['tie'] / judged:>8.1f}%")
    if unrated > 0:
        print(f"  {'not judgeable (too few rated rows)':<38}{unrated:>8,}")

    detail.sort()
    print(f"\n\n  WHERE THE OLD VALUE WINS MOST CLEARLY\n")
    print(f"  {'meet':>10}{'div':>10}{'n':>6}{'old d':>8}{'new d':>8}"
          f"{'gap@old':>9}{'gap@new':>9}  winner")
    print("  " + "-" * 68)
    for _s, (meet, div), n, d_o, d_n, g_o, g_n, who in detail[:args.worst]:
        print(f"  {meet:>10}{div:>10}{n:>6}{d_o:>8.0f}{d_n:>8.0f}"
              f"{g_o:>+9.1f}{g_n:>+9.1f}  {who}")

    print("\n" + "=" * 70)
    share = tally["rebuilt"] / judged
    if share >= 0.8:
        print(f"  THE REBUILD IS RIGHT AND THE OLD FILE WAS WRONG on "
              f"{tally['rebuilt']:,} of {judged:,}.\n"
              f"  Disagreement at that rate is the rebuild working, not "
              f"failing.")
    elif tally["old"] / judged >= 0.5:
        print(f"  ⚠ THE OLD FILE WINS {tally['old']:,} OF {judged:,}. The "
              f"passes are replacing correct\n    values with worse ones. Do "
              f"NOT append. Read a few with\n    rebuild_overrides.py "
              f"--explain MEET/DIV.")
    else:
        print(f"  ⚠ MIXED: rebuilt {tally['rebuilt']:,}, old {tally['old']:,}, "
              f"tie {tally['tie']:,}. Neither source is\n    reliably better, "
              f"which means the disagreements are not one fault.")
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
