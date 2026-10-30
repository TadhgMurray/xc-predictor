"""TFRRS pages are read in the real browser only (owner, 2026-10-30): no
plain-HTTP fetch under a made-up user agent, no curl_cffi Chrome
impersonation, and a challenge page is a block.

    python -m pytest -q tests/test_tfrrs_browser_only.py
"""
import asyncio
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", os.path.join("tfrrs", "scraper")):
    sys.path.insert(0, os.path.join(ROOT, d))

import fetch_tfrrs as F                                          # noqa: E402
from scraper import CloudflareException                          # noqa: E402


def test_no_impersonation_left():
    src = io.open(os.path.join(ROOT, "tfrrs", "scraper", "fetch_tfrrs.py"), encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "curl_cffi" not in code and "impersonate" not in code and "_UA" not in code
    assert "requests.get" not in code


def test_reads_through_the_page_and_a_challenge_is_a_block(monkeypatch):
    calls = []

    async def tier(page, url):
        calls.append(url)
        return page

    monkeypatch.setattr(F, "_tierPlaywright", tier)
    monkeypatch.setattr(F, "_handleVerdict", lambda status, html: html)
    assert asyncio.run(F.fetchTFPage("u1", page=(200, "<html>ok</html>"))) == "<html>ok</html>"
    try:
        asyncio.run(F.fetchTFPage("u2", page=(200, None)))
        raise AssertionError("a challenge page must raise")
    except CloudflareException:
        pass
    try:
        asyncio.run(F.fetchTFPage("u3"))
        raise AssertionError("no page must raise")
    except RuntimeError:
        pass
    assert calls == ["u1", "u2"]
