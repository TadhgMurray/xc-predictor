# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 6/4/2026
# File Title: launcher.py
# Purpose: Launches 25 parallel browsing sessions that all scrape
# the results for each XC/TF meet in meet_queue. Detects whether
# each meet is XC or TF at scrape time, rather than trusting queue's
# sport tag (which is wrong).

import asyncio
import random
import platform

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from database import (
    initPool, closePool, createTables,
    countRows, markScraped, resetInProgress,
    getUnscrapedMeets, countRemaining
)
from scrape_results import scrapeMeetUnified
from scraper import CloudflareException
from vpn_rotation import VPNRotator
 
# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
 
# Chrome path differs by OS. We must use real Chrome (not Playwright's bundled
# Chromium) because Cloudflare fingerprints the binary — Chromium gets blocked.
# headless=False is also required for the same reason.
if platform.system() == "Windows":
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
elif platform.system() == "Linux":
    # On Ubuntu VM, Chrome is installed via apt and lives here.
    CHROME_PATH = "/usr/bin/google-chrome"
else:
    raise RuntimeError(f"Unsupported OS: {platform.system()}")

# How many meets to scrape before restarting the browser.
# Restarts prevent WebSocket connection collapse on long sessions and
# cycle to a fresh proxy IP.
RESTART_EVERY = 200

# How many times to retry a meet after a block-triggered rotation
# before giving up and moving on. 3 retries means we try the meet
# on up to 4 different IPs total before skipping it.
MAX_BLOCK_RETRIES = 3

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
    # {
    #     "label": "[Session 21]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1600, "height": 900}
    # },
    # {
    #     "label": "[Session 22]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1280, "height": 720}
    # },
    # {
    #     "label": "[Session 23]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1440, "height": 810}
    # },
    # {
    #     "label": "[Session 24]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1366, "height": 900}
    # },
    # {
    #     "label": "[Session 25]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1680, "height": 1050}
    # }
]

# ─────────────────────────────────────────────────────────────────────────────
# Browser management
# ─────────────────────────────────────────────────────────────────────────────


# watchForErrorModal
# Purpose: Background task that continuously watches for athletic.net's
#          "Server Communication Error" modal and dismisses it via Escape.
#          Runs for the entire session lifetime so it catches the modal
#          whenever it appears, including mid-scrape.
# Arguments:
#           page: Playwright page object to watch.
# Output: None. Runs forever until the task is cancelled.
async def watchForErrorModal(page):

    while True:
        try:
            modal = page.locator("div.modal-dialog:has(h4:text('Server Communication Error'))")
            if await modal.is_visible(timeout=0):
                await page.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(0.5)

