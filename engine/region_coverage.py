# Project: xc-predictor
# Subset:  Speed Rating Engine -- §6.1 region-coverage diagnostic
#
# WHY THIS EXISTS
# ---------------
# The region prior (_applyRegionPrior) shrinks each REGION's mean difficulty
# toward zero. The gauge anchor then re-centres over EVERY solved cell. When
# only some cells carry a region, those two operations are running on different
# populations: the prior removes level from the mapped group, and the anchor
# hands it straight back to everyone -- including the unmapped cells the prior
# never touched. That runs once per iteration, 276 times.
#
# So the question "which cells are mapped, and which states are missing" is not
# housekeeping. It is the measurement that decides whether the West/Midwest
# split in §1 is a coverage artefact or something real.
#
# Nothing here mutates state. It reads `codes` and `seen` and prints.


# ------------------------------------------------------------------ #
# CHUNK 1 -- key classification
# ------------------------------------------------------------------ #

# sportOf
# Purpose:   which sport a course key belongs to, for a per-sport coverage split.
# Arguments: key -- one entry of cols["course_keys"].
# Output:    "XC", "TF", or "?" for anything unrecognised.
#
# ★ The split is the whole point of this helper. XC and TF resolve their state
#   through DIFFERENT lookups (course_name vs location_id), so a single overall
#   percentage can hide one sport being at 0% behind the other being at 80%.
def sportOf(key):
    if not key:
        return "?"
    if key.startswith("XC:"):
        return "XC"
    if key.startswith("TF:"):
        return "TF"
    return "?"


# ------------------------------------------------------------------ #
# CHUNK 2 -- counting
# ------------------------------------------------------------------ #

# stateCellCounts
# Purpose:   state -> number of cells mapped to that state.
# Arguments: codes -- int array, one per cell, region code or -1;
#            seen  -- dict state -> region code, built by loadCourseRegions.
# Output:    dict state -> int.
#
# Syntax: (codes == code) is a boolean array over all cells; .sum() counts the
# True entries. int() because numpy integers print with a dtype suffix.
def stateCellCounts(codes, seen):
    return {st: int((codes == code).sum()) for st, code in seen.items()}


# coverageBySport
# Purpose:   mapped vs total cells, split by sport.
# Arguments: course_keys -- list[str]; codes -- the region code array.
# Output:    dict sport -> (mapped, total).
#
# ★ THE DECISIVE NUMBER. If XC comes back near 0/N while TF is healthy, the
#   region prior has never applied to a single XC cell -- and every terrain
#   anchor in TERRAIN_ANCHORS is XC.
def coverageBySport(course_keys, codes):
    out = {}
    for key, code in zip(course_keys, codes):
        sport = sportOf(key)
        mapped, total = out.get(sport, (0, 0))
        out[sport] = (mapped + (1 if code >= 0 else 0), total + 1)
    return out


# ------------------------------------------------------------------ #
# CHUNK 3 -- optional reference comparison
# ------------------------------------------------------------------ #

# _readCsv
# Purpose:   header + rows, or None on any I/O failure.
# ★ Returns None rather than raising. A diagnostic that can crash a 6.5-minute
#   solve is worse than no diagnostic.
def _readCsv(path):
    try:
        import csv
        with open(path, newline="") as fh:
            rows = list(csv.reader(fh))
    except OSError:
        return None
    if len(rows) < 2:
        return None
    return [c.strip().lower() for c in rows[0]], rows[1:]


# _countOneCellPerRow
# Purpose:   reference_fit.py's native output is one ROW PER CELL, carrying a
#            `state` column. Counting rows per state therefore counts cells.
# Arguments: rows; i_state -- index of the state column.
def _countOneCellPerRow(rows, i_state):
    counts = {}
    for row in rows:
        if len(row) <= i_state:
            continue
        st = row[i_state].strip().upper()
        if st and st != "NONE":
            counts[st] = counts.get(st, 0) + 1
    return counts


# _countPreAggregated
# Purpose:   the simpler shape -- a two-column (state, cells) summary.
def _countPreAggregated(rows, i_state, i_cells):
    counts = {}
    for row in rows:
        if len(row) <= max(i_state, i_cells):
            continue
        try:
            counts[row[i_state].strip().upper()] = int(float(row[i_cells]))
        except ValueError:
            continue                          # non-numeric row: ignore, never raise
    return counts


