# Project: xc-predictor / tests
# File:    test_no_undefined_names.py
# Purpose: no module uses a name it never imported or bound. No database, no
#          imports of the modules themselves -- pure AST, so it covers files
#          this sandbox cannot import (torch, psycopg2, bs4).
#
#   python -m pytest -q tests/test_no_undefined_names.py
#
# ⚠⚠ WHY THIS EXISTS (2026-09-17). The owner read a diff and said, in full:
#    "import sys". auditInsertColumns used sys.modules[__name__] and
#    database.py imports re but never sys -- a NameError the moment
#    createTables ran, in the file every scraper starts from.
#
#    The same sweep immediately found a SECOND one, older than this session:
#    saveMeetTeams called a bare `Json(teams_array)` while the line thirty
#    below it correctly wrote psycopg2.extras.Json. That had been waiting in
#    a scrape path.
#
# ★ A NameError IS THE CHEAPEST BUG TO FIND AND THE MOST EXPENSIVE TO MEET.
#   It costs nothing here and eleven hours on the server, which is exactly
#   what the rescrape is about to spend.
import ast
import builtins
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The modules a scrape or a pipeline run actually starts from. Not the whole
# repo: a diagnostic that has rotted is not worth a red suite, but anything
# on the path to writing the database is.
WATCHED = (
    # ★ THE ANET SCRAPE'S OWN PATH WAS NOT WATCHED, and that is how a bare
    #   sys.exit() got into launcher.py hours after this file was written to
    #   catch exactly that in database.py (owner, 2026-09-18). These four are
    #   the first thing that runs on a scrape night.
    "scripts/launcher.py",
    "scripts/scrape_results.py",
    "scripts/vpn_rotation.py",
    "scripts/scrape_tuning.py",
    "scripts/queue_meets.py",
    "scripts/queue_status.py",
    "scripts/team_identity_census.py",
    "scripts/diag_crest_read.py",
    "tfrrs/driver/run_tfrrs.py",
    "tfrrs/driver/launch_tfrrs.py",
    "tfrrs/sweep/prefill_tfrrs_queue.py",
    "scripts/database.py",
    "scripts/result_status.py",
    "scripts/backfill_tf_venues.py",
    "scripts/merge_school_names.py",
    "scripts/link_tfrrs_to_anet.py",
    "scripts/anet_teams.py",
    "scripts/scrape_school_logos.py",
    "racecast/school_name.py",
    "racecast/school_logo.py",
    "racecast/build_school_identity.py",
    "racecast/build_team_identity.py",
    "racecast/build_ranking_results.py",
    "tfrrs/parser/column_map.py",
    "tfrrs/parser/parse_xc.py",
    "tfrrs/parser/parse_xc_page.py",
    "tfrrs/parser/parse_tf.py",
    "tfrrs/scraper/save_tfrrs.py",
    "engine/speed_ratings_db.py",
    "engine/build_team_pool.py",
    "engine/rating_outliers.py",
    "engine/diag_difficulty_calibration.py",
    "model/feature_extraction.py",
)

# Names Python provides without an import.
_MODULE_GLOBALS = {"__file__", "__name__", "__doc__", "__package__",
                   "__spec__", "__loader__", "__builtins__", "__path__"}


