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
import psycopg2

from playwright.async_api import async_playwright

from database import (
    _statusOf,
    createTables, getConn,
    saveMeet, saveAthletesBulk, saveResultsBulk,
    saveMeetTF, saveResultsTFBulk,
    logScrapedEventsTFBulk,
    markScraped, countRows, saveMeetTeams,
    saveMeetExtras, _resolveSchool, saveMeetTFMeta
)

from scraper import (
    getMeetData, getMeetResults,
    getMeetDataTF, getMeetResultsTF,
    getAllResultsTF, getAllResultsXC,
    CloudflareException, RateLimitException
)
from scrape_tuning import perRequestDelayRange
 
sys.path.insert(0, "engine")
from event_parse import distanceFromEventShort   # shared parser (dict + free-text)

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
 
# isFieldEvent
# Purpose: 1 if event_short is a field event (jump/throw), else 0. Used by the
#          per-div fallback, which has no isField flag to read.
# Arguments: event_short: event code string.
# Output: 1 field / 0 not.
def isFieldEvent(event_short: str) -> int:
    # Field event short codes. Extend if a field short shows up that's not here;
    # the fast path doesn't rely on this (it has isField), so it only matters for
    # the rare per-div fallback.
    FIELD_EVENTS = {
        "hj", "pv", "lj", "tj", "shot", 
        "discus", "javelin", "hammer", "wt"
    }
    return 1 if event_short in FIELD_EVENTS else 0

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
        # Names SchoolName in XC and TeamName in TF.
        "SchoolName": result.get("SchoolName") or result.get("TeamName")
    }

# Threshold for "this looks like a dead IP, not normal 429 noise."
# Counts consecutive event/div fetches that exhausted ALL of
# getMeetResultsTF's internal retries (3 attempts each) — NOT raw 429
# responses. Two such exhausted cycles back to back (6 total 429s) is
# enough to rule out a one-off blip without wasting too much time
# hammering a possibly-dead IP. Tune this after watching real logs.
CONSECUTIVE_FAILURE_THRESHOLD = 2
 
 
# _isStuckPattern
# Purpose: Tiny, isolated decision: given the current consecutive-
#          failure count for this meet, has it crossed the threshold
#          that means "stop treating this as normal noise, treat it as
#          a possibly-dead IP"?
# Arguments:
#           consecutive_failures: count of consecutive exhausted-retry
#                                  failures so far in this meet.
# Output: True if we've hit the threshold, False otherwise.
def _isStuckPattern(consecutive_failures: int) -> bool:
    return consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD

# _retryStuckEventDiv
# Purpose: Handles one event/div that triggered the stuck-session
#          threshold. Pauses all sessions, retries the fetch exactly
#          once, and forces a real VPN rotation if the retry also
#          fails. Always appends exactly one placeholder row if the
#          final outcome is still a failure — never zero, never two.
# Arguments:
#           page, meet_id, meet_info, event_div, events_dict, jwt_token:
#                       same as _collectTFEventDiv — needed to retry it.
#           label: session label for logging.
#           vpn_rotator: the shared VPNRotatorWindows/Linux instance.
#           meets_to_save, athletes_to_save, results_to_save: collector
#                       lists, mutated in place (same as
#                       _collectTFEventDiv normally does).
# Output: None. Mutates the collector lists. Always leaves exactly one
#         placeholder for this event/div if the retry didn't succeed.
async def _retryStuckEventDiv(page, meet_id: int, meet_info: dict,
                               event_div: dict, events_dict: dict,
                               jwt_token: str, label: str, vpn_rotator,
                               meets_to_save: list, athletes_to_save: list,
                               results_to_save: list):
 
    # Step 1 — pause everyone. This call returns once the pause is over
    # (vpn_rotator owns the actual sleep duration).
    await vpn_rotator.handleStuckSession(label)
 
    # Step 2 — retry this exact event/div, ONE time.
    # add_placeholder_on_failure=True here because this IS the final
    # attempt for this event/div if it fails — no further retry follows.
    retry_succeeded = await _collectTFEventDiv(
        page, meet_id, meet_info, event_div, events_dict, jwt_token, label,
        meets_to_save, athletes_to_save, results_to_save,
        add_placeholder_on_failure=True
    )
 
    if retry_succeeded:
        print(f"{label} Stuck-session retry succeeded — IP recovered after pause")
        return
 
    # Step 3 — pause alone didn't fix it. Force a real rotation.
    # Note: rotating does NOT touch meets_to_save/results_to_save and
    # does NOT mark the meet as failed — _collectTFEventDiv's retry call
    # above already appended the one placeholder this event/div gets.
    # The meet keeps going exactly like any other single-event/div
    # failure (see scrapeMeetTF's existing "continue" behavior).
    print(f"{label} Stuck-session retry still failing — forcing VPN rotation")
    await vpn_rotator.rotate(label, "stuck session — persistent 429s after pause")

# _classifyFailure
# Purpose: Small helper — maps a caught exception to a short string
#          describing WHAT KIND of failure it was. Pulled out as its
#          own function so _collectTFEventDiv's except blocks stay
#          short, and so this mapping logic lives in exactly one place.
# Arguments:
#           e: the caught exception instance.
# Output: One of "rate_limited" or "other_error" (a string label).
def _classifyFailure(e: Exception) -> str:
    if isinstance(e, RateLimitException):
        return "rate_limited"
    return "other_error"

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
                                jwt_token: str, label: str) -> list:
    
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
    print("[COLLECT] _collectXCDivision", flush=True)
    # Collect the meet row for this division.
    collected_meets.append((meet_info, div))

    # The division id (anet's IDMeetDiv) is a property of the DIVISION, not of
    # each result row, so read it ONCE here rather than per-result. This is the
    # SAME key saveMeet writes as the meets PK (divData["IDMeetDiv"] -> div_id),
    # so a result carrying this value will match its meets row exactly. Use .get()
    # (not div["IDMeetDiv"]) so a malformed division missing the key degrades to
    # None instead of crashing the whole meet — None is the honest "unknown
    # division" we would otherwise store anyway.
    div_id = div.get("IDMeetDiv")

    # Collect all results and athletes for this division.
    for r in results:

        # Defensive: skip any non-dict row (a malformed/nested result) instead of
        # crashing the whole session on r.get(...). Mirrors _collectFlatEvent's guard.
        if not isinstance(r, dict):
            continue
        
        if not r.get("AthleteID") or not r.get("IDResult"):
            continue

        collected_athletes.append(r)

        # XC results from GetMeetResults carry SchoolName directly.
        # Fall back to TeamName only if SchoolName is missing/empty —
        # "or" skips to the right side if the left is None or "".
        school = r.get("SchoolName") or r.get("TeamName") or "Unknown"

        # Carry the final school alongside the athlete, WITHOUT mutating r.
        collected_athletes.append((r, school))
        # Carry div_id ALONGSIDE r (4th slot), NOT stamped onto r: r is shared
        # with the athlete collectors and must not be mutated (same reasoning the
        # school comment above states). div_id rides the tuple exactly as school
        # does. THIS is the fix for div_id landing NULL on saved XC results.
        collected_results.append((r, meet_info, school, div_id))

