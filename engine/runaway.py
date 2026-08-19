# Project: xc-predictor
# Subset:  Speed Rating Engine -- §6.5, identifying the runaway cell
#
# THE QUESTION THIS ANSWERS
# -------------------------
# The convergence guard already detects that ONE cell is still moving while the
# mean has settled -- it fired on 158 consecutive iterations of the 2026-07-29
# run while the difficulty floor walked -0.288 -> -0.527. What it does not say
# is WHICH cell, and without that the fix is guesswork.
#
# ★ THE DISTINCTION THAT MATTERS: a STABLE argmax and a ROTATING one are
#   different failures.
#     - Same cell, iteration after iteration -> a genuine runaway. One cell,
#       weakly connected, sliding along the unidentified direction of §2.
#     - Argmax hopping between cells -> a limit cycle, like the period-2
#       oscillation DAMPING = 0.5 was chosen to collapse (§3.4). Not a runaway,
#       and a completely different fix.
#   The `streak` counter below is what separates them. Reporting the max value
#   alone -- which is all the loop does today -- cannot tell them apart.
#
# Nothing here mutates the solve. It observes an array and prints.


# ------------------------------------------------------------------ #
# CHUNK 1 -- tracker state
# ------------------------------------------------------------------ #

# newTracker
# Purpose:   the mutable state the observer carries between iterations.
# Output:    dict.
#
# `totals` accumulates each cell's summed absolute movement over the whole
# solve. A cell that moves 0.0013 every iteration for 200 iterations has
# travelled 0.26 -- which is the actual damage -- while never once looking
# dramatic in a per-iteration max.
def newTracker():
    return {"last_idx": -1,      # argmax of the previous iteration
            "streak": 0,         # consecutive iterations with the same argmax
            "totals": None,      # cumulative |movement| per cell
            "history": []}       # (iteration, idx, value) at each report


# ------------------------------------------------------------------ #
# CHUNK 2 -- observation
# ------------------------------------------------------------------ #

# observe
# Purpose:   record one iteration's worst-moving cell.
# Arguments: tracker; diff -- per-cell |difficulty - prev|; it -- iteration index.
# Output:    dict describing this iteration's worst cell.
#
# Syntax: `diff.argmax()` returns the FIRST maximal index, which is stable under
# ties -- important, because an unstable tiebreak would fake a rotating argmax
# and invert the diagnosis above.
def observe(tracker, diff, it):
    if tracker["totals"] is None:
        tracker["totals"] = diff.copy()
    else:
        tracker["totals"] += diff

    idx = int(diff.argmax())
    if idx == tracker["last_idx"]:
        tracker["streak"] += 1
    else:
        tracker["streak"] = 1
        tracker["last_idx"] = idx

    return {"it": it, "idx": idx,
            "value": float(diff[idx]),
            "streak": tracker["streak"]}


# shouldReport
# Purpose:   throttle. The loop's own warning already fires every iteration;
#            this must not double that noise.
# Arguments: obs -- from observe; every -- report cadence once a streak is real.
# Output:    bool.
#
# Reports on the FIRST iteration a cell holds the argmax for `min_streak`
# iterations (the moment the diagnosis becomes available), then every `every`
# iterations after that.
def shouldReport(obs, every=25, min_streak=5):
    if obs["streak"] < min_streak:
        return False
    if obs["streak"] == min_streak:
        return True
    return obs["it"] % every == 0


# ------------------------------------------------------------------ #
# CHUNK 3 -- description
# ------------------------------------------------------------------ #

# _regionOf
# Purpose:   readable region label for a cell, or '-' when unmapped.
# Arguments: idx; region -- per-course code array or None;
#            region_names -- {code: state}.
def _regionOf(idx, region, region_names):
    if region is None or idx >= len(region):
        return "-"
    code = int(region[idx])
    if code < 0:
        return "UNMAPPED"
    return (region_names or {}).get(code, f"r{code}")


# _keyOf
# Purpose:   the engine's own key for a cell, truncated for log width.
def _keyOf(idx, course_keys, width=44):
    if course_keys is None or idx >= len(course_keys):
        return f"<code {idx}>"
    key = course_keys[idx] or f"<code {idx}>"
    return key if len(key) <= width else key[:width - 1] + "\u2026"


# describe
# Purpose:   one log line for the current worst-moving cell.
# Arguments: obs; course_keys; region; region_names; counts -- rows per course.
# Output:    str.
def describe(obs, course_keys, region, region_names, counts=None):
    idx = obs["idx"]
    n = int(counts[idx]) if counts is not None and idx < len(counts) else -1
    return (f"    \u2605 runaway: {_keyOf(idx, course_keys)}"
            f"  region={_regionOf(idx, region, region_names)}"
            f"  rows={n:,}"
            f"  moved={obs['value']:.6f}/iter"
            f"  for {obs['streak']} iters")


# ------------------------------------------------------------------ #
# CHUNK 4 -- end-of-solve summary
# ------------------------------------------------------------------ #

# _topIndices
# Purpose:   indices of the N largest entries, descending.
# Syntax:    argsort is ascending, so the tail is taken and reversed. argpartition
#            would be faster but N is tiny and this stays readable.
def _topIndices(values, n):
    order = values.argsort()[-n:]
    return [int(i) for i in reversed(order)]


# summary
# Purpose:   ★ THE PAYOFF. Total distance travelled per cell over the whole
#            solve, which is the quantity that actually moved the difficulty
#            floor from -0.288 to -0.527.
# Arguments: tracker; course_keys; region; region_names; counts; top.
# Output:    None; prints.
def summary(tracker, course_keys, region, region_names, counts=None, top=8):
    if tracker["totals"] is None:
        return

    totals = tracker["totals"]
    print("\n[engine] ---- cumulative movement, worst cells ----")
    for idx in _topIndices(totals, top):
        n = int(counts[idx]) if counts is not None and idx < len(counts) else -1
        print(f"    {_keyOf(idx, course_keys)}"
              f"  region={_regionOf(idx, region, region_names)}"
              f"  rows={n:,}"
              f"  total|move|={float(totals[idx]):.4f}")

    # A single dominant traveller is the runaway signature. A flat top-8 is a
    # cycle or ordinary settling, and points at DAMPING rather than at a cell.
    ordered = totals[totals.argsort()][::-1]
    if len(ordered) > 1 and ordered[1] > 0:
        print(f"    leader/runner-up ratio: {float(ordered[0] / ordered[1]):.2f}"
              f"   (>2 suggests one cell, ~1 suggests a cycle)")
    print("[engine] --------------------------------------------")