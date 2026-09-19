#!/usr/bin/env python3
# Project: xc-predictor / scripts
# File:    test_suite_census.py
# Purpose: how much of the test suite actually RUNS, per runner.
#
# ⚠ WHY THIS EXISTS. `python -m unittest discover -s tests` reports a tidy
#   "Ran 890 tests ... FAILED (failures=10, errors=87)" and that number was
#   being read as the suite's state. It is not: 115 of 193 test modules
#   contribute ZERO tests to that run and each reports "OK" on its own,
#   because they are written as bare pytest-style `def test_*()` functions
#   and unittest's loader only collects unittest.TestCase subclasses. Their
#   assertions never execute under that runner. A module that runs no tests
#   and says OK is worse than a failing one -- it is a green light wired to
#   nothing, and this codebase has already been bitten three separate times
#   by code that was written and wired to nothing.
#
# ! THIS SCRIPT CHANGES NOTHING AND RUNS NO TESTS. It reads each module's
#   SOURCE with ast -- no import, so a module that calls sys.exit() at import
#   time (tests/test_capped.py does, which is why pytest cannot collect the
#   suite either) cannot take the census down with it.
#
#     python scripts/test_suite_census.py
#     python scripts/test_suite_census.py --list bare
import argparse
import ast
import os
import sys

TESTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests")


# _classify
# Purpose:   What a single test module offers each runner, from its source.
# Arguments: path -- the .py file.
# Output:    a dict with the counts and the verdict.
def _classify(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError as exc:
        return {"verdict": "unparseable", "cases": 0, "bare": 0,
                "note": f"{exc.__class__.__name__}: {exc}"}
    # ! sys.exit AT MODULE LEVEL MEANS SCRIPT STYLE, AND IT IS FOUND BY
    #   WALKING ONLY THE MODULE'S OWN CODE. tests/test_capped.py puts it
    #   inside `if failed:` at the bottom, so a scan of tree.body alone
    #   missed it and called the module testless -- which was unfair: it
    #   runs every one of its checks under `python tests/test_capped.py`.
    #   It is invisible to the two RUNNERS, not dead. Function and class
    #   bodies are skipped: a sys.exit inside main() is an ordinary CLI.
    def _moduleLevelExit(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and ast.unparse(sub).startswith("sys.exit")):
                    return True
        return False

    exits = _moduleLevelExit(tree.body)
    cases, bare = 0, 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            # A TestCase subclass by NAME, which is all a source read can
            # know -- `unittest.TestCase`, `TestCase`, or a local base that
            # itself subclasses one. Counting methods is what matters.
            bases = {ast.unparse(b) for b in node.bases}
            if any("TestCase" in b for b in bases):
                cases += sum(1 for m in node.body
                             if isinstance(m, (ast.FunctionDef,
                                               ast.AsyncFunctionDef))
                             and m.name.startswith("test"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                bare += 1
    if cases or bare:
        pass                     # a real runner can see it; classify below
    elif exits:
        # module-level checks plus a non-zero exit: a script, and it works
        verdict = "script style"
        return {"verdict": verdict, "cases": 0, "bare": 0,
                "note": "runs only via `python <path>`"}
    if cases and bare:
        verdict = "both"
    elif cases:
        verdict = "unittest"
    elif bare:
        verdict = "bare only"
    else:
        verdict = "no tests"
    return {"verdict": verdict, "cases": cases, "bare": bare, "note": ""}


def main():
    ap = argparse.ArgumentParser(
        description="How much of tests/ actually runs, per runner")
    ap.add_argument("--list", dest="which", default=None,
                    choices=("bare", "none", "broken", "all"),
                    help="also print the module names in that category")
    args = ap.parse_args()

    rows = []
    for name in sorted(os.listdir(TESTS_DIR)):
        if name.startswith("test_") and name.endswith(".py"):
            rows.append((name, _classify(os.path.join(TESTS_DIR, name))))
    if not rows:
        print(f"no test modules found under {TESTS_DIR}")
        return 1

    buckets = {}
    for name, info in rows:
        buckets.setdefault(info["verdict"], []).append((name, info))

    total_cases = sum(i["cases"] for _n, i in rows)
    total_bare = sum(i["bare"] for _n, i in rows)

    print(f"\n  test suite census — {len(rows)} modules under tests/\n")
    print(f"  {'verdict':<18} {'modules':>8} {'TestCase':>9} {'bare fns':>9}")
    print(f"  {'-' * 18} {'-' * 8} {'-' * 9} {'-' * 9}")
    for verdict in ("unittest", "both", "bare only", "script style",
                    "no tests", "unparseable"):
        got = buckets.get(verdict, [])
        if not got:
            continue
        print(f"  {verdict:<18} {len(got):>8} "
              f"{sum(i['cases'] for _n, i in got):>9} "
              f"{sum(i['bare'] for _n, i in got):>9}")
    print(f"  {'-' * 18} {'-' * 8} {'-' * 9} {'-' * 9}")
    print(f"  {'TOTAL':<18} {len(rows):>8} {total_cases:>9} {total_bare:>9}")

    bare_only = buckets.get("bare only", [])
    scripts = buckets.get("script style", [])
    nothing = buckets.get("no tests", [])
    invisible = len(bare_only) + len(scripts) + len(nothing)
    hidden_fns = sum(i["bare"] for _n, i in bare_only)
    visible = len(rows) - invisible
    print(f"\n  ⚠ UNDER `python -m unittest discover -s tests`:")
    print(f"      {invisible} of {len(rows)} modules contribute ZERO tests, "
          f"and each reports OK on its own.")
    print(f"      {hidden_fns} bare assertion-bearing functions never "
          f"execute under it.")
    print(f"      So that runner's pass/fail line describes {visible} "
          f"modules, not {len(rows)}.")
    if scripts:
        print(f"\n  ! {len(scripts)} of those are SCRIPT STYLE and are not "
              f"dead: module-level checks\n      and a non-zero exit, so "
              f"they do run — but only via `python tests/<name>.py`,\n      "
              f"one at a time. And a module-level sys.exit aborts a whole "
              f"pytest run\n      (INTERNALERROR, 'caught unexpected "
              f"SystemExit'), so pytest cannot collect\n      the suite "
              f"either while they sit in tests/.")
    if nothing:
        print(f"\n  ! {len(nothing)} module(s) show no checks of any kind "
              f"to this reader. Either they\n      test through a helper "
              f"this census cannot see, or they test nothing.")
    print(f"\n  ★ THE FIX IS A CHOICE, NOT A NUMBER: either every module "
          f"subclasses\n    unittest.TestCase — which runs under BOTH "
          f"runners — or pytest becomes the\n    one supported runner and "
          f"the script-style modules move out of tests/ so it\n    can "
          f"collect. Until then no single command sees the whole suite, and "
          f"a green\n    line from either runner means less than it looks "
          f"like.\n    New tests: use unittest.TestCase.")

    if args.which:
        want = {"bare": ("bare only",), "none": ("no tests",),
                "broken": ("exits at import", "unparseable"),
                "all": tuple(buckets)}[args.which]
        print()
        for verdict in want:
            for name, info in buckets.get(verdict, []):
                bits = []
                if info["bare"]:
                    bits.append(f"{info['bare']} bare")
                if info["cases"]:
                    bits.append(f"{info['cases']} TestCase")
                if info["note"]:
                    bits.append(info["note"])
                tail = f"  ({'; '.join(bits)})" if bits else ""
                print(f"  {verdict:<18} {name}{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
