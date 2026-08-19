#!/usr/bin/env python3
# ======================================================================
# fill_deadend_venues.py
# ----------------------------------------------------------------------
# THE IDEA (read this before the code)
#
# The automated geocoder failed on these rows for ONE reason: the venue
# record has no name and no address string, so Census/Nominatim had
# nothing to query. The only surviving clue is the STATE code plus a few
# SAMPLE MEET NAMES. A human reads "Ramstein Invitational" and instantly
# knows "Germany, Ramstein air base". This script encodes that human read
# as a CURATED LOOKUP TABLE and applies it in bulk. No network — the
# network already had its turn and abstained.
#
# Design in one sentence: a FIRST-MATCH-WINS CASCADE of small rules,
# where NOT MATCHING is the normal, safe default (we place a coordinate
# only when a clue pins a single city; otherwise we leave it null).
#
#   row (state, meet_names)
#         |
#         v
#   normalize text  (lowercase + strip accents)
#         |
#         v
#   CASCADE — first rule to fire wins:
#     1. @host rule     "...@Hohenfels" -> host is Hohenfels, not the
#                        visiting schools listed before the '@'
#     2. keyword rule    token in meet names -> a curated city coord
#     3. region rule     state code -> a curated city coord
#     4. (no rule)       -> abstain: leave lat/long null   <-- the default
#         |
#         v
#   write filled TSV  +  stdout coverage report  +  optional provenance
#
# WHY abstain-by-default is the whole safety story: a WRONG coordinate
# silently poisons the weather backfill downstream; a NULL just stays a
# dead-end. So every rule below names ONE city, and anything that roams
# (national championships, "Time Trial", bare "All-Island") matches
# nothing and falls through to null on purpose.
# ======================================================================

import argparse                      # CLI: every knob is a flag
import sys                           # stderr for the report
import unicodedata                   # accent folding (Dernière -> derniere)
from collections import Counter, namedtuple
from dataclasses import dataclass
from datetime import date
from typing import Optional


# ======================================================================
# CHUNK 1 — THE RULESET (data, not code)
# ----------------------------------------------------------------------
# Rules are DATA you can eyeball and extend without touching logic. A
# Rule is (tokens, lat, lon, label): if ANY token is a substring of the
# normalized meet-name text, the row is placed at (lat, lon) and stamped
# with `label` for auditing.
#
# Ordering = priority (first match wins). Keep tokens SPECIFIC to one
# city. Never add a broad token like "japan" — "All-Japan Nationals"
# roams, and a broad token would wrongly pin it.
# ======================================================================

Rule = namedtuple("Rule", "tokens lat lon label")

