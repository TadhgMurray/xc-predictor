# scripts/census_corrections.py  (v2 -- v1 had a real bug, see _isTable)
#
# Purpose : Count every correction table in engine/corrections.py, per sport,
#           and prove each _BY_SPORT wrapper still points at the live object.
# Why     : (a) _RESULT_DROP_* grew by an unknown amount across 8 loop cycles.
#           (b) _DISTANCE_DROP has never been re-counted (Q13).
#           (c) Bug #2 (rebind-orphaned dicts) must never recur silently.
# Output  : an aligned table + an ORPHAN verdict per wrapper. Read-only.
#
# v1 DEFECT, recorded rather than quietly fixed: the discovery rule was
#   `isinstance(value, dict)`, which silently excluded every set-typed table --
#   i.e. exactly the drop tables the script existed to count. A type whitelist
#   that swallows misses. The rule below tests for a LENGTH, not a type.

import argparse
import importlib
import sys
from pathlib import Path


# ---------------------------------------------------------------- loading
def _loadCorrections(repoRoot):
    # Purpose   : import engine/corrections.py as a module object.
    # Arguments : repoRoot -- Path to the dir containing engine/.
    # Output    : the module object.
    # Note      : insert(0, ...) puts our path FIRST so we get the repo's
    #             corrections.py, not a same-named module elsewhere on the path.
    sys.path.insert(0, str(repoRoot))
    return importlib.import_module("engine.corrections")


# ---------------------------------------------------------------- discovery
def _isTable(value):
    # Purpose   : decide whether a module attribute is a correction table.
    # Arguments : value -- any object off the module.
    # Output    : bool.
    # Rule      : "has a length and isn't text" -- dict, set, list, tuple all
    #             pass; ints and strings don't. We test the BEHAVIOUR we need
    #             (len works) instead of enumerating types we happen to expect.
    if isinstance(value, (str, bytes)):
        return False                     # len() works but these aren't tables
    return hasattr(value, "__len__")     # duck-typing: can we count it?


def _tableNames(mod):
    # Purpose   : find correction tables without a hardcoded name list.
    # Arguments : mod -- the corrections module object.
    # Output    : a sorted list of attribute names.
    # Note      : dir() lists every attribute name; getattr() fetches the value.
    #             We keep private UPPER_SNAKE names and skip dunders.
    names = []
    for name in dir(mod):
        if not name.startswith("_") or name.startswith("__"):
            continue
        if _isTable(getattr(mod, name)):
            names.append(name)
    return sorted(names)


# ---------------------------------------------------------------- counting
def _describe(value):
    # Purpose   : one table -> (count, type name).
    # Arguments : value -- the table object.
    # Output    : (int, str) e.g. (368213, 'set').
    # Note      : type(value).__name__ gives the bare class name as a string,
    #             so the report can show WHAT it counted, not just how many.
    return (len(value), type(value).__name__)


def _expandBySport(name, value):
    # Purpose   : turn a {"XC": <table>, "TF": <table>} wrapper into real rows.
    # Arguments : name  -- the wrapper's name, e.g. '_RESULT_DROP_BY_SPORT'.
    #             value -- the wrapper dict itself.
    # Output    : a list of (label, count, kind) tuples, one per sport.
    # Why       : len(wrapper) is 2 -- the SPORT COUNT, not the payload. The
    #             number you actually want lives one level down, in the values.
    rows = []
    for sport, table in sorted(value.items()):
        count, kind = _describe(table)
        rows.append((f"{name}[{sport}]", count, kind))
    return rows


def _censusRows(mod):
    # Purpose   : assemble every report row, expanding wrappers as we go.
    # Arguments : mod -- the module object.
    # Output    : a list of (label, count, kind) tuples.
    rows = []
    for name in _tableNames(mod):
        value = getattr(mod, name)
        if name.endswith("_BY_SPORT") and isinstance(value, dict):
            rows.extend(_expandBySport(name, value))   # recurse one level
        else:
            count, kind = _describe(value)
            rows.append((name, count, kind))
    return rows


# ---------------------------------------------------------------- bug #2 probe
def _orphanCheck(mod, wrapperName):
    # Purpose   : prove a _BY_SPORT wrapper still points at the LIVE table.
    # Arguments : mod         -- the module object.
    #             wrapperName -- e.g. '_RESULT_DROP_BY_SPORT'.
    # Output    : a list of (label, verdict) tuples.
    # THE IDEA  : `is` compares IDENTITY (same object in memory), `==` compares
    #             VALUE. Bug #2 was a wrapper holding a reference to a dict that
    #             a later `X = {...}` rebind replaced -- the wrapper kept the OLD
    #             object. It still had contents, so nothing errored; it was just
    #             stale. `==` would have missed it. Only `is` catches it.
    wrapper = getattr(mod, wrapperName, None)
    if not isinstance(wrapper, dict):
        return []
    stem = wrapperName.replace("_BY_SPORT", "")     # '_RESULT_DROP'
    out = []
    for sport, table in sorted(wrapper.items()):
        live = getattr(mod, f"{stem}_{sport}", None)   # '_RESULT_DROP_XC'
        if live is None:
            out.append((f"{stem}_{sport}", "no top-level twin (fine if by design)"))
        elif live is table:
            out.append((f"{stem}_{sport}", "OK  wrapper points at the live object"))
        else:
            out.append((f"{stem}_{sport}", "*** ORPHANED -- BUG #2 IS BACK ***"))
    return out


# ---------------------------------------------------------------- reporting
def _report(rows, orphans):
    # Purpose   : all printing lives here, so the counters stay print-free.
    # Arguments : rows    -- (label, count, kind) tuples.
    #             orphans -- (label, verdict) tuples.
    if not rows:
        print("no correction tables found -- did the import resolve?")
        return
    width = max(len(label) for label, _, _ in rows)
    print("CORRECTIONS CENSUS")
    print("-" * (width + 22))
    for label, count, kind in rows:
        # :<{width} left-align to the longest label; :>11, thousands separators
        print(f"  {label:<{width}}  {count:>11,}  ({kind})")
    print("-" * (width + 22))
    if orphans:
        print("\nWRAPPER IDENTITY (bug #2 probe)")
        for label, verdict in orphans:
            print(f"  {label:<{width}}  {verdict}")


# ---------------------------------------------------------------- entry
def main():
    parser = argparse.ArgumentParser(description="Count corrections.py tables.")
    parser.add_argument("--root", default=".", help="dir containing engine/")
    args = parser.parse_args()

    mod = _loadCorrections(Path(args.root).resolve())
    rows = _censusRows(mod)

    orphans = []
    for name in _tableNames(mod):
        if name.endswith("_BY_SPORT"):
            orphans.extend(_orphanCheck(mod, name))

    _report(rows, orphans)


if __name__ == "__main__":
    main()