# _collectXCFlatEvent
# Purpose: Turn ONE flatEvents entry from GetAllResultsData (XC) into collector
#          rows — the fast-path analog of _collectXCDivision, which fetched each
#          division separately. The key win: an XC flatEvents entry uses the SAME
#          field names as an xcDivisions[] entry (IDMeetDiv, Meters, Division,
#          LevelMask, CourseId), so it can be handed to saveMeet as `divData`
#          unchanged — no remapping needed (unlike the TF flatEvents path, which
#          renamed everything). Results are inline in event["results"], so there's
#          no per-division fetch.
# Arguments:
#           event:
#               One XC flatEvents dict — carries IDMeetDiv, Meters, Division,
#               LevelMask, CourseId, Gender, and an inline results[] list.
#           meet_info:
#               Meet-level dict from getMeetData (ID/Name/Location/jwtMeet).
#           collected_meets:
#               Collector list of (meet_info, div) tuples, mutated in place.
#           collected_athletes:
#               Collector list of athlete dicts, mutated in place.
#           collected_results:
#               Collector list of (result, meet_info, school) tuples, mutated
#               in place.
# Output:
#           None. Mutates the three collector lists.
def _collectXCFlatEvent(event: dict, meet_info: dict,
                        collected_meets: list, collected_athletes: list,
                        collected_results: list):

    # The flatEvents entry IS the division dict saveMeet wants — same keys
    # (IDMeetDiv, Meters, Division, LevelMask, CourseId). So we append it as the
    # `div` half of the (meet_info, div) tuple, exactly like _collectXCDivision
    # did with an xcDivisions[] entry. One meets row per division.
    collected_meets.append((meet_info, event))

    # Same as _collectXCDivision: IDMeetDiv is a DIVISION property. The flatEvents
    # entry IS the division dict (it shares the xcDivisions field names — see this
    # function's docstring), so event["IDMeetDiv"] is this division's id. Read it
    # ONCE here; .get() so a malformed event degrades to None, not a crash.
    div_id = event.get("IDMeetDiv")

    # Walk the inline results for this division. Same per-row guards as
    # _collectXCDivision — skip non-dict junk and rows missing the identifying
    # keys, so one malformed row can't sink the meet.
    for r in event.get("results", []):

        if not isinstance(r, dict):
            continue

        if not r.get("AthleteID") or not r.get("IDResult"):
            continue

        school = r.get("SchoolName") or r.get("TeamName") or "Unknown"

        athlete_row = {
            "AthleteID":  r.get("AthleteID"),
            "FirstName":  r.get("FirstName", ""),
            "LastName":   r.get("LastName", ""),
            "Gender":     r.get("Gender", ""),
            "SchoolName": school,
        }
        collected_athletes.append(athlete_row)
        # 4th slot = div_id, matching _collectXCDivision's tuple contract.
        collected_results.append((r, meet_info, school, div_id))

# _athleteRowsFromResults
# Purpose: Build athlete upsert rows from the SAME (result, meet_info, school)
#          tuples the results insert uses, so athletes.school is byte-identical
#          to results.school for every (athlete_id, school) pair. This closes the
#          FK gap where saveAthletesBulk's _resolveSchool produced a different
#          school string than the result row carried (e.g. "Unattached" vs
#          "36-Unattached"), leaving results_athlete_school_fkey with no match.
# Arguments:
#           collected_results: the (result, meet_info, school) tuple list.
# Output:   a list of athlete dicts shaped for saveAthletesBulk, one per
#           (athlete_id, school) pair actually referenced by a result.
def _athleteRowsFromResults(collected_results):
    seen = set()
    out = []
    # collected_results is now a 4-tuple (…, school, div_id); this function does
    # not need div_id, but the unpack SHAPE must still match — bind it to _div to
    # say "present, deliberately unused". A 3-target unpack here would raise
    # "too many values to unpack" on the first row.
    for resultData, meetData, school, _div in collected_results:
        athlete_id = resultData.get("AthleteID")
        if not athlete_id:
            continue
        # The exact pair the result will reference - dedup so we upsert each once.
        pair = (athlete_id, school or "Unknown")
        if pair in seen:
            continue
        seen.add(pair)
        out.append({
            "AthleteID": athlete_id,
            "FirstName": resultData.get("FirstName", ""),
            "LastName":  resultData.get("LastName", ""),
            "Gender":    resultData.get("Gender", ""),
            # Force saveAthletesBulk down the same value: stash the result's
            # school where _resolveSchool will find it first.
            "SchoolName": school or "Unknown",
        })
    return out

def _debugSchoolMismatch(collected_athletes, collected_results, label):
    print(f"{label} [DEBUG] entered: {len(collected_athletes)} athletes, "
          f"{len(collected_results)} results", flush=True)

    athlete_pairs = set()
    for a in collected_athletes:
        aid = a.get("AthleteID")
        if aid:
            athlete_pairs.add((aid, _resolveSchool(a) or "Unknown"))

    misses = 0
    # 4-tuple now; div_id unused in this debug pass -> _div (shape must match).
    for resultData, meetData, school, _div in collected_results:
        aid = resultData.get("AthleteID")
        if not aid:
            continue
        if (aid, school or "Unknown") not in athlete_pairs:
            misses += 1
            inserted = [s for (a_, s) in athlete_pairs if a_ == aid]
            print(f"{label} [MISMATCH] athlete {aid}: "
                  f"result wants {school!r}, athletes got {inserted!r}", flush=True)

    print(f"{label} [DEBUG] done: {misses} mismatches", flush=True)
            
            # _saveResultAthletesRaw
