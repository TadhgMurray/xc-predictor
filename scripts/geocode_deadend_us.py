#!/usr/bin/env python3
# ======================================================================
# geocode_deadend_us.py   (companion to fill_deadend_venues.py)
# ----------------------------------------------------------------------
# THE IDEA
#
# fill_deadend_venues.py placed the overseas rows from a curated table.
# It abstained on the ~110 US rows because a US town can't be a hardcoded
# constant — but it CAN be geocoded, IF we hand the geocoder a real query
# string. The original geocoder failed on these only because the venue
# record was nameless; the town was hiding in the MEET NAME the whole time
# ("Burnsville Boys JV" -> the town is Burnsville).
#
# So this script does two jobs:
#   1. MINE a town guess out of the meet names (offline, testable).
#   2. GEOCODE "Guess, ST, USA" through the SAME cascade your other
#      scripts use — Census one-line, then Nominatim — cached + paced.
#
#   US abstained row (state, meet_names)
#         |
#         v
#   placeGuess()  ->  "Burnsville"        (mine the town; offline)
#         |
#         v
#   _buildQuery() ->  "Burnsville, MN, USA"
#         |
#         v
#   _geocode():  Census one-line  --miss-->  Nominatim  --miss--> abstain
#         |            (cached by query string; Nominatim paced >=1s)
#         v
#   write filled coords + a REVIEW sidecar (guess/query/source/coords)
#
# Run --dry-run FIRST (no network): it prints every guess+query so you can
# eyeball the mining before spending Nominatim's ~1/s budget. Then run for
# real. Bad guesses simply fail to geocode and stay null — never wrong.
# ======================================================================

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from dataclasses import dataclass
from typing import Optional


# US / spelled-out state codes we will attempt (everything else is left to
# the curated table or accepted as a genuine null).
US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","MA","MD","MI","MN","MO","MS","NC","ND","NE","NH","NJ","NM",
    "NY","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VA","VT","WA","WI",
    "WV","WY",
}
# A few cells spell the state out; map them back to a code for the query.
STATE_ALIASES = {"Texas": "TX"}


# ======================================================================
# CHUNK 1 — TOWN MINING (all offline; this is the part we can test here)
# ----------------------------------------------------------------------
# Meet names follow a rough grammar: <PLACE> <role words> [# / year].
# We peel the role words and keep the leading place. Two escape hatches:
#   - a governing-body prefix (WIAA/MHSAA/AAU...) means the place is NOT at
#     the front; try the "- Town" tail instead (e.g. "WIAA D2 - Portage").
#   - if mining yields junk, we still emit it, flagged low-confidence, and
#     let the geocoder be the final judge.
# ======================================================================

# Words that are never a place — they END the leading place run.
_ROLE_WORDS = {
    "boys", "girls", "coed", "mixed", "jv", "varsity", "hs", "ms", "middle",
    "school", "elementary", "invitational", "invite", "relays", "relay",
    "classic", "meet", "quad", "quadrangular", "dual", "duals", "tri",
    "championship", "championships", "champs", "conference", "conf",
    "district", "districts", "sectional", "sectionals", "regional",
    "regionals", "qualifier", "open", "throwdown", "early", "bird", "annual",
    "vs", "vs.", "and", "at", "the", "day", "final", "results",
}

# Leading acronyms that GOVERN a meet but aren't the host town. Their
# presence sends us to the "- Town" tail.
_GOVERNING_PREFIXES = {
    "wiaa", "miaa", "nchsaa", "mhsaa", "ihsaa", "nsaa", "aau", "taaf",
    "ciml", "sac", "poi", "wcac", "nic", "paisaa", "pcl", "mais", "mcsaa",
    "cyo", "iona", "nycal",
}


def _firstSample(meetNames):
    """
    Purpose : the first meet-name sample (the others are usually variants).
    Arguments: meetNames -- the raw 'sample_meet_names' cell (str).
    Output  : str, the text before the first '|', trimmed of a stray quote.
    """
    head = meetNames.split("|", 1)[0]
    return head.strip().lstrip('"').strip()


