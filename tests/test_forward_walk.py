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

    # ⚠ NO SINGLE ID MAY DECIDE THE FRONTIER (owner, 2026-09-18: "can we
    #   start at the highest batch of meet ids we've scraped, so one or two
    #   crazy ids don't fuck us? ... 670k shouldn't even be possible as the
    #   max we scraped was like 270k"). A max() is decided by its largest
    #   value, so one bogus row moves the start by 400,000 ids.
    def test_the_frontier_is_a_dense_block_not_a_max(self):
        self.assertIn("def denseFrontier(", self.src)
        body = _func(self.src, "denseFrontier")
        self.assertIn("GROUP  BY 1", body)
        self.assertIn("HAVING count(DISTINCT meet_id) >= %(minimum)s", body)
        self.assertIn("ORDER  BY 1 DESC", body)

        seed = _func(self.src, "seedSport")
        self.assertIn("denseFrontier(cur, sport)", seed)
        self.assertIn("frontier + 1", seed)
        # never a bare max, from either table
        self.assertNotIn("lo, hi = top + 1", seed)
        self.assertNotIn("max(top, asked or 0)", seed)

    def test_a_block_needs_more_than_one_meet(self):
        import re as _re
        m = _re.search(r"FRONTIER_MIN_PER_BLOCK = (\d+)", self.src)
        self.assertIsNotNone(m)
        self.assertGreater(int(m.group(1)), 1)

    # ★ scraped = 4 IS THE 404, RECORDED ALL ALONG.
    #   launcher._processMeetResult writes status=4 for `not exists`, and its
    #   own comment says a future pass should re-check. Nothing did, and the
    #   first version of this walk looked only at states 1 and 2 -- which is
    #   why it reported 0 ids above a watermark of 275,657 on a queue whose
    #   highest id is 656,607.
    def test_state_four_is_the_404(self):
        launcher = _read("scripts/launcher.py")
        call = "markScraped, meet_id, sport, status=4"
        self.assertIn(call, launcher)
        # and it is the branch taken when the meet does not exist
        i = launcher.index(call)
        self.assertIn("if not exists:", launcher[max(0, i - 400):i])

    def test_the_ceiling_counts_state_four(self):
        body = _func(self.src, "trailingMisses")
        self.assertIn("scraped IN (1, 2, 4)", body)
        self.assertIn("scraped != 4", body)

    def test_no_meet_is_an_answer_that_expires(self):
        body = _func(self.src, "seedForward")
        self.assertIn("q.scraped = 4", body)
        self.assertIn("NOT EXISTS", body)

    # ! AND ONLY THAT POPULATION. A real meet with no results is SCHEDULED,
    #   and re-asking those belongs to the recent pass, measured from the
    #   watermark. Two passes, two populations, no overlap.
    def test_it_does_not_re_ask_scheduled_meets(self):
        body = _func(self.src, "seedForward")
        i = body.index("q.scraped = 4")
        self.assertIn('SPORTS[sport]["meets"]', body)
        self.assertIn("NOT EXISTS", body[i:])

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

    # ⚠ THE BUG THAT WOULD HAVE STOPPED THE WALK EARLY (owner, 2026-09-18:
    #   "there's like 10k meets that are scheduled with no results, and
    #   they're interspersed between the last like 30k ids"). Every id above
    #   the watermark has no results by definition, so counting empties made
    #   the first run of scheduled meets look like the end of the corpus.
    def test_the_ceiling_is_missing_MEETS_not_missing_results(self):
        body = _func(self.src, "trailingMisses")
        src = _read("scripts/queue_anet_new.py")
        # it asks the meets table, not the results table
        self.assertIn('SPORTS[sport]["meets"]', body)
        self.assertNotIn('SPORTS[sport]["results"]', body)
        # and the two tables are genuinely different per sport
        self.assertIn('"meets": "meets_tf"', src)

    def test_scheduled_meets_are_retried_not_counted_as_a_ceiling(self):
        src = _read("scripts/queue_anet_new.py")
        body = _func(src, "scheduledToRetry")
        # a real meet with no results
        self.assertIn('SPORTS[sport]["meets"]', body)
        self.assertIn("NOT EXISTS", body)
        # and it does not waste a fetch on one still in the future
        self.assertIn("meet_date > %(today)s", body)

    def test_the_two_numbers_are_reported_separately(self):
        body = _func(_read("scripts/queue_anet_new.py"), "seedSport")
        self.assertIn("scheduled", body)
        self.assertIn("not meets at all", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
