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
    """Both drain loops, and the walk they share."""

    def setUp(self):
        self.walk = _read("scripts/queue_meets.py")
        i = self.walk.index("class ForwardWalk")
        self.body = self.walk[i:self.walk.index("\ndef main(", i)]

    def test_the_session_loop_tries_to_extend_before_breaking(self):
        for rel, fn in (("scripts/launcher.py", "runSession"),
                        ("tfrrs/driver/run_tfrrs.py", "_sessionWorker")):
            body = _func(_read(rel), fn)
            i = body.index("if not batch:")
            tail = body[i:i + 400]
            with self.subTest(module=rel):
                self.assertIn("_extendFrontier", tail)
                self.assertLess(tail.index("_extendFrontier"),
                                tail.index("break"))

    def test_extending_is_forward_only(self):
        """Re-running the recent pass on every drain would re-queue the same
        meets forever."""
        self.assertIn("do_recent=False", self.body)

    def test_only_one_session_extends_at_a_time(self):
        for rel in ("scripts/launcher.py", "tfrrs/driver/run_tfrrs.py"):
            src = _read(rel)
            body = _func(src, "_extendFrontier")
            with self.subTest(module=rel):
                self.assertIn("_EXTEND_LOCK", body)
                # the walk object is built inside the lock, not per call
                lock_at = body.index("async with _EXTEND_LOCK")
                self.assertIn("ForwardWalk(", body[lock_at:])

    def test_exhaustion_is_sticky(self):
        """Once a sport's walk ends, later drains must not re-seed it."""
        self.assertIn("self.done.add(sp)", self.body)
        # and `done` is consulted before any seeding happens
        self.assertLess(self.body.index("self.done"),
                        self.body.index("seedAll("))

    def test_it_says_which_it_was(self):
        self.assertIn("seeded the next block", self.body)
        self.assertIn("forward walk finished", self.body)
        self.assertIn("forward walk is finished", self.body)


