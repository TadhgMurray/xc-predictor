# Project: xc-predictor / scripts
# File:    wipe_overrides.py
# Purpose: Clear the overrides the three-pass rebuild regenerates, WITHOUT
#          touching 1.45M lines of history.
#
#     python scripts/wipe_overrides.py            # report
#     python scripts/wipe_overrides.py --write
#     python scripts/wipe_overrides.py --undo
#
# ★ IT APPENDS FIVE LINES INSTEAD OF DELETING A MILLION. Every block after the
#   base dicts is `_X.update({...})`, so the last word wins -- a trailing
#   .clear() is exactly equivalent to deleting every entry above it, and is
#   undone by deleting the block rather than restoring a backup. The file is
#   in git to be the record of decisions nobody can recompute; a wipe that
#   destroys the record to clear the working set trades the wrong thing.
#
# ⚠ THE DROPS SURVIVE, DELIBERATELY. _DISTANCE_DROP and _RESULT_DROP remove
#   junk that NONE of the three passes regenerate:
#
#       pass 1 -> _DISTANCE_OVERRIDES     (regenerated)
#       pass 2 -> _RESULT_OVERRIDE        (regenerated)
#       pass 3 -> _RESULT_DROP            (ADDS to it, never rebuilds it)
#
#   Clearing the drops would delete filtering with nothing to replace it, and
#   pass 3 only finds rows that are still rated -- a row dropped for being
#   unrateable is invisible to it. Pass --drops to clear them anyway.
#
# ! _GENDER_OVERRIDES GOES, because pass 2 rewrites gender per row. Two rows
#   in corrections.py today; both are division-level sex fixes, which is
#   precisely pass 2's job.
#
# ⚠ AND NOT EVERY DISTANCE OVERRIDE IS A DECISION THE PASSES CAN RE-DERIVE.
#   A row in dist_override is one of two things:
#
#     a CORRECTION    `meets` or the tfrrs blob already has a distance and the
#                     override disagrees. Clearing it restores the scraped
#                     value; the division stays rated, and pass 1 gets another
#                     look with the same evidence that justified the override.
#
#     a SOLE SOURCE   nothing else has a distance at all. Clearing it does not
#                     restore anything -- backfill_normalize writes no
#                     normalized_time, the engine rates nothing there, and an
#                     unrated division is INVISIBLE to all three passes. That
#                     is the blind spot that hid 26359/0 from every earlier
#                     tool: meet 26359 has 568 tfrrs rows and no `meets` row.
#
#   --keep-sole-source re-states those entries below the .clear() so a reset
#   clears the judgements and keeps the data. Run
#   scripts/census_override_sources.py first; it counts both kinds and the
#   rows behind them.

import argparse
import io

PATH = "engine/corrections.py"
MARK = "# === WIPE FOR REBUILD (scripts/wipe_overrides.py) ==="

_REGENERATED = ["_DISTANCE_OVERRIDES_XC", "_DISTANCE_OVERRIDES_TF",
                "_RESULT_OVERRIDE_XC", "_RESULT_OVERRIDE_TF",
                "_GENDER_OVERRIDES_XC", "_GENDER_OVERRIDES_TF"]
_DROPS = ["_DISTANCE_DROP_XC", "_DISTANCE_DROP_TF",
          "_RESULT_DROP_XC", "_RESULT_DROP_TF"]


def soleSource(sport="XC"):
    """{(meet_id, div_id): distance} for overrides that are the ONLY distance.

    Reads the LIVE dist_override, so it describes what dump_overrides last
    wrote -- which is what the backfill actually read. If corrections.py has
    moved since, re-run engine/dump_overrides.py before trusting this.
    """
    import sys as _sys
    _sys.path.insert(0, "racecast")
    from database import getConn
    from census_override_sources import _XC_TFRRS_DIST_SQL, _CENSUS, classify
    from census_override_sources import _TABLE

    keep = {}
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(_XC_TFRRS_DIST_SQL)
            cur.execute(_CENSUS.format(table=_TABLE[sport]))
            for meet, div, ovr, anet, tfrrs, _dict, _n, _rated in cur:
                kind, _base = classify(float(ovr), anet and float(anet),
                                       tfrrs and float(tfrrs))
                if kind == "sole source":
                    keep[(int(meet), int(div))] = float(ovr)
    return keep


def sizes(path):
    """{dict name: entries} as the file currently resolves."""
    import importlib.util
    import sys
    sys.path.insert(0, "engine")
    spec = importlib.util.spec_from_file_location("corrections_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {n: len(getattr(mod, n, ()) or ())
            for n in _REGENERATED + _DROPS}


