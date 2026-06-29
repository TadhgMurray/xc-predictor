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
from database import createTables, saveMeetQueue, countQueue
import random

# Raised when athletic.net returns a Cloudflare challenge page OR a raw
# HTML page (detected by a <!DOCTYPE html> in the response body) instead
# of the expected JSON. Both mean the current IP is blocked at the network
# level and require a VPN rotation to recover. Distinct from
# RateLimitException (429) — that is a server miscommunication error.
class CloudflareException(Exception):
    pass

# RateLimitException
# Purpose: Raised specifically when the server returns HTTP 429
#          (rate limited). This is a SEPARATE class from the generic
#          Exception so callers can tell "we got rate-limited" apart
#          from "something else went wrong" (timeout, bad JSON, etc.)
#          without parsing error message strings. Same pattern as
#          CloudflareException — a type carries information that a
#          string message can't reliably carry.
class RateLimitException(Exception):
    pass



# getMeetResults
# Purpose: Scrape one XC division's results from athletic.net's GetResultsData3
#          endpoint, via a direct in-page fetch (inherits the browser's cookies/
#          auth). Retries on transient errors; raises CloudflareException on an
#          IP-level block so the caller can rotate VPN.
#
#          CHANGED: now returns a 3-tuple (results, teams, team_scores) instead
#          of just the results list. teams[] (team rosters) and teamScores[]
#          (per-division team scoring) come from the same response and were
#          being discarded.
# Arguments:
#           page:
#               Playwright page object.
#           meet_id:
#               athletic.net meet ID (logging/error context only).
#           div_id:
#               division ID to fetch results for.
#           jwt_token:
#               meet JWT auth token (anettokens header) from meet_info.
# Output:
#           On success, a tuple (results, teams, team_scores):
#               results      - list of resultsXC[] dicts (one per athlete).
#               teams        - teams[] roster list, or None if absent.
#               team_scores  - teamScores[] list for this division, or None.
#           Raises CloudflareException on an IP block, or Exception once all
#           retries are exhausted.
async def getMeetResults(page, meet_id: int, div_id: int, jwt_token: str):

    MAX_RETRIES = 3
    RETRY_DELAY = 60.0   # Seconds to wait between retries

    # Timeout for the JS fetch inside the browser.
    # 15 seconds is generous — real responses come back in under 2s.
    # If it takes longer than this, the server is hung and we should move on.
    JS_FETCH_TIMEOUT_MS = 15000

    # ------------------------------------------------------------------ #
    # Retry loop — the evaluate call can fail if the page isn't ready    #
    # or if the server returns a 429. Retrying after a short delay       #
    # resolves this in most cases.                                       #
    # ------------------------------------------------------------------ #
    last_error = None


    for attempt in range(MAX_RETRIES):

        try:
            # Use page.evaluate to run Java
            # Script in the context of the page to 
            # fetch the results data from the API endpoint. Avoids issues with the 
            # page navigating away before we can read the data and where we need
            # to make API calls that require authentication tokens the browser has.
            # This is a python function containing a JavaScript function inside a string
            # as the first arg and the second arg is optional arguments we can pass to
            # the JavaScript function to avoid syntax issues with string concatenation.
            # async(args) => is a JS arrow function. It is async so it can use await. We
            # uses args instead of two separate parameters because evaluate only accepts
            # one arg, so we have to pack them together.
            # page.evaluate runs JS inside the browser, inheriting its
            # cookies and auth state. We call the API directly rather than
            # navigating so we get structured JSON back instead of HTML.
            data = await page.evaluate("""
                async (args) => {
                    // AbortController lets us cancel the fetch after a timeout.
                    // Without this, a hung server response waits forever.
                    const controller = new AbortController();

                    // Schedule abort() to fire after timeoutMs.
                    // If fetch completes first, clearTimeout cancels this.
                    const timeoutId = setTimeout(
                        () => controller.abort(),
                        args.timeoutMs
                    );
                                       
                    const response = await fetch('/api/v1/Meet/GetResultsData3', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'anettokens': args.token,
                            'anet-appinfo': 'web:web:0:240'
                        },
                        body: JSON.stringify({divId: args.divId}),
                                       
                        // Ties fetch to the controller. If abort() fires,
                        // fetch throws AbortError — which propagates up
                        // through page.evaluate to Python as an exception.
                        signal: controller.signal
                    });
                                       
                    // Response arrived in time — cancel the scheduled abort.
                    clearTimeout(timeoutId);
                                       
                    return {
                        status: response.status,
                        text: await response.text()
                    };
                }
            """, {"divId": div_id, "token": jwt_token, "timeoutMs": JS_FETCH_TIMEOUT_MS})

            status = data.get('status')
            text = data.get('text', '')

            # Empty text or 429 — server not ready, worth retrying
            if not text:
                raise Exception(f"Empty response from API (status {status})")
            if status == 429:
                raise Exception("Server miscommunication error (status 429)")
            if status != 200:
                raise Exception(f"API returned status {status}")

            # Cloudflare block — IP-level issue, no point retrying
            if "<!DOCTYPE" in text or "<html" in text:
                raise CloudflareException("Cloudflare block detected")

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                raise Exception(f"Invalid JSON (status {status}): {text[:100]}")

            # Success — return immediately
            # CHANGED: pull the three pieces of this response we care about.
            # resultsXC[] = the athlete results. teams[] = the meets teams roster
            # (same list repeated in every division's response — caller grabs it
            # once). teamScores[] = THIS division's team scoring (caller
            # accumulates across divisions). teams/team_scores are None when the
            # response omits them.
            results = parsed.get("resultsXC", [])

            # resultsXC sometimes comes back as a list-of-lists (the actual
            # result dicts nested one level deeper) — same shape quirk TF handles
            # in getMeetResultsTF. Unwrap so callers always get a flat list of
            # result dicts. Without this, _collectXCDivision iterates the outer
            # list and each item is a LIST, not a dict -> r.get(...) crashes the
            # whole session ('list' object has no attribute 'get').
            if results and isinstance(results[0], list):
                results = results[0]

            teams = parsed.get("teams")
            team_scores = parsed.get("teamScores")
 
            return results, teams, team_scores

        except CloudflareException:
            # Don't retry Cloudflare blocks — raise immediately so the
            # caller can trigger VPN rotation
            raise

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                print(
                    f"[XC] getMeetResults failed meet {meet_id} div {div_id} "
                    f"attempt {attempt + 1}/{MAX_RETRIES}: {e} "
                    f"— retrying in {RETRY_DELAY}s"
                )
                await asyncio.sleep(RETRY_DELAY)

    # All attempts exhausted
    raise Exception(
        f"getMeetResults failed after {MAX_RETRIES} attempts "
        f"(meet {meet_id} div {div_id}): {last_error}"
    )

