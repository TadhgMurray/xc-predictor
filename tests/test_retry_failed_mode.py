# Project: xc-predictor / tests
# File:    test_retry_failed_mode.py
# Purpose: --retry-failed really is retry-only. Pure source reading; no
#          database, no network.
#
#   python tests/test_retry_failed_mode.py
#
# ⚠⚠ WHY (owner, 2026-09-18): "can we rerun the scrapers to do the failed ones
#    without the forwards pass and stuff." Two halves have to hold together,
#    and each is easy to land without the other:
#      1. the failures are re-claimed AT ALL -- nothing did this before.
#         seedForward re-asks states 2, 3 and 4 but only inside the block it
#         is walking, so a meet that failed below the watermark (the 62 that
#         died on the Unicode jsonb bug) was never re-claimed by any pass.
#      2. the forward walk does NOT run. A drained queue must END the run, or
#         the walk seeds the next block the moment the failures finish and the
#         mode quietly becomes an ordinary scrape night.
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ⚠⚠ AND A RETRY MUST NOT GO THROUGH STATE 0 (owner, 2026-09-18: "I think
#    you're checking more ids than just the failed ones. I'm not even sure
#    you're resetting the ids at all"). Both halves were the same fault: the
#    first version reset 2 and 3 to 0 and then claimed state 0 -- which is
#    EVERY due row, including thousands an earlier forward walk had seeded.
#    The run was an ordinary scrape night wearing a retry flag, and no output
#    could show otherwise, because the reset made the failures identical to
#    everything else. A retry now claims 2 and 3 DIRECTLY and resets nothing.
class TheClaimIsScopedToTheFailures(unittest.TestCase):

    def test_both_claims_take_the_states_from_the_caller(self):
        db = read("scripts", "database.py")
        self.assertIn("def _claimOneSport(cursor, limit, sport, states=(0,)):",
                      db)
        self.assertIn("def _claimTFRRSBatch(cursor, batch_size, states=(0,)):",
                      db)
        self.assertEqual(db.count("WHERE scraped = ANY(%s)"), 2)
        self.assertNotIn("WHERE scraped = 0 AND source =", db)

    def test_retry_mode_claims_2_and_3_not_0(self):
        for mod in ("scripts/launcher.py", "tfrrs/driver/run_tfrrs.py"):
            src = read(*mod.split("/"))
            self.assertIn("CLAIM_STATES = (2, 3)", src, mod)
            self.assertIn("else (0,)", src, mod)
            self.assertIn("CLAIM_STATES)", src, mod)

    # ⚠⚠ AND THE START-UP RESET MUST NOT DRAIN STATE 3 OUT OF THAT SET.
    #    Both launchers flip 3 -> 0 at start; in retry mode that would move
    #    every stranded claim out of the very set the run works on.
    def test_the_startup_reset_is_skipped_in_retry_mode(self):
        a = read("scripts", "launcher.py")
        i = a.index("        resetInProgress()")
        self.assertIn("if RETRY_FAILED:", a[i - 400:i])
        t = read("tfrrs", "driver", "launch_tfrrs.py")
        j = t.index("_resetStaleClaimsSync)")
        self.assertIn("TFRRS_RETRY_FAILED", t[j - 500:j])

    # ! AND THE COUNT REPORTED MUST BE THE FAILURES, or a queue full of them
    #   reports "nothing due" and the run exits.
    def test_the_reported_work_is_the_failures(self):
        self.assertIn("def failedCounts(conn, source=\"anet\"):",
                      read("scripts", "queue_meets.py"))
        self.assertIn("failedCounts(conn, \"anet\") if RETRY_FAILED",
                      read("scripts", "launcher.py"))
        self.assertIn("wanted = ((2, 3)",
                      read("tfrrs", "driver", "launch_tfrrs.py"))


class ThePassExists(unittest.TestCase):

    def test_it_resets_failed_and_stranded_only(self):
        q = read("scripts", "queue_meets.py")
        self.assertIn("def retryFailed(cur, sport, source=\"anet\"):", q)
        body = q[q.index("def retryFailed("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("scraped IN (2, 3)", body)
        self.assertIn("SET scraped = 0", body)

    # ⚠ STATE 4 IS NOT A FAILURE. It is the feed saying "no meet here", and
    #   re-asking the whole id space is the forward walk's aimed, bounded job.
    def test_it_does_not_touch_state_4(self):
        q = read("scripts", "queue_meets.py")
        body = q[q.index("def retryFailed("):]
        body = body[:body.index("\ndef ")]
        self.assertNotIn("4", body.split('"""')[-1])

    def test_seed_sport_can_run_it_and_defaults_off(self):
        q = read("scripts", "queue_meets.py")
        self.assertIn("do_failed=False", q)
        self.assertIn("if do_failed:", q)

    # ! BEFORE THE WATERMARK GUARD: a failed meet is worth re-claiming whether
    #   or not the feed has produced results yet.
    def test_it_runs_before_the_watermark_early_return(self):
        q = read("scripts", "queue_meets.py")
        self.assertLess(q.index("if do_failed:"),
                        q.index("top = watermark(cur, sport, source)"))


class BothLaunchersHonourIt(unittest.TestCase):

    # (queue-pass file, drain-loop file, env var, the token the DRAIN file
    #  guards on -- the anet launcher reads a module constant it set from the
    #  env, tfrrs reads os.environ directly, and either is fine)
    CASES = (("scripts/launcher.py", "scripts/launcher.py",
              "ANET_RETRY_FAILED", "RETRY_FAILED"),
             ("tfrrs/driver/launch_tfrrs.py", "tfrrs/driver/run_tfrrs.py",
              "TFRRS_RETRY_FAILED", "TFRRS_RETRY_FAILED"))

    def test_the_queue_pass_is_retry_only(self):
        for prep, _drain, env, _tok in self.CASES:
            src = read(*prep.split("/"))
            self.assertIn(env, src, prep)
            self.assertIn("Nothing seeded, nothing reset", src, prep)

    # ★★ THE HALF THAT IS EASY TO FORGET. Without this the walk seeds the next
    #    block the moment the failures drain, and the mode is a no-op.
    def test_the_forward_walk_is_skipped(self):
        for _prep, drain, _env, tok in self.CASES:
            src = read(*drain.split("/"))
            i = src.index("def _extendFrontier")
            # everything up to where the walk is first built: the guard has to
            # be inside this, or it fires too late to prevent seeding
            body = src[i:src.index("ForwardWalk(", i)]
            self.assertIn(tok, body, drain)
            self.assertIn("return False", body, drain)
            self.assertLess(body.index(tok), body.index("return False"), drain)

    def test_an_empty_retry_exits_clean_not_as_an_error(self):
        """Nothing failed is success, not the mis-seeded-queue error."""
        for prep, _drain, _env, _tok in self.CASES:
            src = read(*prep.split("/"))
            i = src.index("nothing failed")
            self.assertIn("sys.exit(0)", src[i:i + 400], prep)


if __name__ == "__main__":
    unittest.main(verbosity=2)
