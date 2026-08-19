# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/distance_pin.py
# Purpose: repair anomalous races that distance_repair CANNOT reach, by
#          snapping to a STANDARD distance instead of to the venue's history.
#
#   THE GAP THIS FILLS
#   ------------------
#   distance_repair snaps an inferred distance to one the VENUE has actually
#   run. That is what gives it precision -- the inference is only good to
#   ~15%, and the venue's short discrete list of real distances supplies the
#   rest. But it means a race is unreachable when:
#
#     * the venue has no OTHER distance on record        (22 races last run)
#     * no candidate had >= 2 race days across >= 2 yrs  (guarded out)
#     * the meets row does not join at all               (never even loaded)
#
#   That last class is the worst of the three. Foundation Academy Lion
#   Invitational: tfrrs stamped 5000 m on all six divisions including
#   "BOYS MIDDLE SCHOOL" and "G MIDDLE SCHOOL". The middle-school medians ran
#   939.6 s against the high schoolers' 1559.7 s at the same meet -- ten
#   minutes faster over a nominally identical distance. Those kids rate 155
#   to 175 and own the ms_f board.
#
#   ★ THE FIX IS THE SAME INFERENCE, A DIFFERENT SNAP TARGET. Cross country
#     is not run at arbitrary lengths: 1600, 2000, 2414 (1.5 mi), 3000, 3218
#     (2 mi), 4000, 4828 (3 mi), 5000, 6000, 6437 (4 mi), 8000. When the venue
#     cannot tell us what it ran, the SPORT can. A course measured at 2,779 m
#     was almost certainly a 2 mile or a 3 k, and nothing else.
#
#   ⚠ AND THAT IS WEAKER EVIDENCE, SO THE BAR IS HIGHER. Venue history is a
#     fact about this course; the standard-distance list is a prior about the
#     sport. The tolerance here is therefore TIGHTER than distance_repair's,
#     not looser -- if the inference does not land close to a standard
#     distance, we have learned that the anomaly is not a distance error and
#     the honest output is nothing.

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  CONSTANTS
# ------------------------------------------------------------------ #

# The distances cross country is actually run at, in metres.
#
# ★ IMPERIAL AND METRIC BOTH, because American XC uses both and the scrapers
#   record whichever the meet advertised. 3218 is 2 miles, 4828 is 3 miles,
#   2414 is 1.5 miles, 6437 is 4 miles, 1609 is a mile.
#
# ⚠ DELIBERATELY EXCLUDES 2896 (1.8 mi), 4023 (2.5 mi), 5471 and friends.
#   Those exist but are rare, and every extra entry makes an ambiguous snap
#   more likely -- which under the rule below means REFUSING rather than
#   guessing. A short list is a strict list.
_STANDARD = (1609.0, 2000.0, 2414.0, 3000.0, 3218.0, 4000.0,
             4828.0, 5000.0, 6000.0, 6437.0, 8000.0)

# How close the inference must land to a standard distance.
#
# ★ 8%, TIGHTER THAN distance_repair's 10%. The venue's own history is
#   evidence about THIS course; a list of common distances is only a prior
#   about the sport. Weaker evidence, higher bar.
SNAP_TOLERANCE = 0.08

# The proposal must actually change the distance -- 3200 -> 3218 is the same
# race restated in different units and cannot explain a rating gap.
MIN_CHANGE_FRAC = 0.03

# Gates inherited from field_shift. A race must be anomalous in BOTH the
# median and the tail before a distance is pinned to it: a distance error
# moves the whole field, so p10 must move with the median.
MIN_FIELD = 15
MIN_MED_GAP = 10.0
MIN_P10 = 6.0
MIN_BASELINE_RACES = 2

DISTANCE_EXPONENT = 1.0707
PIVOT_K = 1.0 / DISTANCE_EXPONENT


# ------------------------------------------------------------------ #
# CHUNK 1 -- INFERENCE (shared shape with distance_repair)
# ------------------------------------------------------------------ #

def inferDistance(claimed, base_rating, med_gap):
    """
    claimed / rho, where rho = (1 + med_gap/base) ** (1/k).

    rho > 1 means the claimed distance is TOO LONG, which is the case that
    inflates ratings: the normalizer credits the field with ground they did
    not cover.
    """
    if not claimed or claimed <= 0 or not base_rating or base_rating <= 0:
        return None
    inflation = (base_rating + med_gap) / base_rating
    if inflation <= 0:
        return None
    return claimed / (inflation ** PIVOT_K)


