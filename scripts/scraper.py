# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 5/28/2026
# File Title: scraper.py
# Purpose: Scrapes meet ids and other relevant info for the predictor
# and the results scraper. Save the relevant info to a SQLite db.


import asyncio
from playwright.async_api import async_playwright
import json
from database import createTables, saveAthlete, saveResult, saveMeet, countRows, saveMeetQueue, countQueue, DB_PATH
import random

# Custom exception for rate limiting so callers can handle it differently
# from genuine failures.
class RateLimitException(Exception):
    pass

# Raised when athletic.net returns an HTML page instead of JSON,
# which means Cloudflare has blocked the current IP entirely.
# Distinct from RateLimitException (429) — this is an IP block,
# not a rate limit, and requires a VPN rotation to recover.
class CloudflareException(Exception):
    pass


# getMeetResults
# Purpose: Function to scrape meet results from Athletic.net for a given 
# race and division
# arguments: page (Playwright page object), (should be ints) meet_id is the ID of the meet, div_id is the 
# ID of the race division, jwt_token is the authentication token we get from the cookies to
# avoid a 403 error when we call the API.
# output: list of dictionaries, one per athlete. Each dictionary contains 
# the athlete's name, time, and other relevant info. This is the raw 
# data that will be used to train the predictor.
async def getMeetResults(page, meet_id: int, div_id: int, jwt_token: str):

    await page.goto(f"https://www.athletic.net/CrossCountry/meet/{meet_id}/results/{div_id}", timeout=60000)
    # Waits until the HTML is parsed ("domcontentloaded"), as opposed to waiting
    # for no network activity, which takes forever because there are always
    # background processes going on.
    # This returns as soon as the page is ready instead of always waiting 3 seconds.
    await page.wait_for_load_state("domcontentloaded", timeout=10000)
    await asyncio.sleep(random.uniform(1.5, 2.5))

    # Use page.evaluate to run JavaScript in the context of the page to 
    # fetch the results data from the API endpoint. Avoids issues with the 
    # page navigating away before we can read the data and where we need
    # to make API calls that require authentication tokens the browser has.
    # This is a python function containing a JavaScript function inside a string
    # as the first arg and the second arg is optional arguments we can pass to
    # the JavaScript function to avoid syntax issues with string concatenation.
    # async(args) => is a JS arrow function. It is async so it can use await. We
    # uses args instead of two separate parameters because evaluate only accepts
    # one arg, so we have to pack them together.
    data = await page.evaluate("""
        // Comments here are double slashes as this is JS.
        // This function runs in the browser context, so we can use 
        // fetch to call the API directly. async allows us to 
        // wait for the response before returning.
        // Pass div_id as an argument rather than concatenating it into the string.
        // This avoids syntax errors and is cleaner.
        async (args) => {
            // Call the API endpoint that returns the results data for the 
            // specified division. This is a POST request.
            // Contains three parameters of a HTTP request. Post - send data.
            // Headers - what we're sending. Body - data we're sending, the divId.
            const response = await fetch('/api/v1/Meet/GetResultsData3', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    // JWT token to authenticate the request
                    'anettokens': args.token,
                    // athletic.net header identifying version of the app making
                    // the request.
                    'anet-appinfo': 'web:web:0:240'
                },
                // The data we're sending, specifically the division ID and
                // the authentication token. We have to stringify it to send 
                // it as JSON.
                body: JSON.stringify({divId: args.divId})
            });
            // Return it to the Python context as a JavaScript object, 
            // which will be converted to a Python dictionary.
            return {
                status: response.status,
                text: await response.text()
            };
        }
    """, {"divId": div_id, "token": jwt_token})  # Pass div_id as an argument to the function. 
                                                 # jwt_token is the authentication token we got 
                                                 # from the cookies so we don't get a 403 error.

    # data is the dict that page.evaluate returns. We take each part of it,
    # the HTTP status code, and the response text and hold them here. We use
    # .get in case nothing is returned, so it doesn't crash.
    status = data.get('status')
    text = data.get('text', '')

    # Empty text means the API returned nothing at all — this is a genuine
    # failure, not an empty meet. Raise an exception so scrapeMeet treats
    # it as a failed division.
    if not text:
        raise Exception(f"Empty response from API (status {status})")

    if status == 429:
        # Rate limited — wait 60 seconds and raise so scrapeMeet can retry.
        # We raise a specific exception type so the caller can distinguish
        # a rate limit from a genuine failure.
        raise RateLimitException("Rate limited by athletic.net")
    

    # If status is not 200, the API rejected the request.
    if status != 200:
        raise Exception(f"API returned status {status}")
    
    # If the response starts with <!DOCTYPE, Cloudflare returned an HTML
    # challenge page instead of JSON — the IP is blocked.
    # We check before attempting json.loads() so we get a clean specific
    # exception instead of a confusing JSONDecodeError.
    if "<!DOCTYPE" in text or "<html" in text:
        raise CloudflareException("Cloudflare block detected")


    # Parse the results from the response text. If it fails it raises the
    # error to the caller.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
         raise Exception(f"Invalid JSON response (status {status}): {text[:100]}")

    # Return the results — could be an empty list for a genuine empty meet,
    # which is fine and will be handled correctly in scrapeMeet.
    return parsed.get("resultsXC", [])


