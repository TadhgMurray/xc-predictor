"""
drop_old.py -- clear results_old / results_tf_old before a heap-rebuild merge.

WHY THIS EXISTS
    saveResultSpeedRatings rebuilds results by writing a new table and swapping
    it in, leaving <table>_old behind as the undo copy. It REFUSES to start if
    one is already present -- deliberately, so the swap cannot fail after the
    rebuild has already burned ~14 minutes.

★ THIS HAS TO RUN INSIDE THE CHAIN, NOT BEFORE IT. The ALS engine's own final
  phase does the same rebuild, so it RECREATES both _old tables. Dropping them
  ahead of time accomplishes nothing; the drop belongs immediately before
  pair_golive.

⚠ WHAT YOU GIVE UP. results_old is the rollback for whatever last rebuilt the
  table -- here, the weather backfill. After this, undoing the weather change
  means re-running the backfill from the .prebake pickle. pair_golive takes its
  OWN backup into results_speed_rating_backup first, so the go-live itself
  stays reversible.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT,
           os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_TABLES = ("results_old", "results_tf_old")


def main():
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            for table in _TABLES:
                cur.execute("SELECT to_regclass(%s)", (table,))
                if cur.fetchone()[0] is None:
                    print(f"[drop] {table}: absent, nothing to do")
                    continue
                cur.execute(f"SELECT count(*) FROM {table}")
                n = cur.fetchone()[0]
                cur.execute(f"DROP TABLE {table}")
                print(f"[drop] {table}: dropped ({n:,} rows)")
        conn.commit()
    print("[drop] done -- pair_golive can now rebuild")


if __name__ == "__main__":
    main()