"""
build_college_directory.py -- every NCAA and NAIA institution's state, from
Wikipedia's four lists, into `college_directory` (issue 211). Runs on the
server (this sandbox cannot reach Wikipedia); once, and again when the
lists change. build_school_identity (10b) reads it: a school whose name
matches is placed in its directory state, whatever its athletes' home
states say -- a college team races away most weekends, so the data-side
rule (where its athletes race most) was wrong for BYU and the like.

    /srv/venv/bin/python scripts/build_college_directory.py --check   # parse, print, write nothing
    /srv/venv/bin/python scripts/build_college_directory.py --write

    college_directory (name_norm PK, name, state, division, source)

Matching is by NORMALISED name (normName): lower case, punctuation out,
"university / college / the / of / at" out, plus the ALIASES below for
the short names the feeds use ("BYU"). Exact matches only: a name the
directory does not know keeps the data rule.
"""
import argparse
import html
import re
import sys
import urllib.request

sys.path.insert(0, "scripts")

LISTS = {
    "D1":   "https://en.wikipedia.org/wiki/List_of_NCAA_Division_I_institutions",
    "D2":   "https://en.wikipedia.org/wiki/List_of_NCAA_Division_II_institutions",
    "D3":   "https://en.wikipedia.org/wiki/List_of_NCAA_Division_III_institutions",
    "NAIA": "https://en.wikipedia.org/wiki/List_of_NAIA_institutions",
}

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "washington, d.c.": "DC", "washington d.c.": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
    "british columbia": "BC", "alberta": "AB", "ontario": "ON",
}

# the short names the feeds use -> the directory's normalised name
ALIASES = {
    # the Wisconsin system as the feeds spell it (owner, 2026-09-07, Joey
    # Sullivan: 'UW-La Crosse' and 'Wis.-La Crosse' were unknown to the
    # directory, so the college rules never fired for him)
    "uw la crosse": "wisconsin la crosse", "wis la crosse": "wisconsin la crosse",
    "uw eau claire": "wisconsin eau claire", "wis eau claire": "wisconsin eau claire",
    "uw oshkosh": "wisconsin oshkosh", "wis oshkosh": "wisconsin oshkosh",
    "uw stevens point": "wisconsin stevens point", "wis stevens point": "wisconsin stevens point",
    "uw whitewater": "wisconsin whitewater", "wis whitewater": "wisconsin whitewater",
    "uw platteville": "wisconsin platteville", "wis platteville": "wisconsin platteville",
    "uw river falls": "wisconsin river falls", "wis river falls": "wisconsin river falls",
    "uw stout": "wisconsin stout", "wis stout": "wisconsin stout",
    "uw superior": "wisconsin superior", "wis superior": "wisconsin superior",
    "uw milwaukee": "wisconsin milwaukee", "wis milwaukee": "wisconsin milwaukee",
    "uw parkside": "wisconsin parkside", "wis parkside": "wisconsin parkside",
    "uw green bay": "wisconsin green bay", "wis green bay": "wisconsin green bay",
    "uw madison": "wisconsin", "wisconsin madison": "wisconsin",
    "byu": "brigham young", "ucla": "california los angeles",
    "usc": "southern california", "unc": "north carolina",
    "lsu": "louisiana state", "tcu": "texas christian", "smu": "southern methodist",
    "bu": "boston", "nyu": "new york", "mit": "massachusetts institute technology",
    "rpi": "rensselaer polytechnic institute", "wpi": "worcester polytechnic institute",
    "umass": "massachusetts", "umass amherst": "massachusetts", "uconn": "connecticut",
    "utep": "texas el paso", "unlv": "nevada las vegas", "uab": "alabama birmingham",
    "ucf": "central florida", "usf": "south florida", "vcu": "virginia commonwealth",
    "vmi": "virginia military institute", "ucsb": "california santa barbara",
    "uc davis": "california davis", "uc irvine": "california irvine",
    "uc riverside": "california riverside", "uc san diego": "california san diego",
    "ucsd": "california san diego", "cal": "california berkeley",
    "cal poly": "california polytechnic state", "ole miss": "mississippi",
    "penn": "pennsylvania", "pitt": "pittsburgh", "cuny": "city new york",
    "unh": "new hampshire", "uri": "rhode island", "uva": "virginia",
    "umbc": "maryland baltimore county", "uic": "illinois chicago",
    "utsa": "texas san antonio", "utrgv": "texas rio grande valley",
    "fiu": "florida international", "fau": "florida atlantic",
    "etsu": "east tennessee state", "wku": "western kentucky",
    "unlv": "nevada las vegas", "unf": "north florida", "uncw": "north carolina wilmington",
    "unc charlotte": "north carolina charlotte", "unc asheville": "north carolina asheville",
    "unc greensboro": "north carolina greensboro", "uncg": "north carolina greensboro",
    "cal state fullerton": "california state fullerton",
    "cal state northridge": "california state northridge",
    "csun": "california state northridge", "cal state la": "california state los angeles",
    "sdsu": "san diego state", "sjsu": "san jose state", "asu": "arizona state",
    "osu": "ohio state", "psu": "penn state", "msu": "michigan state",
    "iu": "indiana", "ku": "kansas", "ou": "oklahoma", "cu": "colorado",
    "wsu": "washington state", "uw": "washington", "uo": "oregon",
    "uvu": "utah valley", "usu": "utah state", "unm": "new mexico",
    "nau": "northern arizona", "unlv": "nevada las vegas",
}

