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
    DDL, SHA_INDEX, Manners, _tableExists, ensureTable, kindRank, markShared,
    normalise, record, sharedAlready, storedKind, writeFile)

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


# ★ WHICH STATE A ROW'S SCHOOL IS IN, FROM ONE PLACE. The assignment
#   first, the racing mode second -- the same order
#   build_school_identity's si_assign and school_identity.stateFilterSql
#   use, so all three agree about who is in which cluster. Both queues
#   below key crests on (school, state), so both must ask it the same way.
def _stateSource(cur, alias="t"):
    """(join SQL, state expression) for a query whose row alias is
    `alias` and which has person_id and school on it."""
    joins, parts = [], []
    if _tableExists(cur, "school_athlete_state"):
        joins.append(f"LEFT JOIN school_athlete_state sa"
                     f" ON sa.person_id = {alias}.person_id"
                     f" AND sa.school = {alias}.school")
        parts.append("sa.state")
    if _tableExists(cur, "person_home_state"):
        joins.append(f"LEFT JOIN person_home_state h"
                     f" ON h.person_id = {alias}.person_id")
        parts.append("h.state")
    empty = "''"                      # the SQL empty string, not Python's
    return (" ".join(joins),
            f"COALESCE({', '.join(parts + [empty])})" if parts else empty)


