"""Lighter scraping (owner, 2026-11-10): index pages a third a night, Kailo
only upcoming meets, hand-run scrapers at the nightly's pace, TFRRS re-asks
11 days."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
for _d in ("scripts", "tfrrs/driver"):
    sys.path.insert(0, os.path.join(_ROOT, _d))

from sources import rotate  # noqa: E402


class _Store:
    def __init__(self, metas):
        self.metas = metas

    def meta(self, u):
        return self.metas.get(u)


def test_a_third_of_the_index_pages_a_night_the_oldest_first():
    urls = [f"u{i}" for i in range(9)]
    store = _Store({u: {"status": 200, "fetched_at": i} for i, u in enumerate(urls)})
    assert rotate.due(store, urls, 3) == {"u0", "u1", "u2"}
    # never stored, or failed last time: always due
    store.metas["u8"] = {"status": 500, "fetched_at": 99}
    del store.metas["u7"]
    got = rotate.due(store, urls, 3)
    assert got == {"u7", "u8", "u0", "u1", "u2"}
    assert rotate.due(store, urls, 1) == set(urls)


def test_hand_runs_default_to_the_nightly_pace(monkeypatch):
    import importlib
    for k in ("NUM_SESSIONS", "PER_MEET_DELAY", "TARGET_REQUESTS_PER_SEC_PER_IP"):
        monkeypatch.delenv(k, raising=False)
    import scrape_tuning
    importlib.reload(scrape_tuning)
    assert scrape_tuning.NUM_SESSIONS == 2
    assert scrape_tuning.TARGET_REQUESTS_PER_SEC_PER_IP == 0.5
    assert scrape_tuning.perMeetDelayRange() == (5.0, 10.0)
    src = open(os.path.join(_ROOT, "tfrrs/driver/run_tfrrs.py")).read()
    assert 'os.environ.get("TFRRS_SESSIONS", 1)' in src and '"TFRRS_PER_MEET_DELAY", (10, 20)' in src
    assert 'os.environ.get("TFRRS_RECENT_DAYS", 11)' in open(os.path.join(_ROOT, "tfrrs/driver/launch_tfrrs.py")).read()


def test_kailo_records_only_meets_not_yet_run():
    src = open(os.path.join(_ROOT, "scripts/kailo_compare.py")).read()
    body = src.split("def recordUpcoming")[1].split("\ndef ")[0]
    assert 'r.get("state") in UPCOMING' in body and '>= today' in body


def test_tfrrs_pages_skip_images_fonts_and_video():
    src = open(os.path.join(_ROOT, "tfrrs/driver/launch_tfrrs.py")).read()
    assert '_HEAVY = {"image", "font", "media"}' in src
    assert 'await page.route("**/*", _skipHeavy)' in src
