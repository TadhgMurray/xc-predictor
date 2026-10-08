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


def test_a_skipped_year_is_vouched_for_by_the_last_trusted_season():
    acad = {(1, 2023): _v("9"), (1, 2025): _v("11")}           # no 2024 at all
    trustByProgression(acad, {(1, 2023): 4, (1, 2025): 1})
    assert acad[(1, 2025)]["trust"] == "high"
    acad = {(1, 2023): _v("9"), (1, 2025): _v("10")}           # one step in two years
    trustByProgression(acad, {(1, 2023): 4, (1, 2025): 1})
    assert acad[(1, 2025)]["trust"] == "low"


def test_a_thin_contradicting_season_between_does_not_break_the_chain():
    acad = {(1, 2023): _v("9"), (1, 2024): _v("12"), (1, 2025): _v("11")}
    trustByProgression(acad, {(1, 2023): 4, (1, 2024): 1, (1, 2025): 1})
    assert acad[(1, 2024)]["trust"] == "low"
    assert acad[(1, 2025)]["trust"] == "high"


def test_a_first_season_has_nothing_to_vouch_for_it():
    acad = {(1, 2025): _v("9")}
    trustByProgression(acad, {(1, 2025): 1})
    assert acad[(1, 2025)]["trust"] == "low"


def test_the_open_season_trusts_one_race_unless_it_contradicts():
    """Owner, 2026-10-08: "a lot of the rated not ranked rules fire bcs they
    haven't run enough races, on basically everybody"."""
    acad = {
        (1, 2026): _v("9"),                                  # first season ever
        (2, 2026): _v(None, "hs"),                           # field verdict, no grade
        (3, 2025): _v("10"), (3, 2026): _v("10"),            # repeats a grade
        (4, 2025): _v(None, "ms"), (4, 2026): _v(None, "college"),  # skips a level
        (5, 2025): _v(None, "hs"), (5, 2026): _v(None, "college"),  # one step up
        (6, 2024): _v("9"),                                  # closed year: old rule
    }
    counts = {(1, 2026): 1, (2, 2026): 1, (3, 2025): 4, (3, 2026): 1,
              (4, 2025): 3, (4, 2026): 1, (5, 2025): 3, (5, 2026): 1,
              (6, 2024): 1}
    trustByProgression(acad, counts, open_ay=2026)
    t = {k: v["trust"] for k, v in acad.items()}
    assert t[(1, 2026)] == "high" and acad[(1, 2026)]["trust_by"] == "season_open"
    assert t[(2, 2026)] == "high"
    assert t[(3, 2026)] == "low"          # a repeated grade still contradicts
    assert t[(4, 2026)] == "low"          # ms straight to college: Colussi
    assert t[(5, 2026)] == "high"
    assert t[(6, 2024)] == "low"          # a closed year keeps the two-race rule