def _stripTail(text):
    """
    Purpose : drop trailing noise a geocoder chokes on (# numbers, years,
              ordinals) so 'Garnet Valley MS #2' -> 'Garnet Valley MS'.
    Arguments: text -- one meet-name sample (str).
    Output  : str with trailing tokens removed while they look like
              #N / 4-digit years / ordinals ('14th') / bare numbers.
    """
    tokens = text.split()
    while tokens:
        last = tokens[-1].lower().strip(".,#")
        isHashNum = tokens[-1].startswith("#")
        isYear = last.isdigit() and len(last) == 4
        isOrdinal = last[:-2].isdigit() and last[-2:] in ("st", "nd", "rd", "th")
        isBareNum = last.isdigit()
        if isHashNum or isYear or isOrdinal or isBareNum:
            tokens.pop()                          # peel one noise token
        else:
            break
    return " ".join(tokens)


def _stripLeadNoise(text):
    """
    Purpose : peel LEADING noise (years, ordinals, class codes like '2A')
              so '2A State Qualifier - Eddyville' loses its '2A' and
              '2005 AAU ...' loses its '2005'. The front-side mirror of
              _stripTail.
    Arguments: text -- one meet-name sample (str).
    Output  : str with leading noise tokens removed.
    """
    tokens = text.split()
    while tokens:
        low = tokens[0].lower().strip(".,#")
        isYear = low.isdigit() and len(low) == 4
        isOrdinal = low[:-2].isdigit() and low[-2:] in ("st", "nd", "rd", "th")
        isBareNum = low.isdigit()
        # class code = digits then only division letters, e.g. '2a','1aaa','5a'
        isClass = (len(low) >= 2 and low[0].isdigit()
                   and low.rstrip("abcd").isdigit() and not low[-1].isdigit())
        if isYear or isOrdinal or isBareNum or isClass:
            tokens.pop(0)
        else:
            break
    return " ".join(tokens)


def _leadingPlace(text):
    """
    Purpose : the run of leading words up to the first role word.
    Arguments: text -- cleaned meet-name sample (str).
    Output  : str. 'Burnsville Boys JV' -> 'Burnsville'; 'Garnet Valley MS'
              -> 'Garnet Valley'. Empty if the very first word is a role word.
    """
    kept = []
    for word in text.split():
        if word.lower().strip(".,") in _ROLE_WORDS:
            break                                 # hit a role word -> stop
        kept.append(word)
    return " ".join(kept)


def _afterDash(text):
    """
    Purpose : the segment after the last dash — often the HOST town in
              governing-body meets ('WIAA D2 Sectional - Portage').
    Arguments: text -- cleaned meet-name sample (str).
    Output  : str after the final ' - ' / '-', or '' if there is no dash.
    """
    for sep in (" - ", "- ", "-"):
        if sep in text:
            return text.rsplit(sep, 1)[1].strip()
    return ""


def _looksGoverned(text):
    """Purpose: True if the sample starts with a governing-body acronym."""
    first = text.split()[0].lower().strip(".,") if text.split() else ""
    return first in _GOVERNING_PREFIXES


def placeGuess(meetNames):
    """
    Purpose : best single town guess from a row's meet names.
    Arguments: meetNames -- raw 'sample_meet_names' cell (str).
    Output  : str (possibly ''). Governed meets prefer the '- Town' tail;
              everyone else takes the leading place run. This is a GUESS —
              the geocoder validates it.
    """
    sample = _stripLeadNoise(_stripTail(_firstSample(meetNames)))
    if _looksGoverned(sample):                    # WIAA/AAU/... -> town is later
        rest = " ".join(sample.split()[1:])       # drop the governing acronym
        return _afterDash(sample) or _leadingPlace(rest)
    return _leadingPlace(sample) or _afterDash(sample)


def _lowConfidence(guess):
    """
    Purpose : flag guesses a human should double-check.
    Arguments: guess -- the mined town string (str).
    Output  : bool. Too short, all-caps acronym, or contains slashes ->
              likely not a real town name.
    """
    if len(guess) < 3:
        return True
    if guess.isupper():                           # 'GFS', 'ESS' -> acronym
        return True
    return "/" in guess


# ======================================================================
# CHUNK 2 — QUERY BUILDING
# ======================================================================

def _stateCode(stateRaw):
    """Purpose: normalize a state cell to a 2-letter code. Output: str."""
    s = stateRaw.strip()
    return STATE_ALIASES.get(s, s)


