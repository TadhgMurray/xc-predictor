#!/usr/bin/env python3
"""
build_school_websites.py -- WHERE each school lives on the web (305,
docs/IMAGES-PLAN.md, step 1). Nothing here fetches a logo; it fills the
address book the logo scraper then works through.

    python scripts/build_school_websites.py --csv ccd_directory.csv --check
    python scripts/build_school_websites.py --wikidata --check
    python scripts/build_school_websites.py --csv ccd.csv --wikidata --write

    school_website (school, state, url, direct_logo, source, matched)

★ TWO SOURCES, ONE TABLE.

  --csv    any directory export with a NAME, a STATE and a WEBSITE column,
           under any of the spellings in _COLS: the NCES Common Core of
           Data school or agency directory, the Private School Survey, or
           a list the owner assembles by hand. Public domain, no scraping,
           no terms to breach.

           ⚠ THE CCD SCHOOL FILE DOES NOT ALWAYS CARRY A SCHOOL WEBSITE.
             Some years only the AGENCY (district) file has one. That is
             still worth loading: a district URL yields the district's
             crest, and the shared-logo rule in the scraper then throws it
             out for every school that shares it -- which is the right
             answer, reached automatically, rather than forty schools
             wearing one badge.

  --wikidata  US universities and secondary schools, one SPARQL query per
           state: the official website (P856) and, when the item has one,
           the logo file itself (P154 / P18). A direct logo needs no
           homepage visit at all, so colleges mostly cost nothing.

★ A NAME IS MATCHED OR IT IS REVIEWED, NEVER GUESSED. Our school strings
  are free text from two scrapers and carry no ids, so a source row is
  accepted only when its normalised name and state match exactly one of
  ours. Colleges get a second key through the college directory's
  normalisation, which knows the feeds' short names ("BYU"). Everything
  ambiguous or unmatched goes to a TSV for the owner to read -- a wrong
  crest on a school page is worse than a blank one.

★ NORMALISATION IS DELIBERATELY SHY. "High School" and its spellings come
  off, punctuation and case come off, and that is all: "university" and
  "college" STAY, because dropping them turns Boston College and Boston
  University into one school. The college key (build_college_directory's
  normName) is the one place they come off, and it is only tried for a
  name the college directory already knows.
"""
import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

UA = ("racecast school directory (https://racecast.co; "
      "contact: tadhg.a.murray@gmail.com)")

STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "PR",
]

# the column spellings the public directories use, in the order they are
# tried; the first header that matches wins
_COLS = {
    "name":  ("sch_name", "school_name", "schnam", "name", "school",
              "institution", "lea_name", "agency_name", "pinst"),
    "state": ("st", "state", "stabbr", "lstate", "state_code", "pstabb"),
    "site":  ("website", "web_site", "url", "web", "homepage", "site",
              "web_address"),
}

# ---------------------------------------------------------------- names

_HS_TAIL = re.compile(
    r"\b(?:senior\s+high\s+school|junior\s+senior\s+high\s+school|"
    r"jr\s+sr\s+high\s+school|high\s+school|senior\s+high|sr\s+high|"
    r"secondary\s+school|hs|h\s+s)\b")
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")
# only the three that are unambiguous in a school name; "no "/"so " would
# eat a word ("Rancho Solano") for a gain nobody needs
_ABBREV = (("st ", "saint "), ("mt ", "mount "), ("ft ", "fort "))


