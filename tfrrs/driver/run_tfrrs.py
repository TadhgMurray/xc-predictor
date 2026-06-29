# Project: xc-predictor
# File:    tfrrs/driver/run_tfrrs.py
# Purpose: The TFRRS orchestration driver - the single caller that ties the
#          built-but-uncalled pieces together. Per meet it runs:
#              fetch -> parse (meta + results + team scores)
#              -> transform -> [save] -> [mark queue]
#          The two bracketed steps are GUARDED: individual-result saving is gated
#          on the identity migration (saveTFRRSResultsBulk needs columns that
#          don't exist yet), and team-score saving has no meet_extras glue at all
#          yet. So today the driver runs end-to-end THROUGH transform and returns
#          the rows in hand; saving + the meet_queue update are explicit seams.

import re
from bs4 import BeautifulSoup
import sys
import json
import time
import random
import psycopg2.extras
import threading
import asyncio


sys.path.insert(0, "tfrrs/parser")
sys.path.insert(0, "tfrrs/scraper")
sys.path.insert(0, "scripts")
from scraper import CloudflareException, RateLimitException
# NOTE: claim is the TFRRS-scoped one (returns (meet_id, sport) tuples), NOT
# anet's getBatchUnscrapedMeets (which returns a dict and claims anet rows).
from database import getConn, claimTFRRSMeetBatch, resetTFRRSInProgress

from fetch_tfrrs import fetchTFPage, IPBlockedException
from parse_meet_meta import parseXCMeetMeta
from parse_xc_page import parseXCPage, parseXCTeams
from parse_tf_page import parseTFPage
from parse_xc_team import parseXCTeamTable
from parse_tf_team import parseTFTeams
from save_tfrrs import buildTFRRSResultRows, saveTFRRSResultsBulk, saveTFRRSResultsBulk, saveTFRRSMeetMeta

# Sport tags - UPPERCASE to match the meet_queue convention ('XC' / 'TF').
SPORT_XC = "XC"
SPORT_TF = "TF"

# How many concurrent sessions to run. Each is one Playwright page draining the
# shared claim queue at the normal per-meet pace. Scale this up; the limiter is
# per-session, so more sessions = more throughput without any one going faster.
SESSION_COUNT = 24

# Save gates. Both are False today and flip to True when their blocker clears:
#   - results: saveTFRRSResultsBulk needs the migration's identity columns
#     (source/native_id/person_id/...) that don't exist on results_tf yet.
#   - teams: there is NO meet_extras glue for TFRRS team scores yet.
# Until a gate is True its save is SKIPPED (not attempted-and-failed), and the
# meet is NOT marked done - so nothing is lost, the meet just stays re-runnable.
SAVE_RESULTS_ENABLED = True
SAVE_TEAMS_ENABLED   = True

# A meet landing page links to its OWN per-gender compiled pages:
#   /results/<meet_id>/m/<slug>  and/or  /results/<meet_id>/f/<slug>
# An event leaf does not. The {sep}{gender}{sep} shape (e.g. "/96875/m/")
# is what distinguishes a meet from an event in the shared /results/ space.
# A real meet links to its OWN compiled pages. The shape differs by sport:
#   TF: /results/<id>/m/<slug>  or  /results/<id>/f/<slug>
#   XC: /results/xc/<id>/<slug>
# An event leaf links UP to a different parent id, so the id won't match.
_TF_SELF_RE = re.compile(r"/results/(\d+)/(?:m|f)/")
_XC_SELF_RE = re.compile(r"/results/xc/(\d+)/")

# Inter-meet delay (seconds). This is the ENTIRE 429 defense — single session,
# single IP, no rotation. Jittered, and leaned higher (3-6) for unattended runs.
PER_MEET_DELAY = (3, 6)

# _borrowConn / _returnConn
# Purpose: Borrow a raw connection from the shared pool and return it. These wrap
#          psycopg2's pool primitives directly (NOT the getConn context manager,
#          which can't span an await). Each async session borrows ONE connection
#          for its whole life and returns it when the queue drains — with 16-24
#          sessions against a 5-250 pool, that's well within budget and avoids
#          per-meet churn. Called via asyncio.to_thread since pool ops block.
# Output:   _borrowConn -> a psycopg2 connection; _returnConn -> None.
def _borrowConn():
    # Mirror getConn's lazy init so the pool exists before we borrow.
    import database
    if database._pool is None:
        database.initPool()
    return database._pool.getconn()


