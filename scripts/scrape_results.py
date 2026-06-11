# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 5/30/2026
# File Title: scrape_results.py
# Purpose: Scrapes meet results from Athletic.net and saves them to a 
# SQLite db. Uses the meet_queue in the db to get ids for the meets.

import asyncio
import random
import sys

from playwright.async_api import async_playwright

from database import (
    createTables, getConn,
    saveMeet, saveAthletesBulk, saveResultsBulk,
    saveMeetTF, saveResultsTFBulk,
    logScrapedEventsTFBulk, updateMeetSport,
    markScraped, getUnscrapedMeets, countRemaining, countRows,
)
from scraper import (
    getMeetData, getMeetResults,
    getMeetDataTF, getMeetResultsTF,
    CloudflareException
)
 
sys.path.insert(0, "engine")
from normalize_distance import EVENT_DISTANCES_TF

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# parseMeetDate
# Strips the time portion from an ISO datetime string.
# Arguments:
#           meet_info: meet-level dict from getMeetData or getMeetDataTF.
# Output: Date string "YYYY-MM-DD", or empty string if no date present.
def parseMeetDate(meet_info: dict) -> str:
    raw = meet_info.get("MeetDate", "")
    return raw.split("T")[0] if raw else ""

# isRelayEvent
# Purpose: Returns 1 if the event short code is a relay event, 0 if not.
#          Relay events are excluded from individual speed rating calculations.
# Arguments:
#           event_short: event code string from EVENT_ID_TO_SHORT mapping.
# Output: 1 if relay, 0 if individual.
def isRelayEvent(event_short: str) -> int:
    RELAY_EVENTS = {
        "4x100m", "4x200m", "4x400m", "4x800m", "4x1600m",
        "distmed12,4,8,16", "sprintmed1124", "sprintmed2248",
        "100shuttleh", "110shuttleh"
    }
    return 1 if event_short in RELAY_EVENTS else 0

# isSentinelTime
# Purpose: Returns True if a SortInt value is a sentinel code meaning
#          DNS (did not start), DNF (did not finish), or DQ (disqualified).
#          Athletic.net uses SortInt >= 10000000 for these — not real times.
# Arguments:
#           sort_int: raw SortInt value from the API result dict.
# Output: True if sentinel, False if real time.
def isSentinelTime(sort_int) -> bool:
    return sort_int is None or sort_int >= 10000000

# buildAthleteDict
# Purpose: Extracts athlete fields from a raw result dict into the format
#          saveAthletesBulk expects. Centralizes field name mapping so if
#          the API changes we fix it in one place.
# Arguments:
#           result: raw result dict from getMeetResults or getMeetResultsTF.
# Output: Dict with AthleteID, FirstName, LastName, Gender, SchoolName.
def buildAthleteDict(result: dict) -> dict:
    return {
        "AthleteID":  result.get("AthleteID"),
        "FirstName":  result.get("FirstName"),
        "LastName":   result.get("LastName"),
        "Gender":     result.get("Gender"),
        "SchoolName": result.get("SchoolName")

    }

# ─────────────────────────────────────────────────────────────────────────────
# XC scraping
# ─────────────────────────────────────────────────────────────────────────────

# _fetchXCDivisionResults
# Purpose: Fetches results for one XC division, with retry logic for rate
#          limits.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           div_id: division ID for this division.
#           jwt_token: auth token from meet_info.
#           label: session label for logging.
# Output: List of result dicts, or raises on unrecoverable failure.
async def _fetchXCDivisionResults(page, meet_id: int, div_id: int,
                                jwt_token: list, label: str) -> list:
    
    max_retries = 3

    # Fetches the results for the division, retrying on rate limit.
    for attempt in range(max_retries):

        # Fetches the results for the division
        try:
            results = await getMeetResults(page, meet_id, div_id, jwt_token)
            break  # Success — exit the retry loop
                
        # CloudflareException must come before Exception — Python matches
        # except clauses top to bottom and stops at the first match.
        # If CloudflareException were caught by Exception it would never
        # bubble up to the launcher for VPN rotation.
        except (CloudflareException, asyncio.CancelledError):
            # Re-raise so it bubbles up through scrapeMeet to runSession,
            # which handles the VPN rotation and browser restart.
            raise

        # Any other exception is a complete fail.
        except Exception as e:
            print(f"{label} [!] getMeetResults failed for div {div_id}: {e}")
            return -1
        
    return results

