# Project: xc-predictor / tests
# File:    test_maintenance_flag_expires.py
# Purpose: A maintenance flag left behind by a killed process must not take
#          the site down forever.
#
# ★ WHY. siteMaintenance removes the flag in a `finally`, which covers an
#   exception and does NOT cover SIGKILL. Killing a pipeline step whose
#   query will not die is routine on this box, so the flag DOES get left
#   behind -- and the old app.py check was os.path.exists, which then
#   answers 503 to every visitor and every crawler until a human deletes
#   the file by hand. Days of 503 is how a site falls out of an index.
import os
import sys
import time
import unittest

sys.path.insert(0, "engine")


class MaintenanceFlagExpires(unittest.TestCase):
    def setUp(self):
        self.path = f"/tmp/xcp-maint-test-{os.getpid()}"
        os.environ["XCP_MAINTENANCE_FLAG"] = self.path
        os.environ["XCP_MAINTENANCE_MAX_S"] = "60"
        for mod in [m for m in sys.modules if m == "maintenance"]:
            del sys.modules[mod]
        import maintenance
        self.m = maintenance

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
        os.environ.pop("XCP_MAINTENANCE_FLAG", None)
        os.environ.pop("XCP_MAINTENANCE_MAX_S", None)
        sys.modules.pop("maintenance", None)

    def test_no_flag_is_not_maintenance(self):
        self.assertFalse(self.m.isMaintenance())
        self.assertIsNone(self.m.flagAge())

    def test_inside_the_block_is_maintenance(self):
        with self.m.siteMaintenance("swap ranking_results"):
            self.assertTrue(self.m.isMaintenance())
            self.assertTrue(os.path.exists(self.path))
        self.assertFalse(self.m.isMaintenance())
        self.assertFalse(os.path.exists(self.path))

    def test_flag_survives_an_exception_but_the_block_still_clears_it(self):
        with self.assertRaises(RuntimeError):
            with self.m.siteMaintenance("swap"):
                raise RuntimeError("swap blew up")
        self.assertFalse(os.path.exists(self.path))

    def test_a_flag_from_a_killed_process_expires(self):
        # what SIGKILL leaves behind: the file, with nobody to remove it
        with open(self.path, "w") as f:
            f.write("swap ranking_results")
        old = time.time() - 3600
        os.utime(self.path, (old, old))
        self.assertTrue(os.path.exists(self.path))     # still there...
        self.assertFalse(self.m.isMaintenance())       # ...but not honoured
        self.assertGreater(self.m.flagAge(), 60)

    def test_a_long_but_live_hold_stays_honoured(self):
        # merge_column's siege can hold the flag ~11 min; it heartbeats
        with open(self.path, "w") as f:
            f.write("swap")
        old = time.time() - 3600
        os.utime(self.path, (old, old))
        self.assertFalse(self.m.isMaintenance())
        self.m.heartbeat()
        self.assertTrue(self.m.isMaintenance())

    def test_clear_stale_removes_debris_and_leaves_a_fresh_flag_alone(self):
        with open(self.path, "w") as f:
            f.write("swap")
        old = time.time() - 3600
        os.utime(self.path, (old, old))
        age = self.m.clearStale()
        self.assertIsNotNone(age)
        self.assertGreater(age, 60)
        self.assertFalse(os.path.exists(self.path))

        with self.m.siteMaintenance("swap"):
            self.assertIsNone(self.m.clearStale())     # a live swap is safe
            self.assertTrue(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
