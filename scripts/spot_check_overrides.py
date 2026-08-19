# Project: xc-predictor
# File:    scripts/spot_check_overrides.py
# Purpose: READ-ONLY pre-merge audit of the auto distance overrides. Samples
#          divisions from distance_override_<sport>.py (5 at random + the 5
#          largest |c| -- random measures the typical case, worst-c measures
#          the tail), pulls each division's raw finish times, and prints the
#          WINNER / MEDIAN / LAST pace the PROPOSED distance implies.
#
#          The test is the 9308 lesson, made routine: a distance is judged by
#          whether it makes the times PHYSICALLY SANE at both ends of the
#          field -- the winner must not beat world class, the last finisher
#          must not be slower than a walk. The human reads ten lines and
#          verdicts each PASS/FAIL; the script never decides.
#
# USAGE
#   python scripts/spot_check_overrides.py                  # XC + TF, 5+5 each
#   python scripts/spot_check_overrides.py --sport XC -n 8  # 8 random + 8 worst
#   python scripts/spot_check_overrides.py --seed 7         # reproducible draw
# ============================================================================

import argparse
import os
import random
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- CONSTANTS
# ================================================================== #
_TABLE = {"XC": "results", "TF": "results_tf"}
_MILE = 1609.344

# ---------------------------------------------------------------------------
# PARSING THE OVERRIDE FILE
#
# 2026-07-17 REWRITE. The old regex demanded the WHOLE line in one shot:
#
#   \((\d+),\s*(\d+)\):\s*(\d+),\s*#\s*c=([+-][\d.]+)%\s*implied=(\d+)\s*
#   snap_err=([+-][\d.]+)%\s*fast_rows=(\d+)
#
# Two bugs, one disease:
#   1. It required triage_suspects' comment format. adjudicate_regressions
#      writes prose ("# REFUTED: field (UNIFORM_NEG, iqr=3.6 < 10.4) moved as
#      ONE factor against stored 5000"). Zero matches. The header said
#      KEEP IN SYNC and nobody did -- it read 0 of 0 against a 2,406-key file
#      and printed its normal PASS/FAIL banner underneath. AN ALARM THAT
#      DOESN'T GATE IS DECORATION.
#   2. `(\d+)` cannot parse a float target. `(20553, 12): 3218.7,` matched
#      nothing, so every yard-derived distance vanished silently.
#
# THE FIX: match the DATA -- the key and the value are the contract, and they
# are the only things every writer must emit. The evidence in the comment is
# GARNISH: pulled if present, absent otherwise, and never load-bearing. A
# writer changing its prose can no longer blind the audit.
# ---------------------------------------------------------------------------

# the contract: `(meet, div): distance,`  -- value may be int or float
_LINE = re.compile(r"\((\d+),\s*(\d+)\):\s*([\d.]+)\s*,")

# optional evidence, each independent -- a missing one costs its own field only
_EV = {
    "c":         re.compile(r"c=([+-][\d.]+)%"),
    "implied":   re.compile(r"implied=([\d.]+)"),
    "fast_rows": re.compile(r"fast_rows=(\d+)"),
    "stored":    re.compile(r"against stored ([\d.]+)"),   # adjudicate's prose
}

# anet non-finisher sentinel; tfrrs uses NULL (per-source rule from the
# dedup doc). Both are excluded by the same predicate below.
_SENTINEL = 999999


# ================================================================== #
# CHUNK 2 -- LOAD + SAMPLE THE OVERRIDE FILE
# ================================================================== #

def _evidence(line):
    """
    Purpose : scrape whatever evidence the comment happens to carry.
    Arguments: line -- str, the raw file line.
    Output   : dict of the fields that were present. MISSING IS FINE -- an
               absent field is None, never 0.0. NULL IS BETTER THAN STALE:
               c=0.0 would read as "this division is perfectly fine", which is
               the opposite of "we don't know".
    """
    out = {}
    for name, rx in _EV.items():
        m = rx.search(line)
        out[name] = float(m.group(1)) if m else None
    return out


def _loadOverrides(path):
    """
    Purpose : the generated overrides file -> [{meet, div, target, ...}]
    Output  : list of dict. Parsed BY TEXT, not by import -- the evidence lives
              in comments and importlib would throw it away.
    Note    : the key/value match GATES; the evidence match does not. A line
              with no comment at all still audits, it just ranks as unknown.
    """
    out = []
    for line in open(path, encoding="utf-8"):
        m = _LINE.search(line)
        if not m:
            continue
        e = {"meet": int(m.group(1)), "div": int(m.group(2)),
             "target": float(m.group(3))}
        e.update(_evidence(line))
        out.append(e)
    return out


def _magnitude(e):
    """
    Purpose : how big a claim is this override making? Used only for ranking.
    Output  : float, a percent-ish scale. Falls back through what is available:
                |c|                          -- triage's measured field shift
                |target/stored - 1| * 100    -- adjudicate's prose
                0.0                          -- unknown; ranks last, correctly:
                                                an unquantified claim is not a
                                                worst case, it is a no-case.
    ★ Ranking only. Never gates. A stratum boundary that decided admission
      would make this constant load-bearing.
    """
    if e.get("c") is not None:
        return abs(e["c"])
    if e.get("stored"):
        return abs(e["target"] / e["stored"] - 1.0) * 100.0
    return 0.0


