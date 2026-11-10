# Project: xc-predictor
# File:    sources/agent.py
# Purpose: The one User-Agent every scraper and outbound fetch sends.
#
# ★ EVERY SCRAPER NAMES US AND GIVES racecast@racecast.co (owner, 2026-11-10,
#   and CLAUDE.md's standing rule). Before this the timers, TFRRS and
#   athletic.net all went out as a plain Chrome with no name and no address,
#   and the two browser launchers hid navigator.webdriver and varied their
#   window size "to look like a unique machine". All of that is gone: a site
#   that wants us to stop can see who we are and write to us, and a bot
#   challenge, a rate limit or a login wall is reported, never worked around.
#
# ! A BROWSER KEEPS ITS OWN CHROME STRING AND ADDS OURS. athletic.net and
#   TFRRS answer only a real browser (their pages run the scripts that make
#   the calls), so the launchers still drive Chrome -- under its true
#   version and platform, with our name and address appended.
import os
import sys

CONTACT = "racecast@racecast.co"
PRODUCT = f"racecast-results/1.0 (+https://racecast.co; {CONTACT})"


def _fromEnv():
    """XCP_SOURCE_UA, when it still gives the address; else ours."""
    ua = (os.environ.get("XCP_SOURCE_UA") or "").strip()
    return ua if CONTACT in ua else PRODUCT


USER_AGENT = _fromEnv()


def _platform():
    if sys.platform.startswith("win"):
        return "Windows NT 10.0; Win64; x64"
    if sys.platform == "darwin":
        return "Macintosh; Intel Mac OS X 10_15_7"
    return "X11; Linux x86_64"


def browserUserAgent(version):
    """The launched Chrome's own user agent (its real version, this
    machine's platform) with ours after it. version: Browser.version."""
    v = (version or "").strip() or "0"
    return (f"Mozilla/5.0 ({_platform()}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{v} Safari/537.36 {PRODUCT}")
