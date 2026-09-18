# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 6/4/2026
# File Title: launcher.py
# Purpose: Launches 25 parallel browsing sessions that all scrape
# the results for each XC/TF meet in meet_queue. Detects whether
# each meet is XC or TF at scrape time, rather than trusting queue's
# sport tag (which is wrong).

import sys
import asyncio
import random
import platform
import os

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from database import (
    initPool, closePool, createTables,
    countRows, markScraped, getQueueStatus,
    resetInProgress, getBatchUnscrapedMeets
)
from scrape_results import scrapeMeetBySport, scrapeMeetTFMetaOnly
from scraper import CloudflareException
from vpn_rotation import VPNRotator
from async_db_helper import runDbCall
from scrape_tuning import NUM_SESSIONS, perMeetDelayRange, perRequestDelayRange
 
# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
 
# Chrome path differs by OS. We must use real Chrome (not Playwright's bundled
# Chromium) because Cloudflare fingerprints the binary — Chromium gets blocked.
# headless=False is also required for the same reason.
# ! SEARCHED, AND CHROME_PATH IN THE ENVIRONMENT WINS. One hardcoded path
#   per OS meant a box where Chrome sits anywhere else died inside
#   playwright with "executable doesn't exist at /usr/bin/google-chrome" --
#   a Playwright traceback for what is an apt-get.
_CHROME_CANDIDATES = {
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "Linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
}


def _chromePath():
    """The real Chrome binary.

    ⚠ REAL CHROME, NOT PLAYWRIGHT'S CHROMIUM. Cloudflare fingerprints the
      binary and blocks the bundled build, which is also why headless=False
      below is not optional. The chromium entries are last and are a
      last-resort fallback, not an equivalent: expect blocks on them.
    """
    override = os.environ.get("CHROME_PATH")
    if override:
        return override
    system = platform.system()
    if system not in _CHROME_CANDIDATES:
        raise RuntimeError(f"Unsupported OS: {system}")
    for path in _CHROME_CANDIDATES[system]:
        if os.path.exists(path):
            return path
    looked = "\n  ".join(_CHROME_CANDIDATES[system])
    raise RuntimeError(
        "Google Chrome not found. Looked in:\n  " + looked + "\n"
        "Install it (Debian/Ubuntu: apt-get install -y "
        "./google-chrome-stable_current_amd64.deb) or set CHROME_PATH. "
        "Playwright's bundled chromium is NOT a substitute -- Cloudflare "
        "fingerprints the binary and blocks it.")


# ! RESOLVED AT LAUNCH, NOT AT IMPORT. _chromePath() raises when Chrome is
#   absent, and raising at import time would break every module that merely
#   imports this one (the tests do) on any machine without Chrome.
CHROME_PATH = (os.environ.get("CHROME_PATH")
               or next((p for p in _CHROME_CANDIDATES.get(
                        platform.system(), []) if os.path.exists(p)), None))

STAGGER_SECONDS = 8

# Tells scraper to only grab meet metadata, not results.
# Necessary for meet metadata backfill.
META_ONLY = False

# How many meets to scrape before restarting the browser.
# Restarts prevent WebSocket connection collapse on long sessions and
# cycle to a fresh proxy IP.
RESTART_EVERY = 200

# How long a session waits after its post-rotation reload comes back 429, before
# rejoining normal scraping. We wait-then-rejoin — we do NOT retry the reload.
_RELOAD_429_COOLDOWN_SECONDS = 60

# Per-reload navigation timeout. Generous: a fresh IP mid-CF-challenge can take a
# few seconds to return the document.
_RELOAD_TIMEOUT_MS = 30000

# How many times to retry a meet after a block-triggered rotation
# before giving up and moving on. 3 retries means we try the meet
# on up to 4 different IPs total before skipping it.
MAX_BLOCK_RETRIES = 3

# NEW — the full meet_id range to sweep. Each session gets a
# non-overlapping slice of this range (see _computeSessionRange below).
#
# ⚠ DEAD, AND IT LOOKED LIKE A CEILING (owner, 2026-09-18: "I think maxing
#   at 670k may be our issue"). runSession does NOT walk an id range: its
#   loop calls getBatchUnscrapedMeets, which claims meet_queue rows with
#   scraped=0 and source='anet'. Nothing in the session loop ever compares a
#   meet_id to either of these. They survive only because
#   _computeSessionRange still takes them, and the startup line that printed
#   "starts at N, step S, up to 670000" described an arrangement the scraper
#   stopped using -- which is worse than no line at all.
#
#   The real frontier is per sport and lives in the data:
#   queue_anet_new.watermark() takes max(meet_id) with results, and the
#   forward walk goes up from there. XC and TF are separate id spaces (owner
#   confirmed), so each has its own.
_UNUSED_SCAN_START_ID = 1
_UNUSED_SCAN_END_ID   = 670000

# Where checkpoint files live. One file per session index, e.g.
# checkpoints/session_0.txt, checkpoints/session_1.txt, etc.
CHECKPOINT_DIR = "checkpoints"

# How long to wait for a POLITE browser.close() before giving up and killing the
# OS process. A healthy close is well under a second; if it hasn't finished in
# 10s the WebSocket is wedged and waiting longer just freezes the session.
_BROWSER_CLOSE_TIMEOUT_SECONDS = 10

# How long to wait after a block when the rotation turned out to be a no-op
# (we're inside the rotator's 90s cooldown). Short — just enough to not tight-loop
# against an IP that's still settling, WITHOUT paying for a browser restart.
_BLOCK_NOOP_BACKOFF_SECONDS = 60

