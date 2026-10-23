# Project: xc-predictor / racecast
# File:    uploads.py
# Purpose: Missing meets, uploaded by the people who ran them (owner,
#          2026-10-10, approved). A signed-in coach or athlete uploads a
#          Hy-Tek results file or a CSV; it is read (results_parse.py) into
#          a STAGING area -- result_upload, result_upload_row -- and shown
#          back as a preview with every warning. Sent for review, it joins
#          the owner's queue at /account/admin/uploads. Only the owner's
#          Approve writes anything the site or the pipeline reads.
#
#     python racecast/uploads.py --init     # create the staging tables (once)
#     python racecast/uploads.py --check    # are they there
#
# ★ NOTHING REACHES THE LIVE TABLES WITHOUT THE OWNER (owner's rule: nothing
#   that changes data is applied without approval). An upload is 'draft'
#   until its uploader sends it, 'submitted' until the owner decides, then
#   'approved' (written), 'flagged' (looks like a meet we have: NOT written)
#   or 'rejected'. The uploader can discard a draft or a submission; nobody
#   can edit one after approval.
#
# ★ APPROVED ROWS GO WHERE THE SCRAPERS' ROWS GO, TAGGED (applyUpload).
#   meets + results for cross country, meets_tf + meets_tf_meta + results_tf
#   for track -- the raw tables the scrapers feed -- with source = 'upload'
#   and id_system = 'upload', anet-shaped (a meets row per race carries the
#   distance, as anet's does). The ids live in a range nothing else uses:
#   meet_id and div_id NEGATIVE (anet's and tfrrs's are positive), result_id
#   a negative content hash like tfrrs's mint, so a re-approved upload
#   cannot write a row twice.
#
# ★ WHAT THE PIPELINE DOES WITH THEM (2026-10-10, the gaps closed). The rows
#   carry the name, grade and school as written, and NO person_id. Then:
#     04a  scripts/link_uploads.py (pass 4 of link_tfrrs_rows) links each to
#          an existing person by name + school + generation -- the teamless
#          and profile_school linkers' rules -- or makes a new person where
#          no namesake could be him; doubtful rows are REPORT-ONLY, in
#          upload_link_report, shown on the upload's admin page. Logged in
#          person_link_log ('upload' / 'upload_mint'), --undo.
#     04c  engine/twin_flag.py's twin_upload: an uploaded race a feed later
#          scrapes is flagged on the upload's side (the feed's copy stays).
#     04d  person_gender reads the uploaded division's gender word.
#     05   backfill_normalize: anet-shaped (distance through meets.div_id,
#          track through event_short), and the XC gender from the
#          division ("Girls Varsity") when the person gives none.
#     07+  rated, on the boards and the athlete page like any feed's row.
#   PIPELINE_GAPS below is empty now; PIPELINE_STEPS says the above on the
#   admin page.
#
# ★ A MEET WE ALREADY HAVE IS FLAGGED, NOT WRITTEN (dupesFor). Same date
#   (or inside the upload's date range) and a name or venue that reads the
#   same, against anet XC (meets), anet track (meets_tf_meta), tfrrs
#   (meets_tfrrs) and earlier approved uploads. The owner sees the
#   candidates before deciding, and a flagged upload needs a second,
#   explicit "insert anyway".
#
# ! THE FILE IS DATA. 9 MB at most (app.MAX_CONTENT_LENGTH refuses anything
#   larger before a route runs); .txt/.htm/.html/.csv only; executables,
#   archives, PDFs and images refused by their first bytes; parsed with
#   regular expressions and the csv/html.parser modules -- no eval, nothing
#   fetched, no script run. The raw text is kept (for the owner's review)
#   and only ever shown escaped.
#
# ! RATE LIMITED PER ACCOUNT: UPLOADS_PER_HOUR and UPLOADS_PER_DAY, counted
#   from the table itself, so eight workers agree without shared memory.
#   Every POST is CSRF + same origin (accounts.csrfOk).
#
# ★ NO ACCOUNT NEEDED (owner, 2026-10-23: "you should be able to upload
#   without an account, just need to put an email in that case"). Signed
#   out, the form asks for an email address (and Turnstile, when it is on).
#   The upload is the same draft, with account_id NULL, guest_email and the
#   hash of a secret link: /account/uploads/g/<token>, mailed to that address and
#   shown at once. The link is the guest's whole access -- view, send,
#   discard -- the way a login link is; only its hash is stored. The same
#   limits apply, counted per IP and per email address. The owner's review
#   is unchanged, and the decision mail goes to the guest's address.
#
# ★ A LINK INSTEAD OF A FILE (owner, 2026-10-23: "add a way to upload a url
#   ... then we can manually do it (just say file is preferred)"). The form
#   takes a results link when there is no file: the upload goes straight to
#   the queue as file_kind 'url', no rows, source_url set. The owner then
#     - queues it for the scrape, when it is an athletic.net or tfrrs meet
#       (fixes.queueTarget, meet_queue -- what a 'missing meet' fix does);
#     - "Fetch and read": upload_fetch.fetchResults, once, safely; the rows
#       land in the same upload, which is then reviewed like any other;
#     - or attaches the file by hand when the fetch cannot read it.
#   The server never fetches on the uploader's submit, only on the owner's
#   button.

import datetime
import difflib
import hashlib
import json
import re
import sys
import urllib.parse

from flask import Blueprint, Response, abort, make_response, redirect, render_template, request

import accounts as AC
import results_parse as RP

bp = Blueprint("uploads", __name__)

SOURCE = "upload"                 # results.source / meets.source on approval
MAX_UPLOAD_BYTES = 9 * 1024 * 1024     # = app.MAX_CONTENT_LENGTH
UPLOADS_PER_HOUR = 4
UPLOADS_PER_DAY = 12
PREVIEW_ROWS = 400
MEET_BASE = 900_000_000           # upload meet_id = -(MEET_BASE + upload id)
_BIGINT_62 = (1 << 62) - 1
STATUS_WORDS = {"draft": "Draft: not sent yet", "submitted": "Waiting for review",
                "approved": "Approved and added", "flagged": "Held: looks like a meet we have",
                "rejected": "Not added", "discarded": "Discarded"}
SPORTS = ("XC", "TF")

# ★ SAID, NOT HIDDEN: what the nightly pipeline still needs before an
#   approved upload is RATED (see the header). Shown on the admin page.
#   ★ 2026-10-10: both gaps closed (identity: scripts/link_uploads.py; XC
#   gender: backfill_normalize._UPLOAD_DIV_GENDER, person_gender's label).
PIPELINE_GAPS = ()
PIPELINE_STEPS = (
    "04a links each row to an existing athlete by name + school + grade (the teamless and "
    "profile_school linkers' rules), or makes a new athlete where no namesake could be them. "
    "Doubtful rows stay unlinked and are listed on the upload's page (upload_link_report).",
    "04c flags the upload's copy if a feed later scrapes the same race (twin_upload).",
    "05 normalises them (the cross country gender from the division title), and the solve "
    "rates them; the boards and athlete pages show them like any other row.",
)