def _returnConn(conn):
    import database
    # rollback any aborted/open txn so a dirty connection doesn't ride back into
    # the pool (same safety getConn's finally block provides).
    try:
        conn.rollback()
    except Exception:
        pass
    database._pool.putconn(conn)


# _isMeetPage
# Purpose: Decide whether a fetched page is a real MEET page (scrape it) or not
#          (delete its queue row). The signal differs by sport:
#            XC: meet pages carry tablesaw-xc result tables and never self-link
#                their own meet id (events are #event<id> in-page anchors), so we
#                detect them structurally — a populated XC meet has >=1 such table.
#            TF: meet pages self-link to their own /m/ and /f/ compiled pages; an
#                event leaf links up to a DIFFERENT parent id, so we id-match.
# Arguments:
#           html:         the fetched page HTML.
#           requested_id: the id we fetched.
# Output:   True if the page is a real meet page; else False.
def _isMeetPage(html, requested_id):
    soup = BeautifulSoup(html, "lxml")

    # XC: a real meet renders tablesaw-xc result tables. (class_ matches a single
    # token, so "tablesaw-xc" hits even though the full class list is longer.)
    if soup.find("table", class_="tablesaw-xc") is not None:
        return True

    # TF: self-link id-match (event leaves point to a different parent id).
    requested = int(requested_id)
    for a in soup.find_all("a", href=True):
        match = _TF_SELF_RE.search(a["href"])
        if match is not None and int(match.group(1)) == requested:
            return True
    return False


# _deleteQueueRow
# Purpose: Remove a (meet_id, sport) row from meet_queue entirely - used when a
#          claimed id turns out to be an event page, not a real meet. Deletion
#          (not scraped=4) because these ids were never real meets; they're noise
#          from the shared id space and shouldn't linger as skip rows.
# Arguments:
#           conn:    open connection (caller commits).
#           meet_id: the id.
#           sport:   'XC' or 'TF'.
# Output:   none.
def _deleteQueueRow(conn, meet_id, sport):
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM meet_queue WHERE meet_id = %s AND sport = %s",
        (meet_id, sport),
    )

# _markMeetDone
# Purpose: Flip a tfrrs meet's row to scraped=1 (success) - the LAST step, run
#          only after a full save. Scoped to (meet_id, sport, source='tfrrs')
#          because a meet_id can have anet AND tfrrs rows; we touch only ours.
# Arguments:
#           conn, meet_id, sport.
# Output:   none. Caller commits (getConn does NOT auto-commit).
def _markMeetDone(conn, meet_id, sport):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE meet_queue SET scraped = 1 "
        "WHERE meet_id = %s AND sport = %s AND source = 'tfrrs'",
        (meet_id, sport),
    )

# _markMeetFailed
# Purpose: Flip a tfrrs meet to scraped=2 (failed) so a blocked / 429 / parse-MISS
#          meet is COUNTED, not silently stranded at scraped=3. Scoped to
#          source='tfrrs'. Caller commits.
# Arguments:
#           conn, meet_id, sport.
# Output:   none.
def _markMeetFailed(conn, meet_id, sport):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE meet_queue SET scraped = 2 "
        "WHERE meet_id = %s AND sport = %s AND source = 'tfrrs'",
        (meet_id, sport),
    )


# _meetBundle
# Purpose: Build the uniform per-meet result the driver returns, so every path
#          (success, parse-miss, either sport) hands back the SAME shape.
# Arguments:
#           meet_id, sport: identify the meet.
#           ok:      True if at least the result rows came through.
#           results: normalized, save-ready result dicts (transform output).
#           teams:   team-score dicts (parseXC/TFTeams output).
#           meta:    meet-metadata dict, or None.
#           note:    short reason string when ok is False, else None.
# Output:   a dict bundling the above plus counts for quick logging.
def _meetBundle(meet_id, sport, ok, results, teams, meta=None, note=None):
    return {
        "meet_id":   meet_id,
        "sport":     sport,
        "ok":        ok,
        "meta":      meta,
        "results":   results,
        "teams":     teams,
        "n_results": len(results),
        "n_teams":   len(teams),
        "note":      note,
    }