# _collectXCDivision
# Purpose: Collects meet, athlete, and result data for one XC division
#          into the collector lists. Does not touch the DB.
# Arguments:
#           meet_info: meet-level dict from getMeetData.
#           div: division dict from the divisions list.
#           results: list of result dicts from _fetchXCDivisionResults.
#           collected_meets: list to append (meet_info, div) tuple to.
#           collected_athletes: list to append athlete dicts to.
#           collected_results: list to append (result, meet_info) tuples to.
# Output: None. Mutates the collector lists in place.
def _collectXCDivision(meet_info: dict, div: dict, results: list,
                        collected_meets: list, collected_athletes: list,
                        collected_results: list):
    
    # Collect the meet row for this division.
    collected_meets.append((meet_info, div))
    
    # Collect all results and athletes for this division.
    for r in results:
        
        if not r.get("AthleteID") or not r.get("IDResult"):
            continue

        collected_athletes.append(r)
        collected_results.append((r, meet_info))

# _saveXCMeet
# Purpose: Saves all collected XC data for one meet in a single DB
#          transaction.
# Arguments:
#           meet_id: athletic.net meet ID (for error logging only).
#           label: session label for logging.
#           collected_meets: list of (meet_info, div) tuples.
#           collected_athletes: list of athlete dicts.
#           collected_results: list of (result, meet_info) tuples.
# Output: Number of results saved, or -1 on failure.
def _saveXCMeet(meet_id: int, label: str, collected_meets: list,
                collected_athletes: list, collected_results: list) -> int:

    try:
        with getConn() as conn:

            for meet_info_item, div in collected_meets:
                saveMeet(conn, meet_info_item, div)

            saveAthletesBulk(conn, collected_athletes)
            saveResultsBulk(conn, collected_results)

            conn.commit()
        return len(collected_results)
    
    except Exception as e:
        print(f"{label} [!] Save failed for meet {meet_id}: {e}")
        return -1

# scrapeXCDivisions
# Purpose: Collects and saves all XC divisions for one meet.
#          Uses collect-then-save — if any division fetch fails, nothing
#          is written to the DB and the meet is marked failed.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           meet_info: dict from getMeetData with meet-level info and JWT.
#           divisions: list of division dicts from getMeetData.
#           label: session label for logging.
# Output: Total results saved, or -1 on failure.
async def scrapeXCDivisions(page, meet_id: int, meet_info: dict,
                            divisions: list, label: str) -> int:

    # Collect all data before saving anything. If any division fails we
    # discard everything and return -1 so the meet gets marked failed.

    # collected_meets is a list of (meet_info, div) tuples, one per division.
    collected_meets = []

    # collected_results is a list of (result, meet_info) tuples, one per result.
    collected_athletes = []

    # collected_athletes is a list of athlete dicts, one per athlete.
    collected_results = []

    # For each division in the meet we collect the meet, and then fetch and
    # collect every result. We save
    # the meet for each division, not meet.
    for div in divisions:

        # Gets div_id for div in divisions dictionary, and the
        # jwt_token for each meet for authentication.
        div_id = div.get("IDMeetDiv")
        jwt_token = meet_info.get("jwtMeet", "")

        if not div_id:
            continue

        # Short pause between divisions to avoid detection.
        await asyncio.sleep(random.uniform(0.3, 0.6))

        # Fetches the results for the division.
        try:
            results = await _fetchXCDivisionResults(
                page, meet_id, div_id, jwt_token, label
            )
        except Exception:
            # _fetchXCDivisionResults already printed the error.
            # Any failure on any division fails the whole meet.
            return -1
        
        if results == -1:
            return -1
        
        # Collects meet, athlete, and result data for one div.
        _collectXCDivision(
            meet_info, div, results,
            collected_meets, collected_athletes, collected_results
        )
    
    # Saves meeet in one db trip.
    return _saveXCMeet(
        meet_id, label,
        collected_meets, collected_athletes, collected_results
    )


# ─────────────────────────────────────────────────────────────────────────────
# TF scraping
# ─────────────────────────────────────────────────────────────────────────────

# _validateTFMeetData
# Purpose: Checks that getMeetDataTF returned everything we need before
#          we start looping over events.
# Arguments:
#           meet_info: dict from getMeetDataTF.
#           events_dict: event ID → event info mapping.
#           event_divs: list of event/div combos with results.
#           meet_id: for logging.
#           label: session label for logging.
# Output: True if valid, False if we should abort.
def _validateTFMeetData(meet_info: dict, events_dict: dict,
                         event_divs: list, meet_id: int, label: str) -> bool:
    
    if not meet_info.get("ID"):
        print(f"{label} [!] No TF meet data for meet {meet_id}, skipping")
        return False
    
    if not event_divs:
        return False
    
    if not events_dict:
        print(f"{label} [!] No events dict for meet {meet_id}, skipping")
        return False
    
    return True

