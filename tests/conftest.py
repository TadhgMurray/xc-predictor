# Project: xc-predictor / tests
# File:    conftest.py
# Purpose: make `python -m pytest -q tests` ONE run that tells the truth --
#          every file collected, in one process, with no file able to change
#          what another file sees.
#
# ⚠⚠ WHY THIS EXISTS. An unrunnable suite is an absent suite: on 2026-09-19 a
#    diagnostic shipped with `conn = getConn(); conn.cursor()`, the bug that
#    tests/test_lint_getconn.py had caught since 2026-09-10, because nobody
#    could run the suite as a whole. Three things stopped a whole-suite run:
#
#    1. NO PASSWORD. scripts/config.py raises at import when XCP_DB_PASSWORD
#       is unset (deliberately -- see tests/_env.py). Set here, once, to a
#       placeholder no database accepts.
#
#    2. SCRIPT-STYLE FILES. Twenty files used to run their checks at import
#       and end with `sys.exit(1)`; under pytest, import IS collection, and a
#       SystemExit there killed the run. They are now ordinary test
#       functions. The detector below stays as a NET: a new file written that
#       way is run as a subprocess (its own exit code is the verdict) instead
#       of being imported, so it can neither kill collection nor be skipped.
#
#    3. STUB LEAKS -- the big one. ~50 files put fakes into sys.modules at
#       import (`sys.modules["database"] = SimpleNamespace(...)`, a
#       `corrections` stub, a psycopg2 skeleton) because the real modules need
#       a database or a generated 165 MB data file. In one process those
#       fakes outlived their file: the next file's real `build_ranking_results`
#       found a `database` with no dbSetting, `fit_distance_exponent` found a
#       `corrections` whose distanceOverrideSQL was a dict, and torch's import
#       walked sys.modules through inspect and died on a stub -- leaving torch
#       half-initialised for every later file. Every one of those files
#       passed when run alone.
#
# ★ ISOLATION, NOT DISCIPLINE. Asking fifty files to clean up after
#   themselves would hold until the fifty-first. Instead each test file gets
#   its own VIEW of sys.modules and sys.path:
#
#     collect   snapshot -> import the file -> keep what it added that is
#               LOCAL (a stub, or a module from this repo, which may have
#               bound itself to a stub) -> put sys.modules back.
#     run       before the file's first test, lay its view back over the
#               shared state; when the next file starts, take it off again.
#
#   So a file sees exactly what it would see run alone, plus whatever
#   third-party packages are already loaded.
#
# ! THIRD-PARTY MODULES ARE SHARED, NOT ISOLATED. torch, numpy, scipy etc.
#   cannot be safely imported twice in one process (torch re-registers its
#   ops and raises), so a real module from outside the repo stays loaded once
#   it is loaded. The heavy and stub-prone ones are imported up front, before
#   any test file can put a fake next to them.
import ast
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_REPO = _ROOT + os.sep

# ---- 1. the environment ------------------------------------------------ #
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD and XCP_DB_QUIET

for _d in ("model", "scripts", "racecast", "engine"):
    _p = os.path.join(_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---- 2. third-party modules, loaded before any stub exists --------------- #
# ! psycopg2 TOO. Several files `sys.modules.setdefault("psycopg2", stub)`;
#   loading the real one first makes that a no-op everywhere rather than
#   depending on which file happened to run first.
for _m in ("numpy", "torch", "psycopg2", "psycopg2.extras", "psycopg2.errors",
           "psycopg2.pool", "psycopg2.extensions"):
    try:
        __import__(_m)
    except Exception:                                    # noqa: BLE001
        pass                                             # optional here


# ---- 3. script-style files: run, never imported -------------------------- #
def _exitsAtImport(path):
    """Does this module call exit() at import time, outside any function,
    class or `if __name__ == "__main__"` guard?"""
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
    """The test files that exit at import (should be none; see the header)."""
    return [f for f in sorted(os.listdir(directory))
            if f.startswith("test_") and f.endswith(".py")
            and _exitsAtImport(os.path.join(directory, f))]


class ScriptFailed(Exception):
    pass


class ScriptItem(pytest.Item):
    def runtest(self):
        p = subprocess.run([sys.executable, str(self.path)], cwd=_ROOT,
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise ScriptFailed((p.stdout + p.stderr)[-4000:])

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptFailed):
            return (f"{self.path.name} exited non-zero:\n{excinfo.value}\n"
                    "(script-style test: convert its checks into a test "
                    "function -- see tests/conftest.py)")
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script {self.path.name}"


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name="script")


