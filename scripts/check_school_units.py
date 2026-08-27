# Project: xc-predictor / scripts
# File:    check_school_units.py
# Purpose: Infer every school's league / section / district / class from the
#          championship meets it actually attends. Read-only census -- the
#          writer comes after this proves precision on real names.
#
#     python scripts/check_school_units.py                     # XC census
#     python scripts/check_school_units.py --sport TF
#     python scripts/check_school_units.py --school "De La Salle"
#     python scripts/check_school_units.py --unparsed           # worst names
#
# ★ THE LCD MEET IS THE ROSTER (owner's insight, 2026-08-27). Nobody has to
#   enter an invitational, but every program contests its lowest-common-
#   denominator championship -- league finals, district, sectional -- on the
#   way toward state/nationals. So membership is inferred ONLY from
#   late-season championship-gated meets: a school racing the "EBAL
#   Championships" is in the EBAL, full stop. Invitationals never vote.
#
# ★ DIV IS LOST ALONG THE WAY -- GUARD IT (owner's warning). The chain runs
#   league -> section(/div) -> state(/div), and the division token drops off
#   meet names unpredictably ("NCS Championships" one year, "NCS Division I
#   Championships" the next; sometimes it only survives in the RACE title).
#   Therefore: every unit KIND keeps its own independent vote counter, the
#   class/div token is harvested from meet name AND division title, votes
#   only ever ACCUMULATE (a bare name cannot blank an earlier div), and a
#   school whose class votes conflict is FLAGGED, never averaged.

import argparse
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                          # noqa: E402

# late-season windows: the championship chain toward state/nationals
_WINDOW = {"XC": (10, 12), "TF": (5, 6)}

# ---- the parser ------------------------------------------------------- #
# A name must pass the championship gate, then each detector extracts an
# independent (kind, unit) fact. Class/div is its own kind and is also
# probed on the division/race title.

_CHAMP_RX = re.compile(
    r"champ|meet of champions|finals?\b|\bstate meet\b|"
    # the qualifying rounds ARE the LCD meets in most states
    r"sectionals?\b|regionals?\b|districts?\b", re.I)
# invitationals, plus championship-NAMED meets that are not school units:
# club postseason (USATF/AAU Junior Olympics), shoe-company nationals,
# foreign systems (OFSAA/provincials, ekiden), regional all-comers
_NEVER_RX = re.compile(
    r"invit|classic|festival|preview|opener|relays\b|scrimmage|jamboree|"
    r"time trial|last chance|qualifier meet|carnival|series\b|"
    r"usatf|\baau\b|junior olympic|foot ?locker|\bnike\b|\bnxn\b|"
    r"new balance|adidas|runninglane|brooks\b|hoka\b|garrett companies|"
    r"mitca|ekiden|ofsaa|provincial|\byouth\b|xc town|festival of champions|"
    r"new england|all japan|\bbc hs\b|"
    # round 3 (2026-08-27 corpus): youth orgs, foreign systems, national
    # finals and regional all-comers that are not membership units.
    # NCAA NATIONALS excluded; NCAA REGIONALS still vote (region + class).
    r"\bcyo\b|insp?orts|mayor's cup|alberta|manitoba|canadian|toronto|"
    r"british columbia|neicaaa|track houston|"
    # coast all-comers, but never the West Coast CONFERENCE (a real league)
    r"\b(?:east|west) coast (?:track|champ|national)|"
    r"midwest meet of champions|larry steeb|honor roll|\busa junior\b|"
    r"\busa youth\b|ncaa\s+d\S*(?!.*region)", re.I)

