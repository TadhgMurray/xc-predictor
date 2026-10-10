# Project: xc-predictor / racecast
# File:    usage.py
# Purpose: self-hosted usage counts -- which pages and which features are
#          used, on phones or desktops, and where readers came from -- with
#          no cookies and no personal data (owner, 2026-10-10, approved).
#          The beacon is static/usage.js; the endpoint and the admin page are
#          monitor.py; this file is the rules and the aggregate buffer.
#
#     python racecast/monitor.py --init     # creates usage_daily (and app_error)
#
# ★ A BEACON, NOT THE ACCESS LOG (2026-10-10). The pages are edge-cached by
#   Cloudflare, so most views never reach gunicorn: counting on the server
#   counts cache misses, not readers. usage.js posts one small beacon after
#   the page has loaded, which no cache answers.
#
# ★ AGGREGATE COUNTS, NEVER A HIT. A beacon becomes +1 on a row of
#   usage_daily(day, path_template, event, screen) in this process's memory;
#   the buffer is written every FLUSH_SECONDS with one upsert per key
#   (n = n + excluded.n). There is no per-hit row, no visitor id, no session,
#   no IP and no user agent anywhere in the table -- nothing that could be
#   joined back to a person, because nothing about a person is kept.
#
# ! WHAT THE BROWSER SENDS IS ALREADY COARSE. The route's TEMPLATE
#   (/athlete/<int:person_id>, read off request.url_rule by _topbar.html and
#   the same for every reader of that URL, so the page stays cacheable), never
#   the path; the referrer's host only; "p" or "d" for the screen; and event
#   names from EVENTS. The server re-checks every field against its own
#   lists (KNOWN rules, EVENTS, the host pattern) and drops what does not fit.
#
# ! DO NOT TRACK AND GLOBAL PRIVACY CONTROL MEAN NO BEACON AT ALL. usage.js
#   sends nothing when either is on; the endpoint also drops a beacon that
#   arrives with a DNT: 1 or Sec-GPC: 1 header (beaconOptedOut).
#
# ⚠ BOTS ARE DROPPED BY USER AGENT (isBot), which is read and thrown away:
#   the UA is never stored or logged, only asked "is this a crawler".

import datetime
import re
import threading
import urllib.parse

# ★ THE NAMED UI EVENTS, AND ONLY THESE. A beacon is a POST anyone can send,
#   so an event name is checked against this list rather than stored as
#   given -- a free-text field would let anyone write rows into the table.
#   Adding an event: one rcUse('area:action') call in the JS, one name here.
EVENTS = {
    "predict:run",          # predictions.js -- the Predict button ran
    "rankings:filter",      # rankings.js -- a board control changed / Apply
    "follow:click",         # follow.js -- Follow / Following pressed
    "shortlist:click",      # follow.js -- the recruiting shortlist button
    "search:submit",        # topbar-search.js -- Enter in the search box
    "scale:advanced",       # scale-view.js -- switched to own-pool numbers
    "scale:hs",             # scale-view.js -- back to HS-equivalent
    "share:click",          # share.js -- a Share button
}
VIEW = "view"
REF_PREFIX = "ref:"
SCREENS = {"p": "phone", "d": "desktop"}
UNKNOWN_TPL = "(no route)"   # a 404, or a page rendered outside a route

FLUSH_SECONDS = 60
# a cap on distinct keys held between flushes: a day's real traffic is a few
# hundred keys; this only matters if the endpoint is being hammered
MAX_KEYS = 5000
MAX_BODY = 2048
MAX_EVENTS_PER_BEACON = 20

# ⚠ HEURISTIC, AND MEANT TO BE. The crawlers that run JavaScript (Googlebot,
#   bingbot, the link-preview fetchers) all say so; a headless browser that
#   hides it is caught client-side by navigator.webdriver where it is honest.
_BOT = re.compile(
    r"bot|crawl|spider|slurp|scrape|headless|lighthouse|pagespeed|preview|"
    r"facebookexternalhit|embedly|whatsapp|telegram|discord|curl|wget|"
    r"python|httpclient|okhttp|java/|go-http|node-fetch|axios|phantom|"
    r"selenium|puppeteer|playwright|monitor|uptime|pingdom|archive",
    re.I)
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,78}[a-z0-9])?$")
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "k12"}
_RULE_ARG = re.compile(r"<(?:[a-z_]+(?:\([^)]*\))?:)?([a-z_]+)>")   # int(signed=True) too