def pytest_pycollect_makemodule(module_path, parent):
    if _exitsAtImport(str(module_path)):
        return ScriptFile.from_parent(parent, path=module_path)
    return None


# ---- 4. one view of sys.modules per test file ---------------------------- #
def _isLocal(mod):
    """A stub (no real file) or a module of this repo -- never shared."""
    try:
        f = mod.__dict__.get("__file__") if hasattr(mod, "__dict__") else None
    except Exception:                                    # noqa: BLE001
        return True
    if not isinstance(f, str):
        return True
    return os.path.abspath(f).startswith(_REPO)


def _localDiff(before):
    """What sys.modules gained or changed since `before`, local entries only."""
    return {k: v for k, v in list(sys.modules.items())
            if (before.get(k) is not v) and _isLocal(v)}


def _undo(before, local):
    for k in local:
        if k in before:
            sys.modules[k] = before[k]
        else:
            sys.modules.pop(k, None)


# ! AND THE ATTRIBUTES OF THE SHARED psycopg2. A stub file that finds the
#   real psycopg2 loaded still writes onto it (`psycopg2.errors.UndefinedColumn
#   = type(...)`), so those writes are part of the file's view too, and are
#   taken back off with it.
_REAL = {m: sys.modules[m] for m in ("psycopg2", "psycopg2.extras",
                                     "psycopg2.errors", "psycopg2.pool",
                                     "psycopg2.extensions")
         if m in sys.modules}
_PRISTINE = {m: dict(mod.__dict__) for m, mod in _REAL.items()}
_MISSING = object()


def _attrDiff():
    out = {}
    for m, mod in _REAL.items():
        base = _PRISTINE[m]
        ch = {k: v for k, v in mod.__dict__.items()
              if base.get(k, _MISSING) is not v}
        if ch:
            out[m] = ch
    return out


def _attrRestore():
    for m, mod in _REAL.items():
        base = _PRISTINE[m]
        for k in [k for k in mod.__dict__ if k not in base]:
            del mod.__dict__[k]
        for k, v in base.items():
            if mod.__dict__.get(k, _MISSING) is not v:
                setattr(mod, k, v)


def _attrApply(diff):
    for m, ch in diff.items():
        for k, v in ch.items():
            setattr(_REAL[m], k, v)


_views = {}          # str(path) -> (local modules, sys.path, attributes)
_active = None       # (key, sys.modules before activation, sys.path before)


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    """Importing a test file happens inside its collection: snapshot around
    it, keep the file's local modules as its view, and put the rest back."""
    if not isinstance(collector, pytest.Module) or isinstance(collector,
                                                             ScriptFile):
        yield
        return
    before, path = dict(sys.modules), list(sys.path)
    try:
        yield
    finally:
        local = _localDiff(before)
        _views[str(collector.path)] = (local, list(sys.path), _attrDiff())
        _undo(before, local)
        _attrRestore()
        sys.path[:] = path


def _deactivate():
    global _active
    if _active is None:
        return
    _key, before, path = _active
    _undo(before, _localDiff(before))
    for k, v in before.items():          # a test that popped a shared module
        if k not in sys.modules and not _isLocal(v):
            sys.modules[k] = v
    _attrRestore()
    sys.path[:] = path
    _active = None


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    global _active
    key = str(item.path)
    if _active is None or _active[0] != key:
        _deactivate()
        view = _views.get(key)
        if view is not None:
            _active = (key, dict(sys.modules), list(sys.path))
            sys.modules.update(view[0])
            sys.path[:] = view[1]
            _attrApply(view[2])
    yield


def pytest_sessionfinish(session):
    _deactivate()
