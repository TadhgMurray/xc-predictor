"""Divisions that are broken so badly the evidence deleted itself.

    python scripts/find_dropped_divisions.py                 # DO THIS FIRST
    python scripts/find_dropped_divisions.py --min-rows 20 --max-rated-frac 0.25
    python scripts/find_dropped_divisions.py --out pass0.py
    python scripts/find_dropped_divisions.py --meet 26359

Run from the PROJECT ROOT. READ ONLY unless --out is given, and --out writes a
proposal file, never corrections.py.

★ THE FAULT THAT HIDES ITSELF, AND WHY EVERY OTHER TOOL HERE WALKS PAST IT.

  Meet 26359, "Minutemen Classic - Mens Race" at Ox Bow Park, labelled 8046 m.
  145 finishers. TWO ratings:

      place 108   20:03.4   133.9
      place 135   21:32.1   124.7

  The winner ran 15:51.3 and has no rating at all. 15:51 at 8046 m is
  3:10/mile, so the row was dropped upstream as impossible -- and so was
  everyone else near the front. What survives is the tail, slow enough to look
  merely absurd rather than impossible, and it rates 133.9 because
  1203 s normalised from 8046 m onto the 5000 m anchor is 727 s -- a 12:07 5K.
  At the true distance that runner rates 81.

  So the worse the label, the FEWER rows survive to complain about it. Every
  detector in this repo -- rebuild_overrides pass 1, diag_rating_jumps,
  propose_distances, audit_slow_division -- gates on a count of RATED rows,
  and this division has two. The fault deletes its own evidence and every
  floor steps over it.

★ SO THE SIGNAL IS NOT THE GAP. IT IS THE SURVIVAL RATE. 145 finishers and 2
  ratings needs no threshold argument and no noise model: a division where
  almost every row was thrown away is broken, whatever threw them away.

! AND THE SURVIVORS STILL SIZE IT. One rated row is a useless sample of an
  ATHLETE, but a fine estimate of a MULTIPLIER when the multiplier is 1.65.
  implied = label * (own_median / rating) ** (1/K), the same arithmetic pass 1
  uses -- it just no longer demands four of them.

⚠ THIS IS NOT A PACE FLOOR. Nothing here decides a time is too fast for a
  distance. It reads a decision something else already made -- the row is not
  rated -- and treats an unrated MAJORITY as evidence about the division. The
  distance it proposes comes from speed ratings, exactly as everything else
  does.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from psycopg2.extras import RealDictCursor          # noqa: E402
from database import getConn                        # noqa: E402
from audit_overrides import snapToLadder, K         # noqa: E402
from build_ranking_results import _XC_TFRRS_DIST_SQL  # noqa: E402
from rebuild_overrides import emit, SNAP_TOL        # noqa: E402

# A division needs this many finishers before its survival rate means
# anything. Below it, "2 of 3 unrated" is noise.
MIN_ROWS = 20

# Flag a division when this share or less of its finishers kept a rating.
# --census prints the corpus distribution so this can be set from evidence
# rather than taste.
MAX_RATED_FRAC = 0.25

# At least one survivor is needed to size the error; with none, the division
# is reported but no distance is proposed.
MIN_SURVIVORS = 1

# How far the survivors must sit from their own medians before this is a
# DISTANCE fault rather than a division of genuinely poor runners. A whole
# field of survivors 30+ points above their own heads is not a coaching story.
MIN_GAP = 25.0

# ------------------------------------------------------------------ #
#  THE NUKE (owner's call, 2026-08-26)
# ------------------------------------------------------------------ #
#
# ★ A DIVISION THAT IS PROVABLY BROKEN BUT HAS NO DEFENSIBLE ANSWER IS
#   DROPPED, NOT LEFT LIVE-WRONG. The old flow reported it and waited for a
#   human plus another four-hour rebuild; the owner's ruling is the reverse:
#   "I'd rather nuke than have to rerun." A _DISTANCE_DROP entry removes the
#   division from normalization entirely -- times stay on the page, no
#   rating is invented -- and it is fully reversible: the entry lives in the
#   pass block, is re-derived on every reset, and a later hand-verified
#   override simply outbids it by making the division stop flagging.
#
# ⚠ THE NO-SURVIVOR CASE NEEDS ITS OWN EVIDENCE. Board share can be low for
#   reasons that are not a distance fault at all -- a field of unlinked,
#   foreign or DODEA athletes is board-ineligible and perfectly fine. With
#   survivors, the gap bar is that evidence. Without them, the field's own
#   MEDIAN pace at the stored label decides: no full field's median
#   approaches world-record pace (0.151 s/m for the 5k WR), and none walks.
NUKE_FAST_SM = 0.18    # 3:00/km at the label -- the label is too long
NUKE_SLOW_SM = 0.90    # 15:00/km at the label -- the label is too short


def paceImpossible(med_time, label):
    """A reason string when the field's median pace refutes the label."""
    if not med_time or not label:
        return None
    sm = float(med_time) / float(label)
    if sm < NUKE_FAST_SM:
        return f"median pace {sm:.3f} s/m at {label:.0f} -- beyond any human"
    if sm > NUKE_SLOW_SM:
        return f"median pace {sm:.3f} s/m at {label:.0f} -- slower than a walk"
    return None