def normSchool(name):
    """The shy key: case, punctuation, "High School" and a leading "the"
    off, everything else kept."""
    s = html.unescape(name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = _PUNCT.sub(" ", s)
    s = _SPACE.sub(" ", s).strip()
    for a, b in _ABBREV:
        if s.startswith(a):
            s = b + s[len(a):]
        s = s.replace(" " + a, " " + b)
    s = _HS_TAIL.sub(" ", s)
    s = re.sub(r"^the\b", " ", s)
    return _SPACE.sub(" ", s).strip()


def collegeKey(name):
    """The college directory's own normalisation, which knows the feeds'
    short names. None when it is unavailable (it lives in a sibling
    script that imports nothing heavy, but be safe)."""
    try:
        from build_college_directory import normName
    except Exception:                                     # noqa: BLE001
        return None
    return normName(name) or None


# ---------------------------------------------------------------- ours

def ourSchools(cur):
    """[(school, state)] -- every school string the site shows, one row per
    identity cluster, because two schools wearing one name are two schools
    and want two crests. Falls back to the raw ranking rows when
    school_identity has not been built."""
    cur.execute("SELECT to_regclass('public.school_identity')")
    row = cur.fetchone()
    have = (row[0] if not isinstance(row, dict) else row.get("to_regclass"))
    if have is not None:
        cur.execute("""
            SELECT school, state FROM school_identity
            WHERE  n_athletes >= 3 ORDER BY school, state
        """)
    else:
        print("  school_identity is missing; falling back to ranking_results")
        cur.execute("""
            SELECT school, max(state) AS state FROM ranking_results
            WHERE  school IS NOT NULL GROUP BY school ORDER BY school
        """)
    out = []
    for r in cur.fetchall():
        school = r["school"] if isinstance(r, dict) else r[0]
        state = (r["state"] if isinstance(r, dict) else r[1]) or ""
        if school:
            out.append((school, (state or "").upper()))
    return out


def collegeNames(cur):
    """The set of college keys we know, so a college string can use the
    permissive key without letting every high school do the same."""
    try:
        cur.execute("SELECT to_regclass('public.college_directory')")
        row = cur.fetchone()
        if (row[0] if not isinstance(row, dict) else row.get("to_regclass")) is None:
            return set()
        cur.execute("SELECT name_norm FROM college_directory")
        return {(r["name_norm"] if isinstance(r, dict) else r[0])
                for r in cur.fetchall()}
    except Exception:                                     # noqa: BLE001
        cur.connection.rollback()
        return set()


def ourLevels(cur, colleges=()):
    """{(school, state): level} -- what each of our schools IS.

    Two witnesses, and the directory wins. school_level (built by
    racecast/build_school_identity.py) is inferred from the athletes' own
    pools, which is why it exists and also why it cannot be the only vote:
    a college whose runners are mis-pooled as high schoolers would ask for
    a high school's website. A name the college directory places is a
    college, full stop.

    Empty when neither table is built -- the matcher then behaves exactly
    as it did before levels existed."""
    out = {}
    try:
        cur.execute("SELECT to_regclass('public.school_level')")
        row = cur.fetchone()
        if (row[0] if not isinstance(row, dict) else row.get("to_regclass")):
            cur.execute("SELECT school, state, level FROM school_level "
                        "WHERE is_primary")
            for r in cur.fetchall():
                school, st, lvl = ((r["school"], r["state"], r["level"])
                                   if isinstance(r, dict) else r)
                if lvl:
                    out[(school, (st or "").upper())] = lvl
    except Exception:                                     # noqa: BLE001
        cur.connection.rollback()
    colleges = set(colleges or ())
    if colleges:
        for (school, st) in list(out):
            key = collegeKey(school)
            if key and key in colleges:
                out[(school, st)] = "college"
    return out


# ------------------------------------------------------------- matching

def buildIndex(rows):
    """{(key, state): [row, ...]} for the source rows, on both keys."""
    idx = {}
    for row in rows:
        st = (row.get("state") or "").upper()
        for key in {normSchool(row.get("name")), collegeKey(row.get("name"))} - {None, ""}:
            idx.setdefault((key, st), []).append(row)
    return idx


def levelOfRows(hits, want):
    """The hits that can belong to a `want`-level school.

    ★ A COLLEGE IS NEVER A HIGH SCHOOL, WHATEVER THE NAME SAYS (owner,
      2026-09-13). Oregon, Arkansas, Amherst, Hope -- a university and a
      high school share the name, and the source rows for both landed
      under one key. The matcher then read whichever the feed happened to
      list first and a college wore a high school's badge.

    ! A ROW WITH NO LEVEL IS KEPT AT EITHER LEVEL. Most directory exports
      do not say what they are, and dropping every unlabelled row to
      enforce a level we are only guessing at would cost far more crests
      than the collision does. The rule only bites when the source
      actually contradicts us -- a row that SAYS college against a high
      school, or the reverse.

    ! AND IT NEVER RETURNS NOTHING. If the level filter empties the list,
      the unfiltered hits stand: no answer at all is worse than one from
      a source that did not label itself the way we hoped."""
    if not want:
        return hits
    kept = [r for r in hits if (r.get("level") or want) == want]
    return kept or hits


def matchSchools(ours, source_rows, colleges=(), levels=None):
    """(matched, review). `matched` is one row per (school, state) we can
    place beyond doubt; `review` is every name that matched nothing, or
    matched more than one source row that disagree about the URL.

    `levels` is {(school, state): level} -- what WE believe each of our
    schools is. Absent, every source row is eligible, as before."""
    idx = buildIndex(source_rows)
    colleges = set(colleges or ())
    levels = levels or {}
    matched, review = [], []
    for school, state in ours:
        want = levels.get((school, state))
        strict = normSchool(school)
        ckey = collegeKey(school)
        # ⚠ THE COLLEGE KEY GOES FIRST FOR A COLLEGE. It used to go second
        #   always, so "Oregon" matched the strict key against a secondary
        #   school and never reached the directory's own normalisation --
        #   the permissive key exists precisely because a college's short
        #   name is not its directory name.
        keys = ([(ckey, state), (strict, state)]
                if want == "college" and ckey and ckey in colleges
                else [(strict, state)]
                     + ([(ckey, state)] if ckey and ckey in colleges else []))
        hits = []
        for k in keys:
            hits = levelOfRows(idx.get(k) or [], want)
            if hits:
                break
        if not hits:
            review.append((school, state, "unmatched", ""))
            continue
        urls = {(r.get("url") or "").strip().lower() for r in hits if r.get("url")}
        logos = {(r.get("direct_logo") or "").strip() for r in hits if r.get("direct_logo")}
        if len(urls) > 1:
            review.append((school, state, "ambiguous",
                           " | ".join(sorted(urls))[:400]))
            continue
        if not urls and not logos:
            review.append((school, state, "no website", hits[0].get("name") or ""))
            continue
        best = hits[0]
        matched.append({"school": school, "state": state,
                        "url": (sorted(urls)[0] if urls else None),
                        "direct_logo": (sorted(logos)[0] if logos else None),
                        "source": best.get("source") or "csv",
                        "matched": best.get("name") or ""})
    return matched, review


# ------------------------------------------------------------------ csv

def readCsv(path, level=None):
    """Rows from any directory export whose headers name a school, a state
    and a website. Raises when a column is missing, naming what it saw.

    `level` is what the FILE is: the CCD and the PSS are K-12 directories,
    so --csv-level hs stops a college taking a high school's website. None
    means "this file does not say", and an unlevelled row still matches
    anything, exactly as before."""
    with io.open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        heads = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
        picked = {}
        for want, spellings in _COLS.items():
            for s in spellings:
                if s in heads:
                    picked[want] = heads[s]
                    break
        missing = [k for k in ("name", "state", "site") if k not in picked]
        if missing:
            raise SystemExit(
                f"{path}: no {', '.join(missing)} column. Headers seen: "
                f"{', '.join(sorted(heads))[:400]}")
        rows = []
        for r in reader:
            url = cleanUrl(r.get(picked["site"]))
            rows.append({"name": (r.get(picked["name"]) or "").strip(),
                         "state": (r.get(picked["state"]) or "").strip().upper()[:2],
                         "url": url, "source": "csv", "level": level})
    return [r for r in rows if r["name"] and r["state"]]


def cleanUrl(raw):
    """A directory's website cell as a URL, or None. The public files hold
    everything from "www.foo.k12.ca.us" to "N/A" to a mailto:."""
    s = (raw or "").strip().strip('"').strip()
    if not s or s.lower() in {"n/a", "na", "none", "null", "-", "no website",
                              "not applicable", "m", "†", "not available"}:
        return None
    if s.lower().startswith("mailto:"):
        return None
    if not re.match(r"^https?://", s, re.I):
        if not re.match(r"^[\w.-]+\.[a-z]{2,}", s, re.I):
            return None
        s = "http://" + s
    try:
        p = urllib.parse.urlsplit(s)
    except ValueError:
        return None
    if not p.netloc or "." not in p.netloc:
        return None
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(),
                                    p.path or "/", "", ""))


