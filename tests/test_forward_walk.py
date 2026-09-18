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
        # and the finished-sport check happens INSIDE the lock, or five
        # sessions queue up behind it and each seeds the same block again
        lock_at = body.index("async with _EXTEND_LOCK")
        self.assertIn('sp not in _EXTEND_STATE["done"]', body[lock_at:])
        self.assertIn("if not live:", body[lock_at:])

    def test_exhaustion_is_sticky(self):
        """Once a sport's walk ends, later drains must not re-seed it."""
        body = _func(self.src, "_extendFrontier")
        self.assertIn('_EXTEND_STATE["done"].add(sp)', body)
        # and `done` is consulted before any seeding happens
        self.assertLess(body.index('_EXTEND_STATE["done"]'),
                        body.index("seedAll("))

    def test_it_says_which_it_was(self):
        body = _func(self.src, "_extendFrontier")
        self.assertIn("seeded the next block", body)
        self.assertIn("forward walk finished", body)
        self.assertIn("every sport's forward walk is finished", body)


class TheStopComesFromFreshEvidence(unittest.TestCase):
    """Only a block we have just asked can say the corpus has ended."""

    def setUp(self):
        self.src = _read("scripts/launcher.py")

    def test_it_compares_watermarks_across_a_drained_block(self):
        body = _func(self.src, "_extendFrontier")
        self.assertIn("watermark", body)
        self.assertIn('_EXTEND_STATE["last_top"]', body)

    # ★ BOTH SPORTS IN ONE RUN (owner: "so I don't gotta unautomate"), and
    #   fairly: anet's XC and TF id spaces are disjoint, so a single
    #   ORDER BY meet_id is an ORDER BY sport and would drain one entirely
    #   before touching the other.
    def test_the_claim_splits_the_batch_between_sports(self):
        db = _read("scripts/database.py")
        body = _func(db, "_claimMeetBatch")
        self.assertIn('_claimOneSport(cursor, half, "XC")', body)
        self.assertIn('"TF"', body)
        self.assertIn("batch_size // 2", body)

    def test_a_sport_with_nothing_due_gives_its_half_back(self):
        db = _read("scripts/database.py")
        body = _func(db, "_claimMeetBatch")
        self.assertIn("if len(rows) < batch_size:", body)

    def test_a_dry_block_is_counted_and_a_productive_one_resets_it(self):
        body = _func(self.src, "_extendFrontier")
        self.assertIn('_EXTEND_STATE["dry"][sp] = 0', body)
        self.assertIn('_EXTEND_STATE["dry"].get(sp, 0) + 1', body)

    # ! PER SPORT. A shared counter kept seeding dead XC ids for as long as TF
    #   was productive, and finished XC would never be marked done.
    def test_each_sport_finishes_on_its_own(self):
        body = _func(self.src, "_extendFrontier")
        self.assertIn('_EXTEND_STATE["done"].add(sp)', body)
        self.assertIn('sp not in _EXTEND_STATE["done"]', body)
        self.assertIn("sports=live", body)

    # ⚠ HOW FAR PAST THE LAST REAL MEET WE WALK BEFORE GIVING UP. 2,000 x 3
    #   was eight hours of confirmed nothing (owner: "way too patient").
    def test_the_give_up_distance_is_about_a_thousand_ids(self):
        import re as _re
        ahead = int(_re.search(r'SEED_AHEAD = int\(os\.environ\.get\('
                               r'"SEED_AHEAD", (\d+)\)\)',
                               self.src).group(1))
        dry = int(_re.search(r'DRY_BLOCKS_TO_STOP = int\(os\.environ\.get\('
                             r'"DRY_BLOCKS_TO_STOP", (\d+)\)\)',
                             self.src).group(1))
        self.assertLessEqual(ahead * dry, 1500)
        self.assertGreaterEqual(ahead * dry, 300)

    def test_it_takes_several_dry_blocks_not_one(self):
        import re as _re
        m = _re.search(r"DRY_BLOCKS_TO_STOP = int\(os\.environ\.get\("
                       r'"DRY_BLOCKS_TO_STOP", (\d+)\)\)', self.src)
        self.assertIsNotNone(m)
        self.assertGreater(int(m.group(1)), 1)

    def test_it_says_which_reason_it_stopped_for(self):
        body = _func(self.src, "_extendFrontier")
        self.assertIn("nothing left to seed", body)
        self.assertIn("found no new meet", body)


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

    # ⚠ AND IT MUST NOT GATE THE WALK. 31,198 historical "not a meet"
    #   answers stopped the walk dead, and those ids are precisely the ones
    #   being re-asked: an answer from months ago is not evidence about today.
    def test_a_stale_miss_count_does_not_stop_the_seeding(self):
        seed = _func(self.src, "seedSport")
        head, _, tail = seed.partition("if do_new:")
        self.assertNotIn("stop_after_misses", tail)
        self.assertIn("seeding forward", tail)

    # ! AND THE WALK STARTS JUST ABOVE THE WATERMARK, because a new meet
    #   takes the next free id -- starting at the top of the dense block
    #   skipped 275,658..276,000, where this week's meets are.
    def test_the_start_is_the_watermark_guarded_by_the_dense_block(self):
        seed = _func(self.src, "seedSport")
        self.assertIn("frontier = top if (dense is None or top <= dense)",
                      seed)

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
        self.assertIn("are not meets (queue state 4)", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
