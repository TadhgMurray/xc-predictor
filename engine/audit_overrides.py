"""audit_overrides.py -- judge every distance override against the field it
produces, remove the ones the evidence rejects, and PROPOSE new ones.

THE ONE TEST, APPLIED IN BOTH DIRECTIONS
    field_shift = the average of (this row's normalized_time / that athlete's
    own median normalized_time that year). 1.0 means the field lands where its
    runners normally land. It is corroboration from evidence the override did
    not generate, unlike "disagrees with meets.distance" -- which flagged
    2,446 divisions of which ~73% turned out fine.

    REMOVE when an override is an outlier for its distance class AND deleting
    it demonstrably improves the field. PROPOSE when a division with no
    override is an outlier AND a real race distance would demonstrably fix it.

★ COMPARED TO THE CLASS, NEVER TO 1.0. Measured baselines: 1609 races
  average 1.098 as a class, 5000 sits at 1.000, 6437 at 0.982. Judging every
  division against 1.0 would condemn 55 mile races for being mile races.

★ THE ARITHMETIC IS INVERTIBLE, WHICH IS WHY PROPOSING IS POSSIBLE AT ALL.
  normalized_time scales as (target/d)^K, so a wrong distance moves the whole
  field by (d_true/d_used)^K and the true distance falls out:

      d_implied = d_used * (shift / baseline) ^ (1/K)

  Verified on Colina 5k, which had a bad 6000 override and field_shift 0.820:
      6000 * (0.820/0.994)^(1/1.06) = 4,975  ->  snaps to 5000, the truth.

⚠ PROPOSALS ARE NEVER WRITTEN. --propose emits a CSV for review. A generated
  add-list is exactly the mechanism that produced the overrides being deleted
  here (`implied=4648 snap_err=-3.2% fast_rows=12` -- a detector inferring
  distance from times and snapping to a rung, with the snap error recorded and
  ignored). Here the snap error is a GATE, and a human is the other one.

Usage:
    python tools/audit_overrides.py --rebuild            # dry run, both lists
    python tools/audit_overrides.py --rebuild --write    # apply removals only
    python tools/audit_overrides.py --rebuild --propose  # + write the CSV
"""
import csv, io, os, re, sys, statistics

sys.path.insert(0, "scripts"); sys.path.insert(0, "engine")

PATH     = os.path.join("engine", "corrections.py")
CSV_PATH = "override_proposals.csv"

# ! K MUST MATCH THE NORMALIZER'S DISTANCE EXPONENT.
#   ⚠ AND IT IS NOW ONLY AN APPROXIMATION. normalize_distance uses a fitted
#     potential per pool with PER-POOL ANCHORS; 1.06 is its fallback exponent.
#     The error makes both directions CONSERVATIVE -- removals need a bigger
#     measured gain to clear MIN_GAIN, proposals need a cleaner snap -- so it
#     under-acts rather than misfires. Revisit after any refit.
K = 1.06

MIN_ROWS      = 15     # a division needs this many results to testify
MIN_CLASS     = 20     # a distance class needs this many divisions to be one
OUTLIER       = 0.12   # |shift / baseline - 1| past this = removal candidate
MIN_GAIN      = 0.05   # a removal must close at least this much of the gap
SANE_DISTANCE = (500, 20000)

# ⚠ PROPOSING IS HELD TO A HIGHER BAR THAN REMOVING, and deliberately so. A
#   removal returns a division to a scraped fact; a proposal invents a value
#   with nothing behind it but the times, and a wrong one propagates into the
#   spline fit, the pool means and every athlete in that field.
ADD_OUTLIER   = 0.18   # bigger anomaly required to propose than to remove
ADD_SNAP_TOL  = 0.03   # implied distance must land within 3% of a real one
ADD_MIN_CHANGE = 0.08  # and differ from the stored value by at least this
ADD_MIN_ROWS  = 30     # and rest on a real field, not fifteen runners

# The distances races are actually held at. An implied value that does not
# land on one of these is not a mis-recorded distance -- it is a hard course,
# bad weather, or a tired championship field, and none of those is fixed by
# editing a number.
LADDER = (1200, 1500, 1609, 1931, 2000, 2011, 2400, 2414, 2500, 2574, 2816,
          3000, 3200, 3218, 4000, 4023, 4180, 4800, 4828, 5000, 5149, 5500,
          6000, 6437, 7000, 8000, 8047, 10000)


