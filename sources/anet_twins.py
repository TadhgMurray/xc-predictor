# Project: xc-predictor
# File:    sources/anet_twins.py
# Purpose: Don't scrape athletic.net for a meet a timer's own site already
#          gave us (owner, 2026-11-01: "if a meet is scraped locally we need
#          to not scrape anet for that same meet").
#
#   A timer's page names no athletic.net meet id, so the match is on what
#   both sides know before athletic.net's results are fetched: the meet's
#   date, its state and its name.
#
#   1. NO REQUEST -- athletic.net meets we have already seen. Every
#      GetMeetData the launcher makes is kept (anet_meet_seen: name, date,
#      state). After the timers promote, cover() matches the promoted timer
#      meets against those and closes the athletic.net queue rows.
#   2. ONE REQUEST -- a meet id seen for the first time. The launcher's
#      GetMeetData is how we learn the meet exists at all; skipFor() runs
#      right after it and, on a match, the per-division calls are skipped.
#
#   Every match is written to anet_local_twin (meet_id, sport, ext_meet_id)
#   and queue_meets leaves those ids out of its re-ask passes.
#
# ! CROSS COUNTRY ONLY, AND ONLY A TIMER COPY WITH AT LEAST AS MANY RACES AS
#   ATHLETIC.NET HAS DIVISIONS (2026-11-01). A timer that posted only the
#   varsity races would otherwise cost athletic.net's JV and middle school
#   ones for good. Track stays athletic.net's: timers' files leave out field
#   events and relays.
# ! THE NAME TEST IS DELIBERATELY STRICT: the same date, the same state when
#   both name one, and the names' distinctive words agreeing (sameName).
#   A false match loses athletic.net's copy (the timer's is kept); a missed
#   match costs only the scrape we did before.
import re

STOP = set("""the a an of and at in on for invitational invite inv xc cc cross country
meet classic open annual championship championships champs high school hs ms
middle jv varsity boys girls men women mens womens track field tf indoor outdoor
results official final finals race races day league conference conf""".split())
_YEARISH = re.compile(r"\b(19|20)\d\d\b|\b\d+(st|nd|rd|th)\b")

SEEN_DDL = """
CREATE TABLE IF NOT EXISTS anet_meet_seen (
    meet_id    BIGINT NOT NULL,
    sport      TEXT NOT NULL,
    name       TEXT,
    meet_date  DATE,
    state      TEXT,
    n_divs     INTEGER,
    seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meet_id, sport)
);
ALTER TABLE anet_meet_seen ADD COLUMN IF NOT EXISTS n_divs INTEGER;
CREATE INDEX IF NOT EXISTS anet_meet_seen_date ON anet_meet_seen (meet_date);
CREATE TABLE IF NOT EXISTS anet_local_twin (
    meet_id     BIGINT NOT NULL,
    sport       TEXT NOT NULL,
    ext_meet_id BIGINT NOT NULL,
    how         TEXT,
    matched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meet_id, sport)
);
"""


def words(name):
    """The distinctive words of a meet name: lowercase, no years or ordinals,
    no words every meet has."""
    t = _YEARISH.sub(" ", (name or "").lower())
    t = re.sub(r"(?<=[a-z])[-.'](?=[a-z])", "", t)          # 'w-fl' = 'wfl', "st. mary's" = 'st marys'

    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP and len(w) > 1}


