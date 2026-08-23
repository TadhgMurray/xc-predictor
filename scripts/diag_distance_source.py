"""Where did this division's distance come from, and does the field agree?

    python scripts/diag_distance_source.py --pool college_m --distance 6000
    python scripts/diag_distance_source.py --pool college_m            # all labels
    python scripts/diag_distance_source.py --meet 26359
    python scripts/diag_distance_source.py --pool college_f --distance 6000 --worst 40

Run from the PROJECT ROOT. READ ONLY.

★ THE QUESTION IS WHO WROTE THE NUMBER, and there are exactly three answers:

    override   dist_override -- a decision in corrections.py. If the label is
               wrong, WE wrote it wrong, and the fix is an override.
    anet       meets.distance, as scraped. The fix is an override on top.
    tfrrs      meets_tfrrs.division_distances, as scraped. Same.

  Every reader in the project resolves them in that order, so this reports the
  one that actually won.

★ AND WHETHER THE FIELD BELIEVES IT. The winner's time is printed beside the
  label because a men's college 6 km winner runs about 17:30-18:30, and the
  world best for 6 km is a shade over 16:30. A 6 km division whose winner ran
  14:56 is not a fast race; it is a 5 km with the wrong number on it, and no
  rating model is needed to see that.

⚠ THIS IS A DIAGNOSTIC, NOT A GATE. Nothing here decides a distance -- it says
  which source to go and argue with. The distance itself is still derived from
  speed ratings, in rebuild_overrides and find_dropped_divisions.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from psycopg2.extras import RealDictCursor                # noqa: E402
from database import getConn                              # noqa: E402
from build_ranking_results import _XC_TFRRS_DIST_SQL      # noqa: E402

_SQL = """
WITH div AS (
    SELECT r.meet_id, r.div_id,
           count(*)                       AS n_rows,
           count(r.speed_rating)          AS n_rated,
           min(r.time_seconds)            AS win_s,
           max(r.speed_rating)            AS top_rating,
           avg(r.speed_rating)            AS avg_rating,
           min(r.date)                    AS date
    FROM   results r
    GROUP  BY 1, 2
    HAVING count(*) >= %(min_rows)s
),
pool AS (
    SELECT k.meet_id, k.div_id,
           mode() WITHIN GROUP (ORDER BY k.pool) AS pool
    FROM   ranking_results k
    WHERE  k.sport = 'XC'
    GROUP  BY 1, 2
)
SELECT d.meet_id, d.div_id, d.n_rows, d.n_rated, d.win_s, d.top_rating,
       d.avg_rating, d.date, p.pool,
       dov.distance                                   AS d_override,
       dov.dict_name                                  AS ovr_dict,
       a.distance                                     AS d_anet,
       t.distance                                     AS d_tfrrs,
       COALESCE(dov.distance, a.distance, t.distance) AS label,
       CASE WHEN dov.distance IS NOT NULL THEN 'override'
            WHEN a.distance   IS NOT NULL THEN 'anet'
            WHEN t.distance   IS NOT NULL THEN 'tfrrs'
            ELSE 'none' END                           AS src,
       COALESCE(a.course_name, tm.venue_name)         AS course_name,
       tm.meet_name                                   AS meet_name
FROM   div d
LEFT   JOIN pool p ON p.meet_id = d.meet_id AND p.div_id = d.div_id
LEFT   JOIN dist_override dov
       ON dov.meet_id = d.meet_id AND dov.div_id = d.div_id
LEFT   JOIN LATERAL (SELECT course_name, distance FROM meets m
                      WHERE m.meet_id = d.meet_id AND m.div_id = d.div_id
                      LIMIT 1) a ON TRUE
LEFT   JOIN LATERAL (SELECT distance FROM tmp_xc_tfrrs_dist x
                      WHERE x.meet_id = d.meet_id AND x.div_id = d.div_id
                      LIMIT 1) t ON TRUE
LEFT   JOIN LATERAL (SELECT venue_name, meet_name FROM meets_tfrrs mt
                      WHERE mt.meet_id = d.meet_id LIMIT 1) tm ON TRUE
WHERE  TRUE
  {pool_filter}
  {meet_filter}
