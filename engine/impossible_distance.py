"""
impossible_distance.py -- divisions whose LEADER beat the world record. READ ONLY.

    python engine/impossible_distance.py                 # report
    python engine/impossible_distance.py --sport XC --limit 60
    python engine/impossible_distance.py --propose       # also guess the truth

★ THE OWNER'S RULE, AND WHY IT CAN ACT WHERE THE EXISTING AUDIT CANNOT.
  backfill_normalize's suspect audit already flags a division whose MEDIAN
  normalized time sits under a per-pool floor (_AUDIT_FAST_FLOOR_5K), and
  calls it a `mislabel` when ~80% of the field is under it. That is a
  STATISTICAL test: "this division looks too fast for its pool." It is
  necessarily soft, which is why the override list above it says a human must
  confirm each one on athletic.net.

  This is a PHYSICAL test: "the winner ran faster than any human ever has, at
  the distance we think this was." No pool floor, no percentile, no judgement.
  A high schooler did not run 8046 m in 15:54.8 -- that is 3:11/mile for five
  miles, and the open world record pace for that distance is 4:10/mile. The
  time is fine. The DISTANCE is wrong.

⚠⚠ AND THE VERDICT IS ON THE DIVISION, NEVER ON THE ROW. This is the whole
   reason the existing row-level guards do not fix it. Measured on the
   reported race (8046 m stored, really 5000 m):

     real 5k    ratio vs WR   what the row-level pace band does
     15:54.8       0.765      dropped -- too fast
     18:00.0       0.865      RATED, as a 10:52 5k   <- still impossible
     20:00.0       0.961      RATED, as a 12:04 5k   <- still impossible
     22:00.0       1.057      RATED, as a 13:17 5k
     30:00.0       1.442      RATED, as an 18:07 5k

   A row-level filter on a DIVISION-level fault selects for the least
   detectable corruption: it removes exactly the rows whose wrongness is
   obvious and keeps every row whose wrongness is subtle -- including rows
   still faster than the world record. Those survivors then carry inflated
   ratings onto the boards AND vote on the venue's difficulty. If the
   distance is wrong, every row in the division is wrong, so the division is
   the unit.

! SO THIS ONE HAS NO FALSE POSITIVES, and that is the entire point. A division
  flagged here is not "suspicious"; it is arithmetically impossible, and the
  reason the existing note says mislabels "can't be auto-detected from the
  data" does not apply to it. What still cannot be auto-detected is the TRUE
  distance -- see --propose, which guesses only where the meet itself supplies
  a candidate.

⚠ THE BOUND IS THE OPEN WORLD RECORD, NOT A HIGH SCHOOL RECORD, DELIBERATELY.
  A tighter bound would catch more mislabels and would start being a judgement
  about who is plausible. The open record is the one line nobody argues with,
  so anything past it is a data fault by definition. Cross country is also
  ALWAYS slower than track over the same distance, so a track record used
  against an XC time is conservative twice over.
"""

import argparse
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# World-record anchors, track, seconds. Two points per sex fix a power law
# t = a * d^b, which is Riegel's relation with b measured rather than assumed.
#
# ! TWO ANCHORS, NOT ONE EXPONENT. A single anchor plus a textbook b = 1.06
#   would put the bound wherever that b happens to land at 8k. Fitting b from
#   the 5k and 10k records means the curve passes exactly through the two
#   distances nearest the range that actually matters here.
_WR = {
    "M": ((5000.0, 755.36), (10000.0, 1571.00)),    # 12:35.36 · 26:11.00
    "F": ((5000.0, 840.21), (10000.0, 1741.03)),    # 14:00.21 · 29:01.03
}

# How far past the record counts as impossible. 1.0 is the record itself.
# ★ EXACTLY 1.0, WITH NO SAFETY MARGIN, AND THE MARGIN IS NOT MISSING. A
#   margin would trade the property this test exists for -- certainty -- for a
#   handful of extra catches that the statistical audit already reports. If a
#   division is only 3% past the record, it belongs in the human triage queue,
#   not in an automatic action.
LIMIT_RATIO = 1.0

# How many of the fastest rows must be impossible before the DISTANCE is
# blamed rather than one bad time.
# ★ ONE IMPOSSIBLE ROW IS A TYPO; THREE IS A UNIT CONVERSION. A single
#   mistyped time in an otherwise sound division would flag it on the leader
#   alone, and dropping that division would discard real races over one bad
#   row. A wrong distance makes the whole front of the field impossible at
#   once -- the reported race has its top three at 0.765, 0.771, 0.792.
MIN_IMPOSSIBLE = 3


