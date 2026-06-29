# Project: xc-predictor
# File:    tfrrs/parser/parse_tf_page.py
# Purpose: Parse a whole TF compiled page (one gender's /m/ or /f/ page) into a
#          flat list of result rows, one per athlete per event. Walks each
#          event's ROLLED-UP table (skipping the redundant per-section/heat
#          tables), classifies it by header (TIME/MARK/POINTS), and decodes the
#          real result out of the decoy columns. Combined events (decathlon /
#          heptathlon) yield BOTH the parent points row AND each sub-event row,
#          tagged so the model can use or ignore the multi-event provenance.
 
from bs4 import BeautifulSoup
from parse_tf import collectHiddenClasses, parseTFRow, _isResultCell
from parse_tf_event import classifyTFEventTable
 
# ------------------------------------------------------------------ #
# HOW A TF PAGE IS LAID OUT  (verified against raw HTML)
# ------------------------------------------------------------------ #
#
# The page body is a flat sequence, repeated per event:
#     <a name="EVENTID"></a>          <- the event's id (anchor)
#     <h3> Event Title </h3>          <- the event name (+ gender)
#     <style> ...decoys... </style>   <- which result columns are hidden
#     <table> ...rolled-up results... <- the OVERALL result for the event
#     (then per-section/heat <style>+<table> repeats — REDUNDANT, skipped)
#
# ROLLED-UP vs SECTION/HEAT — the keep/skip decision:
#   A table's OWN result cells reveal which it is, by class prefix:
#     rolled-up  -> compiled_round_ / sub_round_ / round_   (KEEP)
#     section    -> compiled_heat_  / sub_heat_             (SKIP, duplicates)
#   We trust this machine signal (the class scheme) rather than the heading
#   level, for the same reason we trust the CSS over the visible numbers.
#
# COMBINED EVENTS (decathlon/heptathlon):
#   - The PARENT table (bare round_, header POINTS) -> one combined row/athlete.
#   - Each SUB-EVENT has its own anchor+id and its own rolled-up table
#     (sub_round_, header TIME or MARK) -> a normal running/field row, tagged
#     from_combined=<parent title> so it can be down-weighted/ignored later.
# 
# Tree Example for Soup:
# tr                                    ← the row (a parent node)
# ├── td                                ← cell 1 (child of tr)
# │   └── "1"                           ← text inside it (child of td)
# ├── td                                ← cell 2
# │   └── a  href="/athletes/9403723"   ← a link (child of td)
# │       └── "Koryee Wyatt Jr"         ← text inside the link
# ├── td  class="compiled_round_4_x_0"  ← cell 3 (carries a class attribute)
# │   └── "10.22"                       ← text inside it
# └── td                                ← cell 4
#     └── nobr                          ← a <nobr> (child of td)
#         └── "2.6"                     ← the wind text
# So the mental model: soup turns HTML text into a tree of nested Tag objects; 
# the parser walks that tree — find/find_all to locate nodes, 
# .get() to read attributes, .get_text() to read text. 
# Everything else (the regex, the int parsing, the decoy logic) 
# is plain Python operating on the strings those tree-reads return.
# ------------------------------------------------------------------ #
 
# Result-cell class prefixes that mark a ROLLED-UP (overall) table — the ones we
# keep. (Heat/section tables use the *_heat_ prefixes and are skipped.)
_ROLLUP_PREFIXES = ("compiled_round_", "sub_round_", "round_")
 
# Result-cell class prefixes that mark a SECTION/HEAT table — skipped.
_HEAT_PREFIXES = ("compiled_heat_", "sub_heat_")

# parseTFPage
# Purpose: Parse one TF compiled page into a flat list of result-row dicts.
# Arguments:
#           html:    the raw page HTML (a /m/ or /f/ compiled page).
#           meet_id: the meet id (carried onto every row for the DB).
# Output:   a list of row dicts. Each row is the parseTFRow dict plus event
#           context: event_id, event_name, result_kind, gender, from_combined.
#           Rows that fail to parse are included with ok=False + a note, so
#           nothing is silently dropped.
def parseTFPage(html, meet_id):
    # Parse the raw HTML string into a navigable tree. "lxml" is the parser
    # engine; BeautifulSoup is the API we walk the tree with. After this line,
    # `soup` is the whole page as objects we can search (find tables, styles...).
    soup = BeautifulSoup(html, "lxml")
 
    # STEP 1 — read the decoy map ONCE for the entire page.
    # Every event has its own <style> block hiding its decoy columns, but those
    # blocks all live in the same document. We gather EVERY hidden class up front
    # into one set, then reuse it for every row on the page. (Collecting per-row
    # would re-scan all the <style> tags thousands of times for no benefit.)
    hidden_classes = collectHiddenClasses(soup)
 
    # STEP 2 — walk every result table on the page, keeping only the rolled-up
    # ones. _resultTables yields all the result tables in document order; many
    # are per-section/heat duplicates we must NOT ingest (they'd double-count
    # athletes). _isRollupTable returns False for those, so we skip them.
    rows = []
    for table in _resultTables(soup):
        # Skip section/heat tables — they re-list the same athletes already in
        # the event's rolled-up table, so ingesting them would duplicate rows.
        if not _isRollupTable(table):
            continue
        # A rolled-up table: parse all its rows (with event context attached) and
        # add them to the page's running list. extend (not append) because
        # _parseEventTable returns a LIST of rows, and we want them flattened in.
        rows.extend(_parseEventTable(table, hidden_classes, meet_id))
 
    # The flat list of every athlete-result on the page, one row per athlete per
    # event (plus the combined parent + sub-event rows for decathlon/heptathlon).
    return rows

 
 