# getAllResultsTF
# Purpose: Fetch one TF meet's entire result set in a single GET, via an in-page
#          fetch (inherits the browser's cookies/session). This is the fast path;
#          scrapeMeetTF falls back to per-event/div fetching if this returns no
#          flatEvents or raises a non-Cloudflare error.
# Arguments:
#           page:
#               Playwright page object.
#           meet_id:
#               athletic.net meet ID.
#           jwt_token:
#               meet JWT (jwtMeet from getMeetDataTF) — sent as the anettokens
#               header to authenticate the call.
# Output:
#           The parsed GetAllResultsData payload dict, whose keys include
#           flatEvents, teams, eventTypes, relayLegs. Returns {} if the response
#           is empty/non-JSON. Raises CloudflareException on an IP block so the
#           caller can rotate VPN.
async def getAllResultsXC(page, meet_id: int, jwt_token: str): 

    # Timeout for the in-browser fetch — same 15s budget as the other fetchers.
    # A whole-meet payload is bigger than a single div, but still returns fast;
    # 15s is comfortably generous and guards against a hung response.
    JS_FETCH_TIMEOUT_MS = 15000
 
    data = await page.evaluate("""
        async (args) => {
            // AbortController cancels the fetch if it hangs past timeoutMs.
            const controller = new AbortController();
            const timeoutId = setTimeout(
                () => controller.abort(), args.timeoutMs
            );
 
            // GET with meetId + rawResults/showTips in the query string. Auth is
            // the anettokens header (the jwtMeet token). anet-appinfo mirrors the
            // other calls' client-identification header.
            const response = await fetch(
                '/api/v1/Meet/GetAllResultsData?meetId=' + args.meetId +
                '&rawResults=false&showTips=false',
                {
                    headers: {
                        'anettokens': args.token,
                        'anet-appinfo': 'web:web:0:240'
                    },
                    signal: controller.signal
                }
            );
 
            clearTimeout(timeoutId);
            return { status: response.status, text: await response.text() };
        }
    """, {"meetId": meet_id, "token": jwt_token, "timeoutMs": JS_FETCH_TIMEOUT_MS})
 
    status = data.get("status")
    text = data.get("text", "")
 
    # Empty body — treat as "no fast-path payload"; caller falls back to per-div.
    if not text:
        return {}
 
    # Cloudflare/HTML block — IP-level. Raise so scrapeMeetTF re-raises it up to
    # the launcher for VPN rotation (must NOT be swallowed as a generic miss).
    if "<!DOCTYPE" in text or "<html" in text:
        raise CloudflareException("Cloudflare block in getAllResultsTF")
    
    # 429 = per-session/IP rate limit. Raise RateLimitException so scrapeMeetTF
    # can log it distinctly (the signal you tune perMeetDelayRange against).
    # Previously a 429 fell through to JSON-parse and looked like a generic
    # failure — invisible. Now it's surfaced.
    if status == 429:
        raise RateLimitException("429 rate limit in getAllResultsTF")
 
    # Parse. On malformed JSON, return {} so the caller falls back rather than
    # crashing — a bad fast-path response shouldn't lose the meet.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
 
    if not isinstance(parsed, dict):
        return {}
 
    return parsed
 