def _sample(entries, n):
    """n largest magnitude (the tail, where a wrong inversion hurts most) + n
    at random (the typical case). Identity-deduped by key, not by dict equality
    -- two divisions can carry identical values and `e not in worst` would drop
    a legitimate random pick."""
    worst = sorted(entries, key=lambda e: -_magnitude(e))[:n]
    seen = {(e["meet"], e["div"]) for e in worst}
    pool = [e for e in entries if (e["meet"], e["div"]) not in seen]
    rand = random.sample(pool, min(n, len(pool)))
    return worst + rand


# ================================================================== #
# CHUNK 3 -- PULL THE DIVISION'S TIMES
# ================================================================== #

def _divisionTimes(cur, table, meet, div):
    """(n, min, median, max) of real finish times for one division.
    time_seconds > 0 and < sentinel excludes DNFs on both sources."""
    cur.execute(f"""
        SELECT count(*),
               min(time_seconds),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY time_seconds),
               max(time_seconds)
        FROM {table}
        WHERE meet_id = %s AND div_id = %s
          AND time_seconds > 0 AND time_seconds < {_SENTINEL}
    """, (meet, div))
    return cur.fetchone()


# ================================================================== #
# CHUNK 4 -- FORMATTING (seconds -> m:ss, pace under a distance)
# ================================================================== #

def _mmss(sec):
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def _pace(sec, metres):
    """min/mile the time implies IF the division really is `metres` long --
    the number the human judges against the sanity bands."""
    return _mmss(sec / metres * _MILE)


# ================================================================== #
# CHUNK 5 -- THE REPORT
# ================================================================== #

def _reportSport(sport, in_dir, n, cur):
    path = os.path.join(in_dir, f"distance_override_{sport.lower()}.py")
    if not os.path.exists(path):
        print(f"\n[skip] {path} not found")
        return
    entries = _loadOverrides(path)
    if not entries:
        # ★ THE ALARM. Reading 0 keys from a file that EXISTS is a parser
        #   failure, not a clean bill of health. The old version printed its
        #   PASS/FAIL banner over an empty read for an entire session.
        print(f"\n!!! {path} exists but 0 overrides parsed -- the audit is "
              f"BLIND. Check _LINE against a real line before trusting "
              f"anything downstream.")
        return

    picks = _sample(entries, n)
    print(f"\n=== {sport}: {len(picks)} of {len(entries):,} auto overrides "
          f"(first {min(n, len(picks))} = largest claim, rest = random) ===")
    print(f"{'meet/div':>16} {'was':>8} {'->':>2} {'prop':>8} {'c%':>7} "
          f"{'n':>4} {'win@prop':>9} {'med@prop':>9} {'last@prop':>9}  verdict?")

    for e in picks:
        n_fin, tmin, tmed, tmax = _divisionTimes(
            cur, _TABLE[sport], e["meet"], e["div"])
        if not n_fin:
            print(f"{e['meet']}/{e['div']:>7}  -- no usable times --")
            continue

        # every evidence field prints "?" when absent -- the report must never
        # imply it knows something it does not (a VERIFIER REPORTS ITS LEDGER
        # IN EVERY BRANCH)
        key = f"{e['meet']}/{e['div']}"
        was = f"{e['stored']:.0f}" if e.get("stored") else "?"
        cpc = f"{e['c']:+.1f}" if e.get("c") is not None else "?"

        print(f"{key:>16} {was:>8} {'->':>2} {e['target']:>8.0f} {cpc:>7} "
              f"{n_fin:>4} "
              f"{_pace(tmin, e['target']):>9} {_pace(tmed, e['target']):>9} "
              f"{_pace(tmax, e['target']):>9}  ____")


def main():
    ap = argparse.ArgumentParser(
        description="Pre-merge spot check of auto distance overrides: "
                    "sampled divisions' paces under the proposed distance "
                    "(read-only).")
    ap.add_argument("--sport", choices=["XC", "TF", "BOTH"], default="BOTH")
    ap.add_argument("-n", type=int, default=5,
                    help="sample size PER STRATUM (n random + n worst-|c|)")
    ap.add_argument("--in", dest="in_dir", default="scripts")
    ap.add_argument("--seed", type=int, default=None,
                    help="seed the random draw for a reproducible sample")
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    sports = ("XC", "TF") if args.sport == "BOTH" else (args.sport,)
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        for s in sports:
            _reportSport(s, args.in_dir, args.n, cur)
        conn.rollback()
    print("\njudge each line: winner pace faster than world class, or last")
    print("pace slower than a walk, under the PROPOSED distance -> FAIL;")
    print("both ends plausible -> PASS. Check the meet page for borderlines.")


if __name__ == "__main__":
    main()