class TheStopComesFromFreshEvidence(unittest.TestCase):
    """Only a block we have just asked can say the corpus has ended."""

    def setUp(self):
        self.src = _read("scripts/queue_meets.py")

    def _walk(self):
        i = self.src.index("class ForwardWalk")
        return self.src[i:self.src.index("\ndef main(", i)]

    def test_it_compares_watermarks_across_a_drained_block(self):
        body = self._walk()
        self.assertIn("watermark(cur, sp, self.source)", body)
        self.assertIn("self.last_top", body)

    # ★ BOTH SPORTS IN ONE RUN (owner: "so I don't gotta unautomate"), and
    #   fairly: anet's XC and TF id spaces are disjoint, so a single
    #   ORDER BY meet_id is an ORDER BY sport and would drain one entirely
    #   before touching the other.
    def test_the_claim_splits_the_batch_between_sports(self):
        db = _read("scripts/database.py")
        body = _func(db, "_claimMeetBatch")
        self.assertIn('_claimOneSport(cursor, half, "XC", states)', body)
        self.assertIn('"TF"', body)
        self.assertIn("batch_size // 2", body)

    def test_a_sport_with_nothing_due_gives_its_half_back(self):
        db = _read("scripts/database.py")
        body = _func(db, "_claimMeetBatch")
        self.assertIn("if len(rows) < batch_size:", body)

    def test_a_dry_block_is_counted_and_a_productive_one_resets_it(self):
        body = self._walk()
        self.assertIn("self.dry[sp] = 0 if moved else "
                      "self.dry.get(sp, 0) + 1", body)

    # ! PER SPORT. A shared counter kept seeding dead XC ids for as long as TF
    #   was productive, and finished XC would never be marked done.
    def test_each_sport_finishes_on_its_own(self):
        body = self._walk()
        self.assertIn("self.done.add(sp)", body)
        self.assertIn("sp not in self.done", body)
        self.assertIn("sports=live", body)

    # ⚠ HOW FAR PAST THE LAST REAL MEET WE WALK BEFORE GIVING UP. 2,000 x 3
    #   was eight hours of confirmed nothing (owner: "way too patient").
    def test_the_give_up_distance_is_about_a_thousand_ids(self):
        """Both feeds, since each names its own knobs."""
        import re as _re
        for rel, a, d in (
                ("scripts/launcher.py", "SEED_AHEAD", "DRY_BLOCKS_TO_STOP"),
                ("tfrrs/driver/run_tfrrs.py", "TFRRS_SEED_AHEAD",
                 "TFRRS_DRY_BLOCKS")):
            src = _read(rel)
            ahead = int(_re.search(a + r' = int\(os\.environ\.get\("' + a
                                   + r'", (\d+)\)\)', src).group(1))
            dry = int(_re.search(d + r' = int\(os\.environ\.get\("' + d
                                 + r'", (\d+)\)\)', src).group(1))
            with self.subTest(module=rel):
                self.assertLessEqual(ahead * dry, 1500)
                self.assertGreaterEqual(ahead * dry, 300)

    def test_it_takes_several_dry_blocks_not_one(self):
        """The default must be more than one block, in both launchers."""
        import re as _re
        for rel, name in (("scripts/launcher.py", "DRY_BLOCKS_TO_STOP"),
                          ("tfrrs/driver/run_tfrrs.py", "TFRRS_DRY_BLOCKS")):
            m = _re.search(name + r' = int\(os\.environ\.get\("' + name
                           + r'", (\d+)\)\)', _read(rel))
            with self.subTest(module=rel):
                self.assertIsNotNone(m)
                self.assertGreater(int(m.group(1)), 1)

    def test_it_says_which_reason_it_stopped_for(self):
        body = self._walk()
        self.assertIn("nothing left to seed", body)
        self.assertIn("found no new meet", body)

    # ★ ONE IMPLEMENTATION. Two loops with the same shape growing two sets of
    #   stopping rules is how they drift.
    def test_both_drain_loops_use_the_same_walk(self):
        for rel in ("scripts/launcher.py", "tfrrs/driver/run_tfrrs.py"):
            src = _read(rel)
            with self.subTest(module=rel):
                self.assertIn("from queue_meets import ForwardWalk", src)
                self.assertIn("_extendFrontier", src)

    # ⚠ AND BOTH LOOPS MUST TRY BEFORE THEY BREAK. tfrrs's ended the run on a
    #   drained queue, so one launch did a single block per sport.
    def test_neither_loop_breaks_without_trying_to_extend(self):
        for rel, fn in (("scripts/launcher.py", "runSession"),
                        ("tfrrs/driver/run_tfrrs.py", "_sessionWorker")):
            body = _func(_read(rel), fn)
            i = body.index("if not batch:")
            tail = body[i:i + 400]
            with self.subTest(module=rel):
                self.assertIn("_extendFrontier", tail)
                self.assertLess(tail.index("_extendFrontier"),
                                tail.index("break"))