def _buildQuery(guess, stateRaw):
    """
    Purpose : assemble the geocoder query string.
    Arguments: guess -- mined town (str); stateRaw -- state cell (str).
    Output  : 'Town, ST, USA' (str), or '' if there is no usable guess.
    """
    if not guess:
        return ""
    return f"{guess}, {_stateCode(stateRaw)}, USA"


# ======================================================================
# CHUNK 3 — THE GEOCODE CASCADE (network; runs on your box)
# ----------------------------------------------------------------------
# Same two providers your other scripts use. Every lookup is cached by
# query string, so a re-run costs zero network. Nominatim is paced to
# respect its ~1 request/second policy and sent a real User-Agent.
# ======================================================================

_CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_UA = "xc-predictor-deadend-geocoder/1.0"

def _httpJson(url, params, headers=None, timeout=20):
    """
    Purpose : GET a URL with query params and parse JSON.
    Arguments: url (str); params (dict); headers (dict|None); timeout (s).
    Output  : parsed JSON (dict/list) or None on any error (caller falls
              through to the next provider — mirrors your 'those fall
              through' behavior).
    """
    try:
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{qs}", headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _censusOneline(query):
    """
    Purpose : geocode one query via the Census one-line endpoint.
    Arguments: query -- 'Town, ST, USA' (str).
    Output  : (lat, lon) floats, or None. Census returns lon/lat under
              matches[0].coordinates as {x: lon, y: lat}.
    """
    data = _httpJson(_CENSUS_URL, {
        "address": query, "benchmark": "Public_AR_Current", "format": "json",
    })
    try:
        m = data["result"]["addressMatches"][0]["coordinates"]
        return float(m["y"]), float(m["x"])       # y=lat, x=lon
    except Exception:
        return None


def _nominatim(query):
    """
    Purpose : geocode one query via Nominatim (fallback).
    Arguments: query -- 'Town, ST, USA' (str).
    Output  : (lat, lon) floats, or None. REQUIRES a User-Agent per policy.
    """
    data = _httpJson(_NOMINATIM_URL,
                     {"q": query, "format": "json", "limit": 1},
                     headers={"User-Agent": _UA})
    try:
        return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        return None


def _geocode(query, cache, sleep):
    """
    Purpose : cached Census->Nominatim cascade for one query.
    Arguments:
      query -- 'Town, ST, USA' (str).
      cache -- dict {query: [lat, lon] | None}; mutated in place.
      sleep -- seconds to wait after a Nominatim call (float, pacing).
    Output  : (lat, lon) or None. Records the result (hit OR miss) in the
              cache so re-runs never re-hit the network for the same query.
    """
    if query in cache:                            # already tried this exact string
        hit = cache[query]
        return tuple(hit) if hit else None
    result = _censusOneline(query)
    if result is None:
        result = _nominatim(query)
        time.sleep(sleep)                         # pace ONLY the Nominatim path
    cache[query] = list(result) if result else None
    return result


def _loadCache(path):
    """Purpose: read the JSON cache (or {} if absent). Output: dict."""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _saveCache(path, cache):
    """Purpose: persist the cache. Output: None."""
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=0)


# ======================================================================
# CHUNK 4 — TSV I/O (reads the FILLED file; only touches US nulls)
# ======================================================================

@dataclass
class Row:
    cols: list                                    # the 7 raw columns, verbatim
    guess: str = ""
    query: str = ""
    source: str = ""                              # census / nominatim / miss
    low: bool = False


def _isUsNull(cols):
    """
    Purpose : True if this row is a US row still missing coordinates.
    Arguments: cols -- the 7 split columns (list[str]).
    Output  : bool. Column 3 is state; column 5 is gps_lat (blank = null).
    """
    state = _stateCode(cols[3])
    return cols[5] == "" and state in US_STATES


def readRows(path):
    """
    Purpose : load the filled file, preserving comments and column order.
    Arguments: path -- input .tsv (the output of fill_deadend_venues.py).
    Output  : (rows, comments): rows is list[Row]; comments is list[str].
    """
    rows, comments = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.lstrip().startswith("#"):
                comments.append(line)
                continue
            if not line.strip():
                continue
            cols = line.split("\t")
            cols += [""] * (7 - len(cols))        # pad to 7
            rows.append(Row(cols[:7]))
    return rows, comments


