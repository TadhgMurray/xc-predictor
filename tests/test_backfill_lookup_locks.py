"""The backfill's lookups must not hold their locks through the stream.

⚠⚠ 2026-09-22 and 2026-09-24: 05_backfill_xc could not swap `results` in,
   twice. The holder the swap finally named was the pipeline itself --
   `xcp-pipeline idle in transaction xact 1816s FETCH FORWARD 50000 FROM
   "backfill_stream_tf"`. The TF backfill's lookups read `results` (the
   wheelchair list spans both sports), ran on the read connection, and that
   transaction stayed open through the 30-minute TF stream.

   Run for real: _runBackfill with the lookups, the row function, the stream
   and the merge stubbed, and two fake connections that record what is done
   to them. The read connection must COMMIT after the lookups and before the
   stream opens -- commit, because a rollback would drop the TEMP table the
   lookups created.

    python -m pytest -q tests/test_backfill_lookup_locks.py
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: {}
    sys.modules["corrections"] = _c

# ! THE REAL database MODULE, NOT ANOTHER TEST'S STAND-IN (2026-09-26). In a
#   whole-suite run test_accounts.py has already put a two-function stub in
#   sys.modules["database"], and backfill_normalize now also imports dbJobs
#   and dbSetting from it. Load the real one for this import, then put the
#   stub back so the tests after this one see the world as they left it.
_stub = sys.modules.get("database")
if _stub is not None and not hasattr(_stub, "dbJobs"):
    del sys.modules["database"]
import backfill_normalize as B                                  # noqa: E402
if _stub is not None:
    sys.modules["database"] = _stub


class _Conn:
    def __init__(self, name, log, pid):
        self.name, self.log, self.pid = name, log, pid

    def get_backend_pid(self):
        return self.pid

    def commit(self):
        self.log.append(f"{self.name}.commit")

    def rollback(self):
        self.log.append(f"{self.name}.rollback")


def test_the_lookup_transaction_ends_before_the_stream_opens(monkeypatch):
    log = []
    read, write = _Conn("read", log, 1), _Conn("write", log, 2)

    def lookups(conn, cfg):
        log.append(f"lookups on {conn.name}")
        return (None,) * 11

    monkeypatch.setattr(B, "_buildLookups", lookups)
    monkeypatch.setattr(B, "_makeRowFn", lambda *a, **k: None)
    monkeypatch.setattr(B, "_openStream",
                        lambda conn, cfg: log.append(f"stream on {conn.name}"))
    monkeypatch.setattr(B, "_drainStream",
                        lambda *a, **k: (0, 0, 0, {}))
    monkeypatch.setattr(B, "_closeStreamQuietly", lambda s: None)
    cfg = types.SimpleNamespace(sport="TF", table="results_tf",
                                write_mode="update")
    try:
        B._runBackfill(read, write, cfg, apply=False, limit=None)
    except Exception:                    # whatever follows the drain is not
        pass                             # what this test is about
    i = log.index("lookups on read")
    j = log.index("stream on read")
    assert "read.commit" in log[i:j], log
    assert "read.rollback" not in log[i:j], log     # would drop TEMP _wcp


def test_a_blocked_swap_cannot_roll_back_the_rebuilt_table(monkeypatch):
    """! 2026-09-25: "relation results_tf_new does not exist" -- the rebuilt
    heap and indexes sat uncommitted in the connection's open transaction,
    and the swap retry's ROLLBACK after a lock timeout took them with it."""
    import psycopg2.errors
    log = []

    class Conn:
        def commit(self):
            log.append("COMMIT(conn)")

    class Cur:
        connection = Conn()

        def execute(self, sql, params=None):
            log.append(sql.split()[0] if sql.split() else sql)

    tries = {"n": 0}

    def body(cur):
        tries["n"] += 1
        if tries["n"] == 1:
            raise psycopg2.errors.LockNotAvailable()

    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(B, "_lockHolders", lambda cur, t: [])
    B._swapWithRetry(Cur(), body, "results_tf swap", table="results_tf")
    first_rollback = log.index("ROLLBACK")
    assert "COMMIT(conn)" in log[:first_rollback]      # the rebuild was kept
    assert log.index("COMMIT(conn)") < log.index("BEGIN")
    assert tries["n"] == 2 and log[-1] == "COMMIT"
