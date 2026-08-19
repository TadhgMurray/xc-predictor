"""
fit_state_offsets.py -- measure and fit per-state rating offsets.

    python scripts/fit_state_offsets.py                    # XC
    python scripts/fit_state_offsets.py --sport TF
    python scripts/fit_state_offsets.py --reference CA
    python scripts/fit_state_offsets.py --dump pairs.txt

THE QUESTION
    An athlete who races in two states IN THE SAME SEASON has one ability, so
    any difference between their ratings in each state is scale error. If each
    state's ratings are shifted by a constant then

        observed(a, b) = offset[a] - offset[b]

    and hundreds of observed gaps should be explainable by ~50 numbers. What is
    LEFT OVER answers whether a per-state correction is a valid fix:

        residuals ~ 0     -> per-state constants ARE the model
        residuals 1-2 pt  -> gaps are pair-specific; the cause is upstream

WHY THE PAIRING HAPPENS IN PYTHON
    The SQL version self-joined per_person_year to itself on person_id. The
    planner estimated 1 row, chose a nested loop over 14M meets_tf rows, and ran
    for 17+ minutes. Postgres cannot see that most people race in exactly one
    state, so it cannot size that join.

    Here SQL does only what it is good at -- one scan, one hash aggregate -- and
    Python does the pairing in a single streaming pass. Rows arrive ordered by
    (person, year), so itertools.groupby walks them in CONSTANT MEMORY; nothing
    larger than one person-season is ever held.

★ THE GAUGE PROBLEM
    Only DIFFERENCES are measured, so offsets are fixed only up to a shared
    additive constant. "Is TX low or is CA high" is a choice of reference, not
    a finding. --reference makes that choice explicit instead of hidden.
"""

import argparse
from collections import defaultdict
from itertools import groupby

import numpy as np

from database import getConn


# ---------------------------------------------------------------------------
# 1. LOADING
# ---------------------------------------------------------------------------

# One row per (person, year, state). No self-join: a single scan and a hash
# aggregate, which is the shape Postgres plans well.
#
# ORDER BY person_id, yr is what lets Python group in constant memory. It costs
# a sort of ~3-5M aggregated rows, far cheaper than the join it replaces.
#
# The state filters drop FIPS numeric codes ('01', '40'), New Zealand regions,
# and the lowercase 'os' junk -- all of which would otherwise be fitted as fake
# states and drag the offsets of everything they touch.
_SQL = {
    "XC": """
        SELECT r.person_id,
               substring(r.date, 1, 4)::int AS yr,
               m.state,
               avg(r.speed_rating)          AS mean_rating,
               avg(r.normalized_time)       AS mean_norm
        FROM results r
        JOIN meets m
          ON m.div_id = r.div_id
         AND m.source = r.source
        WHERE r.speed_rating IS NOT NULL
          AND r.normalized_time IS NOT NULL
          AND r.person_id IS NOT NULL
          AND m.state IS NOT NULL
          AND m.state ~ '^[A-Z]{2}$'
          AND r.date >= %(since)s
        GROUP BY 1, 2, 3
        ORDER BY 1, 2
    """,
    "TF": """
        SELECT r.person_id,
               substring(r.date, 1, 4)::int AS yr,
               m.state,
               avg(r.speed_rating)          AS mean_rating,
               avg(r.normalized_time)       AS mean_norm
        FROM results_tf r
        JOIN meets_tf m
          ON m.meet_id  = r.meet_id
         AND m.div_id   = r.div_id
         AND m.event_id = r.event_id
         AND m.source   = r.source
        WHERE r.speed_rating IS NOT NULL
          AND r.normalized_time IS NOT NULL
          AND r.person_id IS NOT NULL
          AND COALESCE(m.is_indoor, 0) = 0
          AND COALESCE(r.is_relay, 0)  = 0
          AND m.state IS NOT NULL
          AND m.state ~ '^[A-Z]{2}$'
          AND r.date >= %(since)s
        GROUP BY 1, 2, 3
        ORDER BY 1, 2
    """,
}


