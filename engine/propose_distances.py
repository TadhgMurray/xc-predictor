"""propose_distances.py -- suggest distance corrections the way the REMOVAL
test works: an outlier for its class, plus a candidate distance that comes
from somewhere other than the times.

★ WHY THE OLD PROPOSE PATH WAS WEAK, AND WHAT CHANGED.
    audit_overrides removes an override when it is an outlier AND deleting it
    demonstrably helps -- and that works because the candidate, meets.distance,
    is an INDEPENDENT fact. The proposal path had no such fact: it inverted the
    field shift into an implied distance and snapped it to the nearest rung of
    a ladder. A field that is merely slow implies a longer distance, the ladder
    is dense enough that almost anything lands near a rung, and so it proposed
    378 corrections of which many were plainly wrong -- Columbus Grove
    5000 -> 8000 on a field of 294.

    Here the implied distance still says WHICH candidate, but the candidate
    must already be in use at the same course or the same meet. "This division
    says 6000; four other divisions of this meet and 40 other meets on this
    course say 5000; and 5000 fixes the shift" is corroboration. "The runners
    were slow so it must be longer" is not.

★ AND IT READS BOTH SOURCES. audit_overrides joins meets, which describes anet
    only, so every tfrrs division came back with a NULL stored distance and was
    skipped -- 3,750 of 449,748 divisions were even eligible. tfrrs XC keeps
    its distances in meets_tfrrs.division_distances, a JSON blob keyed by the
    per-meet div_id. Thetford, the clearest wrong distance in the corpus, is
    tfrrs and was invisible to the old tool for exactly this reason.

⚠ AND --write TAKES ONLY THE HEAVILY CORROBORATED ONES. A generated distance
  is a new fact entering the corpus with nothing behind it but arithmetic, so
  the CSV holds everything that clears the proposal bar and the file gets a
  strict subset: enough corroborating divisions, a small residual error, and
  no wild change in magnitude. Everything else stays in the CSV for a human.

Usage:
    python engine/propose_distances.py                 # rebuild shifts, propose
    python engine/propose_distances.py --no-rebuild    # reuse ovr_shift
    python engine/propose_distances.py --write         # append the safe subset
    python engine/propose_distances.py --merged        # divisions holding TWO races
    python engine/propose_distances.py --merged --pairs-out merged.txt
                                                       # ... and the work list

★ A MERGED DIVISION IS NOT A DISTANCE PROBLEM AND --merged DOES NOT PROPOSE
  ONE. Two races in one bucket have two true distances, so any single number
  is wrong for half the field. The remedy is a per-ROW split, and the tool for
  that already exists -- scripts/triage_split_divisions.py, built for the
  9889/9888 surgery. --pairs-out writes the file it reads.
"""
import csv, io, math, os, statistics, sys

sys.path.insert(0, "scripts"); sys.path.insert(0, "engine")

CSV_PATH = "distance_proposals.csv"

# ! K MUST MATCH THE NORMALISER'S DISTANCE EXPONENT. It only chooses among
#   candidates that already exist, so an error here picks a neighbour rather
#   than inventing one -- but keep it current.
K = 1.06

MIN_ROWS       = 15    # a division needs this many results to testify
MIN_CLASS      = 20    # a distance class needs this many divisions to be one
OUTLIER        = 0.15  # |shift / class baseline - 1| past this = candidate
MIN_GAIN       = 0.05  # the candidate must close this much of the gap
MATCH_TOL      = 0.04  # implied must land within 4% of the candidate
MIN_CHANGE     = 0.06  # and differ from the distance in use by this much
MIN_CORROBORATE = 2    # how many other divisions must already use it
SANE           = (500, 20000)

# ------------------------------------------------------------------ #
#  THE BAR FOR WRITING, WHICH IS NOT THE BAR FOR PROPOSING
# ------------------------------------------------------------------ #
#
# ★ PROPOSING IS A QUESTION, WRITING IS AN ASSERTION. The CSV can afford to
#   be generous -- a human reads it. corrections.py is consumed by the
#   normaliser with no further review, so what goes in has to be the subset
#   nobody would argue with.
#
# ⚠ THE TOP OF THE PROPOSAL LIST IS EXACTLY WHAT THESE EXCLUDE. Measured on
#   the current corpus: 1609 -> 4023 at a shift of 2.717 backed by five
#   divisions. A 2.5x correction is either a badly mislabelled race or a
#   field that barely ran, and five divisions is not enough to tell which.
#   Meanwhile 8047 -> 5000 with 86 divisions on the same course, and
#   2993 -> 5000 with 131, are not in doubt.
WRITE_MIN_CORROBORATE = 5     # divisions already racing the proposed distance
WRITE_MAX_AFTER       = 0.10  # residual error once applied
WRITE_MAX_RATIO       = 2.00  # and no more than a doubling either way

# ★ AND AN ESCAPE HATCH FOR THE ONES THAT ARE PLAINLY WRONG.
#
#   Corroboration exists to stop a slow field being read as a long course.
#   That risk was real when the shift came from normalized_time, where a
#   difficult course and a wrong distance are literally the same number --
#   and it is why the old tool proposed 1609 -> 4023 at a shift of 2.7 on
#   five divisions, which was a hill, not a mismeasurement.
#
#   The shift now comes from speed_rating, which has the course effect
#   already divided out. A division whose field rates 35% below their own
#   season medians is not running a hard course; it is running a different
#   distance from the one recorded. At that size the error is doing the
#   arguing and fewer siblings are needed to name the answer.
#
# ⚠ THE ERROR BAR RISES AS THE CORROBORATION FALLS -- it is a trade, not a
#   waiver. Two siblings still have to point at a distance that lands the
#   division within WRITE_MAX_AFTER of its class, and the change is held to
#   the same ratio cap.
EXTREME_ERR           = 0.30  # a division this far off its class is wrong
EXTREME_CORROBORATE   = 2     # and needs only this many siblings to fix


MARKER = "# --- generated by propose_distances.py ---"


# ------------------------------------------------------------------ #
#  DISTANCES, FROM BOTH SOURCES
# ------------------------------------------------------------------ #
#
# ! anet AND tfrrs KEEP DISTANCE IN DIFFERENT SHAPES. meets has a column;
#   meets_tfrrs has a JSON blob keyed by the per-meet div_id as a STRING,
#   which is why the key is cast rather than compared. Unioning them here is
#   what lets one tool see the whole corpus.
_DISTANCES = """
    DROP TABLE IF EXISTS div_distance;
    CREATE UNLOGGED TABLE div_distance AS
        SELECT meet_id, div_id, source, course_name,
               distance::float AS distance
        FROM   meets
        WHERE  distance IS NOT NULL
        UNION ALL
        SELECT m.meet_id, (kv.key)::bigint, m.source, m.venue_name,
               (kv.value ->> 'distance')::float
        FROM   meets_tfrrs m,
               LATERAL jsonb_each(m.division_distances) kv
        WHERE  m.division_distances IS NOT NULL
          AND  kv.value ->> 'distance' IS NOT NULL
          AND  kv.key ~ '^[0-9]+$'
;
    -- ⚠ meets_tfrrs HAS NO div_id COLUMN -- it is one row per meet, and the
    --   per-division distances live entirely in the JSON blob, keyed by the
    --   div_id as a string. Its flat `distance` column is therefore a
    --   meet-level value with no division to attach it to, and is not used.
    CREATE INDEX ON div_distance (meet_id, div_id);
    CREATE INDEX ON div_distance (course_name);
    ANALYZE div_distance;
"""

