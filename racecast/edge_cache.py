# Project: xc-predictor / racecast
# File:    edge_cache.py
# Purpose: what Cloudflare may keep, and ONE spelling per URL (2026-11-08).
#
# ★ WHY (Cloudflare's hit rate ~15% -> ~2%). The edge keys its cache on the
#   whole URL, byte for byte: `?b=1&a=2` and `?a=2&b=1`, a trailing `&`, an
#   `offset=0`, a `utm_source=` or a Facebook `fbclid=` are each another cold
#   copy of the same page, rendered again by gunicorn. Measured on the live
#   site 2026-11-08: every one of those spellings was a MISS beside a HIT for
#   the plain URL. This module holds the one rule for "the same URL":
#
#     canonicalPairs / canonicalQuery   the spelling the board JS
#                                       (static/board-url.js), the edge Worker
#                                       (deploy/cloudflare/worker.js) and the
#                                       snapshot publisher (scripts/
#                                       edge_snapshot.py) all emit. One spec,
#                                       three copies, tests/test_edge_cache.py
#                                       holds them equal.
#     install(app)                      the request/response rules:
#        - a page reached with tracking parameters is a 301 to the page
#          without them (a public, cacheable redirect: no render);
#        - the read-only lookup APIs listed in PUBLIC_API_ENDPOINTS are public
#          like the boards (they were no-store only because /api/ is);
#        - NOTHING THAT SETS A COOKIE, VARIES ON ONE, OR LOOKED AT THE READER'S
#          SESSION IS EVER PUBLIC (a WSGI guard over the final headers);
#        - a redirect's Location names a feed by number (?src=1/2), as
#          feed_scrub does for bodies (CLAUDE.md).
#
# ! WHAT IS NOT DROPPED: a parameter whose default only the SERVER knows
#   (scope=usa, scale=pool, sort=rating, limit=50 ...). rankings.js sends
#   scope "including the default" on purpose -- a URL that leans on a default
#   changes meaning when the default moves -- and the edge cannot know the
#   default. offset=0 is the one exception: the first page is page one under
#   any default. Empty values are kept too: sport= is a 400, absent is "both".
#
# ! PURE AT IMPORT. No flask import at module level: scripts/edge_snapshot.py
#   imports the canonical spelling without the site.

TRACKING_PREFIXES = ("utm_",)
# Click and campaign ids the big referrers append. Every one of them is
# unique per click or per campaign and none is read by any route here
# (grep: no route reads a parameter of these names).
TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "msclkid",
    "yclid", "twclid", "ttclid", "li_fat_id", "igshid", "igsh", "mc_cid",
    "mc_eid", "_ga", "_gl", "srsltid", "mkt_tok", "_hsenc", "_hsmi",
    "rdt_cid", "epik", "s_kwcid",
})

# Parameters that equal the default under ANY server default (see above).
_FIXED_DEFAULTS = {("offset", "0")}


def isTracking(name):
    n = (name or "").lower()
    return n in TRACKING_PARAMS or n.startswith(TRACKING_PREFIXES)


def canonicalPairs(pairs):
    """[(name, value)] -> the canonical list: tracking parameters and
    offset=0 dropped, the rest sorted by NAME ONLY and stably, so repeated
    names keep their relative order (a list filter's order is the reader's;
    URLSearchParams.sort() is stable in exactly this way)."""
    kept = [(str(k), str(v)) for k, v in pairs
            if not isTracking(k) and (str(k), str(v)) not in _FIXED_DEFAULTS]
    return sorted(kept, key=lambda kv: kv[0])


_SAFE = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789*-._")


def formEncode(text):
    """application/x-www-form-urlencoded, byte for byte as the browser's
    URLSearchParams.toString() writes it (Python's quote_plus differs on ~
    and *, which would make two spellings again)."""
    out = []
    for b in str(text).encode("utf-8"):
        if b in _SAFE:
            out.append(chr(b))
        elif b == 0x20:
            out.append("+")
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def encodePairs(pairs):
    return "&".join(formEncode(k) + "=" + formEncode(v) for k, v in pairs)


def canonicalQuery(pairs):
    """The canonical query string (no leading '?'; '' when nothing is left)."""
    return encodePairs(canonicalPairs(pairs))


def parseQuery(qs):
    """'a=1&b=x+y' -> [('a', '1'), ('b', 'x y')], blanks kept, as
    URLSearchParams parses (a bare 'a' is ('a', ''); empty pieces dropped)."""
    from urllib.parse import unquote_plus
    out = []
    for piece in (qs or "").lstrip("?").split("&"):
        if not piece:
            continue
        k, _sep, v = piece.partition("=")
        out.append((unquote_plus(k), unquote_plus(v)))
    return out


def canonicalUrl(path, qs):
    """path + '?' + canonical query, or the bare path."""
    q = canonicalQuery(parseQuery(qs))
    return path + ("?" + q if q else "")


