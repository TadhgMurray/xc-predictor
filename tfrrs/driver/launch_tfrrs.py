# Project: xc-predictor
# File:    tfrrs/driver/launch_tfrrs.py
# Purpose: The runnable entry point for the TFRRS scrape. Owns session setup
#          (Playwright page for fetch_tf tier 3), resets stale in-progress claims,
#          builds the meet-URL resolver, and hands off to runDrain. The anet
#          launcher's job, scoped to TFRRS.

import sys
sys.path.insert(0, "scripts")

from database import getConn
from run_tfrrs import runDrain
from playwright.sync_api import sync_playwright
import asyncio
from playwright.async_api import async_playwright
from vpn_rotation import VPNRotator

# _resetStaleClaims
# Purpose: Flip any meets left at scraped=3 (in-progress) from a prior crashed run
#          back to 0 (pending), so they're re-claimable. Run ONCE at startup,
#          mirroring the anet "reset claimed at the start of each run" rule.
# Arguments:
#           conn: open DB connection.
# Output:   none. Caller commits. (getConn does not auto-commit.)
def _resetStaleClaims(conn):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE meet_queue SET scraped = 0 WHERE scraped = 3 AND source = %s",
        ("tfrrs",),
    )
    return cursor.rowcount

# _resetStaleClaimsSync
# Purpose: The stale-claim reset, wrapped so it can run via asyncio.to_thread.
#          Uses your getConn context manager exactly as before — this is sync
#          code, run off the event loop.
# Output:   number of rows reset.
def _resetStaleClaimsSync():
    with getConn() as conn:
        n = _resetStaleClaims(conn)
        conn.commit()        # getConn does NOT auto-commit — explicit commit
    return n

# _urlFor
# Purpose: Resolve a (meet_id, sport) to the page URL the driver should fetch.
#          XC -> the meet results page; TF -> the meet LANDING page (the driver's
#          TF pipeline harvests the /m/ and /f/ compiled URLs off it). We hand
#          TFRRS a bare-id URL with a placeholder slug; the server 302s to the
#          real slug, and fetch_tf follows redirects.
# Arguments:
#           meet_id: the TFRRS meet id.
#           sport:   'XC' or 'TF'.
# Output:   the URL string to fetch.
def _urlFor(meet_id, sport):
    if sport == "XC":
        return f"https://www.tfrrs.org/results/xc/{meet_id}"
    return f"https://www.tfrrs.org/results/{meet_id}"

# _openPage
# Purpose: Launch a single headful Chromium page for fetch_tf's tier-3 fallback.
#          Headful because Cloudflare blocks headless (the anet lesson). Tiers 1-2
#          carry the load; this page only runs when both fail.
# Arguments: none.
# Output:   (playwright, browser, page) bundled so _closePage can tear all down.
def _openPage():

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False)   # headless is blocked by CF
    page = browser.new_page()
    return {"pw": pw, "browser": browser, "page": page}


# _closePage
# Purpose: Tear down everything _openPage started, in reverse order.
# Arguments:
#           handle: the dict from _openPage.
# Output:   none.
def _closePage(handle):
    handle["browser"].close()
    handle["pw"].stop()

# main  (ASYNC)
# Purpose: Run the TFRRS scrape: reset stale claims once, open an async browser,
#          build the rotator, and drain the queue with SESSION_COUNT concurrent
#          sessions + meets-based VPN rotation.
async def main():
    # Stale-claim reset is blocking psycopg2 -> run it off the event loop.
    n_reset = await asyncio.to_thread(_resetStaleClaimsSync)
    print(f"reset {n_reset} stale TFRRS claims", flush=True)

    # The shared rotator — coordinates VPN rotation across all sessions.
    rotator = VPNRotator()

    # Async Playwright: each session gets its OWN page from this one browser.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)

        # make_page is now ASYNC (browser.new_page() is awaitable in the async
        # API). runDrain awaits it once per session.
        async def make_page():
            return await browser.new_page()

        await runDrain(make_page, _urlFor, rotator)

    print("TFRRS drain complete", flush=True)

if __name__ == "__main__":
    asyncio.run(main())