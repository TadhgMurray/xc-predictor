# Project: xc-predictor / racecast
# File:    person_optout.py
# Purpose: An athlete who asked to be taken off the public site (owner,
#          2026-10-11: "remove/unpublish my results"). The rows stay; what
#          the public SEES changes.
#
# ★ HIDDEN, NOT DELETED. person_optout holds the ids; results, ratings and
#   team scores are untouched, so a team's score and every other athlete's
#   rating read exactly as they did. What changes:
#     - the athlete's own pages (/athlete/<id>, its profile, card, compare,
#       recruit, the per-athlete APIs) answer 410 with noindex  (app.py)
#     - every row that names them reads "Hidden athlete" with no link
#       (maskTree on the template context and the JSON, maskHtml on the
#       finished page -- app.py's hooks)
#     - the search index, the sitemap and the follow digest leave them out
#       (search_index.py, build_sitemap.py, follow_alerts.py)
#
# ★ ONE CHOKE POINT, NOT EIGHTY TEMPLATES. The name reaches the page through
#   forty templates and a dozen JS renderers, most of which link
#   /athlete/{{ r.person_id }} unguarded. Masking the ROWS before they are
#   rendered (one walk of the context / the JSON) and unlinking whatever
#   anchor still points at a hidden id (one pass over the HTML) covers every
#   one of them, including the next template someone writes.
#
# ! LOADED ONCE PER PROCESS, like the crests and the labels: restart the
#   site after scripts/optout.py --add. A missing table is an empty set --
#   nothing hidden, never an error.
#
# ! THE OWNER DECIDES. Nothing here detects; an id is added by hand with
#   scripts/optout.py, at the athlete's request.
import json
import re

HIDDEN_NAME = "Hidden athlete"

DDL = """
CREATE TABLE IF NOT EXISTS person_optout (
    person_id  bigint PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    note       text
)
"""

_STATE = {"ids": frozenset(), "loaded": False}

# ★ WHICH KEY NAMES WHICH PERSON. A row that carries the id under one key
#   carries the name under the keys beside it; a team row's best runner is
#   best_person_id / best_name. Only string values are replaced, so a key
#   that happens to hold a dict (the athlete page's `athlete`) is left be.
_ID_NAMES = (
    ("person_id", ("name", "athlete_name", "full_name", "display_name",
                   "athlete", "label")),
    ("best_person_id", ("best_name",)),
)
# first / last kept apart by some rows; together they still read HIDDEN_NAME
_NAME_HALVES = (("first_name", HIDDEN_NAME.split()[0]),
                ("last_name", HIDDEN_NAME.split()[1]))
# a search row names its athlete only by link: {kind, label, link}
_LINK_KEYS = ("link", "url", "href")
# ! DEEP ENOUGH FOR THE DEEPEST ROW: context -> meet -> teams -> team ->
#   runners -> row is five; one spare. A bound, so a context that holds a
#   big unrelated structure cannot turn every render into a long walk.
MAX_DEPTH = 6
# the Flask globals every template context carries: never rows
_SKIP_KEYS = frozenset(("config", "request", "session", "g", "url_for",
                        "get_flashed_messages"))

_ATHLETE_PATH = re.compile(r"^(?:https?://[^/]+)?/athlete/(\d+)(?:[/?#].*)?$")
# an anchor to an athlete page, any attributes, inner text kept
_ANCHOR = re.compile(
    r'<a\b[^>]*?\bhref="(?:https?://[^"/]+)?/athlete/(\d+)(?:[/?#][^"]*)?"[^>]*>(.*?)</a>',
    re.S | re.I)


# ------------------------------------------------------------------ #
#  the table and the process cache
# ------------------------------------------------------------------ #

def ensure(cur):
    """person_optout, through ensureTable (the codebase's one way to run
    DDL: a column added later reaches the table made before it)."""
    from scrape_school_logos import ensureTable
    ensureTable(cur, DDL.strip())


def present(cur):
    cur.execute("SELECT to_regclass('public.person_optout') AS t")
    row = cur.fetchone()
    return (row["t"] if isinstance(row, dict) else row[0]) is not None


def loadIds(cur):
    """Every hidden id, from the table (a script's own read). Empty when the
    table is not there."""
    if not present(cur):
        return frozenset()
    cur.execute("SELECT person_id FROM person_optout")
    return frozenset(int(r["person_id"] if isinstance(r, dict) else r[0])
                     for r in cur.fetchall())