_BUILD_SHIFT = """
    -- ★ speed_rating, NOT normalized_time -- AND THE DIFFERENCE IS THE WHOLE
    --   MEASUREMENT.
    --
    --   normalized_time is the raw time with distance, geometry, era and
    --   weather applied and NO difficulty; difficulty enters afterwards, in
    --   rating = 100 * pool_mean * exp(delta) / normalized_time. So a
    --   division at a hard course has a slow normalized_time, and a shift
    --   built on it reads that as "these people ran slow" -- which is exactly
    --   what a wrong distance looks like. The two are indistinguishable in
    --   that quantity, and this tool exists to tell them apart.
    --
    --   speed_rating already has the difficulty divided out. An athlete at a
    --   correctly measured race on a brutal course rates the same as they do
    --   anywhere else; only a WRONG DISTANCE moves them. That isolates the
    --   thing being measured.
    --
    -- ⚠ AND THE RATIO INVERTS. A rating is HIGH when the time is fast, so the
    --   athlete's median rating goes on top: median / this is > 1 when they
    --   underperformed here, which preserves the old sense where a shift
    --   above 1 meant "slow for this distance". Every threshold and the
    --   (d_old/d_new)^K prediction downstream keep their signs.
    -- ★ AND EVERY ROW TESTIFIES, INCLUDING THE ONES THAT WERE NEVER RATED.
    --   THIS IS THE WHOLE REASON A WRONG DISTANCE COULD HIDE.
    --
    --   speed_rating is NULL exactly where the distance is worst. Normalising
    --   a 15:51 to 8046m implies a 9:34 5000m, whose pace is under
    --   speed_ratings._PACE_FLOOR, so the row is dropped by the engine's
    --   sanity band and never rated. The worse the error, the fewer rows
    --   survive to testify against it -- and below MIN_ROWS the division
    --   stops being examined at all. Measured on Ox Bow Park, Minutemen
    --   Classic 2025: 145 finishers, 2 of them rated, needs 15.
    --
    --   So a rating is COMPUTED HERE for every row that lacks one. It exists
    --   only inside this table: nothing is written back to results, nothing
    --   is shown on a page, and the engine never sees it. It is evidence for
    --   one question, not a number about an athlete.
    --
    -- ★ AND THE SCALE COMES FROM THE ATHLETE'S OWN RATED RACES, so no pool
    --   mean has to be looked up or assumed. A rating is pool_mean /
    --   normalized_time * 100, so for any rated race
    --
    --       speed_rating * normalized_time  =  pool_mean * 100
    --
    --   which is a CONSTANT for that athlete. Take its median over their
    --   rated races and divide by the normalized time here, and the result is
    --   on exactly the same scale as the median it will be compared against.
    --   Pool means, era, gender and level all cancel because both sides are
    --   that one athlete.
    --
    -- ⚠ IT INHERITS THEIR TYPICAL COURSE DIFFICULTY, since speed_rating has
    --   difficulty divided out and the constant absorbs it. That is a
    --   second-order error on one race and it does not move with distance,
    --   which is what this measures.
    --
    -- ! normalized_time IS PRESENT EVEN WHEN speed_rating IS NOT. The band
    --   that suppressed these rows lives in the engine's loader, not in the
    --   backfill that writes the column -- so no exponent has to be
    --   re-applied here. The COALESCE is for the rows that predate it.
    DROP TABLE IF EXISTS ovr_shift;
    CREATE TABLE ovr_shift AS
    WITH med AS (
        SELECT person_id, substring(date, 1, 4)::int AS yr,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS med
        FROM   results
        WHERE  speed_rating IS NOT NULL AND speed_rating > 0
          AND  person_id IS NOT NULL
        GROUP  BY 1, 2
    ), scale AS (
        -- pool_mean * 100 for each athlete, from their own rated races.
        SELECT person_id,
               percentile_cont(0.5) WITHIN GROUP
                   (ORDER BY speed_rating * normalized_time) AS k
        FROM   results
        WHERE  speed_rating IS NOT NULL AND speed_rating > 0
          AND  normalized_time IS NOT NULL AND normalized_time > 0
          AND  person_id IS NOT NULL
        GROUP  BY 1
        HAVING count(*) >= {min_rated}
    ), rated AS (
        SELECT r.meet_id, r.div_id, r.person_id, r.date,
               COALESCE(
                   r.normalized_time,
                   r.time_seconds * power(5000.0 / NULLIF(d.distance, 0), {k})
               ) AS nt,
               r.speed_rating,
               -- ★ FOR THE STALENESS CHECK. nt = t * (anchor/d)^K, so t/nt
               --   recovers the distance the stored value was BUILT with --
               --   which is not always the distance div_distance now reports.
               --   See impliedUsed().
               r.time_seconds AS t,
               k2.pool        AS pool
        FROM   results r
        LEFT   JOIN div_distance d ON d.meet_id = r.meet_id
                                  AND d.div_id = r.div_id
                                  AND d.source = r.source
        -- ! LEFT, AND ONLY FOR THE ANCHOR. A division whose rows were all
        --   dropped from the boards has no pool here, and then the staleness
        --   check reports "unknown" instead of guessing one.
        LEFT   JOIN ranking_results k2 ON k2.result_id = r.result_id
                                      AND k2.sport = 'XC'
        WHERE  r.person_id IS NOT NULL
    )
    SELECT x.meet_id, x.div_id,
           count(*)                     AS n,
           -- ! REPORTED, so a division carried entirely by shadow ratings is
           --   visible as such rather than looking like ordinary evidence.
           count(*) FILTER (WHERE x.speed_rating IS NULL) AS n_shadow,
           avg(m.med / COALESCE(x.speed_rating, s.k / x.nt)) AS field_shift,
           -- The median t/nt, and the pool whose anchor it must be read
           -- against. Both only for impliedUsed(); neither affects the shift.
           percentile_cont(0.5) WITHIN GROUP (ORDER BY x.t / NULLIF(x.nt, 0))
               AS t_over_nt,
           mode() WITHIN GROUP (ORDER BY x.pool) AS pool
    FROM   rated x
    JOIN   med m ON m.person_id = x.person_id
                AND m.yr = substring(x.date, 1, 4)::int
    JOIN   scale s ON s.person_id = x.person_id
    WHERE  m.med > 0
      AND  x.nt > 0
      AND  COALESCE(x.speed_rating, s.k / x.nt) > 0
    GROUP  BY 1, 2;
    CREATE INDEX ON ovr_shift (meet_id, div_id);
    ANALYZE ovr_shift;
""".format(k=K, min_rated=3)

# ⚠ ovr_shift IS A CACHE, AND A CACHE CAN PREDATE THE CODE THAT READS IT.
#   t_over_nt and pool were added for the staleness check; a table built
#   before that has neither, and naming them is an UndefinedColumn error on a
#   tool the user ran to fix something else. Asked, not assumed -- the same
#   mistake as `m.distance` against a table without one, one file over.
_SHIFT_EXTRA = ("t_over_nt", "pool")

_HAS_SHIFT_COLUMN = """
    SELECT count(*) FROM information_schema.columns
    WHERE table_name = 'ovr_shift' AND column_name = ANY(%s)
"""


