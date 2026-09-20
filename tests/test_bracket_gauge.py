# Project: xc-predictor / tests
# File:    test_bracket_gauge.py
# Purpose: which cells are the zero. Pure source + a numpy check of the pin.
#
#   python tests/test_bracket_gauge.py
#
# ★ THE OWNER'S DESIGN (2026-09-18): "we make flat 400 difficulty outdoor to
#   0.0 no matter what. Then we put indoor on avg comparison, and the indoor
#   venues are only rated difficulty wise against each other (accounting for
#   fitness)?"
#
# ⚠ WHAT IT WAS: the gauge held the vote-weighted mean of D per (sport, era) at
#   zero, and `sport` is ONE BIT, so indoor and outdoor track were anchored
#   TOGETHER -- combined mean pinned, split between them free. Measured: indoor
#   published 1.5% EASIER than outdoor, sign wrong. And because the COMBINED
#   mean was held at zero, indoor drifting low pushed outdoor UP to compensate,
#   so the error was not even confined to the group that caused it.
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


class TheGaugeIsOutdoorByDefault(unittest.TestCase):

    def setUp(self):
        self.src = read("engine/bracket_engine.py")

    def test_the_default_is_outdoor(self):
        self.assertIn('GAUGE_DEFAULT = "outdoor"', self.src)
        # ! THE HISTORIC TWO ARE STILL THERE, and the tuple is no longer
        #   asserted whole: "flat400" joined it on 2026-09-19 (the owner's
        #   flat-outdoor-400 pin, plan §2). Pinning the literal tuple made
        #   ADDING a choice a failure, which is not the claim -- the claim is
        #   that the DEFAULT is still outdoor and the old behaviour is still
        #   reachable.
        # ! READ FROM THE SOURCE, not by importing: this file is a pure
        #   source check (see the header) and runs as a script with no path
        #   set up, so `import bracket_engine` is not available here.
        line = [l for l in self.src.splitlines()
                if l.startswith("GAUGE_CHOICES = (")]
        self.assertEqual(len(line), 1, "GAUGE_CHOICES moved or vanished")
        for choice in ('"outdoor"', '"all"'):
            self.assertIn(choice, line[0], line[0])

    # ★ cell_pg is 0 XC, 1 outdoor track, 2 indoor track (priorGroupOfKeys), so
    #   the reference is everything that is not indoor -- XC keeps its own
    #   whole-group zero and the track groups anchor on outdoor.
    def test_the_reference_excludes_indoor_only(self):
        self.assertIn("gauge_ref = (cell_pg != 2) if gauge == \"outdoor\"",
                      self.src)

    # ! THE PIN IS THE REFERENCE'S MEAN, SUBTRACTED FROM THE WHOLE GROUP. If it
    #   were subtracted only from the reference cells, indoor would never be
    #   re-levelled at all.
    def test_the_pin_shifts_the_whole_group(self):
        i = self.src.index("pin = np.zeros(n_cell)")
        block = self.src[i:self.src.index("return dict(vote=", i)]
        self.assertIn("m_ref = m_g & gauge_ref", block)
        self.assertIn("pin[m_g] = np.average(D_pre[use], weights=w_c_[use])",
                      block)

    # ⚠ AN ERA WITH NO OUTDOOR CELLS MUST STILL BE PINNED. An unpinned group
    #   drifts without limit, so the fallback is the whole group, never none.
    def test_an_era_with_no_outdoor_reference_falls_back(self):
        i = self.src.index("m_ref = m_g & gauge_ref")
        block = self.src[i:i + 400]
        self.assertIn("use = m_ref if m_ref.any() else m_g", block)

    def test_the_old_behaviour_is_still_reachable(self):
        self.assertIn('else np.ones(n_cell, bool)', self.src)

    def test_an_unknown_gauge_is_refused(self):
        self.assertIn("raise ValueError(f\"gauge must be one of", self.src)


class TheArithmeticOfTheChange(unittest.TestCase):
    """What the two gauges do to the same numbers, so the intent is pinned as
    arithmetic rather than as a comment.

    ! PURE PYTHON, NO NUMPY. The engine needs numpy; this check does not, and a
      test that only runs where the engine runs is a test that does not run.
    """

    D = (0.01, -0.01, 0.00, 0.02, -0.02, -0.06)   # five outdoor, one indoor low
    IS_INDOOR = (False, False, False, False, False, True)

    def _pin(self, gauge):
        keep = [d for d, ind in zip(self.D, self.IS_INDOOR)
                if gauge == "all" or not ind]
        return sum(keep) / float(len(keep))

    def _shifted(self, gauge):
        p = self._pin(gauge)
        return [d - p for d in self.D]

    @staticmethod
    def _mean(xs):
        return sum(xs) / float(len(xs))

    def test_outdoor_gauge_puts_the_outdoor_mean_at_zero(self):
        out = self._shifted("outdoor")
        self.assertAlmostEqual(self._mean(out[:5]), 0.0, places=12)
        # and indoor keeps its own, real, negative offset
        self.assertLess(out[5], -0.05)

    # ⚠ THE OLD GAUGE CONTAMINATED OUTDOOR. With the combined mean pinned, five
    #   correct outdoor cells are pushed UP by the one low indoor cell -- which
    #   is the 6% x 1.5% smear measured on the real corpus.
    def test_the_old_gauge_pushed_outdoor_off_zero(self):
        old = self._shifted("all")
        self.assertGreater(self._mean(old[:5]), 0.0)
        self.assertAlmostEqual(self._mean(old), 0.0, places=12)

    def test_the_two_differ_by_the_indoor_pull(self):
        self.assertLess(self._pin("all") - self._pin("outdoor"), 0.0)


class TheHoldoutCanScoreBoth(unittest.TestCase):
    """A change to the gauge must be scored, not argued about."""

    def test_the_flag_reaches_every_fit_call(self):
        # ! THE CLAIM, NOT A COUNT (2026-09-19, twice in one day). First this
        #   asserted on "gauge=gauge)" -- with the closing paren -- and broke
        #   when gauge stopped being be.fit's last argument. Then it asserted
        #   exactly two forwarding sites and broke when --sweep-shrinkage added
        #   a third. Both times the CLAIM held and only the arithmetic failed.
        #
        #   So ask the question directly: every call to score() or fitAll() in
        #   this file must forward a gauge, and score() must forward it to
        #   be.fit. A new call site is now covered rather than counted.
        import ast
        src = read("scripts/bracket_holdout.py")
        self.assertIn('ap.add_argument("--gauge"', src)
        tree = ast.parse(src)
        fns = {n.name: n for n in tree.body
               if isinstance(n, ast.FunctionDef)}
        sites = 0
        for host in fns.values():
            for node in ast.walk(host):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "id", "") in ("score", "fitAll")):
                    kws = {k.arg for k in node.keywords}
                    self.assertIn("gauge", kws,
                                  f"{host.name} calls {node.func.id} without "
                                  f"a gauge")
                    sites += 1
        self.assertGreaterEqual(sites, 2, "the call sites vanished")
        for fn in ("score", "fitAll"):
            body = ast.get_source_segment(src, fns[fn])
            self.assertIn("gauge=gauge", body,
                          f"{fn} does not pass its gauge to be.fit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