# ------------------------------------------------------------- wikidata

WDQS = "https://query.wikidata.org/sparql"

# one state at a time: the whole country in one query times out, and a
# state that fails leaves the other fifty alone
# ★ THE CLASS COMES BACK WITH THE ROW (owner, 2026-09-13: "college Oregon
#   gets hs Oregon's logo"). Q38723 is a higher-education institution and
#   Q159334 a secondary school; the query asked for both and returned
#   neither label, so a college and a high school of one name in one state
#   were two indistinguishable rows and the matcher took whichever came
#   first. Selecting ?class costs nothing and is the whole fix.
_SPARQL = """
SELECT ?itemLabel ?site ?logo ?class WHERE {
  VALUES ?class { wd:Q38723 wd:Q159334 }
  ?item wdt:P31/wdt:P279* ?class .
  ?item wdt:P17 wd:Q30 .
  ?item wdt:P131*/wdt:P300 "US-%(st)s" .
  OPTIONAL { ?item wdt:P154 ?logo }
  OPTIONAL { ?item wdt:P856 ?site }
  FILTER (BOUND(?site) || BOUND(?logo))
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
"""


_WD_LEVEL = {"Q38723": "college", "Q159334": "hs"}


def wikidataRows(states=STATES, pace=2.0, verbose=True):
    """[{name, state, url, direct_logo, source}] from the query service.
    A state that errors or times out is reported and skipped."""
    rows = []
    for st in states:
        q = _SPARQL % {"st": st}
        url = WDQS + "?" + urllib.parse.urlencode({"query": q, "format": "json"})
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/sparql-results+json"})
            with urllib.request.urlopen(req, timeout=120) as fh:
                data = json.loads(fh.read().decode("utf-8"))
        except Exception as exc:                          # noqa: BLE001
            print(f"  wikidata {st}: {type(exc).__name__}: {exc}")
            time.sleep(pace)
            continue
        n = 0
        for b in data.get("results", {}).get("bindings", []):
            name = (b.get("itemLabel", {}).get("value") or "").strip()
            if not name or name.startswith("Q"):
                continue
            rows.append({
                "name": name, "state": st,
                "url": cleanUrl(b.get("site", {}).get("value")),
                "direct_logo": (b.get("logo", {}).get("value") or "").strip() or None,
                "source": "wikidata",
                "level": _WD_LEVEL.get(
                    (b.get("class", {}).get("value") or "").rsplit("/", 1)[-1])})
            n += 1
        if verbose:
            print(f"  wikidata {st}: {n:,} institutions")
        time.sleep(pace)
    return rows


