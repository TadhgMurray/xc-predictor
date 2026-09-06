"""One race in the season in progress is trusted when the season before
vouches for it (2026-09-06)."""
import os
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

from grade_sanity import trustByProgression, _advances            # noqa: E402


def _v(grade, level=None):
    return {"grade": grade, "level": level, "method": "corroborated"}


def test_the_new_season_is_vouched_for_by_the_last():
    acad = {(1, 2024): _v("10"), (1, 2025): _v("11")}
    low, vouched = trustByProgression(acad, {(1, 2024): 6, (1, 2025): 1})
    assert acad[(1, 2025)]["trust"] == "high"
    assert acad[(1, 2025)]["trust_by"] == "progression"
    assert (low, vouched) == (0, 1)


def test_a_chain_of_one_race_seasons_that_step_is_trusted_throughout():
    acad = {(1, 2022): _v("9"), (1, 2023): _v("10"), (1, 2024): _v("11"), (1, 2025): _v("12")}
    counts = {(1, 2022): 3, (1, 2023): 1, (1, 2024): 1, (1, 2025): 1}
    trustByProgression(acad, counts)
    assert all(v["trust"] == "high" for v in acad.values())


def test_a_jump_or_a_repeat_is_not_vouched_for():
    acad = {(1, 2024): _v("10"), (1, 2025): _v("10")}          # repeated
    trustByProgression(acad, {(1, 2024): 5, (1, 2025): 1})
    assert acad[(1, 2025)]["trust"] == "low"
    acad = {(1, 2024): _v("9"), (1, 2025): _v("12")}           # a jump
    trustByProgression(acad, {(1, 2024): 5, (1, 2025): 1})
    assert acad[(1, 2025)]["trust"] == "low"
    acad = {(1, 2024): _v("10"), (1, 2025): _v("11")}          # the voucher is itself thin
    trustByProgression(acad, {(1, 2024): 1, (1, 2025): 1})
    assert acad[(1, 2025)]["trust"] == "low"


def test_college_and_pro_seasons_vouch_by_level_and_by_class():
    assert _advances(_v("SO-2", "college"), _v("JR-3", "college"))
    assert _advances(_v("12"), _v("FR-1", "college"))
    assert _advances(_v(None, "college"), _v(None, "college"))
    assert _advances(_v(None, "pro"), _v(None, "pro"))
    assert not _advances(_v("JR-3", "college"), _v("SO-2", "college"))
    assert not _advances(_v("8", "ms"), _v("8", "ms"))