# Purpose: Insert the (athlete_id, school) pairs the RESULTS reference, using the
#          result's school string VERBATIM - no _resolveSchool, no normalization -
#          so athletes.school is byte-identical to results.school and the FK holds.
# Arguments:
#           conn:              open connection (caller commits).
#           collected_results: the (result, meet_info, school) tuples.
# Output:   none. Upserts one row per distinct (athlete_id, school) pair.
def _saveResultAthletesRaw(conn, collected_results):
    seen = set()
    rows = []
    # 4-tuple now; this raw-athlete upsert doesn't need div_id -> _div. Shape
    # must still match or the unpack raises on the first result.
    for resultData, meetData, school, _div in collected_results:
        athlete_id = resultData.get("AthleteID")
        if not athlete_id:
            continue
        school_value = school or "Unknown"
        pair = (athlete_id, school_value)
        if pair in seen:
            continue
        seen.add(pair)
        rows.append((
            athlete_id,
            resultData.get("FirstName", ""),
            resultData.get("LastName", ""),
            resultData.get("Gender", ""),
            school_value,                      # VERBATIM - same string the result writes
        ))

    if not rows:
        return
    cursor = conn.cursor()
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO athletes (athlete_id, first_name, last_name, gender, school)
        VALUES %s
        ON CONFLICT (athlete_id, school) DO NOTHING
    """, rows)

# _saveXCMeet
# Purpose: Save all collected XC data for one meet in a single DB transaction:
#          meets rows, athletes, results, and (new) the meet_extras blobs.
#
#          CHANGED: takes teams + team_scores and writes the meet_extras row.
#          XC fills teams_json + team_scores_json; event_types_json and
#          relay_legs_json are passed None (structurally absent for XC — no
#          implements, no relays). The write is skipped entirely when there's
#          nothing to store.
# Arguments:
#           meet_id:
#               athletic.net meet ID (also the meet_extras PK half — equals
#               meet_info["ID"] for this meet).
#           label:
#               session label for logging.
#           collected_meets:
#               list of (meet_info, div) tuples.
#           collected_athletes:
#               list of athlete dicts.
#           collected_results:
#               list of (result, meet_info, school) tuples.
#           teams:
#               teams[] roster, or None.
#           team_scores:
#               accumulated teamScores[] list across divisions, or [] / None.
# Output:
#           Number of results saved, or -1 on failure.
def _saveXCMeet(meet_id: int, label: str, collected_meets: list,
                collected_athletes: list, collected_results: list,
                teams=None, team_scores=None) -> int:

    try:
        with getConn() as conn:

            for meet_info_item, div in collected_meets:
                saveMeet(conn, meet_info_item, div)


            saveResultsBulk(conn, collected_results)


            # meet_extras: only write a row if there's something to store.
            # `team_scores` may be an empty list when no division scored, which
            # is falsy — so a meet with neither blob writes no row.
            if teams or team_scores:
                saveMeetExtras(
                    conn, meet_id, "xc",
                    teams,        # teams_json
                    None,         # event_types_json — XC has no implements
                    None,         # relay_legs_json   — XC has no relays
                    team_scores,  # team_scores_json
                )

            conn.commit()
        return len(collected_results)
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"{label} [!] Save failed for meet {meet_id}: {e}")   #     the conn returns to the pool
        return -1

# Flag to skip getting results from meets that only have entries and
# no results.
SKIP_ENTRY_ONLY_DIVISIONS = True

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

        # Entry-only skip: Result == 1 means "has results". Anything else
        # (None / Entry-only) has no results to fetch — skipping avoids a
        # wasted network call and a resultless meets row.
        if SKIP_ENTRY_ONLY_DIVISIONS and div.get("Result") != 1:
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

# _collectAndSaveAllResultsXC
# Purpose: The GetAllResultsData fast path for XC. Iterates flatEvents into the
#          collectors via _collectXCFlatEvent, then saves the whole meet in one
#          transaction through the EXISTING _saveXCMeet (which already handles
#          meet_extras). Pure — no awaits; the one network call already happened
#          in the caller. Mirrors the TF _collectAndSaveAllResults.
# Arguments:
#           meet_id:
#               athletic.net meet ID.
#           meet_info:
#               Meet-level dict from getMeetData.
#           payload:
#               The full GetAllResultsData dict — XC keys are flatEvents,
#               teamScores, duplicateAthletes, correctedResultIDs.
#           label:
#               session label for logging.
# Output:
#           Results saved (int), or -1 on save failure (propagated from
#           _saveXCMeet).
def _collectAndSaveAllResultsXC(meet_id: int, meet_info: dict,
                                payload: dict, label: str) -> int:

    collected_meets    = []
    collected_athletes = []
    collected_results  = []

    # Collect every division's inline results.
    for event in payload.get("flatEvents", []):
        if isinstance(event, dict):
            _collectXCFlatEvent(event, meet_info,
                                collected_meets, collected_athletes,
                                collected_results)
    
    # What athletes does the PAYLOAD have, vs what we collected?
    payload_aids = set()
    for event in payload.get("flatEvents", []):
        if isinstance(event, dict):
            for r in event.get("results", []):
                if isinstance(r, dict) and r.get("AthleteID"):
                    payload_aids.add(r.get("AthleteID"))

    collected_aids = set()
    # 4-tuple now; this diagnostic only needs the result dict -> _sch, _div unused.
    for rd, md, _sch, _div in collected_results:
        collected_aids.add(rd.get("AthleteID"))

    dropped = payload_aids - collected_aids
    
    return _saveXCMeet(
        meet_id, label,
        collected_meets, collected_athletes, collected_results,
        teams=None,
        team_scores=payload.get("teamScores"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# TF scraping
# ─────────────────────────────────────────────────────────────────────────────
#
# BIG IDEA FOR THIS SECTION:
# scrapeMeetTF used to call getMeetDataTF itself, then ask
# _validateTFMeetData a single yes/no question that conflated THREE
# different situations (meet doesn't exist / meet is empty / event
# mapping is broken) into one False.
#
# Now: the CALLER (scrapeMeetBySport) fetches the data and checks
# existence FIRST — exactly like the XC branch already does with
# getMeetData. scrapeMeetTF receives already-fetched meet_info,
# events_dict, and event_divs, and only has to answer "given that this
# meet exists and has event_divs to look at, can I scrape it?" That's
# a much narrower question, which is why _validateTFMeetData shrinks.


# _validateTFMeetData
# Purpose: Given a TF meet that's already confirmed to exist (caller
#          checked meet_info["ID"]), decides whether there's anything
#          useful to scrape.
# Arguments:
#           event_divs: list of event/div combos with results, from
#                       getMeetDataTF.
# Output: True if there's at least one event_div to attempt.
#         False if event_divs is empty (genuinely empty meet).
def _validateTFMeetData(event_divs: list) -> bool:
    
    # True if contains anything, false if empty.
    return bool(event_divs)

# _collectTFEventDiv
# Purpose: Per-div FALLBACK collector — fetches one TF event/div via
#          getMeetResultsTF and appends rows to the collectors. Derives
#          is_field from the event short (no isField flag on this path),
#          skips athlete creation for relays (smushed pseudo-athlete), and
#          keeps field-event marks. Produces the 6-tuple meets row (trailing
#          division=None) and the 8-tuple results row (trailing is_field)
#          the save layer expects.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           meet_info: meet-level dict from getMeetDataTF.
#           event_div: dict identifying the event/div to fetch (e=event_id,
#                      d=div_id).
#           events_dict: event ID -> event info mapping from getMeetDataTF.
#           jwt_token: meet JWT auth for the GetResultsData3 call.
#           label: session label for logging.
#           meets_to_save: collector list for meets_tf rows, mutated in place.
#           athletes_to_save: collector list for athlete dicts, mutated in place.
#           results_to_save: collector list for result tuples, mutated in place.
#           add_placeholder_on_failure: if True, append a placeholder meets row
#                      on a fetch failure so recovery can find it.
# Output: (success: bool, failure_kind: str | None).
async def _collectTFEventDiv(page, meet_id: int, meet_info: dict,
                             event_div: dict, events_dict: dict,
                             jwt_token: str, label: str,
                             meets_to_save: list, athletes_to_save: list,
                             results_to_save: list,
                             add_placeholder_on_failure: bool = True,
                             teams_capture: list = None) -> tuple[bool, str | None]:
    
    event_id = event_div.get("e")
    div_id   = event_div.get("d")

    # ★ THE DIVISION NAME, ON THE NORMAL PATH TOO (owner, 2026-09-17:
    #   "athlete page events aren't just distance, include like
    #   prelims/finals"). anet keeps the ROUND in the division, not in the
    #   event: the event is "1600m" for both the prelim and the final, and
    #   what tells them apart is Division ("Prelims", "Finals", "Open",
    #   "Invitational", "Section 3"). getMeetDataTF has been stashing the
    #   IDDiv -> name map on meet_info all along, and _saveMetaOnlyTF used
    #   it -- but the path that scrapes an actual meet passed None, so
    #   every real row lost the round and the site could only show a
    #   distance. Same lookup, same map, same fallback to None.
    division = (meet_info.get("_divisionByIdDiv") or {}).get(div_id)

    event_info = events_dict.get(event_id)
    if event_info is None:
        # NEW: this is a LOCAL dict lookup, no network call happened.
        # It must never be confused with a rate-limit failure — label
        # it distinctly so scrapeMeetTF knows not to count it toward
        # the stuck-session streak.
        return False, "unmapped_event"
    
    event_short     = event_info["event_short"]
    gender          = event_info["gender"]
    # Converts event name to distance in meters
    distance_meters = distanceFromEventShort(event_short)[0]
    is_relay        = isRelayEvent(event_short)
    is_field        = isFieldEvent(event_short)

    # Gets a meet's results
    try:
        results, teams = await getMeetResultsTF(
            page, meet_id, div_id, event_short, gender, jwt_token
        )

    except CloudflareException:
        raise

    except Exception as e:
        # CHANGED: classify the exception instead of assuming it's
        # always the same kind of problem.
        failure_kind = _classifyFailure(e)

        # Single event/div failure — skip it, don't fail the whole meet.
        # TF meets have many event/div combos so one bad one isn't fatal.
        print(f"{label} [!] getMeetResultsTF failed for meet {meet_id} "
              f"event {event_short} div {div_id}: {e}")
        
        # Still record this event in meets_tf with distance_meters = -1
        # so the recovery script can find it via the gap between
        # meets_tf and tf_scraped_events. If says to only append
        # if the caller asked us to.
        if add_placeholder_on_failure:
            meets_to_save.append((meet_info, div_id, event_id, event_short, -1, division))

        return False, failure_kind

    meets_to_save.append((meet_info, div_id, event_id, event_short, distance_meters, division))

    # Saves all results in the meet and the athelete they are attatched to for 
    # later bulk upload
    for result in results:

        if not isinstance(result, dict):
            continue
        
        # CHANGED: field events keep their mark; running events drop sentinels.
        if is_field:
            if not result.get("Result"):
                continue
        else:
            # ! A NON-FINISH WITH ITS LETTERS IS KEPT (issue 59): the row
            #   saves with no time and the status in `mark`. A sentinel
            #   the feed cannot name is still dropped.
            if isSentinelTime(result.get("SortInt", 0)) and not _statusOf(result):
                continue
        
        # Relay results: skip athlete creation (smushed FirstName + pseudo
        # AthleteID). Keep the relay result row; real runners are in relayLegs.
        if not is_relay:
            athletes_to_save.append(buildAthleteDict(result))
 
        school = result.get("SchoolName") or result.get("TeamName")
        # CHANGED: +is_field (8-tuple).
        results_to_save.append(
            (result, meet_info, div_id, event_id, event_short, is_relay, school, is_field)
        )
 
    return True, None

# _collectFlatEvent
# Purpose: Turns ONE flatEvents entry from GetAllResultsData into collector
#          rows. The wrapper hands us EventShort/isField/Division directly, and
#          its results are inline — no per-event fetch, no event-id map.
#          Defensive: skips junk results so one bad row can't sink the meet.
# Arguments:
#           event: one flatEvents dict — carries DivId, EventId, EventShort,
#                  Division, isField, Round, and an inline results[] list.
#           meet_info: meet-level dict (ID/Name/Location/date/LevelMask).
#           meets_to_save: collector list for meets_tf rows, mutated in place.
#           athletes_to_save: collector list for athlete dicts, mutated in place.
#           results_to_save: collector list for result tuples, mutated in place.
# Output: None.
def _collectFlatEvent(event: dict, meet_info: dict,
                      meets_to_save: list, athletes_to_save: list,
                      results_to_save: list):
 
    div_id      = event.get("DivId")
    event_id    = event.get("EventId")
    event_short = event.get("EventShort")
    division    = event.get("Division")
    is_field    = 1 if event.get("isField") else 0
    is_relay    = isRelayEvent(event_short)
 
    # Field events have no track distance (they have a mark) -> None.
    distance_meters = distanceFromEventShort(event_short)[0]
 
    # One meets_tf row per (div_id, event_id). Prelims and finals share that
    # key but differ by Round on each result, so a single metadata row is
    # correct (saveMeetTF's ON CONFLICT DO NOTHING keeps the first).
    meets_to_save.append(
        (meet_info, div_id, event_id, event_short, distance_meters, division)
    )
    
    # For every result in the event check if it's either invalid or if
    # we can add it to the results and athletes to save.
    for result in event.get("results", []):
 
        if not isinstance(result, dict):
            continue
 
        # Field events: keep only rows with an actual mark.
        # Running events: drop sentinel SortInts (DNF/DNS/DQ).
        if is_field:
            if not result.get("Result"):
                continue
        else:
            if isSentinelTime(result.get("SortInt")) and not _statusOf(result):
                continue                                  # issue 59, as above
 
        # Relay results carry a smushed FirstName ("A<BR>B<BR>C<BR>D") and a
        # pseudo-athlete AthleteID — don't make an athlete record from them.
        # The real runners live in relayLegs (stored as relay_legs_json). The
        # relay result row itself is still kept, with is_relay=1.
        if not is_relay:
            athletes_to_save.append(buildAthleteDict(result))
 
        school = result.get("SchoolName") or result.get("TeamName") or "Unknown"
        results_to_save.append(
            (result, meet_info, div_id, event_id, event_short, is_relay, school, is_field)
        )



# _buildScrapedEventsList
# Purpose: Deduplicated (meet_id, event_short, div_id) keys for
#          logScrapedEventsTFBulk, pulled from results_to_save.
# Arguments:
#           results_to_save: list of 8-tuples (see saveResultsTFBulk).
# Output: list of unique (meet_id, event_short, div_id) tuples.
def _buildScrapedEventsList(results_to_save: list) -> list:

    # Log every successfully scraped event/div combo.
    # We do this after saving results — if saveResultTF crashed partway
    # through we don't want to mark those events as successfully scraped.
    # We use a set to deduplicate because results_to_save has one row per
    # result (many per event), but we only want to log each event once.
    seen = set()
    events = []

    for _, meet_info, div_id, event_id, event_short, _, _, _ in results_to_save:

        # (meet_id, event_short, div_id) is the unique key — same as the
        # PRIMARY KEY in tf_scraped_events.
        key = (meet_info.get("ID"), event_short, div_id)

        if key not in seen:
            seen.add(key)
            events.append(key)

    return events

    # Save through the existing XC save path. teamScores comes precomputed in
    # this payload (no reconstruction from per-result Score+TeamID needed);
    # `teams` has no separate XC array, so pass None and let teamScores fill
    # team_scores_json. _saveXCMeet skips the meet_extras write if both are empty.
    return _saveXCMeet(
        meet_id, label,
        collected_meets, collected_athletes, collected_results,
        teams=None,
        team_scores=payload.get("teamScores"),
    )

# _collectAndSaveAllResults
# Purpose: The GetAllResultsData fast path. Iterates flatEvents into the
#          collectors, then saves the meet + its teams/eventTypes/relayLegs
#          blobs in one transaction. Pure (no awaits) — the one network call
#          already happened in scrapeMeetTF.
# Arguments:
#           meet_id: athletic.net meet ID.
#           meet_info: meet-level dict from getMeetDataTF.
#           payload: the full GetAllResultsData dict (flatEvents, teams,
#                    eventTypes, relayLegs).
#           label: session label for logging.
# Output: results saved, or -1 on save failure.
def _collectAndSaveAllResults(meet_id: int, meet_info: dict,
                              payload: dict, label: str) -> int:
 
    meets_to_save    = []
    athletes_to_save = []
    results_to_save  = []
    
    # Collects all the results for each event in the meet.
    for event in payload.get("flatEvents", []):
        if isinstance(event, dict):
            _collectFlatEvent(event, meet_info,
                              meets_to_save, athletes_to_save, results_to_save)
            
 
    # Saves per-meet blobs, straight from the same payload.
    return _saveTFMeet(
        meet_id, label,
        meets_to_save, athletes_to_save, results_to_save,
        payload.get("teams"),
        payload.get("eventTypes"),
        payload.get("relayLegs"),
    )

# _saveTFMeet
# Purpose: Saves one TF meet in a single transaction — meets_tf rows,
#          athletes, results — then bulk-logs scraped events. Optionally
#          writes the per-meet teams/eventTypes/relayLegs blobs to meet_extras
#          (fast path passes them; the per-div fallback leaves them None).
# Arguments:
#           meet_id: athletic.net meet ID (for logging).
#           label: session label for logging.
#           meets_to_save: list of 6-tuples
#                    (meet_info, div_id, event_id, event_short,
#                     distance_meters, division).
#           athletes_to_save: list of athlete dicts.
#           results_to_save: list of 8-tuples (see saveResultsTFBulk).
#           teams_array: teams[] roster blob, or None on the fallback path.
#           event_types_array: eventTypes[] catalog blob, or None.
#           relay_legs_array: relayLegs[] blob, or None.
# Output: results saved, or -1 on failure.
def _saveTFMeet(meet_id: int, label: str, meets_to_save: list,
                athletes_to_save: list, results_to_save: list,
                teams_array=None, event_types_array=None, relay_legs_array=None) -> int:

    try:
        # Save meet info, athletes info, and results info for TF into the db.
        with getConn() as conn:

            # Meet-level metadata row (meets_tf_meta): venue/gps/season/GoogleData/
            # has_results. The full path never wrote this — only _saveMetaOnlyTF did.
            # Without it, full-path TF scrapes save geometry + results but NO
            # meet-level meta, reintroducing the gap the meta backfill just closed.
            # meet_info is identical on every meets_to_save tuple, so take the first.
            if meets_to_save:
                saveMeetTFMeta(conn, meets_to_save[0][0])

            wrote = 0
            for meet_info, div_id, event_id, event_short, distance_meters, division in meets_to_save:
                saveMeetTF(conn, meet_info, div_id, event_id, event_short,
                           distance_meters, division)
                wrote += 1

            saveAthletesBulk(conn, athletes_to_save)
            saveResultsTFBulk(conn, results_to_save)

            # NEW: per-meet blobs (only the fast path passes them; fallback
            # leaves them None and skips the write).
            if teams_array or event_types_array or relay_legs_array:
                saveMeetExtras(conn, meet_id, "tf",
                               teams_array, event_types_array, relay_legs_array)

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
# Purpose: Scrape a whole TF meet. Fast path = one getAllResultsTF call for the
#          entire meet (~30x fewer requests). Fallback = the old per-div loop
#          when a meet doesn't serve "all results".
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           meet_info: meet-level dict from getMeetDataTF; carries jwtMeet
#                      (auths GetAllResultsData) plus the meet metadata.
#           events_dict: event ID -> event info mapping from getMeetDataTF.
#                        Used ONLY by the per-div fallback; the fast path
#                        ignores it.
#           event_divs: list of event/div combos from getMeetDataTF. Used
#                       ONLY by the per-div fallback.
#           label: session label for logging e.g. "[Session 1]".
#           vpn_rotator: shared VPN rotator. Only the fallback's stuck-session
#                        handling touches it.
# Output: total results saved, -1 on failure, or 0 if the meet is empty.
async def scrapeMeetTF(page, meet_id: int, meet_info: dict,
                       events_dict: dict, event_divs: list,
                       label: str, vpn_rotator) -> int:
 
    jwt_token = meet_info.get("jwtMeet", "")
 
    # ---- Fast path: one call for the whole meet -------------------------- #
    payload = None
    try:
        payload = await getAllResultsTF(page, meet_id, jwt_token)
    except CloudflareException:
        raise   # IP block — bubble up for rotation
    except RateLimitException:
        # Per-session/IP 429. Distinct [429] line so you can grep the rate while
        # tuning perMeetDelayRange. Marked failed -> retried later via meet_queue
        # (the "just slow down" behavior — no per-meet backoff here).
        print(f"{label} [429] rate limited on meet {meet_id} — marking failed")
        return -1
    except Exception as e:
        # Anything else: log and fall through to the per-div path.
        print(f"{label} [!] GetAllResultsData failed for meet {meet_id}: {e} "
              f"— falling back to per-div")
 
    flat_events = payload.get("flatEvents") if isinstance(payload, dict) else None
 
    if flat_events:
        return _collectAndSaveAllResults(meet_id, meet_info, payload, label)
 
    # ---- Fallback: original per-event/div loop --------------------------- #
    # return await _scrapeMeetTFPerDiv(
    #     page, meet_id, meet_info, events_dict, event_divs, label, vpn_rotator
    # )

    # Fallback removed (6/22). No flatEvents on a meet that's already confirmed
    # to exist (scrapeMeetBySport checked meet_info["ID"]) -> treat as a
    # fast-path failure, mark the meet failed so it's retried rather than
    # silently marked done. NOTE: a genuinely empty/entries-only meet also lands
    # here and will retry; accepted tradeoff for never losing a real meet.
    
    # No flatEvents. Distinguish the two cases using event_divs
    # (eventDivsWithResults from getMeetDataTF — what athletic.net itself says
    # this meet has):
    #
    #   event_divs EMPTY -> the meet genuinely has no TF results (entries-only,
    #     cancelled, XC-only with a stray TF queue row, future meet). This is a
    #     PERMANENT state, not a miss. Mark DONE (return 0) so it stops churning
    #     the queue forever. Confirmed via meet 551979: ID present,
    #     eventDivsWithResults=0, flatEvents=0.
    #
    #   event_divs NON-EMPTY -> the meet CLAIMS results but the fast path got
    #     none. That's a real miss (the case the removed fallback used to cover).
    #     Mark failed (-1) so it retries.
    if not event_divs:
        # Genuinely empty — success with 0 results, do not retry.
        return 0
 
    # Has divisions but the fast path returned nothing. Some of these recover
    # per-event, some don't - so route them to the recovery system instead of
    # retrying the (dead) fast path forever. Persist the meets_tf division rows
    # NOW so find_failed_tf_events.py's gap query (meets_tf present,
    # tf_scraped_events absent) catches them and recover_tf_events.py can
    # reconstruct + retry each event-div individually.
    print(f"{label} [recover] Meet {meet_id}: {len(event_divs)} event-divs, "
          f"no flatEvents — saving division metadata for recovery")
    _saveDivisionsForRecovery(meet_id, meet_info, events_dict, event_divs)
    return 0

# _saveDivisionsForRecovery
# Purpose: When the fast path returns no flatEvents but the meet CLAIMS divisions,
#          persist those division rows to meets_tf (results unsaved) so the meet
#          enters the recovery pipeline: find_failed_tf_events.py sees a gap
#          (meets_tf present, tf_scraped_events absent) and queues each event-div
#          for recover_tf_events.py to retry per-event. Reuses saveMeetTF so the
#          rows are byte-identical to the normal save path's, which is what
#          _reconstructEventData reads back.
# Arguments:
#           meet_id:     athletic.net meet ID.
#           meet_info:   meet-level dict (ID/Name/Location/date/LevelMask).
#           events_dict: {event_id: {"event_short", "gender"}} from getMeetDataTF.
#           event_divs:  [{"e": event_id, "d": div_id}, ...] the meet claims.
# Output:   None. Writes meets_tf rows only (no results, no athletes).
def _saveDivisionsForRecovery(meet_id, meet_info, events_dict, event_divs):
    meets_to_save = []

    # For evey event div saves it and then saves the entire meet,
    # allow the recovery to see events but no results - rescrape.
    for event_div in event_divs:
        event_id = event_div.get("e")
        div_id   = event_div.get("d")

        info = events_dict.get(event_id)
        if not info:
            continue                       # no metadata for this event -> skip
        event_short = info.get("event_short")

        distance_meters = distanceFromEventShort(event_short)[0]
        # ⚠ IT USED TO WRITE THE GENDER HERE. saveMeetTF's sixth argument is
        #   the DIVISION -- "Open", "Invitational", "Prelims" -- and this
        #   recovery path put "m"/"f" in it, so a recovered meet's division
        #   column held a letter that means nothing to any reader of it.
        #   The real map is on meet_info; None when it has no entry, which
        #   is what an unknown division is.
        division = (meet_info.get("_divisionByIdDiv") or {}).get(div_id)

        meets_to_save.append(
            (meet_info, div_id, event_id, event_short, distance_meters, division)
        )

    if not meets_to_save:
        return

    with getConn() as conn:
        for meet_info_item, div_id, event_id, event_short, distance_meters, division in meets_to_save:
            saveMeetTF(conn, meet_info_item, div_id, event_id, event_short,
                       distance_meters, division)
        conn.commit()
    
# _scrapeMeetTFPerDiv
# Purpose: Scrapes all results from one TF meet. Gets the meet event/divs,
#          loops over all event/div combos, collects results, then saves
#          everything in one transaction. This is the fallback if we can't
#          scrape all results in one transaction.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           meet_info: dict from getMeetDataTF — caller has already
#                      confirmed meet_info["ID"] is present.
#           events_dict: event ID -> event info mapping, from getMeetDataTF.
#           event_divs: list of event/div combos with results, from
#                       getMeetDataTF.
#           label: session label for logging e.g. "[Session 1]".
# Output: Total results saved, or -1 on failure, 0 if meet is empty.
async def _scrapeMeetTFPerDiv(page, meet_id: int, meet_info: dict,
                        events_dict: dict, event_divs: list,
                        label: str, vpn_rotator) -> int:
    
    # Genuinely empty meet — exists, but nothing to scrape.
    # This is success with 0 results, not a failure.
    if not _validateTFMeetData(event_divs):
        return 0
    
    # event_divs is non-empty but events_dict is empty — every event_div
    # in this meet uses event IDs we don't have in EVENT_ID_TO_SHORT.
    # This is a systemic mapping problem (not "a couple events missing"),
    # so it's a hard failure rather than silently producing all-placeholder
    # rows. Worth investigating EVENT_ID_TO_SHORT if this comes up often.
    if not events_dict:
        print(f"{label} [!] events_dict empty but event_divs non-empty "
              f"for meet {meet_id} — event ID mapping may be stale")
        return -1
    
    # Cookies token for authenticating our requests.
    jwt_token  = meet_info.get("jwtMeet", "")

    # Collect all then save in bulk pattern
    meets_to_save  = []
    athletes_to_save = []
    results_to_save  = []

    # NEW: tracks consecutive exhausted-retry failures within THIS meet.
    # Resets to 0 on every successful event/div, and at the start of
    # every new call to scrapeMeetTF (i.e. every new meet).
    consecutive_failures = 0

    for event_div in event_divs:

        # Pause between event/div fetches — paced to hold the COMBINED
        # GetResultsData3 rate at the target against the shared IP.
        low, high = perRequestDelayRange()
        await asyncio.sleep(random.uniform(low, high))  

        try:
            # CHANGED: add_placeholder_on_failure=False — we don't yet
            # know if this is "normal, one-off failure" or "stuck
            # pattern," so we hold off on the placeholder until we
            # decide which branch we're in, below.
           success, failure_kind = await _collectTFEventDiv(
                page, meet_id, meet_info, event_div, events_dict,
                jwt_token, label,
                meets_to_save, athletes_to_save, results_to_save,
                add_placeholder_on_failure=False,
            )
        except CloudflareException:
            # Fatal for the whole meet — bubble up, exactly as before.
            raise
    
        if success:
            # Reset the streak — this event/div worked, whatever came
            # before it doesn't matter anymore.
            consecutive_failures = 0
            continue

        # ── We're in failure territory. Decide: ordinary, or stuck? ──
        # CHANGED: only a real 429 advances the stuck-session streak.
        # Mapping misses and other_error still get logged + placeholdered
        # below, they just can't trigger a pause/rotation anymore.
        if failure_kind == "rate_limited":
            consecutive_failures += 1
        else:
            # NEW: explicitly log that this failure is NOT counted, so
            # future-you reading logs can tell the difference between
            # "ignored on purpose" and "the counter is silently broken
            # again."
            print(f"{label} [!] {failure_kind} for meet {meet_id} "
                f"event_div {event_div} — not counted toward stuck-session streak")

        if consecutive_failures > 0 and _isStuckPattern(consecutive_failures):
            await _retryStuckEventDiv(
                page, meet_id, meet_info, event_div, events_dict,
                jwt_token, label, vpn_rotator,
                meets_to_save, athletes_to_save, results_to_save
            )
            consecutive_failures = 0

        else:
            # Any failure that did NOT trigger the stuck-retry path gets
            # its placeholder appended here — covers unmapped_event,
            # other_error, and rate_limited failures that are still
            # below the stuck threshold. _retryStuckEventDiv (above)
            # appends its own placeholder for the case it handles, so
            # this branch must not double-append for that case.
            event_id = event_div.get("e")
            div_id   = event_div.get("d")
            event_info = events_dict.get(event_id)
            if event_info:
                meets_to_save.append((
                    meet_info, div_id, event_id,
                    event_info["event_short"], -1, None
                ))
 
    return _saveTFMeet(
        meet_id, label,
        meets_to_save, athletes_to_save, results_to_save
    )

# ─────────────────────────────────────────────────────────────────────────────
# Unified entry point
# ─────────────────────────────────────────────────────────────────────────────
#
# BIG IDEA FOR THIS SECTION:
# scrapeMeetUnified used to do "detect sport, then scrape it, then
# retag the queue." In the new flow, the LOOP in launcher.py already
# knows which sport to try for this meet_id (from meet_queue's
# per-sport status) — there's nothing to detect or retag.
#
# scrapeMeetBySport is the new single entry point: given a meet_id AND
# a known sport, fetch the meet data for that sport, check whether it
# exists, and if so scrape it. Both branches (XC and TF) follow the
# same shape: fetch -> check existence -> empty-meet check -> scrape.
#
# The (n, exists) return tuple is the contract launcher.py depends on:
#   exists=False        -> write NOTHING to meet_queue (try again later)
#   exists=True, n>=0    -> markScraped(meet_id, sport, 1)  [success]
#   exists=True, n==-1   -> markScraped(meet_id, sport, 2)  [failure]


# scrapeMeetBySport
# Purpose: Given a meet_id and a KNOWN sport ("XC" or "TF"), fetches
#          that sport's meet data, checks whether the meet exists, and
#          scrapes it if so. This is the single entry point the
#          sequential-scan loop in launcher.py calls.
# Arguments:
#           page: Playwright page object.
#           meet_id: athletic.net meet ID.
#           sport: "XC" or "TF" — which sport to attempt. The caller
#                  decides this from meet_queue's per-sport status, not
#                  this function.
#           label: session label for logging e.g. "[Session 1]".
# Output: Tuple of (n, exists).
#         exists=False: this meet_id has no meet for this sport (or the
#                        response was empty/malformed in a way that's
#                        indistinguishable from absence). n is always 0
#                        in this case and should be ignored.
#         exists=True, n>=0: meet exists, n results saved (0 is valid —
#                        an empty meet).
#         exists=True, n==-1: meet exists, but scraping or saving failed.
async def scrapeMeetBySport(page, meet_id: int, sport: str, 
                            label: str, vpn_rotator) -> tuple:

    if sport == "XC":

        # Fetch meet-level info and division list.
        try:
            meet_info, divisions = await getMeetData(page, meet_id)
        except CloudflareException:
            # Session-level event — bubble up to launcher's retry/rotation loop.
            raise
        except Exception as e:
            print(f"{label} [!] getMeetData failed for meet {meet_id}: {e}")
            # ⚠ A FAILED FETCH IS A FAILURE, NOT "NO MEET HERE" (2026-09-26,
            #   owner: "find out why St. Mary's Invite wasn't scraped"). The
            #   comment used to say writing nothing retries it; the launcher
            #   in fact writes state 4 for (0, False), which only a new
            #   forward block ever re-asks -- so one timeout lost a real
            #   meet. (-1, True) is state 2: retried on the next start.
            return -1, True
        
        # meet_info["ID"] missing means: no XC meet at this ID, OR the
        # response was empty/malformed (these are indistinguishable —
        # see the discussion in design notes). Either way: write nothing.
        if not meet_info.get("ID"):
            print(f"{label} Meet {meet_id} (XC): skipped (doesn't exist)")
            return 0, False
        
        # ---- Fast path: one getAllResultsData call for the whole meet ---- #
        # Mirrors scrapeMeetTF: token from meet_info, same exception contract,
        # NO fallback to the per-division loop (per the 6/22 TF precedent —
        # fast-path-or-fail so a real miss retries rather than silently 0-ing).
        jwt_token = meet_info.get("jwtMeet", "")

        payload = None
        try:
            payload = await getAllResultsXC(page, meet_id, jwt_token)
        except CloudflareException:
            raise   # IP block — bubble up for rotation
        except RateLimitException:
            # Per-session/IP 429. Distinct [429] line for grepping the rate
            # while tuning perMeetDelayRange. Marked failed -> retried later
            # via meet_queue (no per-meet backoff).
            print(f"{label} [429] rate limited on meet {meet_id} (XC) — marking failed")
            return -1, True
        except Exception as e:
            # Anything else: log and fail the meet (no fallback). It retries.
            print(f"{label} [!] GetAllResultsData (XC) failed for meet {meet_id}: {e}")
            return -1, True

        # Stores as variable to find the payload is empty or not.
        flat_events = payload.get("flatEvents") if isinstance(payload, dict) else None

        if flat_events:
            n = _collectAndSaveAllResultsXC(meet_id, meet_info, payload, label)
        else:
            # Meet exists (ID present) but the fast path returned no flatEvents.
            # Mirror TF: distinguish genuinely-empty from a real miss using
            # `divisions` (what getMeetData itself says this meet has).
            #   divisions EMPTY    -> genuinely no XC results: success, 0, no retry.
            #   divisions NON-EMPTY -> claims results but got none: real miss, retry.
            if not divisions:
                return 0, True
            print(f"{label} [!] Meet {meet_id} (XC) claims {len(divisions)} "
                  f"divisions but GetAllResultsData returned no flatEvents — failed")
            return -1, True

        if n == -1:
            print(f"{label} Meet {meet_id} (XC): failed")
        else:
            print(f"{label} Meet {meet_id} (XC): saved {n} results")

        return n, True
    
    elif sport == "TF":

        # Fetch meet-level info, event ID mapping, and event/div list.
        try:
            meet_info, events_dict, event_divs = await getMeetDataTF(page, meet_id)
        except CloudflareException:
            raise
        except Exception as e:
            print(f"{label} [!] getMeetDataTF failed for meet {meet_id}: {e}")
            return -1, True         # a failure to retry, not "no meet" (XC above)
        
        # Same existence check as XC, mirrored for TF.
        if not meet_info.get("ID"):
            print(f"{label} Meet {meet_id} (TF): skipped (doesn't exist)")
            return 0, False
 
        # Hand off to scrapeMeetTF, which handles the empty-meet case
        # and the empty-events_dict failure case internally.
        n = await scrapeMeetTF(
            page, meet_id, meet_info, events_dict, event_divs,
            label, vpn_rotator 
        )

        if n == -1:
            print(f"{label} Meet {meet_id} (TF): failed")
        else:
            print(f"{label} Meet {meet_id} (TF): saved {n} results")
                  
        return n, True
    
    else:
        # Defensive — launcher.py should only ever pass "XC" or "TF".
        # If this ever fires, it's a bug in the caller, not a scraping
        # outcome, so we raise rather than silently returning a result
        # that would get written to meet_queue.
        # !r is a conversion flag inside an f-string, it tells Python
        # to use repr() on the value instead of str(), which add '' around
        # the value.
        raise ValueError(f"scrapeMeetBySport got unknown sport: {sport!r}")

    
# ─────────────────────────────────────────────────────────────────────────────
# Meta-only TF re-scrape (no results fetch)
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY: the two meets_tf bugs (NULL source rollback + the (div_id,event_id) PK
# collision) meant almost no TF meet metadata ever saved. The RESULTS are intact
# (158M rows), so we don't re-pull them — we only need to lay down meets_tf rows
# (+ the new meets_tf_meta row). GetMeetData(sport=tf) carries everything needed
# at the meet+division grain WITHOUT a results call:
#   - meet.Location  -> venue/gps/state/track_type/length/indoor + GoogleData
#   - tfDivisions    -> div_id + division name (via events_dict/getMeetDataTF)
#   - eventDivsWithResults (event_divs) -> the (event_id, div_id) grid
# event_short / distance_meters are NOT in this call; they're filled afterward by
# a SQL copy from results_tf (which already has event_short per result).


# _saveMetaOnlyTF
# Purpose: Write a TF meet's metadata with NO results. Reuses the exact
#          recovery-write pattern (_saveDivisionsForRecovery): walk event_divs ->
#          saveMeetTF per (div_id, event_id). ALSO writes the one meet-level
#          meets_tf_meta row. event_short/distance come from events_dict where
#          present; the SQL backfill fixes any gaps from results_tf afterward.
# Arguments:
#           meet_id:     athletic.net meet ID.
#           meet_info:   the `meet` dict from getMeetDataTF (ID/Name/Location/
#                        SeasonID/Gender/Template/LevelMask/MeetDate/...).
#           events_dict: {event_id: {"event_short", "gender"}} from getMeetDataTF.
#           event_divs:  [{"e": event_id, "d": div_id}, ...] the meet claims.
#           label:       session label for logging.
# Output:   int — number of meets_tf rows written (0 if the meet has no event-divs).
def _saveMetaOnlyTF(meet_id: int, meet_info: dict, events_dict: dict,
                    event_divs: list, label: str) -> int:

    # Division-name map stashed on meet_info by getMeetDataTF (IDDiv -> name).
    division_by_div = meet_info.get("_divisionByIdDiv", {})

    # Build the per-event rows exactly like _saveDivisionsForRecovery does, so
    # they're byte-identical to the normal path's meets_tf rows.
    meets_to_save = []
    for event_div in event_divs:
        event_id = event_div.get("e")
        div_id   = event_div.get("d")

        # events_dict may lack an entry for an event id; still write the row with
        # event_short=None (the SQL backfill fills it from results_tf later).
        info = events_dict.get(event_id) or {}
        event_short     = info.get("event_short")
        distance_meters = distanceFromEventShort(event_short)[0]
        # Real division name ('Open', 'Invitational'), keyed by div_id (= IDDiv),
        # falling back to None rather than gender if the map lacks it.
        division = division_by_div.get(div_id)

        meets_to_save.append(
            (meet_info, div_id, event_id, event_short, distance_meters, division)
        )

    # One transaction: the meet-level row + all event rows together.
    with getConn() as conn:
        # Meet-level metadata (venue/address/season/GoogleData) — one row.
        saveMeetTFMeta(conn, meet_info)

        # Per-event geometry rows into meets_tf (unchanged saver, 3-col PK).
        for meet_info_item, div_id, event_id, event_short, distance_meters, division in meets_to_save:
            saveMeetTF(conn, meet_info_item, div_id, event_id, event_short,
                       distance_meters, division)

        conn.commit()

    print(f"{label} Meet {meet_id} (TF meta-only): wrote {len(meets_to_save)} "
          f"meets_tf rows + 1 meets_tf_meta")
    return len(meets_to_save)


# scrapeMeetTFMetaOnly
# Purpose: The meta-only entry point for one TF meet: fetch GetMeetData(sport=tf)
#          and write metadata, NO results. Mirrors the TF branch of
#          scrapeMeetBySport's fetch + existence check, then hands off to
#          _saveMetaOnlyTF instead of scrapeMeetTF.
# Arguments:
#           page:        Playwright page object.
#           meet_id:     athletic.net meet ID.
#           label:       session label for logging.
#           vpn_rotator: shared rotator (unused on the meta path, kept for a
#                        uniform call signature with scrapeMeetBySport).
# Output:   tuple (n, exists) matching scrapeMeetBySport's contract:
#             n >= 0  -> meets_tf rows written (0 = empty/doesn't-exist-for-TF)
#             exists  -> whether a TF meet exists at this id
async def scrapeMeetTFMetaOnly(page, meet_id: int, label: str, vpn_rotator) -> tuple:

    try:
        meet_info, events_dict, event_divs = await getMeetDataTF(page, meet_id)
    except CloudflareException:
        raise                               # IP block — bubble to launcher rotation
    except Exception as e:
        print(f"{label} [!] getMeetDataTF failed for meet {meet_id}: {e}")
        return -1, True             # a failure to retry, not "no meet"

    # No TF meet at this id (or empty/malformed response).
    if not meet_info.get("ID"):
        print(f"{label} Meet {meet_id} (TF meta): skipped (doesn't exist)")
        return 0, False

    # Genuinely no event-divs -> meet exists but has nothing to write. Success
    # with 0, do not retry (mirrors scrapeMeetTF's empty-meet handling).
    if not event_divs:
        return 0, True

    n = _saveMetaOnlyTF(meet_id, meet_info, events_dict, event_divs, label)
    return n, True