# getMeetData
# Purpose: Fetches meet metadata and division list from athletic.net for a
# given meet ID. Returns meet info.
# Arguments: page is the Playwright page object. We pass it so we don't have
# to keep opening a new page.meet_id is the ID of the meet to fetch data for.
# Output: Makes a tuple of meet_info and divisions.
# meet_info is a dictionary with keys: meet_id, div_id, meet_name,
# course_name, distance, gps_lat, gps_long, state. 
# divisions is a list of dictionaries, each with keys: IDMeetDiv, Distance.
async def getMeetData(page, meet_id: int):

    # Timeout for the JS fetch inside the browser — same pattern as
    # getMeetResults. 15s is generous; real responses come back in <2s.
    JS_FETCH_TIMEOUT_MS = 15000

    # Make the API call directly from the browser context.
    # This uses the browser's own cookies and tokens, so we don't have 
    # to worry about authentication issues.
    # Fetch as text() not json() so we can inspect the raw response
    # before parsing. If Cloudflare blocks us, response.json() would
    # crash inside the JS with a SyntaxError before Python ever sees it,
    # meaning our DOCTYPE check would never run, because JS tries
    # to parse it itself.
    # Make the API call directly from the browser context, with an
    # AbortController so a hung server response doesn't wait forever.
    data = await page.evaluate("""
        async (args) => {
            const controller = new AbortController();
            const timeoutId = setTimeout(
                () => controller.abort(),
                args.timeoutMs
            );

            const response = await fetch(
                '/api/v1/Meet/GetMeetData?meetId=' + args.meetId + '&sport=xc',
                { signal: controller.signal }
            );

            clearTimeout(timeoutId);
            return await response.text();
        }
    """, {"meetId": meet_id, "timeoutMs": JS_FETCH_TIMEOUT_MS})

    # data is the raw response text — empty or "null" both mean no XC
    # data for this meet_id.
    if not data or data == "null":
        return {}, []
    
    # Cloudflare or HTML block — IP is blocked at the network level.
    # Raise so scrapeMeetUnified catches CloudflareException and triggers
    # VPN rotation instead of treating this as a generic failure.
    if "<!DOCTYPE" in data or "<html" in data:
        raise CloudflareException("Cloudflare block in getMeetData")

    # Parse the JSON string into a Python dict.
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return {}, []
    
    if not isinstance(parsed, dict):
        return {}, []
    
    # Extract meet info and divisions from the API response
    meet_info = parsed.get("meet", {})

    # If meet_info is None probably a track meet so return empty lists.
    if meet_info is None:
        meet_info = {}

    # Add JWT token to meet_info so results calls can authenticate.
    meet_info["jwtMeet"] = parsed.get("jwtMeet", "")
 
    divisions = parsed.get("xcDivisions", [])

    return meet_info, divisions

