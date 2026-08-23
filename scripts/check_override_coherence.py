"""Do the distances we just wrote agree with each other? READ ONLY.

    python scripts/check_override_coherence.py
    python scripts/check_override_coherence.py --worst 40
    python scripts/check_override_coherence.py --venue "Farragut"

Run from the PROJECT ROOT.

★ THE ONE CHECK THAT WORKS BEFORE THE REBUILD, AND THAT IS WHY IT EXISTS.

  Every other override audit measures a written distance against the RATINGS
  -- audit_distance_overrides, anchor_check, the passes themselves. None of
  them can be trusted between `dump_overrides` and `05_backfill`, because the
  ratings on disk were still computed at the OLD distances. Running them in
  that window measures the new overrides against the old ratings and answers
  a question nobody asked.

  This one asks something the ratings are not needed for: do the overrides
  AGREE WITH EACH OTHER? A venue runs one course. Sibling divisions at that
  venue, in the same season, racing the same course, have one distance. If
  the passes just gave them five, at least four are wrong -- and that is
  knowable now, for the cost of a query, rather than four hours from now.

⚠ THE CASE THIS WAS BUILT FROM. Farragut State Park, 17 divisions, every one
  labelled 2993 m, and pass 1 proposed five different answers: 5000 (x4),
  4828 (x1), 4715 (x6), 4506 (x2), 4425 (x4). The implied distances scattered
  4430-5171 across fields of 5-34 rows, and each one snapped to whichever
  rung was nearest.

  The mechanism is snapToCourse. It excludes the division's OWN label before
  looking at what the venue runs -- but not its SIBLINGS' labels, and at a
  venue where many divisions are mislabelled in different ways, the wrong
  labels corroborate each other. Course-first is right in general; it has no
  defence against a venue that is wrong in more than one way at once.

★ AND THE SECOND COLUMN IS WHY IT MATTERS. 4425, 4506 and 4715 are 2.75, 2.8
  and 2.93 miles -- arithmetic, not race distances. The corpus ladder carries
  them because a few thousand mislabelled finishers is over the floor. Beside
  the number of finishers the corpus actually races at each, a venue whose
  divisions split across three of them stops looking like a close call.

! SPLIT IS NOT AUTOMATICALLY WRONG. Van Cortlandt genuinely runs 2500, 4023,
  5000 and more; an invitational hosts a 5 km varsity and a 3 km middle
  school on one afternoon. So this ranks rather than condemns, and it counts
  DIVISIONS behind each distance -- one division disagreeing with sixteen is
  a different shape from eight disagreeing with nine.
"""
import argparse
import os
import sys

from psycopg2.extras import RealDictCursor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from database import getConn                                    # noqa: E402

# A venue's distances this close are the same decision. Matches
# compare_overrides.SAME_TOL: 3218 vs 3200 is 0.6 percent and the ladder's
# tightest real neighbours sit under 1 percent apart.
SAME_TOL = 0.01

# How many finishers the corpus must race at a distance before it counts as a
# distance the sport actually runs. Same floor as rebuild_overrides.
LADDER_MIN_N = 5_000

# ★ SPLIT AND SUSPECT ARE DIFFERENT QUESTIONS, AND CONFLATING THEM BURIES THE
#   ANSWER. Van Cortlandt Park really does run 2500, 4023 and 5000; its
#   overrides splitting three ways is the truth, not a fault. Farragut's split
#   across 5000 / 4828 / 4715 / 4506 / 4425 is not, and the difference is not
#   the number of answers -- both look the same from the count alone.
#
#   It is POPULARITY. Van Cortlandt's three answers are each raced by millions
#   of finishers corpus-wide. Farragut's 4715 and 4506 are raced by a few
#   thousand and 4425 by nobody -- they are 2.93, 2.8 and 2.75 miles, which is
#   arithmetic rather than a race. So a minority answer counts as SUSPECT when
#   the corpus races it less than this share of what it races the venue's
#   leading answer, and the report leads on that instead of on the split.
SUSPECT_SHARE = 0.01

