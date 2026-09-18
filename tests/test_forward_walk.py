# Project: xc-predictor / tests
# File:    test_forward_walk.py
# Purpose: "keep going until 404" keeps going.
#
# ★ THE OWNER'S SPEC (2026-09-17): "scrape new meets linearly starting from
#   our last collected meet id (that was actually real). Keep going until
#   404." And (2026-09-18), on how much there is to do: the genuinely-empty
#   recent meets are "60 for tf and xc combined, and then however many have
#   been made since then that we've never scraped at all".
#
# ⚠ SO THE FORWARD WALK IS THE WHOLE JOB, and it was seeding SEED_AHEAD ids
#   ONCE at startup. Draining them ended the run, which turned "until 404"
#   into "run the launcher again by hand per 2,000 ids". A drained queue now
#   seeds the next block; only a block that comes back empty stops the run.
import io
import os
import re
import ast
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with io.open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _func(src, name):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == name:
            return ast.get_source_segment(src, n)
    raise AssertionError(f"{name} not found")


class ADrainedQueueExtendsInsteadOfStopping(unittest.TestCase):

    def setUp(self):
        self.src = _read("scripts/launcher.py")

    def test_the_session_loop_tries_to_extend_before_breaking(self):
        body = _func(self.src, "runSession")
        i = body.index("if not batch:")
        tail = body[i:i + 300]
        self.assertIn("_extendFrontier", tail)
        # the break must come AFTER the attempt, not instead of it
        self.assertLess(tail.index("_extendFrontier"), tail.index("break"))

    def test_extending_is_forward_only(self):
        """Re-running the recent pass on every drain would re-queue the same
        meets forever."""
        body = _func(self.src, "_extendFrontier")
        self.assertIn("do_recent=False", body)

    def test_only_one_session_extends_at_a_time(self):
        body = _func(self.src, "_extendFrontier")
        self.assertIn("_EXTEND_LOCK", body)
        # and the check is repeated inside the lock, or five sessions queue up
        # behind it and each seeds again
        self.assertEqual(body.count('_EXTEND_STATE["exhausted"]'), 3)

    def test_exhaustion_is_sticky(self):
        """Once the walk ends, later drains must not re-seed forever."""
        body = _func(self.src, "_extendFrontier")
        self.assertIn('_EXTEND_STATE["exhausted"] = True', body)

    def test_it_says_which_it_was(self):
        body = _func(self.src, "_extendFrontier")
        self.assertIn("seeded the next block", body)
        self.assertIn("corpus ends here", body)


class TheWalkStartsFromRealData(unittest.TestCase):

    def setUp(self):
        self.src = _read("scripts/queue_anet_new.py")

    # ! "ACTUALLY REAL" = PRODUCED RESULTS. A queue row proves we asked.
    def test_the_watermark_is_the_last_id_with_results(self):
        body = _func(self.src, "watermark")
        self.assertIn("max(meet_id)", body)
        self.assertIn("results", body)
        self.assertNotIn("meet_queue", body)

    # ! "UNTIL 404" CANNOT BE ONE 404: anet ids have real gaps.
    def test_the_stop_is_a_run_of_misses_not_one(self):
        self.assertIn("STOP_AFTER_MISSES", self.src)
        m = re.search(r"STOP_AFTER_MISSES = (\d+)", self.src)
        self.assertIsNotNone(m)
        self.assertGreater(int(m.group(1)), 1)

    def test_a_miss_is_an_id_we_asked_about(self):
        """An id never queued is not evidence of a ceiling."""
        body = _func(self.src, "trailingMisses")
        self.assertIn("scraped IN (1, 2)", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