# dismissConsentPopup
# Purpose: Clicks on the GDPR consent popup. Useful when VPNing to Europe.
#          Also deals with the server communication error modal.
# Arguments:
#           page: browser page where we are clicking the consent popup.
# Output: Return True if popup dismissed, False if not found.
async def dismissConsentPopup(page, meet_id):
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
    41: ("55m",  "m"),
    43: ("55mh", "m"),
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
    46: ("55m",  "f"),
    48: ("55mh", "f"),
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
#          combos that have results to scrape — via a direct fetch from
#          whatever page is currently loaded. No navigation needed;
#          the API works from any athletic.net page once cookies/session
#          are established.
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
    # Step 1: Fetch TF Meet Metadata                                      #
    # ------------------------------------------------------------------ #

    JS_FETCH_TIMEOUT_MS = 15000

    data = await page.evaluate("""
        async (args) => {
            // Create a controller so we can cancel the fetch if it hangs.
            const controller = new AbortController();
                               
            // Schedules the controller to fire after timeoutMs.
            const timeoutId = setTimeout(
                () => controller.abort(), args.timeoutMs
            );
            
            // Fetches the meet data. This is a GET request,
            // so we don't need to send any data.
            const response = await fetch(
                '/api/v1/Meet/GetMeetData?meetId=' + args.meetId + '&sport=tf',
                // Attaches the controller to this fetch request.
                { signal: controller.signal }
            );

            // Cancels the scheduled abort.    
            clearTimeout(timeoutId);
            
            // Returns the resposnse status and text body.
            return { status: response.status, text: await response.text() };
            }
    """, {"meetId": meet_id, "timeoutMs": JS_FETCH_TIMEOUT_MS})

    # ------------------------------------------------------------------ #
    # Step 2: parse response                                              #
    # ------------------------------------------------------------------ #

    status = data.get("status")
    text = data.get("text", "")

    # Empty response — treat as no TF data.
    if not text:
        return {}, {}, []

    # Cloudflare block — IP-level issue, raise so the caller can rotate.
    if "<!DOCTYPE" in text or "<html" in text:
        raise CloudflareException("Cloudflare block in getMeetDataTF")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}, {}, []

    if not isinstance(parsed, dict):
        return {}, {}, []

    if not isinstance(data, dict):
        return {}, {}, []

    meet_info = parsed.get("meet", {})
    if not isinstance(meet_info, dict):
        return {}, {}, []

    # Add JWT token so getMeetResultsTF can authenticate.
    meet_info["jwtMeet"] = parsed.get("jwtMeet", "")

    # Stash the IDDiv -> Division-name map so the meta-only path can write the
    # real division name ('Open', 'Invitational') instead of falling back to
    # gender. tfDivisions carries it; the normal results path ignores this key.
    tf_divisions = parsed.get("tfDivisions") or []
    meet_info["_divisionByIdDiv"] = {
        d.get("IDDiv"): d.get("Division")
        for d in tf_divisions if isinstance(d, dict)
    }

    event_divs = parsed.get("eventDivsWithResults", [])

    if not event_divs:
        return meet_info, {}, []

    # ------------------------------------------------------------------ #
    # Step 3: build events dict from hardcoded ID mapping                 #
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
# Purpose: Fetches results for one event/division/gender combo via a
#          direct fetch from whatever page is currently loaded — no
#          navigation needed. Uses the jwtMeet token obtained once per
#          meet from getMeetDataTF, same pattern as XC's getMeetResults.
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

    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    JS_FETCH_TIMEOUT_MS = 15000

    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            data = await page.evaluate("""
                async (args) => {
                    // Create a controller so we can cancel the fetch if it hangs.
                    const controller = new AbortController();
                    
                    // Schedule abort() to fire after timeoutMs — cancelled below
                    // if the fetch completes first.
                    const timeoutId = setTimeout(
                        () => controller.abort(), args.timeoutMs
                    );

                    // Fetches the meet results data for this MeetID. Method is POST because
                    // GetResultsData3 expects us to send which event/div we want.
                    const response = await fetch('/api/v1/Meet/GetResultsData3', {
                        method: 'POST',
                        // Headers are extra metadata sent with the 
                        // request as key-value pairs. 
                        headers: {
                            // Tells server the body we're sending is JSON.
                            'Content-Type': 'application/json',
                            // Authetnication header.
                            'anettokens': args.token,
                            // client-identification header.
                            'anet-appinfo': 'web:web:0:240'
                        },
                        // Body of the data we're sending.
                        body: JSON.stringify({
                            gender: args.gender,
                            divId: args.divId,
                            eventShort: args.eventShort,
                            rawResults: false,
                            showTips: false
                        }),
                        // Links fetch to the about controller.
                        signal: controller.signal
                    });

                    // Fetch succeeded, cancel the timout.
                    clearTimeout(timeoutId);
                    
                    // response.text is the response body.
                    return { status: response.status, text: await response.text() };
                }
            """, {
                "divId": div_id,
                "eventShort": event_short,
                "gender": gender,
                "token": jwt_token,
                "timeoutMs": JS_FETCH_TIMEOUT_MS
            })

            status = data.get("status")
            text = data.get("text", "")

            if not text:
                raise Exception(f"Empty response from API (status {status})")
            if status == 429:
                raise RateLimitException("Server returned 429 (rate limited)")
            if status != 200:
                raise Exception(f"API returned status {status}")

            if "<!DOCTYPE" in text or "<html" in text:
                raise CloudflareException("Cloudflare block detected")

            # Try converting a JSON string into a Python object. If it's not
            # a valid JSON string it raises an exception.
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                raise Exception(f"Invalid JSON (status {status}): {text[:100]}")
            
            results = parsed.get("resultsTF", [])

            # resultsTF sometimes comes back as a list of lists — unwrap if needed.
            if results and isinstance(results[0], list):
                results = results[0]

            # NEW: the statewide teams[] roster rides along in every
            # GetResultsData3 response (a few hundred entries on a state
            # meet, ~a dozen on a dual, occasionally absent). We were
            # throwing it away here. Surface it so the caller can store it.
            # None when the response has no teams key.
            teams = parsed.get("teams")

            return results, teams
        
        except CloudflareException:
            # Don't retry Cloudflare blocks — raise immediately so the
            # caller can trigger VPN rotation.
            raise

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                print(
                    f"[TF] GetResultsData3 failed meet {meet_id} "
                    f"event {event_short} div {div_id} "
                    f"attempt {attempt + 1}/{MAX_RETRIES}: {e} "
                    f"— retrying in {RETRY_DELAY}s"
                )
                await asyncio.sleep(RETRY_DELAY)

    # If for loop ran the max number of times without exiting, raise
    # an exception.
    raise Exception(
        f"getMeetResultsTF failed after {MAX_RETRIES} attempts "
        f"(meet {meet_id} div {div_id} event {event_short}): {last_error}"
    )

