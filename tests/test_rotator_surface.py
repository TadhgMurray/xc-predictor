# Project: xc-predictor / tests
# File:    test_rotator_surface.py
# Purpose: both VPN rotators answer everything the launcher calls.
#
# ★ THE SYMPTOM (owner, 2026-09-18): every session died before its first
#   fetch on "'VPNRotatorLinux' object has no attribute 'waitForTunnel'".
#   The launcher does `from vpn_rotation import VPNRotator` and cannot know
#   which platform's class it got, so a method on one and not the other is a
#   crash that only shows up on the platform nobody develops on.
#
# ! PURE AST, so it needs neither psycopg2 nor playwright nor a VPN.
import io
import os
import re
import ast
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _classMembers(src):
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = {
                m.name for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return out


class BothRotatorsCoverTheLauncher(unittest.TestCase):

    # ★ THE WHOLE REPO, NOT JUST THE LAUNCHER. The first miss
    #   (waitForTunnel) was a launcher call; the second (handleStuckSession)
    #   was in scrape_results and a launcher-only scan would not have seen
    #   it. Anything outside vpn_rotation that touches a rotator counts.
    def setUp(self):
        self.classes = _classMembers(_read("scripts", "vpn_rotation.py"))
        self.used = set()
        for folder in ("scripts", "racecast", "engine", "backfill"):
            root = os.path.join(_ROOT, folder)
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    if not fn.endswith(".py") or fn == "vpn_rotation.py":
                        continue
                    with io.open(os.path.join(dirpath, fn),
                                 encoding="utf-8", errors="replace") as fh:
                        src = fh.read()
                    self.used |= set(re.findall(r"\brotator\.(\w+)", src))
                    self.used |= set(re.findall(r"vpn_rotator\.(\w+)", src))

    def test_both_classes_exist(self):
        for name in ("VPNRotatorWindows", "VPNRotatorLinux"):
            self.assertIn(name, self.classes)

    def test_the_scan_found_something(self):
        """A regex that matched nothing would make this file vacuous."""
        self.assertIn("waitForTunnel", self.used)
        self.assertIn("handleStuckSession", self.used)
        self.assertGreater(len(self.used), 3)

    def test_every_method_any_caller_uses_is_on_both(self):
        attrs = {"rotation_count", "rotation_generation", "configs",
                 "disabled"}
        for cls in ("VPNRotatorWindows", "VPNRotatorLinux"):
            for name in sorted(self.used - attrs):
                with self.subTest(cls=cls, method=name):
                    self.assertIn(name, self.classes[cls])

    # ! NOT "THE SURFACES ARE IDENTICAL". recordMeet is a Windows-internal
    #   helper -- Linux's checkRotation does that counting inline -- and
    #   demanding an exact match would force a duplicate method for the sake
    #   of a test. What must hold is that no CALLER can tell the two apart,
    #   which is the assertion above.
    def test_the_shared_contract_is_not_empty(self):
        for cls in ("VPNRotatorWindows", "VPNRotatorLinux"):
            self.assertTrue(self.used & self.classes[cls])


class TheGateIsNeverLeftShut(unittest.TestCase):
    """A rotation that raises must not park every session forever."""

    def test_linux_rotate_reopens_the_gate_in_a_finally(self):
        src = _read("scripts", "vpn_rotation.py")
        i = src.index("class VPNRotatorLinux")
        body = src[i:]
        j = body.index("async def rotate(")
        rot = body[j:body.index("async def _rotateLocked", j)]
        self.assertIn("self.tunnel_ready.clear()", rot)
        self.assertIn("finally:", rot)
        self.assertIn("self.tunnel_ready.set()", rot)
        # the generation advances so sessions stagger exactly once
        self.assertIn("self.rotation_generation += 1", rot)

    def test_no_vpn_never_waits(self):
        src = _read("scripts", "vpn_rotation.py")
        i = src.index("async def waitForTunnel", src.index("class VPNRotatorLinux"))
        body = src[i:i + 600]
        self.assertLess(body.index("self.disabled"), body.index("await"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