# _collectTFEventDiv
# Purpose: Fetches and collects data from one TF event/division combo.
#          Does not touch the DB.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           meet_info: dict from getMeetDataTF.
#           event_div: dict with "e" (event_id) and "d" (div_id) keys.
#           events_dict: event ID → event info mapping.
#           jwt_token: auth token from meet_info.
#           label: session label for logging.
#           meets_to_save: collector list for meet/event/div metadata.
#           athletes_to_save: collector list for athlete dicts.
#           results_to_save: collector list for result tuples.
# Output: True if collected, False if skipped, raises on fatal error.
async def _collectTFEventDiv(page, meet_id: int, meet_info: dict,
                              event_div: dict, events_dict: dict,
                              jwt_token: str, label: str,
                              meets_to_save: list, athletes_to_save: list,
                              results_to_save: list) -> bool:
    
    event_id = event_div.get("e")
    div_id   = event_div.get("d")

    event_info = events_dict.get(event_id)
    if event_info is None:
        return False
    
    event_short     = event_info["event_short"]
    gender          = event_info["gender"]
    # Converts event name to distance in meters
    distance_meters = EVENT_DISTANCES_TF.get(event_short)
    is_relay        = isRelayEvent(event_short)

    # Gets a meet's results
    try:
        results = await getMeetResultsTF(
            page, meet_id, div_id, event_short, gender, jwt_token
        )

    except CloudflareException:
        raise

    except Exception as e:
        # Single event/div failure — skip it, don't fail the whole meet.
        # TF meets have many event/div combos so one bad one isn't fatal.
        print(f"{label} [!] getMeetResultsTF failed for meet {meet_id} "
              f"event {event_short} div {div_id}: {e}")
        # Still record this event in meets_tf with distance_meters = -1
        # so the recovery script can find it via the gap between
        # meets_tf and tf_scraped_events.
        meets_to_save.append((meet_info, div_id, event_id, event_short, -1))
        return False
    
    # Saves meet for later bulk upload.
    meets_to_save.append((meet_info, div_id, event_id, event_short, distance_meters))

    # Saves all results in the meet and the athelete they are attatched to for 
    # later bulk upload
    for result in results:

        if not isinstance(result, dict):
            continue
        
        # Skips sentinel times, which are DNF/DQ/DNS.
        sort_int = result.get("SortInt", 0)
        if isSentinelTime(sort_int):
            continue
        
        athletes_to_save.append(buildAthleteDict(result))
        results_to_save.append((result, meet_info, div_id, event_id, event_short, is_relay))
 
    return True


# _buildScrapedEventsList
# Purpose: Builds the deduplicated list of (meet_id, event_short, div_id)
#          tuples for logScrapedEventsTFBulk from the results_to_save list, which
#          logs the events that have been scraped from TF in one DB connection.
# Arguments:
#           results_to_save: list of (result, meet_info, div_id, event_id,
#                            event_short, is_relay) tuples.
# Output: List of (meet_id, event_short, div_id) tuples, deduplicated.
def _buildScrapedEventsList(results_to_save: list) -> list:

    # Log every successfully scraped event/div combo.
    # We do this after saving results — if saveResultTF crashed partway
    # through we don't want to mark those events as successfully scraped.
    # We use a set to deduplicate because results_to_save has one row per
    # result (many per event), but we only want to log each event once.
    seen = set()
    events = []

    for _, meet_info, div_id, event_id, event_short, _ in results_to_save:

        # (meet_id, event_short, div_id) is the unique key — same as the
        # PRIMARY KEY in tf_scraped_events.
        key = (meet_info.get("ID"), event_short, div_id)

        if key not in seen:
            seen.add(key)
            events.append(key)

    return events

