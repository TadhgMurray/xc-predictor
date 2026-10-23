#!/usr/bin/env python3
"""
diag_anet_tf_calls.py -- with athletic.net's whole-meet call gone, the
cheapest way to get a track meet's results. READ ONLY; writes nothing.

    cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
    NO_VPN=1 xvfb-run -a /srv/venv/bin/python -u scripts/diag_anet_tf_calls.py [meet_id]

★ WHY (owner, 2026-10-23): GetAllResultsData answers 403 to everyone not
  signed in (diag_anet_results_call.py), and the per-race call costs a busy
  spring week 149,169 requests (3,885 meets x 39 event-divisions) -- ~150 h at
  the overnight pace. Cross country is 2,335 races a week and fits. Three
  questions for track:
    1. how athletic.net's own track results page asks for results (its
       /api/ calls and the bodies it posts);
    2. whether GetResultsData3 answers a whole DIVISION when no event is
       named -- if it does, a track meet costs about what a cross country
       meet does;
    3. how many of a spring's event-divisions the engine can use at all
       (>= 800 m, not a relay), from meets_tf.
  Without a meet id it takes a big spring 2026 meet from the database.
"""
import asyncio
import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_POST_JS = """
async (a) => {
    const r = await fetch('/api/v1/Meet/GetResultsData3', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'anettokens': a.token,
                  'anet-appinfo': 'web:web:0:0'},
        body: JSON.stringify(a.body)});
    return {status: r.status, text: await r.text()};
}
"""

_RELAY = r"(x|relay|smr|dmr|shuttle|medley)"


def _pickMeet(cur):
    cur.execute("""
        SELECT m.meet_id, count(*) FROM meets_tf m JOIN meets_tf_meta mm USING (meet_id)
        WHERE mm.meet_date >= '2026-04-01' AND mm.meet_date < '2026-05-15'
        GROUP BY m.meet_id ORDER BY count(*) DESC OFFSET 200 LIMIT 1""")
    row = cur.fetchone()
    return row[0] if row else None


def _usableShare(cur):
    cur.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE m.distance_meters >= 800
                                AND m.event_short !~* '{_RELAY}')
        FROM meets_tf m JOIN meets_tf_meta mm USING (meet_id)
        WHERE mm.meet_date >= '2026-03-01' AND mm.meet_date < '2026-06-16'""")
    return cur.fetchone()


async def main(meet_id):
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        total, usable = _usableShare(cur)
        if meet_id is None:
            meet_id = _pickMeet(cur)
        conn.rollback()
    print(f"== 3. spring 2026 event-divisions: {total:,}; >= 800 m and not a relay: "
          f"{usable:,} ({100.0 * usable / max(total, 1):.0f}%)")
    print(f"   meet {meet_id}")

    from playwright.async_api import async_playwright
    import launcher as L
    import scraper as S

    async with async_playwright() as pw:
        browser = await L._launchBrowser(pw)
        ctx = await L._buildContext(browser, 0)
        page = await ctx.new_page()

        calls = []

        async def onResponse(resp):
            if "athletic.net/api/" not in resp.url or "Footer" in resp.url:
                return
            try:
                n = len(await resp.text())
            except Exception:                               # noqa: BLE001
                n = -1
            calls.append((resp.request.method, resp.status, n, resp.url,
                          resp.request.post_data))

        page.on("response", onResponse)
        url = f"https://www.athletic.net/TrackAndField/meet/{meet_id}/results"
        print(f"\n== 1. {url}")
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(8000)
        print(f"   landed on {page.url}")
        for method, status, n, u, body in calls:
            print(f"   {method} {status} {n:>8,}  {u[:120]}" + (f"  body {body[:100]!r}" if body else ""))
        # what the track results page loads instead of results
        r = await page.evaluate("""async () => {
            const r = await fetch('/api/v1/Meet/GetEventListData', {headers: {'anet-appinfo': 'web:web:0:0'}});
            return (await r.text()).slice(0, 400); }""")
        print(f"   GetEventListData starts: {r!r}")

        info, events, event_divs = await S.getMeetDataTF(page, meet_id)
        jwt = info.get("jwtMeet", "")
        by_div = Counter(ed.get("d") for ed in event_divs)
        print(f"\n== 2. meet {meet_id}: {len(event_divs)} event-divisions over "
              f"{len(by_div)} divisions (the per-race calls today: {len(event_divs)})")
        if not by_div:
            await browser.close()
            return
        div_id, n_events = by_div.most_common(1)[0]
        one = next(ed for ed in event_divs
                   if ed.get("d") == div_id and ed.get("e") in events)
        short = events[one["e"]]["event_short"]
        gender = events[one["e"]]["gender"]
        print(f"   division {div_id}: {n_events} events (one of them {short!r}, gender {gender!r})")
        # ! THE BODY getMeetResultsTF SENDS (run 1 left out gender and the two
        #   flags, and even today's call came back empty)
        flags = {"rawResults": False, "showTips": False}
        for name, body in (
                ("today's call: gender+divId+eventShort", {"gender": gender, "divId": div_id, "eventShort": short, **flags}),
                ("gender+divId, no event", {"gender": gender, "divId": div_id, **flags}),
                ("gender+divId, empty event", {"gender": gender, "divId": div_id, "eventShort": "", **flags}),
                ("divId, no gender, no event", {"divId": div_id, **flags}),
                ("gender+eventShort, no division", {"gender": gender, "eventShort": short, **flags})):
            r = await page.evaluate(_POST_JS, {"token": jwt, "body": body})
            rows, evs = [], Counter()
            try:
                parsed = json.loads(r["text"])
                rows = parsed.get("resultsTF") or []
                if rows and isinstance(rows[0], list):
                    rows = [x for grp in rows for x in grp]
                for x in rows:
                    if isinstance(x, dict):
                        evs[x.get("EventShort") or x.get("Event") or x.get("IDEvent")] += 1
                keys = sorted(parsed)[:10]
            except Exception:                               # noqa: BLE001
                keys = r["text"][:80]
            print(f"   {r['status']}  {name:<34} {len(rows):>5} rows, "
                  f"{len(evs)} events {dict(list(evs.items())[:6])}  keys {keys}")
            await asyncio.sleep(3)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else None))
