# Project: xc-predictor / racecast
# File:    us_state.py
# Purpose: THE FIFTY STATES, as a gate the recruiting pages can hold people
#          to (owner, 2026-09-17: "recruiting page should ban ppl from
#          outside us states (so any of the non 50 us states)").
#
# ! NOT pool_resolve.inScope, AND DELIBERATELY SO. inScope answers a
#   different question -- "can I PROVE this row is foreign?" -- so it keeps
#   an unknown state, and it counts Puerto Rico, Guam, the Virgin Islands,
#   American Samoa, the Marianas and the three military codes (AE/AP/AA) as
#   in scope. That is right for the boards: those are American rows on an
#   American scale. Recruiting asks the narrower question the owner asked
#   for, so this is its own set and its own function, rather than a flag on
#   inScope that would change the boards by accident.
#
# ★ DC IS IN. It is not one of the fifty, and a literal reading of the ask
#   drops it -- but a Washington DC high schooler is an American recruit
#   that an American college signs, and the point of the gate is to keep
#   New Zealand and Ontario out, not Georgetown Day School. It is one line
#   to remove if the owner wants the literal fifty: take "DC" out of
#   FIFTY_STATES below and the tests that name it.
#
# ⚠ AN UNKNOWN STATE IS NOT AMERICAN HERE. The boards keep what they cannot
#   disprove because dropping a real row costs a ranking; a recruiting list
#   that quietly carries every state-less row is the thing being complained
#   about. Callers that want the old posture should test `state is None`
#   themselves -- the function will not decide it for them.

FIFTY_STATES = frozenset("""
    AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN
    MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA
    WA WV WI WY
    DC
""".split())

# The spelled-out forms the state column really holds, so a row written
# "Texas" is not thrown away as foreign. Same failure pool_resolve found.
FIFTY_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI",
    "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO",
    "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND",
    "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}


def stateCode(state):
    """'Texas' -> 'TX', 'ca' -> 'CA', anything else -> None."""
    if state is None:
        return None
    s = str(state).strip().upper()
    if not s:
        return None
    if s in FIFTY_STATES:
        return s
    return FIFTY_NAMES.get(s)


def isFiftyState(state):
    """True only for one of the fifty (and DC). Unknown is False -- see the
    warning at the top of this file."""
    return stateCode(state) is not None


def fiftyStateList():
    """The codes, sorted, for binding into `= ANY(%s)`."""
    return sorted(FIFTY_STATES)
