# Project: xc-predictor / tests
# File:    test_chain_invocations.py
# Purpose: every python invocation inside the overnight chain scripts is one
#          the target script will actually accept.
#
# ⚠ WHY THIS FILE EXISTS. Three separate times the chain "ran" a step that
#   did nothing, and every time the chain reported success:
#     * launcher.py --retry-failed  -- launcher.py has no argparse at all, so
#       the flag was silently ignored and nothing was retried.
#     * link_tfrrs_to_anet.py with no --write -- it computed 4,882 links,
#       printed a verdict, and threw them away.
#     * build_team_identity.py / build_team_pool.py with no flags at all --
#       both ap.error("pass --dry-run or --write") and exited 2, under
#       `|| true`, so the chain kept going.
#   All three are the same bug: the chain's idea of a script's CLI drifting
#   from the script's actual CLI. This test reads both, statically, and makes
#   them agree.
#
#   python -m unittest tests.test_chain_invocations
import os
import re
import shlex
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The chain scripts this guards. Add a new chain script here and it is
# covered; that is the point.
CHAINS = (
    "scripts/overnight_fit_pool_solve.sh",
    "scripts/overnight_logos.sh",
)

# A `step`/`step_fatal` line, i.e. how the chains run everything.
_STEP = re.compile(r'^\s*step(?:_fatal)?\s+(\S+)\s+(.*)$')
_ENVVAR = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def _joinContinuations(text):
    r"""Fold backslash-continued lines, so a multi-line step call is one line."""
    out, buf = [], ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return out


# `${VAR:+--flag "$VAR"}` -- an optional flag. The flag inside it still has to
# be one the target accepts, so the braces are stripped and the body kept.
_OPTIONAL = re.compile(r'\$\{[A-Za-z_][A-Za-z0-9_]*:\+([^}]*)\}')


def _invocations(text):
    """(label, script, flags) for every step line that runs a .py file."""
    out = []
    for line in _joinContinuations(text):
        line = _OPTIONAL.sub(r'\1', line)
        m = _STEP.match(line)
        if not m:
            continue
        label, rest = m.group(1), m.group(2)
        # Strip the trailing `|| true` and any comment; keep it lexable.
        rest = rest.split("||")[0].split("#")[0].strip()
        try:
            toks = shlex.split(rest)
        except ValueError:
            continue
        # Drop leading VAR=value env prefixes and the interpreter itself.
        while toks and (_ENVVAR.match(toks[0]) or toks[0] in ("$PY", "python",
                                                             "python3")):
            toks.pop(0)
        if not toks or not toks[0].endswith(".py"):
            continue
        out.append((label, toks[0], [t for t in toks[1:]]))
    return out


def _sourceOf(script):
    path = os.path.join(_ROOT, script)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _knownFlags(src):
    """Long flags the script's argparse declares, or None if it has none."""
    if "add_argument" not in src:
        return None
    return set(re.findall(r'add_argument\(\s*["\'](--[A-Za-z0-9][\w-]*)', src))


def _requiredModes(src):
    """Flag sets a script demands one of, from ap.error("pass --a or --b")."""
    need = []
    for msg in re.findall(r'\.error\(\s*["\']([^"\']+)["\']', src):
        flags = re.findall(r'--[A-Za-z0-9][\w-]*', msg)
        if len(flags) >= 2 and msg.lstrip().lower().startswith("pass "):
            need.append(set(flags))
    return need


class ChainInvocations(unittest.TestCase):

    def setUp(self):
        self.calls = []
        for chain in CHAINS:
            src = _sourceOf(chain)
            self.assertIsNotNone(src, "%s is gone" % chain)
            for label, script, flags in _invocations(src):
                self.calls.append((chain, label, script, flags))
        # If the parser ever stops matching, every assertion below passes
        # vacuously -- which is exactly how this class of bug survives.
        self.assertGreaterEqual(len(self.calls), 6,
                                "step-line parser matched almost nothing; "
                                "the chains' syntax changed")

    def test_target_exists(self):
        for chain, label, script, _ in self.calls:
            self.assertTrue(os.path.exists(os.path.join(_ROOT, script)),
                            "%s step %s runs missing %s"
                            % (chain, label, script))

    def test_flags_are_recognised(self):
        for chain, label, script, flags in self.calls:
            src = _sourceOf(script)
            known = _knownFlags(src)
            passed = [f.split("=")[0] for f in flags if f.startswith("--")]
            if known is None:
                # launcher.py's case: no parser, so a flag is a silent no-op.
                self.assertEqual(passed, [],
                                 "%s step %s passes %s but %s has no "
                                 "argparse -- the flag is ignored"
                                 % (chain, label, passed, script))
                continue
            for f in passed:
                self.assertIn(f, known,
                              "%s step %s passes %s, which %s does not accept"
                              % (chain, label, f, script))

    def test_mode_flag_supplied(self):
        for chain, label, script, flags in self.calls:
            src = _sourceOf(script)
            for need in _requiredModes(src):
                self.assertTrue(need & set(flags),
                                "%s step %s runs %s with %s, but it requires "
                                "one of %s and exits 2 without it"
                                % (chain, label, script, flags or "no flags",
                                   sorted(need)))


if __name__ == "__main__":
    unittest.main()
