# Project: xc-predictor
# File:    sources/registry.py
# Purpose: Every timing company we know of (docs/timing_companies.csv, the
#          research file), as sources the scraper can walk: which parser
#          each needs, whether it is independent of athletic.net and
#          MileSplit, and whether it is switched on.
#
#     python -m sources.registry              # the summary: kinds, states
#     python -m sources.registry --enabled    # the sources a run would walk
#
# KINDS (from the research file's platform column, first match wins)
#   blocked       terms forbid bots (PTTiming and its white labels): never
#   meetpro       MeetPro HTML (results.tfmeetpro.com, or a timer's own copy);
#                 DirectAthletics, i.e. FloSports since 2023
#   hytek         Hy-Tek Meet Manager reports on the timer's own site
#                 (HTML, <pre>, PDF)
#   custom        an own-site system needing its own adapter (Finished
#                 Results, GSE, SportTrax, Flash Results ...)
#   arrow         Arrow Live Results (arrowliveresults.com, Agee Race
#                 Timing's platform; not athletic.net or FloSports): one
#                 adapter covers every timer on it, its index names the timer
#   raceroster    Race Roster: one JSON call is a whole meet as Hy-Tek text
#   athleticlive  posts to AthleticLIVE -- athletic.net's own live results
#   milesplit     posts to MileSplit / MileSplit Live (FloSports)
#   road          RunSignup, ChronoTrack, RaceResult: road-race platforms
#   dead          the domain is gone or repurposed
#   unknown       nothing confirmed
#
# ★ INDEPENDENT = a kind that is the timer's OWN publication (hytek,
#   custom): results that do not pass through athletic.net or FloSports. ENABLED = independent AND its parser exists today (meetpro,
#   hytek); custom sources switch on as their adapters land
#   (ADAPTERS below).

import csv
import os
import re
from collections import Counter
from dataclasses import dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(_ROOT, "docs", "timing_companies.csv")

ADAPTERS = {"meetpro", "hytek"}             # parsers that exist (sources/*.py)
# ⚠ MEETPRO IS NOT INDEPENDENT (platform pass, 2026-10-23): results.tfmeetpro
#   .com is MeetPro's results host, MeetPro is DirectAthletics', and FloSports
#   (MileSplit) bought DirectAthletics in June 2023 -- the owner of TFRRS too,
#   which we already scrape. Still enabled (its parser exists, like TFRRS);
#   just not counted as independent of athletic.net and FloSports.
INDEPENDENT = {"hytek", "custom", "arrow", "raceroster"}
FLOSPORTS = {"meetpro", "milesplit"}

# terms that forbid bots and scrapers: never fetched, whatever else they post
BLOCKED_NAMES = re.compile(r"pttiming|primetime timing|leone timing", re.I)
# ★ THE HOSTS NOTHING FETCHES (owner, 2026-10-23, PLAN-MULTISOURCE.md "What
#   counts as off limits"): terms that forbid bots (PTTiming and its white
#   labels, Leone, TimerHub), and results a robots.txt bars for every
#   crawler (ItsYourRace's results, eccathletics.org's results pages).
#   sources.timers refuses a url on one of these before any request.
OFF_LIMITS_HOSTS = re.compile(r"(^|\.)(pttiming\.com|primetimetiming\.com|leonetiming\.com|"
                              r"timerhub\.com|itsyourrace\.com|eccathletics\.org)$", re.I)


def offLimits(url):
    """True for a url on a host nothing may fetch."""
    import urllib.parse
    host = urllib.parse.urlsplit(url or "").netloc.lower().split(":")[0]
    return bool(OFF_LIMITS_HOSTS.search(host))

US_STATES = set("""AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA
MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV
WI WY""".split())


@dataclass
class Source:
    slug: str
    name: str
    states: list
    website: str
    results_url: str
    sample_url: str
    kind: str
    platform: str           # the research file's text, for reading
    notes: str = ""
    kinds: list = field(default_factory=list)   # every kind the platform names

    @property
    def independent(self):
        # ★ A TIMER'S OWN COPY OF MEETPRO PAGES (timers pass, 2026-10-23:
        #   Snake River's self-hosted archive) is its own publication, not
        #   FloSports': independent when no FloSports host is named
        if self.kind == "meetpro":
            urls = " ".join((self.website, self.results_url, self.sample_url)).lower()
            return "own site" in self.platform.lower() and not re.search(r"tfmeetpro\.com|rtspt\.com", urls)
        return self.kind in INDEPENDENT

    @property
    def enabled(self):
        return self.kind in ADAPTERS