# --- Keyword rules: a clue token in the meet name -> a single city. ----
# Grouped by region only for human reading; the list is scanned top-down.
KEYWORD_RULES = [
    # Japan — Okinawa (DoDEA Pacific; OAC / Petty Memorial are Okinawa)
    Rule(("kadena",),               26.35, 127.77, "jp_okinawa_kadena"),
    Rule(("kubasaki",),             26.28, 127.78, "jp_okinawa_kubasaki"),
    Rule(("okinawa", "koza"),       26.34, 127.80, "jp_okinawa"),
    Rule(("petty", "oaac", "oac"),  26.34, 127.80, "jp_okinawa_oac"),

    # Japan — Kanto / Greater Tokyo (specific prefectures first, then generic)
    Rule(("oi futo",),              35.58, 139.76, "jp_tokyo_oifuto"),
    Rule(("saitama",),              35.86, 139.65, "jp_saitama"),
    Rule(("kanagawa",),             35.45, 139.64, "jp_kanagawa"),
    Rule(("chiba",),                35.61, 140.12, "jp_chiba"),
    Rule(("koshigaya",),            35.89, 139.79, "jp_koshigaya"),
    Rule(("higashi matsuyama",),    36.04, 139.40, "jp_higashimatsuyama"),
    Rule(("kanto", "tokyo"),        35.68, 139.76, "jp_kanto_tokyo"),

    # Korea (KAIAC hub is Seoul; named bases pin their own city)
    Rule(("humphreys",),            36.97, 127.03, "kr_humphreys"),
    Rule(("osan",),                 37.09, 127.03, "kr_osan"),
    Rule(("daegu",),                35.87, 128.60, "kr_daegu"),
    Rule(("seoul", "sahs", "kaiac", "korea", "ksaa"),
                                    37.55, 126.99, "kr_seoul"),

    # Germany (each garrison is its own city)
    Rule(("ramstein",),             49.44,  7.60, "de_ramstein"),
    Rule(("kaiserslautern",),       49.44,  7.77, "de_kaiserslautern"),
    Rule(("patch", "stuttgart"),    48.75,  9.10, "de_stuttgart_patch"),
    Rule(("hohenfels",),            49.22, 11.83, "de_hohenfels"),
    Rule(("vilseck",),              49.62, 11.80, "de_vilseck"),
    Rule(("bamberg",),              49.90, 10.90, "de_bamberg"),
    Rule(("heidel",),               49.40,  8.67, "de_heidelberg"),
    Rule(("wiesbaden",),            50.05,  8.24, "de_wiesbaden"),
    Rule(("ansbach",),              49.30, 10.58, "de_ansbach"),
    Rule(("baumholder",),           49.63,  7.33, "de_baumholder"),
    Rule(("bfa",),                  47.71,  7.66, "de_bfa_kandern"),

    # Low Countries / Belgium
    Rule(("afnorth", "brunssum"),   50.94,  5.97, "nl_afnorth_brunssum"),
    Rule(("shape",),                50.50,  3.97, "be_shape_mons"),
    Rule(("brussels",),             50.85,  4.35, "be_brussels"),
    Rule(("lokeren",),              51.10,  3.99, "be_lokeren"),

    # England (RAF stations)
    Rule(("lakenheath",),           52.41,  0.56, "gb_lakenheath"),
    Rule(("alconbury",),            52.37, -0.22, "gb_alconbury"),
    Rule(("feltwell",),             52.48,  0.52, "gb_feltwell"),
    Rule(("croughton",),            51.98, -1.19, "gb_croughton"),

    # Italy (DoDEA Mediterranean)
    Rule(("vicenza",),              45.55, 11.55, "it_vicenza"),
    Rule(("naples",),               40.85, 14.27, "it_naples"),
    Rule(("aviano",),               46.03, 12.60, "it_aviano"),
    Rule(("sigonella",),            37.40, 14.92, "it_sigonella"),
    Rule(("livorno",),              43.55, 10.31, "it_livorno"),

    # Spain
    Rule(("rota",),                 36.62, -6.35, "es_rota"),
    Rule(("moron",),                37.16, -5.60, "es_moron"),

    # Bahrain
    Rule(("bahrain",),              26.19, 50.55, "bh_manama"),

    # Guam (IIAAG / GDOE / GPSS league acronyms — the island is small; center is fine)
    Rule(("iiaag", "iiag", "iaag", "gdoe", "gpss", "guam"),
                                    13.47, 144.79, "gu_guam"),

    # Canada — split cities (keyword, because provinces are too broad)
    Rule(("moncton", "atlantic indoors", "derniere"),
                                    46.10, -64.80, "ca_moncton"),
    Rule(("nbindoor", "red & black", "red and black"),
                                    45.95, -66.64, "ca_fredericton"),
    Rule(("dino",),                 51.08, -114.13, "ca_calgary"),
    Rule(("k of c",),               52.13, -106.63, "ca_saskatoon"),
]

# --- Region rules: state code -> one city. ONLY where the region's ----
# school meets cluster at a single dominant venue, so a centroid is
# honestly "the city" and not a province-sized average.
REGION_RULES = {
    "NSW":              Rule((), -33.85, 151.07, "au_sydney_sopac"),
    "QLD":              Rule((), -27.56, 153.06, "au_brisbane_qsac"),
    "St. Andrew Parish": Rule((), 18.00, -76.78, "jm_kingston"),
}

