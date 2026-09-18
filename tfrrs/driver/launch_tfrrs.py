# Project: xc-predictor
# File:    tfrrs/driver/launch_tfrrs.py
# Purpose: The runnable entry point for the TFRRS scrape. Owns session setup
#          (Playwright page for fetch_tf tier 3), resets stale in-progress claims,
#          builds the meet-URL resolver, and hands off to runDrain. The anet
#          launcher's job, scoped to TFRRS.

import os
import sys
sys.path.insert(0, "scripts")

from database import getConn
from chrome_path import chromePath
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
    # ⚠ REAL CHROME, LIKE THE anet LAUNCHER. This used to call
    #   pw.chromium.launch(headless=False) with no executable_path, i.e.
    #   Playwright's bundled chromium -- absent on a box that never ran
    #   `playwright install`, and fingerprinted by Cloudflare on one that did.
    browser = pw.chromium.launch(headless=False,
                                 executable_path=chromePath())
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
# ★ SAY WHAT THERE IS TO DO BEFORE OPENING A BROWSER, the same lesson the
#   anet launcher learned: six sessions reporting "0 processed" over an empty
#   queue is indistinguishable from a broken scraper.
def _printQueueDue():
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sport, scraped, count(*)
                FROM   meet_queue WHERE source = 'tfrrs'
                GROUP  BY sport, scraped ORDER BY sport, scraped
            """)
            rows = cur.fetchall()
        conn.rollback()
    names = {0: "due", 1: "done", 2: "failed", 3: "in-progress", 4: "not a meet"}
    # ! IN RETRY MODE THE WORK IS STATES 2 AND 3, not 0 -- the claim takes
    #   them directly, so counting state 0 would report "nothing to do" on a
    #   queue full of failures.
    wanted = ((2, 3) if os.environ.get("TFRRS_RETRY_FAILED", "")
              not in ("", "0", "false") else (0,))
    due = 0
    print("[queue] tfrrs meet_queue:", flush=True)
    for sport, state, n in rows:
        if state in wanted:
            due += n
        print(f"[queue]   {sport}  {names.get(state, state):<12} {n:,}")
    return due


def _prepareQueue():
    """Report the queue, seed it if it has nothing to do, report again.

    ★ THE LAUNCHER OWNS THIS (owner, 2026-09-18: "do I need to manually run
      prefill for tfrrs or can I just run the launch_tfrrs"). Just run the
      launcher. TFRRS_NO_SEED=1 drains the queue exactly as it stands.

    ! THE SEED IS IDEMPOTENT AND ITS CEILING COMES FROM THE DATA. A fixed
      100,000 stopped covering new meets the moment TFRRS passed it, and tfrrs
      ids are already near 96,000 -- so re-running the prefill added nothing
      and the queue stayed empty. prefill.ceilingFor() reads the highest id
      anything has ever seen and seeds past it.
    """
    from queue_meets import seedAll, dueCounts

    # ★ RETRY-ONLY MODE, the same knob as the anet launcher's
    #   ANET_RETRY_FAILED (owner, 2026-09-18: "do the failed ones without the
    #   forwards pass and stuff"). Resets failed and stranded claims, seeds
    #   nothing, and run_tfrrs ends the run when the queue drains.
    if os.environ.get("TFRRS_RETRY_FAILED", "") not in ("", "0", "false"):
        print("[queue] TFRRS_RETRY_FAILED=1 -- failed and stranded meets "
              "only. Nothing seeded, nothing reset, no forward walk.",
              flush=True)
    elif os.environ.get("TFRRS_NO_SEED", "") not in ("", "0", "false"):
        print("[queue] TFRRS_NO_SEED=1 -- draining the queue as it stands.",
              flush=True)
    else:
        print("[queue] seeding: forward from the last real id per sport, plus "
              "recent meets with no results, plus the scheduled ones",
              flush=True)
        with getConn() as conn:
            seedAll(conn, source="tfrrs", write=True,
                    ahead=int(os.environ.get("TFRRS_SEED_AHEAD", 500)),
                    recent_days=int(
                        os.environ.get("TFRRS_RECENT_DAYS", 120)))

    due = _printQueueDue()
    if not due:
        if os.environ.get("TFRRS_RETRY_FAILED", "") not in ("", "0", "false"):
            print("[queue] nothing failed -- there is nothing to retry. Drop "
                  "TFRRS_RETRY_FAILED to scrape forward.", flush=True)
            sys.exit(0)
        print("[queue] ⚠ NOTHING IS DUE even after seeding. Either every id "
              "up to each sport's frontier is done, or the frontier is wrong. "
              "Look at it with: python scripts/queue_meets.py --source tfrrs")
        sys.exit(1)
    print(f"[queue] {due:,} due", flush=True)



# ★★ THE VENUE NAMES, AT THE END OF EVERY SCRAPE (owner, 2026-09-18: "did we
#    ever fix the scraper not getting venue name (also when it does get the
#    venue name does it update it for everything there)").
#
#    The scraper DOES capture it -- saveMeetTFMeta writes venue_name into
#    meets_tf_meta. But meets_tf, which is what the engine labels courses with,
#    is only filled by database.backfillMeetsTFVenueNames, and that was called
#    by exactly one MANUAL script. So every scrape left the names sitting in
#    meets_tf_meta and the engine went on printing a location id as a venue --
#    the same "written, wired to nothing" failure that function's own comment
#    records about itself.
#
# ! PASS 1 ONLY, and that is the point. Pass 1 joins each meet to its OWN
#   meets_tf_meta row, which is exactly where a just-scraped meet's name is: a
#   keyed, chunked, cheap join. Passes 2 and 3 aggregate all of meets_tf to
#   spread a name across a location or a coordinate pair, which is a real job
#   and stays an occasional one (scripts/backfill_tf_venues.py).
def _fillVenueNames(label="[venues]"):
    """Fill meets_tf.venue_name from each meet's own meta row. Never fatal: a
    scrape that worked must not be reported as failed because a tidy-up did
    not."""
    try:
        from database import getConn, backfillMeetsTFVenueNames
        with getConn() as conn:
            got = backfillMeetsTFVenueNames(conn, verbose=False, passes=(1,))
            print(f"{label} meets_tf.venue_name filled from each meet's own "
                  f"meta row: {got[0]:,}", flush=True)
            print(f"{label} to spread a name across a whole location or "
                  f"coordinate, run scripts/backfill_tf_venues.py", flush=True)
    except Exception as exc:                          # noqa: BLE001
        print(f"{label} venue-name fill skipped ({type(exc).__name__}: {exc})",
              flush=True)


async def main():
    # Stale-claim reset is blocking psycopg2 -> run it off the event loop.
    # ⚠ NOT IN RETRY MODE. This flips 3 -> 0, and a retry claims 2 and 3
    #   themselves -- so resetting first would quietly move every stranded
    #   claim out of the set the run is supposed to be working on.
    if os.environ.get("TFRRS_RETRY_FAILED", "") not in ("", "0", "false"):
        n_reset = 0
        print("[queue] retry mode: leaving stranded claims at state 3 so the "
              "retry can claim them.", flush=True)
    else:
        n_reset = await asyncio.to_thread(_resetStaleClaimsSync)
    print(f"reset {n_reset} stale TFRRS claims", flush=True)

    await asyncio.to_thread(_prepareQueue)

    # The shared rotator — coordinates VPN rotation across all sessions.
    rotator = VPNRotator()

    # Async Playwright: each session gets its OWN page from this one browser.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False,
                                           executable_path=chromePath())

        # make_page is now ASYNC (browser.new_page() is awaitable in the async
        # API). runDrain awaits it once per session.
        async def make_page():
            return await browser.new_page()

        await runDrain(make_page, _urlFor, rotator)

    _fillVenueNames("[tfrrs venues]")
    print("TFRRS drain complete", flush=True)

if __name__ == "__main__":
    asyncio.run(main())