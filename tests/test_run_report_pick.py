"""run_report: day_scatter keeps the go-live's XC line (the first), not TF's
(engine review 2026-10-24: the last match was stored under an XC name)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import run_report as R                                           # noqa: E402


def test_day_scatter_is_the_xc_line(tmp_path):
    (tmp_path / "08_golive.log").write_text(
        "  day in, leave-self-out   sd 3.10%   robust 2.20%\n"
        "  day in, leave-self-out   sd 4.10%   robust 3.30%\n")
    assert R.readLogMetrics(str(tmp_path))["day_scatter"] == 2.2


def test_every_sentinel_limit_has_an_order():
    for _k, _lab, sql, _b in R.SENTINELS:
        for part in sql.split("LIMIT 1")[:-1]:
            assert "ORDER BY" in part.rsplit("SELECT", 1)[-1], sql
