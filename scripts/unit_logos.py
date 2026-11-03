#!/usr/bin/env python3
"""
unit_logos.py -- a conference's (or league's) own logo, for its badge on the
conference board and its page (owner, 2026-11-02: "Can we not get these?").

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/unit_logos.py --set conference NESCAC --url https://.../nescac.png            # dry run
    $P scripts/unit_logos.py --set conference NESCAC --url https://.../nescac.png --write
    $P scripts/unit_logos.py --set league "Middlesex League" --state MA --url ... --write
    $P scripts/unit_logos.py --auto                      # what Wikipedia offers, nothing written
    $P scripts/unit_logos.py --auto --write --limit 40   # pin those (review the dry run first)

★ ONE TABLE, ONE ROUTE, ONE CACHE. A unit's logo is stored in school_logo
  under the reserved name unit:<kind>:<NAME> (unitKey), so the school crest
  pipeline -- fetch, normalise to a 512 px PNG, the start-up cache, the
  /img/school route, the version in the URL -- serves it unchanged. The
  generated badge (racecast/unit_board.badgeSvg) stays the fallback.

★ --auto ASKS WIKIPEDIA, AND ONLY FOR COLLEGE CONFERENCES. A conference's
  article carries its logo as the page image; a search for "<name> athletic
  conference" finds the article ("NESCAC" -> New England Small College
  Athletic Conference). A result is taken only when its title reads like a
  conference (Conference / League / Association / Athletic), and every
  pairing is printed for review before --write. A high school league has
  no article to speak of; those are --set by hand.
! ONE REQUEST A SECOND, a User-Agent with a contact, as Wikimedia asks.
! THE PICTURE IS THE CONFERENCE'S OWN MARK, shown beside its name on the
  page that is about it -- the same use the school crests already make.
"""
import argparse
import json
import os
import sys
import re
import time
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

API = "https://en.wikipedia.org/w/api.php"
UA = ("racecast/1.0 (https://racecast.co/about; contact: tadhg.a.murray@gmail.com) "
      "unit_logos.py")
CONF_WORDS = ("conference", "league", "association", "athletic", "collegiate")


# a conference's mark may be a wordmark up to 3:1 (scrape_school_logos.acceptable)
UNIT_SHAPE = "override:icon"

from unit_board import logoKey as unitKey                      # noqa: E402  the one definition


def looksLikeConference(title):
    """Is this article title a conference's? Pure."""
    t = (title or "").lower()
    return any(w in t for w in CONF_WORDS)


RETRY_WAITS = (15, 45, 120)


def _get(params, waits=RETRY_WAITS):
    """One API call. ⚠ Wikipedia answers "You are making too many requests"
    as plain text (or a 429); that is waited out, not counted as no logo."""
    q = urllib.parse.urlencode(dict(params, format="json", formatversion=2))
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
    for wait in tuple(waits) + (None,):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (ValueError, urllib.error.HTTPError) as exc:
            if wait is None or (isinstance(exc, urllib.error.HTTPError)
                                and exc.code not in (429, 503)):
                raise
            print(f"  (Wikipedia is rate-limiting; waiting {wait}s)")
            time.sleep(wait)


_GENERIC = {"conference", "conferences", "athletic", "athletics", "league", "association",
            "intercollegiate", "collegiate", "the", "of", "and", "college", "girls", "boys"}


def _tokens(text):
    import re
    text = re.sub(r"\([^)]*\)", " ", str(text or ""))          # "(athletic conference)"
    return [t for t in re.findall(r"[a-z0-9]+", text.lower())]


