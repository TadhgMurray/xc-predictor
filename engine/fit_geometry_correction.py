# Project: xc-predictor
# File:    engine/fit_geometry_correction.py
# Purpose: STEP 2 of the geometry-correction pipeline — the FIT. Reads the two
#          raw point clouds diagnose_geometry_pairs.py wrote, fits a correction
#          surface to each, and pickles them for the engine. This file FITS; the
#          diagnostic MEASURED. Two surfaces, fit INDEPENDENTLY (the whole reason
#          the clouds were separated):
#
#            LENGTH   — a 1D POTENTIAL g(length). The correction from track A to
#                       track B is g(A) - g(B) in log-time. We do NOT fit the
#                       pairwise (from,to) surface directly: a free surface can
#                       contradict itself (200->400 disagreeing with 200->300->400)
#                       and isn't exactly reversible. A potential makes both
#                       impossible by construction — one value per length, every
#                       pair DERIVED by subtraction. A->B->A == 1 for free.
#
#            BANKING  — a surface over (track_length, event_distance). No
#                       composition law to honor, so we fit it as richly as the
#                       cloud supports: 2D spline, falling back to 1D-over-length,
#                       then a binned median, chosen by cross-validated error. The
#                       fitter REPORTS which tier it used and why.
#
#          Pipeline position:
#            diagnose_geometry_pairs.py -> (2 CSV clouds) -> THIS FILE
#            -> geometry_spline.pkl -> engine (loaded once, like normalize_distance)

import sys
import os
import csv
import math
import pickle

import numpy as np
from scipy.interpolate import UnivariateSpline, SmoothBivariateSpline

# Bare imports off scripts/ kept for convention/parity, though this file is
# pure file-in/file-out (no DB): it consumes the diagnostic's CSVs.
sys.path.insert(0, "scripts")


# ------------------------------------------------------------------ #
# CONSTANTS  (every knob named once)
# ------------------------------------------------------------------ #

# Where the diagnostic wrote the clouds, and where we write the fit.
DEFAULT_DATA_DIR   = "engine/data"
LENGTH_CSV         = "geometry_length_cloud.csv"
BANKING_CSV        = "geometry_banking_cloud.csv"
SPLINE_OUT         = "geometry_spline.pkl"

# The length the potential is pinned to (g(REFERENCE) = 0). A standard flat 400m
# outdoor track is the natural zero — every other length's correction is relative
# to it, matching normalize_distance's FLAT_COURSE_DIFFICULTY = 0 convention.
REFERENCE_LENGTH = 400.0

# Weight given to the anchor row in the potential solve. Large so g(REFERENCE) is
# effectively forced to 0 rather than merely encouraged.
ANCHOR_WEIGHT = 1e6

# Smoothing for the spline drawn THROUGH the solved node potentials. 0 = pass
# through the nodes exactly (they're already the de-noised estimates); raise it
# if sparse nodes make the curve overshoot between lengths.
LENGTH_SMOOTHING = 0.0

# Banking tier gates: minimum data before a richer fit is even attempted.
MIN_POINTS_2D       = 200    # below this, a 2D surface over-fits
MIN_DISTINCT_2D     = 3      # need >=3 distinct lengths AND distances for 2D
MIN_POINTS_1D       = 30     # below this, fall to a binned median
CV_FOLDS            = 5      # k-fold cross-validation for tier selection

# Minimum track length admitted to the LENGTH fit. Below this, "tracks" are
# straightaways (no turns), where the shorter-=-more-turns premise is false —
# their backwards ratios (measured: every 100->X median < 1.0) would bend a
# false hook into g at the short end. Diagnostic still SHOWS them; the fit
# refuses them. 150 splits the observed population: 100/120/133 out,
# 160 and up (real ovals) in.
MIN_FIT_TRACK_LENGTH = 150.0

# Distance bands for the rank-1 shape s(d) and the banking distance
# models. SAME edges the census scripts used — the fitter's built-in
# report stays comparable to the tables that motivated this upgrade.
SHAPE_EDGES  = [0, 80, 250, 700, 1500, 3500, 99999]
SHAPE_LABELS = ["<=79m", "80-249m", "250-699m",
                "700-1499m", "1500-3499m", "3500m+"]

# A band must have this many samples to earn its own fitted s value;
# thinner bands are skipped and the spline interpolates across them.
MIN_BAND_N = 500

# A banked track length must have this many samples to earn a distance
# SPLINE; below it keeps a single median (Iowa: n=1,938 -> median).
MIN_BANKING_SPLINE_N = 20_000

# CONSTANTS: adopt the assumed-400 bridge into the SHIPPED artifact? Default
# False — first runs are measure-only: read the dual-anchor report, adopt by
# flipping this once annotated and assumed anchors agree (measure-then-adopt,
# same as era's discipline).
ADOPT_ASSUMED = True


