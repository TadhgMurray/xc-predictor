#!/usr/bin/env python3
"""
anet_teams.py -- one call per team to Athletic.net's TeamNav/Team, which
answers three separate problems at once (305, and the address book).

    python scripts/anet_teams.py --write --rate 1.0

    GET /api/v1/TeamNav/Team?team=<id>&sport=xc&season=<year>
    -> team: {IDTeam, Name, TeamCode, Level, City, State, ZipCode, Country,
              RegionID, MascotUrl, Website, WebsiteSport, ...}

  MascotUrl     the crest. Hosted on lh3.googleusercontent.com -- GOOGLE's
                bandwidth, not anet's -- so the image costs them nothing.
  WebsiteSport  the ATHLETICS site, handed over directly. This is the thing
                scrape_school_logos.py was reading home pages to guess at,
                and it is the address book's real fix: ~11k addresses from
                Wikidata against one per team here.
  ZipCode/City/State/Level  where the school is, for elevation and for
                school_identity to check itself against.

No crawl and no search: every result row we have already carries anet's
TeamID, so the team list comes from our own database. One API call per
school, once. The images come from Google.

⚠ ROBOTS. The default obeys anet's robots.txt like everything else here,
  and if it disallows /api/ this job will do nothing and say so.
  --ignore-robots overrides that. It is a deliberate flag with no default
  because it is a decision about someone else's site, not a setting.

Athlete photos are NOT taken and will not be: most of the people are
minors. That slot is the athlete's or the coach's to fill (283).
"""
import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scrape_school_logos import (            # noqa: E402
    DDL, Manners, _tableExists, ensureTable, markShared, normalise, record,
    writeFile)

# ! THE CLIENT HEADER anet's OWN SITE SENDS, copied from scripts/scraper.py,
#   which has worked against /api/v1/Meet/GetResultsData3 for a year. The
#   first run of this script got 200 application/json back with no team in
#   it, which is what an API answers when it does not recognise the caller.
HEADERS = {"anet-appinfo": "web:web:0:240", "Accept": "application/json"}

API = "https://www.athletic.net/api/v1/TeamNav/Team?team={team}&sport={sport}&season={season}"
CORE = "https://www.athletic.net/api/v1/TeamHome/GetTeamCore?teamId={team}&sport={sport}&year={season}"
ABORT_AFTER = 20

TEAM_DDL = """
CREATE TABLE IF NOT EXISTS anet_team (
    team_id      int PRIMARY KEY,
    school       text,
    state        text,
    name         text,
    team_code    text,
    level        int,
    city         text,
    anet_state   text,
    zip          text,
    country      text,
    region_id    int,
    mascot       text,
    mascot_url   text,
    website      text,
    website_sport text,
    has_indoor   boolean,
    first_season int,
    last_season  int,
    n_seasons    int,
    fetched      date NOT NULL DEFAULT current_date)
"""

# ★ anet's UNIT HIERARCHY, one row per level, per sport.
#
#   [{id: 167952, b: 79,  name: " United States"},
#    {id: 168416, b: 2,   name: "High School"},
#    {id: 168546, b: 278, name: "California"},
#    {id: 168618, b: 319, name: "North Coast"},   -- our NCS
#    {id: 168639, b: 334, name: "Valley"},        -- our Tri-Valley Area
#    {id: 168642, b: 337, name: "East Bay Ath."}] -- our EBAL
#
#   ⚠ THE NAMES ARE TRUNCATED AND THERE IS NO COMPETITIVE DIVISION. So
#     this is not a replacement for school_unit, which infers both from
#     championship attendance. What it IS is an exact, machine-readable
#     structure -- and `b` looks like the id that survives a season while
#     `id` is re-allocated, so a unit only ever has to be NAMED once.
#     scripts/anet_units.py learns those names from the units we already
#     have rather than parsing anet's; see its header.
#
#   XC and TF disagree (owner: a team can be in an area for track and not
#   for cross country), so sport is in the key and the reader unions.
DIV_DDL = """
CREATE TABLE IF NOT EXISTS anet_division (
    team_id  int  NOT NULL,
    sport    text NOT NULL,
    base_id  int  NOT NULL,
    div_id   int,
    depth    int,
    name     text,
    gender   text,
    custom   boolean NOT NULL DEFAULT false,
    fetched  date NOT NULL DEFAULT current_date,
    PRIMARY KEY (team_id, sport, base_id))
"""