# --- Per-location_id OVERRIDES (hand-curated, HIGHEST priority). ------
# These are rows the token/region rules can't reach but a human CAN place
# by reading the full meet history (a specific town/school/host is named,
# often as an acronym the geocoder mis-resolves). Keyed by location_id so
# there is zero fuzzy-match risk: this exact venue gets exactly this coord.
# City-level; spot-check before trusting the weather backfill.
LOCATION_OVERRIDES = {
    # --- US: town/school identified from the meet names ---------------
    "141237": (44.77,  -93.28, "us_mn_burnsville"),        # Blaze = Burnsville
    "141795": (44.73,  -93.22, "us_mn_apple_valley"),      # AAU + S-metro suburbs
    "140576": (38.63,  -90.23, "us_mo_st_louis"),          # SLPS
    "140734": (41.16,  -92.64, "us_ia_eddyville"),         # EBF
    "142470": (43.13,  -70.93, "us_nh_durham_unh"),        # NHWTL @ UNH
    "113007": (32.35,  -97.39, "us_tx_cleburne"),          # Johnson County
    "140936": (40.52,  -78.39, "us_pa_altoona"),           # @Altoona
    "141811": (30.40,  -88.89, "us_ms_biloxi"),            # Coastal MS / Nativity BVM
    "141229": (40.22,  -74.94, "us_pa_newtown"),           # George School
    "141228": (41.00,  -75.18, "us_pa_e_stroudsburg_esn"), # ESN dual
    "141197": (41.58,  -93.50, "us_ia_pleasant_hill"),     # SE Polk / CIML
    "141583": (41.62,  -93.71, "us_ia_urbandale"),         # DSM Christian
    "142494": (39.95,  -75.16, "us_pa_philadelphia_pcl"),  # Phila Catholic League
    "142743": (33.80, -118.07, "us_ca_los_alamitos"),      # Los Al vs Servite/Rosary
    "140512": (38.56,  -91.01, "us_mo_washington"),        # Borgia
    "141830": (36.07,  -79.77, "us_nc_greensboro_ncat"),   # NCHSAA state @ NC A&T
    "141389": (42.02,  -93.45, "us_ia_nevada"),            # CN Robinson / Nevada
    "140716": (40.51,  -88.99, "us_il_bloomington_normal"),
    "141447": (40.04,  -75.17, "us_pa_philly_gfs"),        # Germantown Friends
    "141231": (40.00,  -75.28, "us_pa_wynnewood_fc"),      # Friends Central
    "142702": (41.87,  -88.01, "us_il_lombard"),           # Glenbard East
    "113006": (37.98, -101.75, "us_ks_syracuse"),          # HPL
    "141568": (41.69,  -98.00, "us_ne_albion"),            # Boone Central JH
    "141599": (40.14,  -97.18, "us_ne_fairbury"),
    "142732": (39.96,  -83.00, "us_oh_columbus"),          # OHSAA Central District
    "142458": (44.70,  -73.45, "us_ny_plattsburgh"),       # Section 7 CVAC
    "141156": (40.92,  -73.78, "us_ny_new_rochelle"),      # Iona / Ursuline
    "141704": (40.04,  -75.51, "us_pa_malvern"),           # Malvern Prep
    "140585": (38.79,  -90.66, "us_mo_st_peters"),         # Fort Zumwalt East
    "141891": (43.54,  -89.46, "us_wi_portage"),           # WIAA D2 - Portage
    "141589": (41.00,  -75.18, "us_pa_e_stroudsburg_ess"), # ESS Cavalier
    "141354": (42.27,  -89.09, "us_il_rockford"),          # NIC-10
    "141468": (41.08,  -96.14, "us_ne_springfield"),       # Platteview
    "142634": (41.88,  -87.85, "us_il_maywood"),           # Proviso East
    "140322": (33.74, -117.16, "us_ca_menifee"),           # Heritage vs San Jacinto/Elsinore
    "140572": (38.55,  -90.61, "us_mo_wildwood"),          # Rockwood district
    "142562": (40.15,  -88.96, "us_il_clinton"),           # Clinton (central IL)
    "113004": (39.40,  -83.15, "us_oh_frankfort"),         # Adena
    # --- Overseas tail the token rules just missed -------------------
    "112879": (26.34, 127.80, "jp_okinawa_oac"),           # OAC Weekly Quadrangular
    "112880": (26.34, 127.80, "jp_okinawa_oac"),
    "112881": (26.34, 127.80, "jp_okinawa_oac"),
    "113019": (37.55, 126.99, "kr_seoul"),                 # SAMHS = Seoul American
    "112953": (52.21,   5.96, "nl_apeldoorn"),             # Dutch Jr indoor nationals
    "112915": (63.37,  25.58, "fi_pihtipudas"),            # Keihäskarnevaalit
    # --- US: distinctive names verified via the geocoder pass ---------
    "138176": ( 40.006,   -83.029, "us_oh_columbus"),
    "138318": ( 32.776,   -89.867, "us_ms_camden"),
    "141187": ( 41.688,   -98.003, "us_ne_albion_bc"),
    "140571": ( 38.650,   -90.336, "us_mo_clayton"),
    "141062": ( 40.998,   -75.184, "us_pa_e_strd_south"),
    "141063": ( 40.986,   -75.195, "us_pa_stroudsburg"),
    "141106": ( 41.839,   -94.107, "us_ia_perry"),
    "141483": ( 40.715,   -94.238, "us_ia_mt_ayr"),
    "140583": ( 38.979,   -90.981, "us_mo_troy"),
    "141194": ( 41.571,   -93.709, "us_ia_wdm_valley"),
    "142742": ( 33.930,  -116.976, "us_ca_beaumont"),
    "141159": ( 41.700,   -93.054, "us_ia_newton"),
    "141122": ( 41.015,   -93.784, "us_ia_osceola"),
    "140575": ( 38.482,   -90.742, "us_mo_pacific"),
    "141448": ( 39.843,   -75.537, "us_pa_garnet_valley"),
    "139744": ( 41.455,   -88.262, "us_il_minooka"),
    "141078": ( 41.114,   -77.482, "us_pa_lock_haven"),
    "140551": ( 41.455,   -88.262, "us_il_minooka"),
    "141449": ( 39.931,   -75.552, "us_pa_westtown"),
    "141514": ( 41.374,   -93.739, "us_ia_martensdale"),
    "141341": ( 40.138,   -75.220, "us_pa_ft_washington"),
    "113000": ( 43.702,  -124.097, "us_or_reedsport"),
    "140577": ( 38.503,   -90.628, "us_mo_eureka"),
    "140586": ( 39.019,   -94.198, "us_mo_grain_valley"),
    "141304": ( 41.890,   -93.396, "us_ia_collins_maxwell"),
    "140582": ( 38.938,   -92.350, "us_mo_columbia"),
    "141294": ( 40.208,   -75.527, "us_pa_royersford"),
    "141719": ( 43.743,   -90.779, "us_wi_cashton"),
    "142528": ( 35.190,   -78.648, "us_nc_falcon"),
    "140942": ( 35.318,   -79.347, "us_nc_cameron"),
    "141252": ( 41.815,   -74.188, "us_ny_accord"),
    "141281": ( 43.081,   -96.164, "us_ia_sioux_center"),
    "141019": ( 40.617,   -93.926, "us_ia_lamoni"),
    "141450": ( 40.025,   -75.277, "us_pa_ardmore"),
    "140957": ( 39.843,   -75.537, "us_pa_garnet_valley"),
    "141784": ( 39.895,   -75.372, "us_pa_wallingford"),
}