# ------------------------------------------------------------------ #
# TINY NUMERIC HELPERS
# ------------------------------------------------------------------ #

# _median
# Purpose:   Middle value — used to de-noise the many pairs of one transition
#            into a single robust constraint. Median not mean: one fall-day ratio
#            shouldn't drag the constraint.
# Arguments: xs — non-empty list/array of floats.
# Output:    the median as a float.
def _median(xs) -> float:
    return float(np.median(xs))


# _rmse
# Purpose:   Root-mean-square error between predictions and truths — the score
#            the banking tier-selection minimises.
# Arguments: preds, trues — equal-length sequences of floats.
# Output:    sqrt(mean((pred - true)^2)); inf if empty (so an unusable tier loses).
def _rmse(preds, trues) -> float:
    if len(preds) == 0:
        return float("inf")
    p = np.asarray(preds, dtype=float)
    t = np.asarray(trues, dtype=float)
    return float(np.sqrt(np.mean((p - t) ** 2)))


# _clamp
# Purpose:   Keep a query length inside the fitted range, so an unseen length
#            never triggers a wild spline extrapolation — it pins to the nearest
#            end instead.
# Arguments: x — value; lo/hi — the fitted bounds.
# Output:    x clamped to [lo, hi].
def _clamp(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)

# _bandIndex
# Purpose:   Which SHAPE_EDGES band a race distance falls in.
# Arguments: dist — event distance in meters.
# Output:    index into SHAPE_LABELS. zip(edges, edges[1:]) walks the
#            consecutive (lo, hi) interval pairs.
def _bandIndex(dist) -> int:
    for i, (lo, hi) in enumerate(zip(SHAPE_EDGES, SHAPE_EDGES[1:])):
        if lo <= dist < hi:
            return i
    return len(SHAPE_LABELS) - 1

# _filterFittableLengths
# Purpose:   Admissibility gate for the LENGTH fit: drop cloud rows where
#            EITHER track is shorter than min_length. A pair with one
#            straightaway end is as unusable as two — the ratio still
#            compares turn-racing to no-turn-racing.
# Arguments: length_cloud — raw rows (length_from, length_to,
#              event_distance, ratio) as read from the CSV.
#            min_length   — the admissibility floor.
# Output:    (kept, n_dropped) — survivors and the count refused, RETURNED
#            (not printed) so the caller books it into the report.
def _filterFittableLengths(length_cloud, min_length):
    kept = [row for row in length_cloud
            if row[0] >= min_length and row[1] >= min_length]
    return kept, len(length_cloud) - len(kept)

# _compareAnchors  (LENGTH FIT section, before fitLengthPotential)
# Purpose:   THE agreement check: solve the potential twice — annotated-only
#            and with the bridge — and print key nodes side by side. Assumed
#            edges are L<->400 only, so gaps between indoor lengths should
#            barely move (they're pinned by 90K direct edges); the ABSOLUTE
#            values may move — that movement IS the anchor re-placement, and
#            its size vs the ±0.4% SE is the verdict. Disagreement beyond
#            that = the seam-weather bias is real; we talk before adopting.
# Arguments: annotated_cloud, full_cloud — 5-field rows, pre-filtered.
# Output:    (nodes_annotated, nodes_full) — also printed.
def _compareAnchors(annotated_cloud, full_cloud):
    n_a, _ = _solveNodePotentials(_aggregateTransitions(annotated_cloud),
                                  REFERENCE_LENGTH)
    n_f, _ = _solveNodePotentials(_aggregateTransitions(full_cloud),
                                  REFERENCE_LENGTH)
    print("\n  ANCHOR CHECK (annotated-only vs +assumed-400 bridge):")
    print(f"    {'length':>8} {'annotated':>10} {'+assumed':>10} {'shift':>8}")
    for L in sorted(set(n_a) & set(n_f)):
        print(f"    {L:>7.0f}m {n_a[L]:>+10.4f} {n_f[L]:>+10.4f} "
              f"{(n_f[L] - n_a[L]):>+8.4f}")
    return n_a, n_f


# ------------------------------------------------------------------ #
# LOADING THE CLOUDS  (read back exactly what the diagnostic wrote)
# ------------------------------------------------------------------ #