# ★ READ-ONLY LOOKUPS THAT ARE A FUNCTION OF THE URL AND THE CORPUS ALONE.
#   Each was no-store only because it lives under /api/ (app._PRIVATE_
#   PREFIXES): no session, no cookie, no reader in the answer -- the same
#   test the board APIs passed (app._publicBoard). A 200 gets the pages'
#   public header; an error keeps the route's own header or, unset, the
#   no-store app._headers gives every non-200.
#   /api/rankings/units is the one that matters: the Conferences tab, 155 KB
#   and ~0.7 s at the origin, DYNAMIC at the edge on every view (2026-11-08).
# ! NOT HERE: anything that reads a session (/api/me, follow, shortlist),
#   writes (POST), or can answer "pending" (the prediction jobs) or an error
#   with a 200 (recruiting's "not available right now").
PUBLIC_API_ENDPOINTS = frozenset({
    "pages3.api_units",          # /api/rankings/units (Conferences tab)
    "api_explain",               # /api/explain/<sport>/<id>
    "api_schools",               # /api/schools  (School picker)
    "api_units",                 # /api/units    (unit pickers)
    "api_meet_units",            # /api/meet-units
    "api_xc_courses",            # /api/xc_courses
    "course_search",             # /api/course_search
    "api_equivalence",           # /api/equivalence
    "api_rating_5k",             # /api/rating_5k
    "athlete_results",           # /api/athlete_results
    "api_athlete_pool",          # /api/athlete_pool
    "meet_xc_find",              # /api/meet/xc/<id>/find
    "meet_tf_find",              # /api/meet/tf/<id>/find
    "api_predict_upcoming",      # /api/predict/upcoming
    "api_predict_share_get",     # /api/predict/share/<id> (a share is fixed)
})

# Paths a tracking redirect never touches: machine endpoints, files, and
# the sign-in flow (an OAuth callback's query is the provider's).
_NO_REDIRECT_PREFIXES = ("/api/", "/static/", "/img/", "/card/", "/login",
                         "/logout", "/auth/", "/account", "/debug/")

PRIVATE = "private, no-store"


def trackingRedirectTarget(path, raw_qs):
    """The URL a page request with tracking parameters is sent to, or None.
    Works on the RAW query string so every other parameter goes back out
    exactly as it came in (order, spelling, ?src=1 -- not the internal key
    app._PublicSrc swaps in)."""
    if path.startswith(_NO_REDIRECT_PREFIXES):
        return None
    pieces = [p for p in (raw_qs or "").split("&") if p]
    from urllib.parse import unquote_plus
    kept = [p for p in pieces if not isTracking(unquote_plus(p.partition("=")[0]))]
    if len(kept) == len(pieces):
        return None
    return path + ("?" + "&".join(kept) if kept else "")


def guardHeaders(headers):
    """The final response headers ([(name, value)]) with the one rule no
    route may break: a response that sets a cookie or varies on one is
    never stored by a shared cache. Returns the (possibly new) list."""
    names = {k.lower() for k, _v in headers}
    vary_cookie = any(k.lower() == "vary" and "cookie" in v.lower()
                      for k, v in headers)
    if "set-cookie" not in names and not vary_cookie:
        return headers
    cc = [v for k, v in headers if k.lower() == "cache-control"]
    if cc and all(("no-store" in v.lower() or "private" in v.lower()) for v in cc):
        return headers
    return [(k, v) for k, v in headers if k.lower() != "cache-control"] + \
           [("Cache-Control", PRIVATE)]


def scrubLocation(headers):
    """A redirect names a meet's feed as ?src=1 / ?src=2, never by its key."""
    import feed_scrub
    return [(k, feed_scrub.scrub(v) if k.lower() == "location" else v)
            for k, v in headers]


class _Guard:
    """WSGI middleware OUTSIDE every after_request hook, so it sees the
    headers that really leave: whatever a hook registered later adds, a
    cookie still makes the response private."""

    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        def start(status, headers, exc_info=None):
            headers = scrubLocation(guardHeaders(list(headers)))
            return start_response(status, headers, exc_info)
        return self.wsgi(environ, start)


def install(app, public_cache_control):
    """Wire the rules into the site (app.py, once, after every hook)."""
    from flask import g, redirect, request

    @app.before_request
    def _edgeTrackingRedirect():
        if request.method not in ("GET", "HEAD"):
            return None
        raw = request.environ.get("RC_RAW_QUERY_STRING",
                                  request.query_string.decode("utf-8", "replace"))
        target = trackingRedirectTarget(request.path, raw)
        if target is None:
            return None
        resp = redirect(target, code=301)
        # the same answer for everyone who follows that link: the edge may
        # keep it, and the page it points at is the one shared copy
        resp.headers["Cache-Control"] = public_cache_control
        return resp

    # ! REGISTERED LAST, SO IT RUNS FIRST (Flask runs after_request hooks in
    #   reverse): it sets Cache-Control before app._headers' setdefault, which
    #   then keeps it.
    @app.after_request
    def _edgePublicApis(resp):
        if hasattr(g, "_xcp_session"):
            # ⚠ THE READER'S SESSION WAS LOOKED AT (accounts.currentSession):
            #   whatever this page is, it may differ per reader. Never public.
            resp.headers["Cache-Control"] = PRIVATE
            return resp
        if (request.method == "GET" and resp.status_code == 200
                and request.endpoint in PUBLIC_API_ENDPOINTS
                and "Cache-Control" not in resp.headers):
            resp.headers["Cache-Control"] = public_cache_control
        return resp

    # keep the query string as the reader sent it, before _PublicSrc turns
    # ?src=1 into the internal key, for the redirect above
    inner = app.wsgi_app

    def _rawQuery(environ, start_response):
        environ.setdefault("RC_RAW_QUERY_STRING", environ.get("QUERY_STRING") or "")
        return inner(environ, start_response)

    app.wsgi_app = _Guard(_rawQuery)
