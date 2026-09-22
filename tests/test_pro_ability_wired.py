# Project: xc-predictor / tests
# File:    test_pro_ability_wired.py
# Purpose: every caller of resolvePool passes the ability gate, and they all
#          read it from the same place.
#
# ⚠⚠⚠ THE FAILURE THIS PREVENTS IS THE ONE THIS MODULE WAS BUILT AFTER.
#     pool_resolve's header: "There were three implementations. The engine's
#     poolOf ran a five-stage arbitration; build_ranking_results and panels
#     each called poolFor with four of its five arguments and none of the
#     gates. Measured against each other they disagreed on about a million
#     athlete-seasons."
#
#     A gate wired into the engine and not into the site is that bug again,
#     and it would be invisible: the boards and the athlete pages would each
#     look internally consistent and disagree with each other.
#
# ! AND ONE READER, NOT FOUR. engine/pro_ability.py owns the table and the
#   tri-state. A call site that grew its own "SELECT ... FROM
#   pro_ability_season" would be free to get the academic-vs-calendar season
#   key wrong, which has already happened twice in the joins beside these
#   very calls.
#
#   python -m pytest -q tests/test_pro_ability_wired.py
import ast
import io
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Every file that decides a pool. Found by grep on 2026-09-22; the last test
# in this file fails if a fifth appears.
_CALLERS = ("engine/speed_ratings.py", "engine/fill_ratings.py",
            "racecast/build_ranking_results.py", "racecast/panels.py")


def _src(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _resolvePoolCalls(src):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "resolvePool":
            yield node


def test_every_call_site_passes_the_gate():
    for rel in _CALLERS:
        calls = list(_resolvePoolCalls(_src(rel)))
        assert calls, f"{rel} no longer calls resolvePool"
        for call in calls:
            kw = {k.arg for k in call.keywords}
            assert "pro_ability" in kw, f"{rel} line {call.lineno}"


def test_none_of_them_reads_the_table_itself():
    """! ONE READER. A second SELECT over pro_ability_season is a second
    chance to type the academic-vs-calendar season key wrong.

    ⚠ STRING LITERALS, NOT THE FILE TEXT. The first version of this grepped
      the source and failed on a COMMENT that merely named the table -- the
      identical mistake test_team_pool records ("a comment that merely
      mentioned loadClubMajority failed them"), made again one file away
      from the note about it. SQL lives in strings; asking the parsed tree
      asks the question this test means.
    """
    for rel in _CALLERS:
        for node in ast.walk(ast.parse(_src(rel))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "pro_ability_season" not in node.value, f"{rel}:{node.lineno}"


def test_they_all_import_the_one_reader():
    for rel in _CALLERS:
        names = set()
        for node in ast.walk(ast.parse(_src(rel))):
            if isinstance(node, ast.ImportFrom) and node.module == "pro_ability":
                names.update(a.name for a in node.names)
        assert names & {"proAbilityFor", "loadProAbility"}, rel


def test_the_reader_is_tri_state_and_defaults_to_no_verdict():
    """⚠ THE ONE THAT MATTERS ON A FRESH DATABASE. No table must mean NO
    VERDICT, not "nobody is fast enough" -- the second reading demotes every
    professional in the corpus."""
    import pro_ability as pa
    pa.reset()
    pa._ABLE = set()                       # a populated-looking empty load
    assert pa.proAbilityFor(1, 2025) is None
    pa._ABLE = {(1, 2025)}
    assert pa.proAbilityFor(1, 2025) is True
    assert pa.proAbilityFor(2, 2025) is False
    assert pa.proAbilityFor(None, 2025) is None
    assert pa.proAbilityFor(1, None) is None
    pa.reset()


def test_the_season_is_the_academic_year_at_every_site():
    """! THE KEY THE TABLE IS BUILT ON. build_pro_ability writes
    seasonYearFromIso's answer; a caller passing left(date,4) would look up
    the wrong season for every spring race and silently demote them."""
    for rel in ("engine/fill_ratings.py", "racecast/panels.py",
                "racecast/build_ranking_results.py"):
        src = _src(rel)
        for call in _resolvePoolCalls(src):
            arg = next(k.value for k in call.keywords if k.arg == "pro_ability")
            # proAbilityFor(<person>, season) -- the second argument must be
            # the same `season` name the call's own season= uses.
            assert isinstance(arg, ast.Call), rel
            assert arg.func.id == "proAbilityFor", rel
            assert isinstance(arg.args[1], ast.Name), rel
            season_kw = next((k.value for k in call.keywords if k.arg == "season"), None)
            assert isinstance(season_kw, ast.Name), rel
            assert arg.args[1].id == season_kw.id, rel
        assert "seasonYearFromIso" in src, rel


def test_no_fifth_caller_appeared_unwired():
    """★ THE LIST ABOVE IS A CLAIM ABOUT THE TREE, so the tree is asked.
    A new call site that skips the gate is the original bug returning."""
    found = set()
    for base in ("engine", "racecast", "scripts"):
        for root, _dirs, files in os.walk(os.path.join(_ROOT, base)):
            if "__pycache__" in root:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, _ROOT).replace(os.sep, "/")
                try:
                    src = io.open(path, encoding="utf-8").read()
                except OSError:
                    continue
                if "resolvePool(" not in src:
                    continue
                try:
                    calls = list(_resolvePoolCalls(src))
                except SyntaxError:
                    continue
                if calls:
                    found.add(rel)
    found.discard("engine/pool_resolve.py")          # the definition
    assert found == set(_CALLERS), sorted(found)