def loadHidden(conn_factory, force=False):
    """Fill the process cache. Safe to call always: no table, no database,
    an empty set."""
    if _STATE["loaded"] and not force:
        return
    try:
        with conn_factory() as conn:
            with conn.cursor() as cur:
                _STATE["ids"] = loadIds(cur)
    except Exception:                    # noqa: BLE001 -- an empty set is the safe default
        pass
    _STATE["loaded"] = True


def setHidden(ids):
    """Replace the cache (tests, and a reload)."""
    _STATE["ids"] = frozenset(int(i) for i in ids)
    _STATE["loaded"] = True


def hiddenIds():
    return _STATE["ids"]


def isHidden(person_id):
    """True when this athlete asked to be hidden. Takes an int, a digit
    string or None; anything else is not hidden."""
    return _isHidden(person_id, _STATE["ids"])


def _isHidden(v, ids):
    try:
        return v is not None and int(v) in ids
    except (TypeError, ValueError):
        return False


def maskedName(person_id, name, ids=None):
    """The name to print for this person: theirs, or HIDDEN_NAME."""
    ids = _STATE["ids"] if ids is None else ids
    return HIDDEN_NAME if _isHidden(person_id, ids) else name


# ------------------------------------------------------------------ #
#  masking: the rows, the finished HTML, the JSON
# ------------------------------------------------------------------ #

def _linkId(v):
    m = _ATHLETE_PATH.match(v) if isinstance(v, str) else None
    return int(m.group(1)) if m else None


def _maskDict(d, ids):
    """Rename this row's hidden people in place. True when the row is a
    SEARCH HIT for a hidden athlete -- no id of its own, only a link -- which
    goes altogether; a result row keeps its place and loses its name."""
    has_id = False
    for id_key, name_keys in _ID_NAMES:
        has_id = has_id or id_key in d
        if _isHidden(d.get(id_key), ids):
            for k in name_keys:
                if isinstance(d.get(k), str):
                    d[k] = HIDDEN_NAME
            # a name the template assembles from its halves
            for k, v in _NAME_HALVES:
                if isinstance(d.get(k), str):
                    d[k] = v
    return not has_id and any(_linkId(d.get(k)) in ids for k in _LINK_KEYS)


def maskTree(obj, ids=None, depth=0, _seen=None):
    """Walk dicts and lists, renaming every hidden person's row in place and
    dropping search hits that point at one. Tuples are walked (their dicts
    are mutable) but not shortened."""
    ids = _STATE["ids"] if ids is None else ids
    if not ids or depth > MAX_DEPTH:
        return
    _seen = set() if _seen is None else _seen
    if id(obj) in _seen:
        return
    if isinstance(obj, dict):
        _seen.add(id(obj))
        _maskDict(obj, ids)
        for k, v in obj.items():
            if k not in _SKIP_KEYS and isinstance(v, (dict, list, tuple)):
                maskTree(v, ids, depth + 1, _seen)
    elif isinstance(obj, (list, tuple)):
        _seen.add(id(obj))
        if isinstance(obj, list):
            # a search hit for a hidden athlete goes altogether
            obj[:] = [v for v in obj
                      if not (isinstance(v, dict) and _maskDict(v, ids))]
        for v in obj:
            if isinstance(v, (dict, list, tuple)):
                maskTree(v, ids, depth + 1, _seen)


def maskHtml(html, ids=None):
    """Unlink every anchor to a hidden athlete's page, keeping its text (the
    rows were renamed before the render; this catches the link itself)."""
    ids = _STATE["ids"] if ids is None else ids
    if not ids or not any(f"/athlete/{i}" in html for i in ids):
        return html
    return _ANCHOR.sub(lambda m: m.group(2) if int(m.group(1)) in ids else m.group(0), html)


def maskJson(text, ids=None):
    """The same walk over a JSON body; the text back unchanged when no
    hidden id is in it (the cheap test first)."""
    ids = _STATE["ids"] if ids is None else ids
    if not ids or not any(str(i) in text for i in ids):
        return text
    try:
        obj = json.loads(text)
    except ValueError:
        return text
    maskTree(obj, ids)
    return json.dumps(obj)


GONE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page removed</title></head>
<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem">
<h1>Page removed</h1>
<p>This athlete's page has been removed at their request.</p>
<p><a href="/">Racecast home</a></p>
</body></html>
"""
