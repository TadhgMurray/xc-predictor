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
from vpn_rotation import VPNRotator
from playwright.async_api import async_playwright
from database import createTables, countRows, markScraped, resetInProgress, DB_PATH, getConn
from scrape_results import getUnscrapedMeets, countRemaining, scrapeMeetUnified
from playwright_stealth import Stealth
from scraper import CloudflareException
import platform

# Detect OS and set Chrome path accordingly.
# We must use real Chrome (not Chromium) because Cloudflare fingerprints
# the browser binary — Chromium gets blocked, real Chrome doesn't.
# headless=False is also required for the same reason.
if platform.system() == "Windows":
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
elif platform.system() == "Linux":
    # On Ubuntu VM, Chrome is installed via apt and lives here.
    CHROME_PATH = "/usr/bin/google-chrome"
else:
    raise RuntimeError(f"Unsupported OS: {platform.system()}")

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
    }#,
    # {
    #     "label": "[Session 11]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1680, "height": 1050}
    # },
    # {
    #     "label": "[Session 12]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1280, "height": 1024}
    # },
    # {
    #     "label": "[Session 13]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1600, "height": 1024}
    # },
    # {
    #     "label": "[Session 14]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1920, "height": 1200}
    # },
    # {
    #     "label": "[Session 15]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1440, "height": 960}
    # },
    # {
    #     "label": "[Session 16]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1280, "height": 800}
    # },
    # {
    #     "label": "[Session 17]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1366, "height": 768}
    # },
    # {
    #     "label": "[Session 18]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1440, "height": 900}
    # },
    # {
    #     "label": "[Session 19]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1920, "height": 1080}
    # },
    # {
    #     "label": "[Session 20]",
    #     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    #     "viewport": {"width": 1536, "height": 864}
    # },
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

# runSession
# Purpose: Runs one complete browser session from start to finish.
#          Pulls meets from the queue, scrapes each one, marks done or failed.
# Arguments:
#           playwright: the Playwright instance shared across all sessions.
#           config: the session config dict with label, user_agent, viewport.
#           rotator: shared VPNRotator instance for coordinated IP rotation.
# Output: Dict with keys label, processed, failed, results_saved.
async def runSession(playwright, config: dict, rotator: VPNRotator) -> dict:
    
    # Gets config for this browser session.
    label = config["label"]

    # Tracks stats for the session's summary.
    processed = 0
    failed = 0
    results_saved = 0

    # How many meets before we restart the browser. 200 is well under the
    # ~600 limit where connections were dying.
    RESTART_EVERY = 200

    # Start as None — restartBrowser handles the first launch too.
    browser = None
    page = None

    # Tracks meets scraped by this browser instance. Resets on each restart.
    meets_since_restart = 0

    # Tracks how many consecutive failures in a row.
    # Resets to 0 on any success.
    consecutive_failures = 0

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
                    browser, page = await restartBrowser(playwright, browser, config)
                    meets_since_restart = 0

                # Scrape the meet — tries XC first, then TF.
                # scrapeMeetUnified handles sport detection and queue retagging.
                try:
                    n, detected_sport = await scrapeMeetUnified(page, meet_id, label)

                # Catches the vpn ip being blocked due to cloudflare block page
                # popping up. Goes to next IP in VPN.
                except CloudflareException:
                    print(f"{label} [CLOUDFLARE] IP blocked, removing server and rotating")
                    # Remove the blocked server then rotate to the next one.
                    rotator.removeCurrentServer()
                    await rotator.rotate(label, "cloudflare block")
                    # Restart browser on the fresh IP.
                    browser, page = await restartBrowser(playwright, browser, config)
                    meets_since_restart = 0
                    consecutive_failures = 0
                    # Reset meet to unscraped so it gets retried on the fresh IP.
                    markScraped(meet_id, status=0)
                    continue
                # Cathces JSON parse error when rate-limited, not blocked.
                # Goes to next IP in VPN.
                except Exception as e:
                    # HTML block — athletic.net returned a page instead of JSON.
                    if "Unexpected token '<'" in str(e) or "DOCTYPE" in str(e):
                        print(f"{label} [HTML BLOCK] Got HTML instead of JSON, rotating immediately")
                        rotator.removeCurrentServer()
                        await rotator.rotate(label, "html block")
                        browser, page = await restartBrowser(playwright, browser, config)
                        meets_since_restart = 0
                        consecutive_failures = 0
                        markScraped(meet_id, status=0)
                        continue
                    raise

                # Marks the meet scraped and tracks results saved.
                if n >= 0:
                    markScraped(meet_id, status=1)
                    processed += 1
                    results_saved += n
                    # Reset failure streak on any success.
                    consecutive_failures = 0
                    print(f"{label} [{processed}] Meet {meet_id}: {n} results | {countRemaining()} remaining")
                else:
                    markScraped(meet_id, status=2)
                    failed += 1
                    consecutive_failures += 1
                    print(f"{label} [FAIL #{failed}] Meet {meet_id} marked failed")

                # Check if VPN rotation should be triggered.
                # Rotates every 200 meets or on 20 consecutive failures.
                # If rotation happened, reset the counter for this session.
                rotated = await rotator.checkRotation(label, consecutive_failures)

                if rotated:
                    consecutive_failures = 0
                
                # Pause between meets to avoid detection.
                await asyncio.sleep(random.uniform(3.0, 5.0))

    except Exception as e:
        print(f"{label} Session crashed: {e}")

    # Returns a dictionary with this session's stats for the final summary.
    return {
        "label": label,
        "processed": processed,
        "failed": failed,
        "results_saved": results_saved
    }