def snapStandard(inferred, tol=SNAP_TOLERANCE):
    """
    Nearest STANDARD distance, or None.

    ★ REFUSES ON AMBIGUITY, same as distance_repair. If two standard
      distances both sit inside the tolerance the inference cannot choose
      between them, and picking the nearer one is a coin flip dressed as an
      answer. At 8% the only pairs close enough to collide are 3000/3218 and
      4828/5000, which is exactly where a wrong pick would matter.
    """
    if inferred is None or inferred <= 0:
        return None
    near = [d for d in _STANDARD if abs(d - inferred) / inferred <= tol]
    return near[0] if len(near) == 1 else None


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE UNREACHABLE RACES
# ------------------------------------------------------------------ #

def _flaggedSql(results_tbl):
    """
    Anomalous races WITH the distance the backfill actually resolved.

    ★ THE DISTANCE COMES FROM normalized_time, NOT FROM meets. That is the
      whole trick: these races are unreachable precisely because their meets
      row is missing or useless, but the backfill still resolved SOME distance
      or there would be no rating at all -- and that distance is recoverable
      from the normalisation factor the row carries.

          normalized_time / time_seconds = (5000 / d) ** k
          =>  d = 5000 / (factor ** (1/k))

      So the row tells us what the pipeline believed, even when no table does.
    """
    return f"""
        WITH gapped AS (
            SELECT r.meet_id, r.div_id, r.date,
                   r.speed_rating - p.career_avg AS gap,
                   p.career_avg,
                   5000.0 / power(r.normalized_time / r.time_seconds,
                                  {PIVOT_K}) AS used_dist
            FROM   {results_tbl} r
            JOIN   person_baseline p ON p.person_id = r.person_id
            WHERE  r.speed_rating BETWEEN 40 AND 200
              AND  p.n_races >= {MIN_BASELINE_RACES}
              AND  r.normalized_time IS NOT NULL
              AND  r.time_seconds > 0
        )
        SELECT meet_id, div_id, date, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY gap)        AS med_gap,
               percentile_cont(0.1) WITHIN GROUP (ORDER BY gap)        AS p10,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY career_avg) AS base,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY used_dist)  AS claimed
        FROM   gapped
        GROUP  BY 1, 2, 3
        HAVING count(*) >= {MIN_FIELD}
           AND percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) >= {MIN_MED_GAP}
           AND percentile_cont(0.1) WITHIN GROUP (ORDER BY gap) >= {MIN_P10}
    """


def loadUnreachable(cur):
    """
    Flagged races that distance_repair could NOT have proposed for, because
    the venue offers no usable candidate.

    ⚠ THE EXCLUSION IS DELIBERATE AND MUST STAY. Where venue history exists,
      it is better evidence than a list of common distances, and the two tools
      would otherwise disagree about the same race. This one takes only what
      the other cannot reach.
    """
    cur.execute(f"""
        WITH flagged AS ({_flaggedSql('results')}),
        venue AS (
            SELECT m.course_name, m.distance,
                   count(DISTINCT r.date)             AS days,
                   count(DISTINCT substr(r.date,1,4)) AS years
            FROM   meets m
            JOIN   results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
            WHERE  m.course_name IS NOT NULL AND m.distance > 0
            GROUP  BY 1, 2
        )
        SELECT f.meet_id, f.div_id, f.date, f.n, f.med_gap, f.p10,
               f.base, f.claimed,
               m.course_name, m.meet_name
        FROM   flagged f
        LEFT   JOIN meets m
               ON m.meet_id = f.meet_id AND m.div_id = f.div_id
        WHERE  NOT EXISTS (
                 SELECT 1 FROM venue v
                 WHERE  v.course_name = m.course_name
                   AND  abs(v.distance - m.distance) > 1
                   AND  v.days >= 2 AND v.years >= 2)
        ORDER  BY f.med_gap * f.n DESC
    """)
    rows = cur.fetchall()
    print(f"    {len(rows):,} anomalous races with no usable venue history")
    return rows


# ------------------------------------------------------------------ #
# CHUNK 3 -- PROPOSE
# ------------------------------------------------------------------ #

def buildProposals(rows):
    """Infer, snap to standard, and record every refusal by reason."""
    out, ledger = [], {}

    def note(r):
        ledger[r] = ledger.get(r, 0) + 1

    for (mid, did, date, n, med, p10, base, claimed,
         course, mname) in rows:
        claimed = float(claimed or 0)
        inferred = inferDistance(claimed, float(base or 0), float(med))
        if inferred is None:
            note("no inference (bad base or distance)")
            continue
        snapped = snapStandard(inferred)
        if snapped is None:
            note("inference matched 0 or >1 standard distances")
            continue
        if abs(snapped - claimed) / claimed < MIN_CHANGE_FRAC:
            note(f"change under {MIN_CHANGE_FRAC:.0%}")
            continue
        out.append({
            "meet_id": mid, "div_id": did, "date": str(date), "n": n,
            "med_gap": float(med), "p10": float(p10),
            "claimed": claimed, "inferred": inferred, "proposed": snapped,
            "err_pct": 100.0 * abs(snapped - inferred) / inferred,
            "course": course or "(no meets row)",
            "meet": mname or ""})
    return out, ledger


