"""About's "How accurate is it" reads run_report's history (2026-10-05):
the fair tests in plain words, newest run first, nothing if there is no
history, and never another site's numbers."""
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
from accuracy import loadAccuracy, TESTS                      # noqa: E402


def _hist(recs):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in recs:
        f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    f.close()
    return f.name


class AboutAccuracy(unittest.TestCase):
    def test_no_file_is_no_section(self):
        self.assertIsNone(loadAccuracy("/nonexistent/run_history.jsonl"))

    def test_latest_run_in_plain_words(self):
        p = _hist([
            {"run": "20261001_010000", "metrics": {"holdout_race_sd": 0.053,
                                                   "lacctic_order_ours": 82.0}},
            "not json",
            {"run": "20261004_010841", "metrics": {
                "holdout_race_sd": 0.041, "lacctic_order_ours": 84.1,
                "lacctic_order_theirs": 80.0, "lacctic_5000_ours": 2.02,
                "sport_holdout_bias": -0.012, "sanity_hard": 3}},
        ])
        a = loadAccuracy(p)
        self.assertEqual(str(a["as_of"]), "2026-10-04")
        vals = {t["label"]: t["value"] for t in a["tests"]}
        self.assertEqual(vals["Predicting a race it never saw"], "±4.1%")
        self.assertEqual(vals["Who finishes ahead"], "84%")
        self.assertEqual(vals["Cross country to the track 5000"], "±2.0%")
        self.assertEqual(vals["A track season from cross country alone"], "1.2%")
        # newest first; a test the older run lacks prints as None
        self.assertEqual(str(a["history"][0]["date"]), "2026-10-04")
        self.assertEqual(a["history"][1]["values"][2], None)

    def test_never_another_sites_numbers(self):
        self.assertFalse(any("theirs" in k for k, *_ in TESTS))

    def test_runs_without_any_test_are_skipped(self):
        p = _hist([{"run": "20261004_010841", "metrics": {"sanity_hard": 3}}])
        self.assertIsNone(loadAccuracy(p))


if __name__ == "__main__":
    unittest.main()
