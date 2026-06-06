# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 5/30/2026
# File Title: scrape_results.py
# Purpose: Scrapes meet results from Athletic.net and saves them to a 
# SQLite db. Uses the meet_queue in the db to get ids for the meets.

import asyncio
import random
import sqlite3
import sys
from playwright.async_api import async_playwright
from database import createTables, saveAthlete, saveMeet, saveResult, countRows, getConn, saveMeetTF, saveResultTF, logScrapedEventTF
from scraper import getMeetData, getMeetResults, getMeetDataTF, getMeetResultsTF, RateLimitException, CloudflareException

sys.path.insert(0, "engine")
from normalize_distance import EVENT_DISTANCES_TF

# getUnscrapedMeets
# Purpose: Queries the db for up to 500 meets that are unscraped (scraped = 0).
# Arguments:
#           limit: the limit for how many meets to return. Default is 500.
# Output: Returns a list of tuples containing up to 500 meets and what sport
# the meet was.
def getUnscrapedMeets(limit: int = 500) -> list[tuple[int, str]]:

    # Connects to the db file, creates a cursor object to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # RETURNING makes UPDATE return the rows it just changed, so we mark
    # and fetch in one single atomic operation with no gap between them.
    cursor.execute("""
        UPDATE meet_queue SET scraped = 3
        WHERE meet_id IN (
            SELECT meet_id FROM meet_queue
            WHERE scraped = 0
            LIMIT %s
        )
        RETURNING meet_id, sport
    """, (limit,))

    # Fetches all rows, returning a list of tuples with the meet ids and sport.                             
    rows = cursor.fetchall()
    
    conn.commit()
    conn.close()

    return rows

# countRemaining
# Purpose: returns the number of meets that are still unscraped.
# Arguments: none.
# Output: Retunrs the number of meets that are still unscraped.
def countRemaining() -> int:
    
    # Connects to the db file, creates a cursor object to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # Gets how many meets are still unscraped
    cursor.execute("SELECT COUNT(*) FROM meet_queue WHERE scraped = 0")

    # cursor.fetchone returns a tuple with the number of unscraped meets.
    # to get it we use fetchone at the first arg, as the tuple is this: (x,).
    n = cursor.fetchone()[0]

    conn.close()

    return n

# scrapeXCDivisions
# Purpose: Scrapes all divisions of an XC meet given already-fetched
#          meet_info and divisions. Separated from getMeetData so
#          scrapeMeetUnified can detect sport first without double-fetching.
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
    # collected_results is a list of (result, meet_info) tuples, one per result.
    # collected_athletes is a list of athlete dicts, one per athlete.
    collected_meets = []
    collected_athletes = []
    collected_results = []

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

    # For each division in the meet we save the meet, and then fetch and
    # save every results. Duplicates are skipped in database.py. We save
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

        # Fetches the results for the division, retrying on rate limit.
        max_retries = 3
        for attempt in range(max_retries):
            # Fetches the results for the division
            try:
                results = await getMeetResults(page, meet_id, div_id, jwt_token)
                break  # Success — exit the retry loop
            # Try for specific rate limiting exception. Wait 60s.
            except RateLimitException:
                if attempt < max_retries - 1:
                    print(f"{label} [429] Rate limited, waiting 60s before retry {attempt + 1}/{max_retries - 1}")
                    await asyncio.sleep(60)
                else:
                    print(f"{label} [!] Rate limited {max_retries} times on div {div_id}, marking meet failed")
                    return -1
            # Must come before generic Exception — if CloudflareException were
            # caught by except Exception it would never reach this block.
            # Python checks except clauses top to bottom and stops at the first match.
            except CloudflareException:
                # Re-raise so it bubbles up through scrapeMeet to runSession,
                # which handles the VPN rotation and browser restart.
                raise
            # Any other exception is a complete fail.
            except Exception as e:
                print(f"{label} [!] getMeetResults failed for div {div_id}: {e}")
                return -1
            
        # Collect the meet row for this division.
        collected_meets.append((meet_info, div))
        
        # Collect all results and athletes for this division.
        for r in results:
            if not r.get("AthleteID") or not r.get("IDResult"):
                continue
            collected_athletes.append(r)
            collected_results.append((r, meet_info))

    # All divisions succeeded. Now save everything.
    total_results = 0
    try:
        for meet_info_item, div in collected_meets:
            saveMeet(meet_info_item, div)
        for athlete in collected_athletes:
            saveAthlete(athlete)
        for result, meet_info_item in collected_results:
            saveResult(result, meet_info_item)
            total_results += 1
    except Exception as e:
        print(f"{label} [!] Save failed for meet {meet_id}: {e}")
        return -1

    return total_results

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