# _readCloudCSV
# Purpose:   Read one cloud CSV into float tuples, tolerating BOTH schema
#            generations: the pre-bridge 4-field length rows and the tagged
#            5-field rows. Old rows are padded with 0.0 — and 0.0 is not a
#            filler, it's the CORRECT value: the assumed flag's 0 means
#            "annotated", and every row an old CSV contains IS annotated
#            (the bridge didn't exist when it was written). Downstream
#            therefore sees exactly ONE schema, and the dispatch-on-data
#            principle holds at the file layer too.
# Arguments: path       — the CSV path.
#            min_fields — the base schema width (4 for length, 3 for banking).
#            pad_to     — the new schema width, or None when the file has
#                         only ever had one schema (banking: no flag).
# Output:    list of float tuples, every one of width pad_to (or min_fields
#            when pad_to is None). Empty list if the file is missing.
def _readCloudCSV(path, min_fields, pad_to=None):
    if not os.path.exists(path):
        print(f"  [warn] cloud not found: {path}")
        return []
    # `or` returns its first truthy operand: pad_to when given, else
    # min_fields — so `target` is THE one width every row leaves with.
    target = pad_to or min_fields
    rows = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)                     # drop the header row
        for raw in reader:
            if len(raw) == target:             # new schema -> take as-is
                rows.append(tuple(float(v) for v in raw))
            elif len(raw) == min_fields:       # old schema -> pad
                vals = [float(v) for v in raw]
                # list-multiplication: [0.0] * 1 == [0.0]; += extends the
                # list by that many zeros (here exactly one, the flag slot).
                vals += [0.0] * (target - min_fields)
                rows.append(tuple(vals))
            # any other width: malformed line -> skip, same policy as before
    return rows


# ================================================================== #
# LENGTH FIT — the potential g(length)
# ================================================================== #
#
# Each length cloud row is (length_from, length_to, event_distance, ratio) with
# from < to and ratio = time(shorter)/time(longer). In log-time that's a single
# linear constraint on the potential:
#
#       g(from) - g(to) = log(ratio)
#
# Many rows share a transition (e.g. dozens of 200<->400 pairs); we collapse each
# transition to its MEDIAN log-ratio (robust) and remember the count (its weight).
# Then we solve all transitions at once for the node potentials, anchored so
# g(REFERENCE) = 0, and draw a smooth curve through the nodes for continuous use.


# _aggregateTransitions
# Purpose:   Collapse the raw length cloud into one robust constraint per distinct
#            length transition: its median log-ratio and how many pairs backed it.
#            WHY: dozens of athletes may have run the SAME 200<->400 transition;
#            each is a noisy estimate of the same true effect. Taking the median
#            per transition de-noises it into one trustworthy number, and the
#            count becomes that number's weight in the solve (more pairs = trust).
# Arguments: length_cloud — list of 4-tuples (length_from, length_to,
#              event_distance, ratio), where length_from < length_to (shorter
#              track first) and ratio = time(shorter) / time(longer). ratio > 1
#              means the shorter, turnier track produced the slower time.
#              event_distance is carried but UNUSED here (length effect only).
# Output:    dict mapping (length_from, length_to) -> (median_log_ratio, count):
#              median_log_ratio = log of the typical time ratio for that
#                transition (the additive form the potential is linear in),
#              count = how many cloud rows backed that transition (its weight).
def _aggregateTransitions(length_cloud) -> dict:
    buckets = {}
    # unpack each cloud row; _dist (event_distance) is ignored here on purpose.
    for (lf, lt, _dist, ratio, _flag) in length_cloud:
        # ratio must be positive to take its log; a <=0 ratio is corrupt -> skip.
        if ratio <= 0:
            continue
        # math.log turns the MULTIPLICATIVE ratio into an ADDITIVE log-ratio, the
        # quantity the potential adds/subtracts. setdefault((k),[]) gives this
        # transition's running list (creating it on first sight), then we append.
        buckets.setdefault((lf, lt), []).append(math.log(ratio))
    # collapse each transition's list of log-ratios to (median, how_many).
    return {key: (_median(logs), len(logs)) for key, logs in buckets.items()}


