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
    r"champ|meet of champions|finals?\b|\bstate meet\b", re.I)
_NEVER_RX = re.compile(
    r"invit|classic|festival|preview|opener|relays\b|scrimmage|jamboree|"
    r"time trial|last chance|qualifier meet|carnival|series\b", re.I)

# class/div tokens, meet name or race title:  5A, AAA, Class B, Division
# III, D3, Group 2 (NJ), Open Division
_CLASS_RX = [
    re.compile(r"\b([1-9]-?A{1,4})\b"),
    re.compile(r"\bclass\s+([A-D]{1,4}|[1-9][A-D]?)\b", re.I),
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

_UNIT_RULES = [
    # "Meet of Champions" is NOT state evidence -- sections hold MoCs too
    # (NCS MoC); the unit comes from the acronym beside it.
    ("state",    re.compile(r"\b(state|all-state)\b", re.I), None),
    ("section",  re.compile(r"\b([\w .&'-]+?)\s+section(?:al)?s?\b", re.I), 1),
    ("section",  re.compile(r"\b(NCS|CCS|CIF-?SS|SJS|SDS)\b"), 1),
    ("district", re.compile(r"\bdistrict\s*([\dA-Z-]{0,6})\b", re.I), 1),
    ("region",   re.compile(r"\bregion(?:al)?s?\s*([\dA-Z-]{0,4})\b", re.I),
     1),
    ("league",   re.compile(r"\b([\w .&'-]+?)\s+league\b", re.I), 1),
    ("league",   re.compile(r"\b([\w .&'-]+?)\s+conference\b", re.I), 1),
    # bare all-caps acronym before Champ/Finals = a league (EBAL, WCAL) --
    # scoped (?i:) so "Championships" matches while the acronym stays
    # case-sensitive; the exclusion set stops section/state double-votes
    ("league",   re.compile(r"\b([A-Z]{3,6})\s+(?i:champ|finals?)"), 1),
]


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
        unit = (m.group(grp).strip() if grp and m.group(grp)
                else ("STATE" if kind == "state" else ""))
        unit = unit.upper() if unit else kind.upper()
        if kind == "league" and unit in _NOT_A_LEAGUE:
            continue
        facts.append((kind, unit))
    # ! class/div from BOTH the meet name and the race/division title --
    #   the token survives in whichever one kept it.
    for src in (name, div_title or ""):
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

    # (school) -> kind -> Counter(unit); plus per-name parse bookkeeping
    votes = defaultdict(lambda: defaultdict(Counter))
    seasons = defaultdict(set)
    name_hits, name_miss = Counter(), Counter()
    n_rows = 0

    with getConn() as conn, conn.cursor() as cur:
        for school, yr, meet_name, div_title, state in _rows(
                cur, args.sport, lo, hi):
            n_rows += 1
            if args.state and (state or "").upper() != args.state.upper():
                continue
            facts = parseUnits(meet_name, div_title)
            if facts:
                name_hits[meet_name] += 1
                for kind, unit in facts:
                    votes[school][kind][unit] += 1
                seasons[school].add(yr)
            else:
                name_miss[meet_name] += 1

    print(f"\n  {args.sport} late-season window months {lo}-{hi}: "
          f"{n_rows:,} (school, championship-meet) attendance rows")
    print(f"  parsed into units: {sum(name_hits.values()):,} rows across "
          f"{len(name_hits):,} distinct meet names")
    print(f"  championship-gated but NO unit extracted: "
          f"{sum(name_miss.values()):,} rows, {len(name_miss):,} names")

    if args.unparsed:
        print("\n  the unparsed names, biggest first (parser work lives "
              "here):")
        for name, n in name_miss.most_common(args.limit):
            print(f"    {n:>6,}  {name[:70]}")
        return

    if args.school:
        pat = args.school.lower()
        for school in sorted(votes):
            if pat not in school.lower():
                continue
            print(f"\n  {school}  (seasons: "
                  f"{', '.join(sorted(seasons[school]))})")
            for kind in ("league", "conference", "section", "district",
                         "region", "state", "class"):
                if votes[school].get(kind):
                    top = votes[school][kind].most_common(4)
                    line = ", ".join(f"{u} ({n})" for u, n in top)
                    flag = ("   !! CONFLICT" if kind == "class"
                            and len([1 for _, n in top if n >= 2]) > 1
                            else "")
                    print(f"    {kind:<9} {line}{flag}")
        return

    # the sample table: schools with the most corroborated units
    print(f"\n    {'school':<34} {'league':<18} {'section':<12} "
          f"{'class':<8} votes")
    print("    " + "-" * 78)
    ranked = sorted(votes.items(),
                    key=lambda kv: -sum(sum(c.values())
                                        for c in kv[1].values()))
    shown = 0
    for school, kinds in ranked:
        if shown >= args.limit:
            break
        lg = (kinds["league"].most_common(1) or [("", 0)])[0]
        sec = (kinds["section"].most_common(1) or [("", 0)])[0]
        cl = (kinds["class"].most_common(1) or [("", 0)])[0]
        total = sum(sum(c.values()) for c in kinds.values())
        cl_flag = ("!" if len([1 for _, n in
                               kinds["class"].most_common(3)
                               if n >= 2]) > 1 else " ")
        print(f"    {school[:34]:<34} {lg[0][:18]:<18} {sec[0][:12]:<12} "
              f"{cl[0][:7]:<7}{cl_flag} {total:>5}")
        shown += 1
    print("\n  ! beside a class = conflicting corroborated class votes "
          "(realignment or\n  a parse bug -- eyes needed). Rerun with "
          "--unparsed to see what the parser\n  misses, --school NAME for "
          "one school's full evidence.")


if __name__ == "__main__":
    main()