DDL = """
CREATE TABLE IF NOT EXISTS result_upload (
    id             bigserial PRIMARY KEY,
    account_id     bigint REFERENCES account(id) ON DELETE SET NULL,
    status         text NOT NULL DEFAULT 'draft',
    file_name      text,
    file_kind      text,
    file_bytes     integer,
    file_sha256    text,
    raw_text       text,
    software       text,
    sport          text,
    meet_name      text,
    meet_date      text,
    meet_date_end  text,
    location       text,
    state          text,
    distance_m     real,
    n_rows         integer NOT NULL DEFAULT 0,
    n_events       integer NOT NULL DEFAULT 0,
    events         jsonb,
    warnings       jsonb,
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    submitted_at   timestamptz,
    decided_at     timestamptz,
    decided_by     text,
    decision_note  text,
    dupes          jsonb,
    applied        jsonb,
    live_meet_id   bigint
);
CREATE INDEX IF NOT EXISTS result_upload_account_idx ON result_upload (account_id, created_at);
CREATE INDEX IF NOT EXISTS result_upload_status_idx ON result_upload (status, submitted_at);
ALTER TABLE result_upload ADD COLUMN IF NOT EXISTS guest_email text;
ALTER TABLE result_upload ADD COLUMN IF NOT EXISTS guest_token_hash text;
ALTER TABLE result_upload ADD COLUMN IF NOT EXISTS guest_ip text;
ALTER TABLE result_upload ADD COLUMN IF NOT EXISTS source_url text;
CREATE UNIQUE INDEX IF NOT EXISTS result_upload_guest_token_idx ON result_upload (guest_token_hash)
    WHERE guest_token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS result_upload_guest_ip_idx ON result_upload (guest_ip, created_at)
    WHERE account_id IS NULL;
CREATE TABLE IF NOT EXISTS result_upload_row (
    upload_id     bigint NOT NULL REFERENCES result_upload(id) ON DELETE CASCADE,
    n             integer NOT NULL,
    event_n       integer NOT NULL,
    event         text,
    gender        text,
    division      text,
    distance_m    real,
    round         text,
    place         integer,
    name          text NOT NULL,
    name_raw      text,
    grade         text,
    school        text,
    time_text     text,
    time_seconds  real,
    status        text,
    line          integer,
    warning       text,
    PRIMARY KEY (upload_id, n)
);
"""


# ---- tables ------------------------------------------------------------