"""


def mmss(sec):
    if not sec:
        return "-"
    sec = float(sec)
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser(
        description="Which source supplied a division's distance.")
    ap.add_argument("--pool", default=None,
                    help="college_m, college_f, hs_m, ...")
    ap.add_argument("--distance", type=float, default=None,
                    help="only divisions whose resolved label is this")
    ap.add_argument("--meet", type=int, default=None)
    ap.add_argument("--min-rows", type=int, default=20, dest="min_rows")
    ap.add_argument("--worst", type=int, default=25)
    args = ap.parse_args()

    sql = _SQL.format(
        pool_filter="AND p.pool = %(pool)s" if args.pool else "",
        meet_filter="AND d.meet_id = %(meet)s" if args.meet else "")
    params = {"min_rows": args.min_rows}
    if args.pool:
        params["pool"] = args.pool
    if args.meet:
        params["meet"] = args.meet

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_XC_TFRRS_DIST_SQL)
            cur.execute(sql, params)
            rows = cur.fetchall()

    if args.distance is not None:
        rows = [r for r in rows if r["label"]
                and abs(float(r["label"]) - args.distance) < 1.0]
    if not rows:
        print("\n  nothing matched.\n")
        return 0

    where = (f"pool {args.pool}" if args.pool else "all pools")
    if args.distance:
        where += f", label {args.distance:.0f} m"
    if args.meet:
        where += f", meet {args.meet}"
    print(f"\nWHO SUPPLIED THE DISTANCE -- {where}, "
          f"{args.min_rows}+ finishers\n")

    # ---- 1. the source breakdown ---------------------------------- #
    by_src = {}
    for r in rows:
        slot = by_src.setdefault(r["src"], [0, 0, []])
        slot[0] += 1
        slot[1] += r["n_rows"]
        if r["win_s"]:
            slot[2].append(float(r["win_s"]))
    print(f"  {len(rows):,} divisions\n")
    print(f"  {'source':<12}{'divisions':>11}{'finishers':>12}"
          f"{'median winner':>16}")
    print("  " + "-" * 51)
    for src in ("override", "anet", "tfrrs", "none"):
        if src not in by_src:
            continue
        n, rowsn, wins = by_src[src]
        med = sorted(wins)[len(wins) // 2] if wins else None
        print(f"  {src:<12}{n:>11,}{rowsn:>12,}{mmss(med):>16}")

    # ---- 2. which dict, when the source is us --------------------- #
    dicts = {}
    for r in rows:
        if r["src"] == "override":
            dicts[r["ovr_dict"]] = dicts.get(r["ovr_dict"], 0) + 1
    if dicts:
        print(f"\n  the overrides came from:")
        for name, n in sorted(dicts.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>7,}  {name}")

    # ---- 3. what the scrape said underneath ----------------------- #
    moved = [r for r in rows if r["src"] == "override"
             and (r["d_anet"] or r["d_tfrrs"])
             and abs(float(r["d_override"])
                     - float(r["d_anet"] or r["d_tfrrs"])) > 1.0]
    if moved:
        print(f"\n  {len(moved):,} of the overrides CHANGED a scraped value:")
        pairs = {}
        for r in moved:
            was = float(r["d_anet"] or r["d_tfrrs"])
            key = (was, float(r["d_override"]))
            pairs[key] = pairs.get(key, 0) + 1
        for (was, now), n in sorted(pairs.items(), key=lambda kv: -kv[1])[:12]:
            print(f"    {n:>7,}  {was:>6.0f} -> {now:>6.0f}")

    # ---- 4. the fastest winners, which is where a wrong label shows #
    rows.sort(key=lambda r: float(r["win_s"] or 1e9))
    print(f"\n\n  FASTEST WINNERS AT THIS LABEL -- a label too long makes "
          f"the winner look impossible\n")
    print(f"  {'winner':>8}{'label':>7}{'rows':>6}{'rated':>6}{'top':>7}"
          f"{'src':>10}  meet/div  course")
    for r in rows[:args.worst]:
        print(f"  {mmss(r['win_s']):>8}{(r['label'] or 0):>7.0f}"
              f"{r['n_rows']:>6}{r['n_rated']:>6}"
              f"{(r['top_rating'] or 0):>7.1f}{r['src']:>10}"
              f"  {r['meet_id']}/{r['div_id']}"
              f"  {(r['course_name'] or '?')[:26]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