def teams(cur, limit=None, state=None, redo=False):
    """[(school, state, team_id)] -- the anet team each school's athletes
    actually raced under, biggest programme first.

    Modal per (school, HOME STATE), not per school string: two real schools
    share the name "Kingston" and anet gives them two ids, which is the
    split school_identity already draws."""
    for ddl in (DDL, TEAM_DDL, DIV_DDL):
        ensureTable(cur, ddl)
    if not _tableExists(cur, "school_identity"):
        raise SystemExit("school_identity is missing; run pipeline step 10b first")
    home = ("LEFT JOIN person_home_state h ON h.person_id = t.person_id"
            if _tableExists(cur, "person_home_state") else "")
    state_expr = "COALESCE(h.state, '')" if home else "''"
    done = "" if redo else "AND a.team_id IS NULL"
    where_state = "AND si.state = %(state)s" if state else ""
    lim = "LIMIT %(limit)s" if limit else ""
    cur.execute(f"""
        WITH t AS (
            SELECT school, team_id, person_id FROM results
            WHERE  team_id IS NOT NULL AND school IS NOT NULL
            UNION ALL
            SELECT school, team_id, person_id FROM results_tf
            WHERE  team_id IS NOT NULL AND school IS NOT NULL
        ), counted AS (
            SELECT t.school, {state_expr} AS state, t.team_id, count(*) AS n
            FROM   t {home}
            GROUP  BY 1, 2, 3
        ), modal AS (
            SELECT DISTINCT ON (school, state) school, state, team_id
            FROM   counted ORDER BY school, state, n DESC
        )
        SELECT si.school, si.state, modal.team_id
        FROM   school_identity si
        JOIN   modal ON modal.school = si.school AND modal.state = si.state
        LEFT   JOIN anet_team a ON a.team_id = modal.team_id
        WHERE  si.n_athletes >= 3 {done} {where_state}
        ORDER  BY si.n_athletes DESC
        {lim}
    """, {"limit": limit, "state": (state or "").upper()})
    return [tuple(r[k] for k in ("school", "state", "team_id"))
            if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]


def parseTeam(raw):
    """The `team` object plus the few things that ride beside it, or None.

    ⚠ THE TWO ENDPOINTS NAME THE ID DIFFERENTLY. TeamNav/Team returns
      team.ID, GetTeamCore returns team.IDTeam, and requiring IDTeam is
      what made the first run report 200 application/json with nothing in
      it. Both are normalised to IDTeam here.

    They also carry different fields, which is why both are fetched and
    merged: divisions, customDivisions, Mascot and colors are TeamNav's;
    WebsiteSport, TeamCode, RegionID and the season list are GetTeamCore's.
    """
    try:
        got = json.loads(raw.decode("utf-8", "replace"))
    except Exception:                                     # noqa: BLE001
        return None
    team = (got or {}).get("team")
    if not isinstance(team, dict):
        return None
    tid = _int(team.get("IDTeam")) or _int(team.get("ID"))
    if not tid:
        return None
    team = dict(team, IDTeam=tid)
    seasons = [y for y in ((got.get("seasonInfo") or {}).get("seasons") or [])
               if _int(y)]
    if seasons:
        team["_seasons"] = sorted(_int(y) for y in seasons)
    custom = [d for d in (got.get("customDivisions") or [])
              if isinstance(d, dict) and _int(d.get("IDDivision"))]
    if custom:
        team["_custom"] = custom
    return team


def mergeTeams(*teams):
    """One team from however many payloads answered; later ones fill gaps
    rather than overwrite, since both endpoints agree where they overlap."""
    out = {}
    for t in teams:
        for k, v in (t or {}).items():
            if v is not None and v != "" and out.get(k) in (None, ""):
                out[k] = v
    return out or None


def parseDivisions(raw):
    """[(depth, base_id, div_id, name, gender)] from whichever payload
    carries `divisions`. Array order is the hierarchy, widest first, so
    the index is the depth."""
    try:
        got = json.loads(raw.decode("utf-8", "replace"))
    except Exception:                                     # noqa: BLE001
        return []
    divs = (got or {}).get("divisions")
    if not isinstance(divs, list):
        divs = ((got or {}).get("team") or {}).get("divisions")
    out = []
    for depth, d in enumerate(divs or []):
        if not isinstance(d, dict):
            continue
        base = _int(d.get("b"))
        if base is None:
            continue
        out.append((depth, base, _int(d.get("id")),
                    (d.get("name") or "").strip(), d.get("gender")))
    return out


def storeDivisions(cur, team_id, sport, divs, custom=()):
    """The main tree, plus anet's `customDivisions` -- an extra affiliation
    off the hierarchy ("ECAC Div III" for Tufts) that the tree misses. No
    depth, so it is stored flagged rather than as a rung."""
    rows = [(team_id, sport, b, i, d, n, g, False) for d, b, i, n, g in divs]
    rows += [(team_id, sport, _int(c.get("IDDivision")),
              _int(c.get("IDDivision")), None,
              (c.get("DivName") or "").strip(), None, True)
             for c in (custom or [])]
    cur.executemany("""
        INSERT INTO anet_division (team_id, sport, base_id, div_id, depth,
                                   name, gender, custom, fetched)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, current_date)
        ON CONFLICT (team_id, sport, base_id) DO UPDATE
        SET div_id = EXCLUDED.div_id, depth = EXCLUDED.depth,
            name = EXCLUDED.name, gender = EXCLUDED.gender,
            custom = EXCLUDED.custom, fetched = current_date
    """, rows)
    return len(rows)