# ------------------------------------------------------------------ #
# HELPERS — finding & classifying the tables
# ------------------------------------------------------------------ #


# _resultTables
# Purpose: Yield every results <table> on the page (the ones with a <tbody> of
#          result rows), in document order.
# Arguments:
#           soup: the parsed page.
# Output:   a list of <table> Tags.
def _resultTables(soup):
    # All result tables share the "tablesaw" class on TFRRS. The team-filter and
    # nav widgets are <select>/<div>, not tables, so this is already clean.
    return soup.find_all("table", class_="tablesaw")

 
# _isRollupTable
# Purpose: Decide whether a table is a ROLLED-UP (overall) result table — the
#          kind we keep — versus a per-section/heat table we skip. Decided by
#          the class prefix on the table's own result cells.
# Arguments:
#           table: a <table> Tag.
# Output:   True if its result cells use a rollup prefix; False for heat tables
#           or tables with no result cells at all.
def _isRollupTable(table):
    prefix = _tableResultPrefix(table)
    return prefix in _ROLLUP_PREFIXES

# _tableResultPrefix
# Purpose: Find which result-cell class scheme a table uses, by inspecting the
#          class of its first result cell. This is what distinguishes rollup
#          (round_) from heat (heat_) tables.
# Arguments:
#           table: a <table> Tag.
# Output:   the matching prefix string (e.g. "sub_round_"), or None if the table
#           has no recognisable result cell.
def _tableResultPrefix(table):

    # For each result cell in the table, get it's class, and return if
    # it starts with a ROLLUP or HEAD prefix.
    for cell in table.find_all(("td", "th")):
        for cls in cell.get("class", []):
            for prefix in (*_ROLLUP_PREFIXES, *_HEAT_PREFIXES):
                if cls.startswith(prefix):
                    return prefix
    return None

# _parseEventTable
# Purpose: Parse one rolled-up event table into result rows, attaching the
#          event context (id, name, kind, gender, combined provenance).
# Arguments:
#           table:          a rolled-up <table> Tag.
#           hidden_classes: the page-wide decoy set.
#           meet_id:        carried onto each row.
# Output:   a list of row dicts (one per <tbody> <tr> that parses).
def _parseEventTable(table, hidden_classes, meet_id):
    event_id      = _eventIdForTable(table)
    title_text    = _titleForTable(table)
    classified    = classifyTFEventTable(table, title_text)
    result_kind   = classified["result_kind"]
    gender        = classified["gender"]
    from_combined = _combinedParentTitle(table) # Multi sub-event.
 
    body = table.find("tbody")
    if body is None:
        return []
 
    out = []
    # For each result in an event parse it and update 
    # event context.
    for tr in body.find_all("tr", recursive=False):
        row = parseTFRow(tr, hidden_classes, result_kind)
        # Attach event context to every row (parsed or not) so nothing floats free.
        row.update({
            "meet_id":       meet_id,
            "event_id":      event_id,
            "event_name":    title_text,   # raw — distance backfill reads this
            "gender":        gender,
            "from_combined": from_combined,
        })
        out.append(row)
    return out
 
 
# ------------------------------------------------------------------ #
# HELPERS — locating an event's id, title, and combined-parent
# ------------------------------------------------------------------ #

# _eventIdForTable
# Purpose: Find the event id for a table — the nearest <a name="..."> anchor
#          ABOVE it in the document. Each event (and each combined sub-event)
#          has its own anchor.
# Arguments:
#           table: a <table> Tag.
# Output:   the event id string, or None if no anchor precedes it.
def _eventIdForTable(table):
    anchor = table.find_previous("a", attrs={"name": True})
    return anchor.get("name") if anchor is not None else None
 
 
# _titleForTable
# Purpose: Find the event title for a rolled-up table — the nearest <h3> ABOVE
#          it. (Rolled-up tables are titled by <h3>; sections by <h5>.)
# Arguments:
#           table: a <table> Tag.
# Output:   the cleaned title text, or "" if no <h3> precedes it.
def _titleForTable(table):
    h3 = table.find_previous("h3")
    return " ".join(h3.get_text().split()) if h3 is not None else ""
 
 
# _combinedParentTitle
# Purpose: If this table is a SUB-EVENT of a combined event, return the parent
#          combined event's title (e.g. "Men's Decathlon") to tag the row's
#          from_combined; else None. Detected by the sub_round_ class scheme.
# Arguments:
#           table: a <table> Tag.
# Output:   the parent combined title string, or None for non-combined events.
def _combinedParentTitle(table):
    # Sub-event tables use the sub_round_ scheme; standalone events don't.
    if _tableResultPrefix(table) != "sub_round_":
        return None
    # The parent decathlon/heptathlon <h3> sits above all its sub-events. We
    # take the FIRST <h3> that mentions a combined-event word, scanning upward.
    for h3 in table.find_all_previous("h3"):
        text = " ".join(h3.get_text().split())
        low = text.lower()
        if "decathlon" in low or "heptathlon" in low or "pentathlon" in low:
            return text
    return None