def shiftColumns(cur):
    """('s.t_over_nt, s.pool', True) when the cache has them, NULLs when not."""
    try:
        cur.execute(_HAS_SHIFT_COLUMN, (list(_SHIFT_EXTRA),))
        row = cur.fetchone()
        n = int(row["count"] if isinstance(row, dict) else row[0])
    except Exception:                                   # noqa: BLE001
        n = 0
    if n >= len(_SHIFT_EXTRA):
        return "s.t_over_nt, s.pool", True
    return "NULL::float AS t_over_nt, NULL::text AS pool", False


_LOAD = f"""
    SELECT s.meet_id, s.div_id, s.n, s.n_shadow, s.field_shift,
           {{extra}},
           d.course_name,
           COALESCE(o.distance, d.distance) AS used,
           d.distance   AS stored,
           o.distance   AS override
    FROM   ovr_shift s
    JOIN   div_distance d ON d.meet_id = s.meet_id AND d.div_id = s.div_id
    LEFT   JOIN dist_override o ON o.meet_id = s.meet_id AND o.div_id = s.div_id
    WHERE  s.n >= {MIN_ROWS}
"""


# ★ THE DISTANCE THE STORED normalized_time WAS ACTUALLY BUILT WITH, WHICH IS
#   NOT ALWAYS THE ONE div_distance REPORTS -- AND THAT GAP IS HOW AN 8000m
#   OVERRIDE GETS WRITTEN ONTO A THREE-MILE COURSE.
#
#       nt = t * (anchor / d) ^ K      so      d = anchor * (t / nt) ^ (1/K)
#
#   First to the Finish 2025 at Detweiller Park, reconstructed from what the
#   corpus now holds:
#
#       field_shift measured           1.6944
#       the athletes' own medians      98.7, and 0% of those races overridden
#       so the rows rated              98.7 / 1.694 = 58 when it was measured
#       which needs                    nt built at about 3,218m -- a 2-mile
#       but div_distance said          4,828m
#       implied = 4828 * 1.694^(1/K) = 7,940  ->  snapped to the 8000 rung
#
#   The shift was measured in one state and multiplied by a distance from
#   another. Every part of that arithmetic is correct; the two inputs simply
#   do not describe the same corpus. Had the shift been applied to the 3,218m
#   the ratings were actually built on, it would have proposed 5,292 -- the
#   5000 rung, and roughly right.
#
# ⚠ THE ANCHOR IS PER POOL, so t/nt alone cannot give a distance. ms anchors
#   at 3200m and hs at 5000m for cross country, so the same stored value reads
#   56% long if the pool is guessed wrong. The pool comes from
#   ranking_results, and a division with no ranked rows returns None rather
#   than a number built on an assumed anchor.
STALE_TOL = 0.05


def impliedUsed(row):
    """The distance the stored normalized_time was built with, or None."""
    tn, pool = row.get("t_over_nt"), row.get("pool")
    if not tn or not pool:
        return None
    try:
        from normalize_distance import targetFor
        anchor = targetFor(pool, "XC")
    except Exception:                                   # noqa: BLE001
        return None
    if not anchor:
        return None
    return float(anchor) * float(tn) ** (1.0 / K)


def staleness(row):
    """(is_stale, implied_distance). A division whose ratings and whose
    distance table disagree about what race was run.

    ! NOT A VERDICT ABOUT THE DISTANCE. It says the two INPUTS disagree, so
      nothing computed from both can be trusted -- neither a proposal nor a
      removal. The cure is to re-normalise, not to write another override.
    """
    implied = impliedUsed(row)
    used = row.get("used")
    if not implied or not used:
        return False, implied
    return abs(implied / float(used) - 1.0) > STALE_TOL, implied


def classBaselines(rows):
    """{distance: median field_shift}. MEDIAN so the outliers being hunted
    cannot drag the baseline toward themselves."""
    by = {}
    for r in rows:
        if r["used"]:
            by.setdefault(round(r["used"]), []).append(r["field_shift"])
    return {d: statistics.median(v) for d, v in by.items() if len(v) >= MIN_CLASS}


def baselineFor(dist, base):
    if not dist or not base:
        return 1.0
    d = round(dist)
    return base[d] if d in base else base[min(base, key=lambda x: abs(x - d))]


def candidateSets(rows):
    """Distances already in use, indexed by course and by meet.

    ★ THIS IS THE CORROBORATION. A proposal may only name a distance that
      something else at the same venue or the same meet already races. It
      cannot invent one, which is the whole difference from the ladder snap.
    """
    by_course, by_meet = {}, {}
    for r in rows:
        if not r["used"]:
            continue
        d = round(r["used"])
        if r["course_name"]:
            by_course.setdefault(r["course_name"], {}).setdefault(d, 0)
            by_course[r["course_name"]][d] += 1
        by_meet.setdefault(r["meet_id"], {}).setdefault(d, 0)
        by_meet[r["meet_id"]][d] += 1
    return by_course, by_meet


def judge(r, base, by_course, by_meet):
    """(verdict, reason, proposal|None) for one division."""
    used = r["used"]
    if not used or not SANE[0] <= used <= SANE[1]:
        return "skip", "no sane distance in use", None

    # ⚠ FIRST, BEFORE ANY ARITHMETIC: DO THE TWO INPUTS DESCRIBE THE SAME
    #   CORPUS? field_shift comes from ratings, which come from
    #   normalized_time; `used` comes from the distance tables. When those
    #   disagree the proposal multiplies a shift measured in one world by a
    #   distance from another, and the answer is wrong by exactly their gap.
    #   That is how Detweiller Park -- a three-mile course -- was given an
    #   8000m override. See impliedUsed.
    stale, implied_built = staleness(r)
    if stale:
        return ("skip",
                f"STALE: normalized_time was built at "
                f"{implied_built:.0f}m but the distance tables say "
                f"{used:.0f}m -- re-normalise before proposing anything here",
                None)

    err = abs(r["field_shift"] / baselineFor(used, base) - 1)
    if err <= OUTLIER:
        return "skip", "normal for its class", None

    # The distance that would put this field on its class baseline.
    implied = used * (r["field_shift"] / baselineFor(used, base)) ** (1.0 / K)
    if not SANE[0] <= implied <= SANE[1]:
        return "skip", f"implied {implied:.0f} is not a race distance", None

    # ! CANDIDATES COME FROM THE MEET FIRST, THEN THE COURSE. A sibling
    #   division on the same day is stronger evidence than the same ground in
    #   another year, when a course may genuinely have been re-measured.
    pool = {}
    for src, weight in ((by_meet.get(r["meet_id"], {}), "meet"),
                        (by_course.get(r["course_name"], {}), "course")):
        for d, cnt in src.items():
            if d not in pool:
                pool[d] = (cnt, weight)

    best = None
    for d, (cnt, where) in pool.items():
        if abs(d / used - 1) < MIN_CHANGE:
            continue                      # same as what is already in use
        if cnt < MIN_CORROBORATE:
            continue                      # one other division is not a pattern
        if abs(implied / d - 1) > MATCH_TOL:
            continue                      # implied does not point here
        after = abs(r["field_shift"] * (used / d) ** K
                    / baselineFor(d, base) - 1)
        if err - after < MIN_GAIN:
            continue                      # would not actually help
        if best is None or after < best[3]:
            best = (d, cnt, where, after)

    if best is None:
        return "skip", "no corroborated candidate", None
    d, cnt, where, after = best
    return "propose", f"{cnt} divisions on the same {where} use {d:.0f}", {
        "meet_id": r["meet_id"], "div_id": r["div_id"],
        "course_name": r["course_name"] or "", "n": r["n"],
        "in_use": round(used), "proposed": round(d),
        "implied": round(implied, 1),
        "corroborated_by": cnt, "corroboration": where,
        "field_shift": round(r["field_shift"], 4),
        "err_before": round(err, 4), "err_after": round(after, 4)}


