"""
seo.py -- the rules every page's <head> follows for search, in one place.

★ WHY A MODULE (SEO pass, 2026-10-10). Most visitors arrive from Google
  typing a runner's name, a meet or a school. _meta.html already carried
  the tags; what it could not carry was RULES: which query arguments make a
  different page, what a page's breadcrumb trail is, how an athlete's
  description is built from real facts, and JSON that stays JSON when a
  name has a quote in it. Templates call these through jinja globals
  (registered by install()) and stay a few lines each.

  canonicalPath(path, args, alt)   the path + only the arguments that select
                                   a different page (see KEEP_ARGS, ?alt=)
  athleteSeo(...)                  title, description, Person, breadcrumbs
  eventLd(...)                     a SportsEvent for a meet or race
  homeLd(origin, desc)             Organization + WebSite (SearchAction)
  crumbsLd(crumbs, origin)         BreadcrumbList, positions contiguous
  ldJson(obj)                      None-free, script-safe JSON for a
                                   <script type="application/ld+json">

! PURE FUNCTIONS. Nothing here touches the database; the routes hand in
  what they already fetched. tests/test_seo.py exercises them with stubs.
"""
import json
import re
from urllib.parse import urlencode

SITE_NAME = "Racecast"
# ★ THE SITE NAME RIDES AT THE END, AFTER A BAR (owner-approved SEO pass,
#   2026-10-10: "Owen Castellano — Jesuit (OR) cross country & track |
#   Racecast"). One constant, so a page title and a test agree.
TITLE_SUFFIX = " | " + SITE_NAME
# Google cuts a description at roughly 155-160 characters; past this the
# trailing clauses are dropped whole rather than cut mid-word.
DESC_MAX = 160

_UNSET = object()

# ★ THE ARGUMENTS THAT MAKE A DIFFERENT PAGE, per path prefix. Everything
#   else -- ?r= (a highlighted row), ?school= (highlighted rows), ?units=,
#   ?scale=, utm_* and the rest -- shows the same page and is stripped, so
#   Google keeps one URL per page. Order is the order emitted.
# ! /school/ is not here: school.html sets its own meta_path (state and
#   level settle the identity there, 2026-09-14), and so do the landing
#   boards and the season tracker.
KEEP_ARGS = (
    ("/meets", ("course",)),
)


def _altArg(args):
    """?alt= as a positive int, or None (0, junk and absence are all the
    bare page)."""
    raw = (args.get("alt") or "").strip() if args is not None else ""
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def canonicalPath(path, args=None, alt=_UNSET):
    """The canonical path (site-relative, with query) for a request.

    ★ ?alt= IS KEPT ONLY WHEN IT SELECTS A DIFFERENT PAGE (2026-10-10). One
      meet_id can hold two real meets (app.meet_sources); the second lives
      at ?alt=N and the sitemap lists it there. _meta.html used to drop
      every argument, so /meet/xc/N?alt=1 told Google it was a copy of
      /meet/xc/N -- a different meet -- and the sitemap URL was discarded.
      `alt` is what the route resolved: the index when the bare URL would
      serve something else, None when it would serve this same page. A
      route that sets nothing falls back to the argument as given, on meet
      and race paths only.
    ! A TRAILING SLASH IS NEVER CANONICAL (app redirects /x/ -> /x), and
      "/" stays "/".
    """
    path = path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    args = args if args is not None else {}
    keep = []
    for prefix, names in KEEP_ARGS:
        if path == prefix or path.startswith(prefix + "/"):
            for n in names:
                v = (args.get(n) or "").strip()
                if v:
                    keep.append((n, v))
    if path.startswith(("/meet/", "/race/")):
        if alt is _UNSET:
            alt = _altArg(args)
        if alt:
            keep.append(("alt", str(int(alt))))
    return path + ("?" + urlencode(keep) if keep else "")


def fullTitle(title):
    """'<title> | Racecast', or the site's own line when a page sets none."""
    if not title:
        return SITE_NAME + " - every result on one comparable scale"
    title = str(title).strip()
    if title.endswith(TITLE_SUFFIX):
        return title
    return title + TITLE_SUFFIX


def fitDescription(parts, limit=DESC_MAX):
    """Join sentences until the next one would pass `limit`. The first part
    always stands (it is the sentence that has to work alone)."""
    out = ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        nxt = (out + " " + p).strip()
        if out and len(nxt) > limit:
            continue
        out = nxt
    return out