def streamPersonSeasons(sport, since):
    """
    Yield (person_id, yr, state, mean_rating, mean_norm), ordered by person+year.

    A NAMED cursor is a server-side cursor: Postgres keeps the result set and
    ships it in batches instead of materialising all ~3-5M rows in client
    memory at once. itersize sets the batch size.

    Named cursors must live inside a transaction, which `with getConn()`
    provides. This is the same streaming pattern speed_ratings_db uses.
    """
    with getConn() as conn:
        with conn.cursor(name="state_offset_stream") as cur:
            cur.itersize = 100_000
            cur.execute(_SQL[sport], {"since": since})

            for row in cur:
                yield row


# ---------------------------------------------------------------------------
# 2. PAIRING
# ---------------------------------------------------------------------------

def accumulatePairs(rows, progressEvery=500_000):
    """
    Walk person-seasons and tally every cross-state pair -> {(a,b): [n, sumDiff, sumRatio]}

    groupby yields consecutive runs sharing a key, so it REQUIRES the input to
    be sorted by that key -- which the ORDER BY guarantees. It does not sort,
    and given unsorted input it would silently split one person into fragments.

    Only person-seasons touching 2+ states produce anything; the rest fall
    through. That is the vast majority, and skipping them here is exactly what
    the SQL self-join could not do cheaply.

    States are ordered alphabetically within a pair so ('CA','TX') and
    ('TX','CA') accumulate into one bucket rather than two mirror-image ones.
    """
    pairs = defaultdict(lambda: [0, 0.0, 0.0])
    seen = 0

    for _key, group in groupby(rows, key=lambda r: (r[0], r[1])):
        entries = [(r[2], float(r[3]), float(r[4])) for r in group]
        seen += len(entries)

        if progressEvery and seen % progressEvery < len(entries):
            print(f"    ...{seen:,} person-season-states")

        if len(entries) < 2:
            continue

        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                first, second = entries[i], entries[j]

                if first[0] == second[0]:
                    continue
                if first[0] > second[0]:
                    first, second = second, first

                if second[2] <= 0:
                    continue

                bucket = pairs[(first[0], second[0])]
                bucket[0] += 1
                bucket[1] += first[1] - second[1]
                bucket[2] += first[2] / second[2]

    return pairs


def finalizePairs(pairs, minN):
    """
    Convert accumulator sums to means -> [(a, b, n, ratingDiff, normRatio), ...]

    minN is the evidence floor. Below ~25 athlete-seasons a pair's estimate is
    mostly noise, and with hundreds of pairs the noisy ones would drag the fit.
    """
    out = []
    for (a, b), (n, sumDiff, sumRatio) in pairs.items():
        if n < minN:
            continue
        out.append((a, b, n, sumDiff / n, sumRatio / n))

    out.sort(key=lambda p: -p[2])
    return out


# ---------------------------------------------------------------------------
# 3. THE FIT
# ---------------------------------------------------------------------------

def collectStates(pairs):
    """
    Sorted state list, plus name -> column index.

    SORTED so the matrix column order is deterministic. Otherwise the fitted
    numbers would depend on set iteration order and two runs on identical data
    could disagree.
    """
    names = sorted({s for p in pairs for s in p[:2]})
    return names, {n: i for i, n in enumerate(names)}


def buildSystem(pairs, index):
    """
    Weighted least-squares system -> (A, y, w).

    One ROW per pair, one COLUMN per state: +1 in column a, -1 in column b,
    target = the observed gap. That encodes `offset[a] - offset[b] = observed`.

    WEIGHTS are sqrt(n). Least squares minimises SQUARED residuals, so scaling
    a row by sqrt(n) makes its influence scale with n -- a pair resting on
    33,675 athletes counts ~1,000x more than one resting on 294.
    """
    A = np.zeros((len(pairs), len(index)))
    y = np.zeros(len(pairs))
    w = np.zeros(len(pairs))

    for p, (a, b, n, diff, _ratio) in enumerate(pairs):
        A[p, index[a]] = 1.0
        A[p, index[b]] = -1.0
        y[p] = diff
        w[p] = np.sqrt(n)

    return A, y, w


