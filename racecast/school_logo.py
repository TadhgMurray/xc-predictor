# Project: xc-predictor / racecast
# File:    school_logo.py
# Purpose: The site's side of the school logo job (305, docs/IMAGES-PLAN.md):
#          given a school string and its state, the PNG on disk, or nothing.
#
# ★ THE SCRAPER IS THE ONLY WRITER. scripts/scrape_school_logos.py fetches,
#   normalises and stores; this module reads one row and hands back a path.
#   Nothing here touches the network and nothing here writes.
#
# ★ A MISSING LOGO CHANGES NOTHING. No table, no row, an override of
#   "none", a file the disk lost -- every one of them returns None, and
#   the header, the card and the athlete card render exactly as they did
#   before this existed. That rule is what lets the scraper run against a
#   live site.
#
# ! THE FILENAME IS DERIVED, NEVER TRUSTED. School strings are free text
#   with slashes, dots and apostrophes in them ("Chisago Lakes/Rush City"),
#   so the file is named by a hash of (school, state) and the stored name
#   is checked against that shape before it is joined to a directory.
import hashlib
import os
import re
import time

# ! NOT UNDER static/: the site runs as a user that cannot write the repo,
#   and the scraper runs as another user again (the cards directory learnt
#   this the hard way, cards.py). One directory both agree on, from the
#   environment.
LOGO_DIR = os.environ.get("XCP_LOGO_DIR", "/var/lib/racecast/logos")

# the served size; the scraper writes exactly this
LOGO_PX = 512

# ★ INLINE CRESTS ARE TINY AND THERE ARE HUNDREDS OF THEM. A results table
#   names forty schools; handing each of them a 512 px PNG is a megabyte
#   for forty marks eighteen pixels wide. The route resizes on demand into
#   a cache the SITE can write (the logo directory belongs to the scraper's
#   user -- the same split cards.py learnt), and serves the full file if
#   anything about that fails.
THUMB_PX = (64, 128)
THUMB_DIR = os.environ.get("XCP_LOGO_CACHE", "/var/tmp/racecast-logo-thumbs")

_NAME_RE = re.compile(r"^[0-9a-f]{16}\.png$")

# ★ THE TABLE MAY NOT EXIST YET, AND ASKING COSTS A ROUND TRIP. Cached per
#   process for five minutes: a site started before the first scrape picks
#   the table up within the hour, and a page never pays two queries for a
#   logo it has not got.
_HAVE = {"at": 0.0, "ok": False}
_HAVE_TTL = 300

# ★ THE SITE-WIDE CACHE, THE ONE school_identity ALREADY USES. A school
#   name is mentioned hundreds of times on a busy page -- a race result, a
#   meet's standings, a board -- and every one of them has to know whether
#   there is a crest BEFORE it writes an <img>, because a tag that 404s is
#   worse than no tag. One query at start-up answers all of them: which
#   (school, state) rows serve. A few hundred kilobytes, loaded like the
#   labels, refreshed by a restart after the scraper runs.
_CRESTS = {"loaded": False, "map": {}}


def fileFor(school, state=None):
    """The file name for one school, the scraper's and the site's shared
    convention: sha1(school|state) truncated, plus .png."""
    key = f"{school}|{(state or '').upper()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".png"


def pathFor(name):
    """The absolute path of a stored file name, or None when the name is
    not one of ours."""
    if not name or not _NAME_RE.match(str(name)):
        return None
    return os.path.join(LOGO_DIR, str(name))


def tableExists(cur, force=False):
    now = time.time()
    if not force and now - _HAVE["at"] < _HAVE_TTL:
        return _HAVE["ok"]
    try:
        cur.execute("SELECT to_regclass('public.school_logo')")
        row = cur.fetchone()
        val = row[0] if not isinstance(row, dict) else row.get("to_regclass")
        _HAVE["ok"] = val is not None
    except Exception:                              # noqa: BLE001 -- optional
        try:
            cur.connection.rollback()
        except Exception:                          # noqa: BLE001
            pass
        _HAVE["ok"] = False
    _HAVE["at"] = now
    return _HAVE["ok"]


def loadCrests(conn_factory, force=False):
    """Fill the site-wide cache: {school: [state, ...]} for every row that
    would serve. Safe to call always -- a missing table loads an empty map
    and no page draws a crest."""
    if _CRESTS["loaded"] and not force:
        return
    got = {}
    try:
        with conn_factory() as conn:
            with conn.cursor() as cur:
                if tableExists(cur, force=True):
                    cur.execute("""
                        SELECT school, state FROM school_logo
                        WHERE  path IS NOT NULL
                          AND  COALESCE(lower(override), '') <> 'none'
                          AND  (NOT shared OR override IS NOT NULL)
                    """)
                    for school, state in cur.fetchall():
                        got.setdefault(school, []).append((state or "").upper())
    except Exception:                              # noqa: BLE001 -- optional
        got = {}
    _CRESTS["map"] = got
    _CRESTS["loaded"] = True


def crestState(school, state=None):
    """The state whose crest answers for this mention, or None when none
    does. The cache's version of pickRow, and it keeps pickRow's rule: a
    name two real schools wear answers only when the caller says which."""
    rows = _CRESTS["map"].get(school)
    if not rows:
        return None
    st = (state or "").upper()
    if st and st in rows:
        return st
    if "" in rows:
        return ""
    return rows[0] if len(rows) == 1 else None


def crestUrl(school, state=None, px=None):
    """The <img src> for a mention of this school, or None -- with NO
    query, from the start-up cache, because this is called once per row of
    every table on the site."""
    st = crestState(school, state)
    if st is None:
        return None
    url = logoUrl(school, st or None)
    if px:
        url += ("&" if "?" in url else "?") + f"px={int(px)}"
    return url


