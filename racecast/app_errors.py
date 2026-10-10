# Project: xc-predictor / racecast
# File:    app_errors.py
# Purpose: error monitoring (owner, 2026-10-10, approved) -- unhandled
#          exceptions, slow requests and browser JS errors, grouped by
#          fingerprint in app_error, listed at /account/status/errors, and
#          the owner emailed when a NEW group appears or a resolved one comes
#          back. monitor.py wires it into the app; this file is the rules.
#
#     python racecast/monitor.py --init     # creates app_error (and usage_daily)
#
# ★ GROUPS, NOT EVENTS. A fingerprint is the exception's type plus the
#   innermost frame in OUR code (file:function, not the line number, so an
#   unrelated edit above it does not make every known error "new" after a
#   deploy). One row per fingerprint: first_seen, last_seen, count, and the
#   LAST path template and traceback. A thousand hits are one row with
#   count = 1000.
#
# ! NEVER BREAKS A REQUEST. Recording is an in-memory dict and nothing else:
#   no database, no mail, inside the request. monitor's flusher thread writes
#   the dict every minute and sends any mail; every step there is wrapped,
#   and a database or provider that is down costs a log line, not a page.
#
# ! NOTHING ABOUT THE READER IS KEPT. The traceback is Python's own (code
#   and line, no local variables), trimmed, with anything shaped like an
#   email address or a token scrubbed; the path is the route's template
#   (/athlete/:person_id). No request body, no headers, no cookies, no IP,
#   no user agent -- they are never read here.
#
# ⚠ EMAIL IS BATCHED, NEVER PER HIT: see NOTIFY_WINDOW.

import datetime
import hashlib
import os
import re
import threading
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the repository's own code; a frame anywhere else (site-packages, the
# standard library) is not "ours"
_OUR_DIRS = tuple(os.path.join(ROOT, d) + os.sep
                  for d in ("racecast", "engine", "scripts", "model"))

# ★★ AT MOST ONE EMAIL PER 15 MINUTES (2026-10-10). Why 15:
#   - a bad deploy or a pipeline table swap surfaces its new fingerprints
#     together, within a minute or two of each other; a window longer than
#     the flusher's 60 s gathers that burst into ONE mail instead of a mail
#     per worker per minute;
#   - the FIRST new error after a quiet spell is mailed at the next flush
#     (within a minute) -- only the follow-ups wait -- so the owner still
#     hears inside the time a reader would take to report it;
#   - the worst case, a fresh fingerprint every window all day, is
#     24 * 60 / 15 = 96 mails, under the 100/day of the mail provider's free
#     tier (Resend), so error mail can never starve the login links that
#     share it.
NOTIFY_WINDOW = datetime.timedelta(minutes=15)
MAX_GROUPS_HELD = 200          # distinct fingerprints between two flushes
MAX_JS_NEW_PER_FLUSH = 25      # a forged-beacon flood cannot mint thousands
TB_LIMIT = 6000
MAIL_LIST = 20                 # groups named in one mail; the rest counted


def slowRequestMs(env=None):
    """★ THE SLOW-REQUEST LINE IS HALF THE STATEMENT TIMEOUT (2026-10-10).
    The site's Postgres statement_timeout (XCP_DB_STATEMENT_TIMEOUT_MS,
    55 000 in the service unit) is set just under gunicorn's 60 s --timeout
    so a request that would be killed dies cleanly first. A request past
    HALF of it is one more slow statement away from that kill -- the 503 /
    502 a reader sees -- so that is where it is worth a row. Not
    db_timing.SLOW_MS (250 ms): that is per STATEMENT, and a page runs
    dozens. XCP_SLOW_REQUEST_MS overrides; with no timeout configured (a
    dev box) the service unit's 55 000 is assumed."""
    env = os.environ if env is None else env
    own = str(env.get("XCP_SLOW_REQUEST_MS") or "").strip()
    if own.isdigit() and int(own) > 0:
        return int(own)
    st = str(env.get("XCP_DB_STATEMENT_TIMEOUT_MS") or "").strip()
    base = int(st) if st.isdigit() and int(st) > 0 else 55000
    return base // 2


# ---- fingerprints ---------------------------------------------------------