# _solveNodePotentials
# Purpose:   Solve for one log-potential per distinct length, best-fitting all the
#            transition constraints at once (weighted least squares), anchored so
#            the reference length sits at 0.
#
#   THE PICTURE — think of it as a graph of lengths connected by measured gaps:
#
#         g(200)        g(300)        g(400)=0      g(600)
#           o-----+0.035---o----+0.025---o-----0.015---o
#                (measured) (measured)  (anchor) (measured)
#
#   Each transition is an EDGE saying "these two lengths differ by this log-ratio".
#   We have more edges than nodes (many transitions, few lengths), so the system
#   is over-determined — no single set of node values satisfies every noisy edge
#   exactly. Least squares finds the node values that come CLOSEST to all edges at
#   once, which averages out per-transition noise. One node is pinned (the anchor)
#   so the whole curve has an absolute zero instead of floating.
#
# Arguments: transitions — dict {(length_from, length_to): (median_log_ratio,
#              count)} from _aggregateTransitions. Each entry is one edge: the
#              measured log-gap between two lengths, plus its support count.
#            reference    — the length (metres) to pin at g = 0, e.g. 400.0. If
#              this exact length wasn't sampled, the nearest sampled length is
#              anchored instead (the curve is relative, so the choice only shifts
#              every value by a constant).
# Output:    (nodes, used_reference):
#              nodes = dict {length: g} — the solved log-potential for each
#                distinct length (g(reference) == 0; positive = slower than ref).
#              used_reference = the length actually anchored (may differ from the
#                requested reference if it wasn't in the data).
def _solveNodePotentials(transitions, reference):
    # Distinct lengths = the nodes (matrix columns). Sorted for a stable column
    # order. The set comprehension flattens every (from,to) pair into its lengths.
    lengths = sorted({L for (lf, lt) in transitions for L in (lf, lt)})
    col = {L: i for i, L in enumerate(lengths)}     # length -> its column index

    # We build three parallel lists: A rows (the equations), b (right-hand sides),
    # w (per-equation weights). One entry each, per transition.
    rows, b, w = [], [], []
    for (lf, lt), (med, count) in transitions.items():
        # One equation per transition: (+1 at `from`) + (-1 at `to`) = log-ratio,
        # i.e. g(from) - g(to) = measured gap. np.zeros makes an all-zero row the
        # width of the node count, then we set the two involved columns.
        r = np.zeros(len(lengths))
        r[col[lf]] = 1.0
        r[col[lt]] = -1.0
        rows.append(r)
        b.append(med)
        w.append(count)                              # more backing pairs -> trust

    # Anchor equation: pin the reference length to 0. If the exact reference
    # wasn't sampled, anchor the NEAREST length we have (min by absolute distance).
    used_ref = reference if reference in col else min(lengths, key=lambda L: abs(L - reference))
    anchor = np.zeros(len(lengths))
    anchor[col[used_ref]] = 1.0                      # (+1 at reference) = 0
    rows.append(anchor)
    b.append(0.0)
    w.append(ANCHOR_WEIGHT)                          # huge weight -> ~exactly 0

    # Weighted least squares. Standard reweighting trick: scaling each equation by
    # sqrt(weight) makes an ordinary least-squares solver minimise the WEIGHTED
    # squared error. sw[:, None] reshapes the weight vector to a column so it
    # multiplies row-wise (broadcasts across each row's columns).
    A = np.array(rows)
    b = np.array(b)
    sw = np.sqrt(np.array(w))
    # lstsq returns (solution, residuals, rank, singular_values); we want [0].
    coeffs, *_ = np.linalg.lstsq(A * sw[:, None], b * sw, rcond=None)

    # Map the solved coefficient vector back to {length: g}.
    nodes = {L: float(coeffs[col[L]]) for L in lengths}
    return nodes, used_ref


# _fitGSpline
# Purpose:   Draw a smooth continuous curve through the solved node potentials, so
#            g(length) can be evaluated at ANY length, not just the sampled ones.
#            The nodes are a handful of discrete (length, g) dots; this connects
#            them into a callable curve for in-between lengths.
# Arguments: nodes — dict {length: g} from _solveNodePotentials: one log-potential
#              per distinct sampled length (g(reference) == 0).
# Output:    (spline, min_len, max_len):
#              spline = a scipy UnivariateSpline; call spline(x) to get g at any
#                length x (returns a numpy scalar).
#              min_len / max_len = the smallest and largest sampled lengths, the
#                bounds we clamp queries to so we never extrapolate past the data.
def _fitGSpline(nodes):
    xs = sorted(nodes)                       # lengths, ascending (spline needs sorted x)
    ys = [nodes[x] for x in xs]              # their potentials, same order
    # Spline degree k must be < number of points. With many lengths use cubic (3);
    # with very few, drop toward linear (1) so the fit can't demand more points
    # than exist. max(1, ...) guarantees at least a linear fit.
    k = max(1, min(3, len(xs) - 1))
    # s = LENGTH_SMOOTHING controls how tightly the curve hugs the nodes (0 = pass
    # through them, since they're already the de-noised estimates).
    spline = UnivariateSpline(xs, ys, k=k, s=LENGTH_SMOOTHING)
    return spline, xs[0], xs[-1]

