"""Do the rebuilt overrides agree with the ones they replace?

    python scripts/compare_overrides.py pass1.py
    python scripts/compare_overrides.py pass1.py --against engine/corrections.bak-20260822-220201.py
    python scripts/compare_overrides.py pass1.py --worst 40

Run from the PROJECT ROOT. READ ONLY -- it never edits corrections.py.

★ THIS IS THE VALIDATION OF THE WHOLE RESET, AND NOTHING ELSE IS.

  The three passes re-derive from evidence what a person previously decided by
  hand, one division at a time, over months. If the rebuild is sound, most of
  what it proposes should land on the numbers those decisions already reached
  -- not because the old file is authoritative, but because two independent
  methods agreeing on 5,000 divisions is not a coincidence, and two methods
  disagreeing is a finding either way.

  Four buckets, and each means something different:

    AGREE     proposed the same distance the old override held. The method
              reproduces a hand-verified decision. This is the number that
              says the rebuild works.

    DISAGREE  proposed a DIFFERENT distance for a division the old file also
              corrected. Both cannot be right, and the old one was checked by
              a human. Read every one of these.

    NEW       a division the old file never covered. Genuinely new findings,
              which is the point of rebuilding -- but also where a false
              positive would hide, so the count matters against the sweep's
              noise ceiling.

    LOST      an old override with no proposal. Either the fault was real and
              the pass missed it, or the override was wrong and its absence is
              a fix. Cannot be told apart from here -- but a LOST count near
              the size of the old set means the reset is deleting, not
              rebuilding.

⚠ THE OLD FILE IS EVIDENCE, NOT TRUTH. It is where 26359/0 sat mislabelled at
  8046 m for as long as anyone had been looking. Disagreement is a prompt to
  read the division, not to defer to whichever number is older.
"""
import argparse
import importlib.util
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

_DICT = "_DISTANCE_OVERRIDES_{}"

# Two distances this close are the same decision written twice. 3218 vs 3200
# is 0.6%; the ladder's tightest real neighbours sit under 1% apart, so 1%
# calls a rounding difference the same and still separates adjacent rungs.
SAME_TOL = 0.01


def loadDicts(path, sport):
    """The dict a corrections-shaped file RESOLVES to.

    ! A PROPOSAL FILE IS NOT IMPORTABLE ON ITS OWN. emit() writes bare
      `_DISTANCE_OVERRIDES_XC.update({...})` lines, so the name has to exist
      first. Seeding an empty dict into the module's globals before exec is
      what lets the same loader read both a proposal and a full corrections.py.
    """
    name = _DICT.format(sport)
    spec = importlib.util.spec_from_file_location("_cmp_probe", path)
    mod = importlib.util.module_from_spec(spec)
    for s in ("XC", "TF"):
        setattr(mod, _DICT.format(s), {})
        setattr(mod, f"_RESULT_OVERRIDE_{s}", {})
        setattr(mod, f"_GENDER_OVERRIDES_{s}", {})
        setattr(mod, f"_RESULT_DROP_{s}", set())
        setattr(mod, f"_DISTANCE_DROP_{s}", set())
    spec.loader.exec_module(mod)
    return {k: float(v) for k, v in getattr(mod, name, {}).items()
            if isinstance(k, tuple) and len(k) == 2}


def newestBackup():
    d = os.path.join(_ROOT, "engine")
    found = sorted(f for f in os.listdir(d) if f.startswith("corrections.bak-"))
    return os.path.join(d, found[-1]) if found else None


