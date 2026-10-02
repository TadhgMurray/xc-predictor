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


def test_redefined_canonical_index_is_built_once_with_new_columns(monkeypatch):
    # 2026-10-02: the live table kept as_board_mean_idx WITHOUT NULLS LAST
    # under the canonical name; old and new both reached the shadow under one
    # name and step 10 failed on pg_class_relname_nsp_index
    import build_ranking_results as B
    monkeypatch.setattr(B, "_CANONICAL_INDEXES", {"athlete_season": [
        ("as_person_idx", "(person_id)"),
        ("as_board_mean_idx", "(pool, sport, year, mean_rating DESC NULLS LAST, person_id)")]})
    rows = [("athlete_season_as_board_mean_idx",
             "CREATE INDEX athlete_season_as_board_mean_idx ON public.athlete_season "
             "USING btree (pool, sport, year, mean_rating DESC, person_id)"),
            ("athlete_season_as_person_idx",
             "CREATE INDEX athlete_season_as_person_idx ON public.athlete_season "
             "USING btree (person_id)")]
    defs = B.indexDefs(Conn(rows), "athlete_season")
    boards = [d for n, d in defs if n.endswith("as_board_mean_idx")]
    assert len(boards) == 1 and "NULLS LAST" in boards[0]
    # an unchanged canonical index is kept from the catalogue, not doubled
    assert [n for n, _ in defs].count("athlete_season_as_person_idx") == 1
    assert len(defs) == 2
