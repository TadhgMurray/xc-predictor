"""database.py's start-up migrations ask the catalogue before they ALTER
(2026-10-26: an unconditional ADD COLUMN IF NOT EXISTS on results queued
behind an 18-hour reader and every page queued behind it)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import database as D                                             # noqa: E402


class _Cur:
    def __init__(self, have_cols, have_idx):
        self.have_cols, self.have_idx, self.sql = have_cols, have_idx, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self._last = (sql, params)

    def fetchone(self):
        sql, p = self._last
        if "information_schema.columns" in sql:
            return (1,) if (p[0], p[1]) in self.have_cols else None
        if "to_regclass" in sql:
            return ("x",) if p[0].split(".", 1)[1] in self.have_idx else (None,)
        return None


def test_nothing_is_altered_when_the_columns_are_there():
    cur = _Cur({(t, c) for t in ("results", "results_tf") for c in ("team_slug", "status")},
               {"idx_results_team_slug", "idx_results_tf_team_slug"})
    D._migrateResultsAddTeamSlug(cur)
    D._migrateResultsAddStatus(cur)
    assert not any("ALTER TABLE" in s or "CREATE INDEX" in s for s, _ in cur.sql)


def test_a_missing_column_is_added_with_a_lock_timeout():
    cur = _Cur(set(), set())
    D._migrateResultsAddTeamSlug(cur)
    stmts = [s for s, _ in cur.sql]
    alters = [i for i, s in enumerate(stmts) if s.startswith("ALTER TABLE")]
    assert len(alters) == 2
    assert all("lock_timeout" in stmts[i - 1] for i in alters)