# Static residential proxy list from WebShare.
# Format: (host, port, username, password)
# Each session gets assigned a proxy from this list based on its index.
# On browser restart, cycles to the next proxy in the list.
PROXIES = [
    ("45.45.203.194", "8195", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("94.154.170.185", "6107", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("94.154.170.29", "5951", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.155.37", "6568", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("208.66.72.230", "5879", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("107.180.180.204", "5253", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.143.206", "7029", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("72.1.182.103", "5900", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("63.246.132.166", "5484", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.171.112", "7613", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("23.229.21.244", "8466", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.171.59", "7560", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("207.228.6.254", "7986", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("72.1.129.102", "7495", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("168.235.150.7", "5291", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.146.40", "7863", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("69.30.72.204", "5260", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("163.123.201.30", "5815", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("130.180.238.218", "5600", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.146.208", "8031", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("130.180.239.244", "6883", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("163.123.203.234", "8337", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("185.52.136.233", "8932", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.45.203.111", "8112", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.130.159", "6948", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.136.77", "8509", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("9.249.19.212", "7146", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("130.180.239.203", "6842", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.143.209", "7032", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.176.62", "7640", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("130.180.252.187", "8887", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("72.1.152.87", "5979", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("207.228.19.17", "5385", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.131.195", "7307", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("23.229.21.155", "8377", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("63.246.132.149", "5467", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("216.98.252.39", "5769", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("207.228.33.124", "8836", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("72.1.133.182", "7574", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("192.53.141.50", "5438", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("216.98.228.137", "5838", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("69.30.72.140", "5196", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("9.142.31.68", "5226", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("23.229.85.111", "5623", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("207.228.33.24", "8736", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("72.1.178.138", "7032", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("168.158.185.250", "6517", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("216.98.253.243", "6286", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("45.56.130.206", "6995", "jmceylbxstaticresidential", "vqldxl956dvv"),
    ("207.228.33.55", "8767", "jmceylbxstaticresidential", "vqldxl956dvv"),
]

# List of 3 dictionaries, one for each sessions. Each has slightly different
# chrome versions and window sizes to make each session look like a unique
# machine to cloudflare.
SESSION_CONFIGS = [
    {
        "label": "[Session 1]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800}
    },
    {
        "label": "[Session 2]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768}
    },
    {
        "label": "[Session 3]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 900}
    },
    {
        "label": "[Session 4]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1080}
    },
    {
        "label": "[Session 5]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "viewport": {"width": 1536, "height": 864}
    },
    {
        "label": "[Session 6]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "viewport": {"width": 1600, "height": 900}
    },
    {
        "label": "[Session 7]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720}
    },
    {
        "label": "[Session 8]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 810}
    },
    {
        "label": "[Session 9]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 900}
    },
    {
        "label": "[Session 10]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 960}
    },
    {
        "label": "[Session 11]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "viewport": {"width": 1680, "height": 1050}
    },
    {
        "label": "[Session 12]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 1024}
    },
    {
        "label": "[Session 13]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "viewport": {"width": 1600, "height": 1024}
    },
    {
        "label": "[Session 14]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1200}
    },
    {
        "label": "[Session 15]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 960}
    },
    {
        "label": "[Session 16]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800}
    },
    {
        "label": "[Session 17]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768}
    },
    {
        "label": "[Session 18]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 900}
    },
    {
        "label": "[Session 19]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1080}
    },
    {
        "label": "[Session 20]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "viewport": {"width": 1536, "height": 864}
    },
    {
        "label": "[Session 21]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "viewport": {"width": 1600, "height": 900}
    },
    {
        "label": "[Session 22]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720}
    },
    {
        "label": "[Session 23]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 810}
    },
    {
        "label": "[Session 24]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 900}
    },
    {
        "label": "[Session 25]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "viewport": {"width": 1680, "height": 1050}
    },
        {
        "label": "[Session 26]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800}
    },
    {
        "label": "[Session 27]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768}
    },
    {
        "label": "[Session 28]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 900}
    },
    {
        "label": "[Session 29]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1080}
    },
    {
        "label": "[Session 30]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "viewport": {"width": 1536, "height": 864}
    },
    {
        "label": "[Session 31]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "viewport": {"width": 1600, "height": 900}
    },
    {
        "label": "[Session 32]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720}
    },
    {
        "label": "[Session 33]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 810}
    },
    {
        "label": "[Session 34]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 900}
    },
    {
        "label": "[Session 35]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 960}
    },
    {
        "label": "[Session 36]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "viewport": {"width": 1680, "height": 1050}
    },
    {
        "label": "[Session 37]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 1024}
    },
    {
        "label": "[Session 38]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
        "viewport": {"width": 1600, "height": 1024}
    },
    {
        "label": "[Session 39]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1200}
    },
    {
        "label": "[Session 40]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 960}
    },
    {
        "label": "[Session 41]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800}
    },
    {
        "label": "[Session 42]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768}
    },
    {
        "label": "[Session 43]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 900}
    },
    {
        "label": "[Session 44]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1080}
    },
    {
        "label": "[Session 45]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36",
        "viewport": {"width": 1536, "height": 864}
    },
    {
        "label": "[Session 46]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
        "viewport": {"width": 1600, "height": 900}
    },
    {
        "label": "[Session 47]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720}
    },
    {
        "label": "[Session 48]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 810}
    },
    {
        "label": "[Session 49]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 900}
    },
    {
        "label": "[Session 50]",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 960}
    },
    {"label": "[Session 51]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 52]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 53]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 54]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 55]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 56]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 57]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 58]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 59]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 60]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 61]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 62]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 63]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 64]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 65]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 66]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 67]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 68]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 69]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 70]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 71]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 72]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 73]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 74]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 75]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 76]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 77]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 78]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 79]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 80]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 81]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 82]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 83]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 84]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 85]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 86]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 87]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 88]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 89]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 90]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 91]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 92]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 93]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 94]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 95]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 96]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 97]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 98]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 99]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 100]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 101]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 102]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 103]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 104]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 105]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 106]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 107]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 108]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 109]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 110]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 111]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 112]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 113]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 114]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 115]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 116]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 117]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 118]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 119]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 120]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 121]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 122]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 123]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 124]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 125]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 126]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 127]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 128]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 129]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 130]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 131]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 132]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 133]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 134]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 135]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 136]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 137]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 138]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 139]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 140]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 141]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 142]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 143]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 144]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 145]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 146]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 147]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 148]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 149]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 150]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 151]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 152]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 153]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 154]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 155]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 156]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 157]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 158]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 159]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 160]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 161]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 162]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 163]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 164]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 165]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 166]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 167]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 168]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 169]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 170]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 171]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 172]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 173]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 174]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 175]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 176]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 177]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 178]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 179]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 180]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 181]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 182]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 183]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 184]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 185]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
    {"label": "[Session 186]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36", "viewport": {"width": 1680, "height": 1050}},
    {"label": "[Session 187]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 1024}},
    {"label": "[Session 188]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 1024}},
    {"label": "[Session 189]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1200}},
    {"label": "[Session 190]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 960}},
    {"label": "[Session 191]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 800}},
    {"label": "[Session 192]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}},
    {"label": "[Session 193]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}},
    {"label": "[Session 194]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}},
    {"label": "[Session 195]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36", "viewport": {"width": 1536, "height": 864}},
    {"label": "[Session 196]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36", "viewport": {"width": 1600, "height": 900}},
    {"label": "[Session 197]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}},
    {"label": "[Session 198]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 810}},
    {"label": "[Session 199]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 900}},
    {"label": "[Session 200]", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 960}},
]

# ─────────────────────────────────────────────────────────────────────────────
# Browser management
# ─────────────────────────────────────────────────────────────────────────────

# _launchBrowser
# Purpose: Launches a fresh Chrome instance with the correct flags and path.
# Arguments:
#           playwright: the shared Playwright instance.
# Output: A Playwright browser object.
async def _launchBrowser(playwright):

    return await playwright.chromium.launch(
        headless=False,
        executable_path=_chromePath(),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-setuid-sandbox",
            "--single-process",
            # New — turn off peripheral Chrome features unrelated to
            # page rendering, to reduce per-instance CPU/memory overhead.
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--disable-software-rasterizer",  # disables fallback CPU renderer (you already have --disable-gpu)
            "--disable-background-timer-throttling",  # stops Chrome throttling timers in background tabs
            "--disable-renderer-backgrounding",  # prevents Chrome deprioritizing background renderers
            "--disable-backgrounding-occluded-windows",  # same but for hidden windows
            "--js-flags=--max-old-space-size=128",  # caps V8 heap at 128MB per instance (default is ~1.4GB)
            "--disable-features=TranslateUI,BlinkGenPropertyTrees",  # kills translate popup + a rendering feature you don't need
            "--disable-ipc-flooding-protection",  # removes artificial throttle on IPC messages between processes
            "--no-default-browser-check",  # skips the "make Chrome default?" check on startup
            "--disable-client-side-phishing-detection",  # kills a background ML model Chrome runs
            "--disable-component-extensions-with-background-pages",  # disables built-in extensions (PDF viewer etc)
        ]
    )

# _buildContext
# Purpose: Create a browser context with a randomized user agent
#          and viewport so each restart looks like a slightly different  machine.
# Arguments:
#           browser: the browser object to create the context on.
#           proxy_index: which proxy to use if proxies are re-enabled.
# Output: A Playwright browser context.
async def _buildContext(browser, proxy_index: int):

    # Each restart gets a slightly randomized Chrome version so the
    # user agent string doesn't look like a bot cycling identically.
    chrome_version = random.randint(125, 128)

    # Pick proxy for this restart — cycles through the list using modulo
    # so it wraps around when it reaches the end.
    proxy = PROXIES[proxy_index % len(PROXIES)]
    host, port, username, password = proxy

    # Returns the necessary browser context.
    return await browser.new_context(
        user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36",
        viewport = {"width": random.randint(1280, 1440), "height": random.randint(768, 900)},
        java_script_enabled = True,
        # Proxies disabled — Mullvad handles IP rotation.
        # Each restart uses a different static residential proxy.
        # proxy_index cycles through the PROXIES list so sessions
        # spread across different IPs.
        # proxy={
        #     "server": f"http://{host}:{port}",
        #     "username": username,
        #     "password": password
        # }
    )

# _blockHeavyResources
# Purpose: Sets up a route handler on the page that aborts requests for
#          resource types we don't need — images, fonts, and media. This
#          reduces per-page CPU/network load (Chrome doesn't have to
#          download/decode/paint these) without changing what Cloudflare
#          sees in terms of JS execution or page structure, since the
#          requests are aborted at the network layer, the same as a real
#          browser with content-blocking would do.
# Arguments:
#           page: the Playwright page to attach the route handler to.
# Output: None. The handler stays active for the page's lifetime.
async def _blockHeavyResources(page):

    # Resource types we never need for this scraper — we only care
    # about the page's HTML/JS (to let Angular boot and fire its API
    # calls) and the XHR/fetch calls themselves (GetResultsData3, etc)
    BLOCKED_TYPES = {"image", "font", "media"}

    # route() registers a handler that intercepts EVERY request matching
    # the pattern "**/*" (i.e. all requests) before it's sent.
    # The handler receives a `route` object representing one request.
    async def handle_route(route):

        if route.request.resource_type in BLOCKED_TYPES:
            # abort() cancels the request entirely — Chrome never sends
            # it, never waits for a response, never decodes/paints
            # whatever would've come back.
            await route.abort()
        else:
            # continue_() lets the request proceed normally — required
            # for every request we DON'T want to block, otherwise
            # Playwright leaves it hanging forever
            await route.continue_()

    # This tell Playwright that for every request matching pattern
    # call handler instead of letting requests go through
    # automatically.
    # **/* is a global pattern that matches everything. 
    await page.route("**/*", handle_route)

# _killOldBrowser
# Purpose: Tear down the previous browser so it cannot survive as an orphan
#          process. Tries the polite Playwright close first (bounded by a
#          timeout); if that errors OR hangs, falls back to killing the
#          underlying OS process directly. Always returns cleanly — teardown
#          must never raise into the caller, but it also must never leak.
# Arguments:
#           old_browser: the browser to tear down. May be None (nothing to do)
#                        or already-dead (polite close throws, which we treat as
#                        success — already gone is the goal).
#           label: session label for log output, e.g. "[Session 7]".
# Output: None.
async def _killOldBrowser(old_browser, label: str):
 
    # Nothing to tear down.
    if not old_browser:
        return
 
    # Grab a handle to the underlying OS process BEFORE we try to close, while
    # the object is still intact. Playwright exposes the launched Chrome's
    # process at browser.process (a subprocess.Popen). If close() later hangs or
    # corrupts the object, we still hold this handle and can kill the PID.
    # Wrapped defensively: on some connection types .process may be absent.
    # getattr gets an attribute off an object by its name as a string. This
    # gets the processe's object (the process handle), 
    # and if there is none it gets None.
    proc = getattr(old_browser, "process", None)
 
    # Step 1 — try the polite close, but bounded by a timeout so a wedged
    # WebSocket can't hang the session forever (the bare `await close()` with no
    # timeout was a latent freeze).
    closed_cleanly = False
    try:
        await asyncio.wait_for(
            old_browser.close(),
            timeout=_BROWSER_CLOSE_TIMEOUT_SECONDS,
        )
        closed_cleanly = True
    except asyncio.TimeoutError:
        # Close hung — the process is almost certainly still alive. Fall through
        # to the OS kill below.
        print(f"{label} browser close timed out after "
              f"{_BROWSER_CLOSE_TIMEOUT_SECONDS}s — killing process")
    except Exception:
        # Close threw. This is usually "already dead" (good — nothing leaked),
        # but it can also be a teardown error (CancelledError/TargetClosedError)
        # on a live process. We can't tell which from the exception alone, so we
        # still attempt the OS kill below; killing an already-dead PID is a
        # harmless no-op.
        pass
 
    # Step 2 — if the polite close didn't cleanly succeed, kill the OS process
    # directly. This is the guarantee that no orphaned Chrome survives. If proc
    # is None (no handle) or already exited, the kill is a harmless no-op.
    if not closed_cleanly and proc is not None:
        try:
            # poll() returns None if still running, an exit code if already gone.
            # Checks if dead before killing it.
            if proc.poll() is None:
                proc.kill()                 # SIGKILL — immediate, no cleanup.
                print(f"{label} killed orphaned browser process (pid {proc.pid})")
        except Exception:
            # Even the kill failed (process vanished mid-call, permission, etc.).
            # Nothing more we can safely do; log and move on — the worst case is
            # one leaked process, which is strictly better than the old code's
            # leak-on-every-wedged-close.
            print(f"{label} could not kill old browser process — continuing")


# restartBrowser
# Purpose: Restarts the browser by closing the existing browser context and
#          opening a fresh one. Prevents WebSocket connection collapse on
#          long sessions.
# Arguments:
#           playwright: the Playwright instance (which stays alive the whole run).
#           old_browser: the browser object to close. Could already be closed/not
#                        existing(none).
#           config: the session config dict with user_agent and viewport.
#           proxy_index: which proxy to use if proxies are re-enabled.
# Output: Returns a tuple of (browser, page) ready to scrape with.
async def restartBrowser(playwright, old_browser, config: dict, proxy_index: int = 0):

    label = config["label"]

    # Tear down the old browser FIRST, guaranteed — polite close with a timeout,
    # OS-process kill as fallback. Replaces the old swallow-everything block that
    # leaked a process on every wedged/hung close.
    await _killOldBrowser(old_browser, label)
    
    # Launches browser, builds its context, and opens a new page.
    browser = await _launchBrowser(playwright)
    context = await _buildContext(browser, proxy_index)
    page    = await context.new_page()

    # NEW — block images/fonts/media on every request this page makes,
    # for the rest of this page's lifetime.
    await _blockHeavyResources(page)

    # Patches browser properties to look less like automation.
    # await Stealth().apply_stealth_async(page)

    # Change webdriver property of browser so it doesn't announce itself as a bot.
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    # Navigate to athletic.net on every fresh start so the session
    # looks like a real user before hitting API endpoints.
    await page.goto("https://www.athletic.net", timeout=60000)
    await page.wait_for_load_state("domcontentloaded", timeout=10000)

    print(f"{label} Browser restarted")

    return browser, page

    # _reloadWithCooldown
# Purpose: After a REAL VPN rotation, reload this session's page so its Cloudflare
#          clearance matches the new exit IP. The proactive (meet-count) rotation
#          path swaps the IP underneath every session WITHOUT restarting their
#          browsers, so each page is still holding clearance issued for the OLD IP
#          — reloading re-runs the CF handshake on the new one. Sessions call this
#          staggered across the 90s window (POST_ROTATION_STAGGER_SECONDS) so they
#          don't all reload at once. If the reload itself returns 429 (the fresh IP
#          is already rate-limited by the reload burst), this session waits
#          _RELOAD_429_COOLDOWN_SECONDS and returns — it does NOT retry the reload.
# Arguments:
#           page: this session's Playwright page (None-guarded defensively).
#           label: session label for log output, e.g. "[Session 7]".
# Output: None. Best-effort — never raises into the caller; a failed reload just
#         means the next scrape attempt pays for CF clearance instead.
async def _reloadWithCooldown(page, label: str):

    # Guard: nothing to reload. Shouldn't happen at the call site, but a None
    # page must never crash the session.
    if page is None:
        return

    try:
        # page.reload() re-navigates to the page's CURRENT url — athletic.net,
        # since scraping is navigation-free and never moves the page off it — and
        # returns the main-document Response. "domcontentloaded" returns as soon
        # as the HTML is parsed; we don't need sub-resources (_blockHeavyResources
        # aborts them anyway).
        response = await page.reload(
            wait_until="domcontentloaded",
            timeout=_RELOAD_TIMEOUT_MS,
        )
    except Exception as e:
        # Timeout/navigation error. Non-fatal — log and rejoin; the next
        # scrapeMeetBySport hits its own block handling if the IP is truly bad.
        print(f"{label} post-rotation reload failed ({e}) — rejoining anyway")
        return

    # Cloudflare's 1015 rate-limit page is served as a real HTTP 429 on the
    # document, so the reload's own response status detects it — no need to reach
    # into the scraper layer. (response is None only for non-HTTP navigations,
    # which can't occur here.)
    if response is not None and response.status == 429:
        print(f"{label} post-rotation reload got 429 — waiting "
              f"{_RELOAD_429_COOLDOWN_SECONDS}s before rejoining")
        await asyncio.sleep(_RELOAD_429_COOLDOWN_SECONDS)

# ─────────────────────────────────────────────────────────────────────────────
# Block Handler
# ─────────────────────────────────────────────────────────────────────────────
# Pattern:
#   1. Log what happened and why.
#   2. (Cloudflare only) Remove the current server — it's confirmed blocked.
#   3. Call rotator.rotate() to switch to a new IP.
#   4. Return True/False so the session loop knows whether to retry.
#
# The RETRY LOOP lives in the session, not here. These handlers just rotate
# and signal. Keeping retry logic in the caller means the handler stays
# small and testable.

# _handleCloudflareBlock
# Purpose: Responds to a Cloudflare/HTML block on the current meet. Asks the
#          rotator to switch IPs, but ONLY restarts the browser when the IP
#          actually changed. A rotation request the rotator turns into a no-op
#          (inside the 90s post-rotation cooldown, or another session already
#          rotated) must NOT trigger a browser restart — restarting re-runs the
#          expensive Cloudflare JS challenge on the SAME IP and piles load onto an
#          IP that's already settling. The old version restarted unconditionally,
#          which is what turned a single 429 into a storm of browser refreshes.
# Arguments:
#           rotator: the shared VPNRotator instance from main().
#           playwright: the Playwright instance (needed to restart the browser).
#           old_browser: the current browser (reused as-is if the IP didn't change).
#           old_page: the current page (reused as-is if the IP didn't change).
#           config: session config dict for restartBrowser.
#           proxy_index: current proxy index, passed through / incremented.
#           label: session label for log output e.g. "[Session 3]".
# Output: Tuple of (browser, page, proxy_index).
#           - real IP change  -> a freshly restarted (browser, page)
#           - cooldown no-op   -> the SAME (old_browser, old_page), after a backoff
#           - rotation failure -> (None, None), caller skips the meet
async def _handleCloudflareBlock(rotator, playwright, old_browser, old_page,
                                  config: dict, proxy_index: int,
                                  label: str) -> tuple:

    print(f"[{label}] Cloudflare block detected — removing current server and rotating")
 
    # Remove the current server BEFORE rotating so _advanceIndex() in
    # rotate() moves to a genuinely different server, not back to this one.
    # removeCurrentServer() is synchronous — it mutates the list directly.
    rotator.removeCurrentServer()

    # Snapshot the rotation counter BEFORE asking to rotate. rotate() returns
    # True both when it really rotated AND when it skipped (cooldown / someone
    # else already rotated). The counter is the only thing that distinguishes
    # them: it increments ONLY on a real rotation. So comparing before/after is
    # how we learn whether the exit IP actually changed.
    count_before = rotator.rotation_count
 
    # Rotate to the next server. rotate() handles the lock, the CLI calls,
    # the 10s wait, and the skip-if-already-rotated logic.
    success = await rotator.rotate(label, "Cloudflare block")
 
    if not success:
        print(f"[{label}] Rotation failed after Cloudflare block — skipping meet")
        return None, None, proxy_index
 
    # Did the IP actually change? True only if a real rotation bumped the counter
    # (ours, or another session's that we picked up while waiting on the lock —
    # either way the IP is now fresh and a restart is warranted).
    ip_changed = rotator.rotation_count > count_before

    if not ip_changed:
        # Cooldown no-op: rotator deliberately refused because a recent rotation
        # is still settling. Same IP → a restart would only re-solve Cloudflare
        # and add load. Back off briefly and reuse the existing browser/page so
        # the caller's retry loop tries again IN PLACE.
        print(f"{label} Rotation skipped (cooldown) — reusing browser, "
              f"backing off {_BLOCK_NOOP_BACKOFF_SECONDS}s")
        await asyncio.sleep(_BLOCK_NOOP_BACKOFF_SECONDS)
        return old_browser, old_page, proxy_index

    # Real IP change — NOW a restart earns its cost: it drops dead keep-alive
    # connections from the old IP and gets a fresh Cloudflare clearance on the new
    # one. Bump proxy_index so the per-restart config cycles too.
    proxy_index += 1
    browser, page = await restartBrowser(playwright, old_browser, config, proxy_index)

    return browser, page, proxy_index

# ─────────────────────────────────────────────────────────────────────────────
# Range splitting
# ─────────────────────────────────────────────────────────────────────────────

# _computeSessionRange
# Purpose: Determines this session's interleaved sweep — instead of
#          owning a contiguous block of meet_ids, session `index` owns
#          every `total_sessions`-th meet_id starting at start_id + index.
#          This spreads each session across the whole ID range evenly,
#          so no session finishes drastically early just because its
#          slice happened to land in a sparse region (e.g. mostly
#          status=4 "doesn't exist" meets).
# Arguments:
#           index: this session's index (0-based).
#           total_sessions: total number of sessions (the stride length).
#           start_id: first meet_id in the overall sweep range.
#           end_id: last meet_id in the overall sweep range (inclusive).
# Output: Tuple of (first_meet_id, end_id, stride).
#         first_meet_id = start_id + index — this session's starting point.
#         end_id — unchanged, shared upper bound for all sessions.
#         stride = total_sessions — the step size for range().
def _computeSessionRange(index: int, total_sessions: int,
                        start_id: int, end_id: int) -> tuple:
    
    first_meet_id = start_id + index
    stride = total_sessions

    return first_meet_id, end_id, stride


# ─────────────────────────────────────────────────────────────────────────────
# Checkpointing
# ─────────────────────────────────────────────────────────────────────────────
#
# BIG IDEA FOR THIS SECTION:
# meet_queue can't tell us where a session "left off" — it already
# contains ~545K rows from the OLD discovery method, scattered across
# the whole 1-670,000 space with no relation to this sweep's order.
# MAX(meet_id) in a session's range would jump to whatever pre-existing
# row happens to be near the top of that range, skipping everything
# below it that this sweep hasn't actually touched yet.
#
# Instead: each session writes its OWN tiny progress file, recording
# the last meet_id it FULLY finished (both sports checked/attempted).
# On restart, a session reads its own file and resumes right after that
# point — no dependency on meet_queue's contents at all.
#
# The file also stores the (session_start, session_end) range it was
# written under. If you change the number of active sessions, every
# session's range shifts (chunk sizes change), so an old checkpoint's
# meet_id may no longer correspond to anything meaningful for the "new"
# session at that index. We detect this by checking whether the stored
# range matches the range freshly computed for THIS run — if not, the
# checkpoint is stale and we fall back to session_start. getQueueStatus
# still skips anything already resolved, just at the cost of re-checking
# from the top of the (new, smaller-or-larger) range.


# _checkpointPath
# Purpose: Builds the file path for a given session's checkpoint file.
# Arguments:
#           index: this session's position in active_configs (0-based).
# Output: Path string, e.g. "checkpoints/session_3.txt".
def _checkpointPath(index: int) -> str:

    # Combines path pieces into a single file path string, combining
    # the checpoint directory and then the file in that directory
    # whose session index we are on.
    return os.path.join(CHECKPOINT_DIR, f"session_{index}.txt")


# _loadCheckpoint
# Purpose: Figures out where this session should start (or resume)
#          within its assigned range, based on its checkpoint file.
# Arguments:
#           index: this session's index.
#           first_meet_id: this session's starting meet_id for this run
#                           (= start_id + index).
#           stride: this session's step size for this run
#                   (= total_sessions).
# Output: The meet_id this session's loop should start (or resume) at.
#         - No checkpoint file: session_start (fresh start).
#         - Checkpoint file exists but its stored range doesn't match
#           (session_start, session_end) for this run: session_start
#           (stale — session count changed since last run).
#         - Checkpoint file exists and matches: last_completed + 1
#           (resume right after the last fully-finished meet_id).
#         - Any parse error (corrupted/partial file from a Ctrl+C
#           mid-write): session_start, treated as no checkpoint.
def _loadCheckpoint(index: int, first_meet_id: int, stride: int) -> int:

    path = _checkpointPath(index)

    # If the file is not already created that means this session has not
    # scraped before, so it starts at the start of it's slice.
    if not os.path.exists(path):
        return first_meet_id
    
    try:
        # Stores contains of the file. "r" is read mode. f is the
        # file object.
        with open(path, "r") as f:
            contents = f.read().strip()

        # Get's contents of the file.
        # Splits "start,end,meet_id" into three string pieces, then
        # converts each to an int. If contents is malformed (wrong
        # number of pieces, non-numeric text from a partial write),
        # this raises ValueError, caught below.
        stored_first_str, stored_stride_str, last_completed_str = contents.split(",")

        stored_first = int(stored_first_str)
        stored_stride = int(stored_stride_str)
        last_completed = int(last_completed_str)

    # ValueError if .split(",") doesn't produce exactly 3 pieces 
    # or int(...) fails on non-numeric text.
    # (both can happen if Ctrl+C interrupted a write mid-way, leaving a truncated file).
    # OSError if the file exists but can't be read for some reason.
    except (ValueError, OSError):
        # Corrupted, partial, or unreadable file — treat as no checkpoint.
        return first_meet_id
    
    # Stored stride/first_meet_id don't match this run's — e.g. total_sessions
    # changed, so this session's slice of the ID space is different now.
    # The old last_completed_meet_id may not even be ≡ first_meet_id (mod stride)
    # under the new stride, so don't try to adapt it — start fresh.
    if stored_first != first_meet_id or stored_stride != stride:
        return first_meet_id
 
    # Valid checkpoint — resume one stride past the last fully-completed
    # meet_id.
    return last_completed + stride


# _saveCheckpoint
# Purpose: Records that this session has FULLY finished processing one
#          meet_id (both XC and TF checked/attempted), by overwriting
#          this session's checkpoint file.
#          Called once per meet_id, after both sports are handled —
#          NOT after each individual sport attempt, so a checkpoint
#          never points at a meet_id with unfinished work.
# Arguments:
#           index: this session's index.
#           first_meet_id: this session's starting meet_id for this run
#                           (= start_id + index).
#           stride: this session's step size for this run
#                   (= total_sessions).
#           meet_id: the meet_id just fully resolved — becomes
#                    last_completed_meet_id.
# Output: None. Overwrites (does not append to) the checkpoint file.
def _saveCheckpoint(index: int, first_meet_id: int, stride: int, meet_id: int):

    # Ensure the checkpoints/ directory exists. exist_ok=True means
    # "don't raise if it's already there" — makes this safe to call
    # every time without checking first.
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)

    path = _checkpointPath(index)
 
    # "w" mode overwrites the file's entire contents — we only ever
    # need the MOST RECENT completed meet_id, not a history of all of
    # them. So "w" first truncates, and then we can write to the file
    # using it. We write the session's current info to the file.
    # f is the file object.
    with open(path, "w") as f:
        f.write(f"{first_meet_id},{stride},{meet_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Meet processing
# ─────────────────────────────────────────────────────────────────────────────


# _processMeetResult
# Purpose: Handles the result of scraping ONE (meet_id, sport)
#          combo — records the outcome in meet_queue (or doesn't, if
#          the meet doesn't exist for this sport), updates session
#          stats, and prints progress.
# Arguments:
#           n: result count from scrapeMeetBySport. Meaningful only if
#              exists=True: 0+ = success, -1 = failed.
#           exists: whether a meet exists at this (meet_id, sport).
#           meet_id: athletic.net meet ID.
#           label: session label for logging.
#           processed: current processed count for this session.
#           failed: current failed count for this session.
#           results_saved: current results saved count for this session.
#           consecutive_failures: current consecutive failure streak.
# Output: Tuple of (processed, failed, results_saved, consecutive_failures).
async def _processMeetResult(n: int, exists: bool, meet_id: int, sport: str,
                        label: str, processed: int, failed: int,
                        results_saved: int, consecutive_failures: int) -> tuple:
    
    # No meet exists at this (meet_id, sport) — write nothing, count nothing.
    # If a future full pass is run it will check this combo again (getQueueStatus
    # returns None forever for it, since no row ever gets written).
    if not exists:
        await runDbCall(markScraped, meet_id, sport, status=4)
        return processed, failed, results_saved, consecutive_failures

    # Marks the meet scraped successfully or failed based on
    # if there are no or some results, prints progress.
    if n >= 0:
        await runDbCall(markScraped, meet_id, sport, status=1)
        processed += 1
        results_saved += n
        consecutive_failures = 0
    # One of the functions returned -1 error, meet fails.
    else:
        await runDbCall(markScraped, meet_id, sport, status=2)
        failed += 1
        consecutive_failures += 1 
    return processed, failed, results_saved, consecutive_failures

# ─────────────────────────────────────────────────────────────────────────────
# Session runner
# ─────────────────────────────────────────────────────────────────────────────

# runSession
# Purpose: Runs one complete browser session from start to finish.
#          Sweeps this session's assigned meet_id range, checking
#          meet_queue for each sport before attempting it, scraping
#          whichever sports are still needed, and recording results.
# Arguments:
#           playwright: the Playwright instance shared across all sessions.
#           config: the session config dict with label, user_agent, viewport.
#           rotator: the shared VPNRotator instance from main().
#           index: this session's position in active_configs (0-based) —
#                  used for the checkpoint file name.
#           session_start: this session's range lower bound (inclusive).
#           session_end: this session's range upper bound (inclusive).
# Output: Dict with keys label, processed, failed, results_saved.
async def runSession(playwright, config: dict, rotator: VPNRotator,
                      index: int, first_meet_id: int, session_end: int,
                      stride: int) -> dict:
    
    # Gets config for this browser session.
    label = config["label"]

    # NEW — stagger each session's startup so their initial Cloudflare
    # JS challenges (triggered by the athletic.net navigation inside
    # restartBrowser) don't all fire in the same ~1s window. Session 0
    # starts immediately; session 1 waits 8s, session 2 waits 16s, etc.
    await asyncio.sleep(index * STAGGER_SECONDS)

    # Tracks stats for the session's summary.
    processed = 0
    failed = 0
    results_saved = 0

    # Start as None — restartBrowser handles the first launch too.
    browser = None
    page = None

    # Tracks meets scraped by this browser instance. Resets on each restart.
    meets_since_restart = 0

    # Tracks the last # of VPN rotations this session has seen.
    last_rotation_generation = 0

    # Tracks how many consecutive failures in a row.
    # Resets to 0 on any success.
    consecutive_failures = 0

    # How many unscraped meets to pull from DB at one time.
    BATCH_SIZE = 50

    # Tracks which proxy to use — increments on each browser restart
    # so we cycle through different IPs over time.
    proxy_index = SESSION_CONFIGS.index(config)  # start each session on a different proxy

    while (True):
        # Wraps the entire session in a try/except so if anything bad happens
        # we return what we have instead of crashing the launcher and other sessions.
        try:
                
            # One DB round trip for the whole batch.
            # Returns only the meet_ids in this batch that need work,
            # with their specific sports pre-filtered by the DB.
            # Meet_ids already done or in progress (status 1/2/3/4) 
            # are absent from needs_work entirely — we never loop over them.
            batch = await runDbCall(getBatchUnscrapedMeets, BATCH_SIZE)
            
            # Ran out of meets to scrape, break.
            if not batch:
                break

            for meet_id in batch:

                # Gets the unscraped Sports for this meetID.
                sports_to_try = batch.get(meet_id, [])

                # No work needed for this meet_id — shouldn't happen
                # but continue anyway.
                if not sports_to_try:
                    continue

                meets_since_restart += 1

                # First meet ever, or we've hit the restart threshold.
                # Close old browser, open fresh one, reset the counter.
                if browser is None or meets_since_restart >= RESTART_EVERY:
                    browser, page = await restartBrowser(
                        playwright, browser, config, proxy_index
                    )
                    proxy_index         += 1
                    meets_since_restart  = 0

                # Attempts to scrape each sport this meet_id still hasn't
                # scraped.
                for sport in sports_to_try:

                    # ── Scrape with retry loop ────────────────────────────────
                    # On a Cloudflare block we rotate and retry the same meet
                    # up to MAX_BLOCK_RETRIES times. If rotation fails or we
                    # exhaust retries, we skip the meet and move on.

                    n = -1       # default to failed if we never succeed
                    block_retries = 0

                    # Scrape the meet — tries XC first, then TF.
                    # scrapeMeetUnified handles sport detection and queue retagging.
                    while block_retries <= MAX_BLOCK_RETRIES:
                        try:
                            # Wait out any in-progress rotation, then take this
                            # session's staggered slot in the 90s post-rotation
                            # window (waitForTunnel sleeps us to it). Snapshot the
                            # generation BEFORE so we can tell whether we actually
                            # came through a NEW rotation.
                            gen_before = last_rotation_generation
                            last_rotation_generation = await rotator.waitForTunnel(
                                index, 
                                last_rotation_generation, 
                                len(SESSION_CONFIGS)
                            )

                            # The generation advances ONLY on a real rotation. If it
                            # moved while we waited, the exit IP just changed under
                            # us — reload to refresh Cloudflare clearance on the new
                            # IP (the 60s 429 cooldown is inside the helper).
                            if last_rotation_generation != gen_before:
                                await _reloadWithCooldown(page, label)

                            # Meta-only TF re-scrape: GetMeetData only, no results.
                            # XC still goes through the normal path even in meta mode
                            # (this pass is about repairing TF metadata).
                            if META_ONLY and sport == "TF":
                                n, exists = await scrapeMeetTFMetaOnly(
                                    page, meet_id, label, rotator
                                )
                            else:
                                n, exists = await scrapeMeetBySport(
                                    page, meet_id, sport,
                                    label, rotator
                                )
                            break

                        except CloudflareException:
                            block_retries += 1

                            if block_retries > MAX_BLOCK_RETRIES:
                                # Exhausted retries — log and give up on this meet.
                                print(f"{label} Block retries exhausted for meet "
                                    f"{meet_id} — skipping")
                                break

                            print(f"{label} Block retry {block_retries}/"
                                f"{MAX_BLOCK_RETRIES} for meet {meet_id}")

                            # Ask the rotator to switch IPs. The browser is only
                            # restarted if the IP actually changed; on a cooldown
                            # no-op we get the SAME browser back and retry in place.
                            prev_browser = browser
                            browser, page, proxy_index = await _handleCloudflareBlock(
                                rotator, playwright, browser, page,
                                config, proxy_index, label
                            )

                            # Rotation itself failed — skip meet entirely.
                            if browser is None:
                                break

                            # Only reset the restart counter if we genuinely
                            # restarted. `is not` is an IDENTITY check: restartBrowser
                            # returns a brand-new object, so this is True only on a
                            # real restart. On a no-op we reuse the same object, so
                            # its progress toward the next RESTART_EVERY is preserved.
                            if browser is not prev_browser:
                                meets_since_restart = 0
                    
                    # Record result.
                    processed, failed, results_saved, consecutive_failures = (
                        await _processMeetResult(
                            n, exists, meet_id, sport, label,
                            processed, failed, results_saved, consecutive_failures
                        )
                    )

                    # Proactive rotation check — rotates every GLOBAL_MEETS_PER_ROTATION
                    # meets across all sessions combined.
                    await rotator.checkRotation(label)

                    # Pause between meets — per-session pacing knob (scrape_tuning.py).
                    # Widen perMeetDelayRange there if you see [429] lines; narrow to speed up.
                    low, high = perMeetDelayRange()
                    await asyncio.sleep(random.uniform(low, high))

        except Exception as e:
            import traceback
            print(f"{label} Session crashed: {e}")
            traceback.print_exc()

    if browser:
        await browser.close()

    # Returns a dictionary with this session's stats for the final summary.
    return {
        "label": label,
        "processed": processed,
        "failed": failed,
        "results_saved": results_saved
    }

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

# _printSummary
# Purpose: Prints the final summary across all sessions after the queue
#          is empty. Aggregates per-session stats into totals.
# Arguments:
#           summaries: list of session result dicts (or Exception objects
#                      if a session crashed).
# Output: None. Prints to stdout.
def _printSummary(summaries: list):
    print("\n===== FINAL SUMMARY =====")
 
    total_processed = 0
    total_failed    = 0
    total_results   = 0
 
    for summary in summaries:
        if isinstance(summary, Exception):
            print(f"A session crashed: {summary}")
            continue

        print(f"{summary['label']}: {summary['processed']} processed, "
              f"{summary['failed']} failed, "
              f"{summary['results_saved']} results saved")
        
        total_processed += summary["processed"]
        total_failed    += summary["failed"]
        total_results   += summary["results_saved"]
 
    print(f"\nTOTAL: {total_processed} processed | "
          f"{total_failed} failed | "
          f"{total_results} results saved")
    print("=========================")
 

# main
# Purpose: Initializes the DB pool and tables, resets any stuck in-progress
#          meets, launches all sessions in parallel, then prints the final
#          summary. This is the top-level entry point for the scraper.
# Arguments: None.
# Output: None.
_QUEUE_STATE = {0: "due", 1: "done", 2: "failed", 3: "in-progress",
                4: "skipped"}


def _queueState(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sport, scraped, count(*)
            FROM   meet_queue WHERE source = 'anet'
            GROUP  BY sport, scraped ORDER BY sport, scraped
        """)
        rows = cur.fetchall()
    conn.rollback()
    return rows


def _prepareQueue():
    """Fill the queue, then report it. Exits if there is nothing to do.

    ★ THE LAUNCHER OWNS THIS NOW (owner, 2026-09-18: "can you just make the
      launcher handle the queue itself"). Running a seeding script and then
      a scraper, and reasoning about five queue states in between, was two
      commands and one silent failure mode: the sessions claimed nothing and
      said "0 processed".

    ! THE SAME CODE THE SCRIPT USES -- queue_anet_new.seedAll -- so a scrape
      night cannot get a different answer than a dry run did. Set
      ANET_NO_SEED=1 to drain the queue exactly as it stands.
    """
    from database import getConn
    from queue_anet_new import seedAll, dueCounts

    skip = os.environ.get("ANET_NO_SEED", "") not in ("", "0", "false")
    with getConn() as conn:
        if skip:
            print("[queue] ANET_NO_SEED=1 -- draining the queue as it "
                  "stands, seeding nothing.")
        else:
            print("[queue] seeding: forward from the last real id per sport, "
                  "plus recent meets with no results")
            seedAll(conn, write=True,
                    ahead=int(os.environ.get("SEED_AHEAD", 2000)),
                    recent_days=int(os.environ.get("SEED_RECENT_DAYS", 120)))

        print("[queue] anet meet_queue:")
        for sport, state, n in _queueState(conn):
            print(f"[queue]   {sport}  "
                  f"{_QUEUE_STATE.get(state, state):<12} {n:,}")
        due = dueCounts(conn)

    total = sum(due.values())
    if not total:
        print("[queue] ⚠ NOTHING IS DUE even after seeding. Either every id "
              "up to each sport's watermark is done and the forward walk "
              "found no gap, or the walk has decided the corpus ends "
              "(--stop-after-misses).")
        print("[queue]   Look at it with: python scripts/queue_anet_new.py")
        sys.exit(1)
    print("[queue] due: " + ", ".join(f"{k} {v:,}"
                                      for k, v in sorted(due.items())))


async def main():
 
    # Pool must be initialized before any session touches the DB.
    initPool()
    createTables()
    resetInProgress()

    # ★ SAY WHAT THERE IS TO DO, BEFORE DOING IT (owner, 2026-09-18: six
    #   sessions reported "0 processed" and the reason -- an empty queue --
    #   was printed nowhere). A run with nothing to claim is the most likely
    #   outcome of a mis-seeded queue and the hardest to tell from a broken
    #   scraper, so it is stated up front and the run stops instead of
    #   spinning up six copies of Chrome to discover it.
    _prepareQueue()

    # One VPNRotator shared across all sessions. Passed into every runSession
    # call so they all coordinate rotation through the same lock and counter.
    rotator = VPNRotator()
 
    async with async_playwright() as playwright:
 
        # To run 10 sessions locally, change SESSION_CONFIGS to SESSION_CONFIGS[:10].
        active_configs = SESSION_CONFIGS[:NUM_SESSIONS] 
 
        # Each session is stored in this list.
        tasks = []

        # For each config, computes this session's interleaved starting
        # point and stride, then starts the session.
        for i, config in enumerate(active_configs):

            # ! THE RANGE IS VESTIGIAL, AND NOT PRINTED ANY MORE. runSession
            #   takes these three and never uses them -- it claims from
            #   meet_queue. Passed so the signature is unchanged; see the
            #   note on _UNUSED_SCAN_START_ID.
            first_meet_id, session_end, stride = _computeSessionRange(
                i, len(active_configs),
                _UNUSED_SCAN_START_ID, _UNUSED_SCAN_END_ID
            )

            print(f"  {config['label']}: claiming from the shared queue")

            # Starts the sessions and appends it to the session list.
            tasks.append(asyncio.create_task(
                runSession(playwright, config, rotator, i,
                           first_meet_id, session_end, stride)
            ))
        summaries = await asyncio.gather(*tasks, return_exceptions=True)
 
    _printSummary(summaries)
    countRows()
 
    # Close all pool connections cleanly on exit.
    closePool()
 
 
if __name__ == "__main__":
    asyncio.run(main())