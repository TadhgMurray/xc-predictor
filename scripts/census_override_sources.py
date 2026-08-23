"""What would a wipe of the distance overrides actually cost?

    python scripts/census_override_sources.py
    python scripts/census_override_sources.py --sport TF
    python scripts/census_override_sources.py --worst 40

Run from the PROJECT ROOT. READ ONLY.

★ THE QUESTION, AND IT HAS TO BE ANSWERED BEFORE ANYTHING IS CLEARED.

  A row in dist_override is one of two completely different things:

    A CORRECTION   `meets` (or the tfrrs blob) already carries a distance, and
                   the override disagrees with it. Clearing this is
                   RECOVERABLE: the scraped value comes back, the division is
                   still rated, and pass 1 gets another look at it. If the
                   override was right, pass 1 re-proposes it from the same
                   evidence that justified it the first time.

    A SOLE SOURCE  nothing else has a distance at all. The override IS the
                   data. Clearing it does not restore a scraped value -- it
                   leaves the division with no distance, so backfill_normalize
                   writes no normalized_time and the engine rates NOTHING
                   there. Meet 26359, Ox Bow Park, has 568 tfrrs rows and no
                   `meets` row whatsoever; that shape is not rare.

  wipe_overrides.py clears both kinds indiscriminately. For a correction that
  is the intent. For a sole source it is silent data loss with nothing to
  replace it -- and it is invisible afterwards, because a division that is no
  longer rated cannot show up in any rating-based audit. That is the same
  blind spot that hid 26359/0 from every tool before it.

⚠ SO THIS IS NOT A REPORT ABOUT TIDINESS. It is the number that decides
  whether the three-pass rebuild starts from a clean slate or from a slate
  with the sole sources still on it.
"""
import os
import sys
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

from database import getConn                              # noqa: E402
# ★ THE SAME READER build_ranking_results USES, imported not copied. tfrrs XC
#   distances live in a JSONB blob on meets_tfrrs keyed by div_id-as-string,
#   and a second spelling of that is a second way to get it wrong.
from build_ranking_results import _XC_TFRRS_DIST_SQL      # noqa: E402

# Cross country only takes its distance from meets / the tfrrs blob; track
# keeps it in the event name, so `meets_tf` cannot answer this question and an
# override there is always a sole source by construction.
_TABLE = {"XC": "results", "TF": "results_tf"}

# ------------------------------------------------------------------ #
#  --verify -- WHICH DISTANCE DID THE BACKFILL ACTUALLY USE?
# ------------------------------------------------------------------ #
#
# ⚠ THE FACT THE WHOLE RESET TURNS ON, AND IT IS NOT DERIVABLE FROM DATES.
#   rebuild_overrides --as-if-wiped reverts each division's ratings from the
#   override distance to the scraped one before judging. That is right ONLY if
#   the ratings on disk were built WITH the overrides. If they were already
#   built without them, the flag reverts a second time and every proposal is
#   garbage.
#
#   dump_overrides is not a pipeline step and run_ratings.ps1 starts at 08, so
#   the propagation is easy to reason about and easy to get wrong. This does
#   not reason about it: it recomputes.
#
# ★ THE TEST IS A RECOMPUTATION, THE SAME ONE anchor_check MAKES.
#   normalizeTime is deterministic given the row's time, distance, pool and
#   sport -- so run it at BOTH candidate distances and see which reproduces
#   the stored normalized_time. Whichever matches is the distance the backfill
#   used. No date, no log, no inference.
#
# ! THE POOL COMES FROM ranking_results, which is the only table that stores
#   it, and rows missing from there cannot be checked -- they are counted as
#   unanswerable rather than as either answer.
_VERIFY = """
SELECT o.meet_id, o.div_id, o.distance AS ovr, base.d AS scraped,
       r.result_id, r.time_seconds, r.normalized_time, k.pool
FROM   dist_override o
CROSS  JOIN LATERAL (
    SELECT COALESCE(
        (SELECT min(m.distance) FROM meets m
          WHERE m.meet_id = o.meet_id AND m.div_id = o.div_id
            AND m.distance IS NOT NULL AND m.distance > 0),
        (SELECT min(x.distance) FROM tmp_xc_tfrrs_dist x
          WHERE x.meet_id = o.meet_id AND x.div_id = o.div_id)
    ) AS d
) base
JOIN   {table} r ON r.meet_id = o.meet_id AND r.div_id = o.div_id
JOIN   ranking_results k ON k.result_id = r.result_id AND k.sport = %(sport)s
WHERE  base.d IS NOT NULL AND base.d > 0 AND o.distance > 0
  AND  abs(base.d - o.distance) / base.d > %(tol)s
  AND  r.normalized_time IS NOT NULL AND r.normalized_time > 0
  AND  r.time_seconds > 0
LIMIT  %(sample)s
"""

