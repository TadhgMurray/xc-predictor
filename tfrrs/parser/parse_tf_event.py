# Project: xc-predictor
# File:    tfrrs/parser/parse_tf_event.py
# Purpose: Classify a TF event by reading the table's RESULT-COLUMN HEADER, not
#          by guessing from the event name. The header literally declares what
#          kind of result the table holds: "TIME" (running), "MARK" (field), or
#          "POINTS" (combined / decathlon-heptathlon). This is the stable signal
#          — it can't miss an unfamiliar event the way a keyword list does. The
#          event NAME is kept only as raw text (for display + a later distance
#          backfill) and to read gender; distance is NOT computed here.
 
# ------------------------------------------------------------------ #
# WHY HEADER-DRIVEN, NOT KEYWORD-DRIVEN
# ------------------------------------------------------------------ #
#
# Earlier this file enumerated field-event names, combined-event names, and a
# distance table. All three were open-ended lists we could never finish, and
# each gap was a silent misclassification. The table itself already states the
# answer: its result column is headed TIME, MARK, or POINTS. Read that one cell
# and you know the kind, regardless of how obscure the event's name is. So:
#   - header contains "TIME"   -> running  (row parser reads a time)
#   - header contains "MARK"   -> field    (row parser reads a mark, time NULL)
#   - header contains "POINTS" -> combined (row parser reads a points total)
#   - none of the above        -> unknown  (flag; store raw; resolve later)
#
# NOTE on decoys: every result column (real AND decoy) carries the SAME header
# text, so a running event shows several "TIME" headers. That's fine — we only
# need to know WHICH WORD appears, not how many. The CSS decoder (parse_tf)
# still picks the real column; this function only decides the column's KIND.
# ------------------------------------------------------------------ #
 
# The header words that name each result kind, mapped to our result_kind label.
# Matched case-insensitively against the header cells' text.
_HEADER_KIND = (
    ("TIME",   "running"),
    ("MARK",   "field"),
    ("POINTS", "combined"),
)

# classifyTFEventTable
# Purpose: Decide an event's result_kind and gender from its result table.
# Arguments:
#           table:      the BeautifulSoup <table> Tag for this event's rolled-up
#                       results (the one whose <thead> we read).
#           title_text: the event's <h3> title text (e.g. "Men's Long Jump"),
#                       used for gender and kept raw by the caller for display +
#                       the distance backfill.
# Output:   a dict:
#             result_kind -- "running" / "field" / "combined" / "unknown"
#             gender      -- "M" / "F" / None
#           (No distance — that is a separate backfill off the raw event name.)
def classifyTFEventTable(table, title_text):
    result_kind = _kindFromHeader(table)
    gender      = _genderFromTitle(title_text.lower())
    return {"result_kind": result_kind, "gender": gender}


# ------------------------------------------------------------------ #
# HELPERS
# ------------------------------------------------------------------ #
 
 
# _kindFromHeader
# Purpose: Read the result kind from the table's header row by looking for the
#          words TIME / MARK / POINTS among the <th> cells.
# Arguments:
#           table: the event's <table> Tag.
# Output:   "running" / "field" / "combined", or "unknown" if no result header
#           word is found (e.g. an unexpected layout — flag, don't guess).
def _kindFromHeader(table):

    # Collects uppercased text of every header cell as one string
    # to substring search.
    header_text = _headerText(table)

    # Looks at each possible result type and finds if it's in
    # the header. If it is, returns the type(kind), otherwise
    # returns "unknown".
    for word, kind in _HEADER_KIND:
        if word in header_text:
            return kind
    return "unknown"
 
 
# _headerText
# Purpose: Collect the uppercased text of every header cell in the table's first
#          header row, as one string we can substring-search.
# Arguments:
#           table: the event's <table> Tag.
# Output:   an uppercased string of the header cells joined by spaces (empty
#           string if the table has no <thead>/<th>).
def _headerText(table):

    # Finds every header cell in the table's first header row.
    head = table.find("thead")
    if head is None:
        return ""
    
    # Join all <th> texts; uppercase so "Time"/"time"/"TIME" all match "TIME".
    cells = head.find_all("th")
    return " ".join(c.get_text() for c in cells).upper()
 
 
# _genderFromTitle
# Purpose: Read gender from a TF title. "Men"/"Men's"/"Boys" -> M,
#          "Women"/"Women's"/"Girls" -> F. Check women FIRST because the string
#          "women" contains "men".
# Arguments:
#           lowered: the already-lowercased title text.
# Output:   "M", "F", or None.
def _genderFromTitle(lowered):
    if "women" in lowered or "girls" in lowered:
        return "F"
    if "men" in lowered or "boys" in lowered:
        return "M"
    return None