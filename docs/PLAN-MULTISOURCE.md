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

## Built 2026-10-25: landing, dedup, promotion, linking, the HS TFRRS sites

| Piece | File | What it does |
|---|---|---|
| Landing tables | `sources/land.py` | `meets_ext` (one row per timer meet page, `source = 'timer:<slug>'`), `races_ext` (one per race, with the **timer's own distance**, `meters_stated`), `results_ext` (one per runner). A re-land that changes nothing is a no-op; a promoted meet is never rewritten. |
| Meet dedup | `sources/dedup.py` | Each landed race is judged against every anet / TFRRS / upload / promoted-timer row on its day ±1. The runners are the evidence: same day, same time to the printed precision, and the same name (or the same school with one partner each way). 3 name matches, or 3 matches that are 30% of the finishers, make a race a **twin**. Meet status: `twin`, `partial` or `new`. A twin race's feed division and distance are kept beside the timer's (`twin_div_id`, `twin_meters`); `status` lists the ones that disagree by more than 3%. |
| Promotion | `sources/promote.py` | The races no feed has go into `meets` + `results` (XC) or `meets_tf_meta` + `meets_tf` + `results_tf` (TF), shaped like an approved upload: `source = 'timer'`, the slug in `id_system`, `meet_id = -(700,000,000 + ext_meet_id)`, no `person_id`. |
| Linking | `scripts/link_uploads.py --source timer` | The upload linker (name + school + generation, the teamless / profile_school / feed_twins rules), with its own rules `timer` / `timer_mint` and report `timer_link_report`. It runs as part of pass 4 of `link_tfrrs_rows.py` (04a). `twin_flag`'s `twin_upload`, `person_gender` and `backfill_normalize` read `timer` rows as they read uploads. |
| The job's steps | `sources/timers.py` | `fetch` (fetches and parses into a file, no database), `land`, `dedup`, `promote`, `status`. Each one is a dry run until `--write` / `--apply`. Adapters: MeetPro meet index, a Hy-Tek report (HTML, `<pre>` or PDF, and its page files), or a Hy-Tek directory (YenTiming). |
| HS TFRRS sites | `sources/tfrrs_hs.py` | fl.tfrrs.org and indiana.tfrrs.org are views of TFRRS's one database: the same meet ids and athlete ids as www.tfrrs.org, which the existing scraper already walks. `index` reads each site's `results_search.html` list. `check` says what we hold and how it is linked. `queue` puts meets with no rows back in `meet_queue` and keeps the list in `tfrrs_hs_meets`. `refresh` runs nightly (3 pages per site) before the TFRRS scrape. |
| HS linking | `scripts/link_tfrrs_hs.py` | Pass 2b of `link_tfrrs_rows.py`: a TFRRS identity at a state-site meet joins its athletic.net career by name + school + grade (FR–SR read as 9–12) instead of being minted a second person. The whole id moves or none of it does, and it is logged with rule `tfrrs_hs`. Nightly it judges ids with a row scraped in the last 14 days. |

The fetcher now sends `racecast-results/1.0 (+https://racecast.co/about)`. It used to send a Chrome string (owner, 2026-10-25: "never disguise the fetcher's identity").

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
3. **Landing table** `results_ext` (built 2026-10-25, above): one row per runner per race, with
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
