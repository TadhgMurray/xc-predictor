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
_SIBLINGS = """
    SELECT DISTINCT m.distance
    FROM   meets m
    WHERE  m.meet_id = %(meet)s AND m.distance IS NOT NULL
      AND  m.div_id <> %(div)s
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
            bad = []
            seen = 0
            for r in cur:
                seen += 1
                d = r["distance"]
                if not d or d <= 0 or not r["best"]:
                    continue
                g = _genderOf(r["division"])
                wr = wrTime(float(d), g)
                ratio = float(r["best"]) / wr
                if ratio < LIMIT_RATIO:
                    r["wr"] = wr
                    r["ratio"] = ratio
                    r["gender"] = g
                    bad.append(r)

        bad.sort(key=lambda x: x["ratio"])
        print(f"\n{len(bad):,} divisions of {seen:,} have a winner FASTER than "
              f"the open world record for their stored distance")
        print("  ratio < 1.000 is impossible; lower is more impossible\n")

        with conn.cursor() as sib:
            for r in bad[:args.limit]:
                mm, ss = divmod(float(r["best"]), 60)
                line = (f"  {r['ratio']:.3f}  {r['meet_id']}/{r['div_id']:<6} "
                        f"{float(r['distance']):>7.0f}m  "
                        f"{int(mm):>3}:{ss:04.1f}  n={r['n']:<4} "
                        f"{r['gender']}  {r['meet_name'][:34]}")
                if args.propose:
                    sib.execute(_SIBLINGS, {"meet": r["meet_id"],
                                            "div": r["div_id"]})
                    cands = sorted({float(x[0]) for x in sib.fetchall()})
                    ok = [c for c in cands
                          if float(r["best"]) / wrTime(c, r["gender"]) >= 1.0]
                    line += ("   -> " + ", ".join(f"{c:.0f}m" for c in ok)
                             if ok else "   -> no plausible sibling")
                print(line)

    print(f"\n! NOTHING WAS WRITTEN. A flagged division needs its distance "
          f"corrected in\n  corrections.py, not its rows hidden -- the times "
          f"are real, the metre count is not.")


if __name__ == "__main__":
    main()
