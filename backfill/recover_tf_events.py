# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Backfill
# Date: 6/9/2026
# File Title: recover_tf_events.py
# Purpose: Reads failed TF event/div combos from tf_recovery_queue and
#          re-scrapes them using getMeetDataTF + getMeetResultsTF.
#          Groups events by meet_id so we only call getMeetDataTF once
#          per meet (one page navigation = one JWT token for all events
#          in that meet).
#
# System design:
#   1. Fetch a batch of unscraped events from tf_recovery_queue
#   2. Group them by meet_id
#   3. For each meet: call getMeetDataTF once to get the JWT token
#   4. For each failed event in that meet: call getMeetResultsTF
#   5. Save results and mark event done/failed in tf_recovery_queue
#   6. Repeat until queue is empty

import sys
import os
import asyncio
import math
from collections import defaultdict
 
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts'))
 
from playwright.async_api import async_playwright
from database import (
    initPool, closePool, getConn,
    getUnscrapedRecoveryEvents, markRecoveryEventScraped,
    logScrapedEventsTFBulk, countRecoveryRemaining
)
from scraper import getMeetDataTF, CloudflareException, EVENT_ID_TO_SHORT
from scrape_results import _collectTFEventDiv, _saveTFMeet
from launcher import restartBrowser, SESSION_CONFIGS

# ── Config ────────────────────────────────────────────────────────────────────

BATCH_SIZE = 100
N_SESSIONS = 5

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    initPool()

    try:
        # Bridges into async, creates an event loop and runs _runRecovery inside it.
        asyncio.run(_runRecovery())
    finally:
        closePool()

# _runRecovery
# Purpose: Top-level asynyc loop. Fetches batches of failed events,
#          groups them by meets, and dispactches parallel sessions to scrape them.
# Arguments: None.
# Output: None.
async def _runRecovery():

    print("[Recovery] Starting TF event recovery...")
    
    # async with means playwright cleans up automatically once it's over.
    async with async_playwright() as playwright:

        while True:
 
            # Fetch a batch — getUnscrapedRecoveryEvents marks them
            # in-progress (scraped=3) atomically so no two sessions
            # can grab the same event.
            events = getUnscrapedRecoveryEvents(limit=BATCH_SIZE)
 
            if not events:
                print("[Recovery] Queue empty — done.")
                break
 
            # Group flat list into meet_id → [(event_short, div_id), ...]
            # so we navigate to each meet only once for the JWT.
            grouped = _groupByMeet(events)
 
            print(f"[Recovery] Processing {len(events)} events "
                  f"across {len(grouped)} meets...")
 
            meet_ids = list(grouped.keys())
            slices = _splitIntoSlices(meet_ids, N_SESSIONS)
 
            # Run one browser session per slice concurrently.
            # asyncio.gather waits for all sessions to finish before
            # moving on to the next batch.
            await asyncio.gather(*[
                _runSession(playwright, grouped, slice_, i)
                for i, slice_ in enumerate(slices)
                if slice_
            ])
 
            remaining = countRecoveryRemaining()
            print(f"[Recovery] Batch done. {remaining:,} events still in queue.")

# _groupByMeet
# Purpose: Converts flat (meet_id, event_short, div_id) tuples into
#          a dict keyed by meet_id so we only scrape each meet once.
# Arguments:
#           events: list of (meet_id, event_short, div_id) tuples.
# Output: dict[int, list[tuple[str, int]]]
def _groupByMeet(events: list) -> dict:

    # Makes a dictionary with a list as the values.
    grouped = defaultdict(list)

    # Groups up the events and div based on meet_id. meet_id is the key,
    # list of (event_short, div_id) tuples are the values.
    for meet_id, event_short, div_id in events:
        grouped[meet_id].append((event_short, div_id))

    return grouped

# _splitIntoSlices
# Purpose: Splits meet_ids into N roughly equal slices for parallel sessions.
#          Uses ceiling division so no meet_id gets left out.
# Arguments:
#           meet_ids: list of meet IDs.
#           n: number of slices.
# Output: List of lists.
def _splitIntoSlices(meet_ids: list, n: int) -> list:
 
    # -(-a // b) is the ceiling division trick in Python.
    # e.g. 10 meets, 3 sessions → ceil(10/3) = 4 → slices of 4, 4, 2
    # Ceiling division rounds up.
    size = math.ceil(len(meet_ids) / n)
    return [meet_ids[i:i + size] for i in range(0, len(meet_ids), size)]