def initTables(conn):
    """accounts.initTables' rule: the DDL alone, under a lock timeout."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(DDL)
    conn.commit()


_READY = {"checked": False, "ready": False, "guest": False}


def tablesReady(cur):
    """Once per process (follows.tablesReady's pattern)."""
    if not _READY["checked"]:
        try:
            cur.execute("SELECT to_regclass('public.result_upload') AS u, "
                        "to_regclass('public.result_upload_row') AS r")
            row = AC._one(cur)
            _READY["ready"] = bool(row and row.get("u") and row.get("r"))
            if _READY["ready"]:
                cur.execute("""SELECT 1 AS ok FROM information_schema.columns
                               WHERE table_name = 'result_upload' AND column_name = 'guest_token_hash'""")
                _READY["guest"] = bool(AC._one(cur))
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            _READY["ready"] = False
        _READY["checked"] = True
        if not _READY["ready"]:
            print("[uploads] tables are not there: run racecast/uploads.py --init", flush=True)
        elif not _READY["guest"]:
            print("[uploads] no guest columns: run racecast/uploads.py --init for uploads "
                  "without an account", flush=True)
    return _READY["ready"]


def guestReady(cur):
    """The guest columns are there (an --init after 2026-10-23)."""
    return tablesReady(cur) and _READY["guest"]


# ---- admin gate (app._statusAdmin's rule, for the blueprints) -----------

def requireAdmin():
    """(session, None) for an admin; a login redirect when signed out; a
    plain 404 for anyone else, with the reason in the log."""
    sess = AC.currentSession()
    if not sess:
        return None, redirect("/login?" + urllib.parse.urlencode({"next": request.path}))
    if not AC.isAdmin(sess["account"]):
        print(f"[admin] {request.path} refused: account {sess['account'].get('id')} is not in "
              f"XCP_ADMIN_EMAILS ({len(AC.adminEmails())} address(es) configured)", flush=True)
        abort(404)
    return sess, None


# ---- pure helpers --------------------------------------------------------

def mintResultId(upload_id, row):
    """A negative content hash (save_tfrrs._mintResultId's scheme, its own
    prefix): the same row of the same upload is the same id every time."""
    key = "|".join(str(x) for x in ("upload", upload_id, row.get("event_n"), row.get("round"),
                                    (row.get("name") or "").lower(), row.get("time_seconds"),
                                    row.get("status"), row.get("place"), row.get("n")))
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return -(int.from_bytes(digest, "big") & _BIGINT_62) - 1


def meetIdFor(upload_id):
    return -(MEET_BASE + int(upload_id))


def divIdFor(upload_id, event_n):
    """One meets row per race: unique, negative, never an anet div_id."""
    return meetIdFor(upload_id) * 1000 - int(event_n)


_STOP = {"the", "of", "and", "at", "xc", "cc", "cross", "country", "invitational", "invite",
         "meet", "classic", "annual", "championship", "championships", "high", "school", "hs",
         "track", "field", "t&f", "results", "varsity"}


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"\b(19|20)\d{2}\b", " ", s)
    s = re.sub(r"\b\d+(st|nd|rd|th)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def similarity(a, b):
    """0..1: how alike two meet (or venue) names read, once years, ordinals
    and the words every meet uses are gone."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    ta = {t for t in na.split() if t not in _STOP}
    tb = {t for t in nb.split() if t not in _STOP}
    jac = len(ta & tb) / len(ta | tb) if (ta and tb) else 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    return round(max(jac, seq), 3)


NAME_SIM = 0.6
VENUE_SIM = 0.75


def matchMeets(meet_name, location, candidates):
    """The candidates (dicts with name, venue) that read as this meet."""
    out = []
    # ★ A DIFFERENT NUMBER IN THE NAME IS A DIFFERENT MEET (owner,
    #   2026-10-11: "GHSA 6A Sectional 1 (Regions 1,4,7,8)" was held as a
    #   duplicate of Sectional 2 and of 4A Sectional 2, same day, 0.76 alike).
    #   Sectional 1 / 2, 6A / 4A, Region 3 / 5 differ by exactly the tokens
    #   that make them separate races. The years are left out: one meet's
    #   names carry them inconsistently ("2024 ..." on one feed, not the other).
    def _nums(text):
        return {t for t in re.findall(r"\d+[a-z]?", (text or "").lower())
                if not re.fullmatch(r"(19|20)\d\d", t)}
    mine = _nums(meet_name)
    for c in candidates:
        theirs = _nums(c.get("name"))
        # ! CONFLICTING, NOT MERELY UNEQUAL: "GHSA 6A Sectional 1" against
        #   "GHSA 6A Sectional 1 (Regions 1,4,7,8)" is one meet named twice
        if mine and theirs and not (mine <= theirs or theirs <= mine):
            continue
        ns = similarity(meet_name, c.get("name"))
        vs = similarity(location, c.get("venue")) if (location and c.get("venue")) else 0.0
        if ns >= NAME_SIM or vs >= VENUE_SIM:
            out.append(dict(c, name_sim=ns, venue_sim=vs))
    out.sort(key=lambda c: -(max(c["name_sim"], c["venue_sim"])))
    return out[:12]


def _dates(meet_date, meet_date_end):
    d0 = datetime.date.fromisoformat(meet_date)
    d1 = datetime.date.fromisoformat(meet_date_end) if meet_date_end else d0
    if d1 < d0 or (d1 - d0).days > 6:
        d1 = d0
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def checkMeta(form, parsed_meet, parsed_sport):
    """The meet fields the uploader confirms on the preview -> (meta, errors)."""
    errors = []
    name = " ".join((form.get("meet_name") or parsed_meet.get("name") or "").split())[:200]
    date = RP.parseDate(form.get("meet_date") or "") or parsed_meet.get("date")
    date_end = RP.parseDate(form.get("meet_date_end") or "") or parsed_meet.get("date_end")
    loc = " ".join((form.get("location") or parsed_meet.get("location") or "").split())[:200]
    state = (form.get("state") or "").strip().upper()[:20]
    sport = (form.get("sport") or parsed_sport or "").strip().upper()
    dist = (form.get("distance_m") or "").strip()
    if not name:
        errors.append("The meet needs a name.")
    if not date:
        errors.append("The meet needs a date (YYYY-MM-DD).")
    else:
        d = datetime.date.fromisoformat(date)
        if d.year < 1990 or d > datetime.date.today() + datetime.timedelta(days=1):
            errors.append("That date is not a meet that has happened.")
    if sport not in SPORTS:
        errors.append("Pick cross country or track.")
    if state and (len(state) != 2 or not state.isalpha()):
        errors.append("The state is two letters.")
    distance = None
    if dist:
        distance = RP.distanceOf(dist) if re.search(r"[A-Za-z]", dist) else None
        if distance is None:
            try:
                distance = float(dist)
            except ValueError:
                errors.append("Could not read the race distance.")
        if distance is not None and not (400 <= distance <= 42195):
            errors.append("The race distance is in metres, 400 to 42195.")
            distance = None
    return {"meet_name": name or None, "meet_date": date, "meet_date_end": date_end,
            "location": loc or None, "state": state or None, "sport": sport if sport in SPORTS else None,
            "distance_m": distance}, errors


def eventDistances(events, meet_distance):
    """event n -> metres: the title's, else the meet-wide distance the
    uploader gave (cross country files often name none)."""
    return {e["n"]: (e.get("distance_m") or meet_distance) for e in events}


def readyToApply(up, events):
    """What stops an upload being written, as sentences (empty: none)."""
    why = []
    if "n_rows" in up and not up["n_rows"]:
        why.append("no results read yet" if up.get("source_url") else "no results")
    if not up.get("meet_name") or not up.get("meet_date"):
        why.append("no meet name or date")
    if up.get("sport") not in SPORTS:
        why.append("no sport")
    dist = eventDistances(events, up.get("distance_m"))
    for e in events:
        if e.get("kind") == "running" and e.get("n_rows") and not dist.get(e["n"]):
            why.append(f"no distance for {e.get('title')}")
    return why


# ---- database pieces ---------------------------------------------------

def recentCount(cur, account_id):
    cur.execute("""SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS h,
                          count(*) AS d
                   FROM   result_upload
                   WHERE  account_id = %s AND created_at > now() - interval '1 day'""", (account_id,))
    row = AC._one(cur) or {}
    return int(row.get("h") or 0), int(row.get("d") or 0)


def recentGuestCount(cur, ip, email):
    """recentCount for uploads without an account: per IP and per email
    address, whichever is higher."""
    cur.execute("""SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS h,
                          count(*) AS d
                   FROM   result_upload
                   WHERE  account_id IS NULL AND (guest_ip = %s OR guest_email = %s)
                     AND  created_at > now() - interval '1 day'""", (ip, email))
    row = AC._one(cur) or {}
    return int(row.get("h") or 0), int(row.get("d") or 0)


def stage(cur, account_id, filename, data, parsed, form, guest=None):
    """Write one parsed file to staging as a draft -> its id. guest =
    (email, token hash, ip) for an upload without an account."""
    from psycopg2.extras import execute_values
    from jsonb import J as Json   # SQL_ASCII-safe, NUL-free (jsonb.py)
    meta, _errs = checkMeta(form, parsed["meet"], parsed.get("sport"))
    text = RP.decode(data).replace("\x00", "")
    events = parsed["events"]
    cur.execute("""
        INSERT INTO result_upload (account_id, file_name, file_kind, file_bytes, file_sha256, raw_text,
                                   software, sport, meet_name, meet_date, meet_date_end, location,
                                   state, distance_m, n_rows, n_events, events, warnings, note
                                   {guest_cols})
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{guest_vals})
        RETURNING id""".format(guest_cols=", guest_email, guest_token_hash, guest_ip" if guest else "",
                               guest_vals=", %s, %s, %s" if guest else ""),
                (account_id, (filename or "")[:200], parsed["format"], len(data),
                 hashlib.sha256(data).hexdigest(), text, parsed.get("software"), meta["sport"],
                 meta["meet_name"], meta["meet_date"], meta["meet_date_end"], meta["location"],
                 meta["state"], meta["distance_m"], len(parsed["rows"]), len(events),
                 Json(events), Json(parsed["warnings"][:500]),
                 (form.get("note") or "").strip()[:1000] or None) + tuple(guest or ()))
    uid = AC._one(cur)["id"]
    _insertRows(cur, uid, parsed)
    return uid


def _insertRows(cur, uid, parsed):
    """The parsed rows into result_upload_row for one upload."""
    from psycopg2.extras import execute_values
    events = parsed["events"]
    evs = {e["n"]: e for e in events}
    rows = []
    for n, r in enumerate(parsed["rows"], start=1):
        e = evs.get(r["event"]) or {}
        rows.append((uid, n, r["event"], (e.get("title") or "")[:200], e.get("gender"),
                     (e.get("division") or "")[:80] or None, e.get("distance_m"), r.get("round") or "F",
                     r.get("place"), (r["name"] or "")[:120], (r.get("name_raw") or "")[:160],
                     (r.get("grade") or "")[:12] or None, (r.get("school") or "")[:160] or None,
                     (r.get("time_text") or "")[:24], r.get("time_seconds"), r.get("status"),
                     r.get("line"), r.get("warning")))
    if rows:
        execute_values(cur, """
            INSERT INTO result_upload_row (upload_id, n, event_n, event, gender, division, distance_m,
                                           round, place, name, name_raw, grade, school, time_text,
                                           time_seconds, status, line, warning)
            VALUES %s""", rows, page_size=2000)


URL_MAX = 1000


def cleanUrl(text):
    """A results link as sent: http(s), a host, no spaces -> the url or None.
    Nothing is fetched here."""
    u = (text or "").strip()
    if not u or len(u) > URL_MAX or any(c.isspace() for c in u):
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    parts = urllib.parse.urlsplit(u)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        return None
    return u


def stageUrl(cur, account_id, url, form, guest=None):
    """A results link, straight into the owner's queue -> its id."""
    sport = (form.get("sport") or "").upper()
    cur.execute("""
        INSERT INTO result_upload (account_id, status, file_name, file_kind, file_bytes, sport,
                                   meet_name, meet_date, location, note, source_url, submitted_at
                                   {guest_cols})
        VALUES (%s, 'submitted', %s, 'url', 0, %s, %s, %s, %s, %s, %s, now(){guest_vals})
        RETURNING id""".format(guest_cols=", guest_email, guest_token_hash, guest_ip" if guest else "",
                               guest_vals=", %s, %s, %s" if guest else ""),
                (account_id, urllib.parse.urlsplit(url).hostname[:200], sport if sport in SPORTS else None,
                 (form.get("meet_name") or "").strip()[:200] or None,
                 RP.parseDate(form.get("meet_date") or ""),
                 (form.get("location") or "").strip()[:200] or None,
                 (form.get("note") or "").strip()[:1000] or None, url) + tuple(guest or ()))
    return AC._one(cur)["id"]