# ---- the pure rules ------------------------------------------------------

def isBot(user_agent):
    """True for a crawler, a script, or no user agent at all."""
    ua = (user_agent or "").strip()
    return not ua or bool(_BOT.search(ua))


def beaconOptedOut(headers):
    """The request itself says Do Not Track or Global Privacy Control."""
    return (str(headers.get("DNT") or "").strip() == "1"
            or str(headers.get("Sec-GPC") or "").strip() == "1")


def normalizeRule(rule):
    """A Flask rule -> the stored template: every variable part becomes
    :name, so /athlete/<int:person_id> is /athlete/:person_id and
    /race/xc/<int:meet_id>/<int:div_id> is /race/xc/:meet_id/:div_id. No id,
    name or school ever reaches the table."""
    rule = rule.strip() if isinstance(rule, str) else ""
    if not rule.startswith("/"):
        return ""
    return _RULE_ARG.sub(lambda m: ":" + m.group(1), rule)[:120]


def knownTemplates(url_map):
    """The page templates the site actually serves, from its own url_map:
    the only values path_template can take (plus UNKNOWN_TPL)."""
    out = set()
    for r in url_map.iter_rules():
        if "GET" not in (r.methods or ()):
            continue
        if r.rule.startswith(("/api/", "/static/", "/debug/", "/img/", "/card/")):
            continue
        out.add(normalizeRule(r.rule))
    out.discard("")
    return out


def pathTemplate(raw, known):
    """What the browser said its route was -> a stored template. Anything
    that is not one of the site's own routes is UNKNOWN_TPL."""
    t = normalizeRule(raw)
    return t if t and t in known else UNKNOWN_TPL


def refHost(raw, own_host=""):
    """The referrer as a site, never a URL: a bare host, lowercased, www.
    and subdomains folded to the registrable part (l.facebook.com and
    m.facebook.com are facebook.com), this site itself dropped. None when
    there is nothing worth counting."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "://" in s:
        s = urllib.parse.urlsplit(s).hostname or ""
    s = s.split("/")[0].split(":")[0].strip(".")
    if not s or not _HOST.match(s):
        return None
    labels = s.split(".")
    if len(labels) > 2:
        keep = 3 if (len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL) else 2
        labels = labels[-keep:]
    host = ".".join(labels)
    own = (own_host or "").lower().split(":")[0]
    if own and (host == own or own.endswith("." + host) or host.endswith("." + own)):
        return None
    return host


def screenOf(raw):
    return SCREENS.get(str(raw or "")[:1], "desktop")


def parseBeacon(payload, known, own_host=""):
    """A decoded beacon -> the increments it is worth: [(tpl, event, screen)].

    k="v": a page view (+ a ref:<host> row when it came from another site).
    k="e": named UI events from rcUse, each checked against EVENTS.
    Anything else -- including k="x", a JS error, which app_errors takes --
    counts nothing here."""
    if not isinstance(payload, dict):
        return []
    tpl = pathTemplate(payload.get("t"), known)
    screen = screenOf(payload.get("s"))
    kind = payload.get("k")
    if kind == "v":
        out = [(tpl, VIEW, screen)]
        host = refHost(payload.get("r"), own_host)
        if host:
            out.append((tpl, REF_PREFIX + host, screen))
        return out
    if kind == "e":
        names = payload.get("e")
        if not isinstance(names, list):
            return []
        return [(tpl, n, screen) for n in names[:MAX_EVENTS_PER_BEACON]
                if isinstance(n, str) and n in EVENTS]
    return []


# ---- the buffer ------------------------------------------------------------

class Counts:
    """This worker's unwritten counts: {(day, tpl, event, screen): n}.
    Thread-safe: the request thread adds, the flusher thread takes."""

    def __init__(self, max_keys=MAX_KEYS):
        self._lock = threading.Lock()
        self._n = {}
        self.max_keys = max_keys
        self.dropped = 0

    def add(self, rows, day=None):
        day = day or datetime.date.today()
        with self._lock:
            for tpl, event, screen in rows:
                key = (day, tpl, event, screen)
                if key not in self._n and len(self._n) >= self.max_keys:
                    self.dropped += 1
                    continue
                self._n[key] = self._n.get(key, 0) + 1

    def take(self):
        """Everything held, emptied: [(day, tpl, event, screen, n)]."""
        with self._lock:
            got, self._n = self._n, {}
        return [(*k, n) for k, n in got.items()]

    def putBack(self, rows):
        """A flush that failed: the counts go back for the next try."""
        with self._lock:
            for day, tpl, event, screen, n in rows:
                key = (day, tpl, event, screen)
                if key not in self._n and len(self._n) >= self.max_keys:
                    self.dropped += n
                    continue
                self._n[key] = self._n.get(key, 0) + n

    def __len__(self):
        return len(self._n)


BUFFER = Counts()

UPSERT = """
    INSERT INTO usage_daily (day, path_template, event, screen, n)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (day, path_template, event, screen)
    DO UPDATE SET n = usage_daily.n + excluded.n
