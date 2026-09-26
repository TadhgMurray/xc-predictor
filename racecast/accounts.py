# Project: xc-predictor / racecast
# File:    accounts.py
# Purpose: Logins and claims (issue 283): who you are on Racecast, and
#          which athletes or teams your account speaks for. The first cut:
#          email magic links, Google sign-in when configured, server-side
#          sessions, the three roles, unverified claims. No passwords.
#
#     python racecast/accounts.py --init     # create the tables (once)
#     python racecast/accounts.py --check    # which features the env enables
#     python racecast/accounts.py --send-test you@x.com   # prove the mail provider
#     python racecast/accounts.py --google-check         # the redirect URI, the env
#
# ★ NO PASSWORDS. A login is an email and a one-time link, or Google. There
#   is nothing to leak or reset, every account has a working email, and a
#   Google login hands over a verified school-domain address for free.
#
# ★ SESSIONS LIVE IN POSTGRES, NOT IN A SIGNED COOKIE. Eight gunicorn
#   workers share one database already; a row per session means "log out
#   everywhere" and a revoked claim are one DELETE, and nothing here needs
#   a shared signing key. The cookie carries a random id; the table keeps
#   its hash, as it keeps the hash of every login token, so a read of the
#   table yields no logins.
#
# ★ ROLES ARE CLAIMS. An account is an email. What it speaks for is its
#   claims: an athlete claim names a person_id (several allowed: the
#   identity pass is not perfect and one kid often exists twice), a coach
#   claim names a team (school, state, level) or the athlete themself. The
#   role the account picked (athlete, coach, neither) is a preference for
#   the page, not a permission. Claims are 'claimed' (unverified) in this
#   cut; verification (a coach confirming, a school domain, the admin
#   queue) is the next one, and nothing an unverified claim can do touches
#   another person.
#
# ⚠ THE PAGES STAY ANONYMOUS. Every page on the site is edge-cacheable
#   HTML (app._headers), so no template renders the signed-in state: the
#   topbar asks /api/me and swaps its own link. The only routes that read
#   the session are these, and app.py lists them as private (no-store).
#
# ! AGE. Accounts are for people 13 or older (owner, 2026-09-15); the
#   login form asks once and the attestation is stored on the account.
#
# Env (all optional; a feature is off until its variables are set):
#   XCP_MAIL_PROVIDER=resend|postmark  XCP_MAIL_KEY=...  XCP_MAIL_FROM="Racecast <login@racecast.co>"
#   XCP_GOOGLE_CLIENT_ID=...  XCP_GOOGLE_CLIENT_SECRET=...
#   XCP_TURNSTILE_SITEKEY=...  XCP_TURNSTILE_SECRET=...
#   XCP_ADMIN_EMAILS=a@b.com,c@d.com
#   XCP_SITE_ORIGIN=https://racecast.co   (the links in the mails)

import hashlib
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

from flask import (Blueprint, request, render_template, redirect, jsonify, g,
                   make_response)

bp = Blueprint("accounts", __name__)