def restage(cur, up, filename, data, parsed):
    """A link's results, read (fetched or attached by the owner), into the
    same upload: the file's fields, the meet's details where the uploader
    left them blank, and every row. Its status does not change."""
    from jsonb import J as Json
    meta, _errs = checkMeta({}, parsed["meet"], parsed.get("sport"))
    text = RP.decode(data).replace("\x00", "")
    cur.execute("DELETE FROM result_upload_row WHERE upload_id = %s", (up["id"],))
    cur.execute("""UPDATE result_upload SET file_name = %s, file_kind = %s, file_bytes = %s,
                          file_sha256 = %s, raw_text = %s, software = %s,
                          sport = COALESCE(sport, %s), meet_name = COALESCE(meet_name, %s),
                          meet_date = COALESCE(meet_date, %s), meet_date_end = COALESCE(meet_date_end, %s),
                          location = COALESCE(location, %s), state = COALESCE(state, %s),
                          n_rows = %s, n_events = %s, events = %s, warnings = %s
                   WHERE id = %s""",
                ((filename or "")[:200], parsed["format"], len(data), hashlib.sha256(data).hexdigest(),
                 text, parsed.get("software"), meta["sport"], meta["meet_name"], meta["meet_date"],
                 meta["meet_date_end"], meta["location"], meta["state"], len(parsed["rows"]),
                 len(parsed["events"]), Json(parsed["events"]), Json(parsed["warnings"][:500]), up["id"]))
    _insertRows(cur, up["id"], parsed)


def loadUpload(cur, uid):
    cur.execute("""SELECT u.*, COALESCE(a.email, u.guest_email) AS account_email, a.name AS account_name
                   FROM result_upload u LEFT JOIN account a ON a.id = u.account_id
                   WHERE u.id = %s""", (uid,))
    return AC._one(cur)


def loadGuestUpload(cur, token, lock=False):
    """The upload a guest's secret link names, or None."""
    if not token or len(token) > 100:
        return None
    cur.execute("""SELECT u.*, u.guest_email AS account_email, NULL AS account_name
                   FROM result_upload u
                   WHERE u.guest_token_hash = %s AND u.account_id IS NULL"""
                + (" FOR UPDATE" if lock else ""), (AC.hashToken(token),))
    return AC._one(cur)


def loadRows(cur, uid, limit=None):
    cur.execute("SELECT * FROM result_upload_row WHERE upload_id = %s ORDER BY event_n, round DESC, "
                "place NULLS LAST, n" + (f" LIMIT {int(limit)}" if limit else ""), (uid,))
    return [dict(r) for r in cur.fetchall()]


def _regclass(cur, name):
    cur.execute("SELECT to_regclass(%s) AS t", (name,))
    row = AC._one(cur)
    return bool(row and row.get("t"))


def dupesFor(cur, up):
    """Meets we already hold that read as this upload: same date (or in its
    range), and a name or venue that reads the same."""
    if not up.get("meet_date"):
        return []
    days = _dates(up["meet_date"], up.get("meet_date_end"))
    cands = []
    queries = (
        ("meets", "anet XC", """SELECT meet_id, min(meet_name) AS name, min(course_name) AS venue,
                                       min(left(meet_date, 10)) AS date, min(source) AS source
                                FROM meets WHERE left(meet_date, 10) = ANY(%s) GROUP BY meet_id"""),
        ("meets_tf_meta", "anet TF", """SELECT meet_id, meet_name AS name, venue_name AS venue,
                                              left(meet_date, 10) AS date, source
                                       FROM meets_tf_meta WHERE left(meet_date, 10) = ANY(%s)"""),
        ("meets_tfrrs", "tfrrs", """SELECT meet_id, meet_name AS name, venue_name AS venue,
                                           left(date, 10) AS date, 'tfrrs' AS source, sport
                                    FROM meets_tfrrs WHERE left(date, 10) = ANY(%s)"""),
    )
    # ! A BOUND ON THE ADMIN'S REQUEST: left(meet_date, 10) reads every
    #   meets row (no date index); seconds, not a stalled worker.
    cur.execute("SET LOCAL statement_timeout = '30s'")
    for table, label, sql in queries:
        if not _regclass(cur, table):
            continue
        cur.execute(sql, (days,))
        for r in cur.fetchall():
            d = dict(r)
            d["where"] = label if d.get("source") != SOURCE else "an earlier upload"
            cands.append(d)
    cur.execute("""SELECT id, live_meet_id AS meet_id, meet_name AS name, location AS venue,
                          meet_date AS date, 'upload' AS source
                   FROM result_upload WHERE status = 'approved' AND id <> %s
                     AND meet_date = ANY(%s)""", (up["id"], days))
    for r in cur.fetchall():
        cands.append(dict(r, where=f"upload #{r['id']}"))
    return matchMeets(up.get("meet_name"), up.get("location"), cands)


def linkStatus(cur, up):
    """After Approve: how many of the upload's rows the nightly linker gave
    an athlete (scripts/link_uploads.py, pipeline 04a), and the doubtful
    ones it left for the owner (upload_link_report) -- the report-only half
    of the owner's rule, shown where the upload is reviewed."""
    table = "results" if up.get("sport") == "XC" else "results_tf"
    cur.execute(f"""SELECT count(*) AS n, count(person_id) AS linked FROM {table}
                    WHERE meet_id = %s AND source = %s""", (meetIdFor(up["id"]), SOURCE))
    c = AC._one(cur) or {}
    out = {"n": int(c.get("n") or 0), "linked": int(c.get("linked") or 0), "doubtful": []}
    if _regclass(cur, "upload_link_report"):
        cur.execute("""SELECT result_id, name, school, reason, touched FROM upload_link_report
                       WHERE upload_id = %s ORDER BY reason, name LIMIT 200""", (up["id"],))
        out["doubtful"] = [dict(r) for r in cur.fetchall()]
    return out


