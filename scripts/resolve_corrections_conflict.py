# Project: xc-predictor / scripts
# File:    resolve_corrections_conflict.py
# Purpose: Resolve a git merge conflict in corrections.py by KEEPING BOTH
#          SIDES, which for this file is the correct resolution.
#
#     python scripts/resolve_corrections_conflict.py            # report
#     python scripts/resolve_corrections_conflict.py --write
#
# ★ WHY BOTH SIDES. corrections.py is append-structured: every block after the
#   base dicts is `_X.update({...})`. Two people appending different
#   adjudications produce a textual conflict and NO semantic one -- both sets
#   of decisions are wanted, and .update() in file order already defines who
#   wins a shared key. Picking a side throws away real work for no reason.
#
# ⚠ ONLY FOR THIS FILE'S APPEND BLOCKS. If a conflict ever lands inside a
#   single dict literal rather than between whole blocks, this will still
#   produce valid Python but the ordering may not be what either side meant.
#   The script compiles the result and refuses to write if it does not parse.

import argparse
import ast
import io
import sys

PATH = "engine/corrections.py"


def resolve(text):
    """Conflicted text -> (resolved text, blocks resolved)."""
    out, blocks = [], 0
    mode = None          # None | "ours" | "theirs"
    for line in text.split("\n"):
        if line.startswith("<<<<<<<"):
            mode, blocks = "ours", blocks + 1
            continue
        if line.startswith("=======") and mode == "ours":
            mode = "theirs"
            continue
        if line.startswith(">>>>>>>") and mode == "theirs":
            mode = None
            continue
        out.append(line)      # every non-marker line survives, both sides
    return "\n".join(out), blocks


def main():
    ap = argparse.ArgumentParser(
        description="Keep both sides of a corrections.py merge conflict.")
    ap.add_argument("--path", default=PATH)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    text = io.open(args.path, encoding="utf-8").read()
    if "<<<<<<<" not in text:
        print(f"  {args.path}: no conflict markers.")
        try:
            ast.parse(text)
            print("  parses cleanly. Nothing to do.")
            return 0
        except SyntaxError as exc:
            print(f"  ⚠ but it does NOT parse: line {exc.lineno}: {exc.msg}")
            return 1

    fixed, blocks = resolve(text)
    print(f"  {args.path}: {blocks} conflict block(s), both sides kept.")
    try:
        ast.parse(fixed)
    except SyntaxError as exc:
        print(f"  ⚠ REFUSING TO WRITE -- the result does not parse: "
              f"line {exc.lineno}: {exc.msg}")
        return 1
    print("  the result parses.")
    if not args.write:
        print("  DRY RUN -- pass --write to apply.")
        return 0
    io.open(args.path + ".conflicted", "w", encoding="utf-8").write(text)
    io.open(args.path, "w", encoding="utf-8", newline="\n").write(fixed)
    print(f"  wrote {args.path} (original kept at {args.path}.conflicted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