# _saveTFMeet
# Purpose: Saves all collected TF data for one meet in a single DB
#          transaction, then bulk-logs the scraped events for recovery.
# Arguments:
#           meet_id: athletic.net meet ID (for error logging only).
#           label: session label for logging.
#           meets_to_save: list of meet/event/div metadata tuples.
#           athletes_to_save: list of athlete dicts.
#           results_to_save: list of result tuples.
# Output: Number of results saved, or -1 on failure.
def _saveTFMeet(meet_id: int, label: str, meets_to_save: list,
                athletes_to_save: list, results_to_save: list) -> int:
    
    try:
        # Save meet info, athletes info, and results info for TF into the db.
        with getConn() as conn:

            for meet_info, div_id, event_id, event_short, distance_meters in meets_to_save:
                saveMeetTF(conn, meet_info, div_id, event_id, event_short, distance_meters)

            saveAthletesBulk(conn, athletes_to_save)
            saveResultsTFBulk(conn, results_to_save)

            conn.commit()
        
        # Logs scraped events after the commit - we only want to
        # record events whose results acutally made it into the db.
        scraped_events = _buildScrapedEventsList(results_to_save)
        logScrapedEventsTFBulk(scraped_events)

        return len(results_to_save)
    
    except Exception as e:
        print(f"{label} [!] Save failed for TF meet {meet_id}: {e}")
        return -1
    
# scrapeMeetTF
# Purpose: Scrapes all results from one TF meet. Gets the meet event/divs,
#          loops over all event/div combos, collects results, then saves
#          everything in one transaction.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           label: session label for logging e.g. "[Session 1]".
# Output: Total results saved, or -1 on failure, 0 if meet is empty.
async def scrapeMeetTF(page, meet_id: int, label: str) -> int:

    # Gets the meet data for the TF meet.
    try:
        meet_info, events_dict, event_divs = await getMeetDataTF(page, meet_id)
    except Exception as e:
        print(f"{label} [!] getMeetDataTF failed for meet {meet_id}: {e}")
        return -1
    
    # Return 0 for empty meets (not a failure), -1 for bad data.
    if not _validateTFMeetData(meet_info, events_dict, event_divs, meet_id, label):
        return 0 if not event_divs else -1
    
    # Cookies token for authenticating our requests.
    jwt_token  = meet_info.get("jwtMeet", "")

    # Collect all then save in bulk pattern
    meets_to_save  = []
    athletes_to_save = []
    results_to_save  = []

    for event_div in event_divs:
        try:
            success = await _collectTFEventDiv(
                page, meet_id, meet_info, event_div, events_dict,
                jwt_token, label,
                meets_to_save, athletes_to_save, results_to_save
            )

            if not success:
                event_id = event_div.get("e")
                div_id   = event_div.get("d")
                event_info = events_dict.get(event_id)
                if event_info:
                    meets_to_save.append((
                        meet_info, div_id, event_id,
                        event_info["event_short"], -1
                    ))

        except CloudflareException:
            # Fatal for the whole meet — bubble up.
            raise

        except Exception:
            # Non-fatal single event failure already logged in _collectTFEventDiv.
            continue

 
    return _saveTFMeet(
        meet_id, label,
        meets_to_save, athletes_to_save, results_to_save
    )

# ─────────────────────────────────────────────────────────────────────────────
# Unified entry point
# ─────────────────────────────────────────────────────────────────────────────

# _attemptTF
# Purpose: Attempts to scrape a meet that is presumably TF.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           label: session label for logging.
# Output: Tuple of (n, detected_sport).
#         n: results saved, -1 on failure, -2 if skipped.
#         detected_sport: "TF"
async def _attemptTF(page, meet_id: int, label: str) -> tuple:
    try:
        n = await scrapeMeetTF(page, meet_id, label)
    except Exception as e:
        print(f"{label} [!] scrapeMeetTF failed for meet {meet_id}: {e}")
        return -1, "UNKNOWN"
    updateMeetSport(meet_id, "TF")
    return n, "TF"

# scrapeMeetUnified
# Purpose: Detects wether a meet is XC or TF, scrapes it with the
#          correct scraper, and update the queue sport tag.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           label: session label for logging.
# Output: Tuple of (n, detected_sport).
#         n: results saved, -1 on failure, -2 if skipped.
#         detected_sport: "XC", "TF", "SKIPPED", or "UNKNOWN".
async def scrapeMeetUnified(page, meet_id: int, label: str) -> tuple:

    # We always try XC first because:
    # 1. Most unscraped meets are TF but unknown XC meets are more valuable
    # 2. getMeetData already uses the XC URL so we're consistent with
    #    all existing scraped data
    # 3. If xcDivisions comes back non-empty, it's definitively XC —
    #    the API only returns XC divisions for XC meets

    try:
        meet_info, divisions = await getMeetData(page, meet_id)
    except Exception as e:
        print(f"{label} [!] getMeetData failed for meet {meet_id}: {e}")
        return -1, "UNKNOWN"
    
    # getMeetData returned something but no meet ID — probably track and
    # field then.
    if not meet_info.get("ID"):
        return await _attemptTF(page, meet_id, label)
    
    # Non-empty divisions means the XC API returned XC data — it's XC.
    if divisions:

        updateMeetSport(meet_id, "XC")

        # Scrape it as XC using the existing scrapeMeet logic.
        # We already have meet_info and divisions from the getMeetData call
        # above, so we pass them directly instead of calling getMeetData again.
        n = await scrapeXCDivisions(page, meet_id, meet_info, divisions, label)

        return n, "XC"
    
    # Valid meet ID but no divisions — not XC, try TF.
    return await _attemptTF(page, meet_id, label)