def applyUpload(cur, up, rows, events):
    """Write an approved upload into the raw tables the scrapers feed.
    Returns the counts. The caller owns the transaction (one commit with
    the status change); nothing here commits."""
    from psycopg2.extras import execute_values
    uid = up["id"]
    meet_id = meetIdFor(uid)
    sport = up["sport"]
    dist = eventDistances(events, up.get("distance_m"))
    evs = {e["n"]: e for e in events}
    now = datetime.datetime.now(datetime.timezone.utc)
    used = sorted({r["event_n"] for r in rows if (evs.get(r["event_n"]) or {}).get("kind", "running") == "running"})
    out = {"meet_id": meet_id, "sport": sport, "races": 0, "results": 0, "skipped_rows": 0}

    def gword(g):
        return {"M": "Boys", "F": "Girls"}.get(g, "")

    if sport == "XC":
        meet_rows = []
        for n in used:
            e = evs.get(n) or {}
            division = " ".join(x for x in (gword(e.get("gender")), e.get("division") or "") if x) or None
            meet_rows.append((divIdFor(uid, n), meet_id, up["meet_name"], up["meet_date"], up.get("location"),
                              dist.get(n), up.get("state"), division, SOURCE, SOURCE, uid))
        execute_values(cur, """
            INSERT INTO meets (div_id, meet_id, meet_name, meet_date, course_name, distance, state,
                               division, source, id_system, native_id)
            VALUES %s ON CONFLICT (div_id) DO NOTHING""", meet_rows)
        out["races"] = cur.rowcount
        res = []
        for r in rows:
            if r["event_n"] not in used:
                out["skipped_rows"] += 1
                continue
            res.append((mintResultId(uid, r), None, None, SOURCE, SOURCE, uid * 100000 + r["n"],
                        r["name"], meet_id, divIdFor(uid, r["event_n"]), r.get("time_seconds"),
                        r.get("grade") or "", up["meet_date"], r.get("school") or "Unknown",
                        SOURCE if r.get("school") else None, r.get("place"), now, r.get("status")))
        if res:
            execute_values(cur, """
                INSERT INTO results (result_id, athlete_id, person_id, source, id_system, native_id,
                                     athlete_name, meet_id, div_id, time_seconds, grade, date,
                                     school, school_source, place, scraped_at, status)
                VALUES %s ON CONFLICT (result_id) DO NOTHING""", res, page_size=2000)
            out["results"] = cur.rowcount
        return out

    # track: one meets_tf row per race (div_id, event_id), the meet's own
    # row in meets_tf_meta (its date and venue), results_tf
    cur.execute("""
        INSERT INTO meets_tf_meta (meet_id, meet_name, meet_date, venue_name, state, source,
                                   id_system, native_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (meet_id) DO NOTHING""",
                (meet_id, up["meet_name"], up["meet_date"], up.get("location"), up.get("state"),
                 SOURCE, SOURCE, uid))
    meet_rows = []
    for n in used:
        e = evs.get(n) or {}
        meet_rows.append((divIdFor(uid, n), meet_id, up["meet_name"], up.get("location"),
                          (e.get("title") or "")[:200], n, dist.get(n), up.get("state"), 0,
                          e.get("division"), SOURCE, SOURCE))
    execute_values(cur, """
        INSERT INTO meets_tf (div_id, meet_id, meet_name, venue_name, event_short, event_id,
                              distance_meters, state, is_indoor, division, source, id_system)
        VALUES %s ON CONFLICT DO NOTHING""", meet_rows)
    out["races"] = cur.rowcount
    res = []
    for r in rows:
        if r["event_n"] not in used:
            out["skipped_rows"] += 1
            continue
        title = r.get("event") or ""
        if r.get("round") == "P":
            title += " Prelims"
        res.append((mintResultId(uid, r), None, None, SOURCE, SOURCE, uid * 100000 + r["n"],
                    r["name"], meet_id, divIdFor(uid, r["event_n"]), r["event_n"], title[:200],
                    r.get("time_seconds"), "running", 0, r.get("grade") or "", up["meet_date"], 0,
                    r.get("school") or "Unknown", r.get("place"), now, r.get("status")))
    if res:
        execute_values(cur, """
            INSERT INTO results_tf (result_id, athlete_id, person_id, source, id_system, native_id,
                                    athlete_name, meet_id, div_id, event_id, event_short,
                                    time_seconds, result_kind, is_field, grade, date, is_relay,
                                    school, place, scraped_at, status)
            VALUES %s ON CONFLICT (result_id) DO NOTHING""", res, page_size=2000)
        out["results"] = cur.rowcount
    return out


def decide(cur, uid, action, admin_email, note=None):
    """The owner's decision on one upload -> (status, detail). action:
    'approve' (flags a likely duplicate instead of writing), 'force'
    (writes a flagged upload anyway), 'reject'. The caller commits."""
    from jsonb import J as Json   # SQL_ASCII-safe, NUL-free (jsonb.py)
    cur.execute("SELECT * FROM result_upload WHERE id = %s FOR UPDATE", (uid,))
    up = AC._one(cur)
    if up is None:
        return None, "No such upload."
    if action == "reject":
        if up["status"] not in ("submitted", "flagged"):
            return up["status"], "Only a waiting upload can be rejected."
        cur.execute("""UPDATE result_upload SET status = 'rejected', decided_at = now(), decided_by = %s,
                              decision_note = %s WHERE id = %s""", (admin_email, note, uid))
        return "rejected", None
    if action == "queue":
        import fixes as FX
        target = FX.queueTarget(up.get("source_url"))
        if up["status"] not in ("submitted", "flagged") or not target:
            return up["status"], "Only a waiting athletic.net or tfrrs meet link can be queued."
        mid, sport, src = target
        cur.execute("""INSERT INTO meet_queue (meet_id, sport, scraped, source) VALUES (%s, %s, 0, %s)
                       ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 0""", (mid, sport, src))
        applied = {"mechanism": "meet_queue", "meet_id": mid, "sport": sport, "source": src}
        cur.execute("""UPDATE result_upload SET status = 'approved', decided_at = now(), decided_by = %s,
                              decision_note = %s, applied = %s WHERE id = %s""",
                    (admin_email, note, Json(applied), uid))
        return "approved", None
    if action not in ("approve", "force"):
        return up["status"], "Unknown action."
    if up["status"] == "approved":
        return "approved", "Already approved."
    if action == "approve" and up["status"] != "submitted":
        return up["status"], "Only a submitted upload can be approved."
    if action == "force" and up["status"] != "flagged":
        return up["status"], "Insert anyway is for a flagged upload."
    events = up.get("events") or []
    gaps = readyToApply(up, events)
    if gaps:
        return up["status"], "Cannot write it: " + "; ".join(gaps) + "."
    if action == "approve":
        dupes = dupesFor(cur, up)
        if dupes:
            cur.execute("""UPDATE result_upload SET status = 'flagged', dupes = %s, decision_note = %s
                           WHERE id = %s""", (Json(_jsonable(dupes)), note, uid))
            return "flagged", None
    rows = loadRows(cur, uid)
    applied = applyUpload(cur, up, rows, events)
    applied["forced"] = action == "force"
    cur.execute("""UPDATE result_upload SET status = 'approved', decided_at = now(), decided_by = %s,
                          decision_note = %s, applied = %s, live_meet_id = %s WHERE id = %s""",
                (admin_email, note, Json(applied), applied["meet_id"], uid))
    return "approved", None


def _jsonable(x):
    return json.loads(json.dumps(x, default=str))


# ---- the mail to the uploader ------------------------------------------

def notifyUploader(up, status, note=None):
    """The decision, in the plain style of the alert mails (decision_email)."""
    import decision_email as DE
    if not up.get("account_email") or status not in ("approved", "rejected", "flagged"):
        return False
    subject, text, html = DE.uploadDecision(up, status, note, AC.siteOrigin())
    return AC.sendMail(up["account_email"], subject, text, html=html,
                       log_as=f"account {up.get('account_id')}" if up.get("account_id") else "guest upload")


def mailGuestLink(email, url, meet_name):
    """The guest's private link, so they can come back to it."""
    meet = meet_name or "your meet"
    text = (f"Hi,\n\nYou uploaded results to Racecast ({meet}). This private link is how you "
            f"check what we read, send it for review, or discard it:\n\n{url}\n\n"
            "Anyone with the link can see and change the upload until it is decided, so keep it "
            "to yourself. Nothing goes live until the site owner reviews it, and you will get an "
            "email either way.\n\nIf you did not upload anything, ignore this message.\n")
    return AC.sendMailWithin(email, f"Your upload to Racecast: {meet}", text)


# ---- routes: the uploader ------------------------------------------------

def _page(tpl, **kw):
    return render_template(tpl, status_words=STATUS_WORDS, **kw)


def _notReady():
    return _page("uploads.html", mode="unready", uploads=[], csrf="", notice="", error=""), 503


@bp.route("/account/uploads")
def uploads_page():
    sess, go = AC._requireSession()
    if go:
        return go
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        cur.execute("""SELECT id, status, meet_name, meet_date, sport, n_rows, file_name, created_at,
                              decision_note
                       FROM result_upload WHERE account_id = %s AND status <> 'discarded'
                       ORDER BY created_at DESC LIMIT 50""", (sess["account"]["id"],))
        mine = [dict(r) for r in cur.fetchall()]
        conn.commit()
    return _page("uploads.html", mode="list", uploads=mine, csrf=sess["csrf"],
                 admin=AC.isAdmin(sess["account"]),
                 notice=request.args.get("notice", "")[:200], error=request.args.get("error", "")[:200])