# processMeet (REVISED)
# Purpose: Handle one claimed id. Fetch its page ONCE, classify it, then either
#          delete it (event leaf) or scrape it (real meet). The one fetch serves
#          both the meet/event decision AND the scrape - no double request.
# Arguments:
#           conn, meet_id, sport, meet_url, page: as before.
# Output:   the meet bundle, annotated with how it resolved.
async def processMeet(conn, meet_id, sport, meet_url, page=None):

    try:
        html = await fetchTFPage(meet_url, page=page)        # <-- await
    except (CloudflareException, RateLimitException) as exc:
        return _meetBundle(meet_id, sport, False, [], [], note=f"blocked: {exc}")
    except IPBlockedException:
        # Re-raise so the session loop catches it and rotates. Do NOT swallow it
        # into a bundle — the loop needs to see it to trigger rotation.
        raise
    except Exception as exc:
        return _meetBundle(meet_id, sport, False, [], [], note=f"error: {exc}")

    if not _isMeetPage(html, meet_id):
        # _deleteQueueRow + commit are blocking psycopg2 -> run in a thread.
        await asyncio.to_thread(_deleteQueueRow, conn, meet_id, sport)
        await asyncio.to_thread(conn.commit)
        return _meetBundle(meet_id, sport, True, [], [], note="event - deleted")

    try:
        if sport == SPORT_XC:
            bundle = await _processXCMeet(html, meet_id, page=page)   # <-- await
        else:
            bundle = await _processTFMeet(html, meet_id, page=page)   # <-- await
    except Exception as exc:
        return _meetBundle(meet_id, sport, False, [], [], note=f"error: {exc}")

    # _saveBundle is all psycopg2 -> thread it.
    fully_saved = await asyncio.to_thread(_saveBundle, conn, bundle)
    if fully_saved:
        await asyncio.to_thread(_markMeetDone, conn, meet_id, sport)
    elif not bundle["ok"]:
        await asyncio.to_thread(_markMeetFailed, conn, meet_id, sport)
    await asyncio.to_thread(conn.commit)

    bundle["saved"] = fully_saved
    return bundle

# _processXCMeet
# Purpose: Scrape one XC meet from its already-fetched page HTML. The fetch now
#          happens in processMeet (shared with the meet/event classify), so this
#          just runs the parsers on the HTML it's handed.
# Arguments:
#           html:    the meet page HTML (already fetched + confirmed a real meet).
#           meet_id: stamped onto every row.
# Output:   a meet bundle.
async def _processXCMeet(html, meet_id, page=None):
    meta  = parseXCMeetMeta(html)
    rows  = parseXCPage(html, meet_id)
    teams = parseXCTeams(html, meet_id)
    results = buildTFRRSResultRows(rows, meta, meet_id)

    ok = len(results) > 0
    note = None if ok else "no XC results parsed"
    return _meetBundle(meet_id, SPORT_XC, ok, results, teams, meta=meta, note=note)

# _processTFMeet
# Purpose: Scrape one TF meet. The LANDING page HTML is already fetched (shared
#          with the classify) and gives meta + team scores. The /m and /f
#          compiled pages still need fetching - that's where the results live.
# Arguments:
#           landing_html: the meet landing page HTML (already fetched).
#           meet_id:      stamped onto every row.
#           page:         optional Playwright page for fetchTFPage tier-3.
# Output:   a meet bundle.
async def _processTFMeet(landing_html, meet_id, page=None):
    meta  = parseXCMeetMeta(landing_html)
    teams = parseTFTeams(landing_html, meet_id)
 
    rows = []
    for i, gender in enumerate(("m", "f")):
        # Don't fire the m and f fetches back-to-back - that's a 2-request burst
        # on top of the landing fetch with no gap. Small jittered sleep between.
        if i > 0:
            await asyncio.sleep(random.uniform(1, 2))
        url = f"https://www.tfrrs.org/results/{meet_id}/{gender}"
        rows.extend(parseTFPage(await fetchTFPage(url, page=page), meet_id))
 
    results = buildTFRRSResultRows(rows, meta, meet_id)
 
    ok = len(results) > 0
    note = None if ok else "no TF results parsed"
    return _meetBundle(meet_id, SPORT_TF, ok, results, teams, meta=meta, note=note)