# --------------------------------------------------------------------- #
#  JSON-LD
# --------------------------------------------------------------------- #
def _clean(obj):
    """Drop None, empty strings, empty lists and empty dicts, recursively.
    Google's validator flags a null property; a missing one is fine."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = _clean(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(obj, (list, tuple)):
        return [x for x in (_clean(v) for v in obj)
                if not (x is None or x == "" or x == [] or x == {})]
    return obj


def ldJson(obj):
    """JSON for a <script type="application/ld+json"> block.

    ⚠ NEVER HAND-BUILT IN A TEMPLATE (2026-10-10). The breadcrumb block
      used to write "name": "{{ label }}" -- autoescape turned St. Mary's
      into St. Mary&#39;s inside the JSON (which Google reads literally) and
      a name with a double quote broke the block outright. json.dumps, then
      the four characters that can end a script element escaped as \\uXXXX,
      which is still the same JSON string.
    """
    from markupsafe import Markup
    text = json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":"))
    text = (text.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026").replace("'", "\\u0027"))
    return Markup(text)


def crumbsLd(crumbs, origin):
    """BreadcrumbList from [(label, path|None), ...]. Positions are 1-based
    and contiguous; the last crumb (the page itself) carries no item."""
    crumbs = [(l, p) for l, p in (crumbs or ()) if l]
    items = []
    for i, (label, path) in enumerate(crumbs):
        # ! A MIDDLE CRUMB WITH NO URL IS DROPPED: Google requires `item` on
        #   every element but the last, and flags the whole list otherwise
        if not path and i != len(crumbs) - 1:
            continue
        el = {"@type": "ListItem", "position": len(items) + 1,
              "name": str(label)}
        if path:
            el["item"] = origin + path if path.startswith("/") else path
        items.append(el)
    if not items:
        return None
    items[-1].pop("item", None)
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": items}


def defaultCrumbs(path, title):
    """The trail for a page that names none: Racecast > <page>. The home
    page has none (it IS the root)."""
    if not path or path == "/":
        return []
    return [(SITE_NAME, "/"), (title or SITE_NAME, None)]


def homeLd(origin, description):
    """Organization + WebSite with the sitelinks SearchAction. Home only:
    the site is one thing, not one per page."""
    org = {"@context": "https://schema.org", "@type": "Organization",
           "@id": origin + "/#organization",
           "name": SITE_NAME, "url": origin + "/",
           "logo": origin + "/static/icons/icon-512.png"}
    site = {"@context": "https://schema.org", "@type": "WebSite",
            "@id": origin + "/#website",
            "name": SITE_NAME, "url": origin + "/",
            "description": description,
            "publisher": {"@id": origin + "/#organization"},
            "potentialAction": {
                "@type": "SearchAction",
                "target": {"@type": "EntryPoint",
                           "urlTemplate": origin + "/search?q={search_term_string}"},
                "query-input": "required name=search_term_string"}}
    return [org, site]


def _day(value):
    s = str(value or "")[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def _v(x):
    """None for jinja's Undefined (a header without that column)."""
    try:
        from jinja2 import Undefined
        if isinstance(x, Undefined):
            return None
    except ImportError:                                  # pragma: no cover
        pass
    return x


def eventLd(name, url, date=None, place=None, state=None, lat=None,
            lng=None, sport="Running", city=None):
    """A SportsEvent for a meet or a race. Google's Event fields: name,
    startDate and location (a Place with an address) are the ones it
    requires; geo rides along when the caller has coordinates."""
    # a template hands jinja's Undefined for a column the header lacks
    place, state, city, lat, lng, date = (
        _v(place), _v(state), _v(city), _v(lat), _v(lng), _v(date))
    address = None
    if state or city:
        address = {"@type": "PostalAddress", "addressLocality": city,
                   "addressRegion": state, "addressCountry": "US"}
    geo = None
    try:
        if lat is not None and lng is not None:
            geo = {"@type": "GeoCoordinates",
                   "latitude": round(float(lat), 5),
                   "longitude": round(float(lng), 5)}
    except (TypeError, ValueError):
        geo = None
    location = None
    if place or address or geo:
        location = {"@type": "Place", "name": place or state,
                    "address": address, "geo": geo}
    day = _day(date)
    return {"@context": "https://schema.org", "@type": "SportsEvent",
            "name": name, "sport": sport, "url": url,
            "startDate": day, "endDate": day,
            "eventStatus": "https://schema.org/EventScheduled",
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "location": location,
            "organizer": None}


# --------------------------------------------------------------------- #
#  Athletes
# --------------------------------------------------------------------- #
def classOf(grade, season_year, pool):
    """The graduation year for a school-age athlete, or None.

    A class year in the grade column ("2027") is itself. A school grade G in
    the season stored as year Y (the academic year's opening calendar year,
    both sports) graduates in Y + 13 - G -- the rule recruiting.py and
    rankings.gradeKeySql use. College and pro pools: None (eligibility is
    not a class year)."""
    if grade is None:
        return None
    g = str(grade).strip()
    if re.fullmatch(r"(19|20)\d\d", g):
        return int(g)
    level = (pool or "").split("|")[0].split("_")[0].lower()
    if level not in ("hs", "ms") or season_year is None:
        return None
    if not g.isdigit():
        words = {"fr": 9, "so": 10, "jr": 11, "sr": 12}
        n = words.get(g.lower()[:2])
        if n is None:
            return None
    else:
        n = int(g)
    if not 1 <= n <= 12:
        return None
    try:
        return int(season_year) + 13 - n
    except (TypeError, ValueError):
        return None