@bp.route("/account/uploads/new")
def upload_new():
    sess = AC.currentSession()
    sport = (request.args.get("sport") or "").upper()
    return _page("uploads.html", mode="new", uploads=[], csrf=sess["csrf"] if sess else "",
                 guest=not sess, turnstile=AC.turnstileSitekey() if not sess else "",
                 sport=sport if sport in SPORTS else "", school=(request.args.get("school") or "")[:120],
                 max_mb=MAX_UPLOAD_BYTES // (1024 * 1024), per_day=UPLOADS_PER_DAY,
                 notice="", error=request.args.get("error", "")[:200])


def _back(path, **q):
    q = {k: v for k, v in q.items() if v}
    return redirect(path + ("?" + urllib.parse.urlencode(q) if q else ""))


@bp.route("/account/uploads/new", methods=["POST"])
def upload_post():
    sess = AC.currentSession()
    if not sess:
        return _guestPost()
    if not AC.csrfOk(sess):
        return _back("/account/uploads/new", error="That request did not come from this site."), 400
    url = _urlInstead()
    if url is not None:
        if not url:
            return _back("/account/uploads/new", error="That link does not look like a web address."), 400
        with AC._db() as (conn, cur):
            if not tablesReady(cur):
                return _notReady()
            hour, day = recentCount(cur, sess["account"]["id"])
            if hour >= UPLOADS_PER_HOUR or day >= UPLOADS_PER_DAY:
                return _back("/account/uploads/new",
                             error="That is a lot of uploads at once: try again later today."), 429
            uid = stageUrl(cur, sess["account"]["id"], url, request.form)
            AC.logEvent(cur, "upload", sess["account"]["id"], detail={"upload": uid, "url": True})
            conn.commit()
        return _back(f"/account/uploads/{uid}", notice=URL_SENT)
    try:
        f, data, parsed = _readFile()
    except _Refused as r:
        return r.response, 400
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        hour, day = recentCount(cur, sess["account"]["id"])
        if hour >= UPLOADS_PER_HOUR or day >= UPLOADS_PER_DAY:
            return _back("/account/uploads/new",
                         error="That is a lot of uploads at once: try again later today."), 429
        uid = stage(cur, sess["account"]["id"], f.filename, data, parsed, request.form)
        AC.logEvent(cur, "upload", sess["account"]["id"],
                    detail={"upload": uid, "rows": len(parsed["rows"]), "format": parsed["format"]})
        conn.commit()
    return redirect(f"/account/uploads/{uid}")


URL_SENT = ("Got the link. The site owner reads it and adds the results; you will get an email "
            "when it is decided. A file is quicker to check, if you have one.")


def _urlInstead():
    """None when a file was chosen (or nothing at all); else the cleaned
    link, '' when it is not a web address."""
    f = request.files.get("file")
    if f and f.filename:
        return None
    raw = (request.form.get("url") or "").strip()
    if not raw:
        return None
    return cleanUrl(raw) or ""


class _Refused(Exception):
    def __init__(self, response):
        super().__init__("refused")
        self.response = response


def _readFile():
    """(file, bytes, parsed); _Refused carries the error response."""
    f = request.files.get("file")
    if not f or not f.filename:
        raise _Refused(_back("/account/uploads/new", error="Choose a results file, or paste a link to the results."))
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise _Refused(_back("/account/uploads/new", error="That file is over 9 MB."))
    if not data.strip():
        raise _Refused(_back("/account/uploads/new", error="That file is empty."))
    try:
        parsed = RP.parse(f.filename, data)
    except RP.ParseError as exc:
        raise _Refused(_back("/account/uploads/new", error=str(exc))) from None
    return f, data, parsed


def _guestPost():
    """An upload without an account: an email address instead."""
    if not AC.sameOrigin():
        return _back("/account/uploads/new", error="That request did not come from this site."), 400
    email = AC.normEmail(request.form.get("email"))
    if not email:
        return _back("/account/uploads/new",
                     error="Add your email address, so we can tell you what happened."), 400
    if not AC.turnstileOk(request.form.get("cf-turnstile-response")):
        return _back("/account/uploads/new", error="The check that you are a person did not pass: "
                                                   "try again."), 400
    url = _urlInstead()
    if url == "":
        return _back("/account/uploads/new", error="That link does not look like a web address."), 400
    f = data = parsed = None
    if url is None:
        try:
            f, data, parsed = _readFile()
        except _Refused as r:
            return r.response, 400
    ip = AC.clientIp()
    raw, token_hash = AC.newToken()
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        if not guestReady(cur):
            return _back("/login?" + urllib.parse.urlencode({"next": "/account/uploads/new"}))
        hour, day = recentGuestCount(cur, ip, email)
        if hour >= UPLOADS_PER_HOUR or day >= UPLOADS_PER_DAY:
            return _back("/account/uploads/new",
                         error="That is a lot of uploads at once: try again later today."), 429
        if url:
            uid = stageUrl(cur, None, url, request.form, guest=(email, token_hash, ip))
            detail = {"upload": uid, "url": True, "guest": True}
        else:
            uid = stage(cur, None, f.filename, data, parsed, request.form, guest=(email, token_hash, ip))
            detail = {"upload": uid, "rows": len(parsed["rows"]), "format": parsed["format"], "guest": True}
        AC.logEvent(cur, "upload", None, email=email, detail=detail)
        conn.commit()
    here = f"/account/uploads/g/{raw}"
    mailed = "failed"
    meet = (request.form.get("meet_name") or "").strip() or (parsed["meet"].get("name") if parsed else None)
    try:
        mailed = mailGuestLink(email, AC.siteOrigin() + here, meet)
    except Exception as exc:                            # noqa: BLE001
        print(f"[uploads] guest link mail for {uid} failed ({type(exc).__name__}: {exc})", flush=True)
    kept = ("We emailed you this page's link, so you can come back to it." if mailed != "failed"
            else "Keep this page's address: it is your link back to the upload.")
    return _back(here, notice=(URL_SENT + " " + kept) if url else kept)


def _mayView(sess, up):
    return up is not None and (up.get("account_id") == sess["account"]["id"] or AC.isAdmin(sess["account"]))


@bp.route("/account/uploads/<int:uid>")
def upload_view(uid):
    sess, go = AC._requireSession()
    if go:
        return go
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        up = loadUpload(cur, uid)
        if not _mayView(sess, up) or up["status"] == "discarded":
            abort(404)
        rows = loadRows(cur, uid, PREVIEW_ROWS)
        conn.commit()
    return _viewPage(up, rows, sess["csrf"], mine=up.get("account_id") == sess["account"]["id"],
                     base=f"/account/uploads/{uid}")


def _viewPage(up, rows, csrf, mine, base, guest=False):
    events = up.get("events") or []
    return _page("upload_view.html", up=up, rows=rows, events=events, csrf=csrf,
                 gaps=readyToApply(up, events), preview_rows=PREVIEW_ROWS, sports=SPORTS,
                 mine=mine, base=base, guest=guest,
                 notice=request.args.get("notice", "")[:200], error=request.args.get("error", "")[:200])