def slugOf(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def statesOf(text):
    """'CA (LA/Ventura/Orange); AZ' -> ['AZ', 'CA']. Two-letter codes only."""
    def codes(t):
        return {s for s in re.findall(r"\b([A-Z]{2})\b", t or "") if s in US_STATES}
    # ! WHAT IS IN PARENTHESES IS DETAIL: "CA (LA/Ventura/Orange)" is Los
    #   Angeles, not Louisiana. Read it only when nothing outside names a state.
    outside = codes(re.sub(r"\([^)]*\)", " ", text or ""))
    return sorted(outside or codes(text))


def kindsOf(platform, name="", urls="", notes=""):
    """Every kind a platform description names, best (most useful) first."""
    p = (platform or "").lower()
    # ★ WHAT A LATER PASS FOUND LEADS (2026-10-23): "<new> || before <date>:
    #   <old>" -- the old text is kept for reading, not for the kind
    p = p.split("|| before ")[0]
    u = (urls or "").lower()
    kinds = []
    # "... own site unconfirmed" (the MileSplit timer directories, 580 rows)
    # says nothing is known about an own site: drop the phrase before matching
    p = re.sub(r"own[- ]site (is )?(unconfirmed|unknown|not found)", "", p)
    # the own-site check (2026-10-23) wrote what it did NOT find in the same
    # words: "MileSplit only (no own site found)", "Road only on own site ...;
    # HS XC/TF only via MileSplit", "(own site is a contact-form landing page)"
    if p.startswith("milesplit only"):
        return ["milesplit"]
    if re.match(r"road(/multisport)? only", p):
        return ["road", "milesplit"] if "milesplit" in p else ["road"]
    p = re.sub(r"no (working )?own[- ]site found|own[- ]site is a [^);]*|"
               r"own[- ]site (\([^)]*\) )?links out[^);]*", "", p)
    # a timer's own RunSignup page is RunSignup's (road), and TimerHub's
    # terms forbid collection: neither is an own site to scrape
    p = re.sub(r"own runsignup[^;+)]*|timerhub[^;]*", "", p)
    # ★ OFF LIMITS (owner, 2026-10-23: "if it doesn't outright say no results
    #   scraping it is fine"): terms that forbid bots, robots.txt barring the
    #   results for every crawler, or a bot challenge -- what a pass marks
    #   "off limits" in its notes
    if BLOCKED_NAMES.search(name or "") or "pttiming" in p or "off limits" in (notes or "").lower() \
            or "off limits" in p:
        return ["blocked"]
    if "arrowliveresults" in p or "arrow live" in p or "arrowliveresults" in u:
        kinds.append("arrow")
    if "race roster" in p or "raceroster.com" in u:
        kinds.append("raceroster")
    if "meetpro" in p or "tfmeetpro.com" in u:
        kinds.append("meetpro")
    if re.search(r"hy-?tek", p) and re.search(r"own site|own-site|posted on (its|their) own|pdf \(own", p):
        kinds.append("hytek")
    elif re.search(r"hy-?tek", p) and not re.search(r"athleticlive|milesplit", p):
        kinds.append("hytek")
    # "own" alone counts too (timers pass: "OWN ColdFusion results DB", "Own
    # WordPress posts", "Own yearly event index pages"); the phrases saying
    # there is NO own site were dropped above
    if re.search(r"custom|\bown\b|sporttrax|flash results|runnercard|"
                 r"trackscoreboard|static html", p) \
            and not ({"meetpro", "hytek", "arrow", "raceroster"} & set(kinds)):
        kinds.append("custom")
    if "athleticlive" in p or ".anet.live" in u or "athletic.live" in u:
        kinds.append("athleticlive")
    if "milesplit" in p:
        kinds.append("milesplit")
    if re.search(r"runsignup|chronotrack|raceresult|race result", p):
        kinds.append("road")
    if re.search(r"\bdead\b|parked|repurposed|now unrelated|spam site", p + " " + (name or "").lower()):
        kinds.append("dead")
    return kinds or ["unknown"]


def load(path=CSV_PATH):
    """The research file as Sources, one per row (names made unique)."""
    out, seen = [], Counter()
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            slug = slugOf(name)
            seen[slug] += 1
            if seen[slug] > 1:
                slug = f"{slug}-{seen[slug]}"
            urls = " ".join((r.get("website") or "", r.get("results_url") or "",
                             r.get("sample_meet_url") or ""))
            kinds = kindsOf(r.get("platform"), name, urls, r.get("notes"))
            if "dead" in kinds:
                kinds = ["dead"]
            out.append(Source(slug=slug, name=name, states=statesOf(r.get("states")),
                              website=(r.get("website") or "").strip(),
                              results_url=(r.get("results_url") or "").strip(),
                              sample_url=(r.get("sample_meet_url") or "").strip(),
                              kind=kinds[0], kinds=kinds,
                              platform=(r.get("platform") or "").strip(),
                              notes=(r.get("notes") or "").strip()))
    return out


def summary(sources):
    """Counts for reading: by kind, independent, enabled, and per state."""
    by_kind = Counter(s.kind for s in sources)
    indep = [s for s in sources if s.independent]
    per_state = {st: [s.name for s in indep if st in s.states] for st in sorted(US_STATES)}
    return {"total": len(sources), "by_kind": dict(by_kind.most_common()),
            "independent": len(indep), "enabled": sum(s.enabled for s in sources),
            "independent_by_state": per_state,
            "states_without_independent": [st for st, v in per_state.items() if not v]}


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    sources = load()
    if "--enabled" in argv:
        for s in sources:
            if s.enabled:
                print(f"{s.slug:<40} {s.kind:<8} {','.join(s.states):<12} {s.results_url or s.website}")
        return
    sm = summary(sources)
    print(f"{sm['total']:,} sources; independent {sm['independent']}; enabled now {sm['enabled']}")
    for k, n in sm["by_kind"].items():
        print(f"  {k:<13} {n:>5}")
    print("\nindependent sources per state:")
    for st, names in sm["independent_by_state"].items():
        if names:
            print(f"  {st}  {len(names):>3}  {', '.join(names)[:110]}")
    print(f"\nno independent source: {' '.join(sm['states_without_independent'])}")


if __name__ == "__main__":
    main()
