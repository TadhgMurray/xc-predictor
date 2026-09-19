# Project: xc-predictor / tests
# File:    test_logo_supersede.py
# Purpose: replacing a crest keeps the one it replaced.
#
# ★ THE ASK (owner, 2026-09-19: "can we make sure it always replaces the
#   current image (but keeps them both stored), just so we have exactly the
#   same logos as anet no matter what"). writeFile used to os.replace the PNG,
#   which destroyed it, so a --replace scrape could not be undone or audited.
#
#   python -m unittest tests.test_logo_supersede
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scrape_school_logos as L                                  # noqa: E402


class Supersede(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.keep = os.path.join(self.dir, L.SUPERSEDED)

    def served(self, name):
        with open(os.path.join(self.dir, name), "rb") as fh:
            return fh.read()

    def archived(self):
        return sorted(os.listdir(self.keep)) if os.path.isdir(self.keep) else []

    def test_first_write_archives_nothing(self):
        name = L.writeFile("Georgetown", "TX", b"ONE", self.dir)
        self.assertEqual(self.served(name), b"ONE")
        self.assertEqual(self.archived(), [])

    def test_replacement_serves_new_and_keeps_old(self):
        name = L.writeFile("Georgetown", "TX", b"ONE", self.dir)
        L.writeFile("Georgetown", "TX", b"TWO", self.dir)
        # The site derives its path from fileFor, so the newest crest is the
        # one served -- that is what "the same logos as anet" means.
        self.assertEqual(self.served(name), b"TWO")
        kept = self.archived()
        self.assertEqual(len(kept), 1, kept)
        with open(os.path.join(self.keep, kept[0]), "rb") as fh:
            self.assertEqual(fh.read(), b"ONE")
        # ! Content-addressed, so the stem still identifies the school and a
        #   third crest does not collide with the first.
        self.assertTrue(kept[0].startswith(name[:-4] + "."), kept[0])

    def test_same_bytes_archive_nothing(self):
        L.writeFile("Georgetown", "TX", b"ONE", self.dir)
        L.writeFile("Georgetown", "TX", b"ONE", self.dir)
        self.assertEqual(self.archived(), [])

    def test_history_accumulates_without_duplicates(self):
        for png in (b"ONE", b"TWO", b"THREE", b"TWO"):
            L.writeFile("Georgetown", "TX", png, self.dir)
        # ONE, TWO, THREE each kept once -- TWO's second turn as the
        # superseded image hashes to the file that is already there.
        self.assertEqual(len(self.archived()), 3, self.archived())

    def test_keep_old_false_still_overwrites(self):
        name = L.writeFile("Georgetown", "TX", b"ONE", self.dir)
        L.writeFile("Georgetown", "TX", b"TWO", self.dir, keep_old=False)
        self.assertEqual(self.served(name), b"TWO")
        self.assertEqual(self.archived(), [])

    def test_levels_are_separate_crests(self):
        hs = L.writeFile("Amherst", "MA", b"HS", self.dir, level="hs")
        co = L.writeFile("Amherst", "MA", b"COLLEGE", self.dir, level="college")
        self.assertNotEqual(hs, co)
        self.assertEqual(self.served(hs), b"HS")
        self.assertEqual(self.served(co), b"COLLEGE")
        self.assertEqual(self.archived(), [])


if __name__ == "__main__":
    unittest.main()