# How close a recomputation has to land to count as a match. anchor_check uses
# 10% to separate a pool mismatch from a stale spline; here the two candidates
# are a median 25% apart, so 5% separates them with room and still tolerates a
# spline refit since the backfill ran.
MATCH_TOL = 0.05


def verify(cur, sport, tol, sample):
    """Did the stored normalized_time come from the override or the scrape?"""
    from anchor_check import mismatch

    cur.execute(_VERIFY.format(table=_TABLE[sport]),
                {"sport": sport, "tol": tol, "sample": sample})
    rows = cur.fetchall()
    if not rows:
        print("\n  --verify: no checkable row. Either dist_override is empty, "
              "or\n  ranking_results has not been rebuilt since it was "
              "populated.")
        return None

    tally = {"override": 0, "scraped": 0, "neither": 0}
    for _m, _d, ovr, scraped, _rid, t, nt, pool in rows:
        _b1, _e1, r_ovr = mismatch(t, float(ovr), nt, pool, sport)
        _b2, _e2, r_scr = mismatch(t, float(scraped), nt, pool, sport)
        off_o = abs(r_ovr - 1.0) if r_ovr is not None else None
        off_s = abs(r_scr - 1.0) if r_scr is not None else None
        ok_o = off_o is not None and off_o <= MATCH_TOL
        ok_s = off_s is not None and off_s <= MATCH_TOL
        if ok_o and (not ok_s or off_o < off_s):
            tally["override"] += 1
        elif ok_s:
            tally["scraped"] += 1
        else:
            tally["neither"] += 1

    n = len(rows)
    print(f"\n\n  WHICH DISTANCE THE BACKFILL USED  ({n:,} rows sampled from "
          f"divisions whose\n  override disagrees with the scrape, "
          f"recomputed both ways)\n")
    for k in ("override", "scraped", "neither"):
        print(f"    {k:<12}{tally[k]:>10,}{100.0 * tally[k] / n:>8.1f}%")

    top = max(tally, key=tally.get)
    share = tally[top] / n
    print()
    if top == "override" and share >= 0.9:
        print("  => normalized_time WAS built with the overrides.\n"
              "     Run the passes WITH --as-if-wiped.")
        return "override"
    if top == "scraped" and share >= 0.9:
        print("  => normalized_time was built WITHOUT the overrides -- they "
              "never reached\n     the backfill. Run the passes WITHOUT "
              "--as-if-wiped; using it would\n     revert distances a second "
              "time and every proposal would be wrong.")
        return "scraped"
    print("  ⚠ NO CLEAR ANSWER. The corpus is mixed, or the distance spline "
          "has been\n    refit since the backfill ran. Do NOT run "
          "--as-if-wiped on this; re-run\n    05_backfill first so there is "
          "one answer.")
    return None

_CENSUS = """
WITH rated AS (
    SELECT r.meet_id, r.div_id,
           count(*)                                          AS n_rows,
           count(r.speed_rating)                             AS n_rated
    FROM   {table} r
    GROUP  BY 1, 2
),
src AS (
    SELECT o.meet_id, o.div_id, o.distance AS ovr, o.dict_name,
           -- ⚠ meets IS NOT UNIQUE ON (meet_id, div_id) -- a division can
           --   carry several rows. min() over the non-null distances is the
           --   same deterministic choice apply_tilt's `div` CTE makes.
           (SELECT min(m.distance) FROM meets m
             WHERE m.meet_id = o.meet_id AND m.div_id = o.div_id
               AND m.distance IS NOT NULL AND m.distance > 0) AS anet,
           (SELECT min(x.distance) FROM tmp_xc_tfrrs_dist x
             WHERE x.meet_id = o.meet_id AND x.div_id = o.div_id)
                                                              AS tfrrs
    FROM   dist_override o
)
SELECT s.meet_id, s.div_id, s.ovr, s.anet, s.tfrrs, s.dict_name,
       COALESCE(g.n_rows, 0)  AS n_rows,
       COALESCE(g.n_rated, 0) AS n_rated
FROM   src s
LEFT   JOIN rated g ON g.meet_id = s.meet_id AND g.div_id = s.div_id
"""

