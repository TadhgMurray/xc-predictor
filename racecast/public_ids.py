# Project: xc-predictor / racecast
# File:    public_ids.py
# Purpose: OUR OWN IDS ON EVERY PUBLIC PAGE (owner, 2026-11-08: "get further
#          away from [the feeds] by randomizing the identifying info ... just
#          do all please"). The site's URLs carried the feeds' own numbers --
#          /athlete/<the results site's athlete id>, /meet/xc/<its meet id>,
#          /race/xc/<meet>/<division>, #race-<its result id> -- so any page
#          lined up with the feed's page of the same number.
#
# ★ A KEYED PERMUTATION, NOT A TABLE. Each id is put through a four-round
#   Feistel network over 64 bits keyed by XCP_PUBLIC_ID_KEY (HMAC-SHA256
#   rounds), tweaked by the kind of thing it names, plus a 3-bit check, and
#   written as the kind's letter and 13 base-36 digits:
#       person 'a'   meet 'm'   division/race 'd'   result 'r'   venue 'v'
#   /athlete/27034607 -> /athlete/a0k3...; the same id always gives the same
#   token, nothing is stored, and only the key turns one back. The letter
#   keeps a token from ever reading as an old number.
#
# ★ AT THE EDGES, NOT IN EVERY TEMPLATE. The routes and the database keep
#   their integers:
#     - out: every HTML, JSON and XML response is rewritten (rewriteBody):
#       the id parts of the site's own URL shapes, the anchors and data
#       attributes that carry ids, JSON fields named for ids, and the
#       Location header of a redirect;
#     - in: a WSGI layer (decodeEnviron) turns tokens in the path (under the
#       id-bearing prefixes only), in id-named query parameters and in JSON
#       request bodies back into the integers the routes read.
#   An old URL with a feed number in it is sent on, 301, to its token URL
#   (legacyRedirect), so search engines and shared links follow; with
#   XCP_PUBLIC_ID_LEGACY=off it is a 404 instead.
#
# ! NO KEY, NO CHANGE. Without XCP_PUBLIC_ID_KEY every function here is the
#   identity: a development copy, the tests and a server not yet given a key
#   behave exactly as before. THE KEY MUST NEVER CHANGE once pages are out:
#   every token -- every link anyone saved -- is a function of it.
#
# ! EVENT IDS ARE NOT ENCODED. A track race is /race/tf/<meet>/<event>/<div>;
#   the event is the catalogue number for "1600m", the same in every meet,
#   and the page scripts key their event tables on it. It names no meet.
import hashlib
import hmac
import json
import os
import re
from functools import lru_cache

KINDS = "amdrv"
TOKEN_RE = re.compile(r"[amdrv][0-9a-z]{13}")
_FULL = re.compile(r"^[amdrv][0-9a-z]{13}$")
_MASK64 = (1 << 64) - 1
_MASK32 = (1 << 32) - 1
_ROUNDS = 4
_CHECK_BITS = 3
_WIDTH = 13


def _key():
    k = (os.environ.get("XCP_PUBLIC_ID_KEY") or "").strip()
    return k.encode() if k else None


def enabled():
    return _key() is not None


def _round(key, kind, rnd, half):
    d = hmac.new(key, f"{kind}:{rnd}:{half}".encode(), hashlib.sha256).digest()
    return int.from_bytes(d[:4], "big")


def _feistel(u, key, kind, inverse=False):
    left, right = u >> 32, u & _MASK32
    rounds = range(_ROUNDS - 1, -1, -1) if inverse else range(_ROUNDS)
    if not inverse:
        for r in rounds:
            left, right = right, left ^ _round(key, kind, r, right)
        return (left << 32) | right
    for r in rounds:
        left, right = right ^ _round(key, kind, r, left), left
    return (left << 32) | right


def _check(key, kind, x):
    d = hmac.new(key, f"c:{kind}:{x}".encode(), hashlib.sha256).digest()
    return d[0] & ((1 << _CHECK_BITS) - 1)


def _b36(n):
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + s
    return s.rjust(_WIDTH, "0")


