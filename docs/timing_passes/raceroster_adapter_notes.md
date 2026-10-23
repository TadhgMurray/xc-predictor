# Race Roster: HS/MS meet index and adapter notes (2026-10-23)

Companion files:
- `raceroster_meets.csv`: 306 rows, of which 301 are HS/MS school meets with results on Race Roster (2019-2026). 275 came from the search index; 26 are OnlineRaceResults (ORR) imports found by sampling ORR ids.
- `raceroster_timers.csv`: 12 timers that are not in `docs/timing_companies.csv` (by name).

All of the volume is cross country. Race Roster lists school track meets: the northern WI timer's indoor and outdoor invitationals, C3 Timing's Northern Kentucky track meets, KHSAA track regions, and MSJ indoor qualifiers. None of them had published results on Race Roster. Each one is an empty results shell (`subEvents: []`).

## How the index was built

1. **State sweep.** The search matches an event's state name, so `q=<full state name>` returns every event located in that state. I swept all 50 states and DC over 2019-01-01..2026-12-31, paging fully. CA (2,748 hits) and IL (3,181 hits) are over the 2,000-row paging cap, so I re-ran them one year at a time. The sweep gave about 30,000 events.
2. **Term searches.** I ran 105 more terms across the same window:
   - generic terms: cross country, xc, invitational, conference, district, regional, sectional, state meet, relays, track, high school, middle school, HS, MS, JV, varsity, freshman, meet, league, championships, indoor, classic, grade
   - association acronyms: NSAA, IHSA, IHSAA, WIAA, MSHSL, MSHSAA, KHSAA, OSSAA, LHSAA, UIL, CHSAA, GHSA, TSSAA, OHSAA, PIAA, VHSL, NCHSAA, IGHSAU, IAHSAA, KSHSAA, NMAA, TAPPS, MHSAA, OSAA, AIA, UHSAA, SDHSAA, NDHSAA, CIF, FHSAA, SCHSL, AHSAA, MSAA, NYSPHSAA, NJSIAA, CIAC, MIAA, DCSAA, WVSSAC, MPSSAA
   - school types: catholic, academy, prep, christian
   - youth labels: USATF, AAU, bantam

   These terms added about 3,500 events (33,509 distinct in total, 29,742 in the US), but none of them was a US school candidate the state sweep had missed. They were non-US events or fuzzy-match noise. That is the stop condition: the state sweep is saturated.
3. **Name and sub-event filter.** I scored events on school words in the name, on sub-event names (Varsity, JV, MS/HS, grades, team scores), and on road and multisport exclusions. This gave 587 candidates.
4. **Results probe.** For each candidate I made two requests: the redirect, then the event JSON. 305 candidates had at least one sub-event with `hasResults: true`. After removing college meets, elementary series, Nordic ski, open summer series and MTB, 279 search-indexed meets remain.
5. **Column check.** For each meet I fetched the sub-event detail of one structured individual race, which gives the column labels without the rows. For unstructured meets I sampled 18 meets, at most two per timer.
6. **ORR sampling.** I sampled ORR event ids: every 100th id from 100 to 25,700, and every 25th id from 22,300 to 25,775 (which covers 2019-2025). I also took 30 random eids in the 118,000-141,000 gap.

About 2,800 requests in total, at one request per 2.6 s per host. No 429 or 503 responses, and no bot challenge.

## Hosts, robots and terms

| Host | robots.txt | Notes |
|---|---|---|
| `search.raceroster.com` | 404 (none) | JSON search API used by raceroster.com's search box |
| `results.raceroster.com` | `User-agent: * Allow: /` (only Bytespider and ImagesiftBot are blocked) | CloudFront (`X-Cache`), no rate-limit headers, about 0.3 s latency |
| `raceroster.com` | blocks `/registration/`, `/signin`, `/events/*/register`, `/events/*/transfers/`, `/*?aff=*` for all bots; SEO bots also lose `/events/2015..2020/` | Event pages are allowed, but the adapter does not need them |
| `results-cdn.raceroster.com` | not fetched | Hosts the PDFs behind some UNSTRUCTURED sub-events |
| `www.onlineraceresults.com` | robots.txt 302s to `/contribute/` | Returned 403 "Access to this page is forbidden" to our identifying User-Agent; a default-UA curl got the 301. **Do not use this host.** Call the Race Roster redirect directly (below). |

**Terms.** I checked raceroster.com/legal: the ToS for event registrants, the ToS for organizers and the API License ToS.
- None of them mentions scraping, crawling, robots, spiders or bots.
- The registrant ToS has a general clause against reproducing or distributing "Site Content" without permission, which allows a "reasonable number of copies ... for your own informational purposes".
- The API License ToS covers the licensed developer API (developers.raceroster.com/docs/api/results/). It is a credentialed alternative if the owner ever wants one.