# getMeetData
# Purpose: Fetches meet metadaata and division list from athletic.net for a
# given meet ID. Returns meet info.
# Arguments: page is the Playwright page object. We pass it so we don't have
# to keep opening a new page.meet_id is the ID of the meet to fetch data for.
# Output: Makes a tuple of meet_info and divisions.
# meet_info is a dictionary with keys: meet_id, div_id, meet_name,
# course_name, distance, gps_lat, gps_long, state. 
# divisions is a list of dictionaries, each with keys: IDMeetDiv, Distance.
async def getMeetData(page, meet_id: int):

    # Navigate to the meet info page for the specified meet ID to get valid
    # cookies and tokens.
    await page.goto(f"https://www.athletic.net/CrossCountry/meet/{meet_id}/info", timeout=60000)
    # Waits until the HTML is parsed ("domcontentloaded"), as opposed to waiting
    # for no network activity, which takes forever because there are always
    # background processes going on.
    # This returns as soon as the page is ready instead of always waiting 3 seconds.
    await page.wait_for_load_state("domcontentloaded", timeout=10000)

    # Dismiss consent popup BEFORE any API calls.
    # _dismissConsentPopup waits up to 8s for the popup to appear —
    # we don't care if it returns False (popup absent = no problem).
    await dismissConsentPopup(page)

    # Make the API call directly from the browser context.
    # This uses the browser's own cookies and tokens, so we don't have 
    # to worry about authentication issues.
    data = await page.evaluate("""
        async (meetId) => {
            const response = await fetch('/api/v1/Meet/GetMeetData?meetId=' + meetId + '&sport=xc');
            return await response.json();
        }
    """, meet_id)  # Pass meet_id as an argument to the function to avoid syntax issue with string concatenation.

    # If data is None probably a track meet so return empty lists.
    if data is None:
        return {}, []
    
    # Extract meet info and divisions from the API response
    meet_info = data.get("meet", {})

    # If meet_info is None probably a track meet so return empty lists.
    if meet_info is None:
        meet_info = {}
    meet_info["jwtMeet"] = data.get("jwtMeet", "") # Add the jwt token to the meet_info dictionary so we can use it later to authenticate our results API request.
    divisions = data.get("xcDivisions", [])

    return meet_info, divisions

