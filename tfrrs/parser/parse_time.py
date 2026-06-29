# Project: xc-predictor
# File:    tfrrs/parser/parse_time.py
# Purpose: Convert TFRRS display time strings ("16:12.4") into float seconds.
#          Unlike athletic.net (which hands us SortValue, a clean float),
#          TFRRS only ever gives times as human display strings. There is NO
#          numeric field to fall back on, so parsing these correctly is the
#          single most important correctness gate in the TFRRS scraper — a bug
#          here is exactly how athletic.net got its 12-19s corrupt times.
 
# ------------------------------------------------------------------ #
# THE FORMATS WE HAVE ACTUALLY SEEN ON REAL TFRRS PAGES
# ------------------------------------------------------------------ #
#
#   "9.88"     -> seconds only (a sprint)            -> 9.88
#   "4:48.76"  -> minutes:seconds.tenths (a mile)    -> 288.76
#   "16:12.4"  -> minutes:seconds.tenths (XC 5k)     -> 972.4
#   "23:35.0"  -> minutes:seconds.tenths (XC 8k)     -> 1415.0
#   "2:01:09"  -> hours:minutes:seconds (team total) -> 7269.0
#
# The trick the whole parser rests on: the NUMBER OF COLONS tells us the
# format unambiguously. Split on ":" and count the pieces —
#   1 piece  -> seconds only
#   2 pieces -> minutes:seconds
#   3 pieces -> hours:minutes:seconds
# No guessing, no pattern-matching: the piece-count IS the format.
# ------------------------------------------------------------------ #

# parseTimeToSeconds
# Purpose: Convert one TFRRS display time string into a float of seconds.
# Arguments:
#           raw: the time string exactly as scraped from a result cell, e.g.
#                "16:12.4". May carry surrounding whitespace. May be empty or a
#                non-time placeholder ("DNF", "NT", "-") — those are treated as
#                "no time" and return None, rather than crashing the row.
# Output:   a float of seconds (i.e. 972.4), or None when the string is not a real
#           time. We return None rather than guessing a value, because a guessed
#           number (like 0) would be a LIE the engine treats as a real, wrongly
#           fast time — corrupting ratings. None is honest: "no time here."
def parseTimeToSeconds(raw):

    # Strip whitespace so " 16:12.4\n" parses the same as "16:12.4". TFRRS wraps
    # its cell contents in newlines and spaces, so this is not optional.
    cleaned = raw.strip() if raw else ""
 
    # Empty / known non-time markers -> "no time". We do NOT invent a value.
    # _looksLikeTime rejects "DNF", "NT", "-" etc. BEFORE we try to int()/float()
    # them, so a placeholder can't crash the parse.
    if cleaned == "" or not _looksLikeTime(cleaned):
        return None
    
    # The colon-count is the format. Splitting gives us the pieces in order
    # (biggest unit first): ["16", "12.4"] is minutes then seconds.
    parts = cleaned.split(":")
 
    # 1 piece: seconds only, e.g. "9.88". float() handles the decimal directly.
    if len(parts) == 1:
        return float(parts[0])
 
    # 2 pieces: minutes:seconds. Minutes is whole; seconds may carry a decimal.
    # Total = minutes*60 + seconds.
    if len(parts) == 2:
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
 
    # 3 pieces: hours:minutes:seconds, e.g. a team total "2:01:09".
    if len(parts) == 3:
        hours   = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
 
    # 4+ pieces is not a real time we have ever seen. Returning None (rather than
    # guessing) keeps a malformed string from silently becoming a plausible-
    # looking wrong number — same philosophy as the placeholder case above.
    return None

# _looksLikeTime
# Purpose: Cheap guard answering "could this string plausibly be a time?", so
#          parseTimeToSeconds can reject placeholders (DNF, NT, "-") before it
#          tries int()/float() and crashes.
# Arguments:
#           cleaned: an already-stripped candidate string (no surrounding space).
# Output:   True if every character is a digit, a colon, or a dot — the only
#           characters a real time contains. This is intentionally LOOSE: it is
#           a "reject obvious garbage" filter, not a full validator. Anything
#           that slips through still hits the int()/float() calls, which raise on
#           bad input and surface it rather than hiding it.
def _looksLikeTime(cleaned):
    allowed = set("0123456789:.")
    return all(character in allowed for character in cleaned)