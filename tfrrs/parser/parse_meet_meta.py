# Project: xc-predictor
# File:    tfrrs/parser/parse_xc_meta.py
# Purpose: Parse the meet-level METADATA from a TFRRS XC meet page — the name,
#          date, venue, and state that every result row needs to know which meet
#          it belongs to. The engine needs venue (for course difficulty) and the
#          model needs date/distance, so this is not optional trimming.

import re
from bs4 import BeautifulSoup

# ------------------------------------------------------------------ #
# WHERE THE METADATA LIVES  (verified against real HTML)
# ------------------------------------------------------------------ #
#
#   Meet name : <h3 class="panel-title">NCAA Division III Cross Country ...</h3>
#   Date      : <div class="panel-heading-normal-text inline-block">November 22, 2025</div>
#   Venue +   : <div class="panel-heading-normal-text inline-block ">
#   address       Roger Milliken Center
#                 00 University Way
#                 Spartansburg, SC 29303
#             </div>
#
# Two honest limits we design around rather than guess past:
#
#   * NO GPS on the page. athletic.net gave Location.GoogleData; TFRRS does not
#     (at least not here). So gps_lat/gps_long come back None and get filled
#     later by a geocoding backfill from (venue_name, state). We don't invent
#     coordinates.
#
#   * The address block is loosely formatted free text. We extract only what is
#     RELIABLE — the venue name (first line) and the state (the 2-letter code
#     before a zip) — and keep the whole raw block so nothing is lost. We do NOT
#     try to confidently split street vs city; that's brittle and unneeded.
# ------------------------------------------------------------------ #


# parseXCMeetMeta
# Purpose: Pull the meet-level metadata out of a full XC meet page.
# Arguments:
#           html: the full page HTML as one string.
# Output:   a dict of meet fields: meet_name, date (YYYY-MM-DD or None),
#           venue_name, state, location_raw, host, plus gps_lat/gps_long (None,
#           for the later backfill). Any field that can't be found is None.
def parseXCMeetMeta(html):
    soup = BeautifulSoup(html, "lxml")

    meet_name = _meetName(soup)
    date_start, date_end = _meetDate(soup)
    location  = _locationBlock(soup)   # returns (venue_name, city, state, raw)
    officials = _officials(soup)
    host      = officials.get("host")            # replaces _hostName(soup)
    director  = officials.get("director")

    return {
        "meet_name":   meet_name,
        # date_start is the canonical per-result date (== date_end for a single-
        # day meet). date_end is kept so a multi-day meet's span isn't lost;
        # TFRRS gives no finer per-event date than this range.
        "date":        date_start,
        "date_end":    date_end,
        "venue_name":  location[0],
        "city":        location[1],
        "state":       location[2],
        "location_raw": location[3],
        "host":         host,
        "director":        director,
        "timing":         officials.get("timing"),
        "referee":        officials.get("referee"),
        "is_championship": _isChampionship(director, meet_name),  # <-- handy flag
        # No coordinates on TFRRS pages — filled later by a geocoding backfill
        # from (venue, city, state). City materially improves geocode hit rate.
        "gps_lat":     None,
        "gps_long":    None,
    }


# ------------------------------------------------------------------ #
# FIELD HELPERS
# ------------------------------------------------------------------ #


# _meetName
# Purpose: Read the meet name from the panel-title heading.
# Arguments:
#           soup: the parsed page tree.
# Output:   the meet-name string, or None if the title isn't found.
def _meetName(soup):
    title = soup.find("h3", class_="panel-title")
    if title is None:
        return None
    text = " ".join(title.get_text().split())
    return text or None


# _meetDate
# Purpose: Find the meet date and normalise it, returning BOTH a start and end
#          (equal for a single-day meet). TFRRS shows a range for multi-day meets
#          and gives no finer per-event date, so the range is all we get.
# Arguments:
#           soup: the parsed page tree.
# Output:   (start_iso, end_iso), or (None, None) if no parseable date is present.
def _meetDate(soup):
    # The date sits in a panel-heading-normal-text div. There can be several such
    # divs (date AND the address share the class), so we scan them and take the
    # first whose text parses as a real date (single or range).
    for div in soup.find_all("div", class_="panel-heading-normal-text"):
        text = " ".join(div.get_text().split())
        parsed = _toIsoDateRange(text)
        if parsed is not None:
            return parsed
    return None, None