_STRIP = re.compile(r"\b(the|university|college|of|at|in|and|state university)\b")


def lookup(known, name):
    """The directory entry for a feed's spelling of a school, or None.

    ★ THREE STEPS, IN ORDER (issue 304, owner: "D3 filter is better but
      not perfect"): the normalised name exactly; then the feed's short
      form with its punctuation collapsed ("Wis.-La Crosse" -> the tokens
      wis, la, crosse); then the ONE directory entry whose tokens contain
      every token of the feed's name, with a common abbreviation expanded
      (wis -> wisconsin, st -> saint, cal -> california). Ambiguity is a
      miss: "Wesleyan" alone matches five schools and gets none.
    `known` maps name_norm -> anything; the value is returned."""
    if not name:
        return None
    key = normName(name)
    if key in known:
        return known[key]
    toks = [_ABBR.get(t, t) for t in key.split() if t]
    if not toks:
        return None
    hits = []
    for k in known:
        kt = set(k.split())
        if all(t in kt for t in toks):
            hits.append(k)
    if len(hits) == 1:
        return known[hits[0]]
    return None


_ABBR = {"wis": "wisconsin", "st": "saint", "cal": "california", "mt": "mount",
         "ft": "fort", "no": "north", "so": "south", "univ": "", "u": "",
         "tech": "technology", "poly": "polytechnic", "penn": "pennsylvania",
         "ill": "illinois", "mich": "michigan", "minn": "minnesota", "wash": "washington",
         "colo": "colorado", "conn": "connecticut", "mass": "massachusetts",
         "tenn": "tennessee", "ky": "kentucky", "fla": "florida", "ga": "georgia",
         "va": "virginia", "nc": "north carolina", "sc": "south carolina"}


def normName(name):
    s = html.unescape(name or "").lower()
    s = re.sub(r"\[[^\]]*\]", "", s)                    # footnotes
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _STRIP.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