# class/div tokens, meet name or race title:  5A, AAA, Class B, Division
# III, D3, Group 2 (NJ), Open Division
_CLASS_RX = [
    re.compile(r"\b([1-9]-?A{1,4})\b"),
    # letters cover CT-style L/M/S/LL too; \b keeps "Class Championships"
    # from matching (no boundary three letters into "Championships")
    re.compile(r"\bclass\s+([A-Z]{1,3}|[1-9][A-D]?)\b", re.I),
    re.compile(r"\bdivision\s+(I{1,3}V?|VI?|[1-9]|One|Two|Three|Four|Five)\b",
               re.I),
    re.compile(r"\bD-?([1-5])\b"),
    re.compile(r"\bgroup\s+([1-4])\b", re.I),
    re.compile(r"\b(open)\s+division\b", re.I),
]

# unit detectors, most specific first. Each returns (kind, unit-string).
# "X League/Conference Championships" keeps X as the unit; a bare all-caps
# acronym before "Championships" (EBAL, WCAL, MVAL) is a league too.
_SECTION_ACRONYMS = {"NCS", "CCS", "CIF-SS", "CIFSS", "SJS", "SDS"}
# ! NOT a unit by itself: CIF is the state body, "STATE" is the state rule's
#   job, and a section acronym must not double-vote as a league.
_NOT_A_LEAGUE = _SECTION_ACRONYMS | {"CIF", "STATE", "NCAA", "NAIA", "NJCAA",
                                     "USATF", "AAU"}

# state athletic associations: an explicit list plus the suffix families
# (…SIAA, …PHSAA/PHAA, …SHSAA, …HSAA). Their bare championships and Meet
# of Champions ARE the state series (NJSIAA MoC, NYSPHSAA Champs).
_ASSOC_RX = re.compile(
    r"\b(?:[A-Z]{1,5}(?:SIAA|PHS?AA|SHSAA?|SHSL|HSAA|HSSA)|UIL|OSAA|WIAA|"
    r"GHSA|FHSAA|OHSAA|PIAA|VHSL|TSSAA|KHSAA|LHSAA|AHSAA|IHSAA?|MSHSL|"
    r"MHSAA|MSHSAA|MIAA|HHSAA|SDHSAA|NDHSAA|WVSSAC|SCHSL|NCHSAA|"
    r"NHIAA|DIAA|CIAC|ASAA|VISAA|NCSAA|SCISA|NYSAIS)\b")

# US state names: the LAST-RESORT state rule ("Michigan Meet of Champions",
# "Nebraska Championship Meet") -- applied only when NO other unit matched,
# so "Mississippi Valley Conference" stays a league, never a state vote.
_STATE_NAME_RX = re.compile(
    r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|"
    r"Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|"
    r"Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
    r"Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|"
    r"New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|"
    r"Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
    r"South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|"
    r"West Virginia|Wisconsin|Wyoming)\b", re.I)

_UNIT_RULES = [
    ("state",    re.compile(r"\b(state|all-state|federation)\b", re.I),
     None),
    ("state",    _ASSOC_RX, None),
    ("section",  re.compile(r"\b([\w .&'-]+?)\s+section(?:al)?s?\b", re.I), 1),
    ("section",  re.compile(r"\b(NCS|CCS|CIF-?SS|SJS|SDS)\b"), 1),
    # "CIF Sac-Joaquin Cross Country Championships": the section name sits
    # directly after CIF with no "Section" word at all
    ("section",  re.compile(r"\bCIF\s+([A-Z][\w .&'-]+?)\s+"
                            r"(?i:cross|xc|x-|track|champ|finals)"), 1),
    ("district", re.compile(r"\bdistrict\s*([\dA-Z-]{0,6})\b", re.I), 1),
    ("region",   re.compile(r"\bregion(?:al)?s?\s*([\dA-Z-]{0,4})\b", re.I),
     1),
    # county championships are a real unit (Orange County, Bergen County)
    ("county",   re.compile(r"\b([\w .'-]+?)\s+county\b", re.I), 1),
    ("league",   re.compile(r"\b([\w .&'-]+?)\s+league\b", re.I), 1),
    ("league",   re.compile(r"\b([\w .&'-]+?)\s+conference\b", re.I), 1),
    ("league",   re.compile(r"\b(PSAL|CHSAA|CHSFL)\b"), 1),
    # bare all-caps acronym before Champ/Finals = a league (EBAL, WCAL,
    # and with the sport-word filler allowed: "MAC Cross Country
    # Championships", "SEC XC Championship Meet", "NJIC Divisional") --
    # scoped (?i:) so "Championships" matches while the acronym stays
    # case-sensitive; the exclusion set stops section/state double-votes
    ("league",   re.compile(
        r"\b([A-Z]{3,6})\s+"
        r"(?:(?i:cross[- ]?country|xc|x-country|cc|track(?:\s*(?:&|and)\s*"
        r"field)?|t&f|outdoor|indoor|division(?:al)?)\s+)*"
        r"(?i:champ|finals?)"), 1),
]