# getAllResultsTF
# Purpose: Fetch one TF meet's entire result set in a single GET, via an in-page
#          fetch (inherits the browser's cookies/session). This is the fast path;
#          scrapeMeetTF falls back to per-event/div fetching if this returns no
#          flatEvents or raises a non-Cloudflare error.
# Arguments:
#           page:
#               Playwright page object.
#           meet_id:
#               athletic.net meet ID.
#           jwt_token:
#               meet JWT (jwtMeet from getMeetDataTF) — sent as the anettokens
#               header to authenticate the call.
# Output:
#           The parsed GetAllResultsData payload dict, whose keys include
#           flatEvents, teams, eventTypes, relayLegs. Returns {} if the response
#           is empty/non-JSON. Raises CloudflareException on an IP block so the
#           caller can rotate VPN.
async def getAllResultsTF(page, meet_id: int, jwt_token: str): 

    # Timeout for the in-browser fetch — same 15s budget as the other fetchers.
    # A whole-meet payload is bigger than a single div, but still returns fast;
    # 15s is comfortably generous and guards against a hung response.
    JS_FETCH_TIMEOUT_MS = 15000
 
    data = await page.evaluate("""
        async (args) => {
            // AbortController cancels the fetch if it hangs past timeoutMs.
            const controller = new AbortController();
            const timeoutId = setTimeout(
                () => controller.abort(), args.timeoutMs
            );
 
            // GET with m//eetId + rawResults/showTips in the query string. Auth is
            // the anettokens header (the jwtMeet token). anet-appinfo mirrors the
            // other calls' client-identification header.
            const response = await fetch(
                '/api/v1/Meet/GetAllResultsData?meetId=' + args.meetId +
                '&rawResults=false&showTips=false',
                {
                    headers: {
                        'anettokens': args.token,
                        'anet-appinfo': 'web:web:0:240'
                    },
                    signal: controller.signal
                }
            );
 
            clearTimeout(timeoutId);
            return { status: response.status, text: await response.text() };
        }
    """, {"meetId": meet_id, "token": jwt_token, "timeoutMs": JS_FETCH_TIMEOUT_MS})
 
    status = data.get("status")
    text = data.get("text", "")
 
    # Empty body — treat as "no fast-path payload"; caller falls back to per-div.
    if not text:
        return {}
 
    # Cloudflare/HTML block — IP-level. Raise so scrapeMeetTF re-raises it up to
    # the launcher for VPN rotation (must NOT be swallowed as a generic miss).
    if "<!DOCTYPE" in text or "<html" in text:
        raise CloudflareException("Cloudflare block in getAllResultsTF")
    
    # 429 = per-session/IP rate limit. Raise RateLimitException so scrapeMeetTF
    # can log it distinctly (the signal you tune perMeetDelayRange against).
    # Previously a 429 fell through to JSON-parse and looked like a generic
    # failure — invisible. Now it's surfaced.
    if status == 429:
        raise RateLimitException("429 rate limit in getAllResultsTF")
 
    # Parse. On malformed JSON, return {} so the caller falls back rather than
    # crashing — a bad fast-path response shouldn't lose the meet.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
 
    if not isinstance(parsed, dict):
        return {}
 
    return parsed
 