def matches(name, title, intro=""):
    """Is this article the conference `name` names? Pure.

    ★ STRICT, AFTER THE OWNER'S DRY RUN (2026-11-02: A-10 -> Pac-12, MAAC ->
      Metro Conference, EAST -> Big East, MAC -> the Great Midwest). Either
      the article's intro states the name as its abbreviation -- "(NEC)",
      "(A-10)", which every conference article opens with -- or the two
      names are the same words once the generic ones (Conference, Athletic,
      League ...) are set aside: "Ivy League" / "Ivy League", "Middle
      Atlantic" / "Middle Atlantic Conferences"."""
    import re
    raw = str(name or "").strip()
    # ! NOT AN ARTICLE ABOUT ONE SEASON, ONE TOURNAMENT OR ONE SPORT (the
    #   owner's second dry run: NEC -> "Northeast Conference men's soccer
    #   tournament"); the conference's own article carries its logo
    if re.search(r"\b(season|tournament|championships?|men's|women's|football|basketball|"
                 r"soccer|baseball|softball|hockey|\d{4})\b", title or "", re.I):
        return False
    if raw and re.search(r"\(\s*" + re.escape(raw) + r"\s*\)", intro or "", re.I):
        # ...and the abbreviation spells the title (MAAC is not "Metro
        # Conference", whose intro happens to name the MAAC): its letters
        # appear in order in the title
        letters = re.sub(r"[^a-z0-9]", "", raw.lower())
        hay = re.sub(r"[^a-z0-9]", "", (title or "").lower())
        it = iter(hay)
        if all(ch in it for ch in letters):
            return True
    a = {t.rstrip("s") for t in _tokens(raw)} - {g.rstrip("s") for g in _GENERIC}
    b = {t.rstrip("s") for t in _tokens(title)} - {g.rstrip("s") for g in _GENERIC}
    return bool(a) and a == b


# Wikipedia's own sister-project and maintenance icons -- "File:Commons-logo.svg"
# sits at the foot of nearly every article (owner, 2026-11-02: the SEC was
# pinned to the Wikimedia Commons logo).
_WIKI_ICONS = ("commons", "wiki", "wiktionary", "mediawiki", "oojs", "ambox",
               "question book", "edit-clear", "padlock", "symbol ", "portal",
               "crystal clear", "nuvola", "folder hexagonal", "sports icon",
               "open access", "transparent.svg", "transparent square", " pog",
               "increase2", "decrease2")
_OLD_WORDS = ("former", "old ", "_old", "previous", "historic", "retired",
              "alternate", "secondary", "academic", "1990", "200", "198", "197")
_TITLE_FILLER = {"the", "of", "and", "conference", "athletic", "athletics",
                 "association", "intercollegiate", "league", "collegiate"}


def _words(s):
    return [w for w in re.split(r"[^a-z0-9]+", str(s).lower()) if w]


def logoFile(files, title=None):
    """The article's LOGO among its image files, or None. Pure.

    ★ NOT THE PAGE IMAGE (owner's --write run, 2026-11-02: A-10 was pinned to
      "Atlantic_10_Conference_map.svg"). A conference article's lead image
      is as often a map of its members as its mark; the file NAMED a logo is
      the mark. Maps, locations and member charts are never taken.
    ★ NOT WIKIPEDIA'S OWN ICONS, AND THE ONE NAMING THE ARTICLE FIRST (SEC got
      "Commons-logo.svg"; its own is "Southeastern Conference logo (2024).svg").
      Member schools' logos sit in the same list, so with a title a file that
      names it beats one that does not, and with none naming it nothing is taken."""
    picks = []
    for f in files or []:
        low = str(f).lower()
        if any(w in low for w in ("map", "location", "members", "locator", "flag")):
            continue
        if any(w in low for w in _WIKI_ICONS):
            continue
        if "logo" in low or "wordmark" in low:
            picks.append(f)
    if not picks:
        return None

    def rank(f):
        # the current mark: not a "former"/"old" one, a logo before a
        # wordmark, then the plainest name ("Big Ten Conference logo.svg"
        # before "... logo (2012).svg")
        low = str(f).lower()
        old = any(w in low for w in _OLD_WORDS)
        return (old, 0 if "logo" in low else 1, len(low))

    if title:
        tw = [w for w in _words(title) if w not in _TITLE_FILLER]
        full = " ".join(_words(title))
        abbr = "".join(w[0] for w in _words(title) if w not in ("the", "of", "and"))
        named = []
        for f in picks:
            fw = _words(f)
            text = " ".join(fw)
            if full in text or (tw and all(w in fw for w in tw)) or (len(abbr) > 1 and abbr in fw):
                named.append(f)
        if named:
            return sorted(named, key=rank)[0]
        return None          # a lone unnamed file is as often a member's logo
    return sorted(picks, key=rank)[0]


_INFOBOX_LOGO = re.compile(r"^\s*\|\s*(?:logo|image)\s*=\s*(?:\[\[)?(?:File:|Image:)?([^|\]\n<{]+)",
                           re.I | re.M)