# ★ THE TEAMS OUR OWN ROWS NAME AND WE HAVE NEVER ASKED ABOUT (owner,
#   2026-09-16, with athletic.net team 21570: "idk why you think williams
#   isn't on anet"). Measured: 21570 is Williams College, it is NOT in
#   anet_team, and results_tf references it 16,434 times with results
#   another 4,356 -- 20,790 rows whose team we never fetched.
#
# ⚠ AND THE ORDINARY QUEUE CANNOT REACH IT. teams() asks for the modal team
#   of a (school, state) pair THAT ALREADY EXISTS in school_identity, so a
#   cluster that did not exist until an identity rebuild split the name was
#   never on any list -- and without the team we have no level, no state and
#   no mascot for it, which is what kept the college half of every collision
#   invisible. This queue starts from the rows instead: every team_id they
#   use that anet_team has no row for, biggest first.
#
# ! (school, state) IS STILL THE CREST'S KEY, so each team gets the modal
#   pair of its own rows, by _stateSource -- not the other way round.
#
# ⚠ IT SCANS BOTH ROW TABLES, so this is a maintenance command and not part
#   of any pipeline step. --limit with the biggest-first order is the point:
#   a few thousand teams cover most of the corpus's weight, and the rest can
#   wait for the next time somebody runs it.
def unfetchedTeams(cur, limit=None, min_rows=1):
    """[(school, state, team_id, None)] for teams the rows name and
    anet_team has never seen, most rows first."""
    for ddl in (DDL, TEAM_DDL, DIV_DDL):
        ensureTable(cur, ddl)
    home, state_expr = _stateSource(cur)
    legs = []
    for table in ("results", "results_tf"):
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = %s
                         AND column_name = 'team_id'""", (table,))
        if cur.fetchone() is not None:
            legs.append(table)
    if not legs:
        return []
    union = "\n            UNION ALL\n".join(f"""
            SELECT t.school, t.team_id, {state_expr} AS state, count(*) AS n
            FROM   {leg} t {home}
            WHERE  t.team_id IS NOT NULL AND t.team_id <> 0
              AND  t.school IS NOT NULL AND btrim(t.school) <> ''
            GROUP  BY 1, 2, 3""" for leg in legs)
    cur.execute(f"""
        WITH team_rows AS ({union}
        ), agg AS (
            SELECT school, team_id, state, sum(n) AS n
            FROM   team_rows GROUP BY 1, 2, 3
        ), total AS (
            SELECT team_id, sum(n) AS n FROM agg GROUP BY 1
        ), best AS (
            -- the pair the team's own rows mostly sit in
            SELECT DISTINCT ON (team_id) team_id, school, state
            FROM   agg ORDER BY team_id, n DESC, school
        )
        SELECT b.school, b.state, b.team_id, NULL::text AS mascot_url
        FROM   best b
        JOIN   total ON total.team_id = b.team_id
        LEFT   JOIN anet_team a ON a.team_id = b.team_id
        WHERE  a.team_id IS NULL AND total.n >= %(min_rows)s
        ORDER  BY total.n DESC
        {"LIMIT %(limit)s" if limit else ""}
    """, {"limit": limit, "min_rows": int(min_rows)})
    return [tuple(r[k] for k in ("school", "state", "team_id", "mascot_url"))
            if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]


def teams(cur, limit=None, state=None, redo=False, missing=False):
    """[(school, state, team_id, stored mascot_url)] -- the anet team each
    school's athletes actually raced under, biggest programme first.

    Modal per (school, STATE), not per school string: two real schools
    share the name "Kingston" and anet gives them two ids, which is the
    split school_identity already draws.

    ★ AND THE STATE IS THE SCHOOL'S ASSIGNMENT WHERE THERE IS ONE (owner,
      2026-09-16: "the logos are still the old logo (for oregon) ...
      Williams worked perfectly, they just have no logo anymore").
      school_logo is keyed on school_identity's (school, state) pairs, and
      this queue decides which anet TEAM answers for each pair -- so it
      has to use the same answer the clusters were drawn from. It used
      person_home_state alone, where the athlete RACES: for a college that
      is a travel mode, so the modal team for (Oregon, OR) was decided by
      whoever happens to race in Oregon rather than by the University of
      Oregon's own roster. school_athlete_state
      (build_school_identity.buildAthleteState) is that answer, and the
      home state remains the fallback for every name it does not cover.

    ⚠ SO A RERUN IS NEEDED AFTER EVERY IDENTITY REBUILD THAT SPLITS A
      NAME. The crests already stored sit under the OLD pairs; the new
      cluster has none, which is why a split name loses its badge.
      `--redo` re-asks the teams and files them under the new pairs.
      Nothing is re-scraped from the schools: anet_team.mascot_url is
      already stored, and this pass fetches that image.

    ★ AND `missing` IS WHY A RERUN IS NOT 38 HOURS (owner, 2026-09-16: "so
      that anet_teams script is gonna take 38 hrs. Do we have to rerun the
      entire thing?"). No: --redo re-asks all 40,927 teams, and Manners
      paces one request per second PER HOST, so two or three calls each
      against www.athletic.net is a day and a half. What changed is the
      (school, state) pairs, so the work is the pairs that now have no
      crest -- `missing` -- and with --logos-only there is no anet API call
      at all, because mascot_url is already in the table. One image GET per
      pair, on googleusercontent rather than anet."""
    for ddl in (DDL, TEAM_DDL, DIV_DDL):
        ensureTable(cur, ddl)
    cur.execute(SHA_INDEX)
    if not _tableExists(cur, "school_identity"):
        raise SystemExit("school_identity is missing; run pipeline step 10b first")
    home, state_expr = _stateSource(cur)
    # ! ASKED OF THE RESOLVED TEAM. `a` is joined on COALESCE(modal, link)
    #   below, so this still reads "anet_team has never seen this team".
    done = "" if redo else "AND a.team_id IS NULL"
    # ! A PAIR THAT ALREADY HAS A CREST ON DISK IS NOT MISSING ONE. The
    #   same three conditions school_logo.loadCrests serves on, so this
    #   asks exactly for what the site cannot draw.
    gap = ""
    if missing and _tableExists(cur, "school_logo"):
        gap = ("""AND NOT EXISTS (SELECT 1 FROM school_logo g
                   WHERE g.school = si.school AND g.state = si.state
                     AND g.path IS NOT NULL AND g.status = 'ok'
                     AND COALESCE(lower(g.override), '') <> 'none'
                     AND (NOT g.shared OR g.override IS NOT NULL))""")
    # ! OPTIONAL TABLE, STUBBED WHEN ABSENT. link_tfrrs_to_anet has to have
    #   run for the join below to have anything in it, and a database where
    #   it has not must queue exactly what it queued before.
    link_src = ("school_team_link" if _tableExists(cur, "school_team_link")
                else "(SELECT NULL::text AS tfrrs_school, NULL::int AS team_id,"
                     " NULL::text AS state WHERE false)")
    where_state = "AND si.state = %(state)s" if state else ""
    lim = "LIMIT %(limit)s" if limit else ""
    cur.execute(f"""
        WITH t AS (
            -- team_id 0 is anet's unattached sentinel, not an id. Rows
            -- written before database._teamIdOrNone still carry it, and one
            -- of them as a school's modal id would fetch team 0 for everyone.
            SELECT school, team_id, person_id FROM results
            WHERE  team_id IS NOT NULL AND team_id <> 0 AND school IS NOT NULL
            UNION ALL
            SELECT school, team_id, person_id FROM results_tf
            WHERE  team_id IS NOT NULL AND team_id <> 0 AND school IS NOT NULL
        ), counted AS (
            SELECT t.school, {state_expr} AS state, t.team_id, count(*) AS n
            FROM   t {home}
            GROUP  BY 1, 2, 3
        ), modal AS (
            SELECT DISTINCT ON (school, state) school, state, team_id
            FROM   counted ORDER BY school, state, n DESC
        ), by_team AS (
            -- one row per (name, team), athlete-state rolled up: which team
            -- the name's athletes raced under, regardless of where they race
            SELECT school, team_id, sum(n) AS n FROM counted GROUP BY 1, 2
        ), anet_keyed AS (
            -- ★★ THE TEAM anet ITSELF PLACES IN THIS STATE (owner,
            --    2026-09-18: "the issue isn't that we're not getting it from
            --    anet but that we're getting the wrong one from anet (this
            --    applies to wake forest and oregon)").
            --
            --    `modal` picks the team most raced under by athletes ASSIGNED
            --    this state, which is an inference. anet_team.anet_state is
            --    anet's own record of where that team is -- a fact, already
            --    stored by this very script. For a shared name that fact is
            --    the whole answer: (Oregon, OR) wants the team anet puts in
            --    OR, and no amount of athlete counting can be trusted to
            --    agree, because a cluster's athletes include transfers,
            --    mis-assignments and anyone anet filed under the wrong team.
            --
            --    The comment above KIND_RANK says an anet crest "cannot be
            --    the wrong school's, it is fetched BY TEAM ID". True only if
            --    the team id belongs to this school. Choosing it by athlete
            --    modality is exactly how it stops belonging.
            SELECT DISTINCT ON (b.school, upper(btrim(at.anet_state)))
                   b.school,
                   upper(btrim(at.anet_state)) AS state,
                   b.team_id
            FROM   by_team b
            JOIN   anet_team at ON at.team_id = b.team_id
            WHERE  COALESCE(btrim(at.anet_state), '') <> ''
            ORDER  BY b.school, upper(btrim(at.anet_state)), b.n DESC
        )
        SELECT si.school, si.state,
               COALESCE(ak.team_id, modal.team_id, link.team_id) AS team_id,
               a.mascot_url
        FROM   school_identity si
        -- ⚠⚠ LEFT, AND THE LINK BESIDE IT (owner, 2026-09-17: "Penn state
        --    still has no logo at all"; and 2026-09-16 of Williams, "they
        --    just have no logo anymore"). This was an INNER join to `modal`,
        --    which is built from results.team_id -- so a cluster whose rows
        --    carry NO anet team id was not in this queue AT ALL, was never
        --    asked about, and could never be given a crest.
        --
        --    That is not an edge case; it is the college half of every
        --    collision. A tfrrs XC row carries no anet team id (and
        --    `results` has no team_slug), so the University of Oregon,
        --    Williams College and Penn State's college cluster each joined
        --    to nothing. --missing hid it rather than showing it: a pair
        --    that is not in the queue is not reported as missing either.
        --
        -- ! school_team_link IS THAT MISSING team_id, learned from the
        --   athletes who appear in both feeds (scripts/link_tfrrs_to_anet.py,
        --   pipeline step 10b0). ITS OWN STATE MUST MATCH THE CLUSTER'S, so
        --   the link for the string "Oregon" -- the university, in OR --
        --   supplies a team for (Oregon, OR) and can never reach
        --   (Oregon, IL), which keeps its own modal team.
        -- ★ anet'S OWN PLACEMENT FIRST, then the athlete-modal team, then the
        --   tfrrs link. Only the first is a fact about the team.
        LEFT   JOIN anet_keyed ak
               ON ak.school = si.school AND ak.state = si.state
        LEFT   JOIN modal ON modal.school = si.school AND modal.state = si.state
        LEFT   JOIN {link_src} link
               ON link.tfrrs_school = si.school
              AND upper(btrim(link.state)) = si.state
        -- the resolved team, so mascot_url is the linked team's where the
        -- cluster had none of its own
        LEFT   JOIN anet_team a ON a.team_id = COALESCE(ak.team_id,
                                                        modal.team_id,
                                                        link.team_id)
        WHERE  si.n_athletes >= 3 {done} {where_state} {gap}
          AND  COALESCE(ak.team_id, modal.team_id, link.team_id) IS NOT NULL
          -- ⚠ AND NEVER A TEAM anet PLACES SOMEWHERE ELSE. Where anet states
          --   the team's state and it disagrees with this cluster's, the
          --   crest belongs to a different school: no badge is better than
          --   another school's badge. An unknown anet_state still passes,
          --   because most of the corpus has one and refusing those would
          --   strip crests that are right.
          AND  (COALESCE(btrim(a.anet_state), '') = ''
                OR upper(btrim(a.anet_state)) = si.state)
        ORDER  BY si.n_athletes DESC
        {lim}
    """, {"limit": limit, "state": (state or "").upper()})
    return [tuple(r[k] for k in ("school", "state", "team_id", "mascot_url"))
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


# ! ONE NORMALISER, BOTH ENDS. mascotUrls below is what makes the two
#   spellings differ, so the undo lives beside it: add the scheme to a
#   protocol-relative URL and drop the googleusercontent "=sN" size.
_URL_KEY = ("regexp_replace(regexp_replace(btrim(lower({c})), "
            "'^//', 'https://'), '=s[0-9]+$', '')")


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


# ⚠⚠ CHOOSING THE RIGHT TEAM ONLY FIXES WHAT IS WRITTEN NEXT (owner,
#    2026-09-18: "oregon and wake forest still wrong" -- after anet_keyed
#    landed AND after --state OR --write reported 0 crests).
#
#    Both halves are needed and this is the missing one. anet_keyed stopped
#    the queue handing (Oregon, WI) the University of Oregon's team, and the
#    WHERE clause below it refuses a team anet places in another state --
#    so that pair is now not queued AT ALL. The row an earlier run already
#    wrote there is untouched, and it is what the site still serves. "0
#    crests, 2 missed" is the queue working exactly as designed and the bug
#    surviving it.
#
# ★ AND THIS IS PROVABLE, NOT INFERRED -- which is why it is safe to delete
#   where --prune-stale was not. school_logo.source_url IS anet_team's
#   mascot_url: the row records which team's picture it fetched. So a row
#   whose image belongs to a team anet itself places in a DIFFERENT state
#   than the row is, by anet's own record, another school's crest. No
#   athlete counting, no cluster heuristic, no guess about reachability.
#
# ! AN OVERRIDE IS STILL A DECISION, and a stateless row is still the
#   deliberate any-state fallback. Neither is touched.
def misplacedCrests(cur):
    """([(school, state, level, anet_state)], (n_anet_rows, n_matched)) --
    stored crests that are, by anet's own anet_state, some other school's.

    ⚠⚠ THE TWO URLS ARE NOT THE SAME STRING (owner, 2026-09-18, a clean and
       wrong "0 stored crests"). anet_team.mascot_url is anet's raw
       protocol-relative "//lh3.googleusercontent.com/...", and
       school_logo.source_url is what mascotUrls() FETCHED -- scheme added,
       "=s512" appended for googleusercontent sizing. An equality join
       between them matches nothing, ever. _URL_KEY undoes both.

    ⚠⚠ AND NORMALISING BOTH SIDES MAKES THE JOIN UNINDEXABLE, which is the
       second thing that went wrong (owner: "15 mins nothing printed"). The
       first version wrapped both columns in regexp_replace AND put a
       correlated NOT EXISTS over anet_team inside it, so Postgres re-scanned
       every team, evaluating two regexes per row, once per crest row. Tens
       of thousands squared. This is the same non-sargable mistake as the
       course page's COALESCE join earlier the same day.

    ★ SO THE KEYS ARE BUILT ONCE AND THE QUESTION IS A GROUP BY. Each side
      is normalised in a MATERIALIZED CTE (one pass each), joined on the
      plain text key (one hash join), and "does anet place ANY team wearing
      this picture in this state" becomes bool_or over the group rather than
      a subquery per row.

    ! AN OVERRIDE IS A DECISION and a stateless row is the deliberate
      any-state fallback. Neither is looked at.
    """
    if not (_tableExists(cur, "school_logo") and _tableExists(cur, "anet_team")):
        return [], (0, 0)
    cur.execute(f"""
        WITH lk AS MATERIALIZED (
            SELECT l.school, upper(btrim(l.state)) AS state,
                   COALESCE(l.level, '') AS level,
                   {_URL_KEY.format(c='l.source_url')} AS k
            FROM   school_logo l
            WHERE  l.kind = 'anet'
              AND  l.override IS NULL
              AND  COALESCE(btrim(l.source_url), '') <> ''
              AND  COALESCE(btrim(l.state), '') <> ''
        ), tk AS MATERIALIZED (
            SELECT DISTINCT {_URL_KEY.format(c='t.mascot_url')} AS k,
                   upper(btrim(t.anet_state)) AS st
            FROM   anet_team t
            WHERE  COALESCE(btrim(t.mascot_url), '') <> ''
              AND  COALESCE(btrim(t.anet_state), '') <> ''
        ), joined AS (
            SELECT lk.school, lk.state, lk.level,
                   -- ! ANY team anet places HERE makes the row right. One
                   --   picture can be several teams' (a district mark, a
                   --   campus family), and then the disagreement is noise.
                   bool_or(tk.st = lk.state) AS owned_here,
                   min(tk.st)                AS elsewhere
            FROM   lk JOIN tk ON tk.k = lk.k
            GROUP  BY 1, 2, 3
        )
        SELECT school, state, level, elsewhere,
               (SELECT count(*) FROM joined) AS matched
        FROM   joined
        WHERE  NOT owned_here
        ORDER  BY school, state
    """)
    rows = [tuple(r) for r in cur.fetchall()]
    # ! THE DENOMINATOR IS COUNTED SEPARATELY, because "0 of 0" reads as
    #   good news and a broken join gives exactly that.
    cur.execute("""
        SELECT count(*) FROM school_logo
        WHERE  kind = 'anet' AND override IS NULL
          AND  COALESCE(btrim(source_url), '') <> ''
          AND  COALESCE(btrim(state), '') <> ''
    """)
    have = cur.fetchone()[0]
    matched = rows[0][4] if rows else None
    return [(r[0], r[1], r[2], r[3]) for r in rows], (have, matched)


def unfileMisplaced(cur, rows):
    """Delete them. Returns how many went."""
    n = 0
    for school, state, level, _anet_state in rows:
        cur.execute("""DELETE FROM school_logo
                       WHERE school = %s AND upper(btrim(state)) = %s
                         AND COALESCE(level, '') = %s""",
                    (school, state, level or ""))
        n += cur.rowcount
    return n



def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--unfile-misplaced", action="store_true",
                    help="delete stored crests that are, by anet's own "
                         "anet_state, another school's -- the rows an "
                         "earlier run filed before the queue learned to "
                         "refuse them. The pair then shows as --missing and "
                         "a later pass can fill it correctly. Overrides are "
                         "never touched. --dry-run lists them.")
    ap.add_argument("--state", default=None)
    ap.add_argument("--season", type=int, default=None, help="default: this year")
    ap.add_argument("--sports", default="xc,tf",
                    help="which sports' division lists to take (default both: "
                         "a team can be in an area for track and not for XC)")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests to one host (default 1)")
    ap.add_argument("--redo", action="store_true", help="re-ask teams already stored")
    ap.add_argument("--logos-only", action="store_true",
                    help="no anet API calls at all: fetch the crest from the "
                         "mascot_url already stored in anet_team. What to use "
                         "after an identity rebuild splits a name -- the "
                         "metadata did not change, the (school, state) pairs "
                         "did. Implies --redo.")
    ap.add_argument("--unfetched", action="store_true",
                    help="the teams our own rows name that anet_team has never "
                         "seen, biggest first -- the bootstrap the ordinary "
                         "queue cannot reach, because that one starts from "
                         "school_identity pairs that already exist. Use with "
                         "--limit.")
    ap.add_argument("--queue-only", action="store_true",
                    help="print the queue and stop: how many teams, what they "
                         "are, no network and no writes. What to run before "
                         "choosing a --limit.")
    ap.add_argument("--min-rows", type=int, default=1,
                    help="with --unfetched: skip a team our rows name fewer "
                         "than this many times")
    ap.add_argument("--missing", action="store_true",
                    help="only the (school, state) pairs the site currently "
                         "has no crest for. With --logos-only this is the "
                         "whole job after a split, in minutes rather than "
                         "the 38 hours a full --redo costs.")
    ap.add_argument("--no-logos", action="store_true",
                    help="metadata and addresses only, fetch no images")
    ap.add_argument("--replace", action="store_true",
                    help="install anet's mascot unconditionally, placeholders "
                         "and all")
    ap.add_argument("--keep-better", action="store_true",
                    help="leave a crest alone where a better-ranked source "
                         "(the school's own athletics site) already gave one")
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
    if not (args.write or args.dry_run or args.probe or args.queue_only
            or args.unfile_misplaced):
        ap.error("pass --probe, --queue-only, --dry-run or --write")
    # ! CONTRADICTORY, SO IT IS REFUSED RATHER THAN RESOLVED. --replace says
    #   "install anet's mascot whatever is there" and --keep-better says
    #   "leave a better-ranked crest alone". Passing both used to mean
    #   --replace and --keep-better did nothing at all, silently.
    if args.replace and args.keep_better:
        ap.error("--replace and --keep-better contradict each other: the "
                 "first installs anet's mascot over whatever is stored, the "
                 "second leaves a better-ranked crest alone. Pick one. "
                 "(--keep-better is the one that fills gaps without "
                 "overwriting a school's own athletics mark.)")
    if args.unfile_misplaced:
        from database import getConn
        with getConn() as conn:
            with conn.cursor() as cur:
                bad, (have, matched) = misplacedCrests(cur)
                print(f"  {have:,} anet crest rows carry a state and a source")
                # ★ A ZERO MUST BE TELLABLE FROM A BROKEN JOIN. That is
                #   exactly what made the equality-join version look fine.
                if have and matched is None and not bad:
                    raise SystemExit(
                        "  no crest row matched ANY anet team by image -- "
                        "the join is broken, not the data. Nothing written.")
                if matched is not None:
                    print(f"  {matched:,} of them match a team by image")
                print(f"  {len(bad):,} wear a picture anet places only in "
                      f"another state")
                for school, state, level, anet_state in bad[:40]:
                    print(f"    {school} ({state}) level={level or '-'} "
                          f"-- anet puts that picture's team in {anet_state}")
                if len(bad) > 40:
                    print(f"    ... and {len(bad) - 40:,} more")
                if args.write and bad:
                    print(f"  deleted {unfileMisplaced(cur, bad):,}")
                    conn.commit()
                    print("  now re-run with --missing --logos-only --write "
                          "to refill those pairs from the right team.")
                elif bad:
                    print("  DRY RUN -- pass --write to delete them.")
        return
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
            todo = (unfetchedTeams(cur, args.limit, args.min_rows)
                    if args.unfetched else
                    teams(cur, args.limit, args.state,
                          redo=args.redo or args.logos_only,
                          missing=args.missing))
            conn.commit()
            # ! THE ESTIMATE HAS TO BE HONEST ABOUT WHICH CALLS. --logos-only
            #   makes one image request per team and no API call at all, and
            #   the image host is not anet, so the per-host pacing that
            #   dominates a full run does not apply to the same queue twice.
            per = 1 if args.logos_only else (
                len(sports) + (0 if args.no_core else 1)
                + (0 if args.no_logos else 1))
            print(f"  {len(todo):,} teams, biggest programme first, "
                  f"{args.rate}s apart, {per} call(s) each "
                  f"(~{len(todo) * per * args.rate / 3600:.1f} h)"
                  + ("  [--logos-only: the stored mascot_url, no anet API]"
                     if args.logos_only else "")
                  + ("  [--missing: only pairs with no crest]"
                     if args.missing else ""),
                  flush=True)

            # ! BEFORE ANY REQUEST. The whole point is to size the job.
            if args.queue_only:
                for i, (school, state, team_id, murl) in enumerate(todo, 1):
                    print(f"  {i:>6}  team {team_id:<9} {school!r} "
                          f"({state or '--'})"
                          + ("  [mascot_url stored]" if murl else ""))
                print(f"\n  {len(todo):,} teams in the queue. No requests "
                      f"made, nothing written.\n")
                return

            manners = Manners(rate=args.rate)
            if args.ignore_robots:
                manners.allowed = lambda url: (True, 0.0)
                print("  robots.txt IGNORED by --ignore-robots", flush=True)
            # ★ WHICH (school, state) PAIRS HOLD MORE THAN ONE INSTITUTION
            #   (owner, 2026-09-16: "it is weird that it changes from right to
            #   wrong with new scrape -- Amherst college changed from actual
            #   to the falcons logo").
            #
            # ⚠ THE QUEUE PICKS THE MODAL TEAM OF A PAIR, and Amherst Regional
            #   High School has far more rows than Amherst College while both
            #   are (Amherst, MA) -- so the high school wins, and because
            #   "ANET WINS" is the default its Falcons mascot OVERWROTE the
            #   college's real crest, which had come from the college's own
            #   athletics site (kind 'athletics', rank 1, against anet's 2).
            #   Right to wrong, in one run.
            #
            # ! SO ON SUCH A PAIR, ANET DOES NOT WIN. school_logo is keyed on
            #   (school, state) and cannot hold two crests for one key, so
            #   whichever mascot goes there is wrong for the other
            #   institution. Filling an empty key is a coin flip we already
            #   take; REPLACING a better-ranked crest is a regression, and
            #   that is what stops here. The real answer is a level in the
            #   key -- see docs/ISSUES-RUNNING.md S.
            # the anet level NAME per team, for the crest's third key part
            anet_levels = {}
            try:
                from speed_ratings_db import loadTeamLevels
                anet_levels, _meaning, _rows = loadTeamLevels()
            except Exception as exc:                          # noqa: BLE001
                print(f"  team levels unavailable ({type(exc).__name__}: {exc}) "
                      f"-- crests file under no level, as before", flush=True)

            multi_level = set()
            if _tableExists(cur, "school_level"):
                cur.execute("""
                    SELECT school, state FROM school_level
                    WHERE  NOT is_bucket
                    GROUP  BY school, state HAVING count(*) >= 2
                """)
                multi_level = {(r[0], r[1]) for r in cur.fetchall()}
                print(f"  {len(multi_level):,} (school, state) pairs hold more "
                      f"than one institution: anet's mascot may fill an empty "
                      f"crest there but never replace one", flush=True)

            meta = crests = addrs = units = missed = 0
            kept = placeholder = stateless = 0
            t0 = time.time()
            for i, (school, state, team_id, stored_url) in enumerate(todo, 1):
                raw = why = None
                # ★ THE STORED URL IS ENOUGH FOR A CREST (owner, 2026-09-16:
                #   "so that anet_teams script is gonna take 38 hrs. Do we
                #   have to rerun the entire thing?"). No. --logos-only
                #   skips BOTH anet endpoints and fetches only the image,
                #   which lives on a different host: the pacing that makes a
                #   full --redo a day and a half is one request per second
                #   against www.athletic.net, and this makes none of them.
                #   The metadata did not change -- the (school, state) pairs
                #   did -- so with --missing the work is one image GET per
                #   pair that now has no crest.
                if args.logos_only:
                    team = ({"MascotUrl": stored_url, "IDTeam": team_id}
                            if stored_url else None)
                    if team is None:
                        why = "no mascot_url stored for this team"
                else:
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
                    if args.write and not args.logos_only:
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
                    # ★ ANET WINS (owner, 2026-09-13: "I want it to
                    #   overwrite it"). Its mascot is the athletics mark and
                    #   it is the same shape for every school, so a corpus
                    #   of them looks like one set rather than whatever each
                    #   school's CMS happened to publish. --keep-better
                    #   restores the ranked behaviour.
                    #
                    # ⚠ THE ONE EXCEPTION IS A PLACEHOLDER, and it is not a
                    #   precedence rule -- it is arithmetic. An image four
                    #   hundred schools already wear is hidden by the shared
                    #   sweep, so installing it OVER a good crest does not
                    #   swap one picture for another: it leaves the school
                    #   with none. --replace overrides even that.
                    # ★ UNDER THE TEAM'S OWN LEVEL (owner, 2026-09-16: "The
                    #   anet pools should match our school pools. If they
                    #   don't, separate them"). anet_team.level, named by
                    #   loadTeamLevels, says which institution this mascot
                    #   belongs to -- so Amherst College's crest and Amherst
                    #   Regional's are two rows, and neither can shadow the
                    #   other. Unknown level -> '' , the level-less row that
                    #   answers for any level, which is the old behaviour.
                    lv = anet_levels.get(team_id) or ""
                    if lv not in ("elem", "ms", "hs", "college"):
                        lv = ""
                    # ⚠⚠ --replace DID NOT COMPOSE, AND THE HANDOFF'S OWN
                    #    COMMAND PASSED BOTH (2026-09-17: `--redo --replace
                    #    --keep-better`). This whole block was skipped under
                    #    --replace, so --keep-better was silently a no-op --
                    #    the run did the OPPOSITE of what its flags said, and
                    #    "right to wrong on a new scrape" is what that looks
                    #    like from the site. The two are contradictory
                    #    instructions, so they are refused together in main()
                    #    rather than one of them being quietly dropped.
                    #
                    # ★ AND THE TWO-INSTITUTION GUARD IS NOT A PREFERENCE, so
                    #   --replace does not override it. It is arithmetic:
                    #   where the level is UNKNOWN the crest key is
                    #   (school, state) alone, one key holds one crest, and
                    #   (Amherst, MA) is Amherst College AND Amherst Regional
                    #   High -- whichever mascot lands there is wrong for the
                    #   other. Replacing a better-ranked crest on such a key
                    #   is not a swap, it is a loss. With a KNOWN level the
                    #   two are separate rows and nothing is contested, so
                    #   the guard does not apply and anet wins as intended.
                    contested_key = not lv and (school, state) in multi_level
                    if png and args.write and (not args.replace
                                               or contested_key):
                        keep = args.keep_better or contested_key
                        if keep and kindRank("anet") > kindRank(
                                storedKind(cur, school, state, lv)):
                            kept += 1
                            png = None
                        elif not args.replace and sharedAlready(cur, sha):
                            placeholder += 1
                            png = None
                    # ⚠ NEVER UNDER AN EMPTY STATE (2026-09-16, the first
                    #   --unfetched run: "Exeter ()", "Eastlake ()"). A crest
                    #   row with no state is the fallback school_logo.crestState
                    #   serves for ANY mention of the name -- so filing one is
                    #   how a name-wide badge gets made, which is the bug this
                    #   whole split exists to fix. The METADATA is still worth
                    #   the call (level and mascot_url feed the pooling and the
                    #   contested list), so the team is fetched and only the
                    #   crest is held back.
                    if png and not (state or "").strip():
                        stateless += 1
                        png = None
                    if png:
                        crests += 1
                        if args.write:
                            name = writeFile(school, state, png, args.dir,
                                             level=lv)
                            record(cur, school, state, name,
                                   mascotUrls(team)[0], "anet", sha, "ok",
                                   level=lv)
                if i == ABORT_AFTER and meta == 0 and not args.logos_only:
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
            print(f"  left alone: {kept:,} already had a better crest "
                  f"(--keep-better), {placeholder:,} would have replaced a "
                  f"crest with a picture several schools already wear, "
                  f"{stateless:,} had no state to file one under")
            print("  next: scripts/anet_units.py --report  (learns what anet's "
                  "unit ids mean from the units we already infer)")


if __name__ == "__main__":
    main()