def restate(keep, target="_DISTANCE_OVERRIDES_XC"):
    """The sole sources, written back BELOW the .clear() so they survive it.

    ! .update() ORDER IS THE WHOLE MECHANISM, same as the wipe itself. The
      clear runs, then this puts back the entries that were never a judgement
      to begin with.
    """
    if not keep:
        return ""
    lines = [
        "",
        "# --- kept through the wipe: these overrides are the ONLY distance",
        "#     their division has. `meets` and meets_tfrrs.division_distances",
        "#     are both silent for them, so clearing them would not restore a",
        "#     scraped value -- it would leave the division unrated, and an",
        "#     unrated division is invisible to all three rebuild passes.",
        f"#     Counted by scripts/census_override_sources.py: {len(keep):,}.",
        f"{target}.update({{",
    ]
    for (meet, div), dist in sorted(keep.items()):
        lines.append(f"    ({meet}, {div}): {dist:g},")
    lines.append("})")
    lines.append("")
    return "\n".join(lines)


def block(names, keep=None):
    """The whole wipe, between the two markers.

    ⚠ THE RESTATE GOES INSIDE THE MARKERS, NOT AFTER THEM. --undo deletes
      everything between MARK and END WIPE; a restate block below END WIPE
      would survive the undo and silently re-apply a handful of overrides on
      top of the restored file.
    """
    lines = [
        "", MARK,
        "#",
        "# Cleared so the three passes in scripts/rebuild_overrides.py can",
        "# rebuild from evidence. Every entry above this line is still here,",
        "# in git, unchanged -- .update() order means this simply wins.",
        "# Undo with: python scripts/wipe_overrides.py --undo",
    ]
    lines += [f"{n}.clear()" for n in names]
    # ★ THE RESTORED-FROM-HISTORY LAYER SURVIVES EVERY WIPE. The owner's
    #   2026-08-27 restoration (scripts/restore_old_overrides.py) lives in
    #   _DISTANCE_RESTORED_* dicts precisely so a reset cannot lose it
    #   again; this re-seeds them below the clear, fill-only, and the pass
    #   block appended after still wins any key it proposes.
    lines += [
        "for _rk, _rv in globals().get(\"_DISTANCE_RESTORED_XC\", {}).items():",
        "    _DISTANCE_OVERRIDES_XC.setdefault(_rk, _rv)",
        "for _rk, _rv in globals().get(\"_DISTANCE_RESTORED_TF\", {}).items():",
        "    _DISTANCE_OVERRIDES_TF.setdefault(_rk, _rv)",
    ]
    body = "\n".join(lines) + "\n"
    return body + restate(keep or {}) + "# === END WIPE ===\n"


def main():
    ap = argparse.ArgumentParser(
        description="Clear the regenerable overrides by appending a .clear() "
                    "block. Keeps the history.")
    ap.add_argument("--path", default=PATH)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--keep-sole-source", action="store_true",
                    dest="keep_sole",
                    help="re-state the distance overrides that are the ONLY "
                         "distance their division has, below the .clear(). "
                         "Run scripts/census_override_sources.py first.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--drops", action="store_true",
                    help="also clear _DISTANCE_DROP / _RESULT_DROP. The "
                         "passes do not regenerate these -- see the header.")
    args = ap.parse_args()

    text = io.open(args.path, encoding="utf-8").read()
    present = MARK in text

    if args.undo:
        if not present:
            print("  no wipe block present. Nothing to undo.")
            return 0
        head, _, rest = text.partition("\n" + MARK)
        _, _, tail = rest.partition("# === END WIPE ===\n")
        out = head + tail
        if not args.write:
            print("  would remove the wipe block. DRY RUN -- pass --write.")
            return 0
        io.open(args.path, "w", encoding="utf-8", newline="\n").write(out)
        print(f"  removed the wipe block from {args.path}")
        for n, c in sizes(args.path).items():
            print(f"    {n:<26} {c:>6,}")
        return 0

    if present:
        print("  a wipe block is already in place. --undo removes it.")
        return 0

    names = list(_REGENERATED) + (list(_DROPS) if args.drops else [])
    before = sizes(args.path)
    print(f"\n  {args.path}\n")
    for n in _REGENERATED + _DROPS:
        fate = "CLEARED" if n in names else "kept"
        print(f"    {n:<26} {before.get(n, 0):>6,}   {fate}")
    if not args.drops:
        print("\n    the drops are kept: no pass regenerates them, and pass 3 "
              "only sees\n    rows that are still rated. --drops overrides "
              "this.")

    keep = {}
    if args.keep_sole:
        keep = soleSource(args.sport)
        print(f"\n    keeping {len(keep):,} sole-source distance overrides: "
              f"nothing else\n    carries a distance for those divisions, so "
              f"clearing them would un-rate\n    them rather than restore a "
              f"scraped value.")
    else:
        print("\n    ⚠ --keep-sole-source NOT given. Any override that is the "
              "only distance\n      its division has will be cleared with "
              "nothing to replace it, and the\n      division will stop being "
              "rated. Run scripts/census_override_sources.py.")

    if not args.write:
        print("\n  DRY RUN -- pass --write to append the block.\n")
        return 0

    io.open(args.path, "a", encoding="utf-8", newline="\n").write(
        block(names, keep))
    after = sizes(args.path)
    print("\n  after:")
    for n in _REGENERATED + _DROPS:
        print(f"    {n:<26} {after.get(n, 0):>6,}")
    print(f"\n  appended to {args.path}. Undo with --undo --write.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