@bp.route("/account/uploads/<int:uid>/submit", methods=["POST"])
def upload_submit(uid):
    """The uploader confirms the meet's details and sends it for review."""
    sess, go = AC._requireSession()
    if go:
        return go
    here = f"/account/uploads/{uid}"
    if not AC.csrfOk(sess):
        return _back(here, error="That request did not come from this site."), 400
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        cur.execute("SELECT * FROM result_upload WHERE id = %s AND account_id = %s FOR UPDATE",
                    (uid, sess["account"]["id"]))
        up = AC._one(cur)
        if up is None:
            abort(404)
        return _submit(conn, cur, up, here, sess["account"]["id"])


def _submit(conn, cur, up, here, account_id, email=None):
    """Save the meet's details, and send the upload when asked. The
    caller has locked the row and checked it is this uploader's."""
    uid = up["id"]
    if up["status"] not in ("draft", "submitted"):
        return _back(here, error="This upload has been decided; it cannot change now."), 400
    meta, errors = checkMeta(request.form, {"name": up["meet_name"], "date": up["meet_date"],
                                            "date_end": up["meet_date_end"],
                                            "location": up["location"]}, up["sport"])
    if errors:
        return _back(here, error=" ".join(errors)), 400
    events = up.get("events") or []
    send = request.form.get("action") == "send"
    cur.execute("""UPDATE result_upload SET meet_name = %s, meet_date = %s, meet_date_end = %s,
                          location = %s, state = %s, sport = %s, distance_m = %s,
                          note = COALESCE(%s, note),
                          status = CASE WHEN %s THEN 'submitted' ELSE status END,
                          submitted_at = CASE WHEN %s THEN now() ELSE submitted_at END
                   WHERE id = %s""",
                (meta["meet_name"], meta["meet_date"], meta["meet_date_end"], meta["location"],
                 meta["state"], meta["sport"], meta["distance_m"],
                 (request.form.get("note") or "").strip()[:1000] or None, send, send, uid))
    up.update(meta)
    gaps = readyToApply(up, events)
    if send and gaps:
        conn.rollback()
        return _back(here, error="Before sending: " + "; ".join(gaps) + "."), 400
    if send:
        AC.logEvent(cur, "upload_sent", account_id, email=email, detail={"upload": uid})
    conn.commit()
    return _back(here, notice="Sent. The site owner reviews every upload before it goes live; "
                              "you will get an email when it is decided." if send else "Saved.")


@bp.route("/account/uploads/<int:uid>/discard", methods=["POST"])
def upload_discard(uid):
    sess, go = AC._requireSession()
    if go:
        return go
    if not AC.csrfOk(sess):
        return _back(f"/account/uploads/{uid}", error="That request did not come from this site."), 400
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        cur.execute("""UPDATE result_upload SET status = 'discarded', raw_text = NULL
                       WHERE id = %s AND account_id = %s AND status IN ('draft', 'submitted')""",
                    (uid, sess["account"]["id"]))
        n = cur.rowcount
        if n:
            cur.execute("DELETE FROM result_upload_row WHERE upload_id = %s", (uid,))
        conn.commit()
    return _back("/account/uploads", notice="Discarded." if n else "",
                 error="" if n else "That upload has been decided; it cannot be discarded.")


# ---- routes: the uploader without an account (their secret link) ------------

@bp.route("/account/uploads/g/<token>")
def guest_view(token):
    with AC._db() as (conn, cur):
        if not guestReady(cur):
            abort(404)
        up = loadGuestUpload(cur, token)
        if up is None or up["status"] == "discarded":
            abort(404)
        rows = loadRows(cur, up["id"], PREVIEW_ROWS)
        conn.commit()
    return _viewPage(up, rows, "", mine=True, base=f"/account/uploads/g/{token}", guest=True)


@bp.route("/account/uploads/g/<token>/submit", methods=["POST"])
def guest_submit(token):
    """The link is the credential; the request must still be this site's."""
    here = f"/account/uploads/g/{token}"
    if not AC.sameOrigin():
        return _back(here, error="That request did not come from this site."), 400
    with AC._db() as (conn, cur):
        if not guestReady(cur):
            abort(404)
        up = loadGuestUpload(cur, token, lock=True)
        if up is None:
            abort(404)
        return _submit(conn, cur, up, here, None, email=up.get("guest_email"))


@bp.route("/account/uploads/g/<token>/discard", methods=["POST"])
def guest_discard(token):
    if not AC.sameOrigin():
        return _back(f"/account/uploads/g/{token}", error="That request did not come from this site."), 400
    with AC._db() as (conn, cur):
        if not guestReady(cur):
            abort(404)
        cur.execute("""UPDATE result_upload SET status = 'discarded', raw_text = NULL
                       WHERE guest_token_hash = %s AND account_id IS NULL
                         AND status IN ('draft', 'submitted')
                       RETURNING id""", (AC.hashToken(token),))
        row = AC._one(cur)
        if row:
            cur.execute("DELETE FROM result_upload_row WHERE upload_id = %s", (row["id"],))
        conn.commit()
    if not row:
        return _back(f"/account/uploads/g/{token}", error="That upload has been decided; it cannot be discarded.")
    return _back("/account/uploads/new", notice="Discarded.")


@bp.route("/account/uploads/template.csv")
def upload_template():
    resp = make_response(RP.CSV_TEMPLATE)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="racecast-results-template.csv"'
    return resp


# ---- routes: the owner's queue -----------------------------------------------