def mascotUrls(team):
    """The crest to try, best first. MascotUrl is protocol-relative, and
    lh3.googleusercontent.com serves a sized copy for an =sN suffix -- so
    ask for 512 and fall back to whatever the original is."""
    url = (team.get("MascotUrl") or "").strip()
    if not url:
        return []
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("http"):
        return []
    return [f"{url}=s512", url] if "googleusercontent." in url else [url]


def storeTeam(cur, school, state, team):
    cur.execute("""
        INSERT INTO anet_team (team_id, school, state, name, team_code, level,
                               city, anet_state, zip, country, region_id,
                               mascot, mascot_url, website, website_sport,
                               has_indoor, first_season, last_season, n_seasons,
                               fetched)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, current_date)
        ON CONFLICT (team_id) DO UPDATE SET
            school = EXCLUDED.school, state = EXCLUDED.state,
            name = EXCLUDED.name, team_code = EXCLUDED.team_code,
            level = EXCLUDED.level, city = EXCLUDED.city,
            anet_state = EXCLUDED.anet_state, zip = EXCLUDED.zip,
            country = EXCLUDED.country, region_id = EXCLUDED.region_id,
            mascot = EXCLUDED.mascot, mascot_url = EXCLUDED.mascot_url,
            website = EXCLUDED.website, website_sport = EXCLUDED.website_sport,
            has_indoor = EXCLUDED.has_indoor,
            first_season = EXCLUDED.first_season,
            last_season = EXCLUDED.last_season, n_seasons = EXCLUDED.n_seasons,
            fetched = current_date
    """, (team.get("IDTeam"), school, state, team.get("Name"),
          team.get("TeamCode"), _int(team.get("Level")), team.get("City"),
          team.get("State"), team.get("ZipCode"), team.get("Country"),
          _int(team.get("RegionID")), team.get("Mascot"), team.get("MascotUrl"),
          team.get("Website"), team.get("WebsiteSport"),
          team.get("hasIndoor"),
          (team.get("_seasons") or [None])[0],
          (team.get("_seasons") or [None])[-1],
          len(team.get("_seasons") or []) or None))