COOKIE = "xcp_session"
SESSION_DAYS = 90            # sliding: a visit within the window renews it
TOKEN_MINUTES = 15
STATE_MINUTES = 10           # the Google round trip
LINKS_PER_EMAIL_HOUR = 5
LINKS_PER_IP_HOUR = 30
TOUCH_SECONDS = 3600         # how often a session row records a visit
ROLES = ("athlete", "coach", "neither")
CLAIM_KINDS = ("athlete", "coach_team", "coach_self")
LEVELS = ("hs", "college", "ms", "club")
LEVEL_WORDS = {"hs": "High school", "college": "College", "ms": "Middle school", "club": "Club"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# ★ THE PHOTO (305, owner: "get athlete images"). One picture per account,
#   shown on every athlete page the account claims. Re-encoded through
#   Pillow to a square JPEG: the upload's metadata (EXIF, GPS, the camera)
#   never reaches the disk. Served by nginx from static/photos (gitignored)
#   under a name that carries the content hash, so a new picture is a new
#   URL and the old one can be cached for a year like the crests.
PHOTO_DIR = os.environ.get("XCP_PHOTO_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "photos")
PHOTO_URL = "/static/photos/"
# ★ SQUARE, WORN AS A CIRCLE (owner, 2026-09-15: "do it exactly like
#   anet"): athletic.net's athlete page shows a round avatar beside the
#   name, so the stored file is square and the page rounds it off. A 3:4
#   portrait was tried for one commit and cropped badly inside a circle.
PHOTO_SIZE = 512
PHOTO_W = PHOTO_H = PHOTO_SIZE
MAX_PHOTO_BYTES = 8 * 1024 * 1024
NAME_MAX = 60

DDL = """
CREATE TABLE IF NOT EXISTS account (
    id            bigserial PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    name          text,
    role          text NOT NULL DEFAULT 'neither',
    age_ok        boolean NOT NULL DEFAULT false,
    google_sub    text UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    deleted_at    timestamptz
);
CREATE TABLE IF NOT EXISTS login_token (
    token_hash text PRIMARY KEY,
    kind       text NOT NULL DEFAULT 'email',
    email      text,
    next_path  text,
    age_ok     boolean NOT NULL DEFAULT false,
    ip         text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz
);
CREATE INDEX IF NOT EXISTS login_token_email_idx ON login_token (email, created_at);
CREATE INDEX IF NOT EXISTS login_token_ip_idx ON login_token (ip, created_at);
CREATE TABLE IF NOT EXISTS account_session (
    sid_hash     text PRIMARY KEY,
    account_id   bigint NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    csrf         text NOT NULL,
    ip           text,
    user_agent   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS account_session_account_idx ON account_session (account_id);
CREATE TABLE IF NOT EXISTS account_claim (
    id          bigserial PRIMARY KEY,
    account_id  bigint NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    kind        text NOT NULL,
    person_id   bigint,
    school      text,
    state       text,
    level       text,
    status      text NOT NULL DEFAULT 'claimed',
    created_at  timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz,
    verified_by text,
    note        text
);
CREATE UNIQUE INDEX IF NOT EXISTS account_claim_unique ON account_claim
    (account_id, kind, coalesce(person_id, 0), coalesce(school, ''), coalesce(state, ''), coalesce(level, ''));
CREATE INDEX IF NOT EXISTS account_claim_person_idx ON account_claim (person_id);
CREATE TABLE IF NOT EXISTS account_photo (
    account_id bigint PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    file       text NOT NULL,
    width      integer NOT NULL,
    height     integer NOT NULL,
    bytes      integer NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_event (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    account_id bigint,
    email      text,
    kind       text NOT NULL,
    ip         text,
    detail     text
);
"""


class AccountsError(Exception):
    """A message for the person, never a traceback."""


# ---- env ---------------------------------------------------------------

def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def siteOrigin():
    return _env("XCP_SITE_ORIGIN", "https://racecast.co").rstrip("/")


def mailEnabled():
    return _env("XCP_MAIL_PROVIDER").lower() in ("resend", "postmark") and bool(_env("XCP_MAIL_KEY"))


def googleEnabled():
    return bool(_env("XCP_GOOGLE_CLIENT_ID")) and bool(_env("XCP_GOOGLE_CLIENT_SECRET"))


def turnstileSitekey():
    return _env("XCP_TURNSTILE_SITEKEY") if _env("XCP_TURNSTILE_SECRET") else ""


def adminEmails():
    return {e.strip().lower() for e in _env("XCP_ADMIN_EMAILS").split(",") if e.strip()}


def isAdmin(account):
    return bool(account) and (account.get("email") or "").lower() in adminEmails()


# ---- small pure helpers ------------------------------------------------

def normEmail(text):
    """A usable address, lowercased, or None."""
    e = (text or "").strip().lower()
    if not e or len(e) > 254 or not _EMAIL.match(e):
        return None
    return e


def safeNext(path, default="/account"):
    """Only a path on this site: no scheme, no host, no '//' (protocol-
    relative), and never back into the login flow."""
    p = (path or "").strip()
    if (not p.startswith("/") or p.startswith("//") or "\\" in p
            or p.startswith(("/login", "/auth/", "/logout"))):
        return default
    return p[:512]


def newToken():
    """(raw, hash). The raw goes to the person once; only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hashToken(raw)


def hashToken(raw):
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def clientIp():
    """The real client IP: the X-Real-IP header nginx sets from its own
    $remote_addr.

    ! ONLY THAT HEADER (sweep, 2026-09-26). CF-Connecting-IP and
      X-Forwarded-For were read first, and a visitor can send either one
      themselves -- straight to the origin, or X-Forwarded-For through
      anything -- so every per-IP limit (login links, reports) was one
      made-up header away from not applying. nginx overwrites X-Real-IP on
      every request (proxy_set_header), and with cloudflare_realip.sh
      installed its $remote_addr is the visitor, checked against
      Cloudflare's own ranges."""
    return ((request.headers.get("X-Real-IP") or "").strip()
            or request.remote_addr or "")[:64]


def isSecure():
    return request.headers.get("X-Forwarded-Proto", request.scheme) == "https"


def sameOrigin():
    """A state-changing request must come from this site: the Origin
    header when the browser sends one, else the Referer, else refused."""
    src = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if not src:
        return False
    host = urllib.parse.urlsplit(src).netloc.lower()
    return bool(host) and host == (request.host or "").lower()


def csrfOk(sess):
    """The form's token equals the session's, and the request is ours."""
    if not sess:
        return False
    sent = request.form.get("csrf") or request.headers.get("X-CSRF") or ""
    return sameOrigin() and secrets.compare_digest(sent, sess["csrf"])


def parseClaimForm(form):
    """The claim form -> (row, error). kind=athlete|coach_self needs a
    person_id; kind=coach_team a school, a state (or blank) and a level."""
    kind = (form.get("kind") or "").strip()
    if kind not in CLAIM_KINDS:
        return None, "Pick what to claim."
    if kind in ("athlete", "coach_self"):
        pid = (form.get("person_id") or "").strip()
        if not pid.isdigit() or int(pid) <= 0:
            return None, "Pick a name from the list."
        return {"kind": kind, "person_id": int(pid), "school": None, "state": None, "level": None}, None
    school = (form.get("school") or "").strip()[:200]
    state = (form.get("state") or "").strip().upper()
    level = (form.get("level") or "").strip().lower()
    if not school:
        return None, "Pick a school from the list."
    if state and (len(state) != 2 or not state.isalpha()):
        return None, "The state must be two letters."
    if level not in LEVELS:
        return None, "Pick the level."
    return {"kind": kind, "person_id": None, "school": school, "state": state or None, "level": level}, None


# ---- database ----------------------------------------------------------

@contextmanager
def _db():
    import psycopg2.extras
    from database import getConn
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield conn, cur


def _one(cur):
    row = cur.fetchone()
    return dict(row) if row is not None else None


def tablesReady(cur):
    cur.execute("SELECT to_regclass('public.account') AS t")
    row = _one(cur)
    return bool(row and row.get("t"))


def initTables(conn):
    """Create the tables, once, under a lock timeout (the rule: DDL is a
    transaction of its own, never a wrapper around work)."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(DDL)
    conn.commit()


def logEvent(cur, kind, account_id=None, email=None, detail=None):
    try:
        cur.execute("INSERT INTO auth_event (account_id, email, kind, ip, detail) VALUES (%s, %s, %s, %s, %s)",
                    (account_id, email, kind, clientIp(), json.dumps(detail) if detail else None))
    except Exception as exc:                            # noqa: BLE001
        print(f"[accounts] event {kind} not logged ({type(exc).__name__}: {exc})", flush=True)


# ---- mail --------------------------------------------------------------

def _http(url, data=None, headers=None, form=False, timeout=10):
    """One HTTPS call -> parsed JSON (or {} for no body)."""
    body = None
    # ! A NAMED USER AGENT. Resend's API sits behind Cloudflare, which
    #   answers urllib's default "Python-urllib/3.x" with a 403 (error 1010,
    #   "browser signature banned") before the request reaches Resend.
    hdrs = {"Accept": "application/json",
            "User-Agent": f"racecast/1.0 (+{siteOrigin()})", **(headers or {})}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # ! THE BODY IS THE REASON. A provider's 403 says which of "domain
        #   not verified", "key not allowed to send" or "sandbox: your own
        #   address only" it is; without it the log said only Forbidden.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                               # noqa: BLE001
            pass
        raise RuntimeError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}: {detail or exc.reason}")
    return json.loads(raw) if raw.strip() else {}


def sendMail(to, subject, text):
    """True when a provider accepted the message. Resend and Postmark are
    one JSON POST each; nothing else is wired."""
    provider, key = _env("XCP_MAIL_PROVIDER").lower(), _env("XCP_MAIL_KEY")
    sender = _env("XCP_MAIL_FROM", "Racecast <login@racecast.co>")
    try:
        if provider == "resend":
            _http("https://api.resend.com/emails",
                  {"from": sender, "to": [to], "subject": subject, "text": text},
                  {"Authorization": f"Bearer {key}"})
            return True
        if provider == "postmark":
            _http("https://api.postmarkapp.com/email",
                  {"From": sender, "To": to, "Subject": subject, "TextBody": text,
                   "MessageStream": _env("XCP_MAIL_STREAM", "outbound")},
                  {"X-Postmark-Server-Token": key})
            return True
    except Exception as exc:                            # noqa: BLE001
        print(f"[accounts] mail to {to} failed ({type(exc).__name__}: {exc})", flush=True)
        return False
    return False


def loginMail(url):
    return ("Sign in to Racecast",
            f"Here is your sign-in link:\n\n{url}\n\nIt works once and expires in "
            f"{TOKEN_MINUTES} minutes. If you did not ask for it, ignore this message.\n")


# ---- tokens and sessions -----------------------------------------------

def linksRecently(cur, email, ip):
    """(by this email, by this ip) in the last hour."""
    cur.execute("SELECT count(*) AS n FROM login_token WHERE email = %s AND created_at > now() - interval '1 hour'",
                (email,))
    by_email = _one(cur)["n"]
    cur.execute("SELECT count(*) AS n FROM login_token WHERE ip = %s AND created_at > now() - interval '1 hour'",
                (ip,))
    return int(by_email), int(_one(cur)["n"])


def requestLink(cur, email, ip, next_path, age_ok):
    """Create a login token and mail it. 'sent', 'rate' (too many this
    hour) or 'nomail' (no provider configured: the link is printed to the
    server log, where only the owner reads it)."""
    by_email, by_ip = linksRecently(cur, email, ip)
    if by_email >= LINKS_PER_EMAIL_HOUR or by_ip >= LINKS_PER_IP_HOUR:
        logEvent(cur, "link_rate_limited", email=email)
        return "rate"
    raw, h = newToken()
    cur.execute("""INSERT INTO login_token (token_hash, kind, email, next_path, age_ok, ip, expires_at)
                   VALUES (%s, 'email', %s, %s, %s, %s, now() + make_interval(mins => %s))""",
                (h, email, next_path, bool(age_ok), ip, TOKEN_MINUTES))
    url = f"{siteOrigin()}/login/t/{raw}"
    if not mailEnabled():
        print(f"[accounts] no mail provider configured; login link for {email}: {url}", flush=True)
        logEvent(cur, "link_unsent", email=email)
        return "nomail"
    subject, text = loginMail(url)
    if not sendMail(email, subject, text):
        logEvent(cur, "link_failed", email=email)
        return "failed"
    logEvent(cur, "link_sent", email=email)
    return "sent"


def consumeToken(cur, raw, kind="email"):
    """The token's row, marked used, or None (unknown, used, expired)."""
    cur.execute("""UPDATE login_token SET used_at = now()
                   WHERE token_hash = %s AND kind = %s AND used_at IS NULL AND expires_at > now()
                   RETURNING email, next_path, age_ok""", (hashToken(raw), kind))
    return _one(cur)


def findOrCreateAccount(cur, email, age_ok=False, google_sub=None, name=None):
    """The account for an email (or a Google subject), created on first
    login. The age attestation only ever turns on."""
    row = None
    if google_sub:
        cur.execute("SELECT * FROM account WHERE google_sub = %s AND deleted_at IS NULL", (google_sub,))
        row = _one(cur)
    if row is None:
        cur.execute("SELECT * FROM account WHERE email = %s AND deleted_at IS NULL", (email,))
        row = _one(cur)
    if row is None:
        cur.execute("""INSERT INTO account (email, name, age_ok, google_sub, last_login_at)
                       VALUES (%s, %s, %s, %s, now()) RETURNING *""",
                    (email, name, bool(age_ok), google_sub))
        row = _one(cur)
        logEvent(cur, "account_created", row["id"], email, {"google": bool(google_sub)})
        return row
    sets, vals = ["last_login_at = now()"], []
    if age_ok and not row["age_ok"]:
        sets.append("age_ok = true")
    if google_sub and not row.get("google_sub"):
        sets.append("google_sub = %s")
        vals.append(google_sub)
    if name and not row.get("name"):
        sets.append("name = %s")
        vals.append(name)
    cur.execute(f"UPDATE account SET {', '.join(sets)} WHERE id = %s RETURNING *", (*vals, row["id"]))
    return _one(cur)


def openSession(cur, account_id):
    """A new session row; returns the raw id for the cookie."""
    raw, h = newToken()
    cur.execute("""INSERT INTO account_session (sid_hash, account_id, csrf, ip, user_agent, expires_at)
                   VALUES (%s, %s, %s, %s, %s, now() + make_interval(days => %s))""",
                (h, account_id, secrets.token_urlsafe(24), clientIp(),
                 (request.headers.get("User-Agent") or "")[:300], SESSION_DAYS))
    logEvent(cur, "login", account_id)
    return raw


def setCookie(resp, raw):
    resp.set_cookie(COOKIE, raw, max_age=SESSION_DAYS * 86400, httponly=True,
                    secure=isSecure(), samesite="Lax", path="/")


def clearCookie(resp):
    resp.delete_cookie(COOKIE, path="/")


def loadSession(cur):
    """The session behind the cookie, with its account, or None. A visit
    renews the window at most once an hour."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    cur.execute("""SELECT s.sid_hash, s.csrf, s.last_seen_at, a.id, a.email, a.name, a.role, a.age_ok,
                          a.google_sub, a.created_at
                   FROM   account_session s JOIN account a ON a.id = s.account_id
                   WHERE  s.sid_hash = %s AND s.expires_at > now() AND a.deleted_at IS NULL""",
                (hashToken(raw),))
    row = _one(cur)
    if row is None:
        return None
    cur.execute("""UPDATE account_session SET last_seen_at = now(),
                          expires_at = now() + make_interval(days => %s)
                   WHERE sid_hash = %s AND last_seen_at < now() - make_interval(secs => %s)""",
                (SESSION_DAYS, row["sid_hash"], TOUCH_SECONDS))
    account = {k: row[k] for k in ("id", "email", "name", "role", "age_ok", "google_sub", "created_at")}
    return {"sid_hash": row["sid_hash"], "csrf": row["csrf"], "account": account}


def currentSession():
    """The request's session, loaded once. None when signed out, or when
    the tables are not there yet."""
    if hasattr(g, "_xcp_session"):
        return g._xcp_session
    sess = None
    if request.cookies.get(COOKIE):
        try:
            with _db() as (conn, cur):
                sess = loadSession(cur)
                conn.commit()
        except Exception as exc:                        # noqa: BLE001
            print(f"[accounts] session load failed ({type(exc).__name__}: {exc})", flush=True)
            sess = None
    g._xcp_session = sess
    return sess


def claimsFor(cur, account_id):
    """The account's claims, athletes named."""
    from rankings import nameLateral
    cur.execute(f"""SELECT c.id, c.kind, c.person_id, c.school, c.state, c.level, c.status, c.created_at, a.name
                    FROM   account_claim c
                    {nameLateral('c')}
                    WHERE  c.account_id = %s
                    ORDER  BY c.created_at""", (account_id,))
    out = [dict(r) for r in cur.fetchall()]
    from school_identity import schoolLabelIn
    for c in out:
        c["school_label"] = schoolLabelIn(c["school"], c["state"]) if c.get("school") else ""
        c["level_label"] = LEVEL_WORDS.get(c.get("level") or "", "")
    return out


def personExists(cur, person_id):
    cur.execute("SELECT 1 AS x FROM athlete_season WHERE person_id = %s LIMIT 1", (person_id,))
    if _one(cur):
        return True
    cur.execute("SELECT 1 AS x FROM ranking_results WHERE person_id = %s LIMIT 1", (person_id,))
    return bool(_one(cur))


# ---- Google (OpenID Connect, no library) ---------------------------------

def googleStart(cur, next_path, age_ok):
    """The URL to send the browser to; the state is a token row."""
    raw, h = newToken()
    cur.execute("""INSERT INTO login_token (token_hash, kind, next_path, age_ok, ip, expires_at)
                   VALUES (%s, 'google', %s, %s, %s, now() + make_interval(mins => %s))""",
                (h, next_path, bool(age_ok), clientIp(), STATE_MINUTES))
    q = {"client_id": _env("XCP_GOOGLE_CLIENT_ID"),
         "redirect_uri": f"{siteOrigin()}/auth/google/callback",
         "response_type": "code", "scope": "openid email profile",
         "state": raw, "prompt": "select_account"}
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)


def googleFinish(cur, code, state):
    """The callback: the state row consumed, the code exchanged, the
    person read from Google. (email, sub, name, next_path, age_ok)."""
    row = consumeToken(cur, state, kind="google")
    if row is None:
        raise AccountsError("That Google sign-in expired. Try again.")
    try:
        tok = _http("https://oauth2.googleapis.com/token",
                    {"code": code, "client_id": _env("XCP_GOOGLE_CLIENT_ID"),
                     "client_secret": _env("XCP_GOOGLE_CLIENT_SECRET"),
                     "redirect_uri": f"{siteOrigin()}/auth/google/callback",
                     "grant_type": "authorization_code"}, form=True)
        info = _http("https://openidconnect.googleapis.com/v1/userinfo",
                     headers={"Authorization": f"Bearer {tok.get('access_token', '')}"})
    except Exception as exc:                            # noqa: BLE001
        print(f"[accounts] google exchange failed ({type(exc).__name__}: {exc})", flush=True)
        raise AccountsError("Google did not complete the sign-in. Try again.")
    email = normEmail(info.get("email"))
    if not email or not info.get("email_verified") or not info.get("sub"):
        raise AccountsError("Google did not confirm an email address for that account.")
    return email, str(info["sub"]), (info.get("name") or "")[:120], row["next_path"], row["age_ok"]


def turnstileOk(token):
    """True when Turnstile is off, or when it accepts the token."""
    secret = _env("XCP_TURNSTILE_SECRET")
    if not secret:
        return True
    try:
        out = _http("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                    {"secret": secret, "response": token or "", "remoteip": clientIp()}, form=True)
        return bool(out.get("success"))
    except Exception as exc:                            # noqa: BLE001
        print(f"[accounts] turnstile check failed ({type(exc).__name__}: {exc})", flush=True)
        return False


# ---- the photo -----------------------------------------------------------

def processPhoto(data):
    """An upload's bytes -> (jpeg bytes, width, height): opened by Pillow,
    turned upright by its own orientation tag, centre-cropped square,
    shrunk to PHOTO_SIZE, written fresh (no metadata survives). Raises
    AccountsError with a message for the person."""
    if not data:
        raise AccountsError("Choose a picture first.")
    if len(data) > MAX_PHOTO_BYTES:
        raise AccountsError("That picture is over 8 MB. Try a smaller one.")
    import io as _io
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise AccountsError("Pictures are not enabled on this server (Pillow is missing).")
    try:
        img = Image.open(_io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception:                                   # noqa: BLE001
        raise AccountsError("That file is not a picture we can read (JPEG, PNG, WebP or HEIC-free HEIF).")
    if img.width < 64 or img.height < 64:
        raise AccountsError("That picture is too small; 64 pixels each way at least.")
    img = img.convert("RGB")
    # ★ THE BIGGEST SQUARE THAT FITS, centred sideways and biased toward
    #   the top (a face sits high in a photo, so a centred crop takes the
    #   chin off), then shrunk to PHOTO_SIZE at most.
    w, h = img.width, img.height
    side = min(w, h)
    left = (w - side) // 2
    top = max(0, min(h - side, (h - side) * 2 // 5))
    img = img.crop((left, top, left + side, top + side))
    if side > PHOTO_SIZE:
        img = img.resize((PHOTO_SIZE, PHOTO_SIZE), Image.LANCZOS)
    out = _io.BytesIO()
    img.save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return out.getvalue(), img.width, img.height


def photoUrl(file):
    return PHOTO_URL + file if file else None


def savePhoto(cur, account_id, data):
    """Process and store the picture; the row points at the new file, the
    old file goes. Returns the file name."""
    jpeg, w, h = processPhoto(data)
    name = f"{int(account_id)}-{hashlib.sha256(jpeg).hexdigest()[:12]}.jpg"
    os.makedirs(PHOTO_DIR, exist_ok=True)
    with open(os.path.join(PHOTO_DIR, name), "wb") as fh:
        fh.write(jpeg)
    cur.execute("SELECT file FROM account_photo WHERE account_id = %s", (account_id,))
    old = _one(cur)
    cur.execute("""INSERT INTO account_photo (account_id, file, width, height, bytes, updated_at)
                   VALUES (%s, %s, %s, %s, %s, now())
                   ON CONFLICT (account_id) DO UPDATE SET file = EXCLUDED.file, width = EXCLUDED.width,
                       height = EXCLUDED.height, bytes = EXCLUDED.bytes, updated_at = now()""",
                (account_id, name, w, h, len(jpeg)))
    if old and old["file"] != name:
        _unlink(old["file"])
    logEvent(cur, "photo", account_id, detail={"bytes": len(jpeg)})
    return name


def removePhoto(cur, account_id):
    cur.execute("DELETE FROM account_photo WHERE account_id = %s RETURNING file", (account_id,))
    row = _one(cur)
    if row:
        _unlink(row["file"])
        logEvent(cur, "photo_removed", account_id)
    return bool(row)


def _unlink(file):
    try:
        os.unlink(os.path.join(PHOTO_DIR, os.path.basename(file)))
    except OSError:
        pass


def accountPhoto(cur, account_id):
    """The account's picture as {url, width, height} or None."""
    cur.execute("SELECT file, width, height FROM account_photo WHERE account_id = %s", (account_id,))
    row = _one(cur)
    return {"url": photoUrl(row["file"]), "width": row["width"], "height": row["height"]} if row else None


_PHOTO_TABLES = {"checked": False, "ready": False}


def photoTablesReady(cur):
    """Once per process: are account_claim and account_photo there? A
    server that has not run --init otherwise pays an exception and a
    rollback on every athlete page."""
    if not _PHOTO_TABLES["checked"]:
        try:
            cur.execute("SELECT to_regclass('public.account_photo') AS t")
            row = _one(cur)
            _PHOTO_TABLES["ready"] = bool(row and row.get("t"))
        except Exception:                               # noqa: BLE001
            _PHOTO_TABLES["ready"] = False
        _PHOTO_TABLES["checked"] = True
        if not _PHOTO_TABLES["ready"]:
            print("[accounts] account_photo is not there: run racecast/accounts.py --init "
                  "and restart; athlete pages show the placeholder until then", flush=True)
    return _PHOTO_TABLES["ready"]


def photoFor(cur, person_id):
    """The picture on an athlete's page: the photo of the account that
    claims the person (a verified claim first, else the oldest). None when
    nobody has, or the tables are not there. Public data: the page is
    cached and this is what everyone sees."""
    if not photoTablesReady(cur):
        return None
    try:
        cur.execute("""SELECT p.file
                       FROM   account_claim c
                       JOIN   account_photo p ON p.account_id = c.account_id
                       JOIN   account a ON a.id = c.account_id AND a.deleted_at IS NULL
                       WHERE  c.person_id = %s AND c.kind IN ('athlete', 'coach_self')
                       ORDER  BY (c.status = 'verified') DESC, c.created_at
                       LIMIT  1""", (person_id,))
        row = _one(cur)
    except Exception as exc:                            # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:                               # noqa: BLE001
            pass
        print(f"[accounts] photoFor({person_id}) failed ({type(exc).__name__}: {exc})", flush=True)
        return None
    return photoUrl(row["file"]) if row else None


# ---- routes ------------------------------------------------------------

def _loginPage(mode="form", **kw):
    return render_template("login.html", mode=mode, next=safeNext(request.values.get("next")),
                           google=googleEnabled(), turnstile=turnstileSitekey(),
                           mail=mailEnabled(), **kw)


def _finishLogin(cur, account, next_path):
    raw = openSession(cur, account["id"])
    resp = make_response(redirect(safeNext(next_path)))
    setCookie(resp, raw)
    return resp


@bp.route("/login")
def login_page():
    if currentSession():
        return redirect(safeNext(request.args.get("next")))
    return _loginPage()


@bp.route("/login", methods=["POST"])
def login_post():
    if not sameOrigin():
        return _loginPage(error="That request did not come from this site."), 400
    next_path = safeNext(request.form.get("next"))
    age_ok = request.form.get("age_ok") == "on"
    if not age_ok:
        return _loginPage(error="Accounts are for people 13 or older; tick the box to continue.",
                          email=request.form.get("email", "")), 400
    if not turnstileOk(request.form.get("cf-turnstile-response")):
        return _loginPage(error="The spam check did not pass. Try again.",
                          email=request.form.get("email", "")), 400
    try:
        with _db() as (conn, cur):
            if not tablesReady(cur):
                return _loginPage(error="Accounts are not set up on this server yet."), 503
            if request.form.get("provider") == "google" and googleEnabled():
                url = googleStart(cur, next_path, age_ok)
                conn.commit()
                return redirect(url)
            email = normEmail(request.form.get("email"))
            if not email:
                return _loginPage(error="That does not look like an email address.",
                                  email=request.form.get("email", "")), 400
            status = requestLink(cur, email, clientIp(), next_path, age_ok)
            conn.commit()
    except AccountsError as exc:
        return _loginPage(error=str(exc)), 400
    if status == "rate":
        return _loginPage(error="Too many sign-in links for that address this hour. Try later."), 429
    return _loginPage("sent", email=email, unsent=(status if status in ("nomail", "failed") else ""))


@bp.route("/login/t/<token>")
def login_continue(token):
    """The landing page is a button, so a mail scanner that fetches the
    link cannot spend the token."""
    return _loginPage("continue", token=token)


@bp.route("/login/t/<token>", methods=["POST"])
def login_consume(token):
    if not sameOrigin():
        return _loginPage("expired"), 400
    with _db() as (conn, cur):
        row = consumeToken(cur, token)
        if row is None:
            conn.commit()
            return _loginPage("expired"), 410
        account = findOrCreateAccount(cur, row["email"], age_ok=row["age_ok"])
        resp = _finishLogin(cur, account, row["next_path"])
        conn.commit()
    return resp


@bp.route("/auth/google/callback")
def google_callback():
    if request.args.get("error") or not request.args.get("code") or not request.args.get("state"):
        return _loginPage(error="Google sign-in was cancelled."), 400
    try:
        with _db() as (conn, cur):
            email, sub, name, next_path, age_ok = googleFinish(cur, request.args["code"], request.args["state"])
            account = findOrCreateAccount(cur, email, age_ok=age_ok, google_sub=sub, name=name)
            resp = _finishLogin(cur, account, next_path)
            conn.commit()
    except AccountsError as exc:
        return _loginPage(error=str(exc)), 400
    return resp


@bp.route("/logout", methods=["POST"])
def logout():
    sess = currentSession()
    resp = make_response(redirect("/"))
    if sess and csrfOk(sess):
        with _db() as (conn, cur):
            cur.execute("DELETE FROM account_session WHERE sid_hash = %s", (sess["sid_hash"],))
            logEvent(cur, "logout", sess["account"]["id"])
            conn.commit()
    clearCookie(resp)
    return resp


def _requireSession():
    sess = currentSession()
    if not sess:
        return None, redirect("/login?" + urllib.parse.urlencode({"next": request.path}))
    return sess, None


@bp.route("/account")
def account_page():
    sess, go = _requireSession()
    if go:
        return go
    with _db() as (conn, cur):
        claims = claimsFor(cur, sess["account"]["id"])
        photo = accountPhoto(cur, sess["account"]["id"])
        conn.commit()
    return render_template("account.html", account=sess["account"], csrf=sess["csrf"], claims=claims,
                           photo=photo, name_max=NAME_MAX,
                           roles=ROLES, levels=[(k, LEVEL_WORDS[k]) for k in LEVELS],
                           admin=isAdmin(sess["account"]), google=googleEnabled(),
                           notice=request.args.get("notice", "")[:200],
                           error=request.args.get("error", "")[:200])


def _back(notice=None, error=None):
    q = {}
    if notice:
        q["notice"] = notice
    if error:
        q["error"] = error
    return redirect("/account" + ("?" + urllib.parse.urlencode(q) if q else ""))


@bp.route("/account/role", methods=["POST"])
def account_role():
    sess, go = _requireSession()
    if go:
        return go
    if not csrfOk(sess):
        return _back(error="That request did not come from this site."), 400
    role = (request.form.get("role") or "").strip()
    if role not in ROLES:
        return _back(error="Pick a role."), 400
    with _db() as (conn, cur):
        cur.execute("UPDATE account SET role = %s WHERE id = %s", (role, sess["account"]["id"]))
        logEvent(cur, "role", sess["account"]["id"], detail={"role": role})
        conn.commit()
    return _back(notice=f"You are set up as {'an' if role == 'athlete' else 'a' if role == 'coach' else ''} "
                        f"{role if role != 'neither' else 'visitor'}.".replace("  ", " "))


@bp.route("/account/name", methods=["POST"])
def account_name():
    sess, go = _requireSession()
    if go:
        return go
    if not csrfOk(sess):
        return _back(error="That request did not come from this site."), 400
    name = " ".join((request.form.get("name") or "").split())[:NAME_MAX]
    with _db() as (conn, cur):
        cur.execute("UPDATE account SET name = %s WHERE id = %s", (name or None, sess["account"]["id"]))
        logEvent(cur, "name", sess["account"]["id"])
        conn.commit()
    return _back(notice="Name saved." if name else "Name cleared.")


@bp.route("/account/photo", methods=["POST"])
def account_photo():
    """Upload (multipart 'photo') or remove ('remove'=1) the account's
    picture. The file is read up to the cap plus one byte, so an oversized
    upload is refused without being held in memory whole."""
    sess, go = _requireSession()
    if go:
        return go
    if not csrfOk(sess):
        return _back(error="That request did not come from this site."), 400
    with _db() as (conn, cur):
        if request.form.get("remove"):
            removePhoto(cur, sess["account"]["id"])
            conn.commit()
            return _back(notice="Picture removed.")
        f = request.files.get("photo")
        data = f.read(MAX_PHOTO_BYTES + 1) if f else b""
        try:
            savePhoto(cur, sess["account"]["id"], data)
        except AccountsError as exc:
            conn.rollback()
            return _back(error=str(exc)), 400
        conn.commit()
    return _back(notice="Picture saved. It shows on your athlete pages within a few minutes.")


@bp.route("/account/claim", methods=["POST"])
def account_claim():
    sess, go = _requireSession()
    if go:
        return go
    if not csrfOk(sess):
        return _back(error="That request did not come from this site."), 400
    row, err = parseClaimForm(request.form)
    if err:
        return _back(error=err), 400
    with _db() as (conn, cur):
        if row["person_id"] and not personExists(cur, row["person_id"]):
            return _back(error="That athlete is not on Racecast."), 400
        cur.execute("""INSERT INTO account_claim (account_id, kind, person_id, school, state, level)
                       VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (sess["account"]["id"], row["kind"], row["person_id"], row["school"], row["state"], row["level"]))
        logEvent(cur, "claim", sess["account"]["id"], detail=row)
        conn.commit()
    return _back(notice="Linked." if row["kind"] == "athlete" else "Added.")


@bp.route("/account/claim/remove", methods=["POST"])
def account_claim_remove():
    sess, go = _requireSession()
    if go:
        return go
    if not csrfOk(sess):
        return _back(error="That request did not come from this site."), 400
    cid = (request.form.get("id") or "").strip()
    if not cid.isdigit():
        return _back(error="Nothing to remove."), 400
    with _db() as (conn, cur):
        cur.execute("DELETE FROM account_claim WHERE id = %s AND account_id = %s", (int(cid), sess["account"]["id"]))
        logEvent(cur, "claim_removed", sess["account"]["id"], detail={"id": int(cid)})
        conn.commit()
    return _back(notice="Removed.")


@bp.route("/account/logout-all", methods=["POST"])
def account_logout_all():
    sess, go = _requireSession()
    if go:
        return go
    if not csrfOk(sess):
        return _back(error="That request did not come from this site."), 400
    with _db() as (conn, cur):
        cur.execute("DELETE FROM account_session WHERE account_id = %s", (sess["account"]["id"],))
        logEvent(cur, "logout_all", sess["account"]["id"])
        conn.commit()
    resp = make_response(redirect("/login"))
    clearCookie(resp)
    return resp


@bp.route("/account/delete", methods=["POST"])
def account_delete():
    """The account, its sessions and its claims go; the public results
    stay, as they are the public record."""
    sess, go = _requireSession()
    if go:
        return go
    if not csrfOk(sess):
        return _back(error="That request did not come from this site."), 400
    if (request.form.get("confirm") or "").strip().upper() != "DELETE":
        return _back(error="Type DELETE to confirm."), 400
    with _db() as (conn, cur):
        logEvent(cur, "account_deleted", sess["account"]["id"], sess["account"]["email"])
        cur.execute("DELETE FROM account WHERE id = %s", (sess["account"]["id"],))
        conn.commit()
    resp = make_response(redirect("/"))
    clearCookie(resp)
    return resp


@bp.route("/api/me")
def api_me():
    """What the topbar and the pages ask: signed in or not, and the claims.
    Under /api/, so app._headers marks it no-store."""
    sess = currentSession()
    if not sess:
        return jsonify({"signed_in": False})
    a = sess["account"]
    photo = None
    try:
        with _db() as (conn, cur):
            claims = claimsFor(cur, a["id"])
            photo = accountPhoto(cur, a["id"])
            conn.commit()
    except Exception as exc:                            # noqa: BLE001
        print(f"[accounts] claims failed ({type(exc).__name__}: {exc})", flush=True)
        claims = []
    return jsonify({
        "signed_in": True, "email": a["email"], "name": a.get("name") or "",
        "photo": photo["url"] if photo else None,
        "label": a.get("name") or a["email"].split("@", 1)[0],
        "role": a.get("role") or "neither", "admin": isAdmin(a),
        "athletes": [{"person_id": c["person_id"], "name": c.get("name") or "", "status": c["status"]}
                     for c in claims if c["kind"] in ("athlete", "coach_self")],
        # level_label, not just level: /coaches renders these client-side and
        # should not have to keep its own copy of LEVEL_WORDS.
        "teams": [{"school": c["school"], "state": c["state"], "level": c["level"],
                   "level_label": c.get("level_label") or "",
                   "label": c["school_label"], "status": c["status"]}
                  for c in claims if c["kind"] == "coach_team"],
    })


# ---- command line ------------------------------------------------------

def main(argv):
    sys.path.insert(0, "scripts")
    sys.path.insert(0, "racecast")
    if "--check" in argv:
        print(f"  mail:      {'on (' + _env('XCP_MAIL_PROVIDER') + ')' if mailEnabled() else 'OFF (links go to the server log)'}")
        print(f"  google:    {'on' if googleEnabled() else 'off'}")
        print(f"  turnstile: {'on' if turnstileSitekey() else 'off'}")
        print(f"  admins:    {sorted(adminEmails()) or 'none'}")
        print(f"  origin:    {siteOrigin()}")
        return 0
    if "--send-test" in argv:
        # proves the mail provider end to end: the same call a login makes
        to = argv[argv.index("--send-test") + 1] if len(argv) > argv.index("--send-test") + 1 else ""
        if not normEmail(to):
            print("  usage: --send-test you@example.com")
            return 2
        if not mailEnabled():
            print("  no mail provider in the env (XCP_MAIL_PROVIDER, XCP_MAIL_KEY)")
            return 1
        subject, text = loginMail(f"{siteOrigin()}/login/t/TEST-not-a-real-link")
        ok = sendMail(to, "[test] " + subject, text)
        print(f"  {'sent' if ok else 'FAILED'} to {to} via {_env('XCP_MAIL_PROVIDER')}")
        return 0 if ok else 1
    if "--google-check" in argv:
        # what to paste into the Google console, and whether the env has both halves
        print(f"  authorised redirect URI: {siteOrigin()}/auth/google/callback")
        print(f"  client id:     {'set' if _env('XCP_GOOGLE_CLIENT_ID') else 'MISSING'}")
        print(f"  client secret: {'set' if _env('XCP_GOOGLE_CLIENT_SECRET') else 'MISSING'}")
        print(f"  button shows:  {'yes' if googleEnabled() else 'no'}")
        return 0 if googleEnabled() else 1
    if "--init" in argv:
        from database import getConn
        with getConn() as conn:
            initTables(conn)
        print("  account, login_token, account_session, account_claim, auth_event: ready")
        return 0
    print(__doc__ or "python racecast/accounts.py --init | --check")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