# scrapeMeetTF
# Purpose: Scrapes all results from one TF meet. Orchestrates the full
#          scrape — gets the meet plan, loops over all event/div combos,
#          fetches results for each, saves to DB.
#          Uses collect-then-save pattern — only saves if everything
#          succeeds, so partial data never gets written.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           label: session label for logging e.g. "[Session 1]".
# Output: Total number of results saved across all events, or -1 on failure.
async def scrapeMeetTF(page, meet_id: int, label: str) -> int:

    # ------------------------------------------------------------------ #
    # Step 1: get meet metadata and scrape plan                            #
    # ------------------------------------------------------------------ #

    try:
        meet_info, events_dict, event_divs = await getMeetDataTF(page, meet_id)
    except Exception as e:
        print(f"{label} [!] getMeetDataTF failed for meet {meet_id}: {e}")
        return -1

    # If no meet info came back, the meet doesn't exist or has no TF data.
    if not meet_info.get("ID"):
        print(f"{label} [!] No TF meet data for meet {meet_id}, skipping")
        return -1

    # If no event/div combinations have results, the meet is empty.
    # Return 0 — not a failure, just nothing to scrape.
    if not event_divs:
        return 0

    # If events_dict is empty, our dummy call to get the events lookup
    # failed. Can't scrape without knowing event short codes.
    if not events_dict:
        print(f"{label} [!] No events dict for meet {meet_id}, skipping")
        return -1

    jwt_token = meet_info.get("jwtMeet", "")

    # ------------------------------------------------------------------ #
    # Step 2: collect all results before saving anything                   #
    # ------------------------------------------------------------------ #

    # We use the collect-then-save pattern — build up all the data we
    # want to save first, then only write to the DB if everything succeeded.
    # This prevents partial data: if division 3 of 10 fails, we don't
    # save divisions 1 and 2 and mark the meet as done. Either the whole
    # meet saves or nothing does.

    # meets_to_save: list of (meet_info, div_id, event_id, event_short,
    #                         distance_meters) tuples — one per event/div combo.
    meets_to_save = []

    # athletes_to_save: list of athlete dicts to save to the athletes table.
    athletes_to_save = []

    # results_to_save: list of (result, meet_info, div_id, event_id,
    #                           event_short, is_relay) tuples.
    results_to_save = []

    # Loop over every event/division combination that has results.
    for event_div in event_divs:

        event_id = event_div.get("e")
        div_id = event_div.get("d")

        # Look up event details from the events dict we built in getMeetDataTF.
        # If the event_id isn't in the dict, we can't make the results call.
        event_info = events_dict.get(event_id)
        if event_info is None:
            # Unknown event ID — skip this combination.
            continue
        
        # Gets event name and gender.
        event_short = event_info["event_short"]
        gender = event_info["gender"]

        # Look up distance for this event from our hardcoded mapping.
        # None means it's a field event or short sprint — store but don't normalize.
        distance_meters = EVENT_DISTANCES_TF.get(event_short)

        # Determine if this is a relay event.
        # Athletic.net relay event shorts end in "R" e.g. "4x100R", "4x400R",
        # "DMR" (distance medley relay), "SMR" (sprint medley relay).
        is_relay = 1 if (
            event_short in ("4x100m", "4x200m", "4x400m", "4x800m", "4x1600m",
                            "distmed12,4,8,16", "sprintmed1124", "sprintmed2248",
                            "100shuttleh", "110shuttleh")
        ) else 0

        # Fetch results for this specific event/division/gender combo.
        try:
            results = await getMeetResultsTF(
                page, meet_id, div_id, event_short, gender, jwt_token
            )
        except RateLimitException:
            # Rate limited — bail on the whole meet, let the caller retry.
            print(f"{label} [!] Rate limited on meet {meet_id} event {event_short}")
            return -1
        except CloudflareException:
            # IP blocked — re-raise so the launcher can rotate VPN.
            raise
        except Exception as e:
            # Any other failure on a single event/div — skip it and continue.
            # Unlike XC where a failed division fails the whole meet, TF has
            # so many event/div combos that one failure shouldn't kill everything.
            print(f"{label} [!] getMeetResultsTF failed for meet {meet_id} "
                  f"event {event_short} div {div_id}: {e}")
            continue

        # Add the meet/event/div metadata to the save list.
        meets_to_save.append((meet_info, div_id, event_id, event_short, distance_meters))

        # Add each athlete and result to their save lists.
        for result in results:
            
            # Guard against malformed API responses — same issue as events loop.
            if not isinstance(result, dict):
                continue

            # Filter sentinel values — SortInt of 20000001 means DNS/DNF/DQ.
            sort_int = result.get("SortInt", 0)
            if sort_int >= 10000000:
                continue

            athletes_to_save.append({
                "AthleteID": result.get("AthleteID"),
                "FirstName": result.get("FirstName"),
                "LastName": result.get("LastName"),
                "Gender": result.get("Gender"),
                "SchoolName": result.get("SchoolName")
            })

            results_to_save.append((result, meet_info, div_id, event_id, event_short, is_relay))

    # ------------------------------------------------------------------ #
    # Step 3: save everything to the DB                                    #
    # ------------------------------------------------------------------ #

    # Only runs if we got here without returning -1.
    # Save meets, athletes, and results in one pass.
    for meet_info, div_id, event_id, event_short, distance_meters in meets_to_save:
        saveMeetTF(meet_info, div_id, event_id, event_short, distance_meters)

    for athlete_data in athletes_to_save:
        saveAthlete(athlete_data)

    for result, meet_info, div_id, event_id, event_short, is_relay in results_to_save:
        saveResultTF(result, meet_info, div_id, event_id, event_short, is_relay)

    # Log every successfully scraped event/div combo.
    # We do this after saving results — if saveResultTF crashed partway
    # through we don't want to mark those events as successfully scraped.
    # We use a set to deduplicate because results_to_save has one row per
    # result (many per event), but we only want to log each event once.
    logged_events = set()
    for _, meet_info, div_id, event_id, event_short, _ in results_to_save:
        # (meet_id, event_short, div_id) is the unique key — same as the
        # PRIMARY KEY in tf_scraped_events.
        key = (meet_info.get("ID"), event_short, div_id)
        if key not in logged_events:
            logScrapedEventTF(key[0], key[1], key[2])
            logged_events.add(key)

    return len(results_to_save)