Under the owner's rule ("if it doesn't outright say no results scraping it is fine"), this source is **fine**. Note the copy clause in the registry entry.

## Endpoints

### 1. Search (the index)

`GET https://search.raceroster.com/search?q=<term>&l=<1..100>&p=<page, 0-based>&from=YYYY-MM-DD&to=YYYY-MM-DD`

- **Parameter validation is strict.** An unknown parameter (for example `country`) or an empty or one-character `q` returns `400 {"message":"Invalid request query input"}`. The front end also sends `t=` (tab: upcoming or past) and `v=true` (virtual only). Leave out `from`/`to` to search all years.
- **Paging cap: 2,000 rows.** `p*l` must stay below 2,000. For example, `p=20&l=100` returns `hits: 0`. Split large queries by year.
- **Response:** `{status, meta:{prevPage, nextPage, hits, stats:{past, upcoming}}, data:[...]}`
  - `prevPage` and `nextPage` are a full URL string or `false`.
  - each `data[]` item: `{eventId, name, url (raceroster.com/events/<yyyy>/<eventId>/<slug>), location:{city, province:{name, abbreviation}, country, countryISO, address}, date (ISO with offset), category (past|upcoming), registration:{starts, ends}, subEvents:[{name, distance, subEventId (uuid), races, eventType}], distances, logo, hasInPersonRaces, hasVirtualRaces}`
  - A results-only event has `subEvents: []` and registration dates of 1969.
- **Matching is fuzzy.** "NSAA" also matches "NSNA". "kansas" returns the Arkansas events too. Filter on `location.province.abbreviation` and `countryISO`.
- **Sweep order:** use the full state name, one year at a time. For incremental runs, use `from=<last run - 7 days>`.
- **Not indexed:** imported archives. These include the ORR import (associations 2462, 2096, 2120, 2089 on old events) and an association-1921 New England road archive at eids 118,000-139,000.

### 2. eventId to results code

`GET https://results.raceroster.com/v2/api/events/redirect/<eventId>` (do not follow redirects)

- The response is `302 Location: /v3/events/<uniqueCode>`. It comes back for every eventId, including events with no results.
- `/v2/api/events/<numeric id>` returns 404, so the redirect step is always needed.

### 3. Event (meet) JSON

`GET https://results.raceroster.com/v2/api/events/<uniqueCode>`

Response `data.event`:
- **Ids:** `resultEventId` (= `id`), `eid` (the search eventId), `uniqueCode`
- **Owner:** `associationId`, the Race Roster account that owns the results. It is the stable timer key, because `provider` is often null.
- **Meet:** `name`, `eventDate` (UTC), `timezone`, `locality`, `administrativeArea` (state), `countryCode`, `status`
- **Timer:** `provider:{id, label, email, logo, phone}` (nullable), `providerId`
- **Sub-events:** `subEvents[]`, each with:
  - `id` (= `resultSubEventId`), `resultEventId`, `name`, `sortOrder`
  - `resultsType`: `STRUCTURED` or `UNSTRUCTURED`
  - `resultCount`, `hasResults`, `isPublic`
  - `distanceMeters`, `externalResultsUrl`, `unstructuredResultId`, `resultsLastModifiedDate`
  - `columns` (always `[]` in this response)
- The response also contains organizer contact email and `settings.accessCode`. Do not store either.
- A meet has results when **any** `subEvents[].hasResults` is true. Many listed school events are empty shells (`subEvents: []`):
  - 85 of the 125 events owned by the northern WI account
  - 59 of 63 for C3 Timing
  - 16 of 35 for TimingSpot
  - all of Lonnie's HAL Team (CO)
- `resultCount > 0` with `hasResults: false` means the results are not published yet. The 2026 NSAA State XC was in that state on 2026-10-23.
- **Skip these sub-events:**
  - team-score sub-events: names containing "Team", often UNSTRUCTURED with `resultCount 0`
  - `--Text Format` duplicates of a structured race (Alan and Robin 2026)

### 4. Sub-event detail (column labels, or the whole text result)

`GET https://results.raceroster.com/v2/api/events/<uniqueCode>/sub-events/<subEventId>`

- Returns `data.columns[]`, each `{type: result|custom, col (snake_case: overall_place, team_name, gun_time, custom_26162013), label, isHidden, show, order}`. Use this to classify a race before pulling rows.
- Also returns `data.unstructuredData: {text, url}` and `data.filters`, `segments`, `translations`.

### 5. Structured rows

