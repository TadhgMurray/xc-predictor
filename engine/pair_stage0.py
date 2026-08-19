# Project: xc-predictor
# Subset:  Pair Engine -- Stage 0, connectivity
#
# WHAT THIS DECIDES
# -----------------
# The pair engine estimates, in logs:
#
#     ln(norm) - form  =  alpha[athlete, season]  +  delta[cell]  +  eps
#
# which is the algebraic equivalent of least squares over every
# same-athlete-same-season venue PAIR: alpha cancels in the difference, so
# ability is never estimated against difficulty, it is differenced away.
#
# ★ THE ONE THING THAT CANNOT BE ASSUMED. That system determines delta only up
#   to ONE FREE CONSTANT PER CONNECTED COMPONENT of the bipartite graph
#   (athlete-season) <-> (cell). Within a component, every cell's level is
#   pinned relative to every other. ACROSS components nothing pins them --
#   there is no path of shared athletes, so the data contains no information,
#   and any number we print for an isolated cell is an artefact of whatever
#   default the solver fell into.
#
#   This is §2's gauge freedom, but localised and countable instead of global
#   and hidden. That is the whole advantage: the ALS engine had the same
#   problem and dealt with it by a region prior fighting a global re-centring,
#   which is what produced the runaway cell and the 5/7 anchors.
#
# So before any solver exists, we need to know: is there one giant component
# holding nearly every result, or thousands of fragments?
#
# READS THE PACKED CACHE, NOT THE DATABASE. course_keys are minted inside
# packResults; re-deriving them here would risk exactly the key-space mismatch
# that made loadCourseRegions silently match nothing on 47,795 XC cells.

import os
import sys

import numpy as np


# ------------------------------------------------------------------ #
# CHUNK 1 -- loading
# ------------------------------------------------------------------ #

# loadPack
# Purpose:   the packed arrays the engine already builds.
# Arguments: path -- .npz written by speed_ratings.saveCols.
# Output:    dict of arrays.
#
# Imported from speed_ratings so there is ONE reader for this format. If the
# pack layout changes, this file follows automatically instead of drifting.
def loadPack(path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)) or ".")
    from speed_ratings import loadCols
    return loadCols(path)


# ------------------------------------------------------------------ #
# CHUNK 2 -- the two node sets
# ------------------------------------------------------------------ #

# athleteSeasonCodes
# Purpose:   a dense 0..M-1 id per (athlete, season) -- one side of the graph.
# Arguments: athlete -- int code per row; year -- season year per row.
# Output:    (codes ndarray[int64], n_groups int)
#
# Syntax: np.unique with axis=0 on a stacked 2-column array finds the distinct
# pairs and return_inverse gives each row its group index. Stacking into ONE
# int64 key first is much faster than axis=0 on 61.6M rows -- multiplying the
# athlete code by a number larger than any season makes the combination unique
# without building a 2D array.
#
# ★ PER SEASON, NOT PER CAREER. The current engine holds one ability per
#   athlete for all time; a high schooler improving 4%/year for four years has
#   ~15% of variation forced into a single scalar, and the leftover lands in
#   whichever venues they happened to race -- which correlates with WHEN. Per
#   season absorbs that by construction.
def athleteSeasonCodes(athlete, year):
    key = athlete.astype(np.int64) * 10000 + year.astype(np.int64)
    _, codes = np.unique(key, return_inverse=True)
    return codes.astype(np.int64), int(codes.max()) + 1


# usableRows
# Purpose:   the rows that can carry information about a cell.
# Arguments: cols -- the packed dict.
# Output:    boolean mask.
#
# A row with no venue (course < 0) tells us nothing about any cell, and a
# non-positive normalized time is unusable in logs. Both are dropped here so
# every later count means "rows that actually constrain the system".
def usableRows(cols):
    return (cols["course"] >= 0) & (cols["norm"] > 0)


# ------------------------------------------------------------------ #
# CHUNK 3 -- components
# ------------------------------------------------------------------ #

# buildBipartite
# Purpose:   the sparse incidence matrix whose components we want.
# Arguments: group -- athlete-season code per row; course -- cell code per row;
#            n_groups, n_cells.
# Output:    scipy.sparse matrix, shape (n_groups + n_cells, same).
#
# Nodes 0..n_groups-1 are athlete-seasons; nodes n_groups.. are cells. One
# undirected edge per row. Duplicate edges are harmless -- connectivity only
# cares whether an edge exists, and coo_matrix tolerates repeats.
def buildBipartite(group, course, n_groups, n_cells):
    from scipy.sparse import coo_matrix

    n = n_groups + n_cells
    rows = group
    cols_ = course.astype(np.int64) + n_groups
    data = np.ones(rows.size, dtype=np.int8)
    return coo_matrix((data, (rows, cols_)), shape=(n, n))


# componentLabels
# Purpose:   component id per node.
# Output:    (n_components int, labels ndarray)
#
# `connected=False` treats the matrix as undirected, which is what we want:
# an athlete linking two cells connects them regardless of edge direction.
def componentLabels(graph):
    from scipy.sparse.csgraph import connected_components
    n_comp, labels = connected_components(graph, directed=False)
    return n_comp, labels


# ------------------------------------------------------------------ #
# CHUNK 4 -- reporting
# ------------------------------------------------------------------ #

# _sportOf
# Purpose:   'XC' / 'TF' / '?' from a course key, for the per-sport split.
def _sportOf(key):
    if not key:
        return "?"
    return key[:2] if key[:2] in ("XC", "TF") else "?"


