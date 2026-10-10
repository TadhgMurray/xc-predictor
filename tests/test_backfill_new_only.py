"""--new-only (the nightly light update): only rows nobody has normalised,
from last season on, and never a full-write record."""
import datetime
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: (  # not dunders: see test_model_one_scale
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c
# the real database module, not another test's stand-in (see
# test_backfill_merge_speed.py)
_stub = sys.modules.get("database")
if _stub is not None and not hasattr(_stub, "dbJobs"):
    del sys.modules["database"]
import backfill_normalize as bn  # noqa: E402
if _stub is not None:
    sys.modules["database"] = _stub


class _Cfg:
    has_event = True
    has_school = False
    table = "results_tf"
    sport = "TF"


def test_since_is_the_start_of_last_season():
    assert bn.newOnlySince(datetime.date(2026, 10, 4)) == "2025-08-01"
    assert bn.newOnlySince(datetime.date(2026, 3, 1)) == "2024-08-01"
    assert bn.newOnlySince(datetime.date(2026, 8, 1)) == "2025-08-01"


def test_stream_filters_only_when_on():
    plain = bn._streamSQL(_Cfg())
    assert "normalized_time IS NULL" not in plain
    bn._NEW_ONLY.update(on=True, since="2025-08-01")
    try:
        sql = bn._streamSQL(_Cfg(), age_band=True, rating_pool=True)
    finally:
        bn._NEW_ONLY.update(on=False, since=None)
    assert "WHERE r.normalized_time IS NULL" in sql
    assert "r.date >= '2025-08-01'" in sql
    # the WHERE follows the joins, not the SELECT list
    assert sql.index("LEFT JOIN age_band_result") < sql.index("WHERE r.normalized_time")
