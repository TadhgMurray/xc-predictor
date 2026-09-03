"""psycopg2 reads every % in a query as a placeholder, SQL comments
included; a comment saying "+18%" took every athlete page down on
2026-09-03. Every % inside get_races' query must be a %(name)s."""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_get_races_query_has_only_named_placeholders():
    src = io.open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    i = src.index("def get_races(")
    body = src[i:src.index("\ndef ", i + 1)]
    stray = [m.group(0) for m in re.finditer(r"%(?!\()", body)]
    assert not stray, stray
