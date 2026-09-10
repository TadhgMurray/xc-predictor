# Project: xc-predictor / tests
# File:    test_report_steps_report_only.py
# Purpose: The two evidence steps must print a number and write nothing --
#          and their summary line must survive per-group variance arrays.
#
# ★★ THE TWO FAILURES THIS EXISTS FOR, both from one pipeline run (run21).
#
#    1. sigma_u2 and tau2 became PER-GROUP ARRAYS when XC and TF got their
#       own race-day and course spreads (a shared floor was letting the TF
#       race day eat XC course difficulty). Three call sites were updated;
#       the summary print in run_joint.main() was missed, so after a 3073s
#       solve the step died on
#           TypeError: unsupported format string passed to
#           numpy.ndarray.__format__
#       and 08a_holdout produced no number at all.
#
#    2. `--holdout` scores the held-out races and THEN solves the full
#       model on the sample and writes it over joint_difficulty.npz -- the
#       file explain_joint_row and the other diagnostics read. A report
#       step that overwrites the thing it reports on is a trap, and it cost
#       this step half its wall clock for a solve nobody reads.
import ast
import io
import os
import re
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# the summary block ends with the collapse alarm, so exec'ing it needs `js`
import joint_solve as _js

_RUN_JOINT = os.path.join(_ROOT, "engine", "run_joint.py")
_LADDER = os.path.join(_ROOT, "scripts", "ablation_ladder.py")
_PIPELINE = os.path.join(_ROOT, "deploy", "run_pipeline.sh")


class VarianceComponentsAreArrays(unittest.TestCase):
    """⚠ THE ONE THAT COST THE RUN. Nothing anywhere may format
    sigma_u2/tau2 (or their square roots) with a scalar format spec."""

    # ! engine/pair_engine.py IS EXEMPT ON PURPOSE. That is the LEGACY
    #   pair engine, whose tau2 is a genuine scalar -- one method-of-moments
    #   estimate over every cell, no groups. Its `:.5f` is correct.
    _EXEMPT = ("engine/pair_engine.py",)

    def test_no_scalar_format_of_the_per_group_components(self):
        bad = []
        for root, _dirs, files in os.walk(_ROOT):
            if any(p in root for p in (".git", "node_modules", "__pycache__")):
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                if os.path.relpath(path, _ROOT).replace(os.sep, "/") \
                        in self._EXEMPT:
                    continue
                for i, line in enumerate(io.open(path, encoding="utf-8",
                                                 errors="replace"), 1):
                    # sqrt(<anything>sigma_u2<anything>):.5f -- a scalar
                    # spec applied to something derived from the array
                    for name in ("sigma_u2", "tau2"):
                        if name not in line:
                            continue
                        if re.search(r"\{[^{}]*\b" + name
                                     + r"\b[^{}]*\}?[^{}]*:\s*[.,+\-# 0-9]*[eEfgG]\}",
                                     line):
                            # indexed per group -- sigma_u2[g] -- is fine
                            if re.search(r"\b" + name + r"\b\s*\[", line):
                                continue
                            bad.append(f"{os.path.relpath(path, _ROOT)}:{i}: "
                                       f"{line.strip()}")
        self.assertEqual(bad, [], "per-group variance formatted as a scalar:\n"
                                  + "\n".join(bad))

    def test_the_summary_line_prints_one_row_per_group(self):
        """Run the exact block from run_joint.main() against a two-group
        `out` and require both groups to appear."""
        src = io.open(_RUN_JOINT, encoding="utf-8").read()
        start = src.index('print(f"\\n[joint] sigma {np.sqrt(out[\'sigma2\'])')
        end = src.index("n_downweighted", start)
        end = src.rindex("print(", start, end)
        block = src[start:end]
        # the block is indented inside main(); dedent it
        block = "\n".join(ln[4:] if ln.startswith("    ") else ln
                          for ln in block.split("\n"))
        out = {"sigma2": 0.0025,
               "sigma_u2": np.array([0.0016, 0.0036]),
               "tau2": np.array([0.001325, 0.000259])}
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            exec(compile(block, "<summary>", "exec"),
                 {"np": np, "out": out, "js": _js})
        finally:
            sys.stdout = old
        text = buf.getvalue()
        self.assertIn("XC", text)
        self.assertIn("TF", text)
        self.assertIn("0.04000", text)      # sqrt(0.0016), XC race day
        self.assertIn("0.06000", text)      # sqrt(0.0036), TF race day
        self.assertIn("0.03640", text)      # sqrt(0.001325), XC tau
        self.assertIn("0.01609", text)      # sqrt(0.000259), TF tau

    def test_a_scalar_still_prints(self):
        """A one-group solve (or an older npz) must not crash the report."""
        src = io.open(_RUN_JOINT, encoding="utf-8").read()
        start = src.index('print(f"\\n[joint] sigma {np.sqrt(out[\'sigma2\'])')
        end = src.index("n_downweighted", start)
        end = src.rindex("print(", start, end)
        block = "\n".join(ln[4:] if ln.startswith("    ") else ln
                          for ln in src[start:end].split("\n"))
        out = {"sigma2": 0.0025, "sigma_u2": 0.0016, "tau2": 0.001325}
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            exec(compile(block, "<summary>", "exec"),
                 {"np": np, "out": out, "js": _js})
        finally:
            sys.stdout = old
        self.assertIn("0.04000", buf.getvalue())


