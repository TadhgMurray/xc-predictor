# Cloudflare: answering more at the edge (2026-11-08)

The goal is for the origin to do less work. Cloudflare's "percent cached" is
the wrong number to watch, for two reasons. Every page a person reads sends
one `POST /api/b` (the usage beacon), which can never be cached. And a
crawler walking millions of athlete pages fetches each one once. The number
to watch is **origin requests per day**: the nginx access log line count, or
Cloudflare's Analytics > Traffic "Served by origin". Measure it for a week
before and after these steps.

## What the live site does today (measured 2026-11-08, two GETs each)

| page type | 1st / 2nd request | why |
|---|---|---|
| HTML pages (`/`, athlete, race, meet, school, course, unit, landing, `/rankings`) | MISS / HIT | cached by a Cache Rule. Cached copies were up to 11 h old (`/rankings` Age 39,513, `/predictions` 37,237, robots.txt 41,202) although the origin says `s-maxage=900`, so the HTML rule **overrides the origin's TTL** (see step 2). |
| the same page + `?utm_source=`, `?fbclid=`, a trailing `?` or `&` | MISS / HIT | separate cache entries, rendered again by the origin. Fixed in the app: now a public 301 to the plain URL. |
| `/api/rankings`, `/api/teams`, `/api/courses`, `/api/rankings/rank`, `/search`, `/search/api`, next-race, meet-preview, runners-like-you | MISS / HIT | the "Board APIs" rule, origin TTL 900 s. Reordered parameters or a trailing `&` are a separate MISS (Cloudflare does not sort the query). |
| `/api/rankings/units` (Conferences tab, 155 KB, ~0.7 s) | DYNAMIC / DYNAMIC | the origin sent `no-store` (it is under `/api/`), and it is not in the rule. Fixed in the app; needs the rule (step 2). |
| `/api/updates/latest` (asked on every reader's first view in 10 min) | DYNAMIC / DYNAMIC | `max-age=300` with no `s-maxage`, and not in the rule. Fixed in the app; needs the rule. |
| `/api/explain/...` and the picker lookups (`/api/schools`, `/api/units`, ...) | DYNAMIC | `no-store` from the `/api/` default. Now public; needs the rule. |
| `POST /api/b`, `/api/me`, `/account/*` | DYNAMIC | per reader, correctly. |
| `/static/*` | HIT once warm | nginx sends `max-age=604800`. |
| HEAD on any page | 403 | Cloudflare's bot protection; GET is fine. |

The headers were already correct: no `Set-Cookie`, `Vary: Accept-Encoding`
only, `public, max-age=300, s-maxage=900, stale-if-error=86400`. Two copies
of one cached page can still differ in bytes. That is Cloudflare's bot
script, which writes the ray id into each copy (`__CF$cv$params`), not the
origin.

## 1. Tiered Cache (free, 1 minute)

Caching > Tiered Cache > **Smart Tiered Caching Topology: On**. Edge
locations then fill from one upper-tier location instead of each asking the
origin. A board or page that people in several cities read in the same 15
minutes then reaches the origin once instead of once per city.

## 2. Cache Rules (Caching > Cache Rules)

Rules run top to bottom. Edit the two rules that exist. Their names in the
dashboard may differ; one covers the board APIs and one covers HTML.

**Rule A: "Board and lookup APIs".** Edit expression:

    (http.request.method eq "GET" and (
      starts_with(http.request.uri.path, "/api/rankings")
      or http.request.uri.path in {"/api/teams" "/api/courses" "/search" "/search/api"
        "/api/updates/latest" "/api/schools" "/api/units" "/api/meet-units"
        "/api/xc_courses" "/api/course_search" "/api/equivalence" "/api/rating_5k"
        "/api/athlete_results" "/api/athlete_pool" "/api/predict/upcoming"}
      or starts_with(http.request.uri.path, "/api/next-race/")
      or starts_with(http.request.uri.path, "/api/meet-preview/")
      or starts_with(http.request.uri.path, "/api/runners-like-you/")
      or starts_with(http.request.uri.path, "/api/explain/")
      or starts_with(http.request.uri.path, "/api/predict/share/")
      or (starts_with(http.request.uri.path, "/api/meet/") and ends_with(http.request.uri.path, "/find"))))

- Cache eligibility: **Eligible for cache**
- Edge TTL: **Use cache-control header if present, bypass cache if not**.
  The origin sends `no-store` on every error and every per-reader answer,
  and that must win.
- Browser TTL: **Respect origin**
- Cache key: turn on **Ignore query string order / Sort query string** if
  your plan shows the option. If it does not, the page's own requests and
  the Worker in step 6 already send parameters in one order.
- Serve stale content while revalidating: **On**, if offered.

**Rule B: "Public pages" (HTML).** Expression:

    (http.request.method eq "GET"
     and not starts_with(http.request.uri.path, "/api/")
     and not starts_with(http.request.uri.path, "/account")
     and not starts_with(http.request.uri.path, "/login")
     and not starts_with(http.request.uri.path, "/logout")
     and not starts_with(http.request.uri.path, "/auth/")
     and not starts_with(http.request.uri.path, "/debug/")
     and not starts_with(http.request.uri.path, "/compare"))

- Eligible for cache. Edge TTL: **Use cache-control header if present,
  bypass cache if not**. If it is set to "Ignore cache-control header and
  use this TTL" now (the 11-hour ages above suggest so), pages and their
  numbers stay stale for that long after a pipeline go-live, because the
  purge in step 4 is not configured yet. Respecting the origin keeps 15
  minutes until step 4 is done; step 5 then raises it safely.
- Browser TTL: Respect origin.
- Cache key: if your plan offers query-string exclusion, exclude `utm_source
  utm_medium utm_campaign utm_term utm_content fbclid gclid msclkid`.
  Otherwise the origin's new 301 does the same with one cheap round trip.
- Do not add a "bypass on cookie" condition. Every page renders signed out
  by design (the account bits load from `/api/me` after the page), and the
  origin now forces `private, no-store` onto anything that sets a cookie or
  read the session (racecast/edge_cache.py).

## 3. Crawler Hints (free)

Caching > Configuration > **Crawler Hints: On**. When a cached page changes,
Cloudflare tells IndexNow engines (Bing, Yandex, Seznam). They then recrawl
what changed instead of re-walking the whole site. Yandex alone was 11k
origin requests in two hours in the September log.

## 4. The go-live purge (do this before step 5)

docs/PERF-2026-11-06.md step 7: a token with **Zone > Cache Purge > Purge**
on racecast.co, plus `XCP_CF_ZONE_ID` and `XCP_CF_API_TOKEN` in
`/etc/xc-predictor.env`, then test with `scripts/edge_purge.py`. Optional:
if your plan offers **Custom Purge > Prefix**, purging `racecast.co/` minus
`/static/`, `/img/` and `/card/` keeps versioned assets and crests warm.
Today's purge-everything also drops them. It costs origin bandwidth, not
renders.

## 5. Edge TTL longer than 15 minutes (only once step 4 prints "ok")

In `/etc/xc-predictor.env`, then restart the site:

    XCP_PAGE_S_MAXAGE=21600
    XCP_PAGE_SWR=600

This sets 6 hours at the edge, ended early by the purge at every go-live.
Browsers keep 5 minutes. `stale-while-revalidate` lets an expired copy
answer at once while one request refreshes it.

## 6. The edge Worker: board answers from Workers KV (prototype, optional)

`worker.js` runs only on `/api/rankings*`, `/api/teams*`, `/api/courses*`
and `/search/api*`. It puts the URL in canonical form (tracking parameters
and `offset=0` dropped, parameters sorted: the same rule as
`racecast/edge_cache.py` and `static/board-url.js`, held equal by
tests/test_edge_cache.py). It answers from the snapshot published after the
last go-live when that snapshot has the URL. Otherwise it asks the origin
for the canonical URL, so every spelling shares one cache entry. The stored
bytes are the live app's own answers. `scripts/edge_snapshot.py` fetches
them from gunicorn and keeps only 200 JSON that the app marked public.

**First, measure** what it would absorb. On the server:

    cd /srv/xc-predictor
    sudo /srv/venv/bin/python scripts/edge_snapshot.py --estimate /var/log/nginx/access.log --train /var/log/nginx/access.log.1 --top 200

It picks the 200 most-requested canonical board URLs from yesterday's log
and reports what share of today's origin board and search requests they
cover (`snapshot_share_of_routes`) and what share of all origin requests
(`snapshot_share_of_origin`). Deploy only if that share is worth it. The
log holds origin requests only, so this is origin work saved, which is
exactly the goal.

**Deploy** (about 15 minutes, needs Node on any machine):

1. `npx wrangler login`, then from `deploy/cloudflare/`:
   `npx wrangler kv namespace create racecast-snap`. Paste the id into
   wrangler.toml.
2. `npx wrangler deploy`.
3. Workers & Pages > racecast-board-edge > Settings > Triggers > each route
   > Failure mode: **Fail open (proceed)**. If the Worker errors or runs
   over its daily limit, requests go to the origin as today.
4. My Profile > API Tokens > Create Token > Custom: **Account > Workers KV
   Storage > Edit**, scoped to this account. Add to
   `/etc/xc-predictor.env`:

       XCP_CF_ACCOUNT_ID=<account id, from the zone Overview page>
       XCP_CF_KV_NAMESPACE=<the namespace id>
       XCP_CF_KV_TOKEN=<the token>

5. Look before publishing:
   `/srv/venv/bin/python scripts/edge_snapshot.py --from-log /var/log/nginx/access.log.1 --top 200 --dry-run /tmp/snap`
   (`/tmp/snap/index.json` lists every URL and size), then publish without
   `--dry-run`.
6. Check it:
   `curl -s -D - -o /dev/null "https://racecast.co/<one of the URLs>" | grep -i x-racecast-edge`
   should print `snapshot <generation>`.
7. Wire it into the runs. In `deploy/run_pipeline.sh` and
   `deploy/nightly_update.sh`, beside the first purge add
   `"$PY" -u scripts/edge_snapshot.py --off || true`. This stops old boards
   being served the moment new ones go live. After the delayed second purge
   (the `--delay 960` line), chain the publish:
   `... edge_purge.py --delay 960 && "$PY" -u scripts/edge_snapshot.py --from-log /var/log/nginx/access.log.1 --top 200`.
   After hiding an athlete (scripts/optout.py), run `--off`, then republish
   after the restart.

**Cost.** Workers Free covers 100,000 requests a day, KV 100,000 reads a
day, 1,000 writes a day and 1 GB. The Worker sees every request on its
routes, cache hits included, so read the daily count of `/api/rankings`,
`/api/teams`, `/api/courses` and `/search/api` in Analytics > Traffic
first. Under ~80k a day, Free is enough. Over that, Workers Paid is $5 a
month and includes 10M requests and 10M KV reads. One publish of 200 URLs
is 201 KV writes, about 400 a day with the pipeline and the nightly run.
`--search`, the 676 two-letter prefixes the search box asks first, needs
the paid plan's write allowance. Storage is a few MB.

**Rollback**, quickest first:
- `scripts/edge_snapshot.py --off`: the Worker stops answering from KV
  within 60 s and only canonicalises URLs.
- Workers Routes: delete the four routes. Requests go to the origin and
  cache exactly as today.
- `npx wrangler delete`. Nothing in the app depends on the Worker.

## 7. Not recommended now: Cache Reserve

Cache Reserve keeps cached objects in R2 so they are not evicted. It only
takes objects with an edge TTL of 10 hours or more, which nothing here has
until step 5. The go-live purge empties it every night. It bills one
write per cache fill (about $4.50 per million). At the September level of
~460k origin requests a day, that is roughly $60 a month in writes alone,
plus reads and storage (athlete pages are 300-650 KB). Crawlers revisit a
given athlete page far less often than once a day, so few of those writes
would ever be read back. Revisit after step 5 has run for a month and the
purge is by prefix.

## 8. Bots: fewer requests, not more hits

Most origin requests in the September log were crawlers and one scraper
rotating fourteen browser user agents. No cache rule helps a single-pass
crawl. What helps:

- Security > Bots: **Block AI Scrapers and Crawlers** (your call on which
  AI crawlers to allow).
- Security > WAF > Rate limiting rules (Free includes one), using whichever
  action your plan offers (managed challenge or block):

      (starts_with(http.request.uri.path, "/athlete/") or starts_with(http.request.uri.path, "/race/")
       or starts_with(http.request.uri.path, "/meet/")) and not cf.client.bot

  Count by IP, 60 requests per 10 seconds. `cf.client.bot` keeps Google,
  Bing and other verified crawlers out of the rule.
