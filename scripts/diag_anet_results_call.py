#!/usr/bin/env python3
"""
diag_anet_results_call.py -- why GetAllResultsData answers 403 for a meet
athletic.net plainly shows results for. READ ONLY; writes nothing.

    cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
    NO_VPN=1 xvfb-run -a /srv/venv/bin/python -u scripts/diag_anet_results_call.py 288317 1149714

★ WHY (owner, 2026-10-23): the first scrape on the new server got HTTP 403
  with an empty body from GetAllResultsData for 288317 while GetMeetData
  answered 200 in the same browser. The owner: find out why the whole-meet
  call is refused -- no per-race fallback. One browser, the launcher's own
  launch and context:
    1. the meet's division results page and its "all results" page, the
       way a person opens them: every athletic.net /api/ call each page
       makes, with the request's anet-* headers and the answer -- if the
       site's own page calls GetAllResultsData and gets 200, the difference
       is in how we ask; if it gets 403 too, athletic.net changed it;
    2. the scraper's own call, then the same call with the headers the
       page sent (its anet-appinfo, its token), one at a time, so whichever
       one flips 403 to 200 names the cause;
    3. the jwtMeet token's claims (not the signature).
  Run it on the old server too: the same answer there means athletic.net
  changed, not the new server.
"""
import asyncio
import base64
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_FETCH_JS = """
async (a) => {
    const r = await fetch(a.url, {method: a.method, headers: a.headers,
                                  body: a.body || undefined});
    return {status: r.status, text: (await r.text()).slice(0, 160)};
}
"""


def _claims(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception as exc:                                # noqa: BLE001
        return f"<not a JWT: {type(exc).__name__}>"


def _anetHeaders(headers):
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl.startswith("anet") or kl in ("authorization", "content-type",
                                            "x-requested-with", "referer"):
            out[k] = (v[:24] + f"...({len(v)})") if kl in ("anettokens", "authorization") and len(v) > 30 else v
    return out


async def main(meet_id, div_id):
    from playwright.async_api import async_playwright
    import launcher as L
    import scraper as S

    async with async_playwright() as pw:
        browser = await L._launchBrowser(pw)
        ctx = await L._buildContext(browser, 0)
        page = await ctx.new_page()
        print(f"browser {browser.version}; UA {await page.evaluate('navigator.userAgent')}")

        calls = []

        async def onResponse(resp):
            if "athletic.net/api/" not in resp.url:
                return
            try:
                body = await resp.text()
            except Exception as exc:                        # noqa: BLE001
                body = f"<unreadable: {type(exc).__name__}>"
            req = resp.request
            try:
                hdrs = await req.all_headers()
            except Exception:                               # noqa: BLE001
                hdrs = req.headers
            calls.append({"method": req.method, "status": resp.status,
                          "url": resp.url, "headers": hdrs,
                          "post": req.post_data, "n": len(body),
                          "head": body[:120].replace("\n", " ")})

        page.on("response", onResponse)
        base = f"https://www.athletic.net/CrossCountry/meet/{meet_id}/results"
        for url in (f"{base}/{div_id}", f"{base}/all"):
            calls.clear()
            print(f"\n== 1. {url}")
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(8000)
            print(f"   landed on {page.url}")
            for c in calls:
                if any(x in c["url"] for x in ("Footer", "Blog", "Media", "Time_Get",
                                               "Synonyms", "SignedInUser")):
                    continue
                print(f"   {c['method']} {c['status']} {c['n']:>8,}  {c['url'][:140]}")
                print(f"        headers {_anetHeaders(c['headers'])}")
                if c["post"]:
                    print(f"        body sent {c['post'][:120]!r}")
                print(f"        answer {c['head']!r}")
            page_calls = list(calls)

        # what the page used, to replay
        page_appinfo = page_token = None
        for c in page_calls[::-1] + calls[::-1]:
            h = {k.lower(): v for k, v in c["headers"].items()}
            page_appinfo = page_appinfo or h.get("anet-appinfo")
            page_token = page_token or h.get("anettokens")

        print(f"\n== 2. the scraper's call, then variants (meet {meet_id})")
        info, divisions = await S.getMeetData(page, meet_id)
        jwt = info.get("jwtMeet", "")
        print(f"   GetMeetData: HasResults={info.get('HasResults')} divisions={len(divisions)} "
              f"jwtMeet={len(jwt)} chars")
        url = f"/api/v1/Meet/GetAllResultsData?meetId={meet_id}&rawResults=false&showTips=false"
        variants = [
            ("ours (anettokens=jwtMeet, anet-appinfo web:web:0:240)",
             {"anettokens": jwt, "anet-appinfo": "web:web:0:240"}),
            ("no anet-appinfo", {"anettokens": jwt}),
            (f"the page's anet-appinfo {page_appinfo!r}",
             {"anettokens": jwt, "anet-appinfo": page_appinfo or "web:web:0:240"}),
            ("the page's own anettokens",
             {"anettokens": page_token or jwt, "anet-appinfo": page_appinfo or "web:web:0:240"}),
            ("no token at all", {"anet-appinfo": page_appinfo or "web:web:0:240"}),
        ]
        for name, headers in variants:
            try:
                r = await page.evaluate(_FETCH_JS, {"url": url, "method": "GET",
                                                    "headers": headers})
                print(f"   {r['status']}  {name}   {r['text'][:80]!r}")
            except Exception as exc:                        # noqa: BLE001
                print(f"   ERR {name}: {type(exc).__name__}: {exc}")
            await asyncio.sleep(3)

        print("\n== 3. jwtMeet claims")
        print(f"   {_claims(jwt)}")
        if page_token and page_token != jwt:
            print(f"   the page's token differs; its claims: {_claims(page_token)}")
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: diag_anet_results_call.py <meet_id> <div_id>")
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