@lru_cache(maxsize=500_000)
def encode(kind, n):
    """The public token for integer id `n` of `kind` ('a','m','d','r','v'),
    or str(n) when there is no key."""
    key = _key()
    if key is None:
        return str(n)
    u = int(n) & _MASK64
    x = _feistel(u, key, kind)
    return kind + _b36((x << _CHECK_BITS) | _check(key, kind, x))


@lru_cache(maxsize=500_000)
def decode(token):
    """(kind, integer id) for a well-formed token of this key, else None."""
    key = _key()
    if key is None or not _FULL.match(token or ""):
        return None
    kind = token[0]
    v = int(token[1:], 36)
    x, chk = v >> _CHECK_BITS, v & ((1 << _CHECK_BITS) - 1)
    if x > _MASK64 or _check(key, kind, x) != chk:
        return None
    u = _feistel(x, key, kind, inverse=True)
    return kind, (u - (1 << 64) if u >= (1 << 63) else u)


# ------------------------------------------------------------------ #
#  OUT: the site's URL shapes, anchors, attributes and JSON fields
# ------------------------------------------------------------------ #
_N = r"-?\d+"
# (pattern, kinds of its id groups in order); every group 1..k is (prefix,
# id, prefix, id ...): the prefixes are kept, the ids encoded
_PATHS = [
    (re.compile(r"(/(?:athlete|recruit|debug/athlete|card/athlete|api/next-race/athlete|"
                r"api/runners-like-you|account/admin/social)/)(" + _N + r")(?![\d])"), "a"),
    (re.compile(r"(/(?:card/)?race/xc/)(" + _N + r")(/)(" + _N + r")(?![\d])"), "md"),
    (re.compile(r"(/race/xc/)(" + _N + r")(/compiled/)"), "m"),
    (re.compile(r"(/(?:card/)?race/tf/)(" + _N + r")(/" + _N + r"/)(" + _N + r")(?![\d])"), "md"),
    (re.compile(r"(/race/tf/)(" + _N + r")(/" + _N + r"/None)"), "m"),
    (re.compile(r"(/api/meet-preview/xc/)(" + _N + r")(/)(" + _N + r")(?![\d])"), "md"),
    (re.compile(r"(/(?:meet/(?:xc|tf)|meet/preview/xc|meet/recap/xc|card/meet/(?:xc|tf)|"
                r"card/recap/xc|api/meet/(?:xc|tf))/)(" + _N + r")(?![\d])"), "m"),
    (re.compile(r"(/(?:card/)?venue/tf/)(\d+)(?=/)"), "v"),
    (re.compile(r"(/api/explain/(?:xc|tf|XC|TF)/)(" + _N + r")(?![\d])"), "r"),
]
# query parameters that carry ids, and their kind
QUERY_KINDS = {"r": "r", "rid": "r", "jump": "a", "person": "a", "person_id": "a",
               "athlete": "a", "pid": "a", "a": "a", "b": "a",
               "meet_id": "m", "div_id": "d"}
_QUERY = re.compile(r"([?&](?:amp;)?)(" + "|".join(sorted(QUERY_KINDS, key=len, reverse=True))
                    + r")=(" + _N + r")(?![\d])")
# ids inside the HTML: anchors and the attributes the page scripts read
_HTML = [
    (re.compile(r"(#race-|id=\"race-|data-flash-id=\"race-|#r|id=\"r)(" + _N + r")(?![\d])"), "r"),
    (re.compile(r"(data-rid=\"|data-rx=\"(?:xc|tf):)(" + _N + r")(?![\d])"), "r"),
    (re.compile(r"(data-(?:pid|person-id|fix-person|prefill-athlete|a|b)=\")(" + _N + r")(?![\d])"), "a"),
    (re.compile(r"(name=\"(?:person_id|athlete|pid)\" value=\"|id=\"r-athlete\" type=\"hidden\" value=\")("
                + _N + r")(?![\d])"), "a"),
    (re.compile(r"(data-meet=\")(" + _N + r")(?![\d])"), "m"),
    (re.compile(r"(data-div=\"|#div-|id=\"div-|data-tab=\"div-|data-panel=\"div-)(" + _N + r")(?![\d])"), "d"),
]
_DATA_RIDS = re.compile(r"(data-rids=\")([-\d,\s]+)(\")")
# JSON fields named for ids (in a JSON body, and in JSON inside a page)
ID_KEYS = {"person_id": "a", "pid": "a", "athlete_id": "a", "meet_id": "m",
           "div_id": "d", "result_id": "r", "rid": "r"}
