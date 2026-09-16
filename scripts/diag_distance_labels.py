#!/usr/bin/env python3
"""
Is a college 8K recorded as an 8K, or as a 5K?

Owner, 2026-09-16: "why do ppl predct so fast it's predicting an 8k adn knows
it right? (is it bcs of most 8ks they run are mislableed 5ks?)"

★ THE PREDICTION IS NEVER A RACE TIME. predict._predictTimes returns a
  NORMALIZED time -- normalized_time = raw * (5000/d)**k, difficulty divided
  out -- and the page prints it straight. So the number on the card is a
  flat-5K equivalent, and it can only look like a plausible 8K time if the
  corpus rows behind it are themselves raw 8K times. That happens exactly
  when d is recorded as 5000 for a race that was 8000: the factor is
  (5000/5000)**k = 1 and nothing is normalized.

⚠ SO THIS SCRIPT NEVER ASSUMES, IT COUNTS. Three questions, cheapest first:

    1. What distances does the corpus actually record for college XC?
    2. For the meet in question, what distance is on the row -- and does
       dist_override disagree with the scrape?
    3. For one athlete, raw time beside normalized_time. If the ratio is ~1.0
       on a race that was 8000 m, the label is wrong and the model is
       learning raw 8K times as though they were 5K equivalents.

Usage:
    python3 scripts/diag_distance_labels.py                  # 1 only
    python3 scripts/diag_distance_labels.py --meet 271911    # 1 + 2
    python3 scripts/diag_distance_labels.py --person 123456  # 1 + 3
    python3 scripts/diag_distance_labels.py --skip-sample --person 123456
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("racecast", "scripts", "engine", "model"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import psycopg2.extras                                        # noqa: E402
from database import getConn                                  # noqa: E402


def mmss(s):
    if s is None:
        return "--"
    s = float(s)
    return f"{int(s // 60)}:{s % 60:04.1f}"


# Purpose:   what distances college XC rows actually carry.
# ! A SAMPLE, AND IT SAYS SO. results is 54M rows and joining all of them to
#   meets to count distances would take minutes for a question a sample
#   answers exactly as well -- a distribution is the one thing sampling is
#   honest about. The decisive check is (3), which is exact.
# ! rating_pool IS ON THE ROW. ranking_results carries a pool too, but it is
#   61M rows and this needs no second join.
def distances(cur, pct):
    print(f"\n1. RECORDED DISTANCE BY LEVEL  (XC, {pct}% sample of results)")
    cur.execute(f"""
        SELECT split_part(split_part(r.rating_pool, '|', 1), '_', 1) AS lvl,
               (round(m.distance / 100.0) * 100)::int                AS dist,
               count(*)                                              AS n
        FROM   results r TABLESAMPLE SYSTEM ({float(pct)})
        JOIN   meets   m ON m.meet_id = r.meet_id
                        AND m.div_id  = r.div_id
                        AND m.source  = r.source
        WHERE  r.rating_pool IS NOT NULL
          AND  m.distance IS NOT NULL
        GROUP  BY 1, 2
    """)
    rows = [dict(r) for r in cur.fetchall()]
    tot = {}
    for r in rows:
        tot[r["lvl"]] = tot.get(r["lvl"], 0) + r["n"]
    for lvl in sorted(tot, key=lambda k: -tot[k]):
        print(f"\n   {lvl}   ({tot[lvl]:,} sampled rows)")
        mine = sorted((r for r in rows if r["lvl"] == lvl),
                      key=lambda r: -r["n"])
        for r in mine[:8]:
            pctg = 100.0 * r["n"] / tot[lvl]
            if pctg < 0.5:
                continue
            print(f"     {r['dist']:>6} m  {r['n']:>9,}  {pctg:5.1f}%")
    print("\n   ⚠ 5000 m as the modal COLLEGE MEN distance would be the bug the")
    print("     owner guessed: college men race 8K, so those rows are raw 8K")
    print("     times wearing a 5K label, and nothing normalizes them.")


# Purpose:   the distance ON the meet, and whether anything overrides it.
def oneMeet(cur, meet_id):
    print(f"\n2. MEET {meet_id}")
    cur.execute("""
        SELECT m.div_id, m.division, m.distance, m.course_name, m.source,
               d.distance AS override
        FROM   meets m
        LEFT   JOIN dist_override d ON d.meet_id = m.meet_id
                                   AND d.div_id  = m.div_id
        WHERE  m.meet_id = %s
        ORDER  BY m.div_id
    """, (meet_id,))
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        print("   no such meet")
        return
    for r in rows:
        flag = ""
        if r["override"] and r["distance"] and \
                abs(float(r["override"]) - float(r["distance"])) > 50:
            flag = f"   <-- OVERRIDDEN to {r['override']}"
        print(f"   div {str(r['div_id']):>4}  {str(r['division'] or '')[:28]:<28}"
              f" dist={r['distance']}  override={r['override']}"
              f"  {r['course_name'] or ''}{flag}")


# Purpose:   raw time beside normalized_time for one athlete.
# ★ THE RATIO IS THE WHOLE ANSWER, and it is exact -- one athlete, twelve
#   rows, no sampling. normalized/raw is ~0.66 for a true 8K (the distance
#   factor at (5/8)**k) and ~1.00 for a true 5K. A 24-minute college race
#   with a ratio of 1.00 is an 8K the corpus thinks was a 5K, and the model's
#   baseline for that athlete is then a raw 8K time.
def onePerson(cur, person_id):
    print(f"\n3. PERSON {person_id}  (most recent XC races)")
    cur.execute("""
        SELECT r.meet_id, r.div_id, r.rating_pool, r.date, r.time_seconds,
               r.normalized_time, r.speed_rating,
               m.distance, m.course_name,
               COALESCE(d.distance, m.distance) AS used
        FROM   results r
        LEFT   JOIN meets m ON m.meet_id = r.meet_id
                           AND m.div_id  = r.div_id
                           AND m.source  = r.source
        LEFT   JOIN dist_override d ON d.meet_id = r.meet_id
                                   AND d.div_id  = r.div_id
        WHERE  r.person_id = %s
        ORDER  BY r.date DESC
        LIMIT  12
    """, (person_id,))
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        print("   no rows")
        return
    print(f"   {'date':<12}{'dist':>7}{'raw':>10}{'norm':>10}"
          f"{'n/raw':>7}{'rating':>8}  pool / course")
    for r in rows:
        raw, nt = r["time_seconds"], r["normalized_time"]
        ratio = (float(nt) / float(raw)) if raw and nt else None
        flag = ""
        # A 5000-labelled race longer than ~19 minutes at college level is the
        # signature: nothing was corrected, because nothing looked wrong.
        if (ratio is not None and abs(ratio - 1.0) < 0.03 and raw
                and float(raw) > 1150
                and str(r["rating_pool"] or "").startswith("college")):
            flag = "   <-- raw 8K wearing a 5K label?"
        print(f"   {str(r['date'])[:10]:<12}{str(r['used'] or '?'):>7}"
              f"{mmss(raw):>10}{mmss(nt):>10}"
              f"{(f'{ratio:.3f}' if ratio else '--'):>7}"
              f"{(r['speed_rating'] or 0):>8.1f}  "
              f"{str(r['rating_pool'] or '?')[:10]} "
              f"{(r['course_name'] or '')[:22]}{flag}")
    print("\n   ★ n/raw ~0.66 = a true 8K, normalized down to a 5K equivalent.")
    print("   ⚠ n/raw ~1.00 on a 24-minute college race = the distance label "
          "is wrong,\n     and the model trained on raw 8K times as 5K "
          "equivalents.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meet", type=int)
    ap.add_argument("--person", type=int)
    ap.add_argument("--sample", type=float, default=0.5,
                    help="percent of results to sample for (1). Default 0.5.")
    ap.add_argument("--skip-sample", action="store_true")
    a = ap.parse_args()

    with getConn() as c:
        with c.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not a.skip_sample:
                distances(cur, a.sample)
            if a.meet:
                oneMeet(cur, a.meet)
            if a.person:
                onePerson(cur, a.person)
    print()


if __name__ == "__main__":
    main()
