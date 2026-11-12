"""engine/speed_ratings._runCached (2026-11-12): the club tables are built
once per pipeline run -- the first step saves them in the run's folder and
the later steps read the same object; a failed build is never saved; with
no XCP_CLUB_CACHE (a hand run, the nightly) every call builds."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import speed_ratings as sr                                       # noqa: E402


def test_built_once_then_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XCP_CLUB_CACHE", str(tmp_path / ".club_cache"))
    calls = []

    def build():
        calls.append(1)
        return {(1, 2025), (2, 2024)}, True
    assert sr._runCached("club_majority", build) == {(1, 2025), (2, 2024)}
    assert sr._runCached("club_majority", build) == {(1, 2025), (2, 2024)}
    assert len(calls) == 1


def test_a_failed_build_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("XCP_CLUB_CACHE", str(tmp_path / ".club_cache"))
    assert sr._runCached("club_pros", lambda: (({}, {}), False)) == ({}, {})
    assert not list(tmp_path.iterdir())
    assert sr._runCached("club_pros", lambda: (({1: 2}, {}), True)) == ({1: 2}, {})


def test_off_without_the_run_folder(monkeypatch):
    monkeypatch.delenv("XCP_CLUB_CACHE", raising=False)
    calls = []
    for _ in range(2):
        sr._runCached("club_pros", lambda: (calls.append(1) or ({}, {}), True))
    assert len(calls) == 2