# getRankings
# Purpose: Fetches the rankings data and returns the list of athletes and their data.
# Arguments: 
#           page is the current Playwright page.
#           gender is the gender of the race we're searching. It should be a string
#           page_num is the page number of the rankings to fetch. It should be a string.
# Output: A list of athlete objects, each containing the athlete's name, time, and 
# other relevant info.
async def getRankings(page, gender: str, page_num: str):
    
    # Precalculating distance based on gender because cannot do so in JS.
    dist = 10000 if gender == "m" else 6000

    # Makes the API call directly from the browser with given info in browser.
    # Data stores the entire dictionary the API call sends back.
    data = await page.evaluate("""
        async (args) => {
            const response = await fetch('/api/v1/xcRankings/GetRankings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json'},
                body: JSON.stringify({
                    // This is the request body and the params athletc.net expects.
                    // This was taken from the GetRankings API (as seen above).
                    reportType: 'div',
                    divListId: 79899,
                    gender: args.gender,
                    distance: args.distance,
                    // qParams is the field that controls Page Number.
                    qParams: { page: args.pageNum },
                    version: 2
                })
            });
            return await response.json();
        }
    """, {"gender": gender, "distance" : dist, "pageNum": page_num})

    # Gets the data from then list containing the rankings 
    # (including the athlete id). We put this into a Python List.
    return data.get("rankings", [])

# getEvents
# Purpose: Gets the meet dictionaries for all the meets in a state in a month.
# Arguments: 
#           page: browser page we are fetching from
#           state: state we are fetching meets from
#           year, month: year and month of meets we are fetching
# Output: Returns a list[int] of the meet IDs for that month/state/year. We
# only need the meet IDs because we're going to scrape those pages later anyway.
async def getEvents(page, state: str, year: int, month: int) -> list[int]:

    # Format month as zero-padded string (01, 02, etc.).
    month_str = str(month).zfill(2)

    # Set start and end dates for month.
    start = f"{year}-{month_str}-01"
    end = f"{year}-{month_str}-28" # Safe last day for all months. Shouldn't
                                   # matter anyway as athletic.net only cares
                                   # about month and year.

    # Grabs the meet data for that month from the athletic net page.
    data = await page.evaluate("""
        async (args) => {
            const response = await fetch('/api/v1/Event/Events', {
                method: 'POST',
                headers : { 'Content-Type': 'application/json'},
                body: JSON.stringify({
                    start: args.start,
                    end: args.end,
                    state: args.state,
                    country: 'US',
                    // Mask codes for all levels + XC.
                    sportMask: 0,
                    levelMask: 0,
                    // Any location, any meet.
                    filterTerm: '',
                    location: ''
                })
            });
            return await response.text();
        }
    """, {"start": start, "end": end, "state": state})

    # If no meets during that time period and state, return an empty list.
    # We do this to avoid a crash caused by using json.loads() on no data.
    if not data:
        return []
    
    # Uses try/except in case data is returned but it isn't valid JSON. Avoids
    # issues with using json.loads() on invalid JSON.
    try:
        # Converts raw text string from API to a Python dictionary, which
        parsed = json.loads(data)
    except json.JSONDecodeError:
        # Cloudflare block
        if "Just a moment" in data:
            print(f"Cloudflare block for {state} {year}-{month}, skipping")
        # Empty month
        else:
            print(f"Unexpected response for {state} {year}-{month}: {data[:100]}")
        return []

    # Specifically gets the "events" section dictionary for each meet
    # and puts each one into a Python list.
    events = parsed.get("events", [])

    # Returns all the meet IDs and sport(XC/TF) as a tuple in a list. We
    # also return sport to differentiate them for the model.
    return [(e["IDMeet"], e.get("Sport")) for e in events if e.get("IDMeet")]

