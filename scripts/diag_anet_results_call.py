#!/usr/bin/env python3
"""
diag_anet_results_call.py -- why GetAllResultsData comes back empty for a
meet athletic.net plainly shows results for. READ ONLY; writes nothing.

    cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
    NO_VPN=1 xvfb-run -a /srv/venv/bin/python -u scripts/diag_anet_results_call.py 288317 1149714

★ WHY (owner, 2026-10-23): the first scrape on the new server failed past
  meets with "claims N divisions but GetAllResultsData returned no
  flatEvents (HasResults=1, payload [])" -- 288317 among them, whose results
  page shows results. One browser, the launcher's own launch and context:
    1. open the meet's results page the way a person does, and print every
       /api/ call the page itself makes (status, size, first characters) --
       if athletic.net moved to another endpoint or parameter, it shows here;
    2. make the scraper's own two calls (GetMeetData, GetAllResultsData) for
       the same meet and print what came back.
"""
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


async def main(meet_id, div_id):
    from playwright.async_api import async_playwright
    import launcher as L
    import scraper as S

    async with async_playwright() as pw:
        browser = await L._launchBrowser(pw)
        ctx = await L._buildContext(browser, 0)
        page = await ctx.new_page()
        print(f"browser {browser.version}; UA {await page.evaluate('navigator.userAgent')}")
        # the context's UA says Chrome 125-128; the client hints say the
        # real binary's version -- a mismatch a site can see
        print("client hints: " + str(await page.evaluate(
            "navigator.userAgentData ? JSON.stringify(navigator.userAgentData.brands) : 'none'")))

        seen = []

        async def onResponse(resp):
            if "/api/" not in resp.url:
                return
            try:
                body = await resp.text()
            except Exception as exc:                        # noqa: BLE001
                body = f"<unreadable: {type(exc).__name__}>"
            seen.append((resp.request.method, resp.status, len(body),
                         resp.url, body[:140].replace("\n", " ")))

        page.on("response", onResponse)
        url = f"https://www.athletic.net/CrossCountry/meet/{meet_id}/results/{div_id}"
        print(f"\n== 1. the page itself: {url}")
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(8000)
        rows = await page.evaluate(
            "document.querySelectorAll('tr, .result-row, [class*=result]').length")
        print(f"   page elements that look like result rows: {rows}")
        for method, status, n, u, head in seen:
            print(f"   {method} {status} {n:>8,}  {u[:150]}")
            print(f"        {head!r}")

        print(f"\n== 2. the scraper's calls for meet {meet_id}")
        try:
            info, divisions = await S.getMeetData(page, meet_id)
            print(f"   GetMeetData: ID={info.get('ID')} date={info.get('MeetDate')} "
                  f"HasResults={info.get('HasResults')} divisions={len(divisions)} "
                  f"jwtMeet={'yes' if info.get('jwtMeet') else 'NO'}")
            payload = await S.getAllResultsXC(page, meet_id, info.get("jwtMeet", ""))
            resp = getattr(S, "LAST_XC_RESPONSE", {})
            fe = payload.get("flatEvents") if isinstance(payload, dict) else None
            print(f"   GetAllResultsData: HTTP {resp.get('status')}, "
                  f"keys {sorted(payload)[:8] if isinstance(payload, dict) else payload!r}, "
                  f"flatEvents {len(fe) if fe else 0}")
            print(f"        body {resp.get('head')!r}")
        except Exception as exc:                            # noqa: BLE001
            print(f"   failed: {type(exc).__name__}: {exc}")
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: diag_anet_results_call.py <meet_id> <div_id>")
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