def solveOffsets(A, y, w):
    """
    Fit -> (offsets, rank).

    Scaling both sides by w converts the weighted problem to an ordinary one:
    minimising ||w*(Ax - y)||^2 equals minimising ||A'x - y'||^2. `w[:, None]`
    reshapes w into a column so numpy broadcasts down A's rows.

    ★ A IS RANK DEFICIENT BY DESIGN. Every row sums to zero, so the all-ones
    vector is in its nullspace: adding a constant to every offset changes
    nothing. lstsq returns the minimum-norm solution, and recenterOffsets then
    replaces that implicit gauge with the one we chose.
    """
    offsets, _res, rank, _sv = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)
    return offsets, rank


def stateWeights(names, pairs, index):
    """Per state: total athlete-seasons, and how many pairs it appears in."""
    weight = np.zeros(len(names))
    nPairs = np.zeros(len(names), dtype=int)

    for a, b, n, _d, _r in pairs:
        weight[index[a]] += n
        weight[index[b]] += n
        nPairs[index[a]] += 1
        nPairs[index[b]] += 1

    return weight, nPairs


def recenterOffsets(offsets, names, pairs, index, reference):
    """
    Apply an explicit gauge.

    'mean'      offsets sum to zero -- every state equally likely to be wrong.
    'weighted'  big states define zero -- "the bulk of the data is right".
    '<STATE>'   pin that state to zero and read others relative to it.

    NONE IS MORE CORRECT. Only differences were measured.
    """
    if reference == "mean":
        return offsets - offsets.mean()

    if reference == "weighted":
        weight, _nPairs = stateWeights(names, pairs, index)
        return offsets - np.average(offsets, weights=weight)

    if reference in index:
        return offsets - offsets[index[reference]]

    raise ValueError(f"unknown reference {reference!r}")


# ---------------------------------------------------------------------------
# 4. REPORTING
# ---------------------------------------------------------------------------

_MIN_PAIRS = 5
_MIN_SEASONS = 500
_HEAVY_PAIR = 500


def reportOffsets(offsets, names, pairs, index):
    """
    ★ READ `pairs` AND `athlete-seasons` BEFORE BELIEVING AN OFFSET. Every
    state gets a number, including ones tied in by three thin links. Those are
    fitted, not measured, and are flagged.
    """
    weight, nPairs = stateWeights(names, pairs, index)
    order = np.argsort(-offsets)

    print("\n  FITTED STATE OFFSETS")
    print(f"\n    {'state':>6} {'offset':>9} {'pairs':>7} {'athlete-seasons':>17}")

    for i in order:
        thin = nPairs[i] < _MIN_PAIRS or weight[i] < _MIN_SEASONS
        print(f"    {names[i]:>6} {offsets[i]:>+9.2f} {nPairs[i]:>7} "
              f"{int(weight[i]):>17,}{'   thin' if thin else ''}")

    solid = offsets[(nPairs >= _MIN_PAIRS) & (weight >= _MIN_SEASONS)]
    if solid.size:
        print(f"\n    well-linked states only: {solid.min():+.2f} to "
              f"{solid.max():+.2f}  ({solid.size} of {len(names)})")


