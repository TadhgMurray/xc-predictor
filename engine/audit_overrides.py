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
# ★ MEASURED, NOT CHOSEN. --sweep prices every candidate against the whole
#   corpus, and the curve is almost flat: 0.12 condemns 85 overrides of 3,569
#   and 0.04 condemns 91. Six divisions separate the strictest bar from the
#   loosest, because MIN_GAIN and "a sane stored distance to fall back to" are
#   doing the real work -- this gate only decides whether to look.
#
#   0.12 was excluding Thetford 25930/7 by 0.0033: a 6000m override on a 5000m
#   race, shift 0.8777 against a 6000m class baseline of 0.9936, whose removal
#   was independently confirmed correct and would have taken its error from
#   0.117 to 0.066. A bar that misses a case you can verify by hand, at a cost
#   of three divisions, is set wrong.
OUTLIER       = 0.08   # |shift / baseline - 1| past this = removal candidate
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
from propose_distances import (_BUILD_SHIFT, staleness, impliedUsed,
                               shiftColumns)

# ★ THE SIGNATURE A CLASS BASELINE CANNOT SEE, AND THE ONE THE SITE SHOWS.
#
#   First to the Finish 2025 at Detweiller Park -- a three-mile course, 4828m
#   in `meets` -- carried a dist_override of 8000. The whole field was
#   normalised against 8000, so the two stages AGREED and the anchor gate
#   passed every row: both used the same wrong number. What reached the board
#   was twenty-five 21:30s rated 157-159.
#
#   The class baseline could not condemn it, because a division normalised at
#   8000 is compared against other divisions at 8000, and enough bad 8000
#   overrides define their own normal.
#
# ⚠ BUT THE RACE PAGE SHOWED THE TELL: the FASTEST runners had no rating at
#   all and only the slowest sixteen did. A wrong-long distance inflates the
#   entire field, so the head goes past the engine's 20..200 rail and is
#   suppressed, while the tail lands at 150-163 and is published. SEC-HS
#   Jamboree 1: 17:14 through 24:45 unrated, 24:52 through 27:01 rated
#   149-163.
#
# ★ SO THE TEST IS "ARE THE UNRATED RUNNERS FASTER THAN THE RATED ONES". No
#   race does that. It needs no pool, no baseline and no distance -- only the
#   times, which are the one thing an override cannot change.
#
#   And within one division rating is inversely proportional to TIME (same
#   distance, same course, same difficulty), so the head's missing rating can
#   be reconstructed:
#
#       implied_head = median_rating * median_time_rated / fastest_time
#
#   SEC-HS: 157 * 1545 / 1034 = 235, which is why it is not on the page.
#   Removing the override multiplies every rating by (stored/override)^K =
#   (5000/8000)^1.06 = 0.60, putting the head at 141 and inside the rail.
_RATING_STATS = """
    DROP TABLE IF EXISTS ovr_ratings;
    CREATE UNLOGGED TABLE ovr_ratings AS
    WITH rows AS (
        SELECT r.meet_id, r.div_id, r.time_seconds, r.speed_rating
        FROM   results r
        JOIN   dist_override o
                 ON o.meet_id = r.meet_id AND o.div_id = r.div_id
        WHERE  r.time_seconds > 0
        UNION ALL
        SELECT r.meet_id, r.div_id, r.time_seconds, r.speed_rating
        FROM   results_tf r
        JOIN   dist_override o
                 ON o.meet_id = r.meet_id AND o.div_id = r.div_id
        WHERE  r.time_seconds > 0
    )
    SELECT meet_id, div_id,
           count(*) FILTER (WHERE speed_rating IS NOT NULL)  AS n_rated,
           count(*) FILTER (WHERE speed_rating IS NULL)      AS n_unrated,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating)
               FILTER (WHERE speed_rating IS NOT NULL)       AS med_rating,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY time_seconds)
               FILTER (WHERE speed_rating IS NOT NULL)       AS med_time_rated,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY time_seconds)
               FILTER (WHERE speed_rating IS NULL)           AS med_time_unrated,
           min(time_seconds)                                 AS fastest_time
    FROM   rows
    GROUP  BY meet_id, div_id;
    CREATE INDEX ON ovr_ratings (meet_id, div_id);
    ANALYZE ovr_ratings;
"""