@bp.route("/account/admin/uploads")
def admin_uploads():
    sess, go = requireAdmin()
    if go:
        return go
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        cur.execute("""SELECT u.id, u.status, u.meet_name, u.meet_date, u.sport, u.location, u.n_rows,
                              u.n_events, u.software, u.file_kind, u.submitted_at, u.decided_at,
                              jsonb_array_length(COALESCE(u.warnings, '[]'::jsonb)) AS n_warn,
                              COALESCE(a.email, u.guest_email) AS account_email,
                              u.account_id IS NULL AS guest
                       FROM result_upload u LEFT JOIN account a ON a.id = u.account_id
                       WHERE u.status IN ('submitted', 'flagged')
                       ORDER BY u.submitted_at NULLS LAST, u.id""")
        waiting = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT u.id, u.status, u.meet_name, u.meet_date, u.sport, u.n_rows, u.decided_at,
                              u.decided_by, COALESCE(a.email, u.guest_email) AS account_email,
                              u.account_id IS NULL AS guest
                       FROM result_upload u LEFT JOIN account a ON a.id = u.account_id
                       WHERE u.status IN ('approved', 'rejected')
                       ORDER BY u.decided_at DESC NULLS LAST LIMIT 30""")
        decided = [dict(r) for r in cur.fetchall()]
        conn.commit()
    return _page("admin_uploads.html", mode="queue", waiting=waiting, decided=decided, csrf=sess["csrf"],
                 gaps=PIPELINE_GAPS, steps=PIPELINE_STEPS, notice=request.args.get("notice", "")[:200],
                 error=request.args.get("error", "")[:200])


@bp.route("/account/admin/uploads/<int:uid>")
def admin_upload(uid):
    sess, go = requireAdmin()
    if go:
        return go
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        up = loadUpload(cur, uid)
        if up is None:
            abort(404)
        rows = loadRows(cur, uid, 2000)
        linking = None
        if up["status"] == "approved":
            try:
                linking = linkStatus(cur, up)
            except Exception:                           # noqa: BLE001
                conn.rollback()
        dupes = up.get("dupes") or []
        if up["status"] in ("submitted", "draft"):
            try:
                dupes = dupesFor(cur, up)
            except Exception as exc:                    # noqa: BLE001
                conn.rollback()
                dupes = [{"error": f"{type(exc).__name__}: {exc}"}]
        conn.commit()
    events = up.get("events") or []
    import fixes as FX
    return _page("admin_uploads.html", mode="one", up=up, rows=rows, events=events, dupes=dupes,
                 queue_target=FX.queueTarget(up.get("source_url")) if up.get("source_url") else None,
                 csrf=sess["csrf"], gaps=PIPELINE_GAPS, steps=PIPELINE_STEPS, linking=linking,
                 apply_gaps=readyToApply(up, events),
                 raw=(up.get("raw_text") or "")[:60000],
                 notice=request.args.get("notice", "")[:200], error=request.args.get("error", "")[:200])


@bp.route("/account/admin/uploads/<int:uid>/decide", methods=["POST"])
def admin_upload_decide(uid):
    sess, go = requireAdmin()
    if go:
        return go
    here = f"/account/admin/uploads/{uid}"
    if not AC.csrfOk(sess):
        abort(403)
    action = (request.form.get("action") or "").strip()
    if action == "force" and request.form.get("confirm") != "yes":
        return _back(here, error="Tick the box to say you checked it is a different meet."), 400
    note = (request.form.get("note") or "").strip()[:1000] or None
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return _notReady()
        try:
            status, err = decide(cur, uid, action, sess["account"]["email"], note)
        except Exception as exc:                        # noqa: BLE001
            conn.rollback()
            print(f"[uploads] decide {uid} failed ({type(exc).__name__}: {exc})", flush=True)
            return _back(here, error=f"Nothing was written: {type(exc).__name__}: {str(exc)[:200]}"), 500
        if err:
            conn.rollback()
            return _back(here, error=err), 400
        AC.logEvent(cur, f"upload_{status}", sess["account"]["id"], detail={"upload": uid})
        conn.commit()
        up = loadUpload(cur, uid)
        conn.commit()
    mailed = False
    if status in ("approved", "rejected"):
        try:
            mailed = notifyUploader(up, status, note)
        except Exception as exc:                        # noqa: BLE001
            print(f"[uploads] mail for {uid} failed ({type(exc).__name__}: {exc})", flush=True)
    words = {"approved": "Approved and written", "rejected": "Rejected",
             "flagged": "Held: it looks like a meet we already have (see the matches)"}[status]
    return _back(here, notice=words + ("; the uploader was emailed." if mailed else "."))


def _adminLinkUpload(uid):
    """(session, upload, None) for a waiting link upload, or (.., .., response)."""
    sess, go = requireAdmin()
    if go:
        return None, None, go
    here = f"/account/admin/uploads/{uid}"
    if not AC.csrfOk(sess):
        abort(403)
    with AC._db() as (conn, cur):
        if not tablesReady(cur):
            return sess, None, _notReady()
        up = loadUpload(cur, uid)
        conn.commit()
    if up is None:
        abort(404)
    if up["status"] not in ("submitted", "flagged"):
        return sess, up, (_back(here, error="Only a waiting upload can be read again."), 400)
    return sess, up, None


def _restageAndBack(sess, up, filename, data, how):
    here = f"/account/admin/uploads/{up['id']}"
    try:
        parsed = RP.parse(filename, data)
    except RP.ParseError as exc:
        return _back(here, error=f"{how}, but it could not be read: {exc}"), 400
    with AC._db() as (conn, cur):
        cur.execute("SELECT * FROM result_upload WHERE id = %s FOR UPDATE", (up["id"],))
        locked = AC._one(cur)
        if locked is None or locked["status"] not in ("submitted", "flagged"):
            conn.rollback()
            return _back(here, error="It was decided meanwhile."), 400
        restage(cur, locked, filename, data, parsed)
        AC.logEvent(cur, "upload_read", sess["account"]["id"],
                    detail={"upload": up["id"], "rows": len(parsed["rows"]), "how": how})
        conn.commit()
    return _back(here, notice=f"{how}: {len(parsed['rows'])} results in {len(parsed['events'])} events. "
                              "Check them, then approve.")


@bp.route("/account/admin/uploads/<int:uid>/details", methods=["POST"])
def admin_upload_details(uid):
    """The owner sets the meet's details: a link's file often has none."""
    sess, up, go = _adminLinkUpload(uid)
    if go:
        return go
    here = f"/account/admin/uploads/{uid}"
    meta, errors = checkMeta(request.form, {"name": up["meet_name"], "date": up["meet_date"],
                                            "date_end": up["meet_date_end"], "location": up["location"]},
                             up["sport"])
    if errors:
        return _back(here, error=" ".join(errors)), 400
    with AC._db() as (conn, cur):
        cur.execute("""UPDATE result_upload SET meet_name = %s, meet_date = %s, meet_date_end = %s,
                              location = %s, state = COALESCE(%s, state), sport = %s,
                              distance_m = COALESCE(%s, distance_m)
                       WHERE id = %s AND status IN ('submitted', 'flagged')""",
                    (meta["meet_name"], meta["meet_date"], meta["meet_date_end"], meta["location"],
                     meta["state"], meta["sport"], meta["distance_m"], uid))
        conn.commit()
    return _back(here, notice="Saved.")


@bp.route("/account/admin/uploads/<int:uid>/fetch", methods=["POST"])
def admin_upload_fetch(uid):
    """The owner's "Fetch and read" for a link upload."""
    import upload_fetch as UF
    sess, up, go = _adminLinkUpload(uid)
    if go:
        return go
    if not up.get("source_url"):
        return _back(f"/account/admin/uploads/{uid}", error="This upload has no link."), 400
    try:
        filename, data = UF.fetchResults(up["source_url"])
    except UF.FetchError as exc:
        return _back(f"/account/admin/uploads/{uid}",
                     error=f"Could not fetch it: {exc} Download it yourself and attach the file."), 400
    return _restageAndBack(sess, up, filename, data, "Fetched")


@bp.route("/account/admin/uploads/<int:uid>/file", methods=["POST"])
def admin_upload_file(uid):
    """The owner attaches the file for a link the fetch could not read."""
    sess, up, go = _adminLinkUpload(uid)
    if go:
        return go
    f = request.files.get("file")
    if not f or not f.filename:
        return _back(f"/account/admin/uploads/{uid}", error="Choose a results file."), 400
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return _back(f"/account/admin/uploads/{uid}", error="That file is over 9 MB."), 400
    name = f.filename
    if data[:5] == b"%PDF-":
        import upload_fetch as UF
        try:
            data, name = UF._pdfText(data), "attached.txt"
        except UF.FetchError as exc:
            return _back(f"/account/admin/uploads/{uid}", error=str(exc)), 400
    return _restageAndBack(sess, up, name, data, "Attached")


# ---- command line ------------------------------------------------------

def main(argv):
    sys.path.insert(0, "scripts")
    sys.path.insert(0, "racecast")
    from database import getConn
    if "--init" in argv:
        with getConn() as conn:
            initTables(conn)
        print("  result_upload, result_upload_row: ready (with the guest columns)")
        return 0
    if "--check" in argv:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.result_upload'), to_regclass('public.result_upload_row')")
                a, b = cur.fetchone()
                cur.execute("""SELECT count(*) FROM information_schema.columns
                               WHERE table_name = 'result_upload' AND column_name = 'guest_token_hash'""")
                g = cur.fetchone()[0]
        print(f"  result_upload: {'ok' if a else 'MISSING'}   result_upload_row: {'ok' if b else 'MISSING'}"
              f"   guest columns: {'ok' if g else 'MISSING (run --init)'}")
        return 0 if (a and b and g) else 1
    print("python racecast/uploads.py --init | --check")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