def writeRows(path, rows, comments):
    """
    Purpose : write the updated file (same 7 columns, drop-in for apply).
    Arguments: path (str); rows (list[Row]); comments (list[str]).
    Output  : None.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for c in comments:
            fh.write(c + "\n")
        for r in rows:
            fh.write("\t".join(r.cols) + "\n")


def writeReview(path, attempted):
    """
    Purpose : eyes-first sidecar of every US row we tried.
    Arguments: path (str); attempted (list[Row] we ran the cascade on).
    Output  : None. Columns let you scan guesses and sources at a glance.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("location_id\tstate\tguess\tquery\tsource\tlow_conf\tlat\tlon\n")
        for r in attempted:
            fh.write("\t".join([
                r.cols[0], r.cols[3], r.guess, r.query, r.source,
                "1" if r.low else "", r.cols[5], r.cols[6],
            ]) + "\n")


# ======================================================================
# CHUNK 5 — DRIVER (mine -> [geocode] -> write); main stays a conductor
# ======================================================================

def _mineAll(rows):
    """
    Purpose : attach a guess+query to every US-null row.
    Arguments: rows -- list[Row].
    Output  : list[Row], the subset we will attempt (US nulls with a query).
    """
    attempted = []
    for r in rows:
        if not _isUsNull(r.cols):
            continue
        r.guess = placeGuess(r.cols[4])
        r.query = _buildQuery(r.guess, r.cols[3])
        r.low = _lowConfidence(r.guess)
        if r.query:
            attempted.append(r)
    return attempted


def _geocodeAll(attempted, cachePath, sleep):
    """
    Purpose : run the network cascade over the mined rows and fill coords.
    Arguments: attempted (list[Row]); cachePath (str|None); sleep (float).
    Output  : None (mutates rows: fills cols[5]/cols[6] + source).
    """
    cache = _loadCache(cachePath)
    try:
        for i, r in enumerate(attempted, 1):
            coords = _geocode(r.query, cache, sleep)
            if coords:
                r.cols[5], r.cols[6] = f"{coords[0]:.3f}", f"{coords[1]:.3f}"
                r.source = "census/nominatim"
            else:
                r.source = "miss"
            if i % 20 == 0:                       # progress ping
                print(f"  ...{i}/{len(attempted)}", file=sys.stderr)
    finally:
        _saveCache(cachePath, cache)              # save even if interrupted


def _report(attempted, dryRun):
    """Purpose: stderr summary. Output: None."""
    if dryRun:
        hits = sum(1 for r in attempted if not r.low)
        print(f"dry-run: {len(attempted)} US rows mined  "
              f"({hits} confident, {len(attempted) - hits} low-confidence)",
              file=sys.stderr)
        return
    placed = sum(1 for r in attempted if r.source == "census/nominatim")
    print(f"geocoded: {placed}/{len(attempted)} US rows placed "
          f"({len(attempted) - placed} still null)", file=sys.stderr)


def _buildParser():
    """Purpose: CLI flags. Output: ArgumentParser."""
    p = argparse.ArgumentParser(
        description="Geocode the US dead-end rows by mining a town from meet names.")
    p.add_argument("--in", dest="inPath", required=True,
                   help="filled TSV from fill_deadend_venues.py")
    p.add_argument("--out", dest="outPath", required=True,
                   help="updated TSV (same 7 columns)")
    p.add_argument("--review-out", dest="reviewPath", default=None,
                   help="sidecar: guess/query/source per US row")
    p.add_argument("--cache", dest="cachePath", default="deadend_geocode_cache.json",
                   help="JSON query cache (re-runs are free)")
    p.add_argument("--sleep", dest="sleep", type=float, default=1.1,
                   help="seconds to wait after each Nominatim call")
    p.add_argument("--dry-run", dest="dryRun", action="store_true",
                   help="mine + print guesses only; no network")
    return p


def main():
    """Purpose: wire read -> mine -> [geocode] -> write. Output: None."""
    args = _buildParser().parse_args()
    rows, comments = readRows(args.inPath)
    attempted = _mineAll(rows)
    if args.dryRun:
        for r in attempted:                       # show the mining for review
            flag = "  [low]" if r.low else ""
            print(f"  {r.cols[0]:>7}  {r.query}{flag}", file=sys.stderr)
    else:
        _geocodeAll(attempted, args.cachePath, args.sleep)
        writeRows(args.outPath, rows, comments)
    if args.reviewPath:
        writeReview(args.reviewPath, attempted)
    _report(attempted, args.dryRun)


if __name__ == "__main__":
    main()