# EVERY division, overridden or not -- the baselines are computed over all of
# them, so a class describes the population rather than the suspects.
_LOAD = f"""
    SELECT s.meet_id, s.div_id, d.meet_name,
           d.distance   AS stored,
           o.distance   AS override,
           s.n, s.n_shadow, s.field_shift, {{extra}},
           g.n_rated, g.n_unrated, g.med_rating, g.med_time_rated,
           g.med_time_unrated, g.fastest_time
    FROM   ovr_shift s
    -- ⚠ LEFT, AND THIS WAS THE WHOLE BUG. It was an INNER join with
    --   `o.distance > 0` in the WHERE, so a division with no override could
    --   not survive the query -- and main() then computed
    --       none = [r for r in rows if not r["override"]]
    --   which was therefore ALWAYS EMPTY. The census printed "4,673
    --   overridden, 0 not" and PROPOSE reported "nothing clears the bar"
    --   because it had nothing to judge. judgeProposal has been fully
    --   implemented and unreachable: this tool has never once been able to
    --   propose an override for a division that did not already have one.
    --
    --   26359/0 -- Ox Bow Park, JV Minutemen Classic, labelled 8046m -- is
    --   exactly that shape. ovr_shift HAS it: that table is built over the
    --   whole corpus with no dist_override filter, and it reconstructs a
    --   shadow rating (k/nt) for rows the engine never rated, which is what
    --   a division like this is made of. propose_distances reads 455,420
    --   divisions out of the same table. The evidence was always there.
    --
    --   The distance test moves into the join so a NULL override means "no
    --   override" instead of dropping the row.
    LEFT   JOIN dist_override o ON o.meet_id = s.meet_id
                               AND o.div_id = s.div_id
                               AND o.distance > 0
    LEFT   JOIN div_distance d ON d.meet_id = s.meet_id AND d.div_id = s.div_id
    LEFT   JOIN ovr_ratings g ON g.meet_id = s.meet_id AND g.div_id = s.div_id
    WHERE  s.n >= {MIN_ROWS}
      AND  (o.distance > 0 OR {{want_all}})
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


# A suppressed head needs enough of both groups to be a pattern rather than
# two runners, and the unrated group has to be clearly faster -- not a second
# apart, which is just the rail's edge falling between two finishers.
SUPPRESS_MIN   = 5      # rated rows, and unrated rows, before this can fire
SUPPRESS_GAP   = 0.05   # the unrated median must be this much faster
ENGINE_RAIL    = 200.0  # speed_ratings' own upper rail, which is what suppresses


def suppressedHead(row):
    """(is_inverted, implied_head_before, implied_head_after) for a division.

    ★ WITHIN ONE DIVISION, RATING IS INVERSELY PROPORTIONAL TO TIME. Same
      course, same distance, same difficulty for every runner in it -- so the
      only thing separating two of their ratings is the clock. That makes the
      missing head reconstructable from the surviving tail, with no model:

          implied_head = med_rating * med_time_rated / fastest_time

    ⚠ AND THE INVERSION IS THE FINDING, NOT THE ARITHMETIC. A division where
      the runners WITHOUT a rating are FASTER than the runners with one has
      had its whole field inflated past the engine's rail, head first. No
      race does that on its own.
    """
    n_r, n_u = row.get("n_rated") or 0, row.get("n_unrated") or 0
    med_r, med_t = row.get("med_rating"), row.get("med_time_rated")
    med_u, fastest = row.get("med_time_unrated"), row.get("fastest_time")
    if (n_r < SUPPRESS_MIN or n_u < SUPPRESS_MIN
            or not med_r or not med_t or not med_u or not fastest):
        return False, None, None
    inverted = float(med_u) < float(med_t) * (1.0 - SUPPRESS_GAP)
    head = float(med_r) * float(med_t) / float(fastest)
    after = head * (row["stored"] / row["override"]) ** K if row["stored"] else None
    return inverted, head, after


def judgeRemoval(row, baselines):
    """(verdict, reason, err_before, err_after) for one existing override.

    TWO INDEPENDENT WAYS TO CONDEMN AN OVERRIDE, because the first one has a
    blind spot the corpus walked into. The class test compares a division
    against others at the SAME distance -- so an override that moves a field
    to 8000m is judged against the 8000m class, and enough wrong 8000m
    overrides make that class their own normal. The second test never looks
    at another division.
    """
    inverted, head, head_after = suppressedHead(row)
    err_now = abs(row["field_shift"] / baselineFor(row["override"], baselines) - 1)

    # ★ THE SUPPRESSED HEAD, FIRST, because it is the stronger evidence: it
    #   rests on this division's own clock rather than on a population.
    if inverted and sane(row["stored"]) and head and head > ENGINE_RAIL:
        if head_after and head_after < ENGINE_RAIL:
            return ("kill",
                    f"the unrated runners are FASTER than the rated ones; "
                    f"head implies {head:.0f}, and removal brings it to "
                    f"{head_after:.0f}",
                    err_now, err_now)
        return ("keep",
                f"head implies {head:.0f} but removal would leave it at "
                f"{head_after or 0:.0f} -- the distance is not the whole "
                f"story here",
                err_now, None)

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
    heads = [v for v in kills if "unrated runners" in v[1]]
    print(f"\n[audit] REMOVE: {len(kills)} overrides condemned "
          f"({len(heads)} of them by a suppressed head)")
    if kills:
        print(f"    {'meet/div':<22}{'stored':>8}{'ovr':>8}{'shift':>8}"
              f"{'err':>7}{'after':>7}  meet")
    for _, why, e0, e1, row in sorted(kills,
                                      key=lambda v: -(v[2] - (v[3] or 0))):
        print(f"    {row['meet_id']}/{row['div_id']:<14}"
              f"{row['stored']:>8.0f}{row['override']:>8.0f}"
              f"{row['field_shift']:>8.3f}{e0:>7.3f}{(e1 or 0):>7.3f}"
              f"  {(row['meet_name'] or '')[:36]}")
        if "unrated runners" in why:
            print(f"        ⚠ {why}")
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


def loadRows(cur, conn, rebuild, propose=False):
    # ! ALWAYS REBUILT, EVEN WITHOUT --rebuild. It scans the meet tables, not
    #   results, and a stale copy would judge overrides against last run's
    #   distances without saying so.
    print("[audit] building the two-source distance table...")
    cur.execute(_DISTANCES)
    conn.commit()
    cur.execute("SELECT source, count(*) FROM div_distance GROUP BY 1 ORDER BY 1")
    for src, n in cur.fetchall():
        print(f"    {src}: {n:,} divisions with a distance")

    # ! ALWAYS REBUILT TOO, AND CHEAP: it reads only the divisions that have
    #   an override, not the corpus.
    print("[audit] measuring what each override's field actually rates...")
    cur.execute(_RATING_STATS)
    conn.commit()

    if rebuild:
        print("[audit] rebuilding ovr_shift from current ratings...")
        cur.execute(_BUILD_SHIFT)
        conn.commit()
    # ⚠ THE CACHE CAN PREDATE THIS CODE. See propose_distances.shiftColumns:
    #   an ovr_shift built before the staleness check has no t_over_nt, and
    #   naming it kills a tool somebody ran to fix something else.
    extra, fresh_cache = shiftColumns(cur)
    if not fresh_cache:
        print("[audit] ⚠ ovr_shift predates the staleness check -- no "
              "t_over_nt/pool, so\n"
              "        'built at' will read unknown. The suppressed-head "
              "verdict does not\n"
              "        use it and is unaffected; rebuild with --rebuild for "
              "the rest.")
    # Only pay for the corpus when something is going to read it: the
    # removal path judges overrides, and loading 450k rows to do that
    # would be a large cost for no verdict.
    cur.execute(_LOAD.format(extra=extra,
                             want_all="TRUE" if propose else "FALSE"))
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["field_shift"] = float(r["field_shift"])
        for k in ("med_rating", "med_time_rated", "med_time_unrated",
                  "fastest_time", "t_over_nt"):
            r[k] = float(r[k]) if r.get(k) is not None else None
        r["stored"] = float(r["stored"]) if r["stored"] is not None else None
        r["override"] = float(r["override"]) if r["override"] is not None else None
    return rows


# ★ WHERE THE REFERENCE COMES FROM, WHICH IS THE QUESTION "HOW WAS THIS
#   CREATED" ACTUALLY ASKS.
#
#   field_shift is athlete-by-athlete, exactly as it should be: every row is
#   compared against that athlete's own median rating for the year. So a
#   division only looks wrong when the SAME athletes rate differently
#   elsewhere -- which is the right test.
#
# ⚠ BUT THE REFERENCE IS BUILT FROM RATINGS, AND RATINGS ALREADY CONTAIN
#   EVERY OVERRIDE. If some of an athlete's other races sit at divisions that
#   are themselves wrongly overridden to 8000m, their median comes out on the
#   8000m scale -- and this division, at its true distance, then looks slow by
#   exactly (8000/true)^K. Which is what the corpus shows:
#
#       Detweiller  field_shift 1.6944   (8000/4828)^1.06 = 1.708
#       SEC-HS      field_shift 1.6536   (8000/5000)^1.06 = 1.646
#
#   A shift that equals the distance ratio to within 1% is not a field that
#   ran slow. It is a reference that has already moved, and the proposal then
#   "corrects" this division onto the same wrong scale. One bad override
#   recruits the next through the athletes they share.
#
# ! SO THE DIAGNOSTIC SPLITS THE REFERENCE IN TWO: the same shift measured
#   against every one of the athlete's other races, and against only those at
#   divisions carrying NO override. If those two numbers disagree, the shift
#   is measuring the overrides rather than the race.
_WHY_SHIFT = """
    WITH here AS (
        SELECT r.person_id,
               substring(r.date, 1, 4)::int AS yr,
               r.speed_rating
        FROM   results r
        WHERE  r.meet_id = %(meet)s AND r.div_id = %(div)s
          AND  r.person_id IS NOT NULL
    ), ref AS (
        SELECT h.person_id, r.speed_rating,
               (o.meet_id IS NOT NULL) AS overridden
        FROM   here h
        JOIN   results r
                 ON r.person_id = h.person_id
                AND substring(r.date, 1, 4)::int = h.yr
                AND NOT (r.meet_id = %(meet)s AND r.div_id = %(div)s)
        LEFT   JOIN dist_override o
                 ON o.meet_id = r.meet_id AND o.div_id = r.div_id
        WHERE  r.speed_rating > 0
    )
    SELECT count(*)                                      AS n_ref,
           count(*) FILTER (WHERE overridden)             AS n_over,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating)
                                                          AS med_all,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating)
               FILTER (WHERE NOT overridden)              AS med_clean
    FROM   ref