# ---------------------------------------------------------------- write

DDL = """
CREATE TABLE IF NOT EXISTS school_website (
    school      text NOT NULL,
    state       text NOT NULL DEFAULT '',
    url         text,
    direct_logo text,
    source      text,
    matched     text,
    seen        date NOT NULL DEFAULT current_date,
    PRIMARY KEY (school, state))
"""


def store(cur, matched):
    """Upsert, never truncate: a run with one source must not delete what
    the other source found."""
    from scrape_school_logos import ensureTable
    ensureTable(cur, DDL)
    cur.executemany("""
        INSERT INTO school_website (school, state, url, direct_logo, source, matched, seen)
        VALUES (%s, %s, %s, %s, %s, %s, current_date)
        ON CONFLICT (school, state) DO UPDATE
        SET url = COALESCE(EXCLUDED.url, school_website.url),
            direct_logo = COALESCE(EXCLUDED.direct_logo, school_website.direct_logo),
            source = EXCLUDED.source, matched = EXCLUDED.matched,
            seen = current_date
    """, [(m["school"], m["state"], m["url"], m["direct_logo"], m["source"],
           m["matched"]) for m in matched])


def writeReview(path, review):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("school\tstate\twhy\tdetail\n")
        for row in review:
            fh.write("\t".join(str(x or "") for x in row) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", action="append", default=[],
                    help="a directory export (NCES CCD, PSS, or hand-made); repeatable")
    ap.add_argument("--csv-level", default=None, choices=("college", "hs", "ms", "elem"),
                    help="what the --csv files are (the CCD and PSS are hs); "
                         "unset means the file does not say and its rows "
                         "match a school of any level")
    ap.add_argument("--wikidata", action="store_true",
                    help="query the Wikidata service, one request per state")
    ap.add_argument("--states", default="", help="limit --wikidata to these, comma separated")
    ap.add_argument("--write", action="store_true", help="store school_website")
    ap.add_argument("--check", action="store_true", help="parse and report, write nothing")
    ap.add_argument("--review", default="scripts/school_website_unmatched.tsv")
    args = ap.parse_args()
    if not args.csv and not args.wikidata:
        ap.error("nothing to read: pass --csv and/or --wikidata")

    source = []
    for path in args.csv:
        rows = readCsv(path, args.csv_level)
        with_url = sum(1 for r in rows if r["url"])
        print(f"  {path}: {len(rows):,} rows, {with_url:,} with a website")
        source += rows
    if args.wikidata:
        states = [s.strip().upper() for s in args.states.split(",") if s.strip()] or STATES
        source += wikidataRows(states)

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            ours = ourSchools(cur)
            colleges = collegeNames(cur)
            levels = ourLevels(cur, colleges)
            n_col = sum(1 for v in levels.values() if v == "college")
            print(f"  ours: {len(ours):,} schools, {len(colleges):,} known "
                  f"colleges, {len(levels):,} levelled ({n_col:,} college)")
            matched, review = matchSchools(ours, source, colleges, levels)
            direct = sum(1 for m in matched if m["direct_logo"])
            print(f"  matched {len(matched):,} of {len(ours):,} "
                  f"({100.0 * len(matched) / max(1, len(ours)):.1f}%), "
                  f"{direct:,} with a logo file already")
            print(f"  review  {len(review):,} -> {args.review}")
            writeReview(args.review, review)
            if args.check or not args.write:
                if not args.check:
                    print("  (--write to store school_website)")
                for m in matched[:10]:
                    print(f"    {m['school']} ({m['state']}) -> {m['url'] or m['direct_logo']}")
                return
            store(cur, matched)
            conn.commit()
            print(f"  school_website: {len(matched):,} rows written")


if __name__ == "__main__":
    main()