def storeAddress(cur, school, state, team):
    """WebsiteSport into school_website, so scrape_school_logos can work the
    athletics site directly instead of guessing at it from a home page.
    Never overwrites an address a person set by hand."""
    url = (team.get("WebsiteSport") or team.get("Website") or "").strip()
    if not url.startswith("http"):
        return False
    cur.execute("""
        INSERT INTO school_website (school, state, url, source, matched, seen)
        VALUES (%s, %s, %s, 'anet', %s, current_date)
        ON CONFLICT (school, state) DO UPDATE
        SET url = EXCLUDED.url, source = 'anet', matched = EXCLUDED.matched,
            seen = current_date
        WHERE school_website.source IS DISTINCT FROM 'manual'
    """, (school, state, url, team.get("Name")))
    return True


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--state", default=None)
    ap.add_argument("--season", type=int, default=None, help="default: this year")
    ap.add_argument("--sports", default="xc,tf",
                    help="which sports' division lists to take (default both: "
                         "a team can be in an area for track and not for XC)")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests to one host (default 1)")
    ap.add_argument("--redo", action="store_true", help="re-ask teams already stored")
    ap.add_argument("--no-logos", action="store_true",
                    help="metadata and addresses only, fetch no images")
    ap.add_argument("--no-core", action="store_true",
                    help="skip GetTeamCore: one call per sport instead of "
                         "two, but no WebsiteSport and no season list")
    ap.add_argument("--probe", type=int, default=None, metavar="TEAM",
                    help="print both endpoints' whole response for one team "
                         "and stop; no database, no writes")
    ap.add_argument("--ignore-robots", action="store_true",
                    help="fetch even where anet's robots.txt disallows it")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()
    if not (args.write or args.dry_run or args.probe):
        ap.error("pass --probe, --dry-run or --write")
    season = args.season or time.gmtime().tm_year
    if args.probe:
        manners = Manners(rate=0)
        if args.ignore_robots:
            manners.allowed = lambda url: (True, 0.0)
        for name, tpl in (("TeamNav/Team", API), ("GetTeamCore", CORE)):
            url = tpl.format(team=args.probe, sport="xc", season=season)
            raw, why = manners.get(url, max_bytes=512 * 1024, extra=HEADERS)
            print(f"\n=== {name} -> {why}\n{url}")
            if raw is None:
                continue
            body = raw.decode("utf-8", "replace")
            try:
                got = json.loads(body)
                print(f"  top-level keys: {sorted(got)[:20]}"
                      if isinstance(got, dict) else f"  a {type(got).__name__}")
                print(json.dumps(got, indent=2)[:3000])
            except Exception:                             # noqa: BLE001
                print(body[:1500])
        return
    sports = [x.strip() for x in args.sports.split(",") if x.strip() in ("xc", "tf")]
    if not sports:
        ap.error("--sports takes xc, tf or xc,tf")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            todo = teams(cur, args.limit, args.state, args.redo)
            conn.commit()
            print(f"  {len(todo):,} teams, biggest programme first, "
                  f"{args.rate}s apart, "
                  f"{len(sports) + (0 if args.no_core else 1)} calls each "
                  f"(~{len(todo) * (len(sports) + (0 if args.no_core else 1)) * args.rate / 3600:.1f} h)",
                  flush=True)

            manners = Manners(rate=args.rate)
            if args.ignore_robots:
                manners.allowed = lambda url: (True, 0.0)
                print("  robots.txt IGNORED by --ignore-robots", flush=True)
            meta = crests = addrs = units = missed = 0
            t0 = time.time()
            for i, (school, state, team_id) in enumerate(todo, 1):
                # ★ BOTH ENDPOINTS, AND THEY CARRY DIFFERENT THINGS.
                #   TeamNav/Team has the divisions, customDivisions, the
                #   mascot and the crest, and its divisions are PER SPORT.
                #   GetTeamCore has WebsiteSport -- the address book's fix
                #   -- plus TeamCode, RegionID and the season list, none of
                #   which vary by sport. So: nav once per sport, core once.
                parts = []
                for sport in sports:
                    raw, why = manners.get(
                        API.format(team=team_id, sport=sport, season=season),
                        max_bytes=512 * 1024, extra=HEADERS)
                    got = parseTeam(raw) if raw is not None else None
                    if got:
                        parts.append(got)
                    divs = parseDivisions(raw) if raw is not None else []
                    if (divs or got) and args.write:
                        units += storeDivisions(cur, team_id, sport, divs,
                                                (got or {}).get("_custom"))
                if parts and not args.no_core:
                    craw, cwhy = manners.get(
                        CORE.format(team=team_id, sport=sports[0], season=season),
                        max_bytes=512 * 1024, extra=HEADERS)
                    core = parseTeam(craw) if craw is not None else None
                    if core:
                        parts.append(core)
                    else:
                        why = cwhy
                team = mergeTeams(*parts)
                if team is None:
                    missed += 1
                else:
                    meta += 1
                    if args.write:
                        storeTeam(cur, school, state, team)
                        addrs += 1 if storeAddress(cur, school, state, team) else 0
                    png = sha = None
                    for url in ([] if args.no_logos else mascotUrls(team)):
                        img, ctype = manners.get(url)
                        if img is None:
                            continue
                        png, sha, why = normalise(img, ctype=ctype, kind="icon")
                        if png:
                            break
                    if png:
                        crests += 1
                        if args.write:
                            name = writeFile(school, state, png, args.dir)
                            record(cur, school, state, name,
                                   mascotUrls(team)[0], "anet", sha, "ok")
                if i == ABORT_AFTER and meta == 0:
                    conn.rollback()
                    raise SystemExit(
                        f"  {ABORT_AFTER} calls, no team came back ({why}). "
                        f"Nothing written.\n"
                        f"  What anet actually said:\n    "
                        + (raw[:600].decode("utf-8", "replace") if raw else "(no body)")
                        + "\n  If that is a challenge page, --ignore-robots does "
                          "not help; if it says robots, it does. "
                          "--probe <team> prints one whole response.")
                if args.write and i % 100 == 0:
                    conn.commit()
                if i % 100 == 0 or args.dry_run:
                    rate = i / max(1e-9, time.time() - t0)
                    print(f"  [{i:,}/{len(todo):,}] {meta:,} teams, {crests:,} crests, "
                          f"{addrs:,} addresses, {units:,} unit rows, {missed:,} missed · "
                          f"{rate * 3600:,.0f}/h · last {school} ({state})", flush=True)
            if args.write:
                n = markShared(cur)
                conn.commit()
                print(f"  shared crests: {n:,} images worn by several schools")
            print(f"  done: {meta:,} teams, {crests:,} crests, {addrs:,} addresses, "
                  f"{units:,} unit rows, {missed:,} missed, "
                  f"{(time.time() - t0) / 60:.1f} min")
            print("  next: scripts/anet_units.py --report  (learns what anet's "
                  "unit ids mean from the units we already infer)")


if __name__ == "__main__":
    main()