def _wrCurve(gender):
    (d1, t1), (d2, t2) = _WR.get(gender) or _WR["M"]
    b = math.log(t2 / t1) / math.log(d2 / d1)
    a = t1 / (d1 ** b)
    return a, b


def wrTime(distance_m, gender):
    """The open world-record time for this distance, by the fitted power law."""
    a, b = _wrCurve(gender)
    return a * (distance_m ** b)


_SQL = """
    WITH lead AS (
        SELECT r.meet_id, r.div_id,
               min(r.time_seconds)  AS best,
               (array_agg(r.time_seconds ORDER BY r.time_seconds))[1:5]
                                    AS fastest,
               count(*)             AS n
        FROM   {table} r
        WHERE  r.time_seconds IS NOT NULL
          AND  r.time_seconds > 0
          AND  r.time_seconds < 86400
        GROUP  BY r.meet_id, r.div_id
        HAVING count(*) >= %(minrows)s
    )
    SELECT l.meet_id, l.div_id, l.best, l.n,
           COALESCE(m.distance,
                    (mt.division_distances -> l.div_id::text
                       ->> 'distance')::real)          AS distance,
           COALESCE(m.division, '')                    AS division,
           COALESCE(m.meet_name, mt.meet_name, '')     AS meet_name
    FROM   lead l
    LEFT   JOIN LATERAL (
        SELECT distance, division, meet_name FROM meets mm
        WHERE  mm.meet_id = l.meet_id AND mm.div_id = l.div_id LIMIT 1
    ) m ON TRUE
    LEFT   JOIN LATERAL (
        SELECT division_distances, meet_name FROM meets_tfrrs t
        WHERE  t.meet_id = l.meet_id AND t.sport = 'XC' LIMIT 1
    ) mt ON TRUE
"""

# Other distances raced at the SAME meet -- the only honest source for a guess.
# Exactly propose_distances' rule: a proposal is corroborated by the corpus or
# it is not made.
# ⚠ WITH COUNTS, AND ORDERED BY THEM. The first version took the smallest
#   plausible sibling, which proposes 3200 m for a meet that runs both 3200 and
#   5000 -- plausible is not the same as right. propose_distances' rule is
#   CORROBORATION: the distance several other divisions actually raced. So the
#   candidate is the commonest plausible sibling, and a lone one-division
#   candidate is weak evidence that the report shows rather than hides.
_SIBLINGS = """
    SELECT m.distance, count(*) AS n
    FROM   meets m
    WHERE  m.meet_id = %(meet)s AND m.distance IS NOT NULL
      AND  m.div_id <> %(div)s
    GROUP  BY m.distance
    ORDER  BY count(*) DESC, m.distance DESC
"""


def _genderOf(division):
    d = (division or "").lower()
    if any(w in d for w in ("girl", "women", "female", " f ")):
        return "F"
    return "M"