# _locationBlock
# Purpose: From the venue/address div, pull the venue name (first line), the
#          state (2-letter code before a zip), and keep the whole raw block.
# Arguments:
#           soup: the parsed page tree.
# Output:   (venue_name, state, location_raw). Any part may be None if absent.
#
# THE CORE PROBLEM: the date AND the address are wrapped in <div>s with the
# SAME class ("panel-heading-normal-text"):
#       <div class="panel-heading-normal-text">November 22, 2025</div>
#       <div class="panel-heading-normal-text">Roger Milliken Center ... SC 29303</div>
# so class alone can't tell them apart. We distinguish them by CONTENT instead
# of by tag/class — the filter-then-pick pattern below.
def _locationBlock(soup):
    # MOVE 1 — gather every same-classed div, but throw out the DATE one.
    # find_all (not find) returns ALL such divs — date div AND address div. For
    # each, we ask "does its text parse as a date?" by running it through the
    # SAME date parser used elsewhere. If it parses as a date, it's the date div,
    # so we skip it (continue). What survives is everything that ISN'T a date —
    # i.e. address candidates. (Reusing the date parser as an exclusion filter.)
    candidates = []
    for div in soup.find_all("div", class_="panel-heading-normal-text"):
        text_raw = div.get_text()
        if _toIsoDateRange(" ".join(text_raw.split())) is not None:
            continue  # that's the date div, not the address
        candidates.append(text_raw)

    # Guard: no non-date divs found -> return three Nones (venue/state/raw) so
    # the caller gets a clean "nothing here" instead of a crash.
    if not candidates:
        return None, None, None

    # MOVE 2 — pick the LONGEST candidate. key=len makes max() compare by
    # character count, not alphabetically. The address is a big block (venue +
    # street + city/state/zip, often multi-line); any other stray non-date div
    # (a "|" separator, a short label) is short. So "longest non-date div" is
    # reliably the address. This is a HEURISTIC, not a guarantee — see the
    # weakness note below.
    raw = max(candidates, key=len)

    # MOVE 3 — pull the three pieces out of the chosen block.
    # WEAKNESS: these two extractions are XC-SHAPED. They assume the XC address
    # Location blocks come in two observed shapes, and the variation does NOT
    # reliably track sport (it may be per-meet or per-host — unverified, so we
    # don't assume): (a) a multi-line address whose first line is the venue and
    # whose state sits before a 5-digit zip ("... Spartansburg, SC 29303"); and
    # (b) a single line "<venue> - <city>, <ST>" with no zip ("Hayward Field -
    # Eugene, OR"). The helpers below handle BOTH shapes:
    #   _venueName        strips a " - city, ST" tail if present, else first line
    #   _cityFromAddress  the token(s) right before ", ST" (works on both shapes)
    #   _stateFromAddress zip-anchored ST, else the ST after the last comma
    # location_raw is always kept, so a None on any structured field is never
    # data loss — geocoding falls back to the raw string.
    venue_name = _venueName(raw)
    city       = _cityFromAddress(raw)
    state      = _stateFromAddress(raw)
    location_raw = " ".join(raw.split())   # collapsed, for storage
    return venue_name, city, state, location_raw


# _hostName
# Purpose: Read the "HOST: <name>" span, if present (minor, but cheap to keep).
# Arguments:
#           soup: the parsed page tree.
# Output:   the host name string, or None.
def _hostName(soup):
    for span in soup.find_all("span", class_="panel-heading-text"):
        text = " ".join(span.get_text().split())
        if text.upper().startswith("HOST:"):
            # Everything after "HOST:" trimmed.
            return text.split(":", 1)[1].strip() or None
    return None


# ------------------------------------------------------------------ #
# SMALL STRING HELPERS
# ------------------------------------------------------------------ #

# Map of full month names to their number, for date normalisation.
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


