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
    """The text with every feed name taken out (see the module note). Pure."""
    text = _QUOTED.sub(lambda m: '"' + _CODE[m.group(1)] + '"', text)
    text = _SRC.sub(lambda m: "src=" + _CODE[m.group(1)], text)
    text = _ANET_SITE.sub("the results site", text)
    text = _TFRRS.sub("the college feed", text)
    return _ANET.sub("the main feed", text)


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
        if len(body) > max_bytes or not _NEEDLE.search(body):
            return response
        response.set_data(scrub(body.decode("utf-8")).encode("utf-8"))
    except Exception:                                   # noqa: BLE001 -- never break a page
        pass
    return response
