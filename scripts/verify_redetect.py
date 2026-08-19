# Project: xc-predictor
# File:    scripts/verify_redetect.py
# Purpose: READ-ONLY verifier for a post-correction re-detect. Walks the
#          master doc's SS5 decision tree against FILES ALONE (no DB, no
#          console history), so a lost terminal can never again cost a
#          verification. Run it after: apply_triage -> pipeline -> diag.
#
#          The four checks, in decision-tree order:
#            1. TRUNCATION   written rows == --row-limit means a cut worksheet;
#                            nothing downstream of a truncated file is valid.
#            2. DIV REGRESSIONS   overridden (meet,div) still flagging. Few is
#                            per-division work (wrong snap / gender costume);
#                            many is systemic -- corrections not reaching the
#                            re-rating -- and everything STOPS.
#            3. ROW REGRESSIONS   dropped result_ids reappearing as FRESH
#                            flags (already='-'): the drop path from
#                            corrections.py into the loader is broken.
#            4. NOISE FLOOR  p99|c| vs the previous run. Junk removal should
#                            SHRINK it; a shrunken floor lowers the auto
#                            threshold and can flag MORE marginal divisions --
#                            that is sensitivity improving, not regression.
#
#          Plus the tripwire this incident earned: if a division's key is in
#          corrections.py but the worksheet says already='-', the diag run
#          LOADED A DIFFERENT corrections.py than this script found -- the
#          multiple-SSOT-copies landmine, caught before it costs a night.
#
# EXIT CODES (so an overnight queue can gate on it):
#   0 = clean          1 = warnings (bounded regressions to work through)
#   2 = STOP           (truncation / systemic regressions / wrong SSOT copy)
#
# USAGE
#   python scripts/verify_redetect.py
#   python scripts/verify_redetect.py --dir scripts --corrections engine/corrections.py
#   python scripts/verify_redetect.py --limit-xc 3000000 --limit-tf 1000000
# ============================================================================

import argparse
import importlib.util
import os
import re
import sys


# ================================================================== #
# CHUNK 1 -- CONSTANTS
# ================================================================== #

# Same candidate list diag_suspects / apply_triage walk; first hit wins.
# Keeping the THREE scripts on one list is what makes the tripwire in
# check 2 meaningful: if they ever diverge, annotations stop matching.
_CANDIDATES = ("engine/corrections.py", "scripts/corrections.py",
               "corrections.py", "backfill/corrections.py")

_SPORTS = ("XC", "TF")

# Row-limit defaults mirror the tightened-queue commands (SS3 of the
# suspects-triage doc). Overridable per sport from the CLI.
_DEFAULT_LIMITS = {"XC": 3_000_000, "TF": 1_000_000}

# SS5.2's line between "inspect each" and "systemic -- STOP".
_MAX_DIV_REGRESSIONS = 20

# Previous run's census (2026-07-12, pre-correction detect at 6%/25%),
# printed alongside the new numbers so the p99 movement is read against
# a recorded baseline, not memory. Update after each verified pass.
_BASELINE = {
    "XC": {"median": 2.17, "p99": 20.51, "thr": 22.78,
           "div": 3603, "rows": 2_132_386},
    "TF": {"median": 1.19, "p99": 7.55, "thr": 8.33,
           "div": 3030, "rows": 762_681},
}


# ================================================================== #
# CHUNK 2 -- LOAD THE SSOT (corrections.py, by path, like diag does)
# ================================================================== #

def _findCorrections(path):
    """Explicit --corrections wins; otherwise first existing candidate."""
    if path:
        if not os.path.exists(path):
            sys.exit(f"--corrections {path}: not found")
        return path
    for c in _CANDIDATES:
        if os.path.exists(c):
            return c
    sys.exit("corrections.py not found; pass --corrections PATH")


def _loadCorrections(path):
    """Import by FILE PATH (no package assumptions) and return, PER SPORT,
    the two tables the checks key on: override (meet,div) keys and dropped
    ids. Per-sport because the containers are (2026-07-13): checking an XC
    worksheet against TF's corrections -- or against a union -- would
    manufacture false regressions and mask real ones."""
    spec = importlib.util.spec_from_file_location("_corr_verify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {s: {"dist_ov": set(mod._DISTANCE_OVERRIDES_BY_SPORT[s]),
                "res_drop": set(mod._RESULT_DROP_BY_SPORT[s])}
            for s in _SPORTS}


# ================================================================== #
# CHUNK 3 -- WORKSHEET READERS (mirror diag's writers -- KEEP IN SYNC)
# ================================================================== #