class TheWalkStartsFromRealData(unittest.TestCase):

    def setUp(self):
        self.src = _read("scripts/queue_meets.py")

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
        self.assertIn("denseFrontier(cur, sport, source)", seed)
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
        self.assertIn("q.scraped IN (2, 3, 4)", body)
        self.assertIn("NOT ", body)

    # ! AND ONLY THAT POPULATION. A real meet with no results is SCHEDULED,
    #   and re-asking those belongs to the recent pass, measured from the
    #   watermark. Two passes, two populations, no overlap.
    def test_it_does_not_re_ask_scheduled_meets(self):
        body = _func(self.src, "seedForward")
        i = body.index("q.scraped IN (2, 3, 4)")
        # state 1 only comes back when the id is NOT a real meet
        self.assertIn("q.scraped = 1 AND NOT _MEET_", 
                      body[i:].replace(
                          "{_meetExistsSql(source, sport)}", "_MEET_"))

    # ! "UNTIL 404" CANNOT BE ONE 404, and the stop no longer lives here at
    #   all: the seeding script only ever seeds, and each launcher decides
    #   when to stop from its own fresh evidence. A threshold in this file
    #   would be a second answer to the same question.
    def test_the_seeder_does_not_own_the_stop(self):
        self.assertNotIn("STOP_AFTER_MISSES", self.src)
        seed = _func(self.src, "seedSport")
        self.assertIn("seeding forward", seed)
        # and it says plainly that the miss count decides nothing
        self.assertIn("GATES NOTHING", _func(self.src, "trailingMisses"))

    def test_a_miss_is_an_id_we_asked_about(self):
        """An id never queued is not evidence of a ceiling."""
        body = _func(self.src, "trailingMisses")
        self.assertIn("scraped IN (1, 2, 4)", body)

    # ⚠ THE BUG THAT WOULD HAVE STOPPED THE WALK EARLY (owner, 2026-09-18:
    #   "there's like 10k meets that are scheduled with no results, and
    #   they're interspersed between the last like 30k ids"). Every id above
    #   the watermark has no results by definition, so counting empties made
    #   the first run of scheduled meets look like the end of the corpus.
    def test_the_ceiling_is_missing_MEETS_not_missing_results(self):
        body = _func(self.src, "trailingMisses")
        # it asks the MEETS table, not the results table
        self.assertIn("_meetExistsSql(source, sport)", body)
        self.assertNotIn("['results']", body)
        # and each feed and sport names its own meets table
        self.assertIn('"meets": "meets_tf"', self.src)
        self.assertIn('"meets": "meets_tfrrs"', self.src)

    def test_scheduled_meets_are_retried_not_counted_as_a_ceiling(self):
        body = _func(self.src, "scheduledToRetry")
        # a real meet with no results
        self.assertIn("_meetExistsSql(source, sport)", body)
        self.assertIn("NOT EXISTS", body)
        # and it does not waste a fetch on one still in the future
        self.assertIn("> %(today)s", body)

    def test_the_two_numbers_are_reported_separately(self):
        body = _func(self.src, "seedSport")
        self.assertIn("scheduled", body)
        self.assertIn("are not meets (queue state 4)", body)


class OneWalkForBothFeeds(unittest.TestCase):
    """★ "Just make it exactly like anet" (owner, 2026-09-18). The first tfrrs
    attempt treated a table-name difference as a design difference and shipped
    a blind seed instead. The feeds differ in three table names; nothing else.
    """

    def setUp(self):
        self.src = _read("scripts/queue_meets.py")

    def test_both_feeds_are_mapped(self):
        for feed in ('"anet"', '"tfrrs"'):
            self.assertIn(feed, self.src)
        # and each maps both sports
        self.assertEqual(self.src.count('"results": "results",'), 2)
        self.assertEqual(self.src.count('"results": "results_tf",'), 2)

    def test_every_query_is_per_source_and_sport(self):
        for name in ("watermark", "askedFrontier", "denseFrontier",
                     "trailingMisses", "scheduledAbove", "emptyRecent",
                     "scheduledToRetry", "seedForward", "requeue"):
            with self.subTest(fn=name):
                sig = _func(self.src, name).split("\n")[0]
                self.assertIn("sport", sig)
                self.assertIn("source", sig)

    # ⚠ NOTHING ACROSS SPORTS. Both feeds keep XC and TF in separate id
    #   spaces -- anet XC ~276k vs TF ~671k, tfrrs XC ~27k vs TF ~96k -- so a
    #   watermark or frontier measured over both is meaningless.
    def test_the_watermark_is_scoped_to_one_sport(self):
        body = _func(self.src, "watermark")
        self.assertIn("source = %s", body)
        self.assertIn("_t(source, sport)", body)

    def test_both_launchers_use_it(self):
        anet = _read("scripts/launcher.py")
        tfrrs = _read("tfrrs/driver/launch_tfrrs.py")
        self.assertIn("from queue_meets import", anet)
        self.assertIn("from queue_meets import", tfrrs)
        self.assertIn('source="anet"', anet)
        self.assertIn('source="tfrrs"', tfrrs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
