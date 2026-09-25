"""The tfrrs row linker's minted ids (owner, 2026-09-25). Pure.

    python -m pytest -q tests/test_link_tfrrs_rows.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import link_tfrrs_rows as T                                  # noqa: E402


def test_minted_ids_fit_any_integer_column_and_never_overlap():
    top = max(T.MINT_BASE.values()) + T.MINT_MAX_NATIVE
    assert top < 2 ** 31                       # an int4 person_id column holds it
    lo, hi = sorted(T.MINT_BASE.values())
    assert lo + T.MINT_MAX_NATIVE <= hi        # tfrrs and DA ranges never meet
    assert lo > 100_000_000                    # far above any anet id (~33M)


def test_xc_rows_are_tfrrs_ids_and_track_rows_say_which_system():
    assert T._sys("XC") == "'tfrrs'"
    assert T._sys("TF", "r") == "COALESCE(r.id_system, 'tfrrs')"