# How far apart two distances must be before the override is CHANGING
# anything. Scrapers record 5000 against 4989; that is not a correction.
SAME_TOL = 0.02


def classify(ovr, anet, tfrrs):
    """(kind, the distance a wipe would fall back to)."""
    base = anet if anet else tfrrs
    if not base:
        return "sole source", None
    if abs(ovr - base) / base <= SAME_TOL:
        return "agrees with the scrape", base
    return "correction", base


def main():
    ap = argparse.ArgumentParser(
        description="What a wipe of dist_override would cost, by kind.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--worst", type=int, default=25,
                    help="how many sole-source divisions to name")
    ap.add_argument("--tol", type=float, default=SAME_TOL)
    ap.add_argument("--verify", action="store_true",
                    help="recompute normalized_time at both candidate "
                         "distances to find out which one the backfill "
                         "actually used. This is what decides whether "
                         "rebuild_overrides --as-if-wiped is correct.")
    ap.add_argument("--sample", type=int, default=20_000,
                    help="rows to recompute for --verify (default 20,000)")
    args = ap.parse_args()

    print(f"\nWHAT A dist_override WIPE WOULD COST -- {args.sport}")

    used = None
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(_XC_TFRRS_DIST_SQL)
            cur.execute(_CENSUS.format(table=_TABLE[args.sport]))
            rows = cur.fetchall()
            if args.verify and rows:
                used = verify(cur, args.sport, args.tol, args.sample)

    if not rows:
        print("\n  dist_override is empty. Run engine/dump_overrides.py "
              "first -- it is NOT a pipeline step,\n  so the table can be "
              "older than corrections.py.")
        return 0

    kinds = {}
    sole = []
    moved = []
    for meet, div, ovr, anet, tfrrs, dict_name, n_rows, n_rated in rows:
        kind, base = classify(float(ovr), anet and float(anet),
                              tfrrs and float(tfrrs))
        slot = kinds.setdefault(kind, [0, 0, 0])
        slot[0] += 1
        slot[1] += n_rows
        slot[2] += n_rated
        if kind == "sole source":
            sole.append((n_rated, n_rows, meet, div, float(ovr), dict_name))
        elif kind == "correction":
            moved.append(abs(float(ovr) - base) / base)

    print(f"\n  {len(rows):,} divisions carry an override\n")
    print(f"  {'kind':<26}{'divisions':>12}{'rows':>12}{'rated':>12}")
    print("  " + "-" * 62)
    order = ["correction", "agrees with the scrape", "sole source"]
    for kind in order:
        if kind not in kinds:
            continue
        n, n_rows, n_rated = kinds[kind]
        print(f"  {kind:<26}{n:>12,}{n_rows:>12,}{n_rated:>12,}")

    if moved:
        moved.sort()
        mid = moved[len(moved) // 2]
        print(f"\n  corrections move the distance by a median "
              f"{mid:.1%} (tolerance for 'agrees' is {args.tol:.0%})")

    n_sole, rows_sole, rated_sole = kinds.get("sole source", (0, 0, 0))
    print("\n" + "=" * 68)
    if not n_sole:
        print("  A BLIND WIPE IS SAFE. Every override has a scraped distance\n"
              "  underneath it, so clearing them restores that value rather\n"
              "  than removing the only one.")
        print("=" * 68)
        return 0

    print(f"  ⚠ A BLIND WIPE WOULD UN-RATE {rated_sole:,} ROWS across "
          f"{n_sole:,} divisions.")
    print(f"    Those overrides are the ONLY distance those divisions have.\n"
          f"    Clearing them writes no normalized_time, so the engine rates\n"
          f"    nothing there -- and an unrated division is invisible to every\n"
          f"    rating-based audit, including all three rebuild passes.")
    print("=" * 68)

    sole.sort(reverse=True)
    print(f"\n  THE BIGGEST OF THEM\n")
    print(f"  {'meet':>10}{'div':>10}{'override':>10}{'rows':>9}{'rated':>9}"
          f"  written by")
    print("  " + "-" * 68)
    for n_rated, n_rows, meet, div, ovr, dict_name in sole[:args.worst]:
        print(f"  {meet:>10}{div:>10}{ovr:>10.0f}{n_rows:>9,}{n_rated:>9,}"
              f"  {dict_name}")

    print(f"\n  What to do: wipe the corrections and KEEP the sole sources --\n"
          f"    python scripts/wipe_overrides.py --keep-sole-source --write\n"
          f"  A sole source is not a decision the three passes can re-derive;\n"
          f"  it is the only record that division's distance has.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