# _toIsoDateRange
# Purpose: Convert a TFRRS display date — single OR multi-day — into a
#          (start_iso, end_iso) pair of "YYYY-MM-DD" strings. TFRRS gives a
#          DATE RANGE for multi-day meets ("June 5- 8, 2024") and only the
#          meet-level range — it never tags individual events with their day —
#          so the range is the finest date granularity available. For a single
#          day meet start == end.
# Arguments:
#           text: a candidate string, e.g. "November 22, 2025" (single) or
#                 "June 5- 8, 2024" (same-month range) or
#                 "May 30 - June 1, 2025" (cross-month range).
# Output:   (start_iso, end_iso), or None if the text holds no parseable date.
def _toIsoDateRange(text):
    # Try the forms most-specific-first. A cross-month range and a same-month
    # range and a single day are three different shapes; we test each.

    # --- cross-month range: "May 30 - June 1, 2025" -----------------------
    # month1 day1 - month2 day2, year. The year belongs to BOTH ends.
    #
    # Pattern, piece by piece:
    #   ([A-Za-z]+)   group 1: month1 — one or more letters ("May")
    #   \s+           one or more spaces
    #   (\d{1,2})     group 2: day1 — 1 or 2 digits ("30")
    #   \s*-\s*       a dash with optional spaces on either side (" - ")
    #   ([A-Za-z]+)   group 3: month2 — one or more letters ("June")
    #   \s+           one or more spaces
    #   (\d{1,2})     group 4: day2 — 1 or 2 digits ("1")
    #   ,\s*          a comma, then optional spaces
    #   (\d{4})       group 5: year — exactly 4 digits ("2025")
    cross = re.search(
        r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
        text,
    )
    if cross:
        m1 = _MONTHS.get(cross.group(1).lower())
        m2 = _MONTHS.get(cross.group(3).lower())
        if m1 and m2:
            year = int(cross.group(5))
            start = _iso(year, m1, int(cross.group(2)))
            end   = _iso(year, m2, int(cross.group(4)))
            return start, end

    # --- same-month range: "June 5- 8, 2024" ------------------------------
    # month day1 - day2, year. One month, two days.
    #
    # Pattern, piece by piece:
    #   ([A-Za-z]+)   group 1: month — one or more letters ("June")
    #   \s+           one or more spaces
    #   (\d{1,2})     group 2: day1 — 1 or 2 digits ("5")
    #   \s*-\s*       a dash with optional spaces either side. This is what
    #                 absorbs TFRRS's odd "5- 8" spacing (dash hugging the first
    #                 day, space before the second) — \s* on both sides matches
    #                 zero-or-more spaces, so "5- 8", "5 - 8", "5-8" all parse.
    #   (\d{1,2})     group 3: day2 — 1 or 2 digits ("8")
    #   ,\s*          a comma, then optional spaces
    #   (\d{4})       group 4: year — exactly 4 digits ("2024")
    same = re.search(
        r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*(\d{1,2}),\s*(\d{4})",
        text,
    )
    if same:
        month = _MONTHS.get(same.group(1).lower())
        if month:
            year  = int(same.group(4))
            start = _iso(year, month, int(same.group(2)))
            end   = _iso(year, month, int(same.group(3)))
            return start, end

    # --- single day: "November 22, 2025" ----------------------------------
    # month day, year — the common single-day case.
    #
    # Pattern, piece by piece:
    #   ([A-Za-z]+)   group 1: month — one or more letters ("November")
    #   \s+           one or more spaces
    #   (\d{1,2})     group 2: day — 1 or 2 digits ("22")
    #   ,\s*          a comma, then optional spaces
    #   (\d{4})       group 3: year — exactly 4 digits ("2025")
    #
    # NOTE on ordering: this single-day pattern is tried LAST, and that order
    # matters. The range patterns above are stricter (they require the dash), so
    # a range string like "June 5- 8, 2024" only matches them — but a range
    # ALSO contains a "June 5 ... 2024"-ish shape, so if we tried single-day
    # first it could wrongly grab "June 5" and miss the range. Most-specific-
    # first prevents that.
    single = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if single:
        month = _MONTHS.get(single.group(1).lower())
        if month:
            year = int(single.group(3))
            iso  = _iso(year, month, int(single.group(2)))
            return iso, iso   # start == end for a single-day meet

    return None


# _iso
# Purpose: Build a zero-padded "YYYY-MM-DD" string from integer parts.
# Arguments:
#           year, month, day: the integer date parts.
# Output:   e.g. (2024, 6, 5) -> "2024-06-05".
def _iso(year, month, day):
    return f"{year:04d}-{month:02d}-{day:02d}"


# _firstNonEmptyLine
# Purpose: Return the first non-blank line of a multi-line block — the venue name
#          sits on the first line of the address block.
# Arguments:
#           raw: the raw multi-line text of the address div.
# Output:   the first non-empty trimmed line, or None.
def _firstNonEmptyLine(raw):
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


# _venueName
# Purpose: Extract a clean venue name from BOTH page formats. TF puts the whole
#          location on one line as "<venue> - <city>, <ST>" (e.g. "Southwestern
#          U. - Georgetown, TX"); we want just the venue, so we cut at the " - "
#          that separates venue from city. XC has a multi-line address whose
#          first line IS the venue and has no such separator, so we fall back to
#          _firstNonEmptyLine there.
# Arguments:
#           raw: the raw text of the location div.
# Output:   the venue name string, or None.
def _venueName(raw):
    first = _firstNonEmptyLine(raw)
    if first is None:
        return None
    # TF separator: split on the FIRST " - " so a city like "Winston-Salem"
    # (hyphen, no surrounding spaces) is never mistaken for the separator. The
    # separator on TF pages is always spaced (" - "), which the hyphen in a
    # place name is not.
    if " - " in first:
        return first.split(" - ", 1)[0].strip()
    return first