_VENUE_SQL = """
DROP TABLE IF EXISTS coh_ovr;
CREATE TEMP TABLE coh_ovr AS
SELECT o.meet_id, o.div_id, o.distance,
       COALESCE(
           (SELECT m.course_name FROM meets m
             WHERE m.meet_id = o.meet_id AND m.div_id = o.div_id
               AND m.course_name IS NOT NULL LIMIT 1),
           (SELECT t.venue_name FROM meets_tfrrs t
             WHERE t.meet_id = o.meet_id AND t.venue_name IS NOT NULL LIMIT 1)
       ) AS venue,
       COALESCE(
           (SELECT m.meet_name FROM meets m
             WHERE m.meet_id = o.meet_id AND m.meet_name IS NOT NULL LIMIT 1),
           (SELECT t.meet_name FROM meets_tfrrs t
             WHERE t.meet_id = o.meet_id AND t.meet_name IS NOT NULL LIMIT 1)
       ) AS meet_name
FROM   dist_override o
WHERE  o.distance IS NOT NULL AND o.distance > 0;
CREATE INDEX ON coh_ovr (venue);
ANALYZE coh_ovr;
"""

# ! COUNTED BY FINISHERS ACROSS THE WHOLE CORPUS, not by divisions and not at
#   this venue. The question is "does the sport race this distance", and a
#   venue's own mislabelled rows are exactly what must not be allowed to
#   answer it.
_LADDER_SQL = """
SELECT round(m.distance)::int AS d, count(*) AS n
FROM   meets m
JOIN   results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
                AND r.source = m.source
WHERE  m.distance IS NOT NULL AND m.distance > 0
GROUP  BY 1
HAVING count(*) >= %(min_n)s
"""


def loadPopularity(cur, min_n=LADDER_MIN_N):
    """{distance: finishers} for every distance the corpus really races."""
    cur.execute(_LADDER_SQL, {"min_n": min_n})
    return {int(r["d"]): int(r["n"]) for r in cur.fetchall()}


def popularityOf(pop, d, tol=SAME_TOL):
    """Finishers at the nearest rung within tol, or 0. The written distance is
    a snap TARGET, so it will not be the exact integer key."""
    best = 0
    for k, n in pop.items():
        if abs(k - d) / float(d) <= tol and n > best:
            best = n
    return best


def cluster(values, tol=SAME_TOL):
    """[(distance, n_divisions)] with near-identical distances merged.

    Merged on the LARGEST member, not the mean: 3200 and 3218 are one decision
    written two ways, and reporting 3209 would name a distance nobody chose.
    """
    out = []
    items = sorted(values.items())
    for d, n in items:
        for i, (bd, bn, members) in enumerate(out):
            if abs(bd - d) / float(max(bd, d)) <= tol:
                keep = bd if bn >= n else d
                out[i] = (keep, bn + n, members + [d])
                break
        else:
            out.append((d, n, [d]))
    return sorted(((d, n) for d, n, _ in out), key=lambda x: -x[1])


