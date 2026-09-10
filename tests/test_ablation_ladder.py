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


if __name__ == "__main__":
    unittest.main()