# ★ BOTH SOURCES, OR THE TOOL IS BLIND TO MOST OF THE CORPUS.
#
#   _LOAD used to join `meets` for the stored distance. `meets` describes anet
#   only, so every tfrrs division came back stored = NULL and judgeRemoval
#   bailed on "no sane stored distance to fall back to" -- the override
#   survived because the fallback could not be SEEN, not because it was good.
#
#   Measured: of 449,748 divisions with enough results, only 3,750 were even
#   eligible. And the clearest bad override in the corpus was among the
#   invisible ones -- Thetford (25930, divisions 6 to 9) carries a 6000
#   override on a 5000 race, its field shift is 0.83 while the meet's other
#   divisions read 1.01 to 1.11, and removing it predicts
#   0.83 * (6000/5000)^1.06 = 1.01. A clean removal the old query could not
#   even consider.
#
# ⚠ tfrrs KEEPS DISTANCE IN A JSON BLOB, NOT A COLUMN. meets_tfrrs is one row
#   per meet -- it has no div_id at all -- and division_distances is keyed by
#   the per-meet div_id as a STRING, hence the cast. Its flat `distance`
#   column is a meet-level value with no division to attach to, and is unused.
_DISTANCES = """
    DROP TABLE IF EXISTS div_distance;
    CREATE UNLOGGED TABLE div_distance AS
        SELECT meet_id, div_id, source, meet_name, course_name AS venue,
               distance::float AS distance
        FROM   meets
        WHERE  distance IS NOT NULL
        UNION ALL
        SELECT m.meet_id, (kv.key)::bigint, m.source, m.meet_name,
               m.venue_name, (kv.value ->> 'distance')::float
        FROM   meets_tfrrs m,
               LATERAL jsonb_each(m.division_distances) kv
        WHERE  m.division_distances IS NOT NULL
          AND  kv.value ->> 'distance' IS NOT NULL
          AND  kv.key ~ '^[0-9]+$';
    CREATE INDEX ON div_distance (meet_id, div_id);
    ANALYZE div_distance;
"""


# ★ IMPORTED, NOT A SECOND COPY. This tool and propose_distances judge the
#   same divisions against the same quantity; two texts of the same SQL is how
#   a removal starts disagreeing with a proposal about what a field shift is.
#
#   It also carries the fix that made both tools able to see a badly wrong
#   distance at all: rows the engine's sanity band suppressed are given a
#   shadow rating from the athlete's own rated races, so a division is judged
#   on all of its finishers rather than the handful whose times survived the
#   error. See _BUILD_SHIFT in propose_distances.
from propose_distances import _BUILD_SHIFT

# EVERY division, overridden or not -- the baselines are computed over all of
# them, so a class describes the population rather than the suspects.
_LOAD = f"""
    SELECT s.meet_id, s.div_id, d.meet_name,
           d.distance   AS stored,
           o.distance   AS override,
           s.n, s.n_shadow, s.field_shift
    FROM   ovr_shift s
    JOIN   dist_override o ON o.meet_id = s.meet_id AND o.div_id = s.div_id
    LEFT   JOIN div_distance d ON d.meet_id = s.meet_id AND d.div_id = s.div_id
    WHERE  s.n >= {MIN_ROWS} AND o.distance > 0
"""


# ------------------------------------------------------------------ #
#  BASELINES -- what "normal" looks like at each distance
# ------------------------------------------------------------------ #

def usedDistance(row):
    """The distance normalization actually applied: override beats stored."""
    return row["override"] if row["override"] else row["stored"]


def classBaselines(rows):
    """{distance: median field_shift} over every division at that distance.

    ★ MEDIAN, NOT MEAN. The point is to describe the typical division, and
      the outliers this script hunts would drag a mean toward themselves --
      the baseline would move to accommodate the thing being measured.
    """
    by_dist = {}
    for r in rows:
        d = usedDistance(r)
        if d:
            by_dist.setdefault(d, []).append(r["field_shift"])
    return {d: statistics.median(v)
            for d, v in by_dist.items() if len(v) >= MIN_CLASS}


def baselineFor(dist, baselines):
    """The class baseline, or the nearest measured class when it is rare.

    1.0 is the wrong fallback for a thin class -- it would read short-race
    inflation as an error. The nearest measured class is the better guess.
    """
    if not dist or not baselines:
        return 1.0
    if dist in baselines:
        return baselines[dist]
    return baselines[min(baselines, key=lambda d: abs(d - dist))]