"""

# The same split, per athlete, so the aggregate can be disbelieved.
_WHY_SHIFT_WHO = """
    WITH here AS (
        SELECT r.person_id, substring(r.date, 1, 4)::int AS yr,
               r.speed_rating AS rating_here
        FROM   results r
        WHERE  r.meet_id = %(meet)s AND r.div_id = %(div)s
          AND  r.person_id IS NOT NULL AND r.speed_rating > 0
    )
    SELECT h.person_id, h.rating_here,
           count(r.*)                                     AS n_ref,
           count(r.*) FILTER (WHERE o.meet_id IS NOT NULL) AS n_over,
           round(percentile_cont(0.5) WITHIN GROUP
                 (ORDER BY r.speed_rating)::numeric, 1)    AS med_all,
           round(percentile_cont(0.5) WITHIN GROUP
                 (ORDER BY r.speed_rating)
                 FILTER (WHERE o.meet_id IS NULL)::numeric, 1) AS med_clean
    FROM   here h
    JOIN   results r
             ON r.person_id = h.person_id
            AND substring(r.date, 1, 4)::int = h.yr
            AND NOT (r.meet_id = %(meet)s AND r.div_id = %(div)s)
    LEFT   JOIN dist_override o
             ON o.meet_id = r.meet_id AND o.div_id = r.div_id
    WHERE  r.speed_rating > 0
    GROUP  BY h.person_id, h.rating_here
    HAVING count(r.*) >= 3
    ORDER  BY count(r.*) DESC
    LIMIT  %(lim)s