# dismissConsentPopup
# Purpose: Clicks on the GDPR consent popup. Useful when VPNing to Europe.
#          Also deals with the server communication error modal.
# Arguments:
#           page: browser page where we are clicking the consent popup.
# Output: Return True if popup dismissed, False if not found.
async def dismissConsentPopup(page):
    # Wait up to 2s for consent popup
    try:
        consent = page.locator("button.fc-cta-consent")
        if await consent.is_visible(timeout=8000):
            await consent.click()
            got_cookies = await waitForCookies(page)
            if not got_cookies:
                print(f"[DEBUG] cookies never set for meet {meet_id}")
            return True
    except Exception:
        pass

    # Check instantly for error modal — if consent didn't show,
    # error modal might have appeared in that same window
    try:
        error_modal = page.locator("div.modal-dialog:has(h4:text('Server Communication Error'))")
        if await error_modal.is_visible(timeout=8000):
            await page.keyboard.press("Escape")
            got_cookies = await waitForCookies(page)
            if not got_cookies:
                print(f"[DEBUG] cookies never set for meet {meet_id}")
            return True
    except Exception:
        pass

    return False

# Try common events in order until one returns a non-empty events array.
# 100m is most common but older/field-only meets may not have it.
DUMMY_EVENTS = ["100m", "200m", "400m", "800m", "1mile", "3000m", "5000m"]

# fetchEventsArray
# Purpose: Makes a dummy GetResultsData3 call to get the events lookup array.
#          Tries multiple events until one returns a non-empy array.#
# Arguments:
#           page: Playwright browser page object.
#           div_id: the first division ID from event_divs, used for the dummy call.
#           jwt_token: the meet JWT token from meet_info, used to authenticate the call.
# Output: Raw events array from the API — a list of dicts each with keys
#         "ID" (event_id int), "EventShort" (str), and "Gender" ("M" or "F").
#         Returns [] if all fallback event shorts fail.
async def fetchEventsArray(page, div_id, jwt_token):

    # The events array maps event IDs to their short codes and genders.
    # e.g. event ID 58 -> {"event_short": "1mile", "gender": "m"}
    # This array is only returned by GetResultsData3, not GetMeetData.
    # So we make dummy calls to GetResultsData3 just to get the events
    # array — we don't use the actual results from this call.
    # We use eventShort with the list above as the dummy events because every TF meet
    # has at leat one of these events, so the call will succeed and 
    # return the full events array.
    # The div_id we pass doesn't matter for getting the events array.
    for event_short in DUMMY_EVENTS:
        events_raw = await page.evaluate("""
            async (args) => {
                const response = await fetch('/api/v1/Meet/GetResultsData3', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'anettokens': args.token,
                        'anet-appinfo': 'web:web:0:240'
                    },
                    body: JSON.stringify({
                        gender: 'm',
                        divId: args.divId,
                        eventShort: args.eventShort,
                        rawResults: false,
                        showTips: false
                    })
                });
                try {
                    const data = await response.json();
                    return data.events || [];
                } catch(e) {
                    return [];
                }
            }
        """, {"divId": div_id, "token": jwt_token, "eventShort": event_short})

        # Non-empty means this event exists in the meet — events array is populated
        if events_raw:
            return events_raw

    return []

# Wait until the JWT cookie is actually set before firing any API calls.
# Polls every 500ms for up to 10 seconds.
async def waitForCookies(page):
    for _ in range(20):
        cookies = await page.context.cookies()
        # athletic.net sets an auth cookie once the page is fully initialized
        if any(c["name"] == "anettokens" for c in cookies):
            return True
        await asyncio.sleep(0.5)
    return False

