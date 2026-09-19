# Project: xc-predictor / tests
# File:    conftest.py
# Purpose: let `pytest tests/` COLLECT, by keeping the script-style test files
#          out of collection instead of letting one of them kill the run.
#
# ⚠⚠ WHY THIS EXISTS, AND WHAT IT COST NOT TO HAVE IT. Twenty files in here are
#    scripts, not test modules: they run their checks at import and end with
#
#        if failed: ...; sys.exit(1)
#
#    Under pytest, import IS collection, so the first one raises SystemExit
#    during collection and pytest dies with INTERNALERROR -- the whole suite,
#    not just that file. HANDOFF-2026-09-17 recorded it as "nobody can run the
#    suite as a whole", and that is exactly what happened next: on 2026-09-19 a
#    new diagnostic shipped with `conn = getConn(); conn.cursor()`, the bug that
#    tests/test_lint_getconn.py has caught since 2026-09-10, because the lint
#    was never run. An unrunnable suite is an absent suite.
#
# ★ DETECTED, NOT LISTED. A hardcoded list of the twenty would go stale the
#   first time somebody adds the twenty-first, and go stale silently -- back to
#   INTERNALERROR. The AST scan below asks the question directly: does this
#   module exit at IMPORT time, outside any function, class or __main__ guard?
#
# ! THE IGNORED FILES ARE STILL RUN, by scripts/run_tests.sh, which executes
#   each one as `python tests/<file>.py` and checks its exit code -- which is
#   the interface they were written for. Ignoring them here is about collection,
#   not about coverage.
import ast
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _exitsAtImport(path):
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False

    def exits(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "exit":
                    return True
                if isinstance(f, ast.Name) and f.id == "exit":
                    return True
            if isinstance(n, ast.Raise) and n.exc is not None:
                name = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
                if isinstance(name, ast.Name) and name.id == "SystemExit":
                    return True
        return False

    for node in tree.body:
        # A def, a class, or `if __name__ == "__main__"` does not run on import.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            continue
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and getattr(node.test.left, "id", "") == "__name__"):
            continue
        if exits(node):
            return True
    return False


def scriptStyle(directory=_HERE):
    """The test files that exit at import, so cannot be collected."""
    out = []
    for f in sorted(os.listdir(directory)):
        if f.startswith("test_") and f.endswith(".py"):
            if _exitsAtImport(os.path.join(directory, f)):
                out.append(f)
    return out


collect_ignore = scriptStyle()