"""


def whyShift(key, cur, ratio=None, limit=8):
    """Print where this division's reference ratings come from."""
    meet_id, div_id = key
    params = {"meet": meet_id, "div": div_id}
    cur.execute(_WHY_SHIFT, params)
    n_ref, n_over, med_all, med_clean = cur.fetchone()
    if not n_ref:
        print("\n  reference        none: nobody in this division has another "
              "rated race that year, so field_shift has nothing to compare "
              "against")
        return
    print(f"\n  reference        {n_ref:,} other rated races by these "
          f"athletes that year")
    print(f"                   {n_over:,} of them "
          f"({100.0 * n_over / n_ref:.1f}%) are at divisions that ALSO carry "
          f"an override")
    if med_all:
        print(f"  their median     {float(med_all):.1f} over everything"
              + (f", {float(med_clean):.1f} over the un-overridden races only"
                 if med_clean else ", and NONE of it is un-overridden"))
    if med_all and med_clean and float(med_clean) > 0:
        drift = float(med_all) / float(med_clean)
        # ! drift - 1, NOT drift. A ratio of 1.32 is 32% off, and printing it
        #   as +132% is the kind of number somebody acts on.
        print(f"  ⚠ the reference itself sits {drift - 1:+.1%} above its "
              f"clean part" if abs(drift - 1) > 0.02 else
              f"  the reference agrees with its clean part ({drift:.3f})")
    if ratio:
        print(f"  ! field_shift {ratio:.4f} against a distance ratio of "
              f"{(8000 / 4828) ** K:.4f}-ish: a shift that equals the ratio "
              f"is a moved REFERENCE, not a slow field")

    cur.execute(_WHY_SHIFT_WHO, {**params, "lim": limit})
    who = cur.fetchall()
    if who:
        print(f"\n  {'person':>11}{'here':>7}{'refs':>6}{'ovr':>5}"
              f"{'median':>8}{'clean':>8}   ratio here")
        for pid, here, n_r, n_o, m_all, m_clean in who:
            r_all = float(m_all) / float(here) if here else 0
            clean = f"{float(m_clean):>8.1f}" if m_clean else f"{'--':>8}"
            print(f"  {pid:>11}{float(here):>7.1f}{n_r:>6}{n_o:>5}"
                  f"{float(m_all):>8.1f}{clean}   {r_all:>5.2f}")