# EVENT_ID_TO_SHORT
# Purpose: Maps athletic.net event IDs to their short codes and genders.
# Used to look up event_short and gender from event_divs which only
# gives us event IDs. Derived from GetResultsData3 events array.
# Male and female versions of the same event have different IDs.
EVENT_ID_TO_SHORT = {
    # Track — Male
    1:  ("100m",              "m"),
    2:  ("200m",              "m"),
    3:  ("400m",              "m"),
    4:  ("800m",              "m"),
    5:  ("1500m",             "m"),
    6:  ("3000m",             "m"),
    7:  ("4x100m",            "m"),
    8:  ("4x400m",            "m"),
    9:  ("hj",                "m"),
    10: ("110mh",             "m"),
    11: ("300mh",             "m"),
    12: ("shot",              "m"),
    13: ("discus",            "m"),
    16: ("pv",                "m"),
    17: ("lj",                "m"),
    18: ("tj",                "m"),
    39: ("4x800m",            "m"),
    40: ("distmed12,4,8,16",  "m"),
    50: ("4x200m",            "m"),
    52: ("1600m",             "m"),
    58: ("1mile",             "m"),
    60: ("3200m",             "m"),
    65: ("sprintmed1124",     "m"),
    70: ("1200m",             "m"),
    90: ("sprintmed2248",     "m"),
    95: ("4x1600m",           "m"),
    175: ("110shuttleh",      "m"),

    # Track — Female
    19: ("100m",              "f"),
    20: ("200m",              "f"),
    21: ("400m",              "f"),
    22: ("800m",              "f"),
    23: ("1500m",             "f"),
    24: ("3000m",             "f"),
    25: ("4x100m",            "f"),
    26: ("4x400m",            "f"),
    27: ("hj",                "f"),
    28: ("100mh",             "f"),
    29: ("300mh",             "f"),
    30: ("shot",              "f"),
    31: ("discus",            "f"),
    34: ("pv",                "f"),
    35: ("lj",                "f"),
    36: ("tj",                "f"),
    51: ("4x200m",            "f"),
    53: ("1600m",             "f"),
    59: ("1mile",             "f"),
    61: ("3200m",             "f"),
    64: ("distmed12,4,8,16",  "f"),
    66: ("sprintmed1124",     "f"),
    67: ("4x800m",            "f"),
    71: ("1200m",             "f"),
    91: ("sprintmed2248",     "f"),
    96: ("4x1600m",           "f"),
    176: ("100shuttleh",      "f"),
}