# _aggregateTransitionsByBand
# Purpose:   The banded version of _aggregateTransitions: collapse the raw
#            cloud to one robust constraint per (transition, distance band)
#            — its median log-ratio, count, and median distance. Aggregate-
#            then-compare: medians first, so per-pair noise cancels before
#            anything is fit (the lesson the r-metric taught).
# Arguments: length_cloud — filtered rows (lf, lt, event_distance, ratio).
# Output:    dict {(lf, lt, band_idx): (median_log_ratio, count,
#            median_distance)}. median_distance is where this constraint
#            "lives" on the d axis — used to place the spline knots.
def _aggregateTransitionsByBand(length_cloud) -> dict:
    buckets = {}
    for (lf, lt, dist, ratio, _flag) in length_cloud:
        if ratio <= 0:
            continue
        key = (lf, lt, _bandIndex(dist))
        buckets.setdefault(key, []).append((math.log(ratio), dist))
    out = {}
    for key, pairs in buckets.items():
        logs  = [p[0] for p in pairs]      # unzip the (log, dist) pairs
        dists = [p[1] for p in pairs]
        out[key] = (_median(logs), len(pairs), _median(dists))
    return out


# _solveBandShapes
# Purpose:   The rank-1 solve: for each band, the ONE scalar s that best
#            rescales g1's predictions onto that band's observations.
#            Weighted least squares with a closed form — for observations
#            obs ~= s * pred with weights w, the minimizing s is
#                s = sum(w*pred*obs) / sum(w*pred^2)
#            (differentiate the weighted squared error, set to zero).
#            w = the constraint's sample count, so 200->300's 13K-pair
#            bands dominate; near-zero-gap transitions self-silence
#            because their pred^2 contributes ~nothing to the denominator.
# Arguments: banded — _aggregateTransitionsByBand output.
#            nodes  — {length: g1} from the 1D solve.
# Output:    dict {band_idx: (s, n_total, mean_distance)} for bands with
#            n_total >= MIN_BAND_N. mean_distance = n-weighted center of
#            the band's constraints (the spline's x for this band).
def _solveBandShapes(banded, nodes) -> dict:
    num, den, n_tot, d_sum = {}, {}, {}, {}
    for (lf, lt, band), (med, n, med_dist) in banded.items():
        if lf not in nodes or lt not in nodes:
            continue
        pred = nodes[lf] - nodes[lt]           # g1's prediction for this edge
        num[band]   = num.get(band, 0.0)  + n * pred * med
        den[band]   = den.get(band, 0.0)  + n * pred * pred
        n_tot[band] = n_tot.get(band, 0)  + n
        d_sum[band] = d_sum.get(band, 0.0) + n * med_dist
    shapes = {}
    for band in num:
        if n_tot[band] >= MIN_BAND_N and den[band] > 0:
            shapes[band] = (num[band] / den[band],       # s
                            n_tot[band],                  # its evidence
                            d_sum[band] / n_tot[band])    # its x position
    return shapes


# _fitShapeSpline
# Purpose:   A smooth curve through the per-band (distance, s) points, so
#            s(d) is evaluable at ANY race distance.
# Arguments: shapes — _solveBandShapes output.
# Output:    (spline, min_dist, max_dist), or None when fewer than 2 bands
#            survived — the caller then falls back to plain 1D (s == 1).
def _fitShapeSpline(shapes):
    if len(shapes) < 2:
        return None
    pts = sorted((x, s) for (s, _n, x) in shapes.values())  # sort by distance
    xs  = [p[0] for p in pts]
    ys  = [p[1] for p in pts]
    k = max(1, min(3, len(xs) - 1))            # degree < point count
    return UnivariateSpline(xs, ys, k=k, s=0.0), xs[0], xs[-1]

# _splitByAssumed
# Purpose:   Fork the tagged cloud into the two populations the anchor
#            check compares: annotated-only (rows measured from real
#            annotations) and full (annotated + assumed-400 bridge rows).
#            NOTE full CONTAINS annotated — the comparison is "the fit as
#            it was" vs "the fit with the bridge added", not two disjoint
#            halves; a bridge-only solve couldn't even run (assumed edges
#            all touch 400, so indoor-to-indoor gaps would be unconstrained).
# Arguments: length_cloud — 5-field rows
#            (length_from, length_to, event_distance, ratio, assumed_flag).
# Output:    (annotated, full) — two lists of the SAME 5-field rows.
def _splitByAssumed(length_cloud):
    # row[4] is the assumed flag: 0 = annotated, 1 = bridge. The CSV reader
    # returns floats, so "not row[4]" (true for 0.0) is the annotated test.
    annotated = [row for row in length_cloud if not row[4]]
    return annotated, length_cloud       # full = everything, by definition


