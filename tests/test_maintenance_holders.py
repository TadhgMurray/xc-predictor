# Project: xc-predictor / tests
# File:    test_maintenance_holders.py
# Purpose: The maintenance flag stays up while ANY live swap still holds it.
#
# ★ WHY (2026-10-25). Two swaps in steps that run side by side can both fall
#   back to the flag. With one file and no count, the first to finish removed
#   it while the second was still holding the lock, and the site served pages
#   into a locked table. Each hold now registers itself; the last live one
#   takes the flag down, and a holder whose process died does not count.
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "engine")
sys.path.insert(0, ENGINE)


class MaintenanceHolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "flag")
        os.environ["XCP_MAINTENANCE_FLAG"] = self.path
        os.environ["XCP_MAINTENANCE_MAX_S"] = "60"
        sys.modules.pop("maintenance", None)
        import maintenance
        self.m = maintenance

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("XCP_MAINTENANCE_FLAG", None)
        os.environ.pop("XCP_MAINTENANCE_MAX_S", None)
        sys.modules.pop("maintenance", None)

    def test_the_inner_hold_does_not_take_down_the_outer(self):
        with self.m.siteMaintenance("swap a"):
            with self.m.siteMaintenance("swap b"):
                self.assertTrue(self.m.isMaintenance())
            self.assertTrue(self.m.isMaintenance())      # a still holds it
        self.assertFalse(self.m.isMaintenance())
        self.assertEqual(self.m.liveHolders(), [])

    def test_another_process_still_holding_keeps_the_flag(self):
        code = ("import sys, time; sys.path.insert(0, %r); import maintenance\n"
                "with maintenance.siteMaintenance('other step'):\n"
                "    print('held', flush=True); time.sleep(3)\n") % ENGINE
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                 env=dict(os.environ), text=True)
        self.assertEqual(child.stdout.readline().strip(), "held")
        with self.m.siteMaintenance("this step"):
            pass
        self.assertTrue(self.m.isMaintenance())          # the child's hold is live
        child.wait(timeout=20)
        self.assertFalse(self.m.isMaintenance())         # it took the flag down

    def test_a_dead_holder_does_not_hold_the_site_down(self):
        os.makedirs(self.path + ".d", exist_ok=True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        with open(os.path.join(self.path + ".d", f"{dead.pid}-1"), "w") as f:
            f.write("killed swap")
        with self.m.siteMaintenance("swap"):
            self.assertTrue(self.m.isMaintenance())
        self.assertFalse(self.m.isMaintenance())
        self.assertEqual(os.listdir(self.path + ".d"), [])

    def test_clear_stale_leaves_a_flag_a_live_hold_refreshed(self):
        with self.m.siteMaintenance("long swap"):
            old = time.time() - 600
            os.utime(self.path, (old, old))              # the flag looks old...
            self.assertIsNone(self.m.clearStale(now=time.time() + 30))
            self.assertTrue(os.path.exists(self.path))   # ...but its holder lives


if __name__ == "__main__":
    unittest.main()