def infoboxLogo(wikitext):
    """The file the article's infobox shows as its logo, or None. Pure.

    ★ THE INFOBOX IS THE ARTICLE'S OWN ANSWER (owner, 2026-11-02: Big Ten got
      no logo, ASUN got the Open Access padlock). File names are a guess --
      Big Ten has a "former logo", a "(2012)" one and the current one, ASUN's
      is not named a logo at all -- but `| logo = ...` is the one it uses."""
    for m in _INFOBOX_LOGO.finditer(wikitext or ""):
        f = m.group(1).strip()
        low = f.lower()
        if not f or not re.search(r"\.(svg|png|gif|jpe?g|webp)$", low):
            continue
        if any(w in low for w in ("map", "location", "locator", "flag")):
            continue
        if any(w in low for w in _WIKI_ICONS):
            continue
        return "File:" + f
    return None


def _lead(title, pause):
    """The infobox logo file of one article, or None."""
    try:
        got = _get({"action": "parse", "page": title, "prop": "wikitext",
                    "section": 0, "redirects": 1})
    except Exception:                                  # noqa: BLE001
        return None
    time.sleep(pause)
    return infoboxLogo((got.get("parse") or {}).get("wikitext") or "")


def wikiLogo(name, pause=1.0):
    """(article title, logo url, why) for a conference name: url None and
    why set when there is none to take."""
    found = _get({"action": "query", "list": "search", "srlimit": 5,
                  "srsearch": f"{name} conference"})
    time.sleep(pause)
    titles = [h["title"] for h in (found.get("query") or {}).get("search", [])
              if looksLikeConference(h.get("title"))]
    if not titles:
        return None, None, "no conference article"
    page = _get({"action": "query", "titles": "|".join(titles[:5]),
                 "prop": "extracts|images", "imlimit": 50,
                 "exintro": 1, "explaintext": 1, "exchars": 600})
    time.sleep(pause)
    by_title = {p.get("title"): p for p in (page.get("query") or {}).get("pages") or []}
    for t in titles[:5]:
        p = by_title.get(t) or {}
        if not matches(name, t, p.get("extract") or ""):
            continue
        f = _lead(t, pause) or logoFile([i.get("title") for i in p.get("images") or []], t)
        if not f:
            return t, None, "the article has no logo file"
        info = _get({"action": "query", "titles": f, "prop": "imageinfo",
                     "iiprop": "url", "iiurlwidth": 512})
        time.sleep(pause)
        pages = (info.get("query") or {}).get("pages") or []
        ii = ((pages[0].get("imageinfo") or [{}])[0]) if pages else {}
        url = ii.get("thumburl") or ii.get("url")
        return (t, url, None) if url else (t, None, "no image url")
    return None, None, f"no article matches (closest: {titles[0]})"


def collegeConferences(cur, limit=None):
    """The college conferences on record, most schools first -- only the
    ones the Conferences tab lists (unit_board.validKeys: mostly colleges),
    so a high school league filed as a conference is not looked up."""
    import unit_board as UB
    cur.execute("""SELECT conference, count(DISTINCT school) FROM school_unit
                   WHERE is_college AND NULLIF(btrim(conference), '') IS NOT NULL
                   GROUP BY 1 ORDER BY 2 DESC, 1""")
    rows = [r[0] for r in cur.fetchall()]
    valid = UB.validKeys(cur, "conference", True)
    rows = [r for r in rows if UB.normKey(r) in valid]
    return rows[:limit] if limit else rows


# ★ A DIVISION'S MARK IS ITS GOVERNING BODY'S (owner, 2026-11-02: "what about
#   regions ... or divisions?"). "NCAA DIII" has no logo of its own; the NCAA's
#   is the one a reader knows. Mapped by the division's leading word.
DIVISION_ORGS = {
    "NCAA": "National Collegiate Athletic Association",
    "NAIA": "National Association of Intercollegiate Athletics",
    "NJCAA": "National Junior College Athletic Association",
    "NCCAA": "National Christian College Athletic Association",
    "USCAA": "United States Collegiate Athletic Association",
    "CCCAA": "California Community College Athletic Association",
    "NWAC": "Northwest Athletic Conference",
}


def orgFor(division):
    """The governing body's article for a division value, or None. Pure."""
    head = str(division or "").strip().upper().split(" ")[0] if division else ""
    return DIVISION_ORGS.get(head)