# _intOrNone : worksheet key columns carry either an integer or the string
#   "None" (diag writes Python None through str()); same parse triage uses.
def _intOrNone(s):
    return None if s == "None" else int(s)


def _iterDivRows(path):
    """Yield one dict per DIV line of suspects_div_<sport>.tsv. Positional
    parse against _writeDivWorksheet's column order; '#' lines skipped."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            yield {"meet": _intOrNone(p[2]), "div": _intOrNone(p[3]),
                   "c_pct": p[5], "already": p[10], "proposed": p[11]}


def _iterRowRows(path):
    """Yield (result_id, already, proposed, meet, div) per ROW line of
    suspects_row_<sport>.tsv, STREAMING -- the XC file can be 2M+ lines,
    so nothing is held; each line is looked at once and released."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            yield int(p[4]), p[9], p[10], p[2], p[3]


# ================================================================== #
# CHUNK 4 -- THE FOUR CHECKS (one helper each; SS5 order)
# ================================================================== #

def _checkTruncation(row_path, limit):
    """SS5.1. Count data lines; == limit means the worksheet was cut and
    MUST NOT be triaged. Streaming count -- no file held in memory."""
    n = 0
    with open(row_path, encoding="utf-8") as f:
        for line in f:
            n += not line.startswith("#")
    return n, n >= limit


def _checkDivRegressions(div_path, dist_ov):
    """SS5.2 + the SSOT tripwire. Three tallies over the div worksheet:
       regressed  -- flagged AND overridden AND worksheet agrees (already set)
       mismatched -- flagged AND overridden but worksheet says already='-':
                     diag loaded a DIFFERENT corrections.py. STOP-class.
       (cleared is derived by the caller: overridden keys absent here.)"""
    regressed, mismatched, flagged_keys = [], [], set()
    for d in _iterDivRows(div_path):
        key = (d["meet"], d["div"])
        flagged_keys.add(key)
        if key not in dist_ov:
            continue
        if d["already"] == "-":
            mismatched.append((key, d["c_pct"]))
        else:
            regressed.append((key, d["c_pct"]))
    cleared = len(dist_ov - flagged_keys)
    return regressed, mismatched, cleared


def _checkRowRegressions(row_path, res_drop):
    """SS5.3. Dropped ids must vanish (loader filters pre-rating) or come
    back REGRESSION-tagged. An id back with already='-' means the drop
    path is broken. Returns (fresh reappearances, tagged reappearances)."""
    fresh, tagged = [], 0
    for rid, already, proposed, meet, div in _iterRowRows(row_path):
        if rid not in res_drop:
            continue
        if already == "-":
            fresh.append((rid, meet, div))
        else:
            tagged += 1
    return fresh, tagged


def _readCensus(txt_path):
    """SS5.4 input. Parse the noise-floor block out of suspects_<sport>.txt
    (diag's _writeHuman format -- KEEP IN SYNC)."""
    text = open(txt_path, encoding="utf-8").read()
    grab = lambda pat: float(re.search(pat, text).group(1))
    return {"median": grab(r"median\|c\|=([\d.]+)%"),
            "p99": grab(r"p99\|c\|=([\d.]+)%"),
            "thr": grab(r"c-threshold in use\s*:\s*([\d.]+)%"),
            "div": int(re.search(r"flagged divisions\s*:\s*(\d+)", text).group(1)),
            "rows": int(re.search(r"flagged rows\s*:\s*(\d+)", text).group(1))}


# ================================================================== #
# CHUNK 5 -- PER-SPORT REPORT (prints the ledger, returns worst verdict)
# ================================================================== #

def _delta(new, old, invert=False):
    """Human movement marker. invert=True when DOWN is the good direction."""
    d = new - old
    arrow = "down" if d < 0 else ("up" if d > 0 else "flat")
    good = (d < 0) if invert else (d > 0)
    tone = "good" if good and d != 0 else ("check" if d != 0 else "ok")
    return f"{old} -> {new}  ({arrow}, {tone})"


