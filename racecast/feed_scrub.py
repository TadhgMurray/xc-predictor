"""
feed_scrub.py -- no feed name leaves the site (owner, 2026-11-06: "nothing on
the site should say athletic net or tfrrs"; "yes" to the data APIs too).

The templates say neither (tests/test_no_feed_names.py), but values do: a
row's source ("anet" / "tfrrs") in an API's JSON or a page's embedded chart
data, a link built from it, a meet or venue name a feed wrote ("results on
athletic.net"). Every HTML and JSON response passes through scrub() once,
in app.after_request:

    "anet" / "tfrrs" as a whole JSON string    -> "1" / "2"
    src=anet / src=tfrrs in a link              -> src=1 / src=2
    athletic.net, AthleticNet, athletic net     -> the results site
    TFRRS / tfrrs as a word                     -> the college feed
    anet as a word                              -> the main feed

The site reads "1" / "2" back wherever it reads a feed (app._PublicSrc for
?src=; the scripts only pass the value back). Internal keys made of more
than the bare name ("tfrrs_join") are words of their own and are left.
"""
import re

_QUOTED = re.compile(r'"(anet|tfrrs)"')
_SRC = re.compile(r"\bsrc=(anet|tfrrs)\b")
# a "the" before the name is the replacement's own ("the TFRRS copy")
_ANET_SITE = re.compile(r"(?:\bthe\s+)?(?:www\.)?athletic\s*\.?\s*net(?:\.com)?\b", re.I)
_TFRRS = re.compile(r"(?:\bthe\s+)?(?:www\.)?\btfrrs\b(?:\.org)?", re.I)
_ANET = re.compile(r"(?:\bthe\s+)?\banet\b")
_CODE = {"anet": "1", "tfrrs": "2"}
_NEEDLE = re.compile(rb"anet|tfrrs|thletic", re.I)


def scrub(text):
    """The text with every feed name taken out (see the module note). Pure.

    ★ THE SAME FIVE PASSES, TRIED ONLY WHERE THEY CAN MATCH (2026-11-08).
      Each pattern but the first begins with an optional group or a \\b, so
      `re` could not skip ahead to a candidate and tried every character of
      the body: ~130 ms of CPU for a 600 KB page, 370 ms for a 5,000-row
      board, on every response. Every match of a pass contains a literal
      core ("src=anet|tfrrs", "athletic", "tfrrs", "anet"), preceded only
      by characters of its optional "the " / "www." prefix; _passAt finds
      the cores with str.find and runs the SAME compiled pattern, anchored,
      from the start of the prefix run before each one -- the leftmost
      match `re.sub` would have found, so the output is identical (scrubRef
      keeps the plain passes; tests/test_feed_scrub_fast.py compares them).
      The four characters `re.I` folds onto ASCII letters (İ ı ſ K) would
      let a match hide from a lower-cased find, so a body holding any of
      them takes the plain passes."""
    if any(ch in text for ch in _FOLDS):
        return scrubRef(text)
    text = _QUOTED.sub(_quotedCode, text)
    text = _passAt(_SRC, _srcCode, text, text, _srcCore, back=False)
    low = text.lower()
    out = _passAt(_ANET_SITE, "the results site", text, low, _findIn("athletic"))
    if out is not text:
        text, low = out, out.lower()
    text = _passAt(_TFRRS, "the college feed", text, low, _findIn("tfrrs"))
    return _passAt(_ANET, "the main feed", text, text, _findIn("anet"))


def scrubRef(text):
    """scrub() as five plain re.sub passes: the reference the fast one is
    held to, and what a body with a case-folding character gets."""
    text = _QUOTED.sub(_quotedCode, text)
    text = _SRC.sub(_srcCode, text)
    text = _ANET_SITE.sub("the results site", text)
    text = _TFRRS.sub("the college feed", text)
    return _ANET.sub("the main feed", text)


def _quotedCode(m):
    return '"' + _CODE[m.group(1)] + '"'


def _srcCode(m):
    return "src=" + _CODE[m.group(1)]


# the characters re.I matches to an ASCII letter besides its own two cases
_FOLDS = ("\u0130", "\u0131", "\u017f", "\u212a")
# what may stand between a match's start and its core: "the", spaces, "www."
_PREFIX = frozenset("thewTHEW.")
_SRC_CORE = re.compile(r"src=(?:anet|tfrrs)")


def _srcCore(hay, pos):
    m = _SRC_CORE.search(hay, pos)
    return m.start() if m else -1


def _findIn(core):
    return lambda hay, pos: hay.find(core, pos)


def _passAt(rx, repl, text, hay, find, back=True):
    """rx.sub(repl, text), trying rx only at the cores `find` locates in hay
    (text, or text.lower() with the same length). back: a match may start
    before its core, on a run of _PREFIX characters or whitespace."""
    out = []
    q = 0
    c = find(hay, 0)
    while c != -1:
        s = c
        if back:
            while s > q and (text[s - 1] in _PREFIX or text[s - 1].isspace()):
                s -= 1
        m = None
        for p in range(s, c + 1):
            m = rx.match(text, p)
            if m is not None:
                break
        if m is None:
            c = find(hay, c + 1)
            continue
        out.append(text[q:m.start()])
        out.append(repl(m) if callable(repl) else repl)
        q = m.end()
        c = find(hay, q)
    if not out:
        return text
    out.append(text[q:])
    return "".join(out)


def hasFeedWord(body):
    """_NEEDLE.search(body), without the regex: a case-insensitive bytes
    pattern folds ASCII only, exactly as bytes.lower() does. ~10x faster
    on a page with none (the regex has no literal prefix to skip on)."""
    low = body.lower()
    return b"anet" in low or b"tfrrs" in low or b"thletic" in low


def scrubResponse(response, max_bytes=30 * 1024 * 1024):
    """after_request: rewrite an HTML or JSON body in place; anything else,
    streamed, or past max_bytes goes out as it was."""
    try:
        mt = response.mimetype or ""
        if not (mt == "text/html" or mt.endswith("json")):
            return response
        if response.direct_passthrough or response.is_streamed:
            return response
        body = response.get_data()
        if len(body) > max_bytes or not hasFeedWord(body):
            return response
        text = body.decode("utf-8")
        out = scrub(text)
        if out is not text:             # nothing replaced: the body as it was
            response.set_data(out.encode("utf-8"))
    except Exception:                                   # noqa: BLE001 -- never break a page
        pass
    return response