def main():
    ap = argparse.ArgumentParser(
        description="Do the written distance overrides agree with each other?")
    ap.add_argument("--worst", type=int, default=25,
                    help="how many split venues to print (default 25)")
    ap.add_argument("--venue", default=None,
                    help="only venues whose name contains this (case "
                         "insensitive) -- prints every division")
    ap.add_argument("--min-divisions", type=int, default=2, dest="min_div",
                    help="ignore venues with fewer overridden divisions")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_VENUE_SQL)
            pop = loadPopularity(cur)
            cur.execute("SELECT count(*) AS n, count(venue) AS named "
                        "FROM coh_ovr")
            row = cur.fetchone()
            print(f"\n  {row['n']:,} distance overrides in dist_override "
                  f"({row['named']:,} with a venue name)")
            print(f"  {len(pop):,} distances the corpus races "
                  f"({LADDER_MIN_N:,}+ finishers each)\n")

            cur.execute("SELECT venue, distance, meet_id, div_id, meet_name "
                        "FROM coh_ovr WHERE venue IS NOT NULL")
            byVenue = {}
            for r in cur.fetchall():
                byVenue.setdefault(r["venue"], []).append(r)

    # ---------------------------------------------------------------- #
    #  HOW MUCH OF WHAT WE WROTE IS A DISTANCE ANYONE RACES?
    # ---------------------------------------------------------------- #
    allRows = [r for rows in byVenue.values() for r in rows]
    onLadder = sum(1 for r in allRows if popularityOf(pop, float(r["distance"])))
    if allRows:
        print(f"  ON A REAL RUNG   {onLadder:,} of {len(allRows):,} "
              f"({onLadder / len(allRows):.1%}) landed on a distance the "
              f"corpus races")
        print(f"                   the rest are snap targets carried by the "
              f"ladder's floor alone\n")

    if args.venue:
        want = args.venue.lower()
        hits = {v: rows for v, rows in byVenue.items() if want in v.lower()}
        if not hits:
            print(f"  nothing matched {args.venue!r}.\n")
            return 0
        for v, rows in sorted(hits.items()):
            print(f"  {v}  ({len(rows)} overridden divisions)")
            for r in sorted(rows, key=lambda x: -float(x["distance"])):
                d = float(r["distance"])
                n = popularityOf(pop, d)
                mark = f"{n:>11,} finishers" if n else "  NOT A RUNG"
                print(f"    {d:>7,.0f}  {mark}   {r['meet_id']}/{r['div_id']}"
                      f"  {(r['meet_name'] or '')[:40]}")
            print()
        return 0

    # ---------------------------------------------------------------- #
    #  THE SPLIT VENUES
    # ---------------------------------------------------------------- #
    split = []
    for v, rows in byVenue.items():
        if len(rows) < args.min_div:
            continue
        counts = {}
        for r in rows:
            counts[float(r["distance"])] = counts.get(float(r["distance"]), 0) + 1
        merged = cluster(counts)
        if len(merged) < 2:
            continue
        # ! RANKED BY THE MINORITY, NOT THE NUMBER OF ANSWERS. Sixteen
        #   divisions at 5000 and one at 4828 is one bad division; eight and
        #   nine is a venue nobody has a reading on. The second is worse and
        #   this sorts it higher.
        minority = sum(n for _, n in merged[1:])
        top = max(popularityOf(pop, d) for d, _ in merged) or 1
        suspect = sum(n for d, _n in merged
                      for n in [_n]
                      if popularityOf(pop, d) < SUSPECT_SHARE * top)
        split.append((suspect, minority, len(merged), v, merged, len(rows)))
    # ★ SUSPECT FIRST. A venue that split onto distances nobody races is
    #   actionable; one that split onto three real ones is probably just a
    #   venue that runs three races.
    split.sort(key=lambda x: (-x[0], -x[1], -x[2]))

    if not split:
        print("  NO SPLIT VENUES. Every venue's overridden divisions agree "
              "on one distance.\n")
        return 0

    tot_min = sum(s[1] for s in split)
    tot_sus = sum(s[0] for s in split)
    n_sus = sum(1 for s in split if s[0])
    print(f"  {len(split):,} VENUES WHERE THE OVERRIDES DISAGREE WITH EACH "
          f"OTHER\n  {tot_min:,} divisions sit on the minority answer at "
          f"their own venue\n")
    print(f"  OF THOSE, {n_sus:,} venues and {tot_sus:,} divisions landed on "
          f"a distance the corpus\n  barely races "
          f"(under {SUSPECT_SHARE:.0%} of what it races the venue's leading "
          f"answer).\n  Those are the ones to read; a venue that split onto "
          f"three real distances\n  is probably a venue that runs three "
          f"races.\n")
    print(f"    {'susp':>5} {'divs':>5} {'answers':>8}  venue")
    print(f"    {'-' * 72}")
    for suspect, minority, nans, v, merged, ndiv in split[:args.worst]:
        print(f"    {suspect:>5} {minority:>5} {nans:>8}  {v[:48]}  "
              f"({ndiv} divisions)")
        top = max(popularityOf(pop, d) for d, _ in merged) or 1
        for d, n in merged:
            p = popularityOf(pop, d)
            flag = "  <-- barely raced" if p < SUSPECT_SHARE * top else ""
            tag = f"{p:>11,} finishers corpus-wide" if p else "  NOT A RUNG"
            print(f"            {d:>7,.0f} x{n:<4} {tag}{flag}")
    if len(split) > args.worst:
        print(f"\n    ... {len(split) - args.worst:,} more. --worst raises "
              f"the cut.")
    print(f"\n  --venue NAME prints every division at one venue.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
