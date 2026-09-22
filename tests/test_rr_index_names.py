"""The ranking_results shadow must build ALL its indexes on every run, not
every other run.

⚠⚠ 2026-09-22: 10_rankings_finish refused to swap, "8 canonical index(es)
   MISSING". Two faults, both here:

   1. THE NAME. A shadow's indexes are named ranking_results_new_*; the swap
      renames only the tables, so the LIVE table then owns those names.
      Index names are unique per schema, so the next build's
      `CREATE INDEX IF NOT EXISTS ranking_results_new_rr_person_idx` found the
      live table's index and did nothing (0.0s in the log), and the serial
      retry derived the same name and did nothing again.
   2. THE SIGNATURE. pg_indexes writes a partial index's predicate back in
      parentheses; _CANONICAL_INDEXES states it bare. rr_board_mark_idx
      built in 62s and was still reported missing.

   python -m unittest tests.test_rr_index_names

   The cycle test needs a scratch Postgres: XCP_TEST_PG_HOST (a socket dir
   or host), XCP_TEST_PG_PORT, XCP_TEST_PG_DB. It creates and drops
   ranking_results / athlete_season there -- NEVER point it at the site's
   database. Without the variables it is skipped.
"""
import os
import re
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PG = os.environ.get("XCP_TEST_PG_HOST")

if _PG:
    os.environ.update(XCP_DB_HOST=_PG,
                      XCP_DB_PORT=os.environ.get("XCP_TEST_PG_PORT", "5432"),
                      XCP_DB_NAME=os.environ.get("XCP_TEST_PG_DB", "xcp_test"),
                      XCP_DB_USER=os.environ.get("XCP_TEST_PG_USER", "postgres"),
                      XCP_DB_PASSWORD=os.environ.get("XCP_TEST_PG_PASSWORD", "x"),
                      XCP_DB_QUIET="1")
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, d))
if not _PG:
    for name in ("database", "psycopg2", "psycopg2.extras", "psycopg2.errors"):
        sys.modules.setdefault(name, types.ModuleType(name))
    _db = sys.modules["database"]
    for fn in ("getConn", "dbSetting", "dbJobs", "dbQuiet"):
        if not hasattr(_db, fn):
            setattr(_db, fn, (lambda *a, **k: 1) if fn == "dbJobs"
                    else (lambda *a, **k: None))
    sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
    sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]
    for exc in ("UndefinedColumn", "LockNotAvailable", "DeadlockDetected"):
        if not hasattr(sys.modules["psycopg2.errors"], exc):
            setattr(sys.modules["psycopg2.errors"], exc,
                    type(exc, (Exception,), {}))
    if not hasattr(sys.modules["psycopg2"], "Error"):
        sys.modules["psycopg2"].Error = Exception

import build_ranking_results as B                                # noqa: E402


def _pgRendering(cols):
    """What pg_indexes.indexdef says for a canonical (cols) spec."""
    m = re.match(r"(.*?)\s+WHERE\s+(.*)$", cols, re.S)
    body = f"({m.group(2)})" if m else ""
    head = m.group(1) if m else cols
    return (f"CREATE INDEX x ON public.t USING btree {head}"
            + (f" WHERE {body}" if body else ""))


class Signature(unittest.TestCase):

    def test_every_canonical_index_matches_its_catalogue_rendering(self):
        for table, lst in B._CANONICAL_INDEXES.items():
            for name, cols in lst:
                self.assertEqual(B._indexSig(cols),
                                 B._indexSig(_pgRendering(cols)),
                                 f"{table}.{name} would never verify")

    def test_the_partial_board_mark_index_is_one_of_them(self):
        spec = dict(B._CANONICAL_INDEXES["ranking_results"])["rr_board_mark_idx"]
        self.assertIn("WHERE", spec)


@unittest.skipUnless(_PG, "set XCP_TEST_PG_HOST to a scratch Postgres")
class BuildSwapCycle(unittest.TestCase):
    """Three build -> swap cycles, starting from the server's 2026-09-22
    state: a live table whose indexes already carry the shadow's names."""

    KW = {"desc", "include", "asc", "is", "not", "null", "where", "and", "or",
          "true", "false"}

    def _cols(self, like):
        words = {w for _n, c in B._CANONICAL_INDEXES.get(like, [])
                 for w in re.findall(r"[a-z_]+", c.lower())} - self.KW
        return ", ".join(f"{w} text" for w in sorted(words | {"x"}))

    def _exec(self, conn, sql):
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    def _names(self, conn, t):
        with conn.cursor() as cur:
            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename=%s",
                        (t,))
            out = [r[0] for r in cur.fetchall()]
        conn.commit()
        return out

    def _cycle(self, like, shadow, stale_name):
        from database import getConn
        with getConn() as conn:
            self._exec(conn, f"DROP TABLE IF EXISTS {shadow}, {like} CASCADE; "
                             f"CREATE TABLE {like} ({self._cols(like)})")
            for n, c in B._CANONICAL_INDEXES[like]:
                self._exec(conn, f"CREATE INDEX {stale_name(n)} ON {like} {c}")
            for run in (1, 2, 3):
                self._exec(conn, f"CREATE TABLE {shadow} ({self._cols(like)})")
                B.buildIndexes(conn, shadow, like)       # raises if short
                self._exec(conn, f"ALTER TABLE {like} RENAME TO {like}_old; "
                                 f"ALTER TABLE {shadow} RENAME TO {like}; "
                                 f"DROP TABLE {like}_old")
                B._canonicaliseIndexNames(conn, like)
                live = self._names(conn, like)
                self.assertEqual(len(live), len(B._CANONICAL_INDEXES[like]),
                                 f"run {run}: {live}")
                self.assertFalse([n for n in live if f"{like}_new" in n],
                                 f"run {run}: shadow names left live: {live}")
            self._exec(conn, f"DROP TABLE {like}")

    def test_ranking_results(self):
        self._cycle("ranking_results", "ranking_results_new",
                    lambda n: f"ranking_results_new_{n}")

    def test_athlete_season(self):
        self._cycle("athlete_season", "athlete_season_new",
                    lambda n: f"idx_athlete_season_new_new_new_{n}"[:63])


if __name__ == "__main__":
    unittest.main()
