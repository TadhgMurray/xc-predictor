# The race summary calls the winner by surname, never by a suffix (owner,
# 2026-10-28: "III won a second straight title here").
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

from race_story import surname                                 # noqa: E402


def test_suffixes_are_not_surnames():
    assert surname("Robert Jazwinski III") == "Jazwinski"
    assert surname("Mason Hill Jr.") == "Hill"
    assert surname("Evan Noonan") == "Noonan"
    assert surname("Ali") == "Ali"
    assert surname("Smith Jr.") == "Smith Jr."