# _fitLevel
# Purpose:   The LENGTH LEVEL half of the fit — g1(L), the potential itself.
#            This is what the assumed-400 bridge is FOR: it pins the absolute
#            placement of the indoor cluster against 400m. So it reads the
#            anchor-chosen cloud (full when ADOPT_ASSUMED, else annotated).
# Arguments: level_cloud — the cloud the anchor decision selected (full/annotated).
#            reference   — the anchor length (g=0 there).
# Output:    (nodes, used_ref, spline, lo, hi, n_transitions) — everything the
#            artifact needs to describe the level curve.
def _fitLevel(level_cloud, reference):
    transitions = _aggregateTransitions(level_cloud)          # edges -> per-transition deltas
    nodes, used_ref = _solveNodePotentials(transitions, reference)  # WLS on the diff-graph
    spline, lo, hi = _fitGSpline(nodes)                       # spline through node potentials
    return nodes, used_ref, spline, lo, hi, len(transitions)


# _fitShape
# Purpose:   The DISTANCE-SHAPE half — s(d), how the length penalty scales with
#            race distance. This is TURN PHYSIOLOGY, measured from real
#            banked-vs-flat contrasts, so it must ALWAYS read `annotated`, never
#            the bridge. The assumed-400 rows are flat outdoor at the reference;
#            letting them in dilutes every band's ratio toward zero (worst at
#            long distances, where the bridge adds the most rows) — a level
#            correction leaking into a shape it has no evidence about.
# Arguments: annotated_cloud — annotated rows ONLY (real contrasts).
#            nodes           — the level nodes from _fitLevel (s is solved
#                              relative to g1, so it needs them).
# Output:    (shapes, shape_fit) — the per-band scalars and the fitted spline
#            (shape_fit is None when the bands can't support a fit).
def _fitShape(annotated_cloud, nodes):
    banded = _aggregateTransitionsByBand(annotated_cloud)     # per (transition, distance-band)
    shapes = _solveBandShapes(banded, nodes)                  # per-band scalar s vs g1
    shape_fit = _fitShapeSpline(shapes)                       # spline s(d) through bands
    return shapes, shape_fit


# fitLengthPotential
# Purpose:   The whole length fit, now anchor-aware:
#            filter -> split -> COMPARE anchors -> choose cloud -> fit.
#            The comparison always runs (measurement); the assumed bridge
#            only enters the shipped artifact when ADOPT_ASSUMED is True
#            (adoption). Measure-then-adopt as structure.
# Arguments: length_cloud — 5-field rows from the CSV.
#            reference    — anchor length (default REFERENCE_LENGTH).
# Output:    the length artifact dict; new keys vs before:
#              "anchor"    -> "assumed-400" or "annotated-only" — which
#                             population this pickle was actually fit on
#                             (self-describing artifact, same as "kind"),
#              "n_bridge"  -> how many bridge samples existed (adopted or not).
def fitLengthPotential(length_cloud, reference=REFERENCE_LENGTH) -> dict:
    length_cloud, n_dropped = _filterFittableLengths(length_cloud,
                                                     MIN_FIT_TRACK_LENGTH)
    annotated, full = _splitByAssumed(length_cloud)
    _compareAnchors(annotated, full)              # prints the ANCHOR CHECK

    # THE CHOICE: which population the LEVEL fit uses. len(full)-len(annotated)
    # = the bridge count (full contains annotated plus exactly the flagged rows).
    # NOTE: this choice governs the LEVEL ONLY. The SHAPE s(d) is turn physiology
    # and is ALWAYS fit on `annotated` — the bridge is flat outdoor 400m evidence
    # about the LEVEL, and would dilute s(d) toward zero at long distances if let in.
    level_cloud = full if ADOPT_ASSUMED else annotated

    # LEVEL: g1(L), anchored — reads the bridge when adopted.
    nodes, used_ref, spline, lo, hi, n_transitions = _fitLevel(level_cloud, reference)
    # SHAPE: s(d), physiology — reads annotated contrasts ONLY, regardless of adoption.
    shapes, shape_fit = _fitShape(annotated, nodes)

    art = {
        "kind":            "potential_1d",
        "spline":          spline,
        "nodes":           nodes,
        "min_len":         lo,
        "max_len":         hi,
        "reference":       used_ref,
        "n_transitions":   n_transitions,
        "n_dropped_short": n_dropped,
        "anchor":          "assumed-400" if ADOPT_ASSUMED else "annotated-only",
        "n_bridge":        len(full) - len(annotated),
    }
    if shape_fit is not None:
        s_spline, d_lo, d_hi = shape_fit
        art["kind"]         = "potential_rank1"
        art["shape_spline"] = s_spline
        art["min_dist"]     = d_lo
        art["max_dist"]     = d_hi
        art["shape_bands"]  = [(SHAPE_LABELS[b], shapes[b][1], shapes[b][0],
                                shapes[b][2]) for b in sorted(shapes)]
    return art