# _saveBundle
# Purpose: Persist a processed meet, honoring the two gates. Returns whether the
#          meet was FULLY saved - which is what decides if we may mark it done.
# Arguments:
#           conn:   open DB connection (caller owns commit, per database.py).
#           bundle: a meet bundle from _processXC/TFMeet.
# Output:   True if every enabled-and-required save ran; False if any was gated
#           off (meaning the meet must stay pending).
def _saveBundle(conn, bundle):
    if not bundle["ok"]:
        return False   # nothing worth saving; leave the meet pending
    
    # Meet metadata (venue/city/state/date) — written first, same transaction.
    saveTFRRSMeetMeta(conn, bundle["meta"], bundle["meet_id"], bundle["sport"])


    if SAVE_RESULTS_ENABLED:
        saveTFRRSResultsBulk(conn, bundle["results"])
    else:
        return False   # results can't be written yet -> not fully saved

    if SAVE_TEAMS_ENABLED:
        _saveTeamScores(conn, bundle)   # not built yet; see stub below

    return True

# _cleanTeam
# Purpose: Turn one parsed team dict into the bare facts we store - drop the
#          ok/note parser bookkeeping, keep everything else.
# Arguments:
#           team: one team dict from parseXC/TFTeams.
# Output:   a new dict without the "ok"/"note" keys.
def _cleanTeam(team):
    cleaned = {}
    for key, value in team.items():
        if key in ("ok", "note"):
            continue
        cleaned[key] = value
    return cleaned


# _saveTeamScores
# Purpose: Write a meet's team scores into meet_extras.team_scores_json, the same
#          slot anet uses. One JSONB blob per (meet_id, sport); a re-scrape
#          replaces the list rather than appending.
# Arguments:
#           conn:   open DB connection (caller commits).
#           bundle: a processed meet bundle; bundle["teams"] is the team list.
# Output:   none (writes).
def _saveTeamScores(conn, bundle):

    # Keep only cleanly-parsed teams, stripped to their facts.
    teams = []
    for team in bundle["teams"]:
        if team.get("ok"):
            teams.append(_cleanTeam(team))

    payload = json.dumps(teams)

    cursor = conn.cursor()
    # anet stores team scores in teams_json; TFRRS must match so the engine/Flask
    # read ONE column for both sources. (team_scores_json is vestigial/unused.)
    cursor.execute(
        """
        INSERT INTO meet_extras (meet_id, sport, source, teams_json)
        VALUES (%s, %s, 'tfrrs', %s)
        ON CONFLICT (meet_id, sport, source)
        DO UPDATE SET teams_json = EXCLUDED.teams_json
        """,
        (bundle["meet_id"], bundle["sport"], psycopg2.extras.Json(teams)),
    )

# Add to chunk 1's imports:
#   from async_db_helper import runDbCall   # if the driver ends up async
# (kept commented until chunk 5 decides sync vs async)

# _markMeetDone
# Purpose: Flip a meet's row to scraped=1 (success) - the LAST step, run only
#          after a full save. Sport-specific because a meet_id can have both an
#          XC and a TF queue row (composite PK (meet_id, sport)); we must touch
#          only the one we scraped.
# Arguments:
#           conn:    open DB connection.
#           meet_id: the meet.
#           sport:   'XC' or 'TF' (UPPERCASE, matching the queue).
# Output:   none. Caller commits. (getConn does NOT auto-commit - a write needs
#           an explicit conn.commit(), per the database.py note.)
def _markMeetDone(conn, meet_id, sport):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE meet_queue SET scraped = 1 WHERE meet_id = %s AND sport = %s",
        (meet_id, sport),
    )