def report(props, ledger):
    print(f"\n[pin] {len(props):,} proposals")
    print(f"    {'date':<12}{'n':>5}{'med':>7}{'p10':>7}{'used':>8}"
          f"{'infer':>8}{'pin':>7}{'err%':>7}  meet / course")
    for p in props[:60]:
        print(f"    {p['date']:<12}{p['n']:>5}{p['med_gap']:>7.1f}"
              f"{p['p10']:>7.1f}{p['claimed']:>8.0f}{p['inferred']:>8.0f}"
              f"{p['proposed']:>7.0f}{p['err_pct']:>7.1f}  "
              f"{p['meet'][:28]} / {p['course'][:22]}")
    print("\n[pin] not proposed:")
    for r, c in sorted(ledger.items(), key=lambda kv: -kv[1]):
        print(f"    {c:>6}  {r}")


# ------------------------------------------------------------------ #
# CHUNK 4 -- WRITE
# ------------------------------------------------------------------ #

_BLOCK_MARK = "# ==== AUTO: standard-distance pins"


def write(props):
    """
    Append to _DISTANCE_OVERRIDES_XC, same mechanism as distance_repair.

    ★ corrections.py, NOT meets.distance. These races have a broken or absent
      meets row by definition, so writing there would be writing into the
      thing that is already wrong -- and a re-scrape would undo it. The
      override is read FIRST in _resolveDistanceGender's chain.
    """
    if not props:
        print("\n[pin] nothing to write")
        return
    import datetime, shutil, inspect, importlib
    import corrections
    stamp = datetime.date.today().isoformat()
    lines = ["", "", f"{_BLOCK_MARK}, {stamp} ====",
             "# Written by engine/distance_pin.py --write.",
             "#",
             "# These races were anomalous AND unreachable by distance_repair:",
             "# their venue offers no other distance with real history, so the",
             "# inference was snapped to a STANDARD cross country distance",
             "# instead. Weaker evidence than a venue's own record, so the",
             "# tolerance is tighter (8% vs 10%) and the standard list is",
             "# deliberately short -- an ambiguous snap REFUSES.",
             "#",
             "# The 'used' distance is recovered from the row itself:",
             "#     d = 5000 / (normalized_time/time_seconds) ** (1/k)",
             "# which works even when no table can say what the race was.",
             "_DISTANCE_OVERRIDES_XC.update({"]
    for p in sorted(props, key=lambda q: -q["n"]):
        lines.append(
            f"    ({p['meet_id']}, {p['div_id']}): {p['proposed']:.0f},"
            .ljust(34)
            + f"# used {p['claimed']:.0f}, inferred {p['inferred']:.0f}"
              f" (err {p['err_pct']:.0f}%), gap {p['med_gap']:+.1f},"
              f" n={p['n']}  {p['date']}  {p['meet'][:26]}")
    lines += ["})", f"# ==== END AUTO block {stamp} ====", ""]

    path = inspect.getfile(corrections)
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"\n[pin] backup: {bak}")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"[pin] appended {len(props)} pins to {path}")

    before = len(corrections._DISTANCE_OVERRIDES_XC)
    importlib.reload(corrections)
    after = len(corrections._DISTANCE_OVERRIDES_XC)
    live = sum(1 for p in props
               if corrections._DISTANCE_OVERRIDES_XC.get(
                   (p["meet_id"], p["div_id"])) == p["proposed"])
    print(f"[pin] _DISTANCE_OVERRIDES_XC: {before:,} -> {after:,}; "
          f"{live}/{len(props)} live")


def revert():
    import inspect, corrections
    path = inspect.getfile(corrections)
    src = open(path, encoding="utf-8").read()
    if _BLOCK_MARK not in src:
        print("[pin] no pin block found")
        return
    open(path, "w", encoding="utf-8").write(src[:src.rindex(_BLOCK_MARK)])
    print(f"[pin] removed the last pin block from {path}")


# ------------------------------------------------------------------ #
# CHUNK 5 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(live=False):
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            print("[pin] loading unreachable anomalous races...")
            rows = loadUnreachable(cur)
            props, ledger = buildProposals(rows)
            report(props, ledger)
            conn.rollback()
    if live:
        write(props)
    else:
        print("\n[pin] DRY RUN -- pass --write to apply")


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    else:
        main(live="--write" in sys.argv)