# ⚠ "RATED" NOW MEANS "ON A BOARD", NOT "speed_rating IS NOT NULL".
#   fill_ratings prices every row after each rebuild, so a NULL rating
#   stopped being the engine's verdict -- the verdict moved to
#   ranking_results, whose build gates on the same pace band the pack
#   drops on. Counting the column would read every division as 100%
#   healthy and this tool would never flag anything again. The survivor
#   medians are scoped the same way, so a filled garbage rating can
#   never pollute an athlete's own median.
_SQL = """
WITH own AS (
    SELECT COALESCE(r.person_id, r.athlete_id) AS ident,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY r.speed_rating) AS med
    FROM   results r
    JOIN   ranking_results k ON k.result_id = r.result_id AND k.sport = 'XC'
    WHERE  r.speed_rating IS NOT NULL AND r.speed_rating > 0
      AND  COALESCE(r.person_id, r.athlete_id) IS NOT NULL
    GROUP  BY 1
    HAVING count(*) >= 3
),
div AS (
    SELECT r.meet_id, r.div_id,
           count(*)                                    AS n_rows,
           count(k.result_id)                          AS n_rated,
           -- ! THE SURVIVORS' OWN VERDICT, carried up with them. avg over a
           --   handful of rows, because that is all a broken division leaves.
           avg(r.speed_rating)
               FILTER (WHERE k.result_id IS NOT NULL)  AS rating,
           avg(o.med)
               FILTER (WHERE k.result_id IS NOT NULL)  AS own_med,
           -- ! FOR THE NUKE'S NO-SURVIVOR GUARD. The field's median raw
           --   time, so a division nothing rated can still testify.
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY r.time_seconds)               AS med_time,
           min(r.date)                                 AS date
    FROM   results r
    LEFT   JOIN ranking_results k ON k.result_id = r.result_id
                                 AND k.sport = 'XC'
    LEFT   JOIN own o ON o.ident = COALESCE(r.person_id, r.athlete_id)
    GROUP  BY 1, 2
    HAVING count(*) >= %(min_rows)s
)
SELECT d.*,
       COALESCE(dov.distance, m.distance, x.distance) AS label,
       COALESCE(m.course_name, mt.venue_name)         AS course_name,
       mt.meet_name                                   AS meet_name
FROM   div d
LEFT   JOIN dist_override dov
       ON dov.meet_id = d.meet_id AND dov.div_id = d.div_id
LEFT   JOIN LATERAL (
    SELECT course_name, distance FROM meets
     WHERE meets.meet_id = d.meet_id AND meets.div_id = d.div_id LIMIT 1
) m ON TRUE
LEFT   JOIN LATERAL (
    SELECT distance FROM tmp_xc_tfrrs_dist t
     WHERE t.meet_id = d.meet_id AND t.div_id = d.div_id LIMIT 1
) x ON TRUE
LEFT   JOIN LATERAL (
    SELECT venue_name, meet_name FROM meets_tfrrs
     WHERE meets_tfrrs.meet_id = d.meet_id LIMIT 1
) mt ON TRUE
"""


