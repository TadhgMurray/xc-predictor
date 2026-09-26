"""build_ranking_results.indexDefs builds one tree per column list, keeps
UNIQUE and non-btree indexes apart (2026-09-26: rr_result_idx beside
idx_rr_result were both rebuilt every run)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")


class Cur:
    def __init__(self, rows): self.rows = rows
    def execute(self, sql, params=None): pass
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Conn:
    def __init__(self, rows): self.rows = rows
    def cursor(self): return Cur(self.rows)


def test_duplicates_are_built_once(monkeypatch):
    import build_ranking_results as B
    monkeypatch.setattr(B, "_CANONICAL_INDEXES", {})
    rows = [("rr_result_idx", "CREATE INDEX rr_result_idx ON public.rr USING btree (result_id)"),
            ("idx_rr_result", "CREATE INDEX idx_rr_result ON public.rr USING btree (result_id)"),
            ("rr_pkey", "CREATE UNIQUE INDEX rr_pkey ON public.rr USING btree (result_id)"),
            ("rr_name_trgm", "CREATE INDEX rr_name_trgm ON public.rr USING gin (name gin_trgm_ops)"),
            ("rr_name", "CREATE INDEX rr_name ON public.rr USING btree (name)")]
    names = [n for n, _ in B.indexDefs(Conn(rows), "rr")]
    assert names == ["rr_result_idx", "rr_pkey", "rr_name_trgm", "rr_name"]