def articleLogo(title, pause=1.0):
    """The logo url of one named article, or None."""
    page = _get({"action": "query", "titles": title, "prop": "images", "imlimit": 50})
    time.sleep(pause)
    pages = (page.get("query") or {}).get("pages") or []
    f = _lead(title, pause) or (
        logoFile([i.get("title") for i in (pages[0].get("images") or [])], title) if pages else None)
    if not f:
        return None
    info = _get({"action": "query", "titles": f, "prop": "imageinfo",
                 "iiprop": "url", "iiurlwidth": 512})
    time.sleep(pause)
    pages = (info.get("query") or {}).get("pages") or []
    ii = ((pages[0].get("imageinfo") or [{}])[0]) if pages else {}
    return ii.get("thumburl") or ii.get("url")


def haveLogo(cur, key):
    cur.execute("SELECT 1 FROM school_logo WHERE school = %s AND path IS NOT NULL LIMIT 1", (key,))
    return cur.fetchone() is not None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", nargs=2, metavar=("KIND", "NAME"),
                    help="pin this unit's logo to --url (conference, league, section ...)")
    ap.add_argument("--url", help="the image for --set (or 'none' to hide it)")
    ap.add_argument("--state", default="", help="a high school unit's state")
    ap.add_argument("--auto", action="store_true",
                    help="look up every college conference on Wikipedia")
    ap.add_argument("--divisions", action="store_true",
                    help="give each college division its governing body's logo "
                         "(NCAA, NAIA, NJCAA ...)")
    ap.add_argument("--redo", action="store_true", help="--auto: ask again where one is stored")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if not a.set and not a.auto and not a.divisions:
        ap.error("--set KIND NAME --url URL, --auto, or --divisions")

    import scrape_school_logos as S
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        # ⚠ THE TABLE SET-UP IS ITS OWN SHORT TRANSACTION (owner, 2026-11-02:
        #   "All logos on the site are gone"). ensureTable's ALTER takes an
        #   ACCESS EXCLUSIVE lock; held for the whole run it blocked every
        #   crest read, and a site restart cached an empty map. Commit it now,
        #   and commit each logo as it is pinned.
        cur.execute("SET lock_timeout = '10s'")
        S.ensureTable(cur, S.DDL)
        S.ensureLevelKey(cur)
        conn.commit()

        def pin(label, key, url):
            try:
                S.setLogo(cur, manners, key, "", url, level="", write=True, shape=UNIT_SHAPE)
                conn.commit()
            except Exception as exc:              # noqa: BLE001 -- one bad image, not the run
                conn.rollback()
                print(f"  {label:<34} not stored ({type(exc).__name__}: {exc})")
        manners = S.Manners(rate=1.0)
        if a.set:
            if not a.url:
                raise SystemExit("--set needs --url")
            kind, name = a.set
            S.setLogo(cur, manners, unitKey(kind, name), (a.state or "").upper(), a.url,
                      level="", write=a.write, shape=UNIT_SHAPE)
        elif a.divisions:
            cur.execute("""SELECT DISTINCT division FROM school_unit
                           WHERE is_college AND NULLIF(btrim(division), '') IS NOT NULL""")
            divs = sorted(r[0] for r in cur.fetchall())
            logos = {}
            for d in divs:
                org = orgFor(d)
                if not org:
                    print(f"  {d:<20} -- no governing body known")
                    continue
                if org not in logos:
                    logos[org] = articleLogo(org)
                url = logos[org]
                if not url:
                    print(f"  {d:<20} -- {org}: no logo file")
                    continue
                key = unitKey("division", d)
                if not a.redo and haveLogo(cur, key):
                    continue
                print(f"  {d:<20} -> {org}")
                if a.write:
                    pin(d, key, url)
        else:
            todo = collegeConferences(cur, a.limit)
            print(f"[unit logos] {len(todo)} college conferences on record")
            for name in todo:
                key = unitKey("conference", name)
                if not a.redo and haveLogo(cur, key):
                    continue
                try:
                    title, url, why = wikiLogo(name)
                except Exception as exc:                  # noqa: BLE001
                    print(f"  {name:<34} lookup failed ({type(exc).__name__})")
                    continue
                if not url:
                    print(f"  {name:<34} -- {title or 'nothing'}: {why}")
                    continue
                print(f"  {name:<34} -> {title}")
                if a.write:
                    pin(name, key, url)
        if a.write:
            conn.commit()
            print("[unit logos] written; restart the site to show them")
        else:
            conn.rollback()
            print("[unit logos] dry run -- --write pins them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