_JSON_FIELD = re.compile(r"(\"(" + "|".join(ID_KEYS) + r")\"\s*:\s*)\"?(" + _N + r")\"?(?![\d])")


def _sub(pattern, kinds, text):
    def rep(m):
        out, k = [], 0
        for i, g in enumerate(m.groups()):
            if i % 2 == 0:
                out.append(g)
            else:
                out.append(encode(kinds[k], int(g)))
                k += 1
        return "".join(out)
    return pattern.sub(rep, text)


def rewritePaths(text):
    """The id parts of the site's own URL shapes in `text`, encoded."""
    for pat, kinds in _PATHS:
        text = _sub(pat, kinds, text)
    return _QUERY.sub(lambda m: m.group(1) + m.group(2) + "="
                      + encode(QUERY_KINDS[m.group(2)], int(m.group(3))), text)


def rewriteHtml(text):
    text = rewritePaths(text)
    for pat, kind in _HTML:
        text = _sub(pat, kind, text)
    text = _DATA_RIDS.sub(lambda m: m.group(1) + ",".join(
        encode("r", int(x)) if re.fullmatch(_N, x.strip()) else x
        for x in m.group(2).split(",")) + m.group(3), text)
    return _JSON_FIELD.sub(lambda m: m.group(1) + '"' + encode(ID_KEYS[m.group(2)],
                                                               int(m.group(3))) + '"', text)


def _walk(v, key=None):
    """A JSON value with its ids encoded (see the header)."""
    if isinstance(v, dict):
        if key == "levels":                     # above-level: result id -> value
            return {(encode("r", int(k)) if re.fullmatch(_N, str(k)) else k): x
                    for k, x in v.items()}
        return {k: _walk(x, k) for k, x in v.items()}
    if isinstance(v, list):
        if key in ("surprises", "rids"):
            return [encode("r", int(x)) if isinstance(x, (int, str)) and re.fullmatch(_N, str(x))
                    else _walk(x) for x in v]
        return [_walk(x) for x in v]
    if key in ID_KEYS and not isinstance(v, bool) and isinstance(v, (int, str)) \
            and re.fullmatch(_N, str(v)):
        return encode(ID_KEYS[key], int(v))
    if isinstance(v, str) and "/" in v:
        return rewritePaths(v)
    return v


def rewriteJson(text):
    try:
        data = json.loads(text)
    except ValueError:
        return text
    return json.dumps(_walk(data), ensure_ascii=False, separators=(",", ":"))


def rewriteResponse(response):
    """after_request: a page, JSON or XML body and a redirect's Location,
    with the ids encoded. Never breaks a response."""
    if not enabled():
        return response
    try:
        loc = response.headers.get("Location")
        if loc:
            response.headers["Location"] = rewritePaths(loc)
        mt = response.mimetype or ""
        if not (mt == "text/html" or mt.endswith("json") or mt.endswith("xml")):
            return response
        if response.direct_passthrough or response.is_streamed:
            return response
        text = response.get_data(as_text=True)
        out = (rewriteJson(text) if mt.endswith("json")
               else rewritePaths(text) if mt.endswith("xml") else rewriteHtml(text))
        if out != text:
            response.set_data(out)
    except Exception:                                    # noqa: BLE001
        pass
    return response


# ------------------------------------------------------------------ #
#  IN: tokens back to the integers the routes read
# ------------------------------------------------------------------ #
ID_PREFIXES = ("/athlete/", "/recruit/", "/meet/", "/race/", "/venue/", "/card/",
               "/debug/athlete/", "/api/explain/", "/api/meet-preview/", "/api/meet/",
               "/api/next-race/", "/api/runners-like-you/", "/account/admin/social/")