# scrapeMeetUnified
# Purpose: Detects whether a meet is XC or TF, scrapes it with the
#          correct scraper, and updates the queue sport tag to reflect
#          the true sport. This is the single entry point for all meet
#          scraping — the launcher calls this instead of scrapeMeet or
#          scrapeMeetTF directly.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           label: session label for logging e.g. "[Session 1]".
# Output: Tuple of (n, detected_sport).
#         n: number of results saved, or -1 on failure.
#         detected_sport: "XC", "TF", or "UNKNOWN" if detection failed.

async def scrapeMeetUnified(page, meet_id: int, label: str) -> tuple:

    # ------------------------------------------------------------------ #
    # Step 1: try XC first                                                 #
    # ------------------------------------------------------------------ #

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
        # XC API returned nothing — could be a TF meet, try it.
        try:
            n = await scrapeMeetTF(page, meet_id, label)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{label} [!] scrapeMeetTF failed for meet {meet_id}: {e}")
        # Sets the sport to TF in the db.
        conn = getConn()
        cursor = conn.cursor()
        cursor.execute("UPDATE meet_queue SET sport = %s WHERE meet_id = %s", ('TF', meet_id))
        conn.commit()
        conn.close()
        return n, "TF"

    # Non-empty divisions means the XC API returned XC data — it's XC.
    if divisions:

        # Update queue sport tag to XC so we have accurate records.
        # This fixes any meets that were mislabeled as TF in the queue.
        conn = getConn()
        cursor = conn.cursor()
        cursor.execute("UPDATE meet_queue SET sport = %s WHERE meet_id = %s", ('XC', meet_id))
        conn.commit()
        conn.close()

        # Scrape it as XC using the existing scrapeMeet logic.
        # We already have meet_info and divisions from the getMeetData call
        # above, so we pass them directly instead of calling getMeetData again.
        n = await scrapeXCDivisions(page, meet_id, meet_info, divisions, label)

        return n, "XC"
    
    # ------------------------------------------------------------------ #
    # Step 2: no XC divisions — try TF                                    #
    # ------------------------------------------------------------------ #

    # Empty xcDivisions means this isn't an XC meet. Try TF.
    try:
        n = await scrapeMeetTF(page, meet_id, label)
    except Exception as e:
        print(f"{label} [!] scrapeMeetTF failed for meet {meet_id}: {e}")
        return -1, "UNKNOWN"

    # Update queue sport tag to TF.
    conn = getConn()
    conn.execute(
        "UPDATE meet_queue SET sport = 'TF' WHERE meet_id = %s",
        (meet_id,)
    )
    conn.commit()
    conn.close()

    return n, "TF"

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

    