def main():
    ap = argparse.ArgumentParser(
        description="Compare rebuilt distance overrides against the ones they "
                    "replace.")
    ap.add_argument("proposal", help="pass1.py, or any file of "
                                     "_DISTANCE_OVERRIDES_* .update() blocks")
    ap.add_argument("--against", default=None,
                    help="the old file (default: newest "
                         "engine/corrections.bak-*)")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--tol", type=float, default=SAME_TOL)
    ap.add_argument("--worst", type=int, default=25)
    args = ap.parse_args()

    old_path = args.against or newestBackup()
    if not old_path:
        print("\n  no backup found. Take one with "
              "scripts/backup_corrections.py, or pass --against.\n")
        return 1
    for p in (args.proposal, old_path):
        if not os.path.exists(p):
            print(f"\n  no such file: {p}\n")
            return 1

    new = loadDicts(args.proposal, args.sport)
    old = loadDicts(old_path, args.sport)

    print(f"\nREBUILT vs REPLACED -- {args.sport}\n")
    print(f"  proposed : {args.proposal}  ({len(new):,} divisions)")
    print(f"  replaced : {os.path.basename(old_path)}  ({len(old):,})")
    if not old:
        print("\n  ⚠ THE OLD FILE RESOLVES TO ZERO OVERRIDES. It was almost "
              "certainly saved\n    AFTER a wipe -- the .clear() block is "
              "preserved by a backup. Nothing to\n    compare against; take "
              "the backup before wiping next time.")
        return 1

    agree, disagree, newly = [], [], []
    for key, d_new in new.items():
        d_old = old.get(key)
        if d_old is None:
            newly.append((key, d_new))
        elif abs(d_new - d_old) / d_old <= args.tol:
            agree.append((key, d_new, d_old))
        else:
            disagree.append((abs(d_new - d_old) / d_old, key, d_new, d_old))
    lost = [(k, v) for k, v in old.items() if k not in new]

    covered = len(agree) + len(disagree)
    print(f"\n  {'agree with the old value':<32}{len(agree):>10,}"
          + (f"{100.0 * len(agree) / covered:>8.1f}% of overlap"
             if covered else ""))
    print(f"  {'disagree':<32}{len(disagree):>10,}"
          + (f"{100.0 * len(disagree) / covered:>8.1f}% of overlap"
             if covered else ""))
    print(f"  {'new (old file had nothing)':<32}{len(newly):>10,}"
          f"{100.0 * len(newly) / max(len(new), 1):>8.1f}% of proposals")
    print(f"  {'lost (no proposal for it)':<32}{len(lost):>10,}"
          f"{100.0 * len(lost) / max(len(old), 1):>8.1f}% of the old set")

    if disagree:
        disagree.sort(reverse=True)
        print(f"\n\n  WHERE THEY DISAGREE -- read these\n")
        print(f"  {'meet':>10}{'div':>10}{'proposed':>11}{'was':>10}"
              f"{'off':>9}")
        print("  " + "-" * 50)
        for off, (meet, div), d_new, d_old in disagree[:args.worst]:
            print(f"  {meet:>10}{div:>10}{d_new:>11.0f}{d_old:>10.0f}"
                  f"{off:>8.0%}")
        if len(disagree) > args.worst:
            print(f"  ... and {len(disagree) - args.worst:,} more")

    print("\n" + "=" * 68)
    if covered and len(agree) / covered >= 0.9:
        print(f"  THE REBUILD REPRODUCES THE OLD DECISIONS. {len(agree):,} of "
              f"{covered:,} divisions\n  the two have in common land on the "
              f"same distance, derived independently.")
    elif covered:
        print(f"  ⚠ ONLY {100.0 * len(agree) / covered:.0f}% AGREEMENT on the "
              f"overlap. Two methods that both work do not\n    disagree this "
              f"often. Read the table above before appending anything.")
    if len(lost) > 0.5 * len(old):
        print(f"\n  ⚠ {len(lost):,} OF {len(old):,} OLD OVERRIDES HAVE NO "
              f"REPLACEMENT. Some of that is\n    intended -- an override that "
              f"was wrong SHOULD vanish -- but at this share\n    the reset is "
              f"deleting more than it is rebuilding. Check a handful with\n"
              f"    rebuild_overrides.py --explain MEET/DIV before you commit "
              f"to it.")
    print("=" * 68 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