# _launchBrowser
# Purpose: Launches a fresh Chrome instance with the correct flags and path.
# Arguments:
#           playwright: the shared Playwright instance.
# Output: A Playwright browser object.
async def _launchBrowser(playwright):

    return await playwright.chromium.launch(
        headless=False,
        executable_path=CHROME_PATH,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-setuid-sandbox",
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
        viewport = {"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
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

    # Close the old browser if one exists. Frees memory and kills
    # old WebSocket connection cleanly.
    if old_browser:
        try:
            await old_browser.close()
        except Exception:
            pass # If it's already dead, ignore the error.
    
    # Launches browser, builds its context, and opens a new page.
    browser = await _launchBrowser(playwright)
    context = await _buildContext(browser, proxy_index)
    page    = await context.new_page()

    # Patches browser properties to look less like automation.
    await Stealth().apply_stealth_async(page)

    # Background task — watches for and dismisses the error modal.
    asyncio.create_task(watchForErrorModal(page))

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
# Purpose: Handles a Cloudflare or HTML block by removing the blocked server,
#          rotating to a new IP, and restarting the browser on the new IP.
# Arguments:
#           rotator: the shared VPNRotator instance from main().
#           playwright: the Playwright instance (needed to restart the browser).
#           old_browser: the current browser to close before restarting.
#           config: session config dict for restartBrowser.
#           proxy_index: current proxy index to pass through to restartBrowser.
#           label: session label for log output e.g. "[Session 3]".
# Output: Tuple of (browser, page, proxy_index).
#         browser/page are None if rotation failed (caller should skip meet).
async def _handleCloudflareBlock(rotator, playwright, old_browser,
                                  config: dict, proxy_index: int,
                                  label: str) -> tuple:

    print(f"[{label}] Cloudflare block detected — removing current server and rotating")
 
    # Remove the current server BEFORE rotating so _advanceIndex() in
    # rotate() moves to a genuinely different server, not back to this one.
    # removeCurrentServer() is synchronous — it mutates the list directly.
    rotator.removeCurrentServer()
 
    # Rotate to the next server. rotate() handles the lock, the CLI calls,
    # the 10s wait, and the skip-if-already-rotated logic.
    success = await rotator.rotate(label, "Cloudflare block")
 
    if not success:
        print(f"[{label}] Rotation failed after Cloudflare block — skipping meet")
        return None, None, proxy_index
 
    # Restart the browser so its network traffic goes through the new IP.
    # Increment proxy_index so we also cycle to a fresh proxy config.
    proxy_index += 1
    browser, page = await restartBrowser(playwright, old_browser, config, proxy_index)
 
    return browser, page, proxy_index



# ─────────────────────────────────────────────────────────────────────────────
# Meet processing
# ─────────────────────────────────────────────────────────────────────────────


# _processMeetResult
# Purpose: Handles the result of scraping one meet — marks it done or failed,
#          updates session stats, and prints progress.
# Arguments:
#           n: result count from scrapeMeetUnified. 0+ = success, -1 = failed.
#           meet_id: athletic.net meet ID.
#           label: session label for logging.
#           processed: current processed count for this session.
#           failed: current failed count for this session.
#           results_saved: current results saved count for this session.
#           consecutive_failures: current consecutive failure streak.
# Output: Tuple of (processed, failed, results_saved, consecutive_failures).
def _processMeetResult(n: int, meet_id: int, label: str, processed: int,
                        failed: int, results_saved: int,
                        consecutive_failures: int) -> tuple:

    # Marks the meet scraped successfully or failed based on
    # if there are no or some results, prints progress.
    if n >= 0:
        markScraped(meet_id, status=1)
        processed += 1
        results_saved += n
        consecutive_failures = 0
        print(f"{label} [{processed}] Meet {meet_id}: {n} results "
              f"| {countRemaining()} remaining")
    # One of the functions returned -1 error, meet fails.
    else:
        markScraped(meet_id, status=2)
        failed += 1
        consecutive_failures += 1
        print(f"{label} [FAIL #{failed}] Meet {meet_id} marked failed")
 
    return processed, failed, results_saved, consecutive_failures

# ─────────────────────────────────────────────────────────────────────────────
# Session runner
# ─────────────────────────────────────────────────────────────────────────────

# runSession
# Purpose: Runs one complete browser session from start to finish.
#          Pulls meets from the queue, scrapes each one, marks done or failed.
# Arguments:
#           playwright: the Playwright instance shared across all sessions.
#           config: the session config dict with label, user_agent, viewport.
#           rotator: the shared VPNRotator instance from main().
# Output: Dict with keys label, processed, failed, results_saved.
async def runSession(playwright, config: dict, rotator: VPNRotator) -> dict:
    
    # Gets config for this browser session.
    label = config["label"]

    # Tracks stats for the session's summary.
    processed = 0
    failed = 0
    results_saved = 0

    # Start as None — restartBrowser handles the first launch too.
    browser = None
    page = None

    # Tracks meets scraped by this browser instance. Resets on each restart.
    meets_since_restart = 0

    # Tracks how many consecutive failures in a row.
    # Resets to 0 on any success.
    consecutive_failures = 0

    # Tracks which proxy to use — increments on each browser restart
    # so we cycle through different IPs over time.
    proxy_index = SESSION_CONFIGS.index(config)  # start each session on a different proxy

    # Wraps the entire session in a try/except so if anything bad happens
    # we return what we have instead of crashing the launcher and other sessions.
    try:

        # Scrapes all XC meet results until queue is empty.
        while True:

            # Gets the current scraping batch of 100 meets.
            batch = getUnscrapedMeets(limit=100)

            # If nothing in batch all meets have been scraped, so we are done.
            if not batch:
                print(f"{label} Queue empty, finishing")
                break

            for meet_id, sport in batch:
                
                meets_since_restart += 1

                # First meet ever, or we've hit the restart threshold.
                # Close old browser, open fresh one, reset the counter.
                if browser is None or meets_since_restart >= RESTART_EVERY:
                    browser, page = await restartBrowser(
                        playwright, browser, config, proxy_index
                    )
                    proxy_index         += 1
                    meets_since_restart  = 0

                # ── Scrape with retry loop ────────────────────────────────
                # On a Cloudflare block we rotate and retry the same meet
                # up to MAX_BLOCK_RETRIES times. If rotation fails or we
                # exhaust retries, we skip the meet and move on.
 
                n            = -1       # default to failed if we never succeed
                block_retries = 0

                # Scrape the meet — tries XC first, then TF.
                # scrapeMeetUnified handles sport detection and queue retagging.
                while block_retries <= MAX_BLOCK_RETRIES:
                    try:
                        result = await scrapeMeetUnified(page, meet_id, label)
                        n, detected_sport = result
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
 
                        # Rotate VPN and restart browser on new IP.
                        browser, page, proxy_index = await _handleCloudflareBlock(
                            rotator, playwright, browser,
                            config, proxy_index, label
                        )
                        meets_since_restart = 0
 
                        # Rotation itself failed — skip meet entirely.
                        if browser is None:
                            break
                
                # Record result.
                processed, failed, results_saved, consecutive_failures = (
                    _processMeetResult(
                        n, meet_id, label,
                        processed, failed, results_saved, consecutive_failures
                    )
                )

                # Proactive rotation check — rotates every GLOBAL_MEETS_PER_ROTATION
                # meets across all sessions combined.
                await rotator.checkRotation(label)
 
                # Pause between meets to avoid detection.
                await asyncio.sleep(random.uniform(1.0, 2.0))

    except Exception as e:
        print(f"{label} Session crashed: {e}")

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
async def main():
 
    # Pool must be initialized before any session touches the DB.
    initPool()
    createTables()
    resetInProgress()

    # One VPNRotator shared across all sessions. Passed into every runSession
    # call so they all coordinate rotation through the same lock and counter.
    rotator = VPNRotator()
 
    async with async_playwright() as playwright:
 
        # To run 10 sessions locally, change SESSION_CONFIGS to SESSION_CONFIGS[:10].
        active_configs = SESSION_CONFIGS
 
        print(f"Launching {len(active_configs)} sessions...")
 
        # Each session runs concurrently as an asyncio task.
        # return_exceptions=True means a crashed session returns its exception
        # as a value instead of cancelling all other sessions.
        tasks     = [asyncio.create_task(runSession(playwright, config, rotator))
                     for config in active_configs]
        summaries = await asyncio.gather(*tasks, return_exceptions=True)
 
    _printSummary(summaries)
    countRows()
 
    # Close all pool connections cleanly on exit.
    closePool()
 
 
if __name__ == "__main__":
    asyncio.run(main())