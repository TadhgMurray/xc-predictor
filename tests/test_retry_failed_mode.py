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
            self.assertIn("do_new=False, do_recent=False, do_failed=True",
                          src, prep)

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
