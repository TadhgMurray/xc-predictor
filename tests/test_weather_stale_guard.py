"""normalize_distance refuses a weather artifact fitted on the OLD apparent
temperatures (owner, 2026-09-26: "extreme weather races not being helped")."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import normalize_distance as N                                   # noqa: E402


class Stale(unittest.TestCase):
    def test_the_old_scale_is_stale(self):
        self.assertTrue(N._weatherStale({"reference": {"apparent_temp": 55.0}}))

    def test_the_celsius_fit_is_not(self):
        self.assertFalse(N._weatherStale({"reference": {"apparent_temp": 13.0}}))

    def test_an_odd_artifact_is_left_to_the_other_checks(self):
        self.assertFalse(N._weatherStale({}))
        self.assertFalse(N._weatherStale({"reference": {}}))


if __name__ == "__main__":
    unittest.main()