def writable(p):
    """True when a proposal is safe to append without review.

    Two ways to qualify: broad corroboration at any error, or an error large
    enough to speak for itself with a couple of siblings to name the answer.
    Both paths still have to land the division near its class and stay under
    the ratio cap.
    """
    ratio = max(p["proposed"] / p["in_use"], p["in_use"] / p["proposed"])
    if p["err_after"] > WRITE_MAX_AFTER or ratio > WRITE_MAX_RATIO:
        return False
    if p["corroborated_by"] >= WRITE_MIN_CORROBORATE:
        return True
    return (p["err_before"] >= EXTREME_ERR
            and p["corroborated_by"] >= EXTREME_CORROBORATE)


def stripDeadBlocks(text):
    """Remove the DIST_PROPOSED literals earlier versions of this tool wrote.

    ★ SAFE BY CONSTRUCTION, BECAUSE THEY WERE NEVER LIVE. Nothing reads a name
      called DIST_PROPOSED -- that is the bug appendCorrections now fixes --
      so deleting these blocks changes no distance, no rating and no board. It
      only stops the file claiming corrections it was not applying.

    ⚠ AND THEY ARE NOT INERT CLUTTER. audit_overrides.stripLines deletes any
      line shaped `(meet, div): dist,` wherever it appears, so a dead block
      absorbs removals aimed at real overrides -- the audit reports work it
      did not do. Two tools disagreeing about which lines are live is worse
      than either being wrong alone.

    ⚠ AND EVERY LINE THAT MENTIONS THE NAME GOES WITH IT, not just the dict.
      The first version of this removed the literal and left behind whatever
      followed it -- and at least one copy of the file carried a hand-written

          _DISTANCE_OVERRIDES_XC.update(DIST_PROPOSED)

      after the block. Deleting the dict under a live reference does not
      produce a dead block, it produces a NameError on import, and
      corrections.py is imported by dump_overrides and the whole backfill.
      Cleaning up half a thing is worse than leaving it alone.

    Returns (text, n_blocks, n_lines, n_orphans).
    """
    out, blocks, dropped, orphans = [], 0, 0, 0
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        # An orphaned reference: `X.update(DIST_PROPOSED)`, `del DIST_PROPOSED`.
        stripped = lines[i].strip()
        if "DIST_PROPOSED" in stripped and not stripped.startswith("#") \
                and not stripped.startswith("DIST_PROPOSED = {"):
            orphans += 1
            i += 1
            continue
        if lines[i].startswith("DIST_PROPOSED = {"):
            blocks += 1
            # Walk back over the generated-by comment that introduces it.
            while out and (out[-1].lstrip().startswith("#") or
                           not out[-1].strip()):
                out.pop()
            # ! TO THE CLOSING BRACE AT COLUMN ZERO, not to the first "}".
            #   Every entry is indented; a dict literal's own close is the
            #   only unindented one.
            i += 1
            while i < len(lines) and not lines[i].startswith("}"):
                dropped += 1
                i += 1
            i += 1                      # the closing brace itself
            continue
        out.append(lines[i])
        i += 1
    return "".join(out), blocks, dropped, orphans


def appendCorrections(props, path=os.path.join("engine", "corrections.py")):
    """Append a fresh dict of the safe subset, and back the file up first.

    ★ APPENDED AS ITS OWN DICT AND THEN MERGED. corrections.py holds the same
      key in several literals -- audit_overrides measured 2.8 copies each --
      and the later one wins, so a block at the end overrides whatever came
      before it and can be deleted whole without touching a hand-written line.

    ⚠ THE MERGE LINE IS THE WHOLE POINT, AND IT USED TO BE MISSING. This wrote

          DIST_PROPOSED = { (meet, div): distance, ... }

      and stopped. Nothing in the codebase reads a name called DIST_PROPOSED.
      The consumers read _DISTANCE_OVERRIDES_BY_SPORT, built from
      _DISTANCE_OVERRIDES_XC / _TF, so every proposal --write had ever
      produced sat in the file as decoration: the tool reported success, the
      corrections were visibly in corrections.py, and not one of them was
      applied to a single race.

      Every other generated block in that file already got this right --
      `_RESULT_DROP_XC.update(_RESULT_DROP_ADDITIONS)` and friends. This one
      wrote the dict and skipped the update.

    ⚠ _DISTANCE_OVERRIDES_XC, NOT _TF, AND THAT IS NOT A DEFAULT. The whole
      tool is cross country: ovr_shift is built `FROM results`, and results_tf
      is a different table it never reads. corrections.py's header calls a
      cross-sport merge "the 2026-07-13 incident" and distanceOverrideSQL
      raises rather than guess a sport; writing into the wrong dict here would
      apply cross country distances to track races.
    """
    safe = [p for p in props if writable(p)]
    if not safe:
        print("[dist] nothing clears the WRITE bar")
        return 0

    text = io.open(path, encoding="utf-8", newline="").read()
    io.open(path + ".bak", "w", encoding="utf-8", newline="").write(text)

    # ! CLEARED BEFORE APPENDING. Runs of the broken version left dead
    #   DIST_PROPOSED literals behind; leaving them would keep the file
    #   claiming corrections nothing applies. See stripDeadBlocks.
    text, dead_blocks, dead_lines, dead_orphans = stripDeadBlocks(text)
    if dead_blocks or dead_orphans:
        io.open(path, "w", encoding="utf-8", newline="").write(text)
        print(f"[dist] removed {dead_blocks} DIST_PROPOSED block(s) "
              f"({dead_lines:,} proposals) and {dead_orphans} orphaned "
              f"reference(s)")

    lines = [f"\n\n{MARKER}",
             f"# {len(safe)} divisions, each corroborated by at least "
             f"{WRITE_MIN_CORROBORATE} other divisions",
             "# racing the proposed distance at the same meet or course.",
             "# XC only -- ovr_shift is built FROM results. See appendCorrections.",
             "_DISTANCE_OVERRIDES_ADDITIONS = {"]
    for p in sorted(safe, key=lambda x: (x["meet_id"], x["div_id"])):
        why = f"err {p['err_before']:.3f} -> {p['err_after']:.3f}"
        lines.append(f"    ({p['meet_id']}, {p['div_id']}): {p['proposed']},"
                     f"  # was {p['in_use']}, {p['corroborated_by']} on the "
                     f"same {p['corroboration']}, {why}")
    lines.append("}")
    # ! WITHOUT THESE TWO LINES THE DICT ABOVE IS DECORATION. See the docstring.
    lines.append("_DISTANCE_OVERRIDES_XC.update(_DISTANCE_OVERRIDES_ADDITIONS)")
    lines.append("del _DISTANCE_OVERRIDES_ADDITIONS")
    io.open(path, "a", encoding="utf-8", newline="").write("\n".join(lines) + "\n")
    print(f"[dist] appended {len(safe):,} of {len(props):,} proposals to {path}")
    print(f"       backup at {path}.bak")
    # ★ SAID OUT LOUD, because the last version of this reported exactly the
    #   same success while applying nothing. A caller who reads "appended"
    #   has no way to tell a live merge from a dead literal.
    print("       merged into _DISTANCE_OVERRIDES_XC -- live on the next "
          "normalise/rating rebuild")
    return len(safe)