`GET https://results.raceroster.com/v2/api/result-events/<resultEventId>/sub-events/<subEventId>/results?start=<offset>&limit=<n>`

- **Limits:**
  - `limit` must be at least 50. Smaller values return `400 {"meta":{"error":{"msg":"... \"limit\" must be greater than or equal to 50","code":40001}}}`.
  - Large limits work. `limit=1000` returned all 461 rows of the largest race in one call.
  - Unknown parameters such as `language` return 400 "not allowed".
- **Response:** `{_id, data:[row], meta:{filteredResults, totalResults, flattenedColumns[], listingColumns[], filterColumns[]}}`
- **Row example:** `{id, overallPlace, bib, name, teamName, gunTime, overallPace, custom26162013, custom26162015, initials, advertising}`
- **Column names differ by endpoint.** In `meta.flattenedColumns` the `col` is camelCase (`custom26162013`). In the sub-event detail it is snake_case (`custom_26162013`). Map rows using `flattenedColumns[].col` to `.label`.
- **Map by label, not by col.** Start 2 Finish puts a "Team Place" label on `teamName` and keeps the school in a custom column labelled "School".
  - school: Team, School, Team Name, School Name
  - grade: Grade, Year, Class
  - name: Name, Full Name, or First Name + Last Name
  - time: Time, Gun Elapsed Time, Gun Time, Guntime, Chip Elapsed Time, Finish Time
  - place: Place, Race Place, Overall Place
- "Age" sometimes holds the grade. Threshold Timing's LHSAA races show "10" for a 16:04 boy.
- **Label frequency** across the 224 structured meets:

  | Label | Meets |
  |---|---|
  | Bib | 218 |
  | Team | 138 |
  | Race Place | 132 |
  | Gun Elapsed Time | 120 |
  | Full Name | 118 |
  | Gender | 104 |
  | **Grade** | **99** |
  | Name | 92 |
  | Place | 85 |
  | Age | 70 |
  | Time | 68 |
  | School | 54 |
  | Year | 25 |
  | First / Last Name | 14 each |
  | Team Name | 8 |

- To pull one race, make a single call with `start=0&limit=1000` and page only if `totalResults` is above 1,000.

### 6. Unstructured results: the text variant (NSAA and Hy-Tek timers) and PDFs

The same sub-event detail call (section 4) carries the whole result in `data.unstructuredData`.

- **`text`:** a Hy-Tek fixed-width report (team results first, then individual results). Seen at up to 23k characters, with no truncation.
  - NSAA State XC 2025 (ORR import, eid 164520, code `y6kj5a3d7nzpz5en`, sub-events 378459-378466 "Class A Boys" .. "Class D Girls").
  - Its individual block header is `Place Tea Bib Name Year Team Rank 1m Rank 2m Time Pace`, with one runner per line, giving grade and school.
  - Older ORR texts (Muscatine and Burlington 2026, NSAA team blocks) list runners under a team header with the grade in a column.
  - IW2R and ERC (Keokuk, Timm Lamb) individual blocks use `Place TmPl No. Name Cla School Time Pace`.
  - Parse it with the existing Hy-Tek text parser.
- **`url`:** a PDF on `results-cdn.raceroster.com/events/<uuid or code>/sub-events/<id>/<file>.pdf`, with `text` empty. Seen for J3 Timing's 2020-2022 IL meets, 3 Way Racing, and Fred D. Martin. Route these to the PDF parser.

### 7. OnlineRaceResults archive (not in search)

- **Event id:** `GET https://results.raceroster.com/redirect/third-party?source=onlineraceresults&entity=event&id=<orrEventId>` (do not follow redirects).
  - `301 Location: https://results.raceroster.com/v3/events/<uniqueCode>` when the id exists.
  - `302 Location: /v3/not-found` when it does not (about 10% of ids).
- **Race id:** `entity=race&id=<orrRaceId>` gives `301 .../v3/events/<code>/race/<subEventId>`.
- **Walkable:** yes. Ids are sequential and roughly chronological:
  - 100 = 2002, 5,000 = 2010, 15,000 = 2015, 22,300 = January 2019, 25,700 = September 2025.
  - The imported events have Race Roster eids of about 139,999-164,900, are owned by association 2462 (ORR), 2096 (End Result Company), 2120 (Alan and Robin) or 2089 (ERC), and keep the old ORR timer name as `provider.label`.
- **What the samples show:**
  - Whole range, every 100th id: 228 of 257 ids resolved. 14 were school XC meets (6%). States: OK 40, IN 26, CO 26, MN 19, IA 18, WI 14, OR 13, MO 12, TX 11, NE 9. Providers: "Online Race Results" 182, End Result Company 25, Alan and Robin 12, ERC "Adam's Crew" 8.
  - 2019-2025, every 25th id: 127 of 140 resolved. 24 were school meets (19%). IA was the most common, then IN, NE, CO, IL and OK.
