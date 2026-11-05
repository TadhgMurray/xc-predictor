"""The board's school state follows the row's pool (owner, 2026-11-05:
"Katrina Coogan, Georgetown TX", "Olivia Markezich, Notre Dame WI" on the
college women's board -- the names' biggest clusters are high schools)."""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_board_asks_teamState_with_the_rows_pool():
    src = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    i = src.index("def api_rankings")
    j = src.index('stampCrests(rows, state_key="school_state")', i)
    block = src[i:j]
    assert "school_identity.teamState(_r.get(\"school\"), _pool" in block
    assert "primaryState(_r.get(\"school\"))" in block          # the fallback stays