_YEAR_RX = re.compile(r"\b(?:19|20)\d\d\b")
_ORDINAL_RX = re.compile(r"\b\d+(?:st|nd|rd|th)\s+annual\b", re.I)


def _cleanUnit(kind, unit):
    """Strip year prefixes, ordinals and the redundant CIF prefix so
    '2022 CIF LOS ANGELES' and 'CIF NORTH COAST' collapse toward their
    real names. Alias merging (NCS = NORTH COAST) is the writer's job."""
    unit = _YEAR_RX.sub("", unit)
    unit = _ORDINAL_RX.sub("", unit)
    unit = re.sub(r"\s{2,}", " ", unit).strip(" -")
    if kind == "section":
        unit = re.sub(r"^CIF[- ]?", "", unit).strip(" -")
    return unit


def parseUnits(meet_name, div_title):
    """[(kind, unit)] independent facts from one meet+race title pair.
    Empty when the championship gate fails."""
    name = (meet_name or "").strip()
    if not name or not _CHAMP_RX.search(name) or _NEVER_RX.search(name):
        return []
    facts = []
    for kind, rx, grp in _UNIT_RULES:
        m = rx.search(name)
        if not m:
            continue
        # an empty capture (bare "Regionals") keeps the KIND as the unit
        unit = (m.group(grp).strip() if grp and m.group(grp)
                else ("STATE" if kind == "state" else kind.upper()))
        unit = _cleanUnit(kind, unit.upper()) or kind.upper()
        if kind == "league" and (unit in _NOT_A_LEAGUE
                                 or _ASSOC_RX.search(unit)):
            continue
        # "CIF State ..." is the state meet, not a section named STATE
        if kind == "section" and unit in ("STATE", "CIF"):
            continue
        facts.append((kind, unit))
    # ★ PEEL GLUED CLASS PREFIXES off unit names ("2A EVERGREEN",
    #   "4A KINGCO"): the unit is the rest, and a SINGLE peeled token is
    #   class evidence -- a multi-class combine ("2A & 1A KINGCO") names a
    #   shared meet and votes no class at all.
    peeled, fixed = [], []
    for kind, unit in facts:
        if kind in ("league", "county", "section"):
            m = re.match(r"^([1-6]A(?:\s*[&/]\s*[1-6]A)*)\s+(.+)$", unit)
            if m:
                unit = m.group(2).strip()
                if "&" not in m.group(1) and "/" not in m.group(1):
                    peeled.append(("class", m.group(1)))
        fixed.append((kind, unit))
    facts = fixed + peeled
    # last resort: a bare state name in a championship title ("Michigan
    # Meet of Champions", "Nebraska Championship Meet") is the state
    # series -- but ONLY when no real unit matched, so "Mississippi
    # Valley Conference" stays a league.
    if not any(k != "class" for k, _ in facts) \
            and _STATE_NAME_RX.search(name):
        facts.append(("state", "STATE"))
    # ! class/div from BOTH the meet name and the race/division title --
    #   the token survives in whichever one kept it.
    for src in (name, div_title or ""):
        # a multi-class combine in the NAME ("2A & 1A KingCo") votes no
        # class -- the school could be either; the race title still may
        if src is name and re.search(r"[1-6]A\s*[&/]\s*[1-6]A", src):
            continue
        for rx in _CLASS_RX:
            m = rx.search(src)
            if m:
                facts.append(("class", m.group(1).upper()))
                break
        else:
            continue
        break
    return facts


