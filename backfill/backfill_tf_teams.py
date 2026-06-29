# Project: xc-predictor
# File:    backfill/backfill_tf_teams.py
# Purpose: Re-parse team scores for TF meets saved with empty teams_json — the
#          multi-section (HS/middle-school, JV/Varsity) meets missed by the old
#          strict team_scores id regex. Fetches each meet's LANDING page, re-runs
#          the now-fixed parseTFTeams, writes teams_json. Does NOT touch results.
#          Idempotent: re-running only re-fills still-empty meets.
#
#          RUN AFTER the main drain finishes — it's single-session and competes
#          for IP budget. Run it on its own (ideally through the rotated VPN).

import sys, asyncio, random
for p in ("scripts", "tfrrs/scraper", "tfrrs/parser", "tfrrs/glue"):
    sys.path.insert(0, p)
from database import getConn
from fetch_tfrrs import fetchTFPage
from parse_tf_team import parseTFTeams
import psycopg2.extras

# Delay between landing-page fetches. One cheap fetch per meet; lean since it's
# light, but keep it polite on a single IP.
FETCH_DELAY = (2, 4)


# _findEmptyTeamMeets
# Purpose: TF meets that completed but whose teams_json is missing/empty — the
#          backfill targets. (Many will be genuinely teamless and stay empty
#          after a 0-team parse; that's fine, just a cheap wasted fetch.)
# Arguments: cur: open cursor.
# Output:    list of meet_ids.
def _findEmptyTeamMeets(cur):
    cur.execute(
        """
        SELECT e.meet_id
        FROM meet_extras e
        JOIN meet_queue q
          ON q.meet_id = e.meet_id AND q.sport = 'TF' AND q.source = 'tfrrs'
        WHERE e.sport = 'TF' AND e.source = 'tfrrs'
          AND (e.teams_json IS NULL OR jsonb_array_length(e.teams_json) = 0)
        ORDER BY e.meet_id
        """
    )
    return [r[0] for r in cur.fetchall()]


# _writeTeams
# Purpose: Write a meet's parsed teams into meet_extras.teams_json (anet's
#          column), source='tfrrs'. Mirrors the live _saveTeamScores.
# Arguments: cur, meet_id, teams.
# Output:    None.
def _writeTeams(cur, meet_id, teams):
    cur.execute(
        """
        INSERT INTO meet_extras (meet_id, sport, source, teams_json)
        VALUES (%s, 'TF', 'tfrrs', %s)
        ON CONFLICT (meet_id, sport, source)
        DO UPDATE SET teams_json = EXCLUDED.teams_json
        """,
        (meet_id, psycopg2.extras.Json(teams)),
    )


# _backfillOne
# Purpose: Fetch one meet's landing page, parse teams, write them if any.
# Arguments: meet_id.
# Output:    the team count written (0 if genuinely teamless).
async def _backfillOne(meet_id):
    html = await fetchTFPage(f"https://www.tfrrs.org/results/{meet_id}")
    teams = [t for t in parseTFTeams(html, meet_id) if t.get("ok")]
    if not teams:
        return 0
    def _do():
        with getConn() as conn:
            cur = conn.cursor()
            _writeTeams(cur, meet_id, teams)
            conn.commit()        # getConn does NOT auto-commit
    await asyncio.to_thread(_do)
    return len(teams)


async def main():
    def _list():
        with getConn() as conn:
            return _findEmptyTeamMeets(conn.cursor())
    meets = await asyncio.to_thread(_list)
    print(f"{len(meets)} TF meets with empty teams_json to backfill", flush=True)

    filled = 0
    for i, meet_id in enumerate(meets, 1):
        try:
            n = await _backfillOne(meet_id)
            if n:
                filled += 1
            print(f"[{i}/{len(meets)}] meet {meet_id}: {n} teams", flush=True)
        except Exception as exc:
            print(f"[{i}/{len(meets)}] meet {meet_id}: ERROR {exc}", flush=True)
        await asyncio.sleep(random.uniform(*FETCH_DELAY))

    print(f"done — filled {filled}/{len(meets)} meets", flush=True)


if __name__ == "__main__":
    asyncio.run(main())