def explainRemoval(key, rows, baselines, cur):
    """Why was this override not condemned? Walk the removal bar out loud."""
    meet_id, div_id = key
    print(f"\n{'=' * 68}\nWHY NOT REMOVED: {meet_id}/{div_id}\n{'=' * 68}")

    cur.execute("SELECT distance FROM dist_override "
                "WHERE meet_id = %s AND div_id = %s", (meet_id, div_id))
    ov = cur.fetchone()
    if not ov:
        print("  ⛔ THERE IS NO OVERRIDE on this division. Nothing to remove --\n"
              "     if its distance is wrong, that is a PROPOSAL, and\n"
              "     propose_distances.py --explain is the tool.")
        return
    # ! `venue`, NOT `course_name`. This tool's div_distance aliases the anet
    #   column to venue so the tfrrs half can union into it; propose_distances
    #   builds the same table under the other name. Two texts of one table is
    #   how a column that exists in one tool is missing in the other.
    cur.execute("SELECT distance, venue FROM div_distance "
                "WHERE meet_id = %s AND div_id = %s LIMIT 1", (meet_id, div_id))
    stored = cur.fetchone()
    print(f"  override         {float(ov[0]):.0f}m")
    print(f"  stored distance  "
          + (f"{float(stored[0]):.0f}m   course {stored[1]!r}" if stored
             else "NONE -- there is no scraped value to fall back to"))

    cur.execute("SELECT n, n_shadow, field_shift FROM ovr_shift "
                "WHERE meet_id = %s AND div_id = %s", (meet_id, div_id))
    sr = cur.fetchone()
    if not sr:
        print("  ⛔ STOPPED: not in ovr_shift -- no row here has a person_id "
              "with\n     a median rating and >= 3 rated races to scale from.")
        return
    n, n_shadow, shift = sr
    print(f"  ovr_shift        n={n:,} ({n_shadow:,} shadow-rated), "
          f"field_shift={float(shift):.4f}")
    if n < MIN_ROWS:
        print(f"  ⛔ STOPPED: n < MIN_ROWS ({n} < {MIN_ROWS}).")
        return

    r = next((x for x in rows if (x["meet_id"], x["div_id"]) == key), None)
    if r is None:
        print("  ⛔ STOPPED: not in the loaded set.")
        return
    verdict, reason, err_now, err_after = judgeRemoval(r, baselines)

    # ★ THE DIVISION'S OWN CLOCK, BEFORE ANY POPULATION IS CONSULTED. This is
    #   the test that caught First to the Finish and SEC-HS Jamboree, which
    #   the class baseline called normal.
    inverted, head, head_after = suppressedHead(r)
    print(f"\n  rated / unrated  {r.get('n_rated') or 0:,} rated, "
          f"{r.get('n_unrated') or 0:,} unrated")
    if r.get("med_time_rated") and r.get("med_time_unrated"):
        print(f"  median time      {r['med_time_rated']:.1f}s rated  vs  "
              f"{r['med_time_unrated']:.1f}s unrated"
              + ("   ⚠ THE UNRATED ARE FASTER" if inverted else ""))
    if head:
        print(f"  implied head     {head:.0f} at the fastest time "
              f"({r['fastest_time']:.1f}s), rail {ENGINE_RAIL:.0f}")
        if head_after:
            print(f"  if removed       {head_after:.0f}")

    # ★ IS THE SHIFT EVEN MEASURING THIS CORPUS? staleness compares the
    #   distance the stored normalized_time was built with against the one
    #   the distance tables report. When they differ, field_shift describes a
    #   state that no longer exists -- and the suppressed-head verdict below,
    #   which reads only the clock, is the one to trust.
    r["used"] = r["override"] or r["stored"]
    stale, implied_built = staleness(r)
    if implied_built:
        print(f"\n  built at         {implied_built:.0f}m -- what the stored "
              f"normalized_time implies, read against the {r.get('pool')} "
              f"anchor")
        print(f"  tables say       {r['used']:.0f}m in use"
              + (f"   ⚠ STALE by {implied_built / r['used'] - 1:+.1%}: "
                 f"field_shift is measuring a corpus that no longer exists"
                 if stale else ""))

    whyShift(key, cur, ratio=r["field_shift"])

    base_ov = baselineFor(r["override"], baselines)
    print(f"\n  class baseline   {base_ov:.4f} for the OVERRIDE "
          f"({r['override']:.0f}m)")
    print(f"  err vs class     {err_now:.4f}   (OUTLIER bar {OUTLIER})")
    if err_after is not None:
        base_st = baselineFor(r["stored"], baselines)
        print(f"  if removed       shift "
              f"{predictAfterRemoval(r):.4f} vs {base_st:.4f} for "
              f"{r['stored']:.0f}m  -> err {err_after:.4f}")
        print(f"  gain             {err_now - err_after:.4f}   "
              f"(MIN_GAIN {MIN_GAIN})")
    else:
        # ★ SHOWN EVEN WHEN THE FIRST GATE STOPPED IT. "normal for its class"
        #   is the verdict, but what somebody wants to know next is what
        #   removal WOULD have done -- and refusing to compute it because the
        #   bar already said no is how a near miss stays invisible.
        if sane(r["stored"]):
            would = abs(predictAfterRemoval(r)
                        / baselineFor(r["stored"], baselines) - 1)
            print(f"  if removed       err would be {would:.4f} "
                  f"(gain {err_now - would:+.4f}) -- not evaluated, the "
                  f"OUTLIER gate stopped first")
    print(f"\n  verdict          {verdict.upper()}: {reason}")
    if verdict == "keep" and err_now <= OUTLIER:
        print(f"  ⛔ It missed the OUTLIER bar by {OUTLIER - err_now:.4f}. "
              f"Run --sweep to see\n     what a different bar would condemn "
              f"across the whole corpus.")


