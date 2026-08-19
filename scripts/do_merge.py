#!/usr/bin/env python3
# do_merge.py -- merge the adjudicated repairs into the XC overrides,
# with ZERO dependence on editing corrections.py. Run from the repo root:
#     python scripts\do_merge.py
# It prints the before/after counts and writes the file apply_triage reads.

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent   # repo root


def _load(pyPath, attr):
    """Load a single attribute from a .py file by path (no package import,
    so no circular-import trap)."""
    spec = importlib.util.spec_from_file_location("m", pyPath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, attr)


def main():
    corr = ROOT / "engine" / "corrections.py"
    adds = ROOT / "scripts" / "adjudicated_overrides_xc.py"

    base = dict(_load(corr, "_DISTANCE_OVERRIDES_XC"))     # copy of live base
    additions = _load(adds, "_DISTANCE_OVERRIDES_ADDITIONS")

    print(f"base XC overrides : {len(base):,}")
    print(f"adjudicated adds  : {len(additions):,}")

    merged = dict(base)
    merged.update(additions)          # additions win on any shared key
    newKeys = len(merged) - len(base)
    print(f"merged total      : {len(merged):,}  (+{newKeys} genuinely new keys)")

    # Write the merged dict to a fresh file apply_triage can splice in cleanly.
    out = ROOT / "scripts" / "distance_override_xc.py"
    with out.open("w", encoding="utf-8") as f:
        # FIX 1: was the hardcoded literal "287". Now reports the real count.
        f.write(f"# MERGED base + {len(additions):,} adjudicated repairs (do_merge.py)\n")
        # FIX 2: was "_DISTANCE_OVERRIDES_XC = {". apply_triage._MERGES declares
        # this file defines _DISTANCE_OVERRIDES_ADDITIONS, then emits
        #     _DISTANCE_OVERRIDES_XC.update(_DISTANCE_OVERRIDES_ADDITIONS)
        #     del _DISTANCE_OVERRIDES_ADDITIONS
        # The old name never existed at that point -> NameError -> rollback,
        # every cycle since cycle 1. The name must match the contract.
        f.write("_DISTANCE_OVERRIDES_ADDITIONS = {\n")
        for (meet, div), dist in merged.items():
            f.write(f"    ({meet}, {div}): {dist},\n")
        f.write("}\n")
    print(f"\nwrote {out}  ({len(merged):,} keys)")
    print("This is the file apply_triage already reads. Re-run:")
    print("    python scripts\\apply_triage.py")


if __name__ == "__main__":
    main()