# Project: xc-predictor / racecast
# File:    landing.py
# Purpose: The state ranking landing pages: /rankings/<sport>/<pool>[/<state>]
#
# ★ WHY THESE EXIST (SEO, 2026-09-06). The rankings board is one page whose
#   rows arrive by JavaScript and whose filters live in the query string,
#   and the canonical tag folds every ?state=CA variant into /rankings. So
#   the site had NO page for "California high school boys cross country
#   rankings", which is the search a coach or parent actually types. These
#   are that page: one server-rendered top 100 per sport, pool and state,
#   with a real title, a real URL, links to every athlete and school on it,
#   and links to every sibling, so a crawler can walk the whole set from
#   one link. The interactive board is one click away with the same
#   filters preset.
#
# ! THE DATA IS THE ABILITY BOARD'S, through rankings.parseFilters and
#   getAbilityRankings, so a landing page and the board never disagree.
from rankings import parseFilters, getAbilityRankings, US_STATES

SPORTS = {"xc": "XC", "tf": "TF"}
SPORT_WORDS = {"xc": "Cross Country", "tf": "Track & Field"}

# slug -> (pool key, words)
POOLS = {
    "hs-boys":       ("hs_m",      "High School Boys"),
    "hs-girls":      ("hs_f",      "High School Girls"),
    "college-men":   ("college_m", "College Men"),
    "college-women": ("college_f", "College Women"),
    "ms-boys":       ("ms_m",      "Middle School Boys"),
    "ms-girls":      ("ms_f",      "Middle School Girls"),
}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

LIMIT = 100


def landingPath(sport, pool, state=None):
    return f"/rankings/{sport}/{pool}" + (f"/{state.lower()}" if state else "")


def allLandingPaths():
    """Every landing page, for the sitemap and the cross-links."""
    out = []
    for sport in SPORTS:
        for pool in POOLS:
            out.append(landingPath(sport, pool))
            for st in US_STATES:
                if st in STATE_NAMES:
                    out.append(landingPath(sport, pool, st))
    return out


def landingRows(cur, sport, pool, state, year):
    """The top LIMIT of the ability board for one sport, pool, state and
    season label. (rows, filters) or (None, error)."""
    args = {"board": "ability", "sport": SPORTS[sport], "pool": POOLS[pool][0],
            "limit": str(LIMIT), "year": str(year) if year else ""}
    if state:
        args["state"] = state.upper()
    f, err = parseFilters(args)
    if err:
        return None, err
    return getAbilityRankings(cur, f), f


def landingTitle(sport, pool, state, year):
    where = STATE_NAMES.get((state or "").upper(), "") or "National"
    return (f"{year} " if year else "") + f"{where} {POOLS[pool][1]} " \
        + f"{SPORT_WORDS[sport]} Rankings"


def landingDescription(sport, pool, state, year, n):
    where = STATE_NAMES.get((state or "").upper(), "") or "the nation"
    what = "cross country" if sport == "xc" else "track and field"
    return (f"The top {n or LIMIT} {POOLS[pool][1].lower()} {what} runners in "
            f"{where}{(' for ' + str(year)) if year else ''}, ranked by season "
            f"speed rating on one comparable scale, with every athlete's school, "
            f"grade and race count.")
