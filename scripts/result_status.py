# Project: xc-predictor / scripts
# File:    result_status.py
# Purpose: WHY A ROW HAS NO TIME -- one vocabulary, one rule, for every feed
#          and every reader. Pure: no database, no network, no imports.
#
# ⚠⚠ THE PROBLEM (owner, 2026-09-17): "capture the real DNF/DNS flags, not
#    just the sentinel times". Today a non-finisher is INFERRED from a magic
#    number, and the inference is spelled differently in at least five places:
#
#      time_seconds < 999999                 export_for_analysis
#      NULL time or place 0                  resolve_fanout, tfrrs leg
#      999999 time or place 0                resolve_fanout, anet leg
#      time is None or > 100000              census_no_distance
#      time_seconds < DNF_SENTINEL           reference_fit
#
#    Two of those already disagree -- 999999 against 100000 -- and every one
#    of them is a guess at something the source states outright.
#
# ★ WHY IT MATTERS BEYOND TIDINESS.
#
#    A DNS IS NOT A DNF. Someone who never started is no evidence about their
#    fitness; someone who dropped at 4k is. Both arrive as the same sentinel
#    and both get filtered out, so neither the model nor the boards can treat
#    them differently -- and predict's field builder cannot tell "entered but
#    did not start" from "not entered", which is exactly what `entered` and
#    the team-completeness rules turn on.
#
#    A DQ IS A RESULT THAT HAPPENED. Its time is real; its place is not.
#
# ! THE RAW TOKEN IS WHAT IS STORED, and that is deliberate. database.py has
#   written anet XC's `status` as "DNF"/"DNS"/"DQ" since issue 59; re-encoding
#   it to a tidier enum would orphan every row already there. The tidy answer
#   is DERIVED here instead, by kind().

# What athletic.net and tfrrs actually write. Anything not in here is a real
# result, not a status -- a time is never a status.
VOCAB = frozenset({
    "DNF",   # started, did not finish
    "DNS",   # did not start
    "DQ", "DSQ", "FS",       # disqualified (FS = false start)
    "SCR", "WD",             # scratched / withdrawn before the race
    "NT",                    # no time -- finished, clock missed them
    "NH", "ND", "NM",        # no height / distance / mark (field events)
    "DNP",                   # did not place
})

# Spellings that mean the same thing. Stored canonically so a reader never
# has to know both.
ALIASES = {"DSQ": "DQ"}

# ⚠ THE MAGIC NUMBER, NAMED ONCE. Athletic.net writes one SortValue for every
#   kind of non-finish; this is it. Kept only as the fallback for rows stored
#   before `status` existed -- never as the primary test.
SENTINEL = 999999

# ⚠ AND THE TRACK FEED'S OWN. anet TF sends times as SortInt milliseconds and
#   writes a non-finish as 20,000,000 -- 20,000 s, which the savers' old
#   "< 100,000,000" cap let through as a time. It reached a Diamond League
#   mile as "5:33:20" with PR/SR flags and a 1.7 rating (owner, 2026-09-25:
#   Kidder, Rudolf, Birnbaum, Abdilaahi). No track race takes five and a half
#   hours, so every SortInt at or past it is a status, not a result.
TF_SENTINEL_MS = 20_000_000
TF_SENTINEL = TF_SENTINEL_MS / 1000          # 20000.0 s, the stored form


def isSentinelTime(time_seconds):
    """True when a stored time_seconds is one of the feeds' non-finish
    sentinels rather than a time. None is not a sentinel (it is no time at
    all -- the caller already knows). Pure."""
    if time_seconds is None:
        return False
    try:
        t = float(time_seconds)
    except (TypeError, ValueError):
        return False
    return t >= SENTINEL or t == TF_SENTINEL


def timeFromSortInt(sort_int):
    """anet TF's SortInt (milliseconds) as seconds, or None for a missing
    value or a non-finish sentinel. The one reading both TF savers share."""
    if sort_int is None:
        return None
    try:
        ms = float(sort_int)
    except (TypeError, ValueError):
        return None
    if ms <= 0 or ms >= TF_SENTINEL_MS:
        return None
    return ms / 1000

# The five answers a caller actually wants, derived from the token above.
#   ok         finished, the time is real
#   dnf        started, did not finish -- evidence they were racing
#   dns        never started -- no evidence about them at all
#   dq         finished; the TIME is real and the PLACE is not
#   nomark     attempted and recorded nothing (field events, and NT)
_KIND = {
    "DNF": "dnf",
    "DNS": "dns", "SCR": "dns", "WD": "dns",
    "DQ": "dq", "FS": "dq",
    "NT": "nomark", "NH": "nomark", "ND": "nomark", "NM": "nomark",
    "DNP": "nomark",
}


def normalise(text):
    """The canonical status token for a cell's text, or None when the text is
    not a status at all. Pure.

    ! A TIME IS NEVER A STATUS. "21:26.1" and "" both come back None, so a
      caller can hand this every result cell without filtering first.
    """
    if text is None:
        return None
    t = str(text).strip().upper().replace(".", "").replace("-", "")
    if not t or t not in VOCAB:
        return None
    return ALIASES.get(t, t)


def fromFields(*values):
    """The first real status among several candidate fields. anet's JSON puts
    it in whichever of Result / ShortCode / Status / ResultText it feels like
    that season, so the caller passes them all."""
    for v in values:
        got = normalise(v)
        if got:
            return got
    return None


def kind(status, time_seconds=None):
    """'ok' / 'dnf' / 'dns' / 'dq' / 'nomark' for a stored row. Pure.

    ★ THIS IS THE ONE RULE, and the sentinel is only its LAST resort. A row
      that carries a status is judged by it; a row from before the column
      existed falls back to the magic number, which is all those rows have.
      Nothing else in the project should test 999999 again.
    """
    got = normalise(status)
    if got:
        return _KIND.get(got, "nomark")
    if time_seconds is None:
        return "dns"
    try:
        t = float(time_seconds)
    except (TypeError, ValueError):
        return "dns"
    # ⚠ >= , NOT > 100000. census_no_distance used 100000 and the rest used
    #   999999; a 100,000-second race is 27 hours, so the two only ever
    #   disagreed about rows that do not exist -- but they DID disagree, and
    #   this is the spelling that survives.
    return "dns" if isSentinelTime(t) else "ok"


def isFinish(status, time_seconds=None):
    """Did this athlete complete the race with a usable time?

    ! A DQ COUNTS. The time is real -- it is the PLACE that is void -- so a
      rating may use it and a scoring pass may not. A caller that needs the
      distinction asks kind().
    """
    return kind(status, time_seconds) in ("ok", "dq")


def isRated(status, time_seconds=None):
    """May this row carry a speed rating? Everything with a real time."""
    return isFinish(status, time_seconds)


def startedRace(status, time_seconds=None):
    """Did they go to the line? A DNF did; a DNS did not. This is the one
    predict's field builder needs and the sentinel could never answer."""
    return kind(status, time_seconds) in ("ok", "dq", "dnf", "nomark")


# The SQL a query needs when it wants only real results. One spelling, so the
# five in the header cannot drift apart again.
def finishedSql(alias="r"):
    """A WHERE fragment: rows with a usable time, status-aware."""
    return (f"(COALESCE(upper(btrim({alias}.status)), '') NOT IN "
            f"('DNF','DNS','SCR','WD','NT','NH','ND','NM','DNP') "
            f"AND {alias}.time_seconds IS NOT NULL "
            f"AND {alias}.time_seconds < {SENTINEL})")