- **Estimate:** about 600 HS/MS XC meets for 2019-2025 among the ~3,200 live ids from 22,300 to 25,800, and about 1,500 school meets across 2002-2025. That is roughly 85-100 a year, centred on IA, NE, IN, CO and OK.
- **Cost of a full walk:** 25,800 redirects plus about 23,000 event fetches, or about 35 hours at one request per 2.6 s. For 2019-2025 only it is about 7,000 requests (about 5 hours). It is a one-time job; the archive is frozen except for re-imports.

### Rate behaviour

- No `X-RateLimit-*` or `Retry-After` headers. Responses come through CloudFront.
- No 429, 503 or challenge pages in about 2,800 requests at one request per 2.6 s per host. One request timed out once.
- Keep the 2-3 s spacing per host and back off on 429 or 503.

## Adapter outline

1. **Index:**
   - weekly in season: state-name searches with `from=<last run - 7 days>`, filtered to US events
   - yearly full sweep: 50 states plus DC, split by year where `hits` is over 2,000
2. **Classify:** score the name (xc / cross country / invitational / conference / district / sectional / MS / HS / JV / association acronyms) and the search `subEvents` names (Varsity, JV, grades, Boys/Girls + MS/HS), and drop road and multisport words (5K, trot, triathlon, MTB, camp, clinic, Walk MS). Then make the redirect and event JSON calls and keep events with any `hasResults`. Store `associationId` and `providerId` to identify the timer.
3. **Per race:**
   - drop team-score and `--Text Format` duplicates
   - STRUCTURED: sub-event detail (labels), then a rows call with `limit=1000`
   - UNSTRUCTURED: one sub-event detail call, then the text parser or the PDF parser
4. **ORR backfill:** run the id walk once over 22,300-25,800 (2019-2025), then go further back if wanted.

## Findings for the registry

**Timers by HS/MS meets in this index:**
- J3 Timing (IL, assoc 100): 42
- unnamed northern WI / U.P. timer (assoc 387): 38
- Start 2 Finish St. Louis: 37 (+ Memphis 11, Charlotte 2; assoc 147)
- Granite State Race Services (NH/MA/CT/ME, assoc 108): 23
- Chuck Magera (SC): 17
- TimingSpot (Cincinnati, assoc 214): 17
- Negative Split Productions (TX): 15
- Alan and Robin (NE/IA): 13
- ERC Timing Solutions (IA/IL/NM): 12
- IW2R (IN, 2020 only): 12
- Threshold Timing (LA): 12
- ORR imports without a named timer: 18

**Coverage:**

| State | Meets |
|---|---|
| IL | 52 |
| MO | 38 |
| WI | 36 |
| IA | 20 |
| IN | 18 |
| OH | 18 |
| NH | 17 |
| SC | 17 |
| TX | 17 |
| LA | 12 |
| NE | 11 |
| KY | 9 |
| MI, NC, TN | 7 each |
| MA | 3 |
| CO, CT, NJ, NM | 2 each |
| AR, CA, ME, OK | 1 each |

| Year | Meets |
|---|---|
| 2019 | 1 |
| 2020 | 25 |
| 2021 | 42 |
| 2022 | 30 |
| 2023 | 40 |
| 2024 | 49 |
| 2025 | 44 |
| 2026 (season in progress) | 70 |

**What the rows carry:**
- grade + school: 159 meets
- school only: 75
- unchecked (unsampled text or PDF, or ORR imports): 46
- neither in the sampled race: 21

**Off limits:** nothing on Race Roster. `www.onlineraceresults.com` 403s identifying clients; it is not needed because the redirect lives on results.raceroster.com.

## What remains

- **ORR id walk:** about 600 more school meets for 2019-2025 (IA, NE, IN, CO, OK) that search cannot reach. Only 26 of them are in the CSV, from the 1-in-25 sample.
- **The rest of 2026:** results for late-October and November meets will appear on events that are already indexed. The NSAA State, WIAA D2 Rhinelander sectional, IHSA 3A Rock Island regional and two Alan-and-Robin IA meets are listed but not published yet.
- **Unnamed timers:** association 387 (northern WI and the U.P.), 569 (west Chicago suburbs), 339 (Las Cruces), 2694 (SE Iowa) and 731 (central Ohio).
- **Unsampled meets:** 46 meets whose grade and school columns are unchecked.
- **Track:** none on Race Roster so far. Recheck the northern WI timer's and C3 Timing's spring 2027 track shells.