def sane(d):
    return d is not None and SANE_DISTANCE[0] <= d <= SANE_DISTANCE[1]


# ------------------------------------------------------------------ #
#  REMOVE -- outlier for its class, AND deletion demonstrably helps
# ------------------------------------------------------------------ #

def predictAfterRemoval(row):
    """field_shift once meets.distance takes over from the override."""
    return row["field_shift"] * (row["override"] / row["stored"]) ** K


def judgeRemoval(row, baselines):
    """(verdict, reason, err_before, err_after) for one existing override."""
    err_now = abs(row["field_shift"] / baselineFor(row["override"], baselines) - 1)
    if err_now <= OUTLIER:
        return "keep", "normal for its class", err_now, None
    if not sane(row["stored"]):
        return "keep", "no sane stored distance to fall back to", err_now, None
    err_after = abs(predictAfterRemoval(row)
                    / baselineFor(row["stored"], baselines) - 1)
    if err_now - err_after < MIN_GAIN:
        return "keep", "removal would not improve it", err_now, err_after
    return "kill", "outlier, and removal fixes it", err_now, err_after


# ------------------------------------------------------------------ #
#  PROPOSE -- the same test, run backwards
# ------------------------------------------------------------------ #

def impliedDistance(row, baselines):
    """The distance that would put this field on its class baseline.

    normalized_time scales as (target/d)^K, so using d_used when the truth is
    d_true moves the whole field by (d_true/d_used)^K. Invert it. A shift
    above baseline means the field looks slow, which means the distance in
    use is too SHORT -- so the implied value is larger. Colina confirms the
    sign and the size: 6000 * (0.820/0.994)^(1/1.06) = 4,975.
    """
    used = usedDistance(row)
    return used * (row["field_shift"] / baselineFor(used, baselines)) ** (1.0 / K)


def snapToLadder(d):
    """(nearest real race distance, fractional error). Not a rounding -- a
    TEST. A field that is merely slow implies a distance between the rungs,
    and the error is what says so."""
    best = min(LADDER, key=lambda x: abs(x - d))
    return float(best), (d - best) / best


def siblingIndex(rows, baselines):
    """{course_id: [(distance_in_use, err_vs_class)]} for every division.

    ★ THE GATE THAT SEPARATES A WRONG DISTANCE FROM A HARD COURSE, and
      nothing else does. Both make a field look slow, and both imply a
      distance that lands near a ladder rung -- a test run measured a
      deliberately-slow 5000 course implying 6031 and snapping to 6000 at
      0.5% error, which the snap gate happily passed.

      They differ in ONE way: a mis-recorded distance is a property of the
      division that carries it, so OTHER divisions at the same venue race
      the proposed distance and land on their class baseline. A hard course
      is a property of the ground, so every division there is slow together
      and none of them corroborates anything.
    """
    idx = {}
    for r in rows:
        cid, d = r.get("course_id"), usedDistance(r)
        if cid is None or not sane(d):
            continue
        err = abs(r["field_shift"] / baselineFor(d, baselines) - 1)
        idx.setdefault(cid, []).append((d, err))
    return idx


def hasSibling(row, proposed, siblings):
    """Does another division at this venue race `proposed` and behave?"""
    for d, err in siblings.get(row.get("course_id"), ()):
        if abs(d / proposed - 1) < 0.02 and err <= OUTLIER:
            return True
    return False