# ================================================================== #
# BANKING FIT — tiered surface over (track_length, event_distance)
# ================================================================== #
#
# Each banking row is (event_distance, track_length, ratio), ratio =
# time(flat)/time(banked) > 1 = banking helped. No composition law here, so we fit
# the value directly and as richly as the cloud allows:
#     tier "2d"     SmoothBivariateSpline over (length, distance)
#     tier "1d"     UnivariateSpline over length (distance collapsed)
#     tier "binned" median log-ratio per length, nearest-length lookup
# We cross-validate each feasible tier and keep the lowest-error one — so a thin
# cloud can't talk us into an over-fit surface.


# _groupBankingByLength
# Purpose:   Split the banking cloud into per-track-length point sets —
#            each length gets its OWN distance model (200m is rich, Iowa
#            is thin; one policy can't fit both).
# Arguments: banking_cloud — rows (event_distance, track_length, ratio).
# Output:    dict {track_length: [(distance, log_ratio), ...]}.
def _groupBankingByLength(banking_cloud) -> dict:
    groups = {}
    for (dist, length, ratio) in banking_cloud:
        if ratio <= 0:
            continue
        groups.setdefault(length, []).append((dist, math.log(ratio)))
    return groups


# _bankingBandMedians
# Purpose:   One length's points -> per-band (median_dist, median_z, n),
#            keeping bands with n >= MIN_BAND_N. Aggregate-then-fit again:
#            the spline goes through MEDIANS, never raw points.
# Arguments: points — [(distance, log_ratio), ...] for one length.
# Output:    list of (med_dist, med_z, n), ascending in distance.
def _bankingBandMedians(points) -> list:
    buckets = {}
    for dist, z in points:
        buckets.setdefault(_bandIndex(dist), []).append((dist, z))
    out = []
    for band in sorted(buckets):
        pts = buckets[band]
        if len(pts) < MIN_BAND_N:
            continue
        out.append((_median([p[0] for p in pts]),
                    _median([p[1] for p in pts]), len(pts)))
    return out


# _fitBankingModelForLength
# Purpose:   One length's model: a distance spline when the data can fund
#            it, a single median when it can't. The per-length replacement
#            for the old global tier ladder.
# Arguments: points — [(distance, log_ratio), ...] for one length.
# Output:    {"form": "spline", "spline", "lo", "hi", "n", "bands"}  or
#            {"form": "median", "z", "n"}.
def _fitBankingModelForLength(points) -> dict:
    if len(points) >= MIN_BANKING_SPLINE_N:
        meds = _bankingBandMedians(points)
        if len(meds) >= 2:
            xs = [m[0] for m in meds]
            ys = [m[1] for m in meds]
            k = max(1, min(3, len(xs) - 1))
            return {"form": "spline",
                    "spline": UnivariateSpline(xs, ys, k=k, s=0.0),
                    "lo": xs[0], "hi": xs[-1],
                    "n": len(points), "bands": meds}
    return {"form": "median",
            "z": _median([p[1] for p in points]), "n": len(points)}


# fitBankingSurface
# Purpose:   The whole banking fit: group by length, model each length.
# Arguments: banking_cloud — raw rows (event_distance, track_length, ratio).
# Output:    {"kind": "banking_by_distance", "models": {length: model},
#             "n": total points}.
def fitBankingSurface(banking_cloud) -> dict:
    groups = _groupBankingByLength(banking_cloud)
    models = {L: _fitBankingModelForLength(pts) for L, pts in groups.items()}
    return {"kind": "banking_by_distance", "models": models,
            "n": sum(len(p) for p in groups.values())}


# bankingFactor  (replacement — dispatches on kind; old pickles still work)
def bankingFactor(banking_art, length, distance) -> float:
    if banking_art.get("kind") == "banking_by_distance":
        models = banking_art["models"]
        nearest = min(models, key=lambda L: abs(L - length))
        m = models[nearest]
        if m["form"] == "spline":
            d = _clamp(distance, m["lo"], m["hi"])
            z = float(m["spline"](d))
        else:
            z = m["z"]
        return math.exp(z)
    return _bankingFactorLegacy(banking_art, length, distance)


# ================================================================== #
# PREDICTION HELPERS  (what the engine calls at inference)
# ================================================================== #

# lengthToReference
# Purpose:   Convert a time on a track of `length` to its 400m-reference
#            equivalent. Under rank1, the correction is g1(L)*s(d) — the
#            length effect scaled by how much of it THIS race distance
#            experiences.
# Arguments: length_art   — the length artifact (either kind).
#            time_seconds — the raw time.
#            length       — the track's length (m).
#            distance     — the RACE distance (m); None -> s = 1.0, i.e.
#                           the all-distance average (exactly the 1D
#                           behavior) — the honest neutral when the caller
#                           has no distance to give.
# Output:    reference-equivalent seconds.
def lengthToReference(length_art, time_seconds, length, distance=None) -> float:
    L = _clamp(length, length_art["min_len"], length_art["max_len"])
    g = float(length_art["spline"](L))
    if length_art.get("kind") == "potential_rank1" and distance is not None:
        d = _clamp(distance, length_art["min_dist"], length_art["max_dist"])
        g *= float(length_art["shape_spline"](d))
    return time_seconds * math.exp(-g)