# getMeetDataTF
# Purpose: Fetches TF meet metadata, the events lookup table (mapping event
#          IDs to their short codes and genders), and the list of event/div
#          combos that have results to scrape.
#          Uses CDP Fetch.enable to intercept the response body before Chrome
#          garbage collects it — more reliable than page.on('response') or
#          page.expect_response() with real Chrome.
# Arguments:
#           page: Playwright browser page object.
#           meet_id: athletic.net meet ID integer.
# Output: A tuple of (meet_info, events_dict, event_divs).
#         meet_info: dict with meet-level fields (name, GPS, indoor, date, jwt).
#         events_dict: dict mapping event_id (int) to {"event_short": str, "gender": "m"/"f"}.
#         event_divs: list of {"e": event_id, "d": div_id} dicts with results.
#         Returns ({}, {}, []) if the meet has no TF data or interception fails.
async def getMeetDataTF(page, meet_id: int):

    # ------------------------------------------------------------------ #
    # Step 1: enable CDP Fetch interception before navigating             #
    # ------------------------------------------------------------------ #

    # CDP Fetch.enable pauses matching responses at the protocol level before
    # Chrome can garbage collect the body — unlike page.on('response') which
    # races against Chrome's cleanup.
    cdp = await page.context.new_cdp_session(page)
    await cdp.send('Fetch.enable', {
        'patterns': [{'urlPattern': '*GetMeetData*', 'requestStage': 'Response'}]
    })

    captured_data = {}

    async def on_request_paused(event):
        url = event.get('request', {}).get('url', '')
        if 'sport=tf' in url and str(meet_id) in url:
            try:
                body_result = await cdp.send('Fetch.getResponseBody', {
                    'requestId': event['requestId']
                })
                body = body_result['body']
                # CDP returns base64 encoded body for binary responses —
                # decode if needed.
                if body_result.get('base64Encoded'):
                    import base64
                    body = base64.b64decode(body).decode('utf-8')
                captured_data['body'] = body
            except Exception as e:
                print(f"[DEBUG] CDP body error: {e}")
        # Always continue the response so the page gets it too.
        await cdp.send('Fetch.continueResponse', {'requestId': event['requestId']})

    cdp.on('Fetch.requestPaused', on_request_paused)

    # ------------------------------------------------------------------ #
    # Step 2: navigate                                                     #
    # ------------------------------------------------------------------ #

    await page.goto(
        f"https://www.athletic.net/TrackAndField/meet/{meet_id}/results",
        timeout=60000
    )
    await page.wait_for_load_state("domcontentloaded", timeout=10000)

    # Give CDP a moment to process the intercepted response.
    await asyncio.sleep(1)
    await cdp.send('Fetch.disable')

    # ------------------------------------------------------------------ #
    # Step 3: parse response                                              #
    # ------------------------------------------------------------------ #

    if not captured_data:
        return {}, {}, []

    try:
        data = json.loads(captured_data['body'])
    except Exception:
        return {}, {}, []

    if not isinstance(data, dict):
        return {}, {}, []

    meet_info = data.get("meet", {})
    if not isinstance(meet_info, dict):
        return {}, {}, []

    meet_info["jwtMeet"] = data.get("jwtMeet", "")
    event_divs = data.get("eventDivsWithResults", [])
    if not event_divs:
        return meet_info, {}, []

    # ------------------------------------------------------------------ #
    # Step 4: build events dict from hardcoded ID mapping                 #
    # ------------------------------------------------------------------ #

    # We use a hardcoded event ID → (event_short, gender) mapping instead
    # of a dummy API call. Athletic.net event IDs are stable across meets.
    # Unknown event IDs are skipped.
    events_dict = {}
    for event_div in event_divs:
        event_id = event_div.get("e")
        if event_id in EVENT_ID_TO_SHORT:
            event_short, gender = EVENT_ID_TO_SHORT[event_id]
            events_dict[event_id] = {
                "event_short": event_short,
                "gender": gender
            }

    return meet_info, events_dict, event_divs


# getMeetResultsTF
# Purpose: Fetches results for a single event/division/gender combo by
#          navigating directly to the event URL and intercepting Angular's
#          GetResultsData3 response via CDP Fetch.enable.
# Arguments:
#           page: Playwright browser page object.
#           meet_id: athletic.net meet ID integer.
#           div_id: division ID integer.
#           event_short: event short code e.g. "100m", "1mile".
#           gender: "m" or "f".
#           jwt_token: meet JWT token, kept for interface consistency.
# Output: List of result dicts, one per athlete. Empty list if no results.
#         Raises Exception on failure.
async def getMeetResultsTF(page, meet_id: int, div_id: int, event_short: str,
                           gender: str, jwt_token: str):

    # ------------------------------------------------------------------ #
    # Step 1: enable CDP Fetch interception before navigating             #
    # ------------------------------------------------------------------ #

    cdp = await page.context.new_cdp_session(page)
    await cdp.send('Fetch.enable', {
        'patterns': [{'urlPattern': '*GetResultsData3*', 'requestStage': 'Response'}]
    })

    captured_data = {}

    async def on_request_paused(event):
        try:
            body_result = await cdp.send('Fetch.getResponseBody', {
                'requestId': event['requestId']
            })
            body = body_result['body']
            if body_result.get('base64Encoded'):
                import base64
                body = base64.b64decode(body).decode('utf-8')
            captured_data['body'] = body
        except Exception as e:
            pass  # CDP session dead or body unavailable — ignore
        try:
            await cdp.send('Fetch.continueResponse', {'requestId': event['requestId']})
        except Exception:
            pass

    cdp.on('Fetch.requestPaused', on_request_paused)

    # ------------------------------------------------------------------ #
    # Step 2: navigate to event URL                                       #
    # ------------------------------------------------------------------ #

    # Athletic.net event URLs follow this pattern:
    # /TrackAndField/meet/{meet_id}/results/{gender}/{div_id}/{event_short}
    # Navigating here causes Angular to fire GetResultsData3 automatically.
    url = (
        f"https://www.athletic.net/TrackAndField/meet/{meet_id}"
        f"/results/{gender}/{div_id}/{event_short}"
    )
    await page.goto(url, timeout=60000)
    await page.wait_for_load_state("domcontentloaded", timeout=10000)

    await asyncio.sleep(1)
    await cdp.send('Fetch.disable')

    # ------------------------------------------------------------------ #
    # Step 3: parse and return results                                    #
    # ------------------------------------------------------------------ #

    if not captured_data:
        raise Exception("GetResultsData3 never captured")

    try:
        parsed = json.loads(captured_data['body'])
    except Exception as e:
        raise Exception(f"Failed to parse GetResultsData3: {e}")

    if not isinstance(parsed, dict):
        raise Exception(f"Unexpected response type: {type(parsed)}")

    results = parsed.get("resultsTF", [])

    # resultsTF sometimes comes back as a list of lists — unwrap if needed.
    # e.g. [[result1, result2, ...]] instead of [result1, result2, ...]
    if results and isinstance(results[0], list):
        results = results[0]

    return results