# loadReferenceCounts
# Purpose:   per-state cell counts from reference_fit.py, so the report can show
#            a mapped/reference RATIO rather than a bare count.
# Arguments: path -- either reference_fit.py's own `--out` CSV (one row per
#            cell, with cellk/d_ref/n/state) or a pre-aggregated (state, cells).
# Output:    dict state -> int, or None.
#
# ★ Both shapes are accepted so nothing in reference_fit.py has to change --
#   point REGION_REF_COUNTS_CSV straight at exports/reference_fit.csv.
def loadReferenceCounts(path):
    if not path:
        return None
    parsed = _readCsv(path)
    if not parsed:
        return None
    header, rows = parsed

    if "state" not in header:
        return None
    i_state = header.index("state")

    for name in ("cells", "n_cells"):
        if name in header:
            return _countPreAggregated(rows, i_state, header.index(name)) or None

    return _countOneCellPerRow(rows, i_state) or None


# ------------------------------------------------------------------ #
# CHUNK 4 -- the report
# ------------------------------------------------------------------ #

# _sortKey
# Purpose:   order states worst-coverage-first.
# Detail:    with a reference we sort by the RATIO (the states escaping the
#            prior float to the top); without one, by raw mapped count.
def _sortKey(counts, ref_counts):
    if ref_counts:
        return lambda st: counts.get(st, 0) / max(ref_counts.get(st, 0), 1)
    return lambda st: counts.get(st, 0)


# _printSportSplit
# Purpose:   the per-sport coverage block. Kept separate so the caller can use
#            it alone when the per-state tail is too noisy to read.
def _printSportSplit(by_sport):
    print("    coverage by sport:")
    for sport in sorted(by_sport):
        mapped, total = by_sport[sport]
        pct = 100.0 * mapped / max(total, 1)
        flag = "   <-- ★ SPORT IS UNMAPPED" if pct < 5.0 else ""
        print(f"      {sport:>3}: {mapped:7,}/{total:7,}  ({pct:5.1f}%){flag}")


# _printStateTable
# Purpose:   per-state mapped counts, worst first, with the reference ratio
#            when one is available.
# Arguments: limit -- print only the worst N states; None prints all.
def _printStateTable(counts, ref_counts, limit=25):
    states = sorted(counts, key=_sortKey(counts, ref_counts))
    if limit:
        states = states[:limit]

    header = "    worst-mapped states" + (" (mapped / reference)" if ref_counts else "")
    print(header + ":")
    for st in states:
        n = counts[st]
        if ref_counts and st in ref_counts:
            ref = ref_counts[st]
            print(f"      {st:>4}: {n:6,} / {ref:6,}  = {n / max(ref, 1):.2f}")
        else:
            print(f"      {st:>4}: {n:6,}")


# report
# Purpose:   the whole §6.1 diagnostic, printed. Call from loadCourseRegions
#            immediately before it returns, where `codes` and `seen` both exist.
# Arguments: course_keys, codes, seen  -- as inside loadCourseRegions;
#            ref_path -- optional reference_fit.py CSV;
#            limit    -- states to print.
# Output:    dict of the computed pieces, for a caller that wants to assert on
#            them. Printing is the primary product.
def report(course_keys, codes, seen, ref_path=None, limit=25):
    n_total = len(codes)
    n_mapped = int((codes >= 0).sum())
    counts = stateCellCounts(codes, seen)
    by_sport = coverageBySport(course_keys, codes)
    ref_counts = loadReferenceCounts(ref_path)

    print("[engine] ---- §6.1 region coverage ----")
    print(f"    overall: {n_mapped:,}/{n_total:,} cells mapped "
          f"({100.0 * n_mapped / max(n_total, 1):.1f}%), {len(seen)} regions")
    _printSportSplit(by_sport)
    _printStateTable(counts, ref_counts, limit=limit)

    # 219 regions is not 50 states. A long tail of junk labels each holds a
    # handful of cells, has near-zero cross-links, and gets flattened outright.
    tiny = sum(1 for n in counts.values() if n < 10)
    if tiny:
        print(f"    ⚠ {tiny} regions hold fewer than 10 cells "
              f"(junk state labels -- see §6.1 whitelist note)")
    print("[engine] -------------------------------")

    return {"n_total": n_total, "n_mapped": n_mapped,
            "by_sport": by_sport, "state_counts": counts,
            "ref_counts": ref_counts}