# _componentSizes
# Purpose:   cells per component, and rows per component, descending by cells.
# Arguments: cell_labels -- component id per cell; rows_per_cell -- row counts.
# Output:    list of (component_id, n_cells, n_rows) sorted by n_cells desc.
def _componentSizes(cell_labels, rows_per_cell):
    n_comp = int(cell_labels.max()) + 1 if cell_labels.size else 0
    cells = np.bincount(cell_labels, minlength=n_comp)
    rows = np.bincount(cell_labels, weights=rows_per_cell, minlength=n_comp)
    order = np.argsort(-cells)
    return [(int(c), int(cells[c]), int(rows[c])) for c in order if cells[c]]


# reportComponents
# Purpose:   ★ THE STAGE-0 VERDICT. How much of the corpus sits in one
#            component, and therefore how many free constants the solver needs.
# Arguments: sizes -- from _componentSizes; n_cells, n_rows -- totals;
#            course_keys, cell_labels -- for the per-sport split.
def reportComponents(sizes, n_cells, n_rows, course_keys, cell_labels):
    print("[stage0] ---- bipartite connectivity ----")
    print(f"    cells {n_cells:,}   usable rows {n_rows:,}   "
          f"components {len(sizes):,}")

    if not sizes:
        print("    no cells; nothing to solve")
        return

    gc_id, gc_cells, gc_rows = sizes[0]
    print(f"    giant component: {gc_cells:,} cells "
          f"({100.0 * gc_cells / n_cells:.1f}%), "
          f"{gc_rows:,} rows ({100.0 * gc_rows / max(n_rows, 1):.1f}%)")

    # ★ Rows OUTSIDE the giant component are the ones whose difficulty is not
    #   identified against the rest of the corpus. That percentage is the honest
    #   ceiling on how much of the data this method can place on one scale.
    out_cells = n_cells - gc_cells
    out_rows = n_rows - gc_rows
    print(f"    outside GC:      {out_cells:,} cells, {out_rows:,} rows "
          f"({100.0 * out_rows / max(n_rows, 1):.2f}%)")

    # Per-sport share of the giant component: a merged solve is only worth
    # running if BOTH sports are inside it. If TF sits in its own component,
    # the sports share athletes on paper but not in the identified graph.
    in_gc = cell_labels == gc_id
    by_sport = {}
    for key, inside in zip(course_keys, in_gc):
        sport = _sportOf(key)
        n_in, n_tot = by_sport.get(sport, (0, 0))
        by_sport[sport] = (n_in + (1 if inside else 0), n_tot + 1)
    for sport in sorted(by_sport):
        n_in, n_tot = by_sport[sport]
        print(f"      {sport:>3}: {n_in:7,}/{n_tot:7,} cells in GC "
              f"({100.0 * n_in / max(n_tot, 1):5.1f}%)")

    print("    next largest components (cells, rows):")
    for _cid, c, r in sizes[1:6]:
        print(f"      {c:6,} cells   {r:9,} rows")

    singletons = sum(1 for _c, c, _r in sizes if c == 1)
    print(f"    singleton components: {singletons:,} "
          f"(cells no shared athlete connects to anything)")
    print("[stage0] -------------------------------")


# degreeReport
# Purpose:   how many DISTINCT athlete-seasons touch each cell. Degree 1 means
#            the cell is pinned by a single athlete-season, so its difficulty
#            and that athlete's ability are perfectly confounded within the
#            component -- connected on paper, worthless in practice.
def degreeReport(group, course, n_cells):
    # Distinct (cell, group) pairs, counted per cell. Sorting a combined key is
    # cheaper than np.unique(axis=0) at this row count.
    key = course.astype(np.int64) * (group.max() + 1) + group
    uniq = np.unique(key)
    cell_of_uniq = (uniq // (group.max() + 1)).astype(np.int64)
    degree = np.bincount(cell_of_uniq, minlength=n_cells)

    print("    cell degree (distinct athlete-seasons per cell):")
    for lo, hi in ((1, 1), (2, 4), (5, 9), (10, 49), (50, 10**9)):
        n = int(((degree >= lo) & (degree <= hi)).sum())
        label = f"{lo}" if lo == hi else (f"{lo}+" if hi > 10**8 else f"{lo}-{hi}")
        print(f"      degree {label:>6}: {n:7,} cells")
    print(f"      median degree: {int(np.median(degree)):,}")
    return degree


# ------------------------------------------------------------------ #
# CHUNK 5 -- entry point
# ------------------------------------------------------------------ #

def main(path):
    cols = loadPack(path)
    keep = usableRows(cols)
    print(f"[stage0] pack {path}: {keep.size:,} rows, {int(keep.sum()):,} usable")

    course = cols["course"][keep]
    group, n_groups = athleteSeasonCodes(cols["athlete"][keep],
                                         cols["year"][keep])
    course_keys = list(cols["course_keys"])
    n_cells = len(course_keys)

    print(f"[stage0] athlete-seasons {n_groups:,}   cells {n_cells:,}")

    graph = buildBipartite(group, course, n_groups, n_cells)
    n_comp, labels = componentLabels(graph)
    cell_labels = labels[n_groups:]

    rows_per_cell = np.bincount(course, minlength=n_cells).astype(np.float64)
    sizes = _componentSizes(cell_labels, rows_per_cell)

    reportComponents(sizes, n_cells, int(keep.sum()), course_keys, cell_labels)
    degreeReport(group, course, n_cells)


if __name__ == "__main__":
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "packed_XC_TF.npz")
    main(sys.argv[1] if len(sys.argv) > 1 else default)