# _stateFromAddress
# Purpose: Pull the 2-letter US state code from an address. Handles BOTH page
#          formats: XC's multi-line address with a zip ("Spartansburg, SC 29303")
#          and TF's single-line venue ("... - Georgetown, TX", no zip).
# Arguments:
#           raw: the raw address/venue text.
# Output:   the 2-letter state code, or None if neither pattern matches.
def _stateFromAddress(raw):
    # 1) XC format: two capitals, optional space, then a 5-digit zip. Anchoring
    #    on the zip is the most reliable signal, so try it first.
    match = re.search(r"\b([A-Z]{2})\s+\d{5}\b", raw)
    if match:
        return match.group(1)

    # 2) TF format: no zip — the state is the 2-letter code after the LAST comma,
    #    e.g. "Southwestern U. - Georgetown, TX". We take the last comma so a
    #    venue name containing a comma can't be mistaken for the city/state part.
    #    \s* tolerates trailing whitespace/newlines; $ anchors it to the end.
    match = re.search(r",\s*([A-Z]{2})\s*$", raw.strip())
    return match.group(1) if match else None


# _cityFromAddress
# Purpose: Pull the CITY out of an address — the word(s) immediately before a
#          ", ST" tail. CONSERVATIVE BY DESIGN: only trusts the clean single-line
#          "<...>City, ST" shape with NO zip (the TF format, e.g. "Georgetown,
#          TX"). On the multi-line XC address (which collapses to "<street stuff>
#          City, SC 29303"), the word(s) before ", ST" include street fragments,
#          so we DON'T guess — we return None and let geocoding use venue+state+
#          location_raw instead. A clean None beats a dirty "University Way
#          Spartansburg". The presence of a zip is the tell that it's the messy
#          multi-line shape.
# Arguments:
#           raw: the raw address/venue text.
# Output:   the city string (clean format only), or None.
def _cityFromAddress(raw):
    text = raw.strip()

    # If there's a zip, this is the messy multi-line address — don't trust a city
    # parse here (it would sweep up the street). Bail to None.
    if re.search(r"\b\d{5}\b", text):
        return None

    # Clean format: city = word(s) (letters, spaces, dots, hyphens — covers "San
    # Marcos", "Winston-Salem", "St. Louis") then ", ST" at end of string.
    match = re.search(r"([A-Za-z .\-]+),\s*[A-Z]{2}\s*$", text)
    if not match:
        return None
    city = match.group(1).strip()
    # If a " - " venue/city separator is still attached, keep only the city side.
    if " - " in city:
        city = city.split(" - ")[-1].strip()
    return city or None

# _officials
# Purpose: Read every "LABEL: value" official from the panel-heading-text spans
#          into a dict keyed by lowercased label. Generalises _hostName to the
#          whole block in one pass.
# Arguments:
#           soup: the parsed page tree.
# Output:   dict like {"host": "University of Missouri", "director": "NCAA",
#           "timing": "Cody Branch", "referee": "Referee"} — only the labels the
#           page actually has. Empty dict if the block is absent (e.g. TF pages).
def _officials(soup):
    out = {}
    for span in soup.find_all("span", class_="panel-heading-text"):
        text = " ".join(span.get_text().split())
        # Each official is "LABEL: value"; skip spans without that shape (e.g.
        # the TF "COMPILED RESULTS:" header has no value after the colon).
        if ":" not in text:
            continue
        label, _, value = text.partition(":")
        label = label.strip().lower()
        value = value.strip()
        if label and value:
            out[label] = value
    return out

# _isChampionship
# Purpose: A cheap boolean the model can use: is this a championship-caliber meet?
#          Two signals — DIRECTOR is a governing body (NCAA/NAIA/etc.), or the
#          meet name says "Championship(s)". Either is enough to flag it.
# Arguments:
#           director:  the DIRECTOR official value (may be None).
#           meet_name: the meet's name (may be None).
# Output:   True if it looks like a championship meet, else False.
def _isChampionship(director, meet_name):
    # Governing-body director -> championship/qualifier run by that body.
    if director:
        d = director.upper()
        for body in ("NCAA", "NAIA", "NJCAA", "USATF", "USTFCCCA"):
            if body in d:
                return True
    # Fallback: the name itself announces it.
    if meet_name and "championship" in meet_name.lower():
        return True
    return False