# The PR a searcher recognises, best first: a cross country 5K, then the
# track distance events high schoolers are known by.
_XC_PR = ("5000m", "5K", "5k", "5 km", "5km")
_TF_PR = ("1600m", "Mile", "3200m", "800m", "1500m", "3000m", "2 Mile", "5000m")


def _pr(alltime):
    """[(label, time)] -- at most one cross country and one track PR."""
    out = []
    for sport, keys, fmt in (("XC", _XC_PR, "{} XC"), ("TF", _TF_PR, "{}")):
        events = ((alltime or {}).get(sport) or {}).get("events") or {}
        for k in keys:
            race = events.get(k)
            if race and race.get("result"):
                label = "5K" if sport == "XC" else k
                out.append((fmt.format(label), str(race["result"]).strip()))
                break
    return out


def _nationRank(rank_line):
    for e in rank_line or ():
        if e.get("label") == "Nation" and e.get("rank") and not e.get("wip"):
            return e["rank"]
    return None


def _stateRank(rank_line):
    for e in rank_line or ():
        lab = e.get("label") or ""
        if re.fullmatch(r"[A-Z]{2}", lab) and e.get("rank") and not e.get("wip"):
            return lab, e["rank"]
    return None


def athleteSeo(athlete, alltime=None, rank_line=None, school_label=None,
               school_href=None, origin="", photo=None):
    """{title, description, ld, crumbs, noindex} for an athlete page.

    ★ REAL FACTS, NOT A TEMPLATE (owner-approved SEO pass, 2026-10-10):
        Owen Castellano — Jesuit (OR) cross country & track | Racecast
        Owen Castellano, Jesuit (OR). Class of 2027. 5K XC PR 15:02.
        Season rating 124.6, #41 nationally, #3 in OR. ...
      Every clause is left out when its fact is missing, and clauses drop
      from the end to stay under DESC_MAX.
    ★ THIN PAGES ARE noindex: no name, or no race at all. They still
      render and are followed; they are not what a search should land on.
    """
    a = athlete or {}
    name = (a.get("name") or "").strip() or "Unnamed athlete"
    pid = a.get("person_id")
    title = name + ((" — " + school_label) if school_label else "") \
        + " cross country & track"
    parts = [name + ((", " + school_label) if school_label else "") + "."]
    cls = a.get("class_of")
    if cls:
        parts.append(f"Class of {cls}.")
    prs = _pr(alltime)
    if prs:
        parts.append(" ".join(f"{lab} PR {t}." for lab, t in prs))
    rating = a.get("rating_hs") or a.get("rating")
    if rating:
        try:
            r = f"Season rating {float(rating):.1f}"
        except (TypeError, ValueError):
            r = None
        if r:
            nat, st = _nationRank(rank_line), _stateRank(rank_line)
            if nat:
                r += f", #{nat} nationally"
            if st:
                r += f", #{st[1]} in {st[0]}"
            parts.append(r + ".")
    parts.append("Every race, PR and season rating on one comparable scale.")
    description = fitDescription(parts)

    url = f"{origin}/athlete/{pid}" if pid is not None else None
    if photo and str(photo).startswith("/"):
        photo = origin + str(photo)          # schema.org wants an absolute URL
    team = None
    if school_label:
        team = {"@type": "SportsTeam", "name": school_label,
                "sport": "Cross country and track",
                "url": (origin + school_href) if school_href else None}
    ld = {"@context": "https://schema.org", "@type": "Person",
          "@id": (url + "#person") if url else None,
          "name": name, "url": url, "image": photo,
          "memberOf": team,
          "affiliation": ({"@type": "EducationalOrganization",
                           "name": school_label} if school_label else None)}
    crumbs = [(SITE_NAME, "/")]
    if school_label and school_href:
        crumbs = [("Schools", "/schools"), (school_label, school_href)]
    crumbs.append((name, None))
    noindex = bool(a.get("unnamed")) or not a.get("n_races")
    return {"title": title, "description": description, "ld": ld,
            "crumbs": crumbs, "noindex": noindex}


# --------------------------------------------------------------------- #
#  Wiring
# --------------------------------------------------------------------- #
def install(app, origin):
    """Register the jinja globals the head templates call."""
    from flask import g, request

    def canonical_path():
        return canonicalPath(request.path, request.args,
                             getattr(g, "canonical_alt", _UNSET))

    app.jinja_env.globals.update(
        canonical_path=canonical_path,
        seo_full_title=fullTitle,
        seo_crumbs_ld=lambda crumbs: crumbsLd(crumbs, origin),
        seo_default_crumbs=defaultCrumbs,
        seo_home_ld=lambda desc: homeLd(origin, desc),
        seo_event_ld=eventLd,
        seo_athlete=lambda athlete, alltime=None, rank_line=None,
        school_label=None, school_href=None, photo=None: athleteSeo(
            athlete, alltime, rank_line, school_label, school_href,
            origin, photo),
    )
    app.jinja_env.filters["ld_json"] = ldJson