def sweep(rows, baselines):
    """What each OUTLIER bar would condemn, across the whole corpus.

    ★ BECAUSE 0.12 IS A GUESS AND THETFORD MISSES IT BY 0.004. A bar that
      excludes a case you can verify by hand is either the wrong bar or the
      right bar with a wrong case behind it, and a count per candidate is the
      only way to tell which without reading 3,569 divisions.

    ⚠ READ THE CLASS SPREAD FIRST. reportBaselines shows every class sitting
      between about 0.96 and 1.03, so a division 12% from its class is far
      outside the natural spread -- and the question is not whether 0.116 is
      an anomaly but how many ordinary divisions come with it if the bar
      moves down to catch it.
    """
    global OUTLIER
    original = OUTLIER
    print("\n[audit] WHAT EACH OUTLIER BAR WOULD CONDEMN\n")
    print(f"    {'bar':>6}{'condemned':>12}{'% of overrides':>16}")
    print("    " + "-" * 34)
    for bar in (0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30):
        OUTLIER = bar
        n = sum(1 for r in rows if judgeRemoval(r, baselines)[0] == "kill")
        mark = " *" if abs(bar - original) < 1e-9 else ""
        print(f"    {bar:>6.2f}{n:>12,}{100.0 * n / max(len(rows), 1):>15.1f}%"
              f"{mark}")
    OUTLIER = original
    print("\n    * the bar in force. Every one of these still has to clear "
          "MIN_GAIN\n      and have a sane stored distance to fall back to.")


