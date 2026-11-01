"""The nightly scrapes local sources before athletic.net, and probes little
(owner, 2026-11-01)."""
import os
import re

SH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "nightly_update.sh")


def _src():
    with open(SH, encoding="utf-8") as f:
        return f.read()


def test_local_runs_before_anet():
    s = _src()
    assert s.index("step 01_local") < s.index("scrape scrape_anet")


def test_short_probes():
    s = _src()
    assert re.search(r'TFRRS_SEED_AHEAD="\$\{XCP_NIGHTLY_TFRRS_AHEAD:-50\}"', s)
    assert re.search(r'TFRRS_DRY_BLOCKS="\$\{XCP_NIGHTLY_TFRRS_DRY_BLOCKS:-1\}"', s)
    assert re.search(r'DRY_BLOCKS_TO_STOP="\$\{XCP_NIGHTLY_DRY_BLOCKS:-1\}"', s)
