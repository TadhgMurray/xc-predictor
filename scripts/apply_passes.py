"""Append the pass proposals to corrections.py, reversibly.

    python scripts/apply_passes.py                 # report what would go in
    python scripts/apply_passes.py --write
    python scripts/apply_passes.py --undo --write

Run from the PROJECT ROOT.

★ SAME MECHANISM AS THE WIPE, AND FOR THE SAME REASON. Everything goes inside
  a marked block at the end of the file, so undoing it is deleting the block
  rather than restoring a backup. corrections.py is the record of decisions
  nobody can recompute; a generator that rewrites it in place is how the last
  one got lost.

⚠ EVERY PROPOSAL IS VALIDATED BEFORE ANY OF IT IS WRITTEN. A pass file is
  generated code that has never been executed -- one bad line makes the whole
  of corrections.py unimportable, and the next thing to find out would be the
  engine, four hours into a pipeline run. So each file is exec'd against
  throwaway dicts first, and the entries are counted and range-checked.

⚠ AND IT REFUSES A SUSPICIOUS VOLUME. The passes are freshly rewritten; if one
  suddenly proposes twenty thousand overrides that is a bug in the pass, not a
  discovery, and finding out at 3am costs a night. --force overrides.
"""
import argparse
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "engine", "corrections.py")
MARK = "# === PASS PROPOSALS (scripts/apply_passes.py) ==="
END = "# === END PASS PROPOSALS ==="

# What each pass writes, and the most it may write before this refuses.
# Sized from the corpus: pass 1 condemned 3,907 divisions of 543,261, so a
# five-figure count means something broke rather than something was found.
#
# ! THE PASS-1 CAP IS SIZED FOR A WIPE REBUILD, NOT AN INCREMENTAL RUN. The
#   8,000 figure predated --as-if-wiped: a full rebuild reverts ~6,100
#   overridden divisions and re-proposes them alongside the new discoveries,
#   so the measured legitimate volume is 9,147 (2026-08-24, sigma 4.5, every
#   line of it reviewed). The cap sits ~30% above that -- still a fraction of
#   the "twenty thousand" that would mean a broken gate, which is what this
#   guard exists to catch.
_FILES = (("pass0.py", "divisions whose evidence was deleted", 2_000),
          ("pass1.py", "whole-division distance", 12_000),
          ("pass2.py", "per-row distance and sex", 20_000),
          ("pass3.py", "individually corrupt rows", 20_000),
          ("pass4.py", "mixed-division per-result pins", 30_000))

# A distance outside this is not a race. Cheaper than trusting the snap.
_MIN_D, _MAX_D = 400.0, 25_000.0


def validate(path):
    """(entries, problems). Execs the file against throwaway dicts."""
    names = ("_DISTANCE_OVERRIDES_XC", "_DISTANCE_OVERRIDES_TF",
             "_RESULT_OVERRIDE_XC", "_RESULT_OVERRIDE_TF",
             "_GENDER_OVERRIDES_XC", "_GENDER_OVERRIDES_TF",
             "_RESULT_DROP_XC", "_RESULT_DROP_TF",
             "_DISTANCE_DROP_XC", "_DISTANCE_DROP_TF")
    env = {}
    for n in names:
        env[n] = set() if "DROP" in n else {}
    src = io.open(path, encoding="utf-8").read()
    problems = []
    try:
        exec(compile(src, path, "exec"), env)          # noqa: S102
    except Exception as exc:                           # noqa: BLE001
        return {}, [f"does not parse or run: {type(exc).__name__}: {exc}"]

    got = {}
    for n in names:
        obj = env.get(n)
        if obj:
            got[n] = len(obj)
    # ! RANGE-CHECK THE DISTANCES. A snap that returned something absurd is
    #   the failure this catches before the engine does.
    for n, obj in env.items():
        if not n.startswith("_DISTANCE_OVERRIDES") or not isinstance(obj, dict):
            continue
        for k, v in obj.items():
            if not (isinstance(k, tuple) and len(k) == 2
                    and all(isinstance(x, int) for x in k)):
                problems.append(f"{n}: bad key {k!r}")
            elif not (_MIN_D <= float(v) <= _MAX_D):
                problems.append(f"{n}{k}: distance {v} outside "
                                f"{_MIN_D:.0f}-{_MAX_D:.0f} m")
    return got, problems[:20]


def main():
    ap = argparse.ArgumentParser(
        description="Append pass proposals to corrections.py, reversibly.")
    ap.add_argument("--path", default=PATH)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="append even if a pass proposed more than its cap")
    args = ap.parse_args()

    text = io.open(args.path, encoding="utf-8").read()
    present = MARK in text

    if args.undo:
        if not present:
            print("\n  no pass block present. Nothing to undo.\n")
            return 0
        head, _, rest = text.partition("\n" + MARK)
        _, _, tail = rest.partition(END + "\n")
        if not args.write:
            print("\n  would remove the pass block. DRY RUN.\n")
            return 0
        io.open(args.path, "w", encoding="utf-8",
                newline="\n").write(head + tail)
        print(f"\n  removed the pass block from {args.path}\n")
        return 0

    if present:
        print("\n  a pass block is already in place. --undo --write removes "
              "it first.\n")
        return 1

    blocks, bad, total = [], [], 0
    print()
    for name, what, cap in _FILES:
        p = os.path.join(_ROOT, name)
        if not os.path.exists(p):
            print(f"  {name:<12} MISSING -- skipped ({what})")
            continue
        got, problems = validate(p)
        n = sum(got.values())
        total += n
        flag = ""
        if problems:
            bad.append((name, problems))
            flag = "  ⚠ PROBLEMS"
        elif n > cap:
            bad.append((name, [f"{n:,} entries, past the {cap:,} cap -- "
                               f"that is a bug in the pass, not a discovery"]))
            flag = "  ⚠ OVER CAP"
        detail = ", ".join(f"{k.replace('_XC', '')}={v:,}"
                           for k, v in sorted(got.items()))
        print(f"  {name:<12} {n:>7,}  {what}{flag}")
        if detail:
            print(f"               {detail}")
        blocks.append((name, io.open(p, encoding="utf-8").read()))

    if not blocks:
        print("\n  no pass files found. Run rebuild_overrides with --out "
              "first.\n")
        return 1

    if bad:
        print("\n  REFUSING TO WRITE:\n")
        for name, problems in bad:
            for pr in problems:
                print(f"    {name}: {pr}")
        if not args.force:
            print("\n  Fix the pass, or pass --force if you have read the "
                  "output and mean it.\n")
            return 1
        print("\n  --force given; continuing anyway.\n")

    print(f"\n  {total:,} entries from {len(blocks)} file(s)")
    if not args.write:
        print("\n  DRY RUN -- pass --write to append.\n")
        return 0

    out = [
        "", MARK,
        "#",
        "# Generated by the three passes in scripts/rebuild_overrides.py and",
        "# scripts/find_dropped_divisions.py, appended here by",
        "# scripts/apply_passes.py. .update() order means these win over",
        "# everything above.",
        "#",
        "# Undo with: python scripts/apply_passes.py --undo --write",
        "",
    ]
    for name, body in blocks:
        out.append(f"# ---------- {name} ----------")
        out.append(body.rstrip())
        out.append("")
    out.append(END)
    io.open(args.path, "a", encoding="utf-8",
            newline="\n").write("\n".join(out) + "\n")
    print(f"\n  appended to {args.path}")
    print(f"  ⚠ engine/dump_overrides.py is NOT a pipeline step -- run it "
          f"next, or\n    dist_override will not see any of this.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