# ── Session logic ─────────────────────────────────────────────────────────────

# _runSession
# Purpose: Launches one browser session using restartBrowser from launcher.py
#          and processes its slice of meets. Uses SESSION_CONFIGS so each
#          session gets a different user agent and viewport, same as the
#          main scraper.
# Arguments:
#           playwright: Playwright instance.
#           grouped: dict of meet_id → [(event_short, div_id), ...].
#           meet_ids: this session's slice of meet IDs.
#           session_index: index into SESSION_CONFIGS for this session.
# Output: None.
async def _runSession(playwright, grouped: dict, meet_ids: list, session_index: int):

    # Reuse SESSION_CONFIGS from launcher.py so recovery sessions look
    # identical to main scraper sessions to Cloudflare.
    config = SESSION_CONFIGS[]

    # restartBrowser handles Chrome launch, stealth, webdriver patch,
    # and initial navigation to athletic.net.
    browser, page = await restartBrowser(playwright, None, config, proxy_index=0)
 
    try:
        for meet_id in meet_ids:
            await _processMeet(page, meet_id, grouped[meet_id])
    finally:
        # Always close browser even if an exception fires mid-meet.
        await browser.close()

# ── Meet logic ────────────────────────────────────────────────────────────────

# _processMeet
# Purpose: Handles one meet — fetches JWT, reconstructs events_dict and
#          event_divs from meets_tf, collects results, saves, then marks
#          each event done or failed in tf_recovery_queue.
# Arguments:
#           page: Playwright page.
#           meet_id: athletic.net meet ID.
#           events: list of (event_short, div_id) tuples to recover.
# Output: None.
async def _processMeet(page, meet_id: int, events: list):

    # Step 1: get JWT — one page navigation for all events in this meet.
    meet_info, jwt_token = await _fetchMeetJWT(page, meet_id)
 
    if not meet_info or not jwt_token:
        print(f"[Recovery] Could not get JWT for meet {meet_id} "
              f"— marking {len(events)} events failed")
        _markAllFailed(meet_id, events)
        return
    
    # Step 2: reconstruct events_dict and event_divs from meets_tf so
    # we can pass them to _collectTFEventDiv unchanged.
    events_dict, event_divs = _reconstructEventData(meet_id, events)

    if not events_dict or not event_divs:
        print(f"[Recovery] Could not reconstruct event data for meet {meet_id}")
        _markAllFailed(meet_id, events)
        return

    # Step 3: collect results using the existing scraper helper.
    # Same collector lists pattern as scrapeMeetTF.
    meets_to_save    = []
    athletes_to_save = []
    results_to_save  = []
 
    label = f"[Recovery meet {meet_id}]"
 
    for event_div in event_divs:
        try:
            await _collectTFEventDiv(
                page, meet_id, meet_info, event_div, events_dict,
                jwt_token, label,
                meets_to_save, athletes_to_save, results_to_save
            )
        except CloudflareException:
            raise
        except Exception:
            continue

    # Step 4: save everything in one transaction using existing helper.
    _saveTFMeet(meet_id, label, meets_to_save, athletes_to_save, results_to_save)
 
    # Step 5: mark each event done in tf_recovery_queue and log to
    # tf_scraped_events so future recovery runs don't re-queue them.
    _markEventsComplete(meet_id, events, results_to_save)

# _fetchMeetJWT
# Purpose: Calls getMeetDataTF to get JWT token for a meet.
# Arguments:
#           page: Playwright page.
#           meet_id: athletic.net meet ID.
# Output: Tuple of (meet_info dict, jwt_token str). ({}, "") on failure.
async def _fetchMeetJWT(page, meet_id: int):
 
    try:
        meet_info, _, _ = await getMeetDataTF(page, meet_id)
        jwt_token = meet_info.get("jwtMeet", "")
        return meet_info, jwt_token
    except CloudflareException:
        print(f"[Recovery] Cloudflare block fetching JWT for meet {meet_id}")
        return {}, ""
    except Exception as e:
        print(f"[Recovery] JWT fetch failed for meet {meet_id}: {e}")
        return {}, ""
    
