"""TFRRS and the local fetcher keep the athletic.net launcher's privacy
settings (owner, 2026-11-01)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_local_fetcher_sends_plain_chrome():
    from sources import fetch
    assert fetch.USER_AGENT.startswith("Mozilla/5.0") and "racecast" not in fetch.USER_AGENT


def test_tfrrs_pages_hide_webdriver():
    src = open(os.path.join(ROOT, "tfrrs", "driver", "launch_tfrrs.py"), encoding="utf-8").read()
    assert "navigator, 'webdriver'" in src
    assert src.count("add_init_script(WEBDRIVER_MASK)") == 2       # sync and async pages
    assert "browser.new_page()" not in src