def appFrame(tb):
    """The innermost frame of the traceback that is in this repository:
    (relative file, function, line), or None when no frame is ours."""
    best = None
    for fs in traceback.extract_tb(tb):
        fn = os.path.abspath(fs.filename)
        if fn.startswith(_OUR_DIRS) and os.sep + "site-packages" + os.sep not in fn:
            best = (os.path.relpath(fn, ROOT).replace(os.sep, "/"), fs.name, fs.lineno)
    return best


def fingerprintKey(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def pyFingerprint(exc_type_name, frame):
    """'KeyError @ racecast/app.py:athlete_page' and its key."""
    where = f"{frame[0]}:{frame[1]}" if frame else "(no app frame)"
    title = f"{exc_type_name} @ {where}"
    return fingerprintKey("py|" + title), title


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# a token is long AND has digits in it (a long snake_case name in a source
# line is not one)
_TOKEN = re.compile(r"\b(?=(?:[A-Za-z_-]*\d){3})[A-Za-z0-9_-]{32,}\b")
_SECRETISH = re.compile(r"(?i)(password|passwd|secret|api_?key|csrf|cookie)(['\"]?\s*[=:]\s*)\S+")


def scrub(text):
    """No addresses, no tokens, no 'password=...' in anything stored."""
    t = _EMAIL.sub("<email>", text or "")
    t = _SECRETISH.sub(lambda m: m.group(1) + m.group(2) + "<redacted>", t)
    return _TOKEN.sub("<token>", t)


def trimTraceback(text, limit=TB_LIMIT):
    """Head (where it started) and tail (where it broke), within `limit`."""
    text = text or ""
    if len(text) <= limit:
        return text
    head = limit // 5
    return text[:head] + "\n  ... (trimmed) ...\n" + text[-(limit - head - 24):]


def jsFingerprint(message, script, line):
    """A browser error's group: the script, the line and the message with
    its numbers folded (an index or a count inside a message is not a new
    bug)."""
    msg = re.sub(r"\d+", "N", message)[:120]
    title = f"JS {msg} @ {script}:{line}"
    return fingerprintKey("js|" + title), title


_SCRIPT = re.compile(r"^/static/[A-Za-z0-9_.-]+\.js$")


def parseJsError(payload):
    """A k="x" beacon -> (message, script, line) or None. usage.js sends
    only same-origin scripts (/static/x.js, "(inline)", "(promise)"); the
    opaque cross-origin "Script error." and extensions' noise are dropped."""
    if not isinstance(payload, dict) or payload.get("k") != "x":
        return None
    x = payload.get("x")
    if not isinstance(x, dict):
        return None
    msg = " ".join(str(x.get("m") or "").split())[:200]
    script = str(x.get("f") or "")[:80]
    try:
        line = max(0, min(int(x.get("l") or 0), 10 ** 7))
    except (TypeError, ValueError):
        line = 0
    if not msg or msg.lower().startswith("script error"):
        return None
    if script not in ("(inline)", "(promise)") and not _SCRIPT.match(script):
        return None
    return scrub(msg), script, line


# ---- the buffer ------------------------------------------------------------

class Groups:
    """This worker's unwritten groups: {key: {...}}. Thread-safe."""

    def __init__(self, max_groups=MAX_GROUPS_HELD):
        self._lock = threading.Lock()
        self._g = {}
        self.max_groups = max_groups
        self.js_new = 0

    def add(self, key, kind, title, path, detail, now=None):
        now = now or datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
            g = self._g.get(key)
            if g is None:
                if len(self._g) >= self.max_groups:
                    return False
                if kind == "js":
                    if self.js_new >= MAX_JS_NEW_PER_FLUSH:
                        return False
                    self.js_new += 1
                g = self._g[key] = {"kind": kind, "title": title[:300],
                                    "first": now, "count": 0}
            g["count"] += 1
            g["last"] = now
            g["path"] = (path or "")[:120]
            g["detail"] = trimTraceback(scrub(detail or ""))
        return True

    def take(self):
        with self._lock:
            got, self._g = self._g, {}
            self.js_new = 0
        return got

    def putBack(self, got):
        with self._lock:
            for k, g in got.items():
                have = self._g.get(k)
                if have is None:
                    if len(self._g) < self.max_groups:
                        self._g[k] = g
                else:
                    have["count"] += g["count"]
                    have["first"] = min(have["first"], g["first"])

    def __len__(self):
        return len(self._g)


BUFFER = Groups()


def recordException(exc, path, buf=None):
    """An unhandled exception in a request. Never raises."""
    buf = BUFFER if buf is None else buf
    try:
        frame = appFrame(exc.__traceback__)
        key, title = pyFingerprint(type(exc).__name__, frame)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return buf.add(key, "py", title, path, tb)
    except Exception:                                    # noqa: BLE001
        return False


def recordSlow(path, ms, queries=(), threshold=None, buf=None):
    """A request over the slow line: one group per route template. The
    detail names the slowest statements db_timing saw in it (their SQL text
    as written -- parameters are bound by the driver and never in it)."""
    buf = BUFFER if buf is None else buf
    try:
        key = fingerprintKey("slow|" + (path or ""))
        lines = [f"took {ms / 1000:.1f} s (slow line {(threshold or slowRequestMs()) / 1000:.1f} s)"]
        for q in queries:
            lines.append(f"  {q['ms'] / 1000:6.2f} s  {q['sql']}")
        return buf.add(key, "slow", f"Slow request {path}", path, "\n".join(lines))
    except Exception:                                    # noqa: BLE001
        return False


def recordJs(message, script, line, path, buf=None):
    buf = BUFFER if buf is None else buf
    try:
        key, title = jsFingerprint(message, script, line)
        return buf.add(key, "js", title, path, f"{message}\n  at {script}:{line}")
    except Exception:                                    # noqa: BLE001
        return False


# ---- the table -------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS app_error (
    fingerprint    text PRIMARY KEY,           -- fingerprintKey(): 16 hex
    kind           text NOT NULL,              -- 'py' | 'slow' | 'js'
    title          text NOT NULL,              -- 'KeyError @ racecast/app.py:athlete_page'
    first_seen     timestamptz NOT NULL,
    last_seen      timestamptz NOT NULL,
    count          bigint NOT NULL DEFAULT 0,
    last_path      text,                       -- a route template, never a real path
    last_traceback text,                       -- trimmed, scrubbed; no request data
    resolved_at    timestamptz,
    reopened_at    timestamptz,
    notify_pending boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS app_error_open_idx ON app_error (last_seen DESC)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS app_error_pending_idx ON app_error (first_seen)
    WHERE notify_pending;
-- one row: when the last error mail went, shared by every worker
CREATE TABLE IF NOT EXISTS app_error_notify (
    id           int PRIMARY KEY CHECK (id = 1),
    last_sent_at timestamptz
);
INSERT INTO app_error_notify (id) VALUES (1) ON CONFLICT DO NOTHING;
"""

# ★ A RESOLVED GROUP THAT HAPPENS AGAIN IS REOPENED AND MAILED. Every SET
#   expression reads the OLD row, so notify_pending and reopened_at see the
#   resolved_at that the same statement clears.
UPSERT = """
    INSERT INTO app_error (fingerprint, kind, title, first_seen, last_seen,
                           count, last_path, last_traceback, notify_pending)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
    ON CONFLICT (fingerprint) DO UPDATE SET
        last_seen      = GREATEST(app_error.last_seen, excluded.last_seen),
        count          = app_error.count + excluded.count,
        title          = excluded.title,
        last_path      = excluded.last_path,
        last_traceback = excluded.last_traceback,
        notify_pending = app_error.notify_pending OR app_error.resolved_at IS NOT NULL,
        reopened_at    = CASE WHEN app_error.resolved_at IS NOT NULL
                              THEN excluded.last_seen ELSE app_error.reopened_at END,
        resolved_at    = NULL
    RETURNING notify_pending
"""


def flush(conn, buf=None):
    """Write the held groups. Returns (groups written, any now waiting for
    a mail). On failure the groups go back and the exception propagates."""
    buf = BUFFER if buf is None else buf
    got = buf.take()
    if not got:
        return 0, False
    pending = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = 10000")
            for key, g in got.items():
                cur.execute(UPSERT, (key, g["kind"], g["title"], g["first"], g["last"],
                                     g["count"], g["path"], g["detail"]))
                row = cur.fetchone()
                val = (row.get("notify_pending") if isinstance(row, dict)
                       else (row[0] if row else False))
                pending = pending or bool(val)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:                                # noqa: BLE001
            pass
        buf.putBack(got)
        raise
    return len(got), pending


# ---- the mail ----------------------------------------------------------------

def dueToSend(last_sent_at, now, window=NOTIFY_WINDOW):
    """The first mail goes at once; the next not before `window` after it."""
    return last_sent_at is None or now - last_sent_at >= window


def composeMail(rows, origin="https://racecast.co"):
    """(subject, text) for the groups waiting to be told about."""
    new = [r for r in rows if not r.get("reopened_at")]
    back = [r for r in rows if r.get("reopened_at")]
    bits = []
    if new:
        bits.append(f"{len(new)} new")
    if back:
        bits.append(f"{len(back)} back after resolved")
    subject = f"[racecast] errors: {', '.join(bits)}"
    lines = ["New error groups on the site since the last mail.", ""]
    for r in rows[:MAIL_LIST]:
        tag = "BACK" if r.get("reopened_at") else "NEW"
        lines.append(f"[{tag}] {r['title']}  ({r['count']}x, last on {r.get('last_path') or '-'})")
        tb = (r.get("last_traceback") or "").strip().splitlines()
        lines += ["    " + ln for ln in tb[-6:]]
        lines.append("")
    if len(rows) > MAIL_LIST:
        lines += [f"... and {len(rows) - MAIL_LIST} more.", ""]
    lines += [f"All of them, with tracebacks: {origin}/account/status/errors",
              f"At most one of these mails per {int(NOTIFY_WINDOW.total_seconds() // 60)} minutes."]
    return subject, "\n".join(lines) + "\n"


def notifyPending(conn, send, to, now=None, origin="https://racecast.co"):
    """Mail the owner about waiting groups if the window allows.

    send(addr, subject, text) -> bool is accounts.sendMail in production.
    Returns 'busy' (another worker holds the notifier), 'wait' (inside the
    window: try again later), 'none' (nothing waiting), or 'sent'. The
    notifier row is locked FOR UPDATE SKIP LOCKED, so eight workers never
    send the same batch twice; the rows are marked before the mail goes, and
    marked back if every send failed."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 10000")
        cur.execute("SELECT last_sent_at FROM app_error_notify WHERE id = 1 "
                    "FOR UPDATE SKIP LOCKED")
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return "busy"
        last = row.get("last_sent_at") if isinstance(row, dict) else row[0]
        if not dueToSend(last, now):
            conn.rollback()
            return "wait"
        cur.execute("""SELECT fingerprint, kind, title, count, last_path,
                              last_traceback, first_seen, reopened_at
                       FROM app_error WHERE notify_pending
                       ORDER BY first_seen LIMIT 500""")
        rows = [dict(r) if isinstance(r, dict) else dict(zip(
                    ("fingerprint", "kind", "title", "count", "last_path",
                     "last_traceback", "first_seen", "reopened_at"), r))
                for r in cur.fetchall()]
        if not rows:
            conn.rollback()
            return "none"
        keys = [r["fingerprint"] for r in rows]
        cur.execute("UPDATE app_error SET notify_pending = false "
                    "WHERE fingerprint = ANY(%s)", (keys,))
        cur.execute("UPDATE app_error_notify SET last_sent_at = %s WHERE id = 1", (now,))
    conn.commit()
    subject, text = composeMail(rows, origin)
    ok = False
    for addr in to:
        ok = send(addr, subject, text) or ok
    if not ok:
        # nobody got it: put them back so the next window tries again
        with conn.cursor() as cur:
            cur.execute("UPDATE app_error SET notify_pending = true "
                        "WHERE fingerprint = ANY(%s)", (keys,))
        conn.commit()
    return "sent" if ok else "failed"


# ---- the admin page -------------------------------------------------------------

def listGroups(cur, resolved=False, limit=100):
    cur.execute(f"""
        SELECT fingerprint, kind, title, first_seen, last_seen, count,
               last_path, last_traceback, resolved_at, reopened_at, notify_pending
        FROM   app_error
        WHERE  resolved_at IS {'NOT ' if resolved else ''}NULL
        ORDER  BY last_seen DESC
        LIMIT  %s""", (int(limit),))
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT count(*) FILTER (WHERE resolved_at IS NULL) AS open, "
                "count(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved FROM app_error")
    c = cur.fetchone() or {}
    c = dict(c) if isinstance(c, dict) else {"open": c[0], "resolved": c[1]}
    return {"rows": rows, "open": int(c.get("open") or 0),
            "resolved": int(c.get("resolved") or 0)}


def resolve(cur, fingerprint):
    cur.execute("UPDATE app_error SET resolved_at = now(), notify_pending = false "
                "WHERE fingerprint = %s AND resolved_at IS NULL", (fingerprint,))
    return cur.rowcount