def _verifySport(sport, in_dir, corr, limit):
    row_path = os.path.join(in_dir, f"suspects_row_{sport.lower()}.tsv")
    div_path = os.path.join(in_dir, f"suspects_div_{sport.lower()}.tsv")
    txt_path = os.path.join(in_dir, f"suspects_{sport.lower()}.txt")
    for p in (row_path, div_path, txt_path):
        if not os.path.exists(p):
            print(f"  [STOP] missing {p} -- did the {sport} detect run?")
            return 2

    verdict = 0
    print(f"\n=== {sport} ===")

    # -- SS5.1 truncation ------------------------------------------------
    n_rows, truncated = _checkTruncation(row_path, limit)
    if truncated:
        print(f"  [STOP] row worksheet TRUNCATED at {n_rows:,} == limit; "
              f"raise --row-limit and rerun {sport} before triaging")
        verdict = 2
    else:
        print(f"  [ok] rows written {n_rows:,} < limit {limit:,} "
              f"(no truncation; written == true crosser total)")

    # -- SS5.2 division regressions + SSOT tripwire ----------------------
    regressed, mismatched, cleared = _checkDivRegressions(
        div_path, corr["dist_ov"])
    print(f"  [ok] overridden divisions no longer flagging: {cleared:,}")
    if mismatched:
        print(f"  [STOP] {len(mismatched)} overridden divisions flagged with "
              f"already='-': diag loaded a DIFFERENT corrections.py than "
              f"this script found. Resolve the copy before anything else.")
        for key, c in mismatched[:10]:
            print(f"       meet/div {key[0]}/{key[1]}  c={c}%")
        verdict = 2
    if regressed:
        sysmic = len(regressed) > _MAX_DIV_REGRESSIONS
        tag = "STOP" if sysmic else "warn"
        print(f"  [{tag}] {len(regressed)} overridden divisions STILL flag "
              f"({'systemic -- corrections may not reach re-rating; STOP'
                 if sysmic else 'bounded -- inspect each with --dump'}):")
        for key, c in regressed[:10]:
            print(f"       meet/div {key[0]}/{key[1]}  c={c}%")
        verdict = max(verdict, 2 if sysmic else 1)

    # -- SS5.3 row regressions -------------------------------------------
    fresh, tagged = _checkRowRegressions(row_path, corr["res_drop"])
    print(f"  [ok] dropped ids back REGRESSION-tagged: {tagged} (allowed)")
    if fresh:
        print(f"  [STOP] {len(fresh)} DROPPED result_ids reappear as FRESH "
              f"flags -- the corrections->loader drop path is broken:")
        for rid, meet, div in fresh[:10]:
            print(f"       result_id {rid}  meet/div {meet}/{div}")
        verdict = 2
    else:
        print(f"  [ok] no dropped ids reappear as fresh flags")

    # -- SS5.4 noise floor vs recorded baseline ---------------------------
    c, b = _readCensus(txt_path), _BASELINE[sport]
    print(f"  census vs baseline ({sport}):")
    print(f"    p99|c|%     {_delta(c['p99'], b['p99'], invert=True)}")
    print(f"    threshold%  {_delta(c['thr'], b['thr'], invert=True)}")
    print(f"    flagged div {c['div']:,} (was {b['div']:,}) -- judge by the "
          f"worst-c list, not this count: a lower floor flags MORE marginals")
    print(f"    flagged rows {c['rows']:,} (was {b['rows']:,})")
    return verdict


# ================================================================== #
# CHUNK 6 -- CLI
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Read-only SS5 decision-tree verifier for a post-"
                    "correction re-detect (files only, no DB).")
    ap.add_argument("--dir", default="scripts",
                    help="dir holding suspects_* outputs (default scripts)")
    ap.add_argument("--corrections", default=None,
                    help="path to corrections.py (default: auto-detect)")
    ap.add_argument("--limit-xc", type=int, default=_DEFAULT_LIMITS["XC"])
    ap.add_argument("--limit-tf", type=int, default=_DEFAULT_LIMITS["TF"])
    args = ap.parse_args()

    corr_path = _findCorrections(args.corrections)
    corr = _loadCorrections(corr_path)
    print(f"corrections.py: {corr_path}")
    for s in _SPORTS:
        print(f"  [{s}] _DISTANCE_OVERRIDES {len(corr[s]['dist_ov']):,}   "
              f"_RESULT_DROP {len(corr[s]['res_drop']):,}")

    limits = {"XC": args.limit_xc, "TF": args.limit_tf}
    worst = max(_verifySport(s, args.dir, corr[s], limits[s]) for s in _SPORTS)

    label = {0: "CLEAN -- proceed to triage on the new worksheets",
             1: "WARNINGS -- bounded regressions; work them, then proceed",
             2: "STOP -- fix the flagged failure before any more merging"}
    print(f"\nverdict: {label[worst]}")
    sys.exit(worst)


if __name__ == "__main__":
    main()