# _printMeetResult
# Purpose: Print a one-line per-meet summary from a processMeet bundle, mirroring
#          the old [ok]/[MISS] format. flush=True so output appears immediately
#          during a long run (Python buffers stdout otherwise -> looks frozen).
# Arguments:
#           n:      running meet count (for progress).
#           bundle: the dict processMeet returned.
# Output:   none (prints).
def _printMeetResult(n, bundle):
    # bundle["results"]/["teams"] are the parsed lists; len() gives the counts.
    # Adjust the keys here if _meetBundle names them differently.
    n_results = len(bundle.get("results", []))
    n_teams   = len(bundle.get("teams", []))
    saved     = bundle.get("saved", False)
    note      = bundle.get("note") or ""
    tag       = "ok" if saved else "MISS"
    print(f"[{n}] [{tag}] {bundle['sport']} {bundle['meet_id']}: "
          f"{n_results} results, {n_teams} teams {note}".rstrip(), flush=True)
    
# _sessionWorker
# Purpose: One session's drain loop. Claims batches from the shared tfrrs queue
#          and processes them sequentially at the normal per-meet delay, until the
#          queue is empty. Identical to the single-session loop — the only thing
#          that makes this concurrent is running several of these on threads, each
#          with its OWN Playwright page (sessions must not share a page).
# Arguments:
#           session_idx: this worker's index (for labelled output).
#           url_for:     callable (meet_id, sport) -> URL.
#           page:        this session's OWN Playwright page.
#           counter:     a shared [count] list + lock for a global progress number.
# Output:   none.
async def _sessionWorker(session_idx, url_for, page, rotator, counter):
    gen = 0   # this session's last-seen rotation generation (for stagger)
    # Borrow ONE connection for this session's whole lifetime (off the loop).
    conn = await asyncio.to_thread(_borrowConn)
    try:
        while True:
            batch = await asyncio.to_thread(claimTFRRSMeetBatch, CLAIM_BATCH_SIZE)
            if not batch:
                break

            for meet_id, sport in batch:
                gen = await rotator.waitForTunnel(session_idx, gen, SESSION_COUNT)

                url = url_for(meet_id, sport)
                try:
                    bundle = await processMeet(conn, meet_id, sport, url, page=page)
                except IPBlockedException:
                    # roll back any partial txn before rotating, so this session's
                    # conn is clean when it resumes.
                    await asyncio.to_thread(conn.rollback)
                    await rotator.rotate(f"[S{session_idx}]", "CloudFront IP block")
                    continue

                counter[0] += 1
                _printMeetResult(counter[0], bundle)
                await rotator.checkRotation(f"[S{session_idx}]")
                await asyncio.sleep(random.uniform(*PER_MEET_DELAY))
    finally:
        # Always return the connection when this session ends (queue drained or
        # an unexpected error), so the pool slot isn't leaked.
        await asyncio.to_thread(_returnConn, conn)


# How many meets to claim per round trip. The drain stays sequential (one fetch
# at a time, single IP); the batch just limits how many claimed rows we hold
# before processing, so a crash strands at most this many at scraped=3.
CLAIM_BATCH_SIZE = 50


# runDrain
# Purpose: Drain the tfrrs meet_queue until empty. Repeatedly claims a batch of
#          pending (scraped=0, source='tfrrs') meets, processes each sequentially,
#          and stops when a claim comes back empty (queue exhausted).
# Arguments:
#           url_for: callable (meet_id, sport) -> URL to fetch.
#           page:    the Playwright page (passed through to processMeet).
# Output:   none. Each meet's status is written by processMeet.
async def runDrain(make_page, url_for, rotator):
    resetTFRRSInProgress()

    counter = [0]   # shared global meet count (index 0); see _sessionWorker note

    tasks = []
    for i in range(SESSION_COUNT):
        page = await make_page()           # each session its own async page
        tasks.append(asyncio.create_task(
            _sessionWorker(i, url_for, page, rotator, counter)))

    await asyncio.gather(*tasks)
    print(f"[drain] queue empty — processed {counter[0]} meets", flush=True)


# _logMeet
# Purpose: One-line progress print per meet. Plain print for now - swap for the
#          project logger when wired.
# Arguments:
#           bundle: a processed meet bundle.
# Output:   none.
def _logMeet(bundle):
    flag = "ok" if bundle["ok"] else "MISS"
    saved = "saved" if bundle.get("saved") else "pending"
    print(f"[{flag}] {bundle['sport']} {bundle['meet_id']}: "
          f"{bundle['n_results']} results, {bundle['n_teams']} teams "
          f"({saved}) {bundle['note'] or ''}".rstrip())