def main(write=False, rebuild=False, propose=False, explain_keys=(),
         do_sweep=False):
    from database import getConn

    with getConn() as conn, conn.cursor() as cur:
        rows = loadRows(cur, conn, rebuild, propose)
    have = [r for r in rows if r["override"]]
    none = [r for r in rows if not r["override"]]
    print(f"[audit] {len(rows):,} divisions with >= {MIN_ROWS} results "
          f"({len(have):,} overridden, {len(none):,} not)")
    if not propose:
        print("[audit] un-overridden divisions NOT loaded -- pass --propose "
              "to judge them")

    baselines = classBaselines(rows)
    reportBaselines(baselines)

    if do_sweep:
        sweep(rows, baselines)
        return

    if explain_keys:
        # Its own exit: asking "why not" must never write corrections.
        with getConn() as conn, conn.cursor() as cur:
            for key in explain_keys:
                explainRemoval(key, rows, baselines, cur)
        return

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
    def _keys(argv):
        out = []
        for i, a in enumerate(argv):
            if a == "--explain" and i + 1 < len(argv):
                meet, _, div = argv[i + 1].partition("/")
                out.append((int(meet), int(div or 0)))
        return tuple(out)

    main(write="--write" in sys.argv,
         rebuild="--rebuild" in sys.argv,
         explain_keys=_keys(sys.argv),
         do_sweep="--sweep" in sys.argv,
         propose="--propose" in sys.argv)