# ================================================================== #
# REPORT + PERSIST + ORCHESTRATION
# ================================================================== #

# _reportLength
# Purpose:   Eyes-first length report: the node potentials (as %-effects), the
#            reference, and the residual-trend verdict on whether 2D is warranted.
def _reportLength(art) -> None:
    print(f"\n  LENGTH potential ({art['n_transitions']} transitions, "
          f"reference {art['reference']:g}m = 0):")
    if art.get("n_dropped_short"):        # .get: old pickles lack the key
        print(f"    refused {art['n_dropped_short']:,} samples with a track "
              f"< {MIN_FIT_TRACK_LENGTH:g}m (straightaways)")
    for L in sorted(art["nodes"]):
        g = art["nodes"][L]
        # exp(g) as a percentage relative to reference: +1.2% slower, etc.
        pct = (math.exp(g) - 1.0) * 100.0
        print(f"    {L:>6.0f}m   g={g:+.4f}   ({pct:+.2f}% vs reference)")
    # (in _reportLength, replacing the residual-trend print)
    if art.get("kind") == "potential_rank1":
        print("    distance shape s(d)  (each correction = g1 * s; "
              "s=1 is the all-distance average):")
        for label, n, s, x in art["shape_bands"]:
            print(f"      {label:<12} n={n:>7,}  s={s:+.3f}  (at ~{x:,.0f}m)")


def _reportBanking(art) -> None:
    print(f"\n  BANKING (per-length distance models, n={art['n']:,}):")
    for L in sorted(art["models"]):
        m = art["models"][L]
        if m["form"] == "median":
            pct = (math.exp(m["z"]) - 1.0) * 100.0
            print(f"    {L:g}m: single median  {pct:+.2f}%  (n={m['n']:,})")
        else:
            print(f"    {L:g}m: distance spline  (n={m['n']:,})")
            for x, z, n in m["bands"]:
                pct = (math.exp(z) - 1.0) * 100.0
                print(f"        ~{x:>6,.0f}m  n={n:>7,}  factor={pct:+.2f}%")


# _saveSplines
# Purpose:   Pickle both artifacts together, the way the engine will load them
#            (one read, one object), mirroring normalize_distance's spline file.
# Arguments: length_art, banking_art — the two fitted artifacts.
#            path                    — output pickle path.
# Output:    none (writes the file).
def _saveSplines(length_art, banking_art, path) -> None:
    with open(path, "wb") as f:
        pickle.dump({"length": length_art, "banking": banking_art}, f)


# runGeometryFit
# Purpose:   The whole STEP 2, top to bottom: read clouds -> fit both -> report ->
#            persist. Stays short; it ORCHESTRATES the fitters/reporters/writer.
# Arguments: data_dir — folder holding the cloud CSVs and receiving the pickle.
# Output:    (length_art, banking_art) — also returned for tests/callers.
def runGeometryFit(data_dir=DEFAULT_DATA_DIR):
    length_cloud  = _readCloudCSV(os.path.join(data_dir, LENGTH_CSV), 4, pad_to=5)
    banking_cloud = _readCloudCSV(os.path.join(data_dir, BANKING_CSV), 3)

    # Rows, not intentions: an empty cloud with the file PRESENT means a
    # schema mismatch ate every row — that must scream, not flow to None.
    if not length_cloud and os.path.exists(os.path.join(data_dir, LENGTH_CSV)):
        print("  [WARN] length CSV exists but 0 rows loaded — schema mismatch; "
              "the pickle will have NO length correction!")
    print(f"loaded {len(length_cloud):,} length samples, "
          f"{len(banking_cloud):,} banking samples")

    length_art  = fitLengthPotential(length_cloud) if length_cloud else None
    banking_art = fitBankingSurface(banking_cloud) if banking_cloud else None

    print("\n=== geometry fit ===")
    if length_art:
        _reportLength(length_art)
    if banking_art:
        _reportBanking(banking_art)

    out = os.path.join(data_dir, SPLINE_OUT)
    if length_art or banking_art:
        _saveSplines(length_art, banking_art, out)
        print(f"\nwrote fit -> {out}")
    else:
        print("\nno clouds found — nothing fitted.")
    return length_art, banking_art


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Fit the geometry corrections (length potential + banking surface).")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="folder with the cloud CSVs / for the output pickle")
    args = parser.parse_args()
    runGeometryFit(args.data_dir)