async def watchForErrorModal(page):
    # Continuously watches for the server communication error modal
    # and dismisses it immediately — runs as a background task for
    # the entire session so it catches the modal whenever it appears,
    # including before page load completes.
    while True:
        try:
            modal = page.locator("div.modal-dialog:has(h4:text('Server Communication Error'))")
            if await modal.is_visible(timeout=0):
                await page.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(0.5)

# restartBrowser
# Purpose: restarts the browser by closing the existing browser context and
#          opening a fresh one. Prevents WebSocket connection collapse on
#          long sessions.
# Arguments:
#           playwright: the Playwright instance (which stays alive the whole run).
#           old_browser: the browser object to close. Could already be closed/not
#                        existing(none).
#           config: the session config dict with user_agent and viewport.
# Output: Returns a tuple of (browser, page) ready to scrape with.
async def restartBrowser(playwright, old_browser, config: dict):

    label = config["label"]

    # Close the old browser if one exists. Frees memory and kills
    # old WebSocket connection cleanly.
    if old_browser:
        try:
            await old_browser.close()
        except Exception:
            pass # If it's already dead, ignore the error.

    # Launch a fresh browser.
    browser = await playwright.chromium.launch(
        headless = False,
        executable_path=CHROME_PATH,
        args = ["--disable-blink-features=AutomationControlled"]
    )

    # Each restart gets a slightly randomized Chrome version so the
    # user agent string doesn't look like a bot cycling identically.
    chrome_version = random.randint(125, 128)

    # Changes browser context.
    context = await browser.new_context(
        user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36",
        viewport = {"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
        java_script_enabled = True
    )
    
    # Opens a new page in the browser.
    page = await context.new_page()

    # Patches browser properties to look less like automation.
    await Stealth().apply_stealth_async(page)

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

async def main():

    createTables()
    resetInProgress()
    
    # Start up the Playwright engine, call it playwright (as playwright),
    # and shut engine down when code finishes. This stops memory leaks.
    async with async_playwright() as playwright:
        
        print("Launching 25 unified sessions...")

        # One shared VPNRotator instance passed to all sessions.
        # Shared so all sessions coordinate on the same lock and rotation count.
        rotator = VPNRotator()

        # This is a list comprehension, a compact way of building a list.
        # tasks is a list of the function calls.
        tasks = [
            # Schedules each runSession call to run concurrently.
            asyncio.create_task(runSession(playwright, config, rotator))
            for config in SESSION_CONFIGS
        ]

        # asyncio.gather() wait for all 3 tasks to finish and collects their
        # retunn values in a list called summaries. return_exceptions = True means
        # if one fails it still waits for the other two instead of cancelling.
        # The *tasks syntax is called unpacking — instead of passing the list itself, 
        # it unpacks the list and passes each item as a separate argument. 
        # So if tasks has 3 items, *tasks is the same as writing tasks[0], tasks[1], tasks[2].
        summaries = await asyncio.gather(*tasks, return_exceptions = True)

        print("\n===== FINAL SUMMARY =====")
        total_processed = 0
        total_failed = 0
        total_results = 0

        # For all the session summaries it prints their info.
        for summary in summaries:
            # If a session return an exception object instead of a stats dictionary.
            if isinstance(summary, Exception):
                print(f"A session crashed with unhandled exception: {summary}")
                continue
            # Prints summary and calculates total processed, failed, and results.
            print(f"{summary['label']}: {summary['processed']} meets processed, "
                  f"{summary['failed']} failed, "
                  f"{summary['results_saved']} results saved")
            total_processed += summary["processed"]
            total_failed += summary["failed"]
            total_results += summary["results_saved"]

        print(f"TOTAL: {total_processed} meets processed, "
              f"{total_failed} failed, "
              f"{total_results} results saved")
        print("=========================")

        countRows()

if __name__ == "__main__":
    asyncio.run(main())