# ======================================================================
# CHUNK 2 — TEXT NORMALIZATION (tiny, pure helpers)
# ----------------------------------------------------------------------
# Matching is done on a normalized copy so "Dernière", "DERNIERE" and
# "derniere" all collapse to one comparable form.
# ======================================================================

def _normalize(text):
    """
    Purpose : fold a raw meet-name / state string to a match-ready form.
    Arguments:
      text  -- the raw cell (str). May carry mixed case, accents, a stray
               leading quote, or trailing spaces.
    Output  : lowercase, accent-stripped str. NEVER None (empty -> '').
    """
    if not text:                                  # None or '' -> ''
        return ""
    # NFKD splits an accented char into base + combining mark; we then
    # drop the marks, turning 'è' into 'e'.
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Drop periods so acronyms collapse: 'O.A.C.' -> 'oac' (matches token
    # 'oac'); other punctuation is kept because we WANT it as a boundary.
    return stripped.lower().replace(".", "").strip()


def _hostToken(normText):
    """
    Purpose : recover the HOST venue from an "attendees@host" string.
    Arguments:
      normText -- already-normalized meet text (str).
    Output  : the text AFTER the last '@' (str), or '' if there is no '@'.
              Example: 'heidel,ramst@hohenfels 2009' -> 'hohenfels 2009'.
              We return the tail (not just one word) so the keyword scan
              can still find its token inside it.
    """
    if "@" not in normText:
        return ""
    return normText.rsplit("@", 1)[1].strip()     # part after the final '@'


def _wordBounded(haystack, token):
    """
    Purpose : True if `token` occurs bounded by non-letters, so a short
              acronym can't hide inside a bigger word.
    Arguments:
      haystack -- normalized text to search (str).
      token    -- normalized needle (str).
    Output  : bool. The char immediately before AND after the match must
              each be a non-letter or a string edge. This is what makes
              'oac' match 'oac weekly' but NOT 'coaches' (the 'c' before)
              and NOT 'coach' (the 'h' after).
    """
    start = haystack.find(token)
    while start != -1:                            # walk every occurrence
        end = start + len(token)
        before = haystack[start - 1] if start > 0 else ""      # '' = edge
        after = haystack[end] if end < len(haystack) else ""   # '' = edge
        if not before.isalpha() and not after.isalpha():
            return True
        start = haystack.find(token, start + 1)   # keep looking past this hit
    return False


def _containsAny(normText, tokens):
    """
    Purpose : True if any curated token appears as a WHOLE word/acronym.
    Arguments:
      normText -- normalized haystack (str).
      tokens   -- tuple of normalized needles (already lowercase/ascii).
    Output  : bool. Uses _wordBounded so distinctive-looking tokens like
              'oac' can't false-match inside 'coaches'.
    """
    return any(_wordBounded(normText, tok) for tok in tokens)


# ======================================================================
# CHUNK 3 — THE MATCHERS (one rule-kind each; all short)
# ----------------------------------------------------------------------
# Each returns a Rule (a hit) or None (no opinion). Keeping them separate
# is what lets the cascade read as a plain priority list.
# ======================================================================

def _matchOverride(locationId):
    """
    Purpose : hand-curated coordinate for a specific venue, by location_id.
    Arguments: locationId -- the row's location_id cell (str).
    Output  : Rule or None. Highest priority: a human already decided this
              exact venue, so it beats every heuristic below it.
    """
    hit = LOCATION_OVERRIDES.get(locationId.strip())
    if hit is None:
        return None
    lat, lon, label = hit
    return Rule((), lat, lon, label)


def _matchKeyword(normText):
    """
    Purpose : first keyword Rule whose token is in the text.
    Arguments: normText -- normalized meet text (str).
    Output  : Rule or None. Scans KEYWORD_RULES top-down (order = priority).
    """
    for rule in KEYWORD_RULES:
        if _containsAny(normText, rule.tokens):
            return rule
    return None


def _matchHost(normText):
    """
    Purpose : resolve an "@host" string to the HOST city, ignoring the
              visiting schools listed before the '@'.
    Arguments: normText -- normalized meet text (str).
    Output  : Rule or None. We run the ordinary keyword scan but ONLY on
              the post-'@' tail, so 'heidel,ramst@hohenfels' -> Hohenfels
              instead of matching 'heidel' first.
    """
    tail = _hostToken(normText)
    if not tail:
        return None
    return _matchKeyword(tail)


def _matchRegion(stateRaw):
    """
    Purpose : fall back to a state/region's dominant city.
    Arguments:
      stateRaw -- the row's `states` cell, verbatim (str). We match on the
                  RAW value because these codes ('NSW', 'St. Andrew Parish')
                  are not lowercase tokens inside meet names.
    Output  : Rule or None.
    """
    return REGION_RULES.get(stateRaw.strip())


# ======================================================================
# CHUNK 4 — THE CASCADE (the one public decision function)
# ----------------------------------------------------------------------
# This is the whole policy, and it stays short because the matchers do
# the work. Read it as: host beats keyword beats region beats abstain.
# ======================================================================

Placement = namedtuple("Placement", "lat lon rule")

def placeRow(locationId, stateRaw, meetNames):
    """
    Purpose : decide a city-level coordinate for one dead-end row.
    Arguments:
      locationId -- the `location_id` cell (str). Checked first against the
                    hand-curated overrides.
      stateRaw   -- the `states` cell, verbatim (str).
      meetNames  -- the `sample_meet_names` cell, verbatim (str). Pipe-
                    separated samples; may contain a stray quote.
    Output  : Placement(lat, lon, rule). On abstain, lat and lon are None
              and rule is 'abstain' — the caller writes nulls.
    """
    norm = _normalize(meetNames)                  # normalize once, reuse
    # Priority chain — first non-None wins. Override beats every heuristic.
    hit = (_matchOverride(locationId) or _matchHost(norm)
           or _matchKeyword(norm) or _matchRegion(stateRaw))
    if hit is None:
        return Placement(None, None, "abstain")   # the safe default
    return Placement(hit.lat, hit.lon, hit.label)


# ======================================================================
# CHUNK 5 — TSV I/O (robust to real-world mess)
# ----------------------------------------------------------------------
# We split on TAB by hand rather than using csv.reader: one row begins
# with a lone double-quote ('"Pre-Petty'), which the csv module would
# treat as an unterminated quoted field and mis-parse across lines.
# ======================================================================

# One row of the file, with the two coordinate slots we will fill in.
@dataclass
class DeadendRow:
    locationId: str
    nDivs: str
    nMeets: str
    states: str
    meetNames: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    rule: str = ""

# The 5 leading data columns, in file order (coords are appended by us).
_LEAD_COLS = 5

def _parseLine(line):
    """
    Purpose : turn one raw data line into a DeadendRow (coords still empty).
    Arguments: line -- a single TSV line, newline already stripped (str).
    Output  : DeadendRow, or None if the line is blank / a comment.
              Short lines are padded so a row with trimmed trailing tabs
              still yields all 5 lead fields.
    """
    if not line.strip() or line.lstrip().startswith("#"):
        return None                               # skip blanks and '#' comments
    parts = line.split("\t")                      # TAB split, no quote magic
    parts += [""] * (_LEAD_COLS - len(parts))     # pad if trailing tabs were lost
    locationId, nDivs, nMeets, states, meetNames = parts[:_LEAD_COLS]
    return DeadendRow(locationId, nDivs, nMeets, states, meetNames)


def readDeadends(path):
    """
    Purpose : load the dead-end file into rows + its comment header.
    Arguments: path -- input .tsv path (str).
    Output  : (rows, commentLines):
                rows         -- list[DeadendRow] (coords unfilled).
                commentLines -- the leading '#' lines, verbatim, so we can
                                re-emit them and stay drop-in for `apply`.
    """
    rows, commentLines = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.lstrip().startswith("#"):
                commentLines.append(line)         # keep header comments as-is
            parsed = _parseLine(line)
            if parsed is not None:
                rows.append(parsed)
    return rows, commentLines


def _formatCoord(value):
    """
    Purpose : coordinate -> cell string.
    Arguments: value -- float or None.
    Output  : '' for None; else 3-decimal str (~100 m — honest for a
              curated city centroid, no false precision).
    """
    return "" if value is None else f"{value:.3f}"


def writeFilled(path, rows, commentLines):
    """
    Purpose : write the filled file, SAME 7 columns as input (drop-in).
    Arguments:
      path         -- output .tsv path (str).
      rows         -- list[DeadendRow], coords now decided.
      commentLines -- original '#' header lines to re-emit first.
    Output  : None (writes to disk). Adds one extra '#' provenance note;
              `apply` skips '#' lines, so the schema stays 7 columns.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for c in commentLines:
            fh.write(c + "\n")
        fh.write(f"# auto-filled {date.today().isoformat()} by "
                 f"fill_deadend_venues.py (per-row rule in provenance sidecar)\n")
        for r in rows:
            fh.write("\t".join([
                r.locationId, r.nDivs, r.nMeets, r.states, r.meetNames,
                _formatCoord(r.lat), _formatCoord(r.lon),
            ]) + "\n")


def writeProvenance(path, rows):
    """
    Purpose : audit sidecar — which rule placed each row.
    Arguments: path -- output path (str); rows -- placed DeadendRows.
    Output  : None. Two columns: location_id, geo_rule. Kept OUT of the
              main file so the main file stays 7-column drop-in.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("location_id\tgeo_rule\n")
        for r in rows:
            fh.write(f"{r.locationId}\t{r.rule}\n")


# ======================================================================
# CHUNK 6 — REPORT (eyes-first: see coverage before you trust it)
# ----------------------------------------------------------------------
# ======================================================================

def printReport(rows):
    """
    Purpose : print a coverage summary to stderr for a human to sanity-check.
    Arguments: rows -- placed DeadendRows.
    Output  : None. Shows placed/abstained totals and a per-rule tally so a
              wrong rule (e.g. an over-broad token firing 40 times) is obvious.
    """
    placed = [r for r in rows if r.lat is not None]
    tally = Counter(r.rule for r in placed)
    print(f"rows: {len(rows)}  placed: {len(placed)}  "
          f"abstained: {len(rows) - len(placed)}", file=sys.stderr)
    for rule, n in tally.most_common():
        print(f"  {n:>4}  {rule}", file=sys.stderr)


# ======================================================================
# CHUNK 7 — CLI (main stays a short conductor; helpers do the work)
# ----------------------------------------------------------------------
# ======================================================================

def _buildParser():
    """Purpose: define every knob as a flag. Output: ArgumentParser."""
    p = argparse.ArgumentParser(
        description="Fill dead-end venue GPS from state + meet-name clues.")
    p.add_argument("--in", dest="inPath", required=True,
                   help="input tf_deadend_venues.tsv")
    p.add_argument("--out", dest="outPath", required=True,
                   help="output filled .tsv (same 7 columns)")
    p.add_argument("--provenance-out", dest="provPath", default=None,
                   help="optional sidecar: location_id -> firing rule")
    p.add_argument("--min-divs", dest="minDivs", type=int, default=0,
                   help="only place rows with n_divs >= this (0 = all)")
    return p


def _placeAll(rows, minDivs):
    """
    Purpose : run the cascade over every row (respecting --min-divs).
    Arguments: rows -- unfilled DeadendRows; minDivs -- size gate (int).
    Output  : None (mutates rows in place with lat/lon/rule).
    """
    for r in rows:
        divs = int(r.nDivs) if r.nDivs.isdigit() else 0
        if divs < minDivs:                        # below the gate -> skip
            r.rule = "skipped_min_divs"
            continue
        r.lat, r.lon, r.rule = placeRow(r.locationId, r.states, r.meetNames)


def main():
    """Purpose: wire read -> place -> write -> report. Output: None."""
    args = _buildParser().parse_args()
    rows, commentLines = readDeadends(args.inPath)
    _placeAll(rows, args.minDivs)
    writeFilled(args.outPath, rows, commentLines)
    if args.provPath:
        writeProvenance(args.provPath, rows)
    printReport(rows)


if __name__ == "__main__":
    main()