def main():
    ap = argparse.ArgumentParser(
        description="Divisions whose winner beat the world record (READ ONLY).")
    ap.add_argument("--sport", default="XC", choices=["XC", "TF"])
    ap.add_argument("--min-rows", type=int, default=5)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--propose", action="store_true",
                    help="guess the true distance from sibling divisions")
    ap.add_argument("--emit", metavar="FILE", default=None,
                    help="write a corrections.py-ready block to FILE "
                         "(a PROPOSAL -- never appended to corrections.py)")
    args = ap.parse_args()

    from database import getConn
    from psycopg2.extras import RealDictCursor

    table = "results" if args.sport == "XC" else "results_tf"
    with getConn() as conn:
        with conn.cursor(name="impossible",
                         cursor_factory=RealDictCursor) as cur:
            cur.itersize = 50_000
            cur.execute(_SQL.format(table=table),
                        {"minrows": args.min_rows})
            bad, seen = [], 0
            for r in cur:
                seen += 1
                d = r["distance"]
                if not d or d <= 0 or not r["best"]:
                    continue
                g = _genderOf(r["division"])
                wr = wrTime(float(d), g)
                n_imp = sum(1 for t in (r["fastest"] or [])
                            if t and float(t) / wr < LIMIT_RATIO)
                if n_imp == 0:
                    continue
                r.update(wr=wr, ratio=float(r["best"]) / wr, gender=g,
                         n_imp=n_imp,
                         verdict=("DISTANCE" if n_imp >= MIN_IMPOSSIBLE
                                  else "one-row"))
                bad.append(r)

        bad.sort(key=lambda x: x["ratio"])
        dist = [r for r in bad if r["verdict"] == "DISTANCE"]
        rows = [r for r in bad if r["verdict"] == "one-row"]
        print(f"\n{len(bad):,} of {seen:,} divisions have an impossible row.")
        print(f"  {len(dist):,} have {MIN_IMPOSSIBLE}+ impossible in the top 5 "
              f"-> the DISTANCE is wrong, the whole division is void")
        print(f"  {len(rows):,} have 1-2 -> most likely ONE mistyped time; "
              f"left alone\n")

        overrides, drops = [], []
        with conn.cursor() as sib:
            for r in dist[:args.limit]:
                mm, ss = divmod(float(r["best"]), 60)
                line = (f"  {r['ratio']:.3f}  {r['meet_id']}/{r['div_id']:<6} "
                        f"{float(r['distance']):>7.0f}m  "
                        f"{int(mm):>3}:{ss:04.1f}  {r['n_imp']}/5 imp  "
                        f"n={r['n']:<4} {r['gender']}  "
                        f"{r['meet_name'][:32]}")
                ok = []
                if args.propose or args.emit:
                    sib.execute(_SIBLINGS, {"meet": r["meet_id"],
                                            "div": r["div_id"]})
                    # Already ordered by how many divisions raced it.
                    cands = [(float(x[0]), int(x[1])) for x in sib.fetchall()]
                    # A candidate must make EVERY one of the fastest five
                    # possible -- not merely the leader. Same reason the
                    # verdict is on the division.
                    ok = [(c, k) for c, k in cands
                          if all(float(t) / wrTime(c, r["gender"]) >= 1.0
                                 for t in (r["fastest"] or []) if t)]
                    line += ("   -> " + ", ".join(f"{c:.0f}m x{k}"
                                                  for c, k in ok)
                             if ok else "   -> no plausible sibling")
                print(line)
                if ok:
                    overrides.append((r["meet_id"], r["div_id"], ok[0][0],
                                      float(r["distance"]), ok[0][1]))
                else:
                    drops.append((r["meet_id"], r["div_id"],
                                  float(r["distance"]), r["ratio"]))

    if args.emit:
        _emit(args.emit, args.sport, overrides, drops)

    print(f"\n! NOTHING WAS WRITTEN TO corrections.py. A flagged division "
          f"needs its DISTANCE\n  corrected, or the division dropped -- the "
          f"times are real, the metre count is not.")


# _emit
# Purpose:   a paste-ready proposal file, NOT an append to corrections.py.
# Detail:
#   ★ A PROPOSAL, AND DELIBERATELY NOT A --write. The rule this project
#     learned the hard way is that a write goes AFTER a human has read the
#     dry run; diag_suspects already emits its drop list the same way. A
#     distance override is also a claim about what a race really was, and
#     that claim deserves one look before it moves 62M rows.
#
#   ! OVERRIDE BEATS DROP WHEREVER A SIBLING SUPPLIES ONE. Dropping a
#     division discards real races; correcting its distance keeps them and
#     makes them right. Drop is only for the ones nothing at the meet can
#     identify.
def _emit(path, sport, overrides, drops):
    sp = sport.upper()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# generated by engine/impossible_distance.py -- PROPOSAL\n")
        f.write(f"# Divisions whose fastest rows beat the open world record\n")
        f.write(f"# for their stored distance. Review, then paste into\n")
        f.write(f"# engine/corrections.py. Nothing here is applied.\n\n")
        f.write(f"# {len(overrides)} with a plausible distance from another "
                f"division at the same meet:\n")
        f.write(f"_DISTANCE_OVERRIDES_{sp}.update({{\n")
        for meet, div, true_d, was, k in sorted(overrides):
            f.write(f"    ({meet}, {div}): {true_d:.0f}.0,"
                    f"   # was {was:.0f}m; {k} sibling div(s) race {true_d:.0f}m\n")
        f.write("})\n\n")
        f.write(f"# {len(drops)} with nothing at the meet to identify them:\n")
        f.write(f"_DISTANCE_DROP_{sp}.update({{\n")
        for meet, div, was, ratio in sorted(drops):
            f.write(f"    ({meet}, {div}),"
                    f"   # {was:.0f}m stored, leader at {ratio:.3f} of WR\n")
        f.write("})\n")
    print(f"\n[emit] wrote {path}: {len(overrides)} overrides, "
          f"{len(drops)} drops -- REVIEW BEFORE PASTING")


if __name__ == "__main__":
    main()