# scrapeMeet
# Purpose: Fetches meet data then scrapes all XC divisions.
# Arguments:
#           page: browser page we are fetching info from.
#           meet_id: id of the meet we are scraping.
#           label: session label for printed output. Shows what browser session
#                  is running this function.
# Output: Returns an integer showing how many results were saved.
async def scrapeMeet(page, meet_id: int, label: str) -> int:

    # try/except wraps the API call so that if anything fails, we can catch it.
    try:
        # Gets meet data, which is two dicts for the meet info and the
        # divisions. This includes the jwt_token and the div_id.
        meet_info, divisions = await getMeetData(page, meet_id)
    except Exception as e:
        # Exception is the error, we store it as e so we can print it out.
        print(f"{label} [!] getMeetData failed for meet {meet_id}: {e}")
        return -1
    
    # This catches if the API call succeeds but an empty object is returned.
    if not meet_info.get("ID"):
        print(f"{label} [!] No meet data returned for meet {meet_id}, skipping")
        return -1
    
    # Empty meet with no results.
    if not divisions:
        return 0
    
    return await scrapeXCDivisions(page, meet_id, meet_info, divisions, label)

# main
# Purpose: Scrapes the results from all the XC meets in our meet queue.
# Arguments: None.
# Output: None, but updates the results and athletes table in the db.
async def main():

    # Make sure the tables exist before we write to them.
    createTables()

    # Launches the browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless = False,
            args = ["--disable-blink-features=AutomationControlled"]
        )

        # Creates a fresh browser profile.
        context = await browser.new_context(
            # Tells website what browser and OS is making request. We fake
            # it to look more human.
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            # Browser window size. We set it to this size because this is
            # a common size, so less likely to be detected.
            viewport = {"width": 1280, "height": 800},
            java_script_enabled = True
        )

        # Opens a new tab.
        page = await context.new_page()

        # Runs a piece of JS on every page to make the browser look more human
        # by changing its properties.
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: => undefined})"
        )

        # Tracks the number of meets that were correctly processed and the failures.
        processed = 0
        failed = 0

        # Processes meet results in batches, 500 at a time until all meets
        # are scraped or skipped.
        while True:

            # Gets 500 unscraped meets from the db.
            batch = getUnscrapedMeets(limit = 500)

            if not batch:
                print("Queue empty - Phase 2 complete,")
                break
            
            # We scrape each XC meet in the 500 batch.
            for meet_id, sport in batch:
                # If sport is not XC it gets marked scraped and skipped
                if sport.upper() != "XC":
                    markScraped(meet_id, status = 1)
                    continue
                
                # Scrapes the meet results.
                n = await scrapeMeet(page, meet_id)

                # If scrapeMeet succeeded(n != -1) we marked it scraped with succeed
                # status 1. Otherwise we marked it unscraped with failure status
                # 2.
                if n >= 0:
                    markScraped(meet_id, status = 1)
                    processed += 1
                    print(f"[{processed}] Meet {meet_id}: {n} results saved | {countRemaining()} remaining")
                else:
                    markScraped(meet_id, status = 2)
                    failed += 1
                    print(f"[Fail #{failed}] Meet {meet_id} marked failed")

                await asyncio.sleep(random.uniform(0.3, 0.6))
            
                # Every 100 meets processed we reload athletic.net to avoid detection
                # and to get new JWT tokens to avoid them expiring.
                if processed % 100 == 0 and processed > 0:
                    await page.goto("https://www.athletic.net")
                    await page.wait_for_timeout(1000)
        
        # When queue is empty it prints the total number of rows 
        # (meets, divs processed).
        countRows()
        print(f"Done. Processed: {processed}, Failed: {failed}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

    