def crestImg(school, state=None, px=64, size=18, cls="school-mark"):
    """The little crest that goes before a school's name, or "" (305). The
    template global `crest`.

    ★ CALLED ONCE PER ROW OF EVERY TABLE ON THE SITE, so it costs a dict
      lookup and nothing else. "" is the normal answer and renders as
      nothing at all, which is why this can be dropped anywhere a school is
      named without the template having to ask a question first.

    ⚠ IT RETURNS MARKUP, SO EVERYTHING IN IT IS ESCAPED HERE. School names
      are scraped free text and genuinely contain quotes and ampersands.
    """
    url = crestUrl(school, state, px)
    if not url:
        return ""
    from markupsafe import Markup, escape
    return Markup(f'<img class="{escape(cls)}" src="{escape(url)}" alt="" '
                  f'width="{int(size)}" height="{int(size)}" '
                  f'loading="lazy" decoding="async">')


def stampCrests(rows, school_key="school", state_key="state", px=64):
    """Give each row its crest URL, for the boards the BROWSER draws: JS
    cannot ask whether a crest exists without fetching it, and a broken
    <img> per school is worse than no crests. No query -- the answer is in
    the start-up cache -- and a school without one gets no key at all."""
    for r in rows or []:
        url = crestUrl(r.get(school_key), r.get(state_key), px)
        if url:
            r["crest"] = url
    return rows


def crestUrlForLink(link, px=64):
    """The crest for a "/school/<name>?state=ST" link, or None.

    ! FOR THE SEARCH INDEX ONLY. That table stores a rendered link and a
      rendered label rather than the (school, state) pair every other
      caller already holds, so this is the one place the pair is read back
      out of a URL. Anything else should pass the pair."""
    from urllib.parse import unquote, urlsplit, parse_qs
    if not link or not str(link).startswith("/school/"):
        return None
    parts = urlsplit(str(link))
    name = unquote(parts.path[len("/school/"):])
    if name.endswith("/prs"):
        name = name[:-4]
    state = (parse_qs(parts.query).get("state") or [None])[0]
    return crestUrl(name, state, px) if name else None


def thumbPath(path, px):
    """A small copy of a stored crest, drawn once and cached. Falls back to
    the full-size file for any reason at all: a px we do not offer, no
    Pillow, a cache directory the site cannot write."""
    if px not in THUMB_PX:
        return path
    try:
        name = f"{os.path.basename(path)[:-4]}-{px}.png"
        small = os.path.join(THUMB_DIR, name)
        if os.path.exists(small) and os.path.getmtime(small) >= os.path.getmtime(path):
            return small
        from PIL import Image
        os.makedirs(THUMB_DIR, exist_ok=True)
        im = Image.open(path).convert("RGBA")
        im.thumbnail((px, px), Image.LANCZOS)
        tmp = small + ".tmp"
        im.save(tmp, format="PNG", optimize=True)
        os.replace(tmp, small)
        return small
    except Exception:                              # noqa: BLE001
        return path


def logoRow(cur, school, state=None):
    """The stored row for one school, or None. The state is the identity's
    (school_identity's cluster), and a row stored with no state answers for
    every state: a school named once has one logo.

    ⚠ SHARED LOGOS DO NOT SERVE. A district that puts one crest on forty
      school sites is worse than no crest at all -- forty pages wearing the
      same picture reads as a bug. The scraper marks those rows `shared`
      and this skips them; an override still wins.

    ⚠ NO STATE, NO GUESS, WHEN THE NAME IS SHARED. Two real schools wear
      "Kingston" (WA and MO) and each has its own row; a caller who cannot
      say which one it means gets nothing rather than a coin toss. A name
      with exactly one row is not a coin toss and answers.
    """
    if not school or not tableExists(cur):
        return None
    st = (state or "").upper()
    try:
        cur.execute("""
            SELECT school, state, path, kind, source_url, shared, override
            FROM   school_logo WHERE school = %s
        """, (school,))
        rows = cur.fetchall() or []
    except Exception:                              # noqa: BLE001 -- optional
        try:
            cur.connection.rollback()
        except Exception:                          # noqa: BLE001
            pass
        return None
    rows = [r if isinstance(r, dict) else {
        "school": r[0], "state": r[1], "path": r[2], "kind": r[3],
        "source_url": r[4], "shared": r[5], "override": r[6]} for r in rows]
    return pickRow(rows, st)


def pickRow(rows, state):
    """The row that answers for `state`: the one stored under it, else a
    row stored under no state at all, else -- only when the name has just
    one row -- that row."""
    if not rows:
        return None
    st = (state or "").upper()
    if st:
        for r in rows:
            if (r.get("state") or "").upper() == st:
                return r
    for r in rows:
        if not (r.get("state") or ""):
            return r
    return rows[0] if len(rows) == 1 else None


def logoPath(cur, school, state=None):
    """The absolute path of this school's logo PNG, or None. Every failure
    -- no table, no row, an override of "none", a shared district crest, a
    file the disk no longer has -- is None."""
    row = logoRow(cur, school, state)
    if row is None:
        return None
    if (row.get("override") or "").strip().lower() == "none":
        return None
    if row.get("shared") and not row.get("override"):
        return None
    path = pathFor(row.get("path"))
    if path is None or not os.path.exists(path):
        return None
    return path


def logoUrl(school, state=None):
    """The route a page's <img src> points at. Built without a query, so a
    template can call it before anything has been fetched -- the caller
    decides whether to draw the tag by asking logoPath first."""
    from urllib.parse import quote
    url = "/img/school/" + quote(school or "", safe="") + ".png"
    if state:
        url += "?state=" + quote(str(state).upper(), safe="")
    return url