# a tag, with its attribute values allowed to contain '>' -- Wikipedia's
# state cells are <abbr data-mw='{...&lt;/span>...}'>UT</abbr>, and a
# naive <[^>]+> stopped inside the attribute and left half of it as text
# (the BYU row read as Utah</span>... and never matched a state, 215)
_TAG = re.compile(r"<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.S)
_CELL = re.compile(r"<t[hd](?:[^>\"']|\"[^\"]*\"|'[^']*')*>(.*?)</t[hd]>", re.S)


def _cells(row_html):
    out = []
    for c in _CELL.findall(row_html):
        c = re.sub(r"<sup.*?</sup>", "", c, flags=re.S)
        c = _TAG.sub("", c)
        out.append(html.unescape(c).strip())
    return out


def parseList(page_html):
    """[(name, common, state)] from every wikitable that has a name and a
    state (or location) column."""
    rows = []
    for table in re.findall(r"<table[^>]*wikitable[^>]*>(.*?)</table>", page_html, flags=re.S):
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.S)
        if not trs:
            continue
        head = [h.lower() for h in _cells(trs[0])]
        def col(*names):
            for i, h in enumerate(head):
                if any(h.startswith(n) for n in names):
                    return i
            return None
        i_name = col("institution", "school", "team", "name", "member")
        i_common = col("common name", "common", "short")
        i_state = col("state", "province")
        i_loc = col("location", "city")
        if i_name is None or (i_state is None and i_loc is None):
            continue
        for tr in trs[1:]:
            c = _cells(tr)
            if len(c) <= i_name:
                continue
            name = c[i_name]
            common = c[i_common] if i_common is not None and i_common < len(c) else ""
            st = None
            if i_state is not None and i_state < len(c):
                st = c[i_state].strip()
            elif i_loc is not None and i_loc < len(c):
                st = c[i_loc].split(",")[-1].strip()
            if not name or not st:
                continue
            st_code = STATES.get(st.lower()) or (st.upper() if len(st) == 2 else None)
            if st_code:
                rows.append((name, common, st_code))
    return rows


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "racecast directory build (contact: site owner)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--find", default=None,
                    help="print every parsed row whose name contains this "
                         "(case-insensitive), and every table head")
    args = ap.parse_args()
    entries = {}
    dropped = {}
    for div, url in LISTS.items():
        try:
            page = fetch(url)
        except Exception as exc:                          # noqa: BLE001
            print(f"  {div}: fetch failed: {exc}")
            continue
        if args.find:
            for k, table in enumerate(re.findall(r"<table[^>]*wikitable[^>]*>(.*?)</table>", page, flags=re.S)):
                trs = re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.S)
                print(f"    {div} table {k}: {len(trs) - 1} rows, head {_cells(trs[0]) if trs else []}")
                for tr in trs:
                    if args.find.lower() in tr.lower():
                        compact = re.sub(r"\s+", " ", tr)[:700]
                        print(f"      raw row ({len(_cells(tr))} cells): {compact}")
                        print(f"      cells: {_cells(tr)}")
        rows = parseList(page)
        print(f"  {div}: {len(rows):,} institutions parsed")
        for name, common, st in rows:
            if args.find and args.find.lower() in (name + " " + common).lower():
                print(f"    {div}: {name!r} / {common!r} -> {st}  (norm {normName(name)!r}, {normName(common)!r})")
            for key in {normName(name), normName(common)} - {""}:
                if key in entries and entries[key][1] != st:
                    dropped[key] = (entries[key], (name, st, div))
                    entries[key] = (name, None, div)      # ambiguous: two states
                elif key not in entries:
                    entries[key] = (name, st, div)
    if args.find:
        for key, (a, b) in dropped.items():
            if args.find.lower() in key:
                print(f"    ambiguous {key!r}: {a} vs {b}")
    ambiguous = sum(1 for v in entries.values() if v[1] is None)
    usable = {k: v for k, v in entries.items() if v[1]}
    print(f"  directory: {len(usable):,} names with a state ({ambiguous} ambiguous dropped)")
    for probe in ("byu", "tufts", "stanford", "kingston", "washington", "colorado mesa"):
        print(f"    {probe:<16} -> {usable.get(normName(probe), ('-', '-', '-'))[1]}")
    if args.check and not args.write:
        return
    if not args.write:
        print("  (--write to store college_directory)")
        return
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS college_directory_new")
        cur.execute("""
            CREATE TABLE college_directory_new (
                name_norm text PRIMARY KEY, name text NOT NULL,
                state text NOT NULL, division text, source text)
        """)
        cur.executemany(
            "INSERT INTO college_directory_new VALUES (%s, %s, %s, %s, 'wikipedia')",
            [(k, v[0], v[1], v[2]) for k, v in usable.items()])
        cur.execute("DROP TABLE IF EXISTS college_directory")
        cur.execute("ALTER TABLE college_directory_new RENAME TO college_directory")
        conn.commit()
    print(f"  college_directory: {len(usable):,} rows written")


if __name__ == "__main__":
    main()
