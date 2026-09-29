"""A query of short words only is a prefix (2026-09-29): a trigram index
cannot serve a token under three letters, so "ta" scanned the whole index.

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_search_short_tokens.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "racecast"), os.path.join(ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as A                                               # noqa: E402


def test_short_words_anchor_the_first_at_the_start():
    where, params, _ = A._searchTerms("ta")
    assert params["t0"] == "ta%"
    _, params, _ = A._searchTerms("jo sm")
    assert params["t0"] == "jo%" and params["t1"] == "%sm%"


def test_a_real_word_keeps_substring_matching():
    _, params, _ = A._searchTerms("tad")
    assert params["t0"] == "%tad%"
    _, params, _ = A._searchTerms("mt sac")          # sac is long enough for the index
    assert params["t0"] == "%mt%" and params["t1"] == "%sac%"


def test_the_board_find_box_asks_for_athletes_and_drops_stale_answers():
    js = open(os.path.join(ROOT, "racecast", "static", "rankings.js"), encoding="utf-8").read()
    assert "/search/api?kind=athlete&limit=8&q=" in js
    assert "findAbort.abort()" in js and "ticket !== searchTicket" in js