def explain(key, rows, base, by_course, by_meet, cur):
    """Why was this division not proposed? Walk every gate out loud.

    ★ A TOOL THAT REPORTS 1,760 FINDINGS AND MISSES THE ONE YOU CAME FOR IS
      NOT INFORMATIVE, IT IS FRUSTRATING. Every bar here is defensible on its
      own and any of them can be the one that quietly excluded the division
      you already know is wrong. This prints them in order, with the numbers,
      so the answer is "MIN_CORROBORATE, by one" rather than silence.
    """
    meet_id, div_id = key
    print(f"\n{'=' * 68}\nWHY NOT {meet_id}/{div_id}\n{'=' * 68}")

    cur.execute("""
        SELECT count(*) AS n,
               count(r.speed_rating) AS rated,
               count(DISTINCT r.person_id) AS people,
               min(r.time_seconds) AS best,
               max(d.distance) AS stored, max(d.course_name) AS course
        FROM   results r
        LEFT   JOIN div_distance d ON d.meet_id = r.meet_id
                                  AND d.div_id = r.div_id AND d.source = r.source
        WHERE  r.meet_id = %(m)s AND r.div_id = %(d)s
    """, {"m": meet_id, "d": div_id})
    raw = cur.fetchone()
    if not raw or not raw[0]:
        print("  no results rows at all for this (meet_id, div_id)")
        return
    n, rated, people, best, stored, course = raw
    print(f"  results          {n:,} rows, {rated:,} rated by the engine, "
          f"{people:,} with a person_id")
    print(f"  stored distance  {stored}   course {course!r}")
    print(f"  fastest raw      {best}")

    cur.execute("SELECT n, n_shadow, field_shift FROM ovr_shift "
                "WHERE meet_id = %(m)s AND div_id = %(d)s",
                {"m": meet_id, "d": div_id})
    shift_row = cur.fetchone()
    if not shift_row:
        print(f"\n  ⛔ NOT IN ovr_shift. Every row needs a person_id, a median "
              f"rating\n     for that person-year, and >= 3 rated races to "
              f"scale a shadow\n     rating from. Nothing here cleared that.")
        return
    sn, sshadow, sshift = shift_row
    print(f"\n  ovr_shift        n={sn:,} ({sshadow:,} shadow-rated), "
          f"field_shift={float(sshift):.4f}")
    if sn < MIN_ROWS:
        print(f"  ⛔ STOPPED: n < MIN_ROWS ({sn} < {MIN_ROWS}). The division "
              f"is not loaded at all.")
        return

    r = next((x for x in rows if (x["meet_id"], x["div_id"]) == key), None)
    if r is None:
        print("  ⛔ STOPPED: not in the loaded set (no sane stored distance?)")
        return

    used = r["used"]

    # ★ THE FRESHNESS GATE, FIRST, because everything below it is arithmetic
    #   on two numbers that have to come from the same corpus.
    stale, implied_built = staleness(r)
    if implied_built:
        print(f"\n  built at         {implied_built:.0f}m -- what the stored "
              f"normalized_time implies, read against the {r.get('pool')} "
              f"anchor")
        print(f"  tables say       {used:.0f}m")
    else:
        print("\n  built at         unknown -- no ranked rows here, so no "
              "pool, so no anchor to read t/nt against")
    if stale:
        print(f"  ⛔ STOPPED: STALE by {implied_built / used - 1:+.1%}. The "
              f"ratings and the distance\n     tables disagree about which "
              f"race this was. Any proposal from here\n     multiplies a "
              f"shift measured at {implied_built:.0f}m by a distance of "
              f"{used:.0f}m -- which is\n     exactly how this corpus "
              f"acquired 8000m overrides on three-mile\n     courses. "
              f"Re-normalise, then ask again.")
        return

    baseline = baselineFor(used, base)
    err = abs(r["field_shift"] / baseline - 1)
    print(f"  class baseline   {baseline:.4f} for {used:.0f}m")
    print(f"  err vs class     {err:.4f}   (OUTLIER bar {OUTLIER})")
    if err <= OUTLIER:
        print(f"  ⛔ STOPPED: this field is NORMAL for its class. A shift of "
              f"{r['field_shift']:.3f}\n     against a {used:.0f}m baseline of "
              f"{baseline:.3f} is not an anomaly.")
        return

    implied = used * (r["field_shift"] / baseline) ** (1.0 / K)
    print(f"  implied distance {implied:.1f}m")

    pool = {}
    for src, weight in ((by_meet.get(r["meet_id"], {}), "meet"),
                        (by_course.get(r["course_name"], {}), "course")):
        for d, cnt in src.items():
            pool.setdefault(d, (cnt, weight))
    if not pool:
        print("  ⛔ STOPPED: no other division at this meet or course has any "
              "distance.")
        return

    print(f"\n  candidates already raced here (need >= {MIN_CORROBORATE} "
          f"divisions, within {MATCH_TOL:.0%} of implied,\n  differing "
          f">= {MIN_CHANGE:.0%} from what is in use, and gaining "
          f">= {MIN_GAIN}):")
    print(f"    {'dist':>7}{'divs':>7}{'where':>8}{'off implied':>13}"
          f"{'err after':>11}  verdict")
    for d, (cnt, where) in sorted(pool.items()):
        after = abs(r["field_shift"] * (used / d) ** K / baselineFor(d, base) - 1)
        off = abs(implied / d - 1)
        why = ("same as in use" if abs(d / used - 1) < MIN_CHANGE
               else f"only {cnt} division{'' if cnt == 1 else 's'}"
               if cnt < MIN_CORROBORATE
               else f"implied is {off:.1%} away" if off > MATCH_TOL
               else f"gain {err - after:.3f} < {MIN_GAIN}"
               if err - after < MIN_GAIN else "ACCEPTED")
        print(f"    {d:>7.0f}{cnt:>7}{where:>8}{off:>12.1%}{after:>11.3f}"
              f"  {why}")