"""


def flush(conn, buf=None):
    """Write the buffer: one upsert per key, one transaction. Returns the
    number of keys written. On failure the counts go back into the buffer
    and the exception propagates to the caller (monitor's flusher logs it)."""
    buf = BUFFER if buf is None else buf
    rows = buf.take()
    if not rows:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = 10000")
            cur.executemany(UPSERT, rows)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:                                # noqa: BLE001
            pass
        buf.putBack(rows)
        raise
    return len(rows)


DDL = """
CREATE TABLE IF NOT EXISTS usage_daily (
    day           date    NOT NULL,
    path_template text    NOT NULL,   -- /athlete/:person_id, never a real path
    event         text    NOT NULL,   -- 'view', 'ref:<host>', or one of EVENTS
    screen        text    NOT NULL,   -- 'phone' | 'desktop'
    n             bigint  NOT NULL DEFAULT 0,
    PRIMARY KEY (day, path_template, event, screen)
);
"""


# ---- the admin page --------------------------------------------------------

def report(cur, days=7, today=None):
    """Everything /account/status/usage shows, for the last `days` days."""
    today = today or datetime.date.today()
    since = today - datetime.timedelta(days=days - 1)

    def q(sql, *args):
        cur.execute(sql, (since, *args))
        return [tuple(r.values()) if isinstance(r, dict) else tuple(r)
                for r in cur.fetchall()]

    pages = q("""SELECT path_template, sum(n) FROM usage_daily
                 WHERE day >= %s AND event = 'view'
                 GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 25""")
    events = q("""SELECT event, sum(n) FROM usage_daily
                  WHERE day >= %s AND event <> 'view' AND event NOT LIKE 'ref:%%'
                  GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 40""")
    screens = dict(q("""SELECT screen, sum(n) FROM usage_daily
                        WHERE day >= %s AND event = 'view' GROUP BY 1"""))
    refs = q("""SELECT substr(event, 5), sum(n) FROM usage_daily
                WHERE day >= %s AND event LIKE 'ref:%%'
                GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 25""")
    by_day = dict(q("""SELECT day, sum(n) FROM usage_daily
                       WHERE day >= %s AND event = 'view' GROUP BY 1"""))
    daily = [(since + datetime.timedelta(days=i),
              int(by_day.get(since + datetime.timedelta(days=i), 0)))
             for i in range(days)]
    views = sum(n for _, n in daily)
    phone = int(screens.get("phone", 0))
    return {"days": days, "since": since, "views": views,
            "pages": [(p, int(n)) for p, n in pages],
            "events": [(e, int(n)) for e, n in events],
            "phone": phone, "desktop": int(screens.get("desktop", 0)),
            "phone_pct": round(100 * phone / views) if views else 0,
            "refs": [(h, int(n)) for h, n in refs],
            "daily": daily, "peak": max([n for _, n in daily] + [1])}
