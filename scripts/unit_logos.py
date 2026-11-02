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
import time
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


from unit_board import logoKey as unitKey                      # noqa: E402  the one definition


def looksLikeConference(title):
    """Is this article title a conference's? Pure."""
    t = (title or "").lower()
    return any(w in t for w in CONF_WORDS)


def _get(params):
    q = urllib.parse.urlencode(dict(params, format="json", formatversion=2))
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def wikiLogo(name, pause=1.0):
    """(article title, image url) for a conference name, or (None, why)."""
    found = _get({"action": "query", "list": "search", "srlimit": 5,
                  "srsearch": f"{name} conference"})
    time.sleep(pause)
    titles = [h["title"] for h in (found.get("query") or {}).get("search", [])
              if looksLikeConference(h.get("title"))]
    if not titles:
        return None, "no conference article"
    page = _get({"action": "query", "titles": "|".join(titles[:5]),
                 "prop": "pageimages|extracts", "piprop": "thumbnail", "pithumbsize": 512,
                 "exintro": 1, "explaintext": 1, "exchars": 600})
    time.sleep(pause)
    by_title = {p.get("title"): p for p in (page.get("query") or {}).get("pages") or []}
    for t in titles[:5]:
        p = by_title.get(t) or {}
        if matches(name, t, p.get("extract") or ""):
            thumb = (p.get("thumbnail") or {}).get("source")
            return (t, thumb) if thumb else (t, None)
    return None, f"no article matches (closest: {titles[0]})"


def collegeConferences(cur, limit=None):
    """The college conferences on record, most schools first."""
    cur.execute("""SELECT conference, count(DISTINCT school) FROM school_unit
                   WHERE is_college AND NULLIF(btrim(conference), '') IS NOT NULL
                   GROUP BY 1 ORDER BY 2 DESC, 1""")
    rows = [r[0] for r in cur.fetchall()]
    return rows[:limit] if limit else rows


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
    ap.add_argument("--redo", action="store_true", help="--auto: ask again where one is stored")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if not a.set and not a.auto:
        ap.error("--set KIND NAME --url URL, or --auto")

    import scrape_school_logos as S
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        S.ensureTable(cur, S.DDL)
        S.ensureLevelKey(cur)
        manners = S.Manners(rate=1.0)
        if a.set:
            if not a.url:
                raise SystemExit("--set needs --url")
            kind, name = a.set
            S.setLogo(cur, manners, unitKey(kind, name), (a.state or "").upper(), a.url,
                      level="", write=a.write)
        else:
            todo = collegeConferences(cur, a.limit)
            print(f"[unit logos] {len(todo)} college conferences on record")
            for name in todo:
                key = unitKey("conference", name)
                if not a.redo and haveLogo(cur, key):
                    continue
                try:
                    title, url = wikiLogo(name)
                except Exception as exc:                  # noqa: BLE001
                    print(f"  {name:<34} lookup failed ({type(exc).__name__})")
                    continue
                if not url:
                    print(f"  {name:<34} -- {title or 'nothing'}: no logo found")
                    continue
                print(f"  {name:<34} -> {title}")
                if a.write:
                    S.setLogo(cur, manners, key, "", url, level="", write=True)
        if a.write:
            conn.commit()
            print("[unit logos] written; restart the site to show them")
        else:
            conn.rollback()
            print("[unit logos] dry run -- --write pins them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
