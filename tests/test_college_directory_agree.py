"""A name the directory matches several times answers when every match
agrees (owner, 2026-10-15: "Oregon" -> OR, not Oregon High School's WI).

    python -m pytest -q tests/test_college_directory_agree.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import build_college_directory as D                              # noqa: E402


def test_candidates_that_agree_answer():
    assert D._pick([("OR", "OR"), ("OR", "OR"), ("OR", "OR")], None, None) == "OR"


def test_candidates_that_disagree_still_miss():
    assert D._pick([("NY", "NY"), ("CT", "CT")], None, None) is None
    # a state in the name still filters, a raced state still breaks a tie
    assert D._pick([("NY", "NY"), ("CT", "CT")], "CT", None) == "CT"
    assert D._pick([("NY", "NY"), ("CT", "CT")], None, "NY") == "NY"


def test_oregon_by_token_is_oregon():
    # no exact "oregon" key: the token step finds three, all in OR
    entries = {"oregon state": [("OR", "OR")],
               "southern oregon": [("OR", "OR")], "western oregon": [("OR", "OR")]}
    D._ANSWERS.clear()
    D._POSTINGS.clear()
    assert D.lookup(entries, "Oregon") == "OR"
    # a name worn by colleges in two states is still a miss
    two = {"trinity university": [("TX", "TX")], "trinity college": [("CT", "CT")]}
    assert D.lookup(two, "Trinity") is None
