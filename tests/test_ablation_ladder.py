# Project: xc-predictor / tests
# File:    test_ablation_ladder.py
# Purpose: Every rung of the ladder must actually change the model.
#
# ★★ THE FAILURE THIS EXISTS FOR, caught while writing the ladder. The
#    "free-tau" rung passed `--tau-max ,` intending to REMOVE the difficulty
#    prior caps. That parses to an empty dict, which fell through to
#    js.TAU_MAX_DEFAULT -- so the rung ran the SHIPPED model, scored the
#    same as the baseline, and the table would have reported "removing the
#    caps changes nothing" when the caps had never been removed.
#
#    A ladder whose rungs silently do nothing is worse than no ladder: it
#    produces confident conclusions from a control group. So every rung's
#    flags are parsed through run_joint's own parser here and compared
#    against the baseline configuration.
import ast
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _rungs():
    """Read LADDER out of the script without importing it (importing pulls
    in the whole engine)."""
    src = open(os.path.join(_ROOT, "scripts", "ablation_ladder.py")).read()
    tree = ast.parse(src)
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", None) == "LADDER"):
            return ast.literal_eval(node.value)
    raise AssertionError("LADDER not found")


class LadderRungs(unittest.TestCase):
    def test_the_names_are_unique(self):
        names = [r[0] for r in _rungs()]
        self.assertEqual(len(names), len(set(names)), names)

    def test_base_is_first(self):
        self.assertEqual(_rungs()[0][0], "base")

    def test_every_rung_changes_something(self):
        """⚠ THE ONE THAT MATTERS. Parse each rung's flags with run_joint's
        own parser and require the resulting solve configuration to differ
        from the baseline's. A rung that parses to the baseline is a control
        group masquerading as a treatment."""
        import run_joint as rj

        def config(flags):
            # ! run_joint's parser applies its implications (--merge-sports
            #   turning off the sport offset, --tau-max none clearing the
            #   caps) inside main(), so the raw namespace is not enough --
            #   compare the kwargs solveJoint would actually receive.
            # ⚠ THE WHOLE NAMESPACE, not just the solve kwargs. Some rungs
            #   change the DESIGN (--no-race-term, --no-curve, --altitude)
            #   rather than a solve argument, and comparing only the kwargs
            #   let --no-race-effect through as a no-op: it keeps the term
            #   out of the RATING while the solve fits it either way.
            p = rj.buildParser()
            args = rj.applyImplications(p.parse_args(list(flags)), p)
            return {k: repr(v) for k, v in vars(args).items()
                    if k not in ("pack", "out")}

        base = config(_rungs()[0][1])
        for name, flags, _why in _rungs():
            if name == "base":
                continue
            cfg = config(flags)
            diff = {k for k in base if base[k] != cfg.get(k)}
            self.assertTrue(
                diff,
                f"rung {name!r} with flags {flags} produces the SAME solve "
                f"configuration as base -- it is a no-op and the ladder "
                f"would report it as a result")

    def test_free_tau_really_frees_tau(self):
        """The specific bug, pinned: 'none' must clear the caps, and an
        empty 'XC,TF' must be rejected rather than silently ignored."""
        import run_joint as rj
        p = rj.buildParser()
        args = p.parse_args(["--tau-max", "none"])
        rj.applyImplications(args, p)
        self.assertIsNone(rj.solveKwargs(args, None, verbose=False)["tau_max"])

        with self.assertRaises(SystemExit):
            a2 = p.parse_args(["--tau-max", ","])
            rj.applyImplications(a2, p)

    def test_the_default_is_the_measured_caps(self):
        import run_joint as rj
        import joint_solve as js
        p = rj.buildParser()
        args = p.parse_args([])
        rj.applyImplications(args, p)
        self.assertEqual(rj.solveKwargs(args, None, verbose=False)["tau_max"],
                         "default")
        self.assertIn(0, js.TAU_MAX_DEFAULT)
        self.assertIn(1, js.TAU_MAX_DEFAULT)


class VenueDayKey(unittest.TestCase):
    """★ --race-key venue pools the day effect across the distances raced at
    one venue on one day. The cell keeps its own difficulty -- a venue can
    host genuinely different courses, so d must stay per cell; only u moves."""

    def test_distances_at_one_venue_share_a_day(self):
        import numpy as np
        import run_joint as rj
        keys = np.array(["XC:Morley:d4700", "XC:Morley:d4800",
                         "XC:Morley:d5000", "XC:Mt. SAC:d4828"])
        voc = rj.venueOfCell(keys)
        self.assertEqual(len(set(voc[:3].tolist())), 1, "Morley did not pool")
        self.assertNotEqual(voc[0], voc[3], "two venues collapsed into one")

    def test_a_different_day_is_a_different_race(self):
        import numpy as np
        import run_joint as rj
        keys = np.array(["XC:Morley:d4700", "XC:Morley:d5000"])
        voc = rj.venueOfCell(keys)
        course = np.array([0, 1, 0, 1])
        day = np.array([10, 10, 11, 11])
        race, n = rj.raceCodes(course, day, voc)
        self.assertEqual(n, 2, f"expected two days, got {n}")
        self.assertEqual(race[0], race[1])
        self.assertEqual(race[2], race[3])
        self.assertNotEqual(race[0], race[2])

    def test_tf_keys_carry_no_distance_and_are_unchanged(self):
        import numpy as np
        import run_joint as rj
        keys = np.array(["TF:loc:77:out", "TF:loc:88:in"])
        voc = rj.venueOfCell(keys)
        self.assertEqual(len(set(voc.tolist())), 2,
                         "two track venues collapsed")

    def test_a_venue_whose_name_contains_d_digits_is_not_split(self):
        """⚠ rpartition(':d') on a name like 'XC:Camp d2000 Ranch:d5000' must
        take the LAST one. Getting this wrong silently splits a venue."""
        import numpy as np
        import run_joint as rj
        keys = np.array(["XC:Camp d2000 Ranch:d5000",
                         "XC:Camp d2000 Ranch:d4800"])
        voc = rj.venueOfCell(keys)
        self.assertEqual(voc[0], voc[1], "the venue was split on its name")

    def test_the_default_is_still_the_cell(self):
        import run_joint as rj
        p = rj.buildParser()
        self.assertEqual(p.parse_args([]).race_key, "cell")


if __name__ == "__main__":
    unittest.main()


class CoreSet(unittest.TestCase):
    """The default ladder is the core set, every name of which is a rung."""

    def test_core_names_are_rungs_and_base_leads(self):
        src = open(os.path.join(_ROOT, "scripts", "ablation_ladder.py")).read()
        tree = ast.parse(src)
        core = None
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", None) == "CORE"):
                core = ast.literal_eval(node.value)
        self.assertIsNotNone(core)
        names = [r[0] for r in _rungs()]
        self.assertTrue(set(core) <= set(names), set(core) - set(names))
        self.assertEqual(core[0], "base")
        self.assertLess(len(core), len(names))