def judgeProposal(row, baselines, siblings):
    """(verdict, reason, proposal-dict-or-None) for a division with none."""
    used = usedDistance(row)
    if not sane(used):
        return "skip", "no sane distance in use", None
    if row["n"] < ADD_MIN_ROWS:
        return "skip", f"field of {row['n']} is too small to propose from", None

    err_now = abs(row["field_shift"] / baselineFor(used, baselines) - 1)
    if err_now <= ADD_OUTLIER:
        return "skip", "normal for its class", None

    implied = impliedDistance(row, baselines)
    if not sane(implied):
        return "skip", f"implied {implied:.0f} is not a race distance", None

    snapped, snap_err = snapToLadder(implied)
    # ★ THE SNAP ERROR IS A GATE, NOT A FOOTNOTE. The generator that produced
    #   the overrides being deleted here recorded snap_err=-3.2% and shipped
    #   anyway. A field that is slow for some reason OTHER than distance
    #   implies a value between the rungs, and that is exactly what this
    #   catches.
    if abs(snap_err) > ADD_SNAP_TOL:
        return "skip", f"implied {implied:.0f} snaps poorly ({snap_err:+.1%})", None
    if abs(snapped / used - 1) < ADD_MIN_CHANGE:
        return "skip", "snaps back to the distance already in use", None

    # Would it actually help? Same improvement test the removal path uses.
    after = row["field_shift"] * (used / snapped) ** K
    err_after = abs(after / baselineFor(snapped, baselines) - 1)
    if err_now - err_after < MIN_GAIN:
        return "skip", "the proposal would not improve it", None

    # ⚠ THE LAST AND STRICTEST GATE. Everything above is satisfied just as
    #   well by a course that is simply slow. Only a healthy sibling at the
    #   proposed distance says the ground is fine and the number is wrong.
    if not hasSibling(row, snapped, siblings):
        return "skip", ("no division at this venue races "
                        f"{snapped:.0f} and behaves"), None

    return "propose", "outlier, and one real distance fixes it", {
        "meet_id": row["meet_id"], "div_id": row["div_id"],
        "meet_name": row["meet_name"] or "", "n": row["n"],
        "course_id": row.get("course_id"),
        "in_use": round(used), "field_shift": round(row["field_shift"], 4),
        "class_baseline": round(baselineFor(used, baselines), 4),
        "implied": round(implied, 1), "proposed": round(snapped),
        "snap_err": round(snap_err, 4),
        "err_before": round(err_now, 4), "err_after": round(err_after, 4)}


# ------------------------------------------------------------------ #
#  REPORT AND EDIT
# ------------------------------------------------------------------ #

def reportBaselines(baselines):
    print(f"\n[audit] distance-class baselines ({len(baselines)} classes):")
    for d in sorted(baselines):
        print(f"    {d:>8.0f} m   median shift {baselines[d]:.3f}")


def reportRemovals(verdicts):
    kills = [v for v in verdicts if v[0] == "kill"]
    print(f"\n[audit] REMOVE: {len(kills)} overrides condemned")
    if kills:
        print(f"    {'meet/div':<22}{'stored':>8}{'ovr':>8}{'shift':>8}"
              f"{'err':>7}{'after':>7}  meet")
    for _, _, e0, e1, row in sorted(kills, key=lambda v: -(v[2] - v[3])):
        print(f"    {row['meet_id']}/{row['div_id']:<14}"
              f"{row['stored']:>8.0f}{row['override']:>8.0f}"
              f"{row['field_shift']:>8.3f}{e0:>7.3f}{e1:>7.3f}"
              f"  {(row['meet_name'] or '')[:36]}")
    return {(v[4]["meet_id"], v[4]["div_id"]) for v in kills}