# _reconstructEventData
# Purpose: Queries meets_tf to rebuild events_dict and event_divs in the
#          exact format _collectTFEventDiv expects. We need this because
#          tf_recovery_queue only stores (event_short, div_id) — not
#          event_id or gender — so we look those up from meets_tf.
# Arguments:
#           meet_id: athletic.net meet ID.
#           events: list of (event_short, div_id) tuples from tf_recovery_queue.
# Output: Tuple of (events_dict, event_divs).
#         events_dict: {event_id: {"event_short": str, "gender": str}}
#         event_divs:  [{"e": event_id, "d": div_id}, ...]
def _reconstructEventData(meet_id: int, events: list) -> tuple:
 
    # Build a set of (event_short, div_id) pairs for fast lookup.
    needed = {(event_short, div_id) for event_short, div_id in events}
 
    try:
        with getConn() as conn:
            cursor = conn.cursor()
 
            # Fetch event_id for every (event_short, div_id) combo
            # we need to recover from this meet.
            placeholders = ",".join(["%s"] * len(needed))
            cursor.execute(f"""
                SELECT event_id, event_short, div_id
                FROM meets_tf
                WHERE meet_id = %s
                -- Dynamically builds the right number of slots for
                -- the placeholders tuple using the tuple size. Tells
                -- Postgres to only give us rows where event_short and div_id
                -- match the needed events and division.
                AND (event_short, div_id) IN ({placeholders})
            """, [meet_id] + list(needed))
 
            rows = cursor.fetchall()
 
    except Exception as e:
        print(f"[Recovery] meets_tf lookup failed for meet {meet_id}: {e}")
        return {}, []
 
    events_dict = {}
    event_divs  = []
 
    for event_id, event_short, div_id in rows:
 
        # Look up gender from the hardcoded EVENT_ID_TO_SHORT mapping.
        mapping = EVENT_ID_TO_SHORT.get(event_id)
        if not mapping:
            continue
 
        _, gender = mapping
 
        # events_dict format matches what getMeetDataTF returns.
        events_dict[event_id] = {
            "event_short": event_short,
            "gender":      gender
        }
 
        # event_divs format matches what scrapeMeetTF passes to
        # _collectTFEventDiv — {"e": event_id, "d": div_id}.
        event_divs.append({"e": event_id, "d": div_id})
 
    return events_dict, event_divs
    
# _markEventsComplete
# Purpose: Marks each recovered event done or failed in tf_recovery_queue
#          and logs successes to tf_scraped_events.
#          An event is successful if any results were saved for it —
#          determined by checking results_to_save for its event_short.
# Arguments:
#           meet_id: athletic.net meet ID.
#           events: list of (event_short, div_id) tuples we attempted.
#           results_to_save: collector list from _collectTFEventDiv.
# Output: None.
def _markEventsComplete(meet_id: int, events: list, results_to_save: list):
 
    # Build set of (event_short, div_id) combos that got results saved.
    succeeded = {
        (event_short, div_id)
        for _, _, div_id, _, event_short, _ in results_to_save
    }
 
    scraped_log = []
 
    for event_short, div_id in events:
        if (event_short, div_id) in succeeded:
            markRecoveryEventScraped(meet_id, event_short, div_id, status=1)
            scraped_log.append((meet_id, event_short, div_id))
        else:
            markRecoveryEventScraped(meet_id, event_short, div_id, status=2)
 
    # Log all successes to tf_scraped_events in one query so future
    # find_failed_tf_events.py runs don't re-queue them.
    if scraped_log:
        logScrapedEventsTFBulk(scraped_log)
    
# _markAllFailed
# Purpose: Marks every event in a meet as failed (status=2) when the
#          JWT fetch or event data reconstruction fails entirely.
# Arguments:
#           meet_id: athletic.net meet ID.
#           events: list of (event_short, div_id) tuples.
# Output: None.
def _markAllFailed(meet_id: int, events: list):
 
    for event_short, div_id in events:
        markRecoveryEventScraped(meet_id, event_short, div_id, status=2)
 
 
if __name__ == "__main__":
    main()
 
