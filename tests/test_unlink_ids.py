"""unlink.py numbers split people above every minted range (2026-09-29): max()+1
fell inside the DirectAthletics mint range, where a later mint could reuse it."""
import os
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def test_split_base_is_above_every_mint():
    import link_tfrrs_rows as L
    src = open(os.path.join(_ROOT, "engine", "unlink.py")).read()
    base = int(re.search(r"SPLIT_BASE = ([0-9_]+)", src).group(1).replace("_", ""))
    top = max(L.MINT_BASE.values()) + L.MINT_MAX_NATIVE
    assert base >= top, (base, top)
    assert "WHERE person_id >= %(b)s" in src and "max(int(cur.fetchone()[0]) + 1, SPLIT_BASE)" in src