def undefinedNames(src):
    """Names LOADED but never imported, assigned, or bound as a parameter.

    ! DELIBERATELY CRUDE. It does not model scope, so it can only MISS a real
      fault, never invent one: every binding anywhere in the file counts as
      bound everywhere. That is the right side to err on for a guard whose
      whole value is that nobody argues with it.
    """
    tree = ast.parse(src)
    bound = set(dir(builtins)) | _MODULE_GLOBALS
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            bound.add(n.id)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bound.update(n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
    used = {x.id for x in ast.walk(tree)
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
    return sorted(used - bound)


# ⚠⚠ AND A NAME CAN BE BOUND AND STILL BLOW UP (owner, 2026-09-18:
#    "UnboundLocalError: cannot access local variable 'getConn'").
#    anet_teams.main imports getConn from database at line 713 and a new
#    branch used it at line 671. undefinedNames passed it -- correctly, by
#    its own rule: the name IS bound somewhere in the file. Python binds it
#    for the whole function scope but only ASSIGNS it when that line runs,
#    so an earlier use is an UnboundLocalError, which is a NameError
#    wearing a different hat and costs exactly as much on the server.
#
# ★ NARROW ON PURPOSE. Only a FUNCTION-LOCAL import, only a Load in the
#   same function at a line before it, and only when the module level does
#   not bind the name too (`import json` up top and a redundant local one
#   is the repo's own habit and is harmless). Anything cleverer would model
#   control flow and start arguing with the reader.
def usedBeforeLocalImport(src):
    """[(function, name)] -- a function-local import used above itself."""
    tree = ast.parse(src)
    top = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                top.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    top.add(t.id)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first_import = {}
        for n in ast.walk(fn):
            if isinstance(n, (ast.Import, ast.ImportFrom)) and n is not fn:
                for a in n.names:
                    nm = (a.asname or a.name).split(".")[0]
                    if nm not in top:
                        first_import[nm] = min(first_import.get(nm, n.lineno),
                                               n.lineno)
        if not first_import:
            continue
        for n in ast.walk(fn):
            if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n.id in first_import
                    and n.lineno < first_import[n.id]):
                out.append((fn.name, n.id))
    return sorted(set(out))


class NoModuleUsesANameItNeverGot(unittest.TestCase):

    def test_every_watched_module(self):
        offenders = []
        for rel in WATCHED:
            path = os.path.join(_ROOT, *rel.split("/"))
            if not os.path.exists(path):
                continue
            with io.open(path, encoding="utf-8") as fh:
                src = fh.read()
            bad = undefinedNames(src)
            if bad:
                offenders.append(f"{rel}: {bad}")
        self.assertEqual(offenders, [], "\n" + "\n".join(offenders))

    def test_no_watched_module_uses_a_local_import_above_itself(self):
        offenders = []
        for rel in WATCHED:
            path = os.path.join(_ROOT, *rel.split("/"))
            if not os.path.exists(path):
                continue
            with io.open(path, encoding="utf-8") as fh:
                bad = usedBeforeLocalImport(fh.read())
            if bad:
                offenders.append(f"{rel}: {bad}")
        self.assertEqual(offenders, [], "\n" + "\n".join(offenders))

    def test_the_watch_list_is_not_silently_empty(self):
        """A scanner that matches nothing passes forever."""
        found = [r for r in WATCHED
                 if os.path.exists(os.path.join(_ROOT, *r.split("/")))]
        self.assertGreaterEqual(len(found), 15, found)


class TheDetectorItself(unittest.TestCase):

    def test_it_finds_the_two_that_were_really_there(self):
        self.assertEqual(undefinedNames("import re\nsys.modules['x']\n"), ["sys"])
        self.assertEqual(undefinedNames("import psycopg2.extras\nJson(1)\n"),
                         ["Json"])

    def test_it_accepts_the_ordinary_ways_a_name_gets_bound(self):
        for src in ("import sys\nsys.path\n",
                    "from os import path\npath.join('a')\n",
                    "import os.path as p\np.join('a')\n",
                    "def f(x):\n    return x\n",
                    "for i in range(3):\n    print(i)\n",
                    "try:\n    pass\nexcept ValueError as e:\n    print(e)\n",
                    "with open('f') as fh:\n    fh.read()\n",
                    "[y for y in range(3)]\n",
                    "class C:\n    pass\nC()\n",
                    "__file__\n"):
            self.assertEqual(undefinedNames(src), [], src)

    def test_a_module_level_import_inside_a_function_still_counts(self):
        """The repo imports lazily all over the place."""
        self.assertEqual(
            undefinedNames("def f():\n    import json\n    return json.dumps({})\n"),
            [])


    # ★ THE REAL ONE, reduced: anet_teams.main, 2026-09-18.
    def test_it_finds_a_local_import_used_above_itself(self):
        src = ("def main():\n"
               "    with getConn() as c:\n"
               "        pass\n"
               "    from database import getConn\n")
        self.assertEqual(usedBeforeLocalImport(src), [("main", "getConn")])

    def test_it_accepts_the_ordinary_lazy_import(self):
        src = ("def f():\n"
               "    from database import getConn\n"
               "    return getConn()\n")
        self.assertEqual(usedBeforeLocalImport(src), [])

    # ! A MODULE-LEVEL IMPORT MAKES THE LOCAL ONE REDUNDANT, NOT WRONG.
    def test_a_name_also_imported_at_module_level_is_fine(self):
        src = ("import json\n"
               "def f():\n"
               "    x = json.dumps({})\n"
               "    import json\n"
               "    return x\n")
        self.assertEqual(usedBeforeLocalImport(src), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