# Tests the scraper
async def main():

    createTables()
    
    async with async_playwright() as p:

        # Opens a new chrome browser, headless=True so browser is visible. 
        # This makes it harder for cloudflare to detect us.
        # args to stop Chrome advertising it's automated.
        browser = await p.chromium.launch(
            headless = False,
            args = ["--disable-blink-features=AutomationControlled"]
        )

        # Fresh browser profile to make it less detectable as a bot, 
        # with user agent spoofing to look like a real browser.
        context = await browser.new_context(
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            viewport = {"width": 1280, "height": 800},
            java_script_enabled = True
        )

        # Opens a new tabe in the browser, this is where we will navigate to 
        # the meet results page and scrape the data
        page = await context.new_page()

        # Changes webdriver property so scraper doesn't say it's a bot. 
        # undefined = human, true = robot.
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")


        # Navigate to athletic.net so relative URLs work
        await page.goto("https://www.athletic.net")
        await page.wait_for_timeout(2000)

        # A set of all meet ids to avoid duplicates due to location. Since
        # Meet IDs are unique each year, yearly meets should still be collected.
        all_meet_ids = set()

        # List of US states to search
        us_states = ['AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DC', 'DE', 'FL', 
             'GA', 'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 
             'MD', 'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NC', 'ND', 'NE', 
             'NH', 'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI', 
             'SC', 'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV', 'WY']
        
        # Goes through every year, month, and state since 1990 to find all
        # the meets. Year first makes us go through the meets chronologically.
        for year in range(1990, 2027):
            # Re-go to athletic.net to stop the session from dying.
            await page.goto("https://www.athletic.net/events/usa/al/2024-9-1")
            await page.wait_for_timeout(3000)
            for month in range (1, 13):
                for state in us_states:
                    # Gets meets for that year, that month, that state,
                    # adds it to all_meet_ids, and then sleeps a random
                    # amount of time to avoid detection. We use update instead
                    # of add because it adds all items at once, instead of one
                    # at a time.
                    meets = await getEvents(page, state, year, month)
                    all_meet_ids.update(meets)
                    # Saves to db in case of a memory overflow error
                    for meet_id, sport in meets:
                        saveMeetQueue(meet_id, sport)
                    await asyncio.sleep(random.uniform(0.5, 1.0))

                # Prints current year and month and running total of number of meets
                print(f"{year}-{month}: running total {len(all_meet_ids)}")

        print(f"Done. Total unique meets: {len(all_meet_ids)}")

        # Prints size of the databse storing the meet queue.
        countQueue()

        await browser.close()

# Only runs main() if this script is run directly, not if it's imported.
if __name__ == "__main__":
    asyncio.run(main())