# ---- the votes -------------------------------------------------------- #
def _rows(cur, sport, lo, hi):
    """DISTINCT (school, season, meet_name, div_title, state) attendance
    rows from late-season meets, both feeds."""
    if sport == "XC":
        cur.execute("""
            SELECT DISTINCT r.school, substr(r.date, 1, 4) AS yr,
                   m.meet_name, m.division, m.state
            FROM results r
            JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                        AND m.source = r.source
            WHERE COALESCE(TRIM(r.school), '') <> ''
              AND substr(r.date, 6, 2)::int BETWEEN %(lo)s AND %(hi)s
              AND m.meet_name ~* 'champ|finals|meet of champions'
        """, {"lo": lo, "hi": hi})
        yield from cur.fetchall()
        cur.execute("""
            SELECT DISTINCT r.school, substr(r.date, 1, 4) AS yr,
                   mt.meet_name, NULL::text AS division, mt.state
            FROM results r
            JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                               AND mt.source = 'tfrrs'
            WHERE r.source = 'tfrrs'
              AND COALESCE(TRIM(r.school), '') <> ''
              AND substr(r.date, 6, 2)::int BETWEEN %(lo)s AND %(hi)s
              AND mt.meet_name ~* 'champ|finals|meet of champions'
        """, {"lo": lo, "hi": hi})
        yield from cur.fetchall()
    else:
        cur.execute("""
            SELECT DISTINCT r.school, substr(r.date, 1, 4) AS yr,
                   m.meet_name, m.division, m.state
            FROM results_tf r
            JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                           AND m.event_id = r.event_id AND m.source = r.source
            WHERE COALESCE(TRIM(r.school), '') <> ''
              AND substr(r.date, 6, 2)::int BETWEEN %(lo)s AND %(hi)s
              AND m.meet_name ~* 'champ|finals|meet of champions'
        """, {"lo": lo, "hi": hi})
        yield from cur.fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=("XC", "TF"), default="XC")
    ap.add_argument("--school", default=None,
                    help="print one school's full unit evidence")
    ap.add_argument("--state", default=None,
                    help="restrict the sample table to one state")
    ap.add_argument("--unparsed", action="store_true",
                    help="championship-gated names that yielded NO unit")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    lo, hi = _WINDOW[args.sport]

    # ★ VOTES KEY ON (school, MEET'S STATE) -- the LCD meet is local, so
    #   the meet's own state cleanly splits string collisions ("Highland"
    #   with a Mississippi league and a CIF section is two Highlands).
    # ★ AND EVERY VOTE CARRIES ITS SEASON, because the owner's rule is
    #   "most recent is the end-all": resolution takes the latest season's
    #   verdict; older seasons are provenance, never the answer.
    votes = defaultdict(lambda: defaultdict(Counter))   # key -> kind -> (unit, yr)
    seasons = defaultdict(set)
    name_hits, name_miss = Counter(), Counter()
    n_rows = n_excluded = 0

    with getConn() as conn, conn.cursor() as cur:
        for school, yr, meet_name, div_title, state in _rows(
                cur, args.sport, lo, hi):
            n_rows += 1
            st = (state or "").upper()
            if args.state and st != args.state.upper():
                continue
            # excluded (club/foreign/shoe-company postseason) is not a
            # parser MISS -- keep the --unparsed list pure signal
            if meet_name and _NEVER_RX.search(meet_name):
                n_excluded += 1
                continue
            facts = parseUnits(meet_name, div_title)
            if facts:
                name_hits[meet_name] += 1
                key = (school, st)
                for kind, unit in facts:
                    votes[key][kind][(unit, yr)] += 1
                seasons[key].add(yr)
            else:
                name_miss[meet_name] += 1

    print(f"\n  {args.sport} late-season window months {lo}-{hi}: "
          f"{n_rows:,} (school, championship-meet) attendance rows")
    print(f"  parsed into units: {sum(name_hits.values()):,} rows across "
          f"{len(name_hits):,} distinct meet names")
    print(f"  excluded (club/foreign/shoe postseason): {n_excluded:,} rows")
    print(f"  championship-gated but NO unit extracted: "
          f"{sum(name_miss.values()):,} rows, {len(name_miss):,} names")

    if args.unparsed:
        print("\n  the unparsed names, biggest first (parser work lives "
              "here):")
        for name, n in name_miss.most_common(args.limit):
            print(f"    {n:>6,}  {name[:70]}")
        return

    # ---- recency resolution: latest season decides; conflict only when
    #      the LATEST season itself carries two corroborated units.
    def current(counter):
        """(unit, latest_yr, conflict) or None."""
        if not counter:
            return None
        latest = max(yr for (_u, yr) in counter)
        in_latest = Counter()
        for (u, yr), n in counter.items():
            if yr == latest:
                in_latest[u] += n
        top = in_latest.most_common()
        conflict = len(top) > 1 and top[1][1] >= 2
        return top[0][0], latest, conflict

    def history(counter, skip_unit):
        """'earlier: BVAL (to 2015), ...' for --school provenance."""
        last = {}
        for (u, yr), _n in counter.items():
            if u != skip_unit:
                last[u] = max(last.get(u, ""), yr)
        return ", ".join(f"{u} (to {y})"
                         for u, y in sorted(last.items(),
                                            key=lambda kv: -int(kv[1]))[:4])

    if args.school:
        pat = args.school.lower()
        for (school, st) in sorted(votes):
            if pat not in school.lower():
                continue
            key = (school, st)
            print(f"\n  {school} ({st or '??'})  seasons "
                  f"{min(seasons[key])}-{max(seasons[key])}")
            for kind in ("league", "section", "district", "county",
                         "region", "state", "class"):
                c = votes[key].get(kind)
                if not c:
                    continue
                cur_u, yr, conflict = current(c)
                n_total = sum(n for (u, _), n in c.items() if u == cur_u)
                line = f"    {kind:<9} {cur_u}  ({yr}, {n_total} votes)"
                if conflict:
                    line += "   !! CONFLICT in latest season"
                past = history(c, cur_u)
                if past:
                    line += f"   earlier: {past}"
                print(line)
        return

    # the sample table: current (most-recent-season) verdicts only
    print(f"\n    {'school':<28} {'st':<3} {'league':<18} {'section':<14} "
          f"{'class':<7} asof")
    print("    " + "-" * 82)
    ranked = sorted(votes.items(),
                    key=lambda kv: -sum(sum(c.values())
                                        for c in kv[1].values()))
    for (school, st), kinds in ranked[:args.limit]:
        cells, asof, flag = {}, "", " "
        for kind in ("league", "section", "class"):
            got = current(kinds.get(kind, Counter()))
            if got:
                cells[kind] = got[0]
                asof = max(asof, got[1])
                if got[2]:
                    flag = "!"
        print(f"    {school[:28]:<28} {st:<3} "
              f"{cells.get('league', '')[:18]:<18} "
              f"{cells.get('section', '')[:14]:<14} "
              f"{cells.get('class', '')[:6]:<6}{flag} {asof}")
    print("\n  Current = the most recent season's verdict (owner's rule); "
          "! = that latest\n  season itself holds two corroborated units "
          "(true ambiguity -- eyes). Use\n  --unparsed for parser misses, "
          "--school NAME for full provenance.")


if __name__ == "__main__":
    main()