# ==================================================================== #
#  MERGED DIVISIONS: TWO RACES UNDER ONE LABEL
# ==================================================================== #
#
# ★ A DIVISION IS ASSUMED TO BE ONE RACE, AND SOMETIMES IT IS TWO. The Bengal
#   Invite 2025 (26785/0) is stored as one 8000m girls division holding 134
#   finishers: places 1-59 are women between 18:07 and 24:08, and places
#   60-134 are men between 24:48 and 36:34. Those are a women's 5k and a
#   men's 8k, scraped into one table.
#
# ⚠ AND EVERY TOOL HERE IS BLIND TO IT BY CONSTRUCTION. field_shift is a MEAN
#   over the division. The men ran the stored distance and sit at 1.0; the
#   women ran 5000 and are scored as 8000, so they sit near 0.8. The average
#   came out 1.0057 and the division was called "normal for its class" --
#   which it is, on average, while half of it is wrong.
#
# ★ SO THE TEST IS SPREAD, NOT CENTRE. Ask whether the division's own
#   athletes agree with each other. A real race has one distance, so its
#   shifts cluster; two races under one label make two clusters, and the
#   quartiles come apart even when the mean is perfect.
#
# ⚠ AND THE ANSWER IS NOT A DISTANCE. No single number fixes a division that
#   held two races -- correcting it to 5000 breaks the men and leaving it at
#   8000 breaks the women. It needs a per-result split, which corrections.py
#   already has a facility for: _RESULT_OVERRIDE_XC carries "9889 div 4:
#   combined 'JV Women 5K + Men 5 Mile' ... women -> 5000/F; men -> M". This
#   finds the divisions that need one; it does not write them.

# How far apart the quartiles may be before a division is not one race.
# Ordinary race-to-race noise is about 3-5% per athlete, so a healthy
# division lands near 1.08. Two races 20% apart in distance push it past 1.2.
MERGED_IQR = 1.18

# And both halves have to be real. A division where 3 of 130 runners are odd
# is not two races, it is three odd runners.
MERGED_MIN_SIDE = 0.20

_MERGED_SQL = """
    WITH per_athlete AS (
        SELECT s.meet_id, s.div_id,
               m.med / NULLIF(COALESCE(r.speed_rating,
                                       sc.k / NULLIF(x.nt, 0)), 0) AS shift
        FROM   ovr_shift s
        JOIN   results r ON r.meet_id = s.meet_id AND r.div_id = s.div_id
        JOIN   LATERAL (
                   SELECT COALESCE(r.normalized_time, 0) AS nt
               ) x ON TRUE
        JOIN   (
            SELECT person_id, substring(date, 1, 4)::int AS yr,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating)
                       AS med
            FROM   results
            WHERE  speed_rating > 0 AND person_id IS NOT NULL
            GROUP  BY 1, 2
        ) m ON m.person_id = r.person_id
           AND m.yr = substring(r.date, 1, 4)::int
        JOIN   (
            SELECT person_id,
                   percentile_cont(0.5) WITHIN GROUP
                       (ORDER BY speed_rating * normalized_time) AS k
            FROM   results
            WHERE  speed_rating > 0 AND normalized_time > 0
              AND  person_id IS NOT NULL
            GROUP  BY 1
        ) sc ON sc.person_id = r.person_id
        WHERE  s.n >= %(min_rows)s AND m.med > 0
    )
    SELECT meet_id, div_id, count(*) AS n,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY shift) AS q25,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY shift) AS q50,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY shift) AS q75
    FROM   per_athlete
    WHERE  shift > 0
    GROUP  BY 1, 2
    HAVING count(*) >= %(min_rows)s
       AND percentile_cont(0.75) WITHIN GROUP (ORDER BY shift)
           > %(iqr)s * percentile_cont(0.25) WITHIN GROUP (ORDER BY shift)
    ORDER  BY percentile_cont(0.75) WITHIN GROUP (ORDER BY shift)
            / percentile_cont(0.25) WITHIN GROUP (ORDER BY shift) DESC
    LIMIT  %(lim)s
"""



# ⚠ AN ATHLETE WHOSE ONLY RATED RACE IS THIS ONE AGREES WITH THEMSELVES BY
#   CONSTRUCTION. Their median IS this race's rating, so their shift is
#   exactly 1.000 -- and a division full of them reads as perfectly
#   consistent no matter how wrong it is. It shows in the first pass as
#   quartiles landing on exactly 1.000, which is not a coincidence and not
#   data.
#
# ★ SO THE CANDIDATES ARE VERIFIED AGAINST EACH ATHLETE'S RACES ELSEWHERE.
#   Bounded to one division at a time, so the correlated median that would be
#   ruinous corpus-wide costs nothing here: cheap filter first, exact test
#   second.
# ⚠ MATERIALISED ONCE, NOT ONCE PER CANDIDATE. The first version computed
#   this scale inline inside the verify query, which made it a full aggregate
#   over every rated row in `results` -- and then ran it 320 times, once per
#   candidate division. That is not slow, it is a different program. Built
#   here as a table with an index, each verify becomes a lookup.
_SCALE_TABLE = """
    DROP TABLE IF EXISTS ovr_scale;
    CREATE UNLOGGED TABLE ovr_scale AS
        SELECT person_id,
               percentile_cont(0.5) WITHIN GROUP
                   (ORDER BY speed_rating * normalized_time) AS k
        FROM   results
        WHERE  speed_rating > 0 AND normalized_time > 0
          AND  person_id IS NOT NULL
        GROUP  BY 1;
    CREATE INDEX ON ovr_scale (person_id);
    ANALYZE ovr_scale;
"""

_VERIFY_MERGED = """
    WITH here AS (
        SELECT r.person_id, substring(r.date, 1, 4)::int AS yr,
               COALESCE(r.speed_rating,
                        sc.k / NULLIF(r.normalized_time, 0)) AS rating
        FROM   results r
        JOIN   ovr_scale sc ON sc.person_id = r.person_id
        WHERE  r.meet_id = %(m)s AND r.div_id = %(d)s
          AND  r.person_id IS NOT NULL
    ), elsewhere AS (
        -- ! THE SAME YEAR, AT ANY OTHER MEET. Excluding only this DIVISION
        --   would leave the athlete's other races at the same meet in, and a
        --   merged meet is usually merged in every one of its divisions.
        SELECT h.person_id,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY o.speed_rating)
                   AS med
        FROM   here h
        JOIN   results o ON o.person_id = h.person_id
                        AND o.meet_id <> %(m)s
                        AND substring(o.date, 1, 4)::int = h.yr
        WHERE  o.speed_rating > 0
        GROUP  BY 1
        HAVING count(*) >= 2
    )
    SELECT h.person_id, e.med / NULLIF(h.rating, 0) AS shift
    FROM   here h
    JOIN   elsewhere e ON e.person_id = h.person_id
    WHERE  h.rating > 0 AND e.med > 0
"""