def reportResiduals(offsets, pairs, index, show):
    """
    ★ THE TEST. A residual is the part of a gap NO per-state constant explains.

    Three RMS figures, because they say different things:
      weighted    as the fit saw it -- how well the model explains what it
                  actually optimised.
      unweighted  every pair equal. Much worse than weighted means the model
                  fits big pairs and misses small ones, which is thin-pair
                  NOISE rather than model failure.
      n>=500      the honest number: quality on pairs with real evidence.
    """
    rows = []
    for a, b, n, diff, ratio in pairs:
        predicted = offsets[index[a]] - offsets[index[b]]
        rows.append((abs(diff - predicted), a, b, n, diff, predicted,
                     diff - predicted, ratio))

    rows.sort(reverse=True)

    print(f"\n  PAIR RESIDUALS (worst {min(show, len(rows))} of {len(rows)})")
    print(f"\n    {'pair':>9} {'n':>8} {'observed':>9} {'predicted':>10} "
          f"{'residual':>9} {'norm_ratio':>11}")

    for _abs, a, b, n, diff, predicted, residual, ratio in rows[:show]:
        flag = "  <--" if abs(residual) > 1.5 else ""
        print(f"    {a + '-' + b:>9} {n:>8,} {diff:>+9.2f} {predicted:>+10.2f} "
              f"{residual:>+9.2f} {ratio:>11.4f}{flag}")

    resid = np.array([r[6] for r in rows])
    counts = np.array([r[3] for r in rows], dtype=float)
    heavy = counts >= _HEAVY_PAIR

    print(f"\n    pairs                        {len(rows):,}")
    print(f"    weighted RMS residual        "
          f"{np.sqrt(np.average(resid ** 2, weights=counts)):.3f}")
    print(f"    unweighted RMS residual      "
          f"{np.sqrt(np.mean(resid ** 2)):.3f}")
    if heavy.any():
        print(f"    weighted RMS, n>={_HEAVY_PAIR} only    "
              f"{np.sqrt(np.average(resid[heavy] ** 2, weights=counts[heavy])):.3f} "
              f"({int(heavy.sum())} pairs)")
    print(f"    worst residual               {max(abs(resid)):.3f}")

    print("\n    READ: weighted RMS under ~0.8 -> per-state constants explain")
    print("    the gaps and a scalar per state is a valid correction.")


def dumpPairs(pairs, path):
    """Write the measured pairs so a run can be reproduced without the DB."""
    with open(path, "w") as handle:
        for a, b, n, diff, ratio in pairs:
            handle.write(f"{a} {b} {n} {diff:.2f} {ratio:.4f}\n")
    print(f"\n  wrote {len(pairs):,} pairs to {path}")


# ---------------------------------------------------------------------------
# 5. ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Measure and fit per-state rating offsets. Read only.")
    parser.add_argument("--sport", choices=["XC", "TF"], default="XC")
    parser.add_argument("--since", default="2018-01-01")
    parser.add_argument("--min-n", type=int, default=25,
                        help="minimum athlete-seasons per pair (default 25)")
    parser.add_argument("--reference", default="mean",
                        help="gauge: 'mean', 'weighted', or a state code")
    parser.add_argument("--show", type=int, default=25)
    parser.add_argument("--dump", default=None,
                        help="also write measured pairs to this file")
    args = parser.parse_args()

    print("=" * 72)
    print(f"STATE OFFSET FIT -- {args.sport}, races since {args.since}")
    print("=" * 72)
    print("\n  streaming person-seasons...")

    rows = streamPersonSeasons(args.sport, args.since)
    raw = accumulatePairs(rows)
    pairs = finalizePairs(raw, args.min_n)

    print(f"\n  {len(raw):,} state pairs observed, "
          f"{len(pairs):,} with n >= {args.min_n}")

    if len(pairs) < 3:
        print("  not enough pairs to fit.\n")
        return

    if args.dump:
        dumpPairs(pairs, args.dump)

    names, index = collectStates(pairs)
    A, y, w = buildSystem(pairs, index)
    offsets, rank = solveOffsets(A, y, w)
    offsets = recenterOffsets(offsets, names, pairs, index, args.reference)

    print(f"\n  {len(names)} states, design matrix rank {rank} "
          f"(deficiency of 1 expected -- see solveOffsets)")
    print(f"  gauge: {args.reference}")

    reportOffsets(offsets, names, pairs, index)
    reportResiduals(offsets, pairs, index, args.show)
    print()


if __name__ == "__main__":
    main()