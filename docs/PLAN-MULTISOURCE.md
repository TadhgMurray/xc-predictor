# Many sources, each lightly: the distributed results scraper (2026-10-23)

**Why.** athletic.net closed its whole-meet results call. A track meet is now
39 calls and a cross country meet 4.6, so athletic.net alone means one site
carrying all our load. The owner wants the opposite: "a distributed system so
we can spread load so we don't get banned ... the main thing is reducing
load/speed-up by parallelizing." Scraping athletic.net live "a bit is fine as
long as we do it less."

**The idea.** Speed comes from breadth. Hundreds of timing companies publish
results on their own sites (docs/TIMING-COMPANIES.md, docs/timing_companies.csv).
Each one sees one request at a time, seconds apart. Forty hosts in flight is
forty times the throughput while every site still sees a trickle. athletic.net
becomes one source among many, used for what no one else has.

## What exists

| Piece | File | What it does |
|---|---|---|
| Polite parallel fetcher | `sources/fetch.py` | Allows one request in flight per host, `XCP_SOURCE_INTERVAL` (default 10 s) apart with jitter. Runs hosts in parallel under a global cap (`XCP_SOURCE_IN_FLIGHT`, default 8). Honours robots.txt and its Crawl-delay. A 429 or 503 doubles that host's interval. Keeps every body under `XCP_RAW_DIR` (`/srv/raw`): gzipped, indexed in sqlite, refetched with If-None-Match / If-Modified-Since. A finished meet is never fetched twice (`max_age`). |
| Hy-Tek report parser | `sources/hytek.py` | Reads Meet Manager results reports from Crystal Reports HTML (positioned divs), `<pre>` pages and PDFs (`pdftotext -layout`, poppler-utils). Gives the meet name, date, venue and timer, plus each event's gender, distance and rows (place, name, grade, school, time, points). Team scores are skipped. Checked on a real YenTiming page: 44 of 44 rows parsed. |
| Tests | `tests/test_sources_fetch.py`, `tests/test_sources_hytek.py` | Use synthetic names. |

## Next

1. **Source registry** (`sources/registry.py`): one entry per timer with
   - its adapter (Hy-Tek directory, MeetPro, custom),
   - its index URL,
   - a per-host interval override,
   - `enabled`,
   - a `terms` note.

   PTTiming (terms ban bots) and AthleticLIVE (athletic.net's, light use only) are flagged here.
2. **Adapters**, by the number of meets they unlock:
   - **Hy-Tek directory walker.** YenTiming's open directories: `/<year>/cross/<meet>/`, page files `X.html` then `X2.html`, plus PDFs.
   - **MeetPro parser.** `results.tfmeetpro.com/<Timer>/<Meet>/` and `rtspt.com/.../mp/`. One layout everywhere, always with grade and team. Mitchell Timing has about 740 meets, B&B about 350, MAC-FAT about 200.
   - **Small adapters:** Finished Results (CA/AZ, about 1,400 meets), GSE (MN), Black Squirrel (NE), TRXC, SportTrax (UT).
3. **Landing table** `results_ext`: one row per runner per race, with
   - `source = 'timer:<slug>'`,
   - the meet, race and distance,
   - name, grade, school, time and place,
   - `person_id` NULL.

   Linking then uses the same id-less linkers TFRRS rows go through (`link_idless_by_name`, `link_teamless`, `link_feed_twins`). The same meet arriving from athletic.net and a timer is one meet: match on date, venue and the runners, as `link_feed_twins` does for TFRRS.
4. **The job**: a daemon or timer that walks every enabled source's index, fetches new meets in parallel, parses them and lands them. It runs beside the overnight athletic.net cross country scrape rather than inside its 3-hour window.

## Robots.txt policy

- A 4xx means there is no robots.txt, so fetching is allowed.
- A 5xx disallows that host for the run (RFC 9309).
- **YenTiming exception.** YenTiming's robots.txt answers 500. The owner approved an exception (2026-10-23): it is crawled as if it had no robots.txt, at twice the usual interval (`ROBOTS_ERROR_ALLOW`, or `XCP_ROBOTS_ERROR_ALLOW` for more hosts).
- **Defaults.** 10 s between one host's requests, at most 8 hosts in flight ("let's just try to be nice to all these sites").

## What counts as off limits (owner, 2026-10-23)

"If it doesn't outright say no results scraping it is fine."

- **Off limits:**
  - Terms that explicitly forbid scraping, bots or automated collection (PTTiming and its white labels, Leone, TimerHub).
  - A robots.txt that disallows the results pages for every crawler (`*`) or for us (ItsYourRace's results, eccathletics.org's results pages).
  - A bot challenge (Cloudflare, WatchAthletics). It is never worked around.
- **Fine:**
  - "Personal or non-commercial use" terms (SIDEARM college and association sites).
  - A robots.txt that blocks only named AI crawlers (ClaudeBot, GPTBot, CCBot), since our fetcher is not one. This covers SIDtools leagues, chsaanow, IHSAA and ChronoTrack. The fetcher reads robots.txt for "racecast-results" and "*", so the rules a site writes for all crawlers still apply.
  - No terms or robots.txt at all.