class HoldoutOnlyWritesNothing(unittest.TestCase):
    def test_the_flag_exists(self):
        import run_joint as rj
        args = rj.buildParser().parse_args(["--holdout-only"])
        self.assertTrue(args.holdout_only)

    def test_it_returns_before_the_solve_and_before_any_write(self):
        """Read main() and require the --holdout-only return to come
        BEFORE solveJoint and before np.savez."""
        tree = ast.parse(io.open(_RUN_JOINT, encoding="utf-8").read())
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        ret = solve = save = None
        for node in ast.walk(main):
            if isinstance(node, ast.Return) and ret is None:
                # the return guarded by holdout_only
                pass
            if isinstance(node, ast.If):
                test = ast.dump(node.test)
                if "holdout_only" in test and any(
                        isinstance(b, ast.Return) for b in node.body):
                    ret = node.lineno
            if isinstance(node, ast.Call):
                fn = ast.dump(node.func)
                if "solveJoint" in fn and solve is None:
                    solve = node.lineno
                if "savez" in fn and save is None:
                    save = node.lineno
        self.assertIsNotNone(ret, "no `if args.holdout_only: return` in main()")
        self.assertIsNotNone(solve)
        self.assertIsNotNone(save)
        self.assertLess(ret, solve, "--holdout-only still runs the full solve")
        self.assertLess(ret, save, "--holdout-only still writes the npz")

    def test_the_holdout_runs_before_that_return(self):
        src = io.open(_RUN_JOINT, encoding="utf-8").read()
        self.assertLess(src.index("holdout(cols, keep, args"),
                        src.index('"[joint] --holdout-only'),
                        "--holdout-only returns before scoring anything")


class TheEvidenceStepsUseIt(unittest.TestCase):
    """★ Both report steps, not one. They are the only two callers that
    pass --sample-pct, and a sampled solve must never reach disk."""

    def test_pipeline_step_08a(self):
        src = io.open(_PIPELINE, encoding="utf-8").read()
        i = src.index("step 08a_holdout")
        body = src[i:src.index("step 08b_ladder", i)]
        self.assertIn("--holdout-only", body)
        self.assertNotIn(" --holdout ", body)

    def test_every_ladder_rung(self):
        src = io.open(_LADDER, encoding="utf-8").read()
        i = src.index("def runRung")
        body = src[i:src.index("\ndef ", i + 1)]
        self.assertIn('"--holdout-only"', body)
        self.assertNotIn('"--holdout"', body)


if __name__ == "__main__":
    unittest.main()