_SEG = re.compile(r"^([amdrv][0-9a-z]{13})(\.png|\.html)?$")
# the pages an old numeric URL is sent on from (not the APIs or the cards)
_PAGES = ("/athlete/", "/recruit/", "/meet/", "/race/", "/venue/")


def decodePath(path):
    if not path.startswith(ID_PREFIXES):
        return path
    parts = path.split("/")
    for i, seg in enumerate(parts):
        m = _SEG.match(seg)
        if m:
            got = decode(m.group(1))
            if got:
                parts[i] = str(got[1]) + (m.group(2) or "")
    return "/".join(parts)


# id-bearing parameters whose value may hold several ids or a compound key:
# the predictions page's div_ids=<d>,<d> and person_id=<a>,<a>, and a track
# race key div_id=<div>-<event>
_LIST_PARAMS = set(QUERY_KINDS) | {"div_ids", "person_ids", "ids"}
_IN_VALUE = re.compile(r"(?<![0-9a-z])[amdrv][0-9a-z]{13}(?![0-9a-z])")


def _decodeIn(text):
    def rep(m):
        got = decode(m.group(0))
        return str(got[1]) if got else m.group(0)
    return _IN_VALUE.sub(rep, text)


def decodeQuery(qs):
    if not qs:
        return qs
    out = []
    for part in qs.split("&"):
        k, eq, v = part.partition("=")
        if eq and k in _LIST_PARAMS:
            v = _decodeIn(v)
        out.append(k + eq + v)
    return "&".join(out)


def _unwalk(v, key=None):
    if isinstance(v, dict):
        return {k: _unwalk(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_unwalk(x, key) for x in v]
    if isinstance(v, str) and (key in ID_KEYS or key in _LIST_PARAMS
                               or key in ("surprises", "rids")):
        if _FULL.match(v):
            got = decode(v)
            if got:
                return got[1]
        return _decodeIn(v)
    return v


def legacyTarget(path, qs, method):
    """The token URL an old numeric page URL is sent on to, or None."""
    if method not in ("GET", "HEAD") or not path.startswith(_PAGES):
        return None
    new = rewritePaths(path)
    newq = rewritePaths("?" + qs)[1:] if qs else qs
    if new == path and newq == qs:
        return None
    return new + ("?" + newq if newq else "")


class Middleware:
    """WSGI: decode tokens in the path, the id-named query parameters and a
    JSON request body; mark an old numeric page URL for its 301."""

    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        if not enabled():
            return self.wsgi(environ, start_response)
        try:
            path = environ.get("PATH_INFO") or ""
            qs = environ.get("QUERY_STRING") or ""
            legacy = legacyTarget(path, qs, environ.get("REQUEST_METHOD", "GET"))
            if legacy:
                environ["rc.public_id.legacy"] = legacy
            environ["PATH_INFO"] = decodePath(path)
            environ["QUERY_STRING"] = decodeQuery(qs)
            ct = (environ.get("CONTENT_TYPE") or "").lower()
            if ("x-www-form-urlencoded" in ct
                    and environ.get("REQUEST_METHOD") in ("POST", "PUT", "PATCH")):
                n = int(environ.get("CONTENT_LENGTH") or 0)
                if 0 < n <= 2_000_000:
                    import io
                    raw = environ["wsgi.input"].read(n)
                    body = decodeQuery(raw.decode("utf-8", "replace")).encode()
                    environ["wsgi.input"] = io.BytesIO(body)
                    environ["CONTENT_LENGTH"] = str(len(body))
            elif "json" in ct and environ.get("REQUEST_METHOD") in ("POST", "PUT", "PATCH"):
                n = int(environ.get("CONTENT_LENGTH") or 0)
                if 0 < n <= 2_000_000:
                    import io
                    raw = environ["wsgi.input"].read(n)
                    try:
                        body = json.dumps(_unwalk(json.loads(raw))).encode()
                    except ValueError:
                        body = raw
                    environ["wsgi.input"] = io.BytesIO(body)
                    environ["CONTENT_LENGTH"] = str(len(body))
        except Exception:                                # noqa: BLE001
            pass
        return self.wsgi(environ, start_response)


def legacyMode():
    return (os.environ.get("XCP_PUBLIC_ID_LEGACY") or "redirect").strip().lower()
