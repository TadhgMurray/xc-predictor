# Project: xc-predictor / tests
# File:    test_requirements_complete.py
# Purpose: every third-party import is written down somewhere.
#
# ★ WHY (owner, 2026-09-18). The repo had no manifest, so setting up a scrape
#   meant discovering dependencies one ModuleNotFoundError at a time --
#   psycopg2, playwright, bs4, lxml, requests -- each costing a launch, and
#   each time I "listed the modules" by reading the files I happened to open.
#   This walks the AST instead, so the answer cannot be partial.
import ast
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_REQS = ("requirements-scrape.txt", "requirements-site.txt",
         "requirements-engine.txt")

_PACKAGES = ("scripts", "tfrrs", "racecast", "engine", "model", "backfill")

# import name -> pip name, where they differ.
_ALIAS = {
    "psycopg2": "psycopg2-binary",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "playwright_stealth": "playwright-stealth",
}

# ! NOT PACKAGES, AND MEANT TO BE ABSENT. config_local is the deploy's own
#   database settings; corrections is a generated data module of ~165 MB. A
#   ModuleNotFoundError for either is a deploy problem.
_NOT_PACKAGES = {"config_local", "corrections"}


def _localModuleNames():
    out = set()
    for root, _dirs, files in os.walk(_ROOT):
        if ".git" in root or "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                out.add(f[:-3])
    return out


def _thirdPartyImports():
    stdlib = set(sys.stdlib_module_names)
    local = _localModuleNames()
    found = {}
    for pkg in _PACKAGES:
        for root, _dirs, files in os.walk(os.path.join(_ROOT, pkg)):
            if "__pycache__" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        tree = ast.parse(fh.read())
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    mods = []
                    if isinstance(node, ast.Import):
                        mods = [a.name.split(".")[0] for a in node.names]
                    elif (isinstance(node, ast.ImportFrom)
                          and node.level == 0 and node.module):
                        mods = [node.module.split(".")[0]]
                    for m in mods:
                        if (m in stdlib or m in local or m.startswith("_")
                                or m in _NOT_PACKAGES):
                            continue
                        found.setdefault(m, set()).add(
                            os.path.relpath(path, _ROOT))
    return found


class EveryDependencyIsWrittenDown(unittest.TestCase):

    def setUp(self):
        self.listed = ""
        for name in _REQS:
            path = os.path.join(_ROOT, name)
            self.assertTrue(os.path.exists(path), f"{name} is missing")
            with open(path, encoding="utf-8") as fh:
                self.listed += fh.read().lower()

    def test_nothing_is_undeclared(self):
        undeclared = {}
        for mod, where in _thirdPartyImports().items():
            if _ALIAS.get(mod, mod).lower() not in self.listed:
                undeclared[mod] = sorted(where)[:2]
        self.assertEqual(undeclared, {},
                         "third-party imports in no requirements file")

    # ! THE SCAN MUST ACTUALLY FIND THINGS. A walk that returned nothing would
    #   make the test above vacuously true.
    def test_the_scan_is_not_vacuous(self):
        found = _thirdPartyImports()
        for expected in ("psycopg2", "playwright", "bs4", "requests"):
            self.assertIn(expected, found)

    # ★ A SCRAPE MUST NOT NEED THE HEAVY ONES. torch and numba are large
    #   downloads and no launcher imports either.
    def test_the_scrape_list_excludes_torch_and_numba(self):
        with open(os.path.join(_ROOT, "requirements-scrape.txt"),
                  encoding="utf-8") as fh:
            scrape = fh.read().lower()
        self.assertNotIn("torch", scrape)
        self.assertNotIn("numba", scrape)

    def test_the_scrape_list_covers_both_launchers(self):
        with open(os.path.join(_ROOT, "requirements-scrape.txt"),
                  encoding="utf-8") as fh:
            scrape = fh.read().lower()
        for pkg in ("psycopg2-binary", "playwright", "beautifulsoup4",
                    "lxml", "requests", "curl_cffi"):
            self.assertIn(pkg, scrape)


class NoImportNamesAModuleThatIsNotThere(unittest.TestCase):
    """fetch_tf was a renamed module whose import was never updated, so
    sweep_tfrrs_meets raised ModuleNotFoundError on import."""

    def test_every_local_import_resolves(self):
        local = _localModuleNames()
        stdlib = set(sys.stdlib_module_names)
        listed = ""
        for name in _REQS:
            with open(os.path.join(_ROOT, name), encoding="utf-8") as fh:
                listed += fh.read().lower()
        broken = {}
        for mod, where in _thirdPartyImports().items():
            if (mod not in local and mod not in stdlib
                    and _ALIAS.get(mod, mod).lower() not in listed):
                broken[mod] = sorted(where)
        self.assertEqual(broken, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
