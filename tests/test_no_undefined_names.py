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
    "racecast/build_ranking_results.py",
    "tfrrs/parser/column_map.py",
    "tfrrs/parser/parse_xc.py",
    "tfrrs/parser/parse_xc_page.py",
    "tfrrs/parser/parse_tf.py",
    "tfrrs/scraper/save_tfrrs.py",
    "engine/speed_ratings_db.py",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
