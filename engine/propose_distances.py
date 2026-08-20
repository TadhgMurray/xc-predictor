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
    python tools/propose_distances.py                 # rebuild shifts, propose
    python tools/propose_distances.py --no-rebuild    # reuse ovr_shift
    python tools/propose_distances.py --write         # append the safe subset
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
               r.speed_rating
        FROM   results r
        LEFT   JOIN div_distance d ON d.meet_id = r.meet_id
                                  AND d.div_id = r.div_id
                                  AND d.source = r.source
        WHERE  r.person_id IS NOT NULL
    )
    SELECT x.meet_id, x.div_id,
           count(*)                     AS n,
           -- ! REPORTED, so a division carried entirely by shadow ratings is
           --   visible as such rather than looking like ordinary evidence.
           count(*) FILTER (WHERE x.speed_rating IS NULL) AS n_shadow,
           avg(m.med / COALESCE(x.speed_rating, s.k / x.nt)) AS field_shift
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

_LOAD = f"""
    SELECT s.meet_id, s.div_id, s.n, s.n_shadow, s.field_shift,
           d.course_name,
           COALESCE(o.distance, d.distance) AS used,
           d.distance   AS stored,
           o.distance   AS override
    FROM   ovr_shift s
    JOIN   div_distance d ON d.meet_id = s.meet_id AND d.div_id = s.div_id
    LEFT   JOIN dist_override o ON o.meet_id = s.meet_id AND o.div_id = s.div_id
    WHERE  s.n >= {MIN_ROWS}
"""


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

    Returns (text, n_blocks, n_lines).
    """
    out, blocks, dropped = [], 0, 0
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
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
    return "".join(out), blocks, dropped


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
    text, dead_blocks, dead_lines = stripDeadBlocks(text)
    if dead_blocks:
        io.open(path, "w", encoding="utf-8", newline="").write(text)
        print(f"[dist] removed {dead_blocks} dead DIST_PROPOSED block(s) "
              f"({dead_lines:,} proposals) that were never applied to anything")

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



def main(rebuild=True, write=False):
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

        cur.execute(_LOAD)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, x)) for x in cur.fetchall()]

    for r in rows:
        r["field_shift"] = float(r["field_shift"])
        for k in ("used", "stored", "override"):
            r[k] = float(r[k]) if r[k] is not None else None

    n_helped = sum(1 for r in rows if (r.get("n_shadow") or 0) > 0)
    n_only = sum(1 for r in rows if (r.get("n_shadow") or 0) >= r["n"])
    print(f"[dist] {len(rows):,} divisions with >= {MIN_ROWS} results")
    # ★ SAID OUT LOUD. Before shadow ratings these divisions were invisible,
    #   and the ones made ENTIRELY of them are exactly the badly wrong
    #   distances -- a division nobody could rate is the signature.
    print(f"       {n_helped:,} of them include rows the engine never rated; "
          f"{n_only:,} consist only of those")
    base = classBaselines(rows)
    print(f"[dist] {len(base)} distance classes")

    by_course, by_meet = candidateSets(rows)
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


if __name__ == "__main__":
    main(rebuild="--no-rebuild" not in sys.argv,
         write="--write" in sys.argv)