def impliedDistance(label, own_med, rating):
    """The distance that would put the survivors back on their own heads.

        rating scales as d**K, so  rating_obs / rating_true = (d_label/d_true)**K
     => d_true = d_label * (own_med / rating) ** (1/K)
    """
    if not label or not own_med or not rating or rating <= 0:
        return None
    return float(label) * (float(own_med) / float(rating)) ** (1.0 / K)


def census(rows):
    """The corpus-wide survival distribution, so the bar is set from evidence."""
    bands = [(0.0, 0.01), (0.01, 0.05), (0.05, 0.10), (0.10, 0.25),
             (0.25, 0.50), (0.50, 0.75), (0.75, 0.95), (0.95, 1.01)]
    counts = [0] * len(bands)
    rowsum = [0] * len(bands)
    for r in rows:
        frac = r["n_rated"] / r["n_rows"]
        for i, (lo, hi) in enumerate(bands):
            if lo <= frac < hi:
                counts[i] += 1
                rowsum[i] += r["n_rows"]
                break
    total = sum(counts)
    print(f"\n  HOW MANY FINISHERS KEEP A BOARD-ELIGIBLE RATING  "
          f"({total:,} divisions of {MIN_ROWS}+ finishers)\n")
    print(f"    {'rated share':<16}{'divisions':>12}{'%':>8}{'finishers':>14}")
    print("    " + "-" * 50)
    for (lo, hi), n, nr in zip(bands, counts, rowsum):
        if not n:
            continue
        print(f"    {f'{lo:.0%} - {hi:.0%}':<16}{n:>12,}"
              f"{100.0 * n / max(total, 1):>7.1f}%{nr:>14,}")
    print("\n    A healthy division rates most of its field. The bottom bands "
          "are\n    divisions whose evidence was deleted before any detector "
          "saw it.")


def _pool(flagged):
    # ★ DIVISIONS MISLABELLED TOGETHER ARE SIZED TOGETHER.
    #
    #   Meet 26359 divs 1, 2 and 3 all carry the scraped label 8047 and
    #   between them keep 12, 2 and 1 survivors. Sized alone they imply 5222,
    #   5167 and 6011 -- the last from a SINGLE row, which snaps to 6000 and
    #   is simply wrong. One survivor estimates a multiplier badly; fifteen
    #   estimate it well.
    #
    # ! GROUPED ON (meet, scraped label), NOT ON THE MEET ALONE. A meet
    #   legitimately runs several distances -- 26359 also has a division at
    #   4828 that rates 85% of its field and is fine. What identifies rows
    #   that were mislabelled by the same mistake is sharing the mistake:
    #   the same wrong number, at the same meeting.
    #
    #   Pooled by survivor count rather than averaging the implied distances,
    #   because a division with 12 survivors knows twelve times more about
    #   the multiplier than one with 1. The pooled figure puts all three of
    #   26359's divisions on the same rung.
    groups = {}
    for r in flagged:
        groups.setdefault((r["meet_id"], round(float(r["label"]))),
                          []).append(r)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        w = sum(m["n_rated"] for m in members)
        rating = sum(m["n_rated"] * float(m["rating"]) for m in members) / w
        own = sum(m["n_rated"] * float(m["own_med"]) for m in members) / w
        imp = impliedDistance(key[1], own, rating)
        if imp is None:
            continue
        snapped, err = snapToLadder(imp)
        for m in members:
            m["alone"] = (m["snapped"], m["err"])
            m["implied"], m["snapped"], m["err"] = imp, snapped, err
            m["pooled"] = w
            m["siblings"] = len(members)
    return flagged