def sameName(a, b):
    """Most of the shorter name's distinctive words are in the longer one,
    and at least one is shared. 'Wayne XC Invitational 2026' ~ 'Wayne Cross
    Country Invite'; 'Wayne Invitational' !~ 'Wayne Central Invitational'."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    shared = small & big
    return bool(shared) and len(shared) / len(small) >= 0.75 and len(shared) / len(big) > 0.5


def _iso(d):
    return str(d)[:10] if d else None


def match(name, date, state, candidates, n_divs=None):
    """The one candidate (ext_meet_id, name, date, state[, n_races]) that is
    this meet, or None (none, or more than one -- ambiguous is no). With
    n_divs, a candidate with fewer races than that is not enough. Pure."""
    hits = [c for c in candidates
            if _iso(c[2]) == _iso(date)
            and (not state or not c[3] or c[3].upper() == state.upper())
            and sameName(name, c[1])]
    if len(hits) != 1:
        return None
    hit = hits[0]
    if n_divs and len(hit) > 4 and (hit[4] or 0) < n_divs:
        return None
    return hit[0]


def ensureTables(cur):
    cur.execute(SEEN_DDL)


def _timerMeets(cur, date, sport):
    """Promoted timer meets on that day carrying a race of that sport."""
    cur.execute("""SELECT m.ext_meet_id, m.meet_name, m.meet_date, m.state, count(*)
                   FROM meets_ext m JOIN races_ext r USING (ext_meet_id)
                   WHERE m.status = 'promoted' AND m.meet_date = %s AND r.sport = %s
                   GROUP BY 1, 2, 3, 4""",
                (_iso(date), sport))
    return cur.fetchall()


def _close(cur, meet_id, sport, ext, how):
    cur.execute("""INSERT INTO anet_local_twin (meet_id, sport, ext_meet_id, how)
                   VALUES (%s, %s, %s, %s) ON CONFLICT (meet_id, sport) DO NOTHING""",
                (meet_id, sport, ext, how))
    cur.execute("""INSERT INTO meet_queue (meet_id, sport, source, scraped)
                   VALUES (%s, %s, 'anet', 1)
                   ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 1
                   WHERE meet_queue.scraped IN (0, 2, 4)""", (meet_id, sport))


def skipFor(meet_id, sport, meet_info, n_divs=None):
    """Called by the launcher right after GetMeetData. Keeps what the meet
    info said, and -> the timer meet's ext_meet_id when a promoted timer
    meet is this meet (skip athletic.net's results calls), else None.
    Never raises: a database hiccup means scrape as before."""
    try:
        from database import getConn
        loc = (meet_info or {}).get("Location") or {}
        name, state = meet_info.get("Name"), loc.get("State")
        date = _iso(str(meet_info.get("MeetDate") or "").split("T")[0]) or None
        if date and not re.match(r"^(19|20)\d\d-\d\d-\d\d$", date):
            date = None
        with getConn() as conn:
            with conn.cursor() as cur:
                ensureTables(cur)
                cur.execute("""INSERT INTO anet_meet_seen (meet_id, sport, name, meet_date, state, n_divs)
                               VALUES (%s, %s, %s, %s, %s, %s)
                               ON CONFLICT (meet_id, sport) DO UPDATE SET name = EXCLUDED.name,
                                 meet_date = EXCLUDED.meet_date, state = EXCLUDED.state,
                                 n_divs = EXCLUDED.n_divs, seen_at = now()""",
                            (meet_id, sport, name, date, state, n_divs))
                ext = (match(name, date, state, _timerMeets(cur, date, sport), n_divs)
                       if date and sport == "XC" else None)
                if ext is not None:
                    cur.execute("""INSERT INTO anet_local_twin (meet_id, sport, ext_meet_id, how)
                                   VALUES (%s, %s, %s, 'meet info') ON CONFLICT DO NOTHING""",
                                (meet_id, sport, ext))
            conn.commit()
        return ext
    except Exception as exc:                                         # noqa: BLE001
        print(f"[anet_twins] check skipped for meet {meet_id} ({type(exc).__name__}: {exc})")
        return None


def cover(cur, days=21):
    """Close the athletic.net queue rows of meets we have already seen whose
    timer copy is promoted (no request). -> [(meet_id, sport, ext)]."""
    ensureTables(cur)
    cur.execute("""SELECT s.meet_id, s.sport, s.name, s.meet_date, s.state, s.n_divs
                   FROM anet_meet_seen s
                   WHERE s.sport = 'XC' AND s.meet_date >= current_date - %s
                     AND NOT EXISTS (SELECT 1 FROM anet_local_twin t
                                     WHERE t.meet_id = s.meet_id AND t.sport = s.sport)""",
                (days,))
    seen = cur.fetchall()
    out = []
    for meet_id, sport, name, date, state, n_divs in seen:
        res = "results" if sport == "XC" else "results_tf"
        cur.execute(f"SELECT 1 FROM {res} WHERE meet_id = %s AND source = 'anet' LIMIT 1", (meet_id,))
        if cur.fetchone():
            continue                       # athletic.net's copy is in: dedup's business
        ext = match(name, date, state, _timerMeets(cur, date, sport), n_divs)
        if ext is not None:
            _close(cur, meet_id, sport, ext, "seen before")
            out.append((meet_id, sport, ext))
    return out


def twinIds(cur, sport):
    """athletic.net meet ids a timer copy stands in for (queue_meets skips
    them in its re-ask passes). Empty when the table is not there yet."""
    cur.execute("SELECT to_regclass('public.anet_local_twin') IS NOT NULL")
    if not cur.fetchone()[0]:
        return set()
    cur.execute("SELECT meet_id FROM anet_local_twin WHERE sport = %s", (sport,))
    return {r[0] for r in cur.fetchall()}


def main(argv=None):
    import argparse
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    from database import getConn
    ap = argparse.ArgumentParser(description="close athletic.net queue rows for meets a timer gave us")
    ap.add_argument("--days", type=int, default=21)
    a = ap.parse_args(argv)
    with getConn() as conn:
        with conn.cursor() as cur:
            got = cover(cur, a.days)
        conn.commit()
    for meet_id, sport, ext in got:
        print(f"  anet {sport} {meet_id} = timer meet {ext}: closed")
    print(f"[anet_twins] {len(got)} athletic.net meets closed (a timer's copy is in)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