def writeProposals(props):
    """CSV for review. Every column the reviewer needs to disagree with it."""
    if not props:
        print("\n[audit] PROPOSE: nothing clears the bar")
        return
    cols = ["meet_id", "div_id", "meet_name", "course_id", "n", "in_use",
            "proposed",
            "implied", "snap_err", "field_shift", "class_baseline",
            "err_before", "err_after"]
    with io.open(CSV_PATH, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for p in sorted(props, key=lambda p: -(p["err_before"] - p["err_after"])):
            w.writerow(p)
    print(f"\n[audit] PROPOSE: {len(props)} candidates -> {CSV_PATH}")
    print("    NOT written to corrections.py. Review first.")
    for p in sorted(props, key=lambda p: -(p["err_before"] - p["err_after"]))[:15]:
        print(f"    {p['meet_id']}/{p['div_id']:<12}{p['in_use']:>7} ->"
              f"{p['proposed']:>7}  shift {p['field_shift']:<7}"
              f"snap {p['snap_err']:+.1%}  n={p['n']:<5} {p['meet_name'][:30]}")


# ★ ENTRY-SHAPED, NOT LINE-SHAPED, and NOT anchored to the start of a line.
#   corrections.py mostly writes one override per line, but not always -- 14
#   entries in the current file share a line with a neighbour:
#
#       (26284, 0): 5000.0,   (26284, 1): 5000.0,   # NLC Round #1 both 5k
#
#   An anchored line pattern gets both of those wrong at once. It cannot SEE
#   the second entry, so condemning it removes nothing and the override stays
#   live; and if it condemns the first it deletes the whole line, taking an
#   innocent override with it. One failure is silent, the other is worse than
#   silent.
_ENTRY = re.compile(r"\((\d+),\s*(\d+)\)\s*:\s*[\d.]+\s*,")

# What may remain on a line once its entries are gone before the line itself
# is dropped: whitespace, or whitespace and a trailing comment.
_LEFTOVER = re.compile(r"^\s*(#.*)?$")


def stripLines(text, keys):
    """Delete every `(meet, div): dist,` ENTRY whose key is condemned.

    ⚠ EVERY COPY, WHEREVER IT SITS. corrections.py holds the same key in many
      dict literals -- audit_overrides measured 7.2 copies each -- and the
      later one wins, so leaving a single copy behind leaves the override
      live and which one decides is a matter of file position.

    ! A LINE IS ONLY DELETED WHEN NOTHING BUT A COMMENT IS LEFT ON IT. An
      entry sharing a line with a neighbour is cut out of the line and the
      neighbour stays exactly where it was.

    Returns (text, n_entries_removed).
    """
    kept, dropped = [], 0

    def cut(line):
        nonlocal dropped
        n_before = len(_ENTRY.findall(line))
        if not n_before:
            return line
        removed = []

        def sub(m):
            if (int(m.group(1)), int(m.group(2))) in keys:
                removed.append(m.group(0))
                return ""
            return m.group(0)

        out = _ENTRY.sub(sub, line)
        if not removed:
            return line
        dropped += len(removed)
        # Nothing but whitespace and maybe a comment survived -> drop the line.
        if _LEFTOVER.match(out.strip("\r\n")):
            return ""
        return out

    for line in text.splitlines(keepends=True):
        out = cut(line)
        if out:
            kept.append(out)
    return "".join(kept), dropped


def loadRows(cur, conn, rebuild):
    # ! ALWAYS REBUILT, EVEN WITHOUT --rebuild. It scans the meet tables, not
    #   results, and a stale copy would judge overrides against last run's
    #   distances without saying so.
    print("[audit] building the two-source distance table...")
    cur.execute(_DISTANCES)
    conn.commit()
    cur.execute("SELECT source, count(*) FROM div_distance GROUP BY 1 ORDER BY 1")
    for src, n in cur.fetchall():
        print(f"    {src}: {n:,} divisions with a distance")

    if rebuild:
        print("[audit] rebuilding ovr_shift from current ratings...")
        cur.execute(_BUILD_SHIFT)
        conn.commit()
    cur.execute(_LOAD)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["field_shift"] = float(r["field_shift"])
        r["stored"] = float(r["stored"]) if r["stored"] is not None else None
        r["override"] = float(r["override"]) if r["override"] is not None else None
    return rows


def main(write=False, rebuild=False, propose=False):
    from database import getConn

    with getConn() as conn, conn.cursor() as cur:
        rows = loadRows(cur, conn, rebuild)
    have = [r for r in rows if r["override"]]
    none = [r for r in rows if not r["override"]]
    print(f"[audit] {len(rows):,} divisions with >= {MIN_ROWS} results "
          f"({len(have):,} overridden, {len(none):,} not)")

    baselines = classBaselines(rows)
    reportBaselines(baselines)

    verdicts = [(*judgeRemoval(r, baselines), r) for r in have]
    keys = reportRemovals(verdicts)

    if propose:
        siblings = siblingIndex(rows, baselines)
        props = [p for v, _, p in
                 (judgeProposal(r, baselines, siblings) for r in none)
                 if v == "propose"]
        writeProposals(props)

    if not keys:
        print("\n[audit] no removals to apply")
        return
    text = io.open(PATH, encoding="utf-8", newline="").read()
    out, dropped = stripLines(text, keys)
    print(f"\n[audit] {dropped} lines match "
          f"({dropped / max(len(keys), 1):.1f} copies per key)")
    if not write:
        print("[audit] DRY RUN -- pass --write to apply removals")
        return
    io.open(PATH + ".bak", "w", encoding="utf-8", newline="").write(text)
    io.open(PATH, "w", encoding="utf-8", newline="").write(out)
    print(f"[audit] wrote {PATH} (backup at {PATH}.bak)")


if __name__ == "__main__":
    main(write="--write" in sys.argv,
         rebuild="--rebuild" in sys.argv,
         propose="--propose" in sys.argv)