def verifyMerged(cur, meet_id, div_id):
    """Re-measure a candidate against races the athletes ran elsewhere.

    Returns (n, q25, q50, q75, small_side) or None when too few athletes have
    a career outside this meet to say anything.
    """
    cur.execute(_VERIFY_MERGED, {"m": meet_id, "d": div_id})
    shifts = sorted(float(r[1]) for r in cur.fetchall() if r[1])
    if len(shifts) < MIN_ROWS:
        return None
    n = len(shifts)
    q = lambda f: shifts[min(n - 1, int(f * n))]
    q25, q50, q75 = q(0.25), q(0.50), q(0.75)

    # ★ THE SPLIT ITSELF, NOT THE QUARTILES. Quartiles cannot tell two races
    #   from one race with a long tail: the Bengal Invite, which is verified
    #   merged, reads q25 0.604 / median 0.964 / q75 0.994 -- the same shape
    #   as a division with a slow tail, because its smaller race is 44% of
    #   the field so the median lands inside the larger one. What separates
    #   them is whether there is a HOLE between the two groups.
    #
    #   So: split at the widest gap in the middle, and report both sides.
    lo, hi = int(0.2 * n), max(int(0.8 * n), int(0.2 * n) + 1)
    gap_at, gap = lo, 0.0
    for i in range(lo, min(hi, n - 1)):
        d = shifts[i + 1] - shifts[i]
        if d > gap:
            gap_at, gap = i, d
    left, right = shifts[:gap_at + 1], shifts[gap_at + 1:]
    if not right:
        return None
    small_side = min(len(left), len(right)) / n

    # ⚠ WHICH SIDE IS THE ANOMALY IS NOT ALWAYS THE LOW ONE, and assuming so
    #   inverted the answer. The LARGER group is the reference -- it is the
    #   race the label describes, and its shift should sit near 1.0 -- and the
    #   SMALLER group is the one that ran something else. On 132074/563039
    #   the two readings give 338m and 4313m for the same division; only one
    #   of those is a distance.
    if len(left) <= len(right):
        anom, ref = left[len(left) // 2], right[len(right) // 2]
    else:
        anom, ref = right[len(right) // 2], left[len(left) // 2]
    return n, q25, q75, small_side, anom, ref, gap


def reportMerged(cur, limit=40, pairs_out=None):
    """Divisions whose own athletes do not agree about the distance.

    ★ AND IT CAN HAND THE LIST STRAIGHT TO THE SPLITTER. There is already a
      per-row splitter -- scripts/triage_split_divisions.py, built for the
      9889/9888 surgery -- and the only thing between it and these divisions
      was that nobody could get the list out of here without retyping 222
      meet/div pairs. --pairs-out writes the file it takes:

          python engine/propose_distances.py --merged --pairs-out merged.txt
          python scripts/triage_split_divisions.py --sport XC \
                 --pairs-file merged.txt

    ! EVERY VERIFIED DIVISION IS WRITTEN, not just the ones printed. The
      table is capped at `limit` because a screen is a screen; the file is
      the work list.
    """
    cur.execute(_MERGED_SQL, {"min_rows": MIN_ROWS, "iqr": MERGED_IQR,
                              "lim": limit * 8})
    candidates = cur.fetchall()
    print(f"\n[dist] building the per-athlete scale table for the verify "
          f"pass...")
    cur.execute(_SCALE_TABLE)
    print(f"\n\n[dist] DIVISIONS THAT LOOK LIKE TWO RACES "
          f"(q75/q25 > {MERGED_IQR})\n")
    print(f"    {len(candidates):,} candidates from the cheap pass; each is "
          f"now re-measured\n    against races its athletes ran at OTHER "
          f"meets.\n")

    kept, dropped = [], 0
    for i, (meet_id, div_id, _n, _a, _b, _c) in enumerate(candidates, start=1):
        got = verifyMerged(cur, meet_id, div_id)
        if got is None:
            dropped += 1
            if i % 25 == 0 or i == len(candidates):
                print(f"    verified {i:,}/{len(candidates):,} "
                      f"({len(kept)} kept)")
            continue
        n, q25, q75, small, anom, ref, gap = got
        if (q25 <= 0 or ref <= 0 or anom <= 0 or q75 / q25 <= MERGED_IQR
                or small < MERGED_MIN_SIDE):
            dropped += 1
            if i % 25 == 0 or i == len(candidates):
                print(f"    verified {i:,}/{len(candidates):,} "
                      f"({len(kept)} kept)")
            continue
        kept.append((max(anom / ref, ref / anom), meet_id, div_id, n,
                     small, anom, ref, gap))
        # ! PRINTED AFTER THE VERIFY, not before it. Reporting progress at the
        #   top of the loop counts the item it has not done yet, so the last
        #   line said "3/3 (2 kept)" over a table of three.
        if i % 25 == 0 or i == len(candidates):
            print(f"    verified {i:,}/{len(candidates):,} "
                  f"({len(kept)} kept)")

    print(f"    {dropped:,} fell away on the second look -- most of them were "
          f"athletes\n    whose only rated race IS this one, who agree with "
          f"themselves by\n    construction and read as a shift of exactly "
          f"1.000.\n")
    if not kept:
        print("    Nothing survived. No division's athletes disagree with "
              "their own\n    form elsewhere about how far they ran here.")
        return
    print("    ⚠ These need a PER-RESULT split, not a distance. One number "
          "cannot\n      fix a division that held two races. See "
          "_RESULT_OVERRIDE_XC.\n")
    kept.sort(reverse=True)

    if pairs_out:
        # ⚠ THE SPLITTER READS "meet div" AND NOTHING ELSE, so the numbers
        #   come first and the evidence goes behind a #. Its parser splits on
        #   whitespace and takes two fields.
        with io.open(pairs_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# meet div -- verified merged divisions from "
                     "propose_distances --merged\n")
            fh.write("# feed to: python scripts/triage_split_divisions.py "
                     "--sport XC --pairs-file <this>\n")
            for _r, meet_id, div_id, n, small, anom, ref, gap in kept:
                fh.write(f"{meet_id} {div_id}\n")
        print(f"    -> {len(kept):,} divisions written to {pairs_out}\n"
              f"       feed it to scripts/triage_split_divisions.py "
              f"--pairs-file\n")
    # ★ AND THE SMALL GROUP'S IMPLIED DISTANCE, which turns a spread into a
    #   claim you can check by eye. Their shift sits at lo_mid where the rest
    #   of the field sits at hi_mid, so they ran
    #       stored * (lo_mid / hi_mid) ** (1/K)
    #   -- and "42% of this 8000m field ran about 5100m" is something a
    #   results page settles in ten seconds.
    print("    `ref` is the LARGER group -- the race the label describes, so "
          "it should\n    sit near 1.00. Far from it means the whole "
          "division is off, not half.\n")
    print(f"    {'meet/div':<18}{'n':>5}{'small':>7}{'anom':>8}{'ref':>7}"
          f"{'stored':>8}{'they ran':>10}  meet")
    for _r, meet_id, div_id, n, small, anom, ref, gap in kept[:limit]:
        # ! div_distance, NOT meets. It unions anet's column with the tfrrs
        #   JSON blob, and reading `meets` alone printed "-" for every tfrrs
        #   division -- exactly the ones with no distance column to read.
        # ! div_distance HAS course_name, NOT meet_name -- see _DISTANCES.
        #   The meet name comes from `meets`, which only covers anet, so the
        #   course name is the fallback and a tfrrs division still gets a
        #   label rather than a blank.
        cur.execute("""
            SELECT d.distance, COALESCE(m.meet_name, d.course_name)
            FROM   div_distance d
            LEFT   JOIN meets m ON m.meet_id = d.meet_id
                                AND m.div_id = d.div_id
            WHERE  d.meet_id = %s AND d.div_id = %s
            LIMIT  1
        """, (meet_id, div_id))
        got = cur.fetchone()
        stored = float(got[0]) if got and got[0] else None
        name = (got[1] if got else "") or ""
        implied = stored * (anom / ref) ** (1.0 / K) if stored else None
        flag = " " if 0.9 <= ref <= 1.1 else "!"
        print(f"  {flag} {f'{meet_id}/{div_id}':<18}{n:>5}{small:>6.0%}"
              f"{anom:>8.3f}{ref:>7.3f}"
              f"{(f'{stored:.0f}' if stored else '-'):>8}"
              f"{(f'{implied:.0f}' if implied else '-'):>10}  {name[:28]}")
    print("\n    ! marks a division whose reference group is itself more than "
          "10% from\n      1.00 -- read those as 'this whole division is "
          "wrong', not 'half of it'.")


def main(rebuild=True, write=False, explain_keys=(),
         merged=False, repair=False, pairs_out=None):
    if repair:
        # ★ ITS OWN COMMAND, because the file is currently unimportable and
        #   the ordinary --write path would have to import nothing but still
        #   appends proposals. A repair should repair.
        path = os.path.join("engine", "corrections.py")
        text = io.open(path, encoding="utf-8", newline="").read()
        fixed, blocks, lines_, orphans = stripDeadBlocks(text)
        if fixed == text:
            print("[dist] corrections.py has no DIST_PROPOSED left to clean.")
            return
        io.open(path + ".bak", "w", encoding="utf-8", newline="").write(text)
        io.open(path, "w", encoding="utf-8", newline="").write(fixed)
        print(f"[dist] removed {blocks} DIST_PROPOSED block(s) "
              f"({lines_:,} proposal lines) and {orphans} orphaned "
              f"reference(s) to the name")
        print(f"       backup at {path}.bak")
        print("       corrections.py should import again -- verify with "
              "`python -c \"import sys; sys.path.insert(0,'engine'); "
              "import corrections\"`")
        return


    from database import getConn

    with getConn() as conn, conn.cursor() as cur:
        print("[dist] building the two-source distance table...")
        cur.execute(_DISTANCES)
        conn.commit()
        cur.execute("SELECT source, count(*) FROM div_distance GROUP BY 1")
        for src, n in cur.fetchall():
            print(f"    {src}: {n:,} divisions with a distance")

        if rebuild:
            print("[dist] rebuilding ovr_shift from current ratings...")
            cur.execute(_BUILD_SHIFT)
            conn.commit()

        extra, fresh_cache = shiftColumns(cur)
        if not fresh_cache:
            print("[dist] ⚠ ovr_shift predates the staleness check -- it has "
                  "no t_over_nt/pool.\n"
                  "       Every division will report 'could not be checked'. "
                  "Rebuild to get it\n"
                  "       (drop --no-rebuild, or run with --rebuild).")
        cur.execute(_LOAD.format(extra=extra))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, x)) for x in cur.fetchall()]

    for r in rows:
        r["field_shift"] = float(r["field_shift"])
        for k in ("used", "stored", "override"):
            r[k] = float(r[k]) if r[k] is not None else None
        r["t_over_nt"] = (float(r["t_over_nt"])
                          if r.get("t_over_nt") is not None else None)

    n_helped = sum(1 for r in rows if (r.get("n_shadow") or 0) > 0)
    n_only = sum(1 for r in rows if (r.get("n_shadow") or 0) >= r["n"])
    print(f"[dist] {len(rows):,} divisions with >= {MIN_ROWS} results")
    # ★ SAID OUT LOUD. Before shadow ratings these divisions were invisible,
    #   and the ones made ENTIRELY of them are exactly the badly wrong
    #   distances -- a division nobody could rate is the signature.
    print(f"       {n_helped:,} of them include rows the engine never rated; "
          f"{n_only:,} consist only of those")
    # ⚠ HOW MUCH OF THE CORPUS CANNOT BE PROPOSED FROM AT ALL, said before
    #   any proposal is made. A stale division is one where the ratings and
    #   the distance tables disagree about which race was run -- see
    #   impliedUsed -- and a proposal built from both is wrong by their gap.
    n_stale = sum(1 for r in rows if staleness(r)[0])
    n_unknown = sum(1 for r in rows if impliedUsed(r) is None)
    print(f"[dist] {n_stale:,} divisions are STALE (normalized_time was built "
          f"at a distance the tables no longer report) and are skipped")
    print(f"       {n_unknown:,} could not be checked -- no ranked rows, so "
          f"no pool, so no anchor to read t/nt against")

    base = classBaselines(rows)
    print(f"[dist] {len(base)} distance classes")

    by_course, by_meet = candidateSets(rows)

    if merged:
        # Its own exit: this is a question, and it names a different remedy.
        with getConn() as conn, conn.cursor() as cur:
            reportMerged(cur, pairs_out=pairs_out)
        return

    if explain_keys:
        # ! ITS OWN EXIT. Explaining is a question about specific divisions,
        #   not a run that also happens to print some -- writing corrections
        #   as a side effect of asking "why not" would be a nasty surprise.
        with getConn() as conn, conn.cursor() as cur:
            for key in explain_keys:
                explain(key, rows, base, by_course, by_meet, cur)
        return

    props = [p for v, _, p in (judge(r, base, by_course, by_meet) for r in rows)
             if v == "propose"]

    if not props:
        print("[dist] nothing clears the bar")
        return

    props.sort(key=lambda p: -(p["err_before"] - p["err_after"]))
    cols = ["meet_id", "div_id", "course_name", "n", "in_use", "proposed",
            "implied", "corroborated_by", "corroboration", "field_shift",
            "err_before", "err_after"]
    with io.open(CSV_PATH, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for p in props:
            w.writerow(p)

    n_safe = sum(1 for p in props if writable(p))
    print(f"\n[dist] {len(props):,} proposals -> {CSV_PATH}")
    print(f"       {n_safe:,} clear the WRITE bar: "
          f">= {WRITE_MIN_CORROBORATE} corroborating divisions, OR err before "
          f">= {EXTREME_ERR} with >= {EXTREME_CORROBORATE}")
    print(f"       (both need err after <= {WRITE_MAX_AFTER} and change "
          f"<= {WRITE_MAX_RATIO}x)")
    if not write:
        print("       DRY RUN -- pass --write to append that subset.\n")
    print(f"    {'meet/div':<22}{'now':>7}{'->':>4}{'new':>7}"
          f"{'shift':>8}{'err':>7}{'after':>7}  why")
    for p in props[:30]:
        # W = broad corroboration; X = written on the size of the error
        mark = ("W" if p["corroborated_by"] >= WRITE_MIN_CORROBORATE
                else "X") if writable(p) else " "
        print(f"  {mark} {p['meet_id']}/{p['div_id']:<13}{p['in_use']:>7}"
              f"{'->':>4}{p['proposed']:>7}{p['field_shift']:>8.3f}"
              f"{p['err_before']:>7.3f}{p['err_after']:>7.3f}"
              f"  {p['corroborated_by']} on the same {p['corroboration']}")

    if write:
        appendCorrections(props)


def _explainArgs(argv):
    """--explain 25930/7 --explain 900/1 -> [(25930, 7), (900, 1)]"""
    out = []
    for i, a in enumerate(argv):
        if a == "--explain" and i + 1 < len(argv):
            meet, _, div = argv[i + 1].partition("/")
            out.append((int(meet), int(div or 0)))
    return tuple(out)


if __name__ == "__main__":
    def _after(flag, default=None):
        """The value after a flag, or `default` when it is absent or last."""
        if flag in sys.argv:
            i = sys.argv.index(flag)
            if i + 1 < len(sys.argv):
                return sys.argv[i + 1]
        return default

    main(rebuild="--no-rebuild" not in sys.argv,
         write="--write" in sys.argv,
         explain_keys=_explainArgs(sys.argv),
         merged="--merged" in sys.argv,
         repair="--repair" in sys.argv,
         pairs_out=_after("--pairs-out"))