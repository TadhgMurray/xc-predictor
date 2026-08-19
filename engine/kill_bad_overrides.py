"""kill_bad_overrides.py -- remove distance overrides that are outliers FOR
THEIR OWN DISTANCE CLASS, and only when removing them demonstrably helps.

THREE THINGS THIS HAS TO GET RIGHT
    RIGHT      -- not flag overrides that are fine. Measured baselines:
                  1609 races average field_shift 1.098 as a CLASS, 5000 sits
                  at 1.000, 6437 at 0.982. Comparing every override to 1.0
                  would condemn 55 mile races for being mile races. So the
                  comparison is against the median of the override's own
                  distance class, never against 1.0.

    UNDOES     -- removing an override restores meets.distance, which can be
                  WORSE than what it replaced. Since normalization scales as
                  (5000/d)^K, the post-removal shift is predictable:
                      shift_after = shift_before * (override/stored)^K
                  Nothing is removed unless that lands closer to baseline by
                  a real margin. Verified by hand on Colina 5k: 0.8199 *
                  (6000/5000)^1.06 = 0.996 against a 5000-class baseline of
                  1.000 -- 17% out to under 1%.

    CATCHES    -- rerunnable. --rebuild recomputes the shift table from
                  current ratings, so this finds NEW bad overrides after
                  every pipeline run rather than being a one-off cleanup.

⚠ WHAT THIS DELIBERATELY DOES NOT TOUCH: short races run hot as a class
  (1609 at 1.098, 2414 at 1.053, tapering to 1.000 by 5000). That is
  extrapolation error, not an override bug -- converting a mile to a 5k
  equivalent multiplies the time and its error by ~3.5x. Class baselines
  absorb it here on purpose. It is a separate problem.

Writes engine/corrections.py. Dry run unless --write.
"""
import io, os, re, sys, statistics

sys.path.insert(0, "scripts"); sys.path.insert(0, "engine")

PATH = os.path.join("engine", "corrections.py")

# ! K MUST MATCH THE NORMALIZER'S DISTANCE EXPONENT. It is only used to
#   PREDICT what happens after removal, so a small error makes the script
#   cautious rather than wrong -- but if normalize_distance's exponent moves,
#   move this with it.
K = 1.06

MIN_ROWS      = 15     # a division needs this many results to have a baseline
MIN_CLASS     = 20     # a distance class needs this many divisions to be one
OUTLIER       = 0.12   # |shift / class_baseline - 1| past this = candidate
MIN_GAIN      = 0.05   # removal must close at least this much of the gap
SANE_DISTANCE = (500, 20000)   # outside this, the stored value is not a race


_BUILD_SHIFT = """
    DROP TABLE IF EXISTS ovr_shift;
    CREATE TABLE ovr_shift AS
    WITH med AS (
        -- Each athlete's own level that season, across their races.
        SELECT person_id, substring(date, 1, 4)::int AS yr,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY normalized_time) AS med
        FROM   results
        WHERE  normalized_time IS NOT NULL AND person_id IS NOT NULL
        GROUP  BY 1, 2
    )
    SELECT r.meet_id, r.div_id, count(*) AS n,
           avg(r.normalized_time / m.med) AS field_shift
    FROM   results r
    JOIN   med m ON m.person_id = r.person_id
                AND m.yr = substring(r.date, 1, 4)::int
    WHERE  r.normalized_time IS NOT NULL AND m.med > 0
    GROUP  BY 1, 2;
    CREATE INDEX ON ovr_shift (meet_id, div_id);
    ANALYZE ovr_shift;
"""

_LOAD = f"""
    SELECT o.meet_id, o.div_id, mm.meet_name,
           mm.distance AS stored, o.distance AS override,
           s.n, s.field_shift
    FROM   dist_override o
    JOIN   ovr_shift s   ON s.meet_id = o.meet_id AND s.div_id = o.div_id
    LEFT   JOIN meets mm ON mm.meet_id = o.meet_id AND mm.div_id = o.div_id
    WHERE  s.n >= {MIN_ROWS} AND o.distance > 0
"""


# ------------------------------------------------------------------ #
#  BASELINES -- what "normal" means for each distance
# ------------------------------------------------------------------ #

def classBaselines(rows):
    """{distance: median field_shift}, from every division at that distance.

    ★ MEDIAN, NOT MEAN. The point is to describe the TYPICAL division, and
      the outliers this script hunts would drag a mean toward themselves --
      the baseline would move to accommodate the thing being measured.
    """
    by_dist = {}
    for r in rows:
        by_dist.setdefault(r["override"], []).append(r["field_shift"])
    return {d: statistics.median(v)
            for d, v in by_dist.items() if len(v) >= MIN_CLASS}


def baselineFor(dist, baselines):
    """The class baseline, or the nearest measured class when it is rare.

    A distance with three divisions has no baseline of its own, and 1.0 is
    the wrong fallback -- it would read short-race inflation as an error.
    The nearest measured class is the better guess.
    """
    if dist in baselines:
        return baselines[dist]
    if not baselines:
        return 1.0
    return baselines[min(baselines, key=lambda d: abs(d - dist))]


# ------------------------------------------------------------------ #
#  THE VERDICT -- outlier for its class AND removal helps
# ------------------------------------------------------------------ #

def predictAfterRemoval(row):
    """field_shift if the override went away and meets.distance took over.

    normalized_time scales as (5000/d)^K, so changing d from the override to
    the stored value scales it by (override/stored)^K. field_shift is a ratio
    of normalized times, so it scales identically.
    """
    return row["field_shift"] * (row["override"] / row["stored"]) ** K


def judge(row, baselines):
    """(verdict, reason, err_before, err_after) for one override."""
    err_now = abs(row["field_shift"] / baselineFor(row["override"], baselines) - 1)

    if err_now <= OUTLIER:
        return "keep", "normal for its class", err_now, None

    # Removable at all? With no sane stored distance there is nothing to fall
    # back to, and deleting the override would leave the division worse.
    if row["stored"] is None:
        return "keep", "no stored distance to fall back to", err_now, None
    if not SANE_DISTANCE[0] <= row["stored"] <= SANE_DISTANCE[1]:
        return ("keep", f"stored {row['stored']:.0f} is not a race distance",
                err_now, None)

    err_after = abs(predictAfterRemoval(row)
                    / baselineFor(row["stored"], baselines) - 1)
    if err_now - err_after < MIN_GAIN:
        return "keep", "removal would not improve it", err_now, err_after
    return "kill", "outlier, and removal fixes it", err_now, err_after


# ------------------------------------------------------------------ #
#  REPORT AND EDIT
# ------------------------------------------------------------------ #

def report(verdicts, baselines):
    print(f"\n[kill] distance-class baselines ({len(baselines)} classes):")
    for d in sorted(baselines):
        print(f"    {d:>8.0f} m   median shift {baselines[d]:.3f}")

    kills = [v for v in verdicts if v[0] == "kill"]
    print(f"\n[kill] {len(kills)} of {len(verdicts)} overrides condemned")
    if kills:
        print(f"    {'meet/div':<22}{'stored':>8}{'ovr':>8}{'shift':>8}"
              f"{'err':>7}{'after':>7}  meet")
    for _, _, err_now, err_after, row in sorted(kills,
                                                key=lambda v: -(v[2] - v[3])):
        print(f"    {row['meet_id']}/{row['div_id']:<14}"
              f"{row['stored']:>8.0f}{row['override']:>8.0f}"
              f"{row['field_shift']:>8.3f}{err_now:>7.3f}{err_after:>7.3f}"
              f"  {(row['meet_name'] or '')[:36]}")
    return {(v[4]["meet_id"], v[4]["div_id"]) for v in kills}


def stripLines(text, keys):
    """Delete every `(meet, div): dist,` line whose key is condemned.

    ⚠ LINE-BASED, AND THAT IS THE POINT. corrections.py holds the same key in
      many dict literals -- measured at 7.2 copies each. Leaving one behind
      leaves the override live, and which copy wins is decided by position in
      the file.
    """
    pat = re.compile(r"^\s*\((\d+),\s*(\d+)\)\s*:\s*[\d.]+\s*,")
    kept, dropped = [], 0
    for line in text.splitlines(keepends=True):
        m = pat.match(line)
        if m and (int(m.group(1)), int(m.group(2))) in keys:
            dropped += 1
            continue
        kept.append(line)
    return "".join(kept), dropped


def loadRows(cur, rebuild, conn):
    if rebuild:
        print("[kill] rebuilding ovr_shift from current ratings...")
        cur.execute(_BUILD_SHIFT)
        conn.commit()
    cur.execute(_LOAD)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["field_shift"] = float(r["field_shift"])
        r["override"] = float(r["override"])
        r["stored"] = float(r["stored"]) if r["stored"] is not None else None
    return rows


def main(write=False, rebuild=False):
    from database import getConn

    with getConn() as conn, conn.cursor() as cur:
        rows = loadRows(cur, rebuild, conn)
    print(f"[kill] {len(rows):,} overridden divisions with >= {MIN_ROWS} results")

    baselines = classBaselines(rows)
    verdicts = [(*judge(r, baselines), r) for r in rows]
    keys = report(verdicts, baselines)
    if not keys:
        print("[kill] nothing to remove")
        return

    text = io.open(PATH, encoding="utf-8", newline="").read()
    out, dropped = stripLines(text, keys)
    print(f"\n[kill] {dropped} lines match "
          f"({dropped / max(len(keys), 1):.1f} copies per key)")

    if not write:
        print("[kill] DRY RUN -- pass --write to apply")
        return
    io.open(PATH + ".bak", "w", encoding="utf-8", newline="").write(text)
    io.open(PATH, "w", encoding="utf-8", newline="").write(out)
    print(f"[kill] wrote {PATH} (backup at {PATH}.bak)")


if __name__ == "__main__":
    main(write="--write" in sys.argv, rebuild="--rebuild" in sys.argv)