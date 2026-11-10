# Project: xc-predictor / tests
# File:    test_scraper_user_agent.py
# Purpose: every scraper and outbound fetch says who we are, and nothing
#          hides that a scraper is one (owner, 2026-11-10; CLAUDE.md).
#
#   python -m pytest -q tests/test_scraper_user_agent.py
#
# Checked across the code, not per file: a new scraper that sets its own
# User-Agent without the address, or brings back a navigator.webdriver mask,
# a stealth plugin or IP rotation, fails here.
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ADDRESS = "racecast@racecast.co"
DIRS = ("sources", "scripts", "tfrrs", "backfill", "engine", "racecast", "model", "tools")

# owner's decision (2026-11-06): Kailo agreed to requests with no product
# name; it is a comparison against their API, not a scrape of results
EXEMPT = {os.path.join("scripts", "kailo_compare.py")}

# a User-Agent given in code: a header literal, a context's user_agent=, or a
# *UA / USER_AGENT constant (its whole statement, which may run over lines)
_HEADER = re.compile(r"""["']User-Agent["']\s*:\s*(["'][^"'\n]*["'])""")
_KWARG = re.compile(r"""\buser_agent\s*=\s*(["'][^"'\n]*["'])""")
_CONST = re.compile(r"""^\s*_?[A-Z_]*(?:UA|USER_AGENT)\s*=\s*(\(?\s*f?["'].*?["']\s*\)?)\s*$""",
                    re.M | re.S)


def _sources():
    for d in DIRS:
        for root, _dirs, files in os.walk(os.path.join(_ROOT, d)):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    rel = os.path.relpath(path, _ROOT)
                    if rel not in EXEMPT:
                        with open(path, encoding="utf-8") as fh:
                            yield rel, fh.read()


def _constants(text):
    """Each UA constant's full value text, following a parenthesised
    statement over its lines."""
    out = []
    for m in re.finditer(r"^\s*_?[A-Z_]*(?:UA|USER_AGENT)\s*=\s*(.*)$", text, re.M):
        start = m.start(1)
        if text[start] == "(":
            depth, i = 0, start
            while i < len(text):
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                if depth == 0:
                    break
                i += 1
            out.append(text[start:i + 1])
        else:
            out.append(m.group(1))
    return out


def test_the_shared_agent_names_us():
    from sources import agent
    assert ADDRESS in agent.USER_AGENT
    ua = agent.browserUserAgent("142.0.7444.59")
    assert ADDRESS in ua and "Chrome/142.0.7444.59" in ua


def test_an_override_without_the_address_is_not_used(monkeypatch):
    import importlib
    from sources import agent
    monkeypatch.setenv("XCP_SOURCE_UA", "Mozilla/5.0 (Windows NT 10.0) Chrome/141.0.0.0")
    assert ADDRESS in importlib.reload(agent).USER_AGENT
    monkeypatch.setenv("XCP_SOURCE_UA", f"racecast-test ({ADDRESS})")
    assert importlib.reload(agent).USER_AGENT == f"racecast-test ({ADDRESS})"
    monkeypatch.delenv("XCP_SOURCE_UA")
    importlib.reload(agent)


def test_the_timers_fetcher_sends_it():
    from sources import agent, fetch
    assert fetch.USER_AGENT == agent.USER_AGENT


def test_every_user_agent_in_the_code_gives_the_address():
    bad = []
    for rel, text in _sources():
        for rx in (_HEADER, _KWARG):
            for m in rx.finditer(text):
                if ADDRESS not in m.group(1):
                    bad.append(f"{rel}: {m.group(0)[:90]}")
        for value in _constants(text):
            # a constant read from elsewhere (another module, a function)
            # is checked where it is written
            if re.match(r"""\(?\s*f?["']""", value) and ADDRESS not in value:
                bad.append(f"{rel}: {value[:90]}")
    assert not bad, "user agents without the address:\n  " + "\n  ".join(bad)


def test_both_browser_launchers_set_ours():
    for rel in (os.path.join("scripts", "launcher.py"),
                os.path.join("tfrrs", "driver", "launch_tfrrs.py")):
        with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
            assert "browserUserAgent(browser.version)" in fh.read(), rel


def test_nothing_hides_that_a_scraper_is_one():
    bad = []
    for rel, text in _sources():
        if re.search(r"navigator\s*,\s*['\"]webdriver['\"]", text):
            bad.append(f"{rel}: navigator.webdriver mask")
        if re.search(r"^\s*(from|import)\s+playwright_stealth", text, re.M):
            bad.append(f"{rel}: playwright_stealth")
    with open(os.path.join(_ROOT, "requirements-scrape.txt"), encoding="utf-8") as fh:
        if "playwright-stealth" in fh.read():
            bad.append("requirements-scrape.txt: playwright-stealth")
    assert not bad, bad