def main():
    ap = argparse.ArgumentParser(
        description="Divisions where almost nobody kept a rating.")
    ap.add_argument("--min-rows", type=int, default=MIN_ROWS, dest="min_rows")
    ap.add_argument("--max-rated-frac", type=float, default=MAX_RATED_FRAC,
                    dest="max_frac")
    ap.add_argument("--min-gap", type=float, default=MIN_GAP, dest="min_gap")
    ap.add_argument("--min-survivors", type=int, default=MIN_SURVIVORS,
                    dest="min_surv")
    ap.add_argument("--meet", type=int, help="explain one meet and stop")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_XC_TFRRS_DIST_SQL)
            cur.execute(_SQL, {"min_rows": args.min_rows})
            rows = cur.fetchall()

    if args.meet:
        # ⚠ POOLED HERE TOO, OR THIS VIEW CONTRADICTS THE FILE. --meet used to
        #   size each division alone while --out wrote the pooled answer, so
        #   26359/3 read 6000 on screen and 5149 in pass0.py. One division,
        #   two numbers, depending on which flag you passed.
        hit = _pool([r for r in rows if r["meet_id"] == args.meet])
        print(f"\n  MEET {args.meet}\n")
        print(f"    {'div':>10}{'rows':>7}{'rated':>7}{'share':>8}"
              f"{'label':>8}{'rating':>8}{'own med':>9}{'implied':>9}"
              f"{'snap':>8}")
        for r in sorted(hit, key=lambda x: x["div_id"]):
            imp, snap = r.get("implied"), r.get("snapped")
            if imp is None:
                imp = impliedDistance(r["label"], r["own_med"], r["rating"])
                snap = snapToLadder(imp)[0] if imp else None
            print(f"    {r['div_id']:>10}{r['n_rows']:>7}{r['n_rated']:>7}"
                  f"{r['n_rated'] / r['n_rows']:>7.0%}"
                  f"{(r['label'] or 0):>8.0f}"
                  f"{(r['rating'] or 0):>8.1f}{(r['own_med'] or 0):>9.1f}"
                  f"{(imp or 0):>9.0f}{(snap or 0):>8.0f}")
        if not hit:
            print(f"    no division with {args.min_rows}+ finishers.")
        return 0

    census(rows)

    flagged, nukes = [], []
    for r in rows:
        if r["n_rated"] / r["n_rows"] > args.max_frac:
            continue
        # ⚠ NOT THE SURVIVOR FILTER YET. It runs AFTER pooling, below --
        #   filtering here is what dropped Ox Bow Park's girls race: 128
        #   finishers, ONE rating, and fourteen pooled siblings at the same
        #   meet under the same wrong label that would have sized it fine.
        #   A division with one survivor and no siblings is a guess; one with
        #   one survivor and fourteen siblings is not, and the filter could
        #   not tell them apart from where it was.
        if not r["own_med"] or not r["rating"]:
            # ★ NOTHING RATED TO SIZE IT. No survivors means no proposal --
            #   but the field's own median pace can still PROVE the label
            #   wrong, and a proven-wrong division with no answer is nuked
            #   rather than left live (see THE NUKE above). Low share alone
            #   is never enough: unlinked or foreign fields are
            #   board-ineligible and fine.
            pi = paceImpossible(r["med_time"], r["label"])
            if pi:
                nukes.append({**r, "why": f"{pi}; nothing rated to size it"})
            continue
        gap = float(r["rating"]) - float(r["own_med"])
        if abs(gap) < args.min_gap:
            continue
        imp = impliedDistance(r["label"], r["own_med"], r["rating"])
        if imp is None:
            continue
        snapped, err = snapToLadder(imp)
        flagged.append({**r, "gap": gap, "implied": imp,
                        "snapped": snapped, "err": err})

    flagged = _pool(flagged)
    # ! NOW the survivor test, counting the pool. `pooled` is the summed
    #   survivor count across everything mislabelled the same way; a lone
    #   division falls back to its own.
    thin = [r for r in flagged
            if r.get("pooled", r["n_rated"]) < args.min_surv]
    flagged = [r for r in flagged
               if r.get("pooled", r["n_rated"]) >= args.min_surv]
    flagged.sort(key=lambda r: -abs(r["gap"]))
    good = [r for r in flagged if abs(r["err"]) <= SNAP_TOL]
    # The other two unanswerable classes, nuked rather than reported:
    #   - enough survivors, but the implied distance lands on no real rung:
    #     the gap already proves the label; there is just no answer to write.
    #   - too few survivors to trust a proposal, AND the median pace proves
    #     the label on its own.
    for r in flagged:
        if abs(r["err"]) > SNAP_TOL:
            nukes.append({**r, "why": f"survivors {r['gap']:+.0f} off their "
                          f"own heads, implied {r['implied']:.0f} snaps to "
                          f"no rung (err {r['err']:+.0%})"})
    for r in thin:
        pi = paceImpossible(r["med_time"], r["label"])
        if pi:
            nukes.append({**r, "why": f"{pi}; only "
                          f"{r.get('pooled', r['n_rated'])} survivor(s)"})

    print(f"\n\n  DIVISIONS WHOSE EVIDENCE WAS DELETED "
          f"({len(flagged):,} flagged, {len(good):,} snap to a real rung)\n")
    n_pooled = sum(1 for r in good if r.get("siblings"))
    n_moved = sum(1 for r in good
                  if r.get("alone") and r["alone"][0] != r["snapped"])
    print(f"    {n_pooled:,} of them were sized with their siblings; that "
          f"changed the answer for {n_moved:,}.\n")
    print(f"    {'rows':>6}{'rated':>6}{'label':>7}{'rating':>8}{'own':>7}"
          f"{'gap':>8}{'implied':>8}{'snap':>7}{'err':>7}{'sib':>5}"
          f"  meet/div  course")
    for r in good[:args.limit]:
        # ! FLAG THE ONES POOLING RESCUED, so the effect is visible rather
        #   than asserted -- alone[0] is what a single division would have
        #   written on its own.
        moved = ("*" if r.get("alone") and r["alone"][0] != r["snapped"]
                 else " ")
        print(f"    {r['n_rows']:>6}{r['n_rated']:>6}{r['label']:>7.0f}"
              f"{r['rating']:>8.1f}{r['own_med']:>7.1f}{r['gap']:>+8.1f}"
              f"{r['implied']:>8.0f}{r['snapped']:>7.0f}{r['err']:>+7.1%}"
              f"{r.get('siblings', 1):>4}{moved}"
              f"  {r['meet_id']}/{r['div_id']}"
              f"  {(r['course_name'] or '?')[:24]}")
    if n_moved:
        print(f"\n    * pooling moved this division off the rung its own "
              f"survivors implied.")

    if nukes:
        print(f"\n\n  NUKED -- BROKEN WITH NO DEFENSIBLE ANSWER "
              f"({len(nukes):,} divisions dropped from normalization)\n")
        print("    Reversible: these live in the pass block, are re-derived "
              "each reset, and a\n    hand-verified distance override "
              "un-nukes a division by making it stop flagging.\n")
        for r in sorted(nukes, key=lambda x: -x["n_rows"])[:args.limit]:
            print(f"    {str(r['meet_id']) + '/' + str(r['div_id']):>14}"
                  f"{r['n_rows']:>6} rows"
                  f"{(r['label'] or 0):>8.0f}m"
                  f"  {(r['course_name'] or '?')[:24]:<26} {r['why']}")
        if len(nukes) > args.limit:
            print(f"    ... and {len(nukes) - args.limit} more")

    if args.out and (good or nukes):
        lines = [f"({r['meet_id']}, {r['div_id']}): {r['snapped']:.0f},"
                 f"  # was {r['label']:.0f}; {r['n_rated']} of {r['n_rows']} "
                 f"finishers rated, survivors {r['gap']:+.0f} over their own "
                 f"heads, snap {r['err']:+.1%}"
                 for r in good]
        # ! A SET BLOCK, NOT A DICT: entries carry no value, and emit's
        #   .update({...}) over comma-only lines is a set literal, which is
        #   exactly how apply_passes validates _DISTANCE_DROP names.
        drop_lines = [f"({r['meet_id']}, {r['div_id']}),"
                      f"  # {r['n_rows']} rows at {(r['label'] or 0):.0f}m: "
                      f"{r['why']}"
                      for r in sorted(nukes, key=lambda x: -x["n_rows"])]
        emit(args.out, "XC", [("_DISTANCE_OVERRIDES_XC", lines),
                              ("_DISTANCE_DROP_XC", drop_lines)],
             f"# GENERATED by scripts/find_dropped_divisions.py\n"
             f"#\n# {len(lines)} overrides. Each is a division where <= "
             f"{args.max_frac:.0%} of finishers kept a\n"
             f"# board-eligible rating and the survivors sit "
             f"{args.min_gap:.0f}+ points off their own medians.\n"
             f"#\n# {len(drop_lines)} drops (owner's rule: nuke the "
             f"unanswerable rather than leave it live).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
