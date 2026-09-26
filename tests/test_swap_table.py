"""database.swapTable: a rebuilt table goes live under a short lock, and a
busy table is retried rather than waited on for ever (sweep, 2026-09-26)."""
import os
import sys
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = open(os.path.join(_ROOT, "scripts", "database.py")).read()


def _load():
    # the helper alone: database.py imports the server config at import time
    i = _SRC.index("def swapTable(")
    j = _SRC.index("\ndef _liveColumns")
    ns = {"time": time}
    exec(_SRC[i:j], ns)
    return ns["swapTable"]


try:
    from psycopg2 import errors as pgerr
    _HAVE = True
except ImportError:
    _HAVE = False


class FakeConn:
    def __init__(self, busy):
        self.busy, self.sql, self.commits, self.rollbacks = busy, [], 0, 0

    def cursor(self):
        conn = self

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                conn.sql.append(sql)
                if sql.startswith("DROP") and conn.busy:
                    conn.busy -= 1
                    raise pgerr.LockNotAvailable("busy")

            def fetchall(self):
                return [("t_new_pkey",)]

            def fetchone(self):
                return (None,)
        return C()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@unittest.skipUnless(_HAVE, "psycopg2 not installed")
class Swap(unittest.TestCase):
    def test_retries_a_busy_table_then_swaps(self):
        conn = FakeConn(busy=2)
        n = _load()(conn, "t", "t_new", pause=0, verbose=False)
        self.assertEqual(n, 3)
        self.assertEqual(conn.rollbacks, 2)
        self.assertIn("SET LOCAL lock_timeout = '3000ms'", conn.sql)
        self.assertIn('ALTER INDEX "t_new_pkey" RENAME TO "t_pkey"', conn.sql)

    def test_gives_up_and_says_so(self):
        conn = FakeConn(busy=99)
        with self.assertRaises(RuntimeError):
            _load()(conn, "t", "t_new", tries=3, pause=0, verbose=False)

    def test_no_bare_drop_left_at_the_four_sites(self):
        for f, table in (("engine/twin_flag.py", "result_twin"),
                         ("engine/joint_golive.py", "race_day_effect"),
                         ("engine/person_gender.py", "person_gender"),
                         ("engine/build_team_pool.py", "team_pool")):
            src = open(os.path.join(_ROOT, f)).read()
            self.assertIn(f'swapTable(conn, "{table}"', src, f)
            self.assertNotIn(f"TRUNCATE {table}\"", src, f)


if __name__ == "__main__":
    unittest.main()
