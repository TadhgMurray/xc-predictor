# US HS/MS/college timing companies: where results are published

Research date: 2026-10-23. This is a catalog only. No scrapers were written. Each site got at most a few
page fetches. Full per-company data is in `docs/timing_companies.csv`: 334 rows, made up of 201
researched companies or organizations plus 133 AthleticLIVE tenants that are listed by tenant slug and
URL only. Anything not checked directly is marked **unconfirmed**.

Sources used:
- Web searches, state by state.
- HY-TEK's list of authorized independent timing contractors: https://hytek.active.com/support-tf-itc.html (88 contractors).
- MileSplit Virginia's list of timing companies.
- The AthleticLIVE tenant map. Every AthleticLIVE page embeds a JS map named `imagesByOrigin` that lists every timer site on the platform (261 entries).
- One fetch of a homepage or results page per company, plus a sample meet page where needed.

## Key findings

1. **AthleticLIVE is the biggest platform by far.** It has 261 tenant sites, and roughly 230 of them look
   like US timers, schools or leagues. It is owned and run by Athletic.net: the bundle and assets come from
   `livestatic.athletic.net` and the API is at `api.athletic.live`. Of the 201 companies researched, 102
   publish there. This is the same company that just removed the compiled-results API, so it is **not an
   independent source**. Treat it as a business and terms-of-service question, not just a parser question.
2. **Hy-Tek Meet Manager output is the most common artifact format.** Hy-Tek PDFs are posted by timers,
   meet hosts and **state associations** (GHSA, NCHSAA, TSSAA, MHSAA-MS, NHIAA, WHSAA, NMAA, OSSAA via
   ohstrack.com, Iowa coaches association). Only a handful of timers publish Hy-Tek **HTML** on their own
   sites: YenTiming, TRXC, Eagle Eye, Leone and Ticker (historic).
3. **MeetPro HTML is the second Meet Manager format, and it is very consistent.** It is used by
   `results.tfmeetpro.com/<Timer>/<Meet>/` (hosted on S3, no index), CFPI, RT Results (CIF State XC),
   MAC-FAT (PIAA District 3), Mitchell Timing (about 740 links) and B&B Race KC (about 350 links).
   - Every page has the same layout: `Race #`, `Team Scoring Summary`, then `Individual Results: Athlete | Yr. | Team | Score | Time`.
   - Grade and team are always present.
   - Its live companion is **TrackScoreboard** (`<tenant>.trackscoreboard.com`).
4. **PTTiming (PrimeTime Timing, Wisconsin) runs its own platform and explicitly bans automated access.**
   - It times MSHSAA (MO) state XC, FHSAA state XC (2023), WIAA sectionals, NCAA meets, and the NYSPHSAA XC Championships through the Leone white label.
   - The terms (seen at results.leonetiming.com/terms.html) forbid bots, scrapers, scripts, pulling data from endpoints, and AI use.
   - Do not ingest it without written permission.
   - Note: "PTTiming" is **not** the Perfect Timing Group (GA, ptgrouponline.com). Perfect Timing Group publishes on AthleticLIVE and as Hy-Tek PDFs.
5. **MileSplit Live (`milesplit.live/meets/<id>`, timer index at `milesplit.live/timers/<id>`) is the
   second most common destination.** It is FloSports-owned and not independent. Several timers in VA,
   TN, KS, IN, TX and PA post mainly there.
6. **The best independent own-site sources found:**
   - YenTiming (NY): open directory listings of Hy-Tek HTML, with grade and school.
   - Black Squirrel (NE): NSAA district and state Hy-Tek PDFs.
   - Finished Results (CA/AZ): custom meet database, about 1,400 meets.
   - GSE Timing (MN): ASP results database with `meet_id`.
   - TRXC (MO).
   - CFPI (SC/TN).
   - SportTrax/MeetTrax (UT state XC): full results as JSON embedded in each page, with grade and school, and an exposed `can_access_public_api` flag.

## 1. Shared results platforms, ranked by how many timers they carry

| # | Platform | Owner | Timers seen | URL patterns | Meet index? | Format | Independent of athletic.net / MileSplit? |
|---|---|---|---|---|---|---|---|
| 1 | **AthleticLIVE** | Athletic.net | 261 tenant sites (about 230 US); 102 of the 201 researched companies | `https://<timer>.anet.live/meets/<id>`, CNAMEs such as `live.<timer>.com/meets/<id>` and `results.<timer>.com/meets/<id>`, and the main site `live.athletic.net/meets/<id>`. Meet IDs are global (examples: 45347, 58504, 79072, 711288). | Yes: `live.athletic.net/meet-list` and `/<tenant>/meet-list` on each tenant | Angular single-page app. Data comes from `api.athletic.live/api/...` plus Firebase and Elastic endpoints. The tenant list is embedded in every page (`imagesByOrigin`). | **No** (Athletic.net). robots.txt disallows `/meets/*/athletes`, `/live`, `/teams`, `/follow`. Terms not reviewed; check them. |
| 2 | **MileSplit Live / MileSplit timer pages** | FloSports | 18+ researched companies (Trojan, Final Lap, Eagle Eye, Heartland, Above & Beyond, Elite FL, NTX, Tidewater, MAC-FAT, Mitchell, Red Cap, Exact...) | `milesplit.live/meets/<id>`, `milesplit.live/timers/<id>`, `<st>.milesplit.com/timing/<id>/<slug>?year=` | Per-timer index at `milesplit.live/timers/<id>` | JS live app. Accepts Hy-Tek (multi-user), MeetPro, EasyMeet and FinishLynx feeds. | **No** |
| 3 | **MeetPro results host + TrackScoreboard** | MeetPro (tfmeetpro.com) / TrackScoreboard | MeetPro HTML: CFPI, RT Results, MAC-FAT, Mitchell, B&B Race KC. TrackScoreboard tenants: rt, finishedresults, lancer, gc, finishtiming. | `results.tfmeetpro.com/<Timer_Account>/<Meet_Name>/` (static S3) and `<tenant>.trackscoreboard.com/meets/<id>/events` (IDs up to about 113k) | MeetPro host: **no index** (S3 returns NoSuchKey); links have to be collected from timer sites. TrackScoreboard: tenant meet list at `/`. | MeetPro: static HTML with consistent structure, grade and team present. TrackScoreboard: Angular single-page app with `api.trackscoreboard.com` and Firebase RTDB. | **Yes** |
| 4 | **PTTiming** | PrimeTime Timing (WI) | PrimeTime itself, the Leone white label, and the flotrack redirect | `pttiming.com/event.php?mid=<n>&pt=results`, `pttiming.com/event/<n>?pt=results`, `live.pttiming.com/xc-ptt.html?mid=<n>`, `results.leonetiming.com/xc.html?mid=<n>` | Yes (`pttiming.com/results`) | JS bundle with a Firebase REST (`.json`) backend | Yes, but **the terms forbid automated access**. Requires permission. |
| 5 | **RunnerSpace / DyeStat / ResultsCentral** | RunnerSpace | Many state-championship pages; timer "walls" such as `scullion-s-timing-nj-timing.runnerspace.com` | `<state>-cross-country-state-championships.runnerspace.com`, `runnerspace.com/eprofile.php?event_id=<n>` | Per event | HTML news posts with attached results. Mostly historic (2012-2019). Current activity unconfirmed. | Yes |
| 6 | **TFRRS (HS state subdomains)** | TFRRS/DirectAthletics | Lancer (NHIAA), Racemaker (IHSAA), FHSAA, IHSAA sectionals | `indiana.tfrrs.org`, `florida.tfrrs.org`, `nh.tfrrs.org`, `tf.tfrrs.org`; `/results/xc/<id>/<name>` | Yes | HTML tables, college-style, with grade | Yes. The repo already has a `tfrrs/` module, so check whether HS subdomains are covered. |
| 7 | **RACE RESULT** | raceresult.com | Superior (MI), RealTiming (IL), Pacific Sport (HI), Bulldog, On The Mark, Pelican | `my.raceresult.com/<id>/...` | Per organizer | JSON behind the page | Yes |
| 8 | **RunSignup results** | RunSignup | Kinetic (IL), Endurance Sports (FL), Pelican (LA), CK | `runsignup.com/Race/Results/<id>` | Per race | HTML, with an API available | Yes, but mostly road races; little HS XC grade data |
| 9 | **ChronoTrack** | ChronoTrack | Bullseye, Leone, Motion, Big River | `sites.chronotrack.com/event/<id>/results?raceId=` | Per event | JS + JSON | Yes |
| 10 | **SportTrax (MeetTrax)** | SportTrax | UHSAA state XC 2024-25 | `meettrax.com/meets/<id>/results/by-event/<eventId>` (redirects to sporttrax.com) | Meet list | Laravel/Inertia. Full JSON in the `data-page` attribute, including grade, school and gender. Has a "public API" feature flag. | Yes |
| 11 | **RunnerCard** | RunnerCard | UT/West, El Paso TX | `runnercard.com/e/runner.Main?results=<year>`, `runnercard.com/ViewResults.html...`, uploaded `.txt`, `.htm`, `.docx` and `.pdf` files | Yes, by year from 1997 | Mixed | Yes |
| 12 | **Flash Results** | Flash Results Inc. | Flash Results only; Flash Results Texas uses AthleticLIVE | `flashresults.com/<YYYY>_Meets/<xc|Indoor|Outdoor>/<MM-DD_Name>/index.htm` | Homepage list | Static HTML with JS status files | Yes. Mostly college and elite meets. |
| 13 | trackmeet.io | trackmeet.io | Bullseye (mobile results partner), All-American (PDF hosting at static.trackmeetio.com) | `static.trackmeetio.com/meetFiles/<id>/<hash>.pdf` | unconfirmed | PDF and app | Yes |

## 2. Formats ranked by number of companies (parser priority)

These counts are over the 201 researched companies; one company can use several formats. Of these, 41 had
no confirmable publishing format: the site was unreachable, dead, or only historic.

| Rank | Format | Companies | Notes |
|---|---|---|---|
| 1 | AthleticLIVE | 102 | Not independent (see above). Another roughly 133 tenants are known by URL only. |
| 2 | **Hy-Tek Meet Manager PDF** (printed report) | 29 confirmed, plus most state-association postings | Fixed-width text inside the PDF. "Contractor License ... Hy-Tek's MEET MANAGER" header; columns Name, Year, School, Finals, Points. Posted by GHSA, NCHSAA, TSSAA, MHSAA-MS, NHIAA, WHSAA, NMAA, ohstrack.com (OSSAA), iatrackcoaches.org and others. |
| 3 | MileSplit / MileSplit Live | 18 | Not independent. |
| 4 | Custom own-site (bespoke HTML or a database) | 17 | Finished Results, GSE, Just In Time Racing, elitefeats (ASP), Y Timing, ActNow, FLT Results, ScoreYourRace, and others. Each needs its own adapter. |
| 5 | RACE RESULT | 7 | JSON. Mostly road races and chip-timed XC. |
| 6 | **MeetPro HTML** (+ TrackScoreboard live) | 5 MeetPro + 5 TrackScoreboard (overlapping) | Very uniform HTML; large volume (Mitchell about 740 meets, B&B about 350, MAC-FAT about 200). |
| 7 | **Hy-Tek HTML on the timer's own site** | 5 | Two variants: (a) real-time results frameset `index.htm` -> `evtindex.htm` -> `<YYMMDD>F001.htm` (Eagle Eye); (b) a report saved as HTML, either `<pre>` text or absolutely positioned divs (YenTiming, TRXC, Leone, Ticker). Same text layout as the PDF. |
| 8 | ChronoTrack | 5 | JSON. |
| 9 | RunSignup | 4 | |
| 10 | TFRRS | 4 | |
| 11 | PTTiming | 2 platforms (plus white label) | Terms ban automated access. |
| 12 | SportTrax JSON, RunnerCard | 1 each | |

**Takeaway:** a single **Hy-Tek report text parser** covers PDFs (after pdftotext), saved-HTML reports
and RTR event pages. It covers more independent results than anything else, because state associations
and coaches' sites re-host the PDFs. **MeetPro HTML** comes next: one parser, very regular structure.

## 3. State associations that post official state-meet results themselves

| State | Where | Format |
|---|---|---|
| AK | asaa.org `/wp-content/uploads/<year>-State-XCR-Team-Results.pdf` | PDF |
| CO | chsaanow.com/results/cross-country (2006 onward) | HTML |
| GA | ghsa.net `/2024-2025-ghsa-cross-country-results`, `/...-track-results` | Hy-Tek PDF (Perfect Timing Group) |
| HI | hhsaa.org/sports/cross_country/tournament/<year> | HTML |
| IL | ihsa.org records/archives (state series results) | HTML |
| IN | ihsaa.org tournament pages link to results; older Hy-Tek PDFs at ihsaa.org/sites/default/files/documents/ | links / PDF |
| KS | (KSHSAA uses Midwest Timing as official provider) | via Midwest Timing |
| KY | khsaa.org state results posts **and region Hy-Tek `.mdb` database files** (khsaa.org/2025-2026-cross-country-hytek-data/) | HTML + Hy-Tek MDB (readable with mdbtools) |
| LA | lhsaa.org `siteuploads/.../2022 LHSAA Cross Country State Meet Results.pdf` | PDF |
| MA | miaa.net (programs and rosters); results unconfirmed | PDF |
| MD | mpssaa.org/2025-cross-country-championships | unconfirmed |
| MI | mhsaa.com (links to Superior Timing / AthleticLIVE for LP finals) | links |
| MN | mshsl.org/2025-cross-country-state-tournament-results | HTML |
| MO | mshsaa.org/Activities/PostseasonResult.aspx?alg=<n>&id=<n> | HTML (team and individual) |
| MS | misshsaa.com cross-country-results posts | Hy-Tek (Pirate Timing) |
| NC | nchsaa.org/wp-content/uploads/<yyyy>/<mm>/<class>-Boys-Results.pdf | Hy-Tek PDF |
| ND | ndhsaa.com/tournaments/crosscountry-boys (links to Hero's Timing; 2025 PDFs) | PDF / links |
| NE | nsaahome.org (links to Black Squirrel; live on athletic.net) | links |
| NH | nhiaa.org/ckfinder/userfiles/files/*.pdf | Hy-Tek PDF (Lancer) |
| NJ | njsiaa.org history books; mctrack.org/NJSIAAMeets/SeasonIndex.htm (coaches association) | PDF / HTML |
| NM | nmact.org/file/2025_CC_Results.pdf, 2026_A3A_Day1_Results.pdf | Hy-Tek PDF |
| NV | niaa.com/sports/xc/archive | HTML / PDF |
| OH | ohsaa.org/sports/cc/pastresults (district, regional, state) | PDF / HTML |
| OK | ossaaillustrated.com (partners with MileSplit); state TF PDFs on ohstrack.com | Hy-Tek PDF |
| OR | osaa.org/championships?view=bxc (live results by Athletic Timing) | links |
| PA | piaa.org past champions | HTML |
| SC | schsl.org/archives/20734 (qualifier and championship hub) | PDF |
| SD | sdhsaa.com (results link to athletic.net / Dakota Timing) | links |
| TN | tssaasports.com/event/file.cfm?championshipid=<yyyymm..>&type=results | Hy-Tek PDF (AllTrax) |
| UT | uhsaa.org records book; state meet results on SportTrax / RunnerCard | PDF / platform |
| WI | wiaawi.org State Results Archive (sectionals from 2002) | PDF |
| WV | wvssac.org/<year>-cross-country-championship-central | links |
| WY | whsaa.org/track/1aboys.pdf etc. | Hy-Tek PDF |

## 4. Per-state coverage

"Own-site" means the timer publishes results somewhere other than athletic.net, AthleticLIVE or MileSplit.

| State | Companies found (see CSV) | Own-site / independent publishers |
|---|---|---|
| AL | Xpress Timing, C.F.P.I. (historic), High Performance Timing | none confirmed (Xpress uses AthleticLIVE) |
| AK | Skinny Raven (historic) | ASAA PDFs only |
| AZ | Finished Results (AIA state XC), Meetregister, NTS-Timing | **Finished Results** |
| AR | Y Timing, MacDonald Timing, ActNow | **Y Timing** (custom event pages), ActNow (custom) |
| CA | Finished Results, RT Results, Record Timing, Diablo, Red Cap, AdkinsTrak West, Mentzer, Royal Results, Speed Timing, EPI, About Timing, Extra Mile, Meetregister | **Finished Results**, **RT Results** (MeetPro, CIF State), **Diablo** (Hy-Tek PDF) |
| CO | Finish Line Timing, Colorado Race Timing, Rapid Results, Colorado Elite, Denver Athletics, Altitude Endurance | **Finish Line Timing** (fltresults.com) |
| CT | Northeast Sports Timing, Hartford Timing, Ellington Timing | none confirmed (mysportsresults.com is a CT results site, not checked) |
| DE | M&D, Ultimate Sports, Bullseye, Pioneer, Tumey, Motion | none confirmed |
| DC | M&D, Bullseye | none confirmed |
| FL | Half-Mile, Elite Timing, Endurance Sports, CK, Big Dawg, GunLap, Track Timing & Data Mgmt, MOGO, PrimeTime (state XC 2023) | Elite Timing (posts), Endurance (RunSignup); FHSAA state XC on florida.tfrrs.org |
| GA | Perfect Timing Group, Exact Timing, FastFeet, High Performance | **FastFeet** (PDF); GHSA re-hosts Perfect Timing Hy-Tek PDFs |
| HI | Pacific Sport Events & Timing | **PSE** (RaceResult) |
| ID | Snake River Timing (unconfirmed location) | **none found** |
| IL | RealTiming, Kinetic, Palatine Pack, TRXC, Absolute, Lakeshore, Michiana, Timing MD, Big River | **RealTiming** (RaceResult/PDF), Kinetic (RunSignup) |
| IN | Above and Beyond, Timing MD, Racemaker, T&H Timing, Photo Finish, Michiana, A2, Creative | Above and Beyond (PDF + MileSplit); IHSAA on indiana.tfrrs.org |
| IA | All-American, Wayzata, Sub4Sports, AccuRace, ADE, GSE, Lakeshore | **All-American** (Hy-Tek PDFs on iatrackcoaches.org), **AccuRace** (PDF) |
| KS | Midwest Timing, Heartland, B&B Race KC, Ticker, Eagle Eye, Power E, Sub4Sports, ActNow | **B&B** (MeetPro), **Heartland** (PDF), **Eagle Eye** (Hy-Tek HTML, to 2024), Ticker (historic) |
| KY | DC Timing, Timing King, Network Timing | KHSAA (Hy-Tek MDB + HTML) |
| LA | Crescent City, Pelican, AdkinsTrak, Big River, CFPI | **Crescent City** (about 570 PDFs) |
| ME | (no timer identified) | Sub5 hub |
| MD | M&D, Maryland Race Timing, Bullseye, On Track, Western MD, BCPS, Baltimore City, Mantronix | Bullseye (ChronoTrack) |
| MA | Mercury, First Time Out, MSTCA, Lancer, TRXC (historic) | Lancer (PDF) |
| MI | Superior, Michiana, A2, Lakeshore, Mac's Sports, Mitten, ADE | **Michiana** (own posts), **A2** (own pages), **Superior** (RaceResult) |
| MN | Wayzata, GSE, Hero's, Gopher State, Northstar, Mankato East, White River | **GSE** (ASP database), **Hero's** (PDF) |
| MS | Pirate Timing, Xpress | MHSAA posts Pirate's Hy-Tek results |
| MO | TRXC, Midwest, Big River, PrimeTime (state XC), Eagle Eye, ActNow, Xplosion, Championship Timing, 417 Running, MoFast, Ticker | **TRXC** (Hy-Tek HTML), **Eagle Eye**; MSHSAA posts state results |
| MT | Beartooth Track, Grizzly Peak (both unconfirmed) | **none found** |
| NE | Black Squirrel, Sub4Sports, Run Nebraska | **Black Squirrel** (Hy-Tek PDF) |
| NV | NTS-Timing, Mentzer, Finished Results, Meetregister | none confirmed (NIAA archive) |
| NH | Lancer | **Lancer** (PDF); NHIAA |
| NJ | Fraulo, Scullion's, JBAC, Pioneer, Tumey, M&D, Wil-Time, elitefeats | mctrack.org (NJSIAA results, coaches association) |
| NM | Track Timing & Data Mgmt, CCR (historic) | NMAA (nmact.org Hy-Tek PDFs) |
| NY | YenTiming, Leone, elitefeats, Just In Time Racing, ScoreYourRace, FMTiming, CFPI, Armory, Batavia, 802, Wil-Time | **YenTiming** (Hy-Tek HTML, open directories), **Just In Time Racing**, **elitefeats**, **ScoreYourRace**, Leone (PTTiming; terms ban) |
| NC | Foothills, Rhodes, Big Kahuna, Pierre, Charlotte Flights, Scullion's, Mitchell, Network, Mantronix, Flash Results | **Big Kahuna** (PDF), **Mitchell** (MeetPro); NCHSAA PDFs |
| ND | Hero's, Wayzata, Dakota, GSE | **Hero's** (PDF) |
| OH | Timing Crew, Bulldog, On The Mark, Finish Timing, Lightning, Creative, GHG, SEO, Timing First, A2, Lakeshore | **On The Mark** (PDF), **Finish Timing** (TrackScoreboard); OHSAA posts |
| OK | Midwest, NTX, Enduro, Red Dirt | ohstrack.com (OSSAA state TF PDFs) |
| OR | Athletic Timing, PNTFO, USATF Oregon | **none found** (all AthleticLIVE) |
| PA | MAC-FAT, Motion, Tumey, Wil-Time, On Track, Bullseye, M&D, Pioneer, Bucknell, Pymatuning Valley | **MAC-FAT** (MeetPro), Motion (own pages) |
| RI | Wilbur Race Systems (historic) | Ocean State Running hub (ocean.staterunning.net/results/) |
| SC | Mitchell, CFPI, Split Second, Coastal Timing, Charlotte Flights, Network | **Mitchell** (MeetPro), **CFPI** (MeetPro HTML) |
| SD | Dakota Timing, Wayzata, GSE | **none found** (Dakota uses AthleticLIVE) |
| TN | AllTrax, Trojan, Slipstream, Knoxville Youth Athletics, Xpress, CFPI, Network | **CFPI** (TN meets), Slipstream (own pages); TSSAA re-hosts AllTrax Hy-Tek PDFs |
| TX | Flash Results Texas, NTX, Pro-Fit, satrackresults, Hill Country, ATX, Track Timing & Data Mgmt, Enduro, AllTrax, Cowtown (unconfirmed), Live Running Results, RunnerCard (El Paso) | Flash Results (UIL region); otherwise mostly AthleticLIVE/MileSplit. **Weak for the state's size.** |
| UT | SportTrax/MeetTrax, RunnerCard, NTS | **SportTrax** (JSON), **RunnerCard** |
| VT | 802 Timing, TrackLife | 802 Timing (older own HTML archive; current results on AthleticLIVE) |
| VA | Atlantic Coast, Blue Ridge, Final Lap, Snap, Mantronix, Xcelerating, Raintree, Glen Allen, Dan Ward, PMV, Dinwiddie, WEW, Tidewater, Trojan, Network, Runner's High (historic), New River | none confirmed (mostly AthleticLIVE and MileSplit Live) |
| WA | GC Racing, Metro League, Gold Medal, PNTFO | **GC Racing** (TrackScoreboard) |
| WV | KVT&FOA, Mountain Timing, New River, BoComServ (dead) | Mountain Timing (some PDFs) |
| WI | PrimeTime, Performance Timing, OnYourMarks, Badger State, 3B/WiscoTrack, K2, ADE, AccuRace, Absolute, Tortoise & Hare, UW-Whitewater, TrackSide | **Performance Timing** (PDF; portal behind Cloudflare), **AccuRace** (PDF); WIAA archive; PrimeTime (terms ban) |
| WY | Finish Line Timing + Timberline Events | **FLT Results**; WHSAA Hy-Tek PDFs |

## 5. States with no independent source found

These are states where every timer found publishes only to AthleticLIVE, athletic.net or MileSplit, or
where no timer was found at all:

- **No timer identified, or location unconfirmed:** ID, MT, ME (hub only), AK (association PDFs only).
- **AthleticLIVE / athletic.net / MileSplit only:** SD, OR, NV, VA, AL, CT, DE, DC.
- **Thin relative to size:** TX, FL, GA, OH, PA, IL. Each has at least one own-site timer or association
  posting, but most volume goes through AthleticLIVE or MileSplit.

These gaps partly reflect search reach, not absence. There are 133 AthleticLIVE tenants whose names and
states were not researched; resolving them (from each tenant's `/about` page) would fill many state cells.
Even so, all of them would still sit behind Athletic.net.

## 6. Notes on terms and access (not exhaustive)

- **PTTiming platform:** the terms explicitly forbid automated access, endpoint extraction and AI use.
  This covers results.leonetiming.com, live.pttiming.com and pttiming.com. **Do not scrape.**
- **AthleticLIVE:** robots.txt disallows `/meets/*/athletes`, `/live`, `/teams` and `/follow`. It is
  Athletic.net infrastructure. Terms not reviewed.
- **Red Cap Timing** has a `websiteuse.html` terms page (not read). **Flash Results** robots.txt blocks a
  few named bots only.
- **Performance Timing**'s results portal is behind a Cloudflare JS challenge. **Wayzata Results** and
  **Big River** returned 403 to a plain fetch.
- **Unreachable or dead sites** (502 or connection reset from our proxy; may be proxy-related): Atlantic
  Coast, Enduro, EPI, GunLap, Creative, Fraulo, M&D main site, Meetregister, Photo Finish, Power E, Speed
  Timing main site, Steeple, Timing MD main site, TrackLife, Track Timing & Data Mgmt, Tumey, XMG,
  Xplosion.
  - Confirmed dead: alltraxtiming.com (parked) and bcstrack.com (repurposed).

## 7. What to build first

1. **Hy-Tek report parser**, text-based. One parser fed from three inputs:
   - PDF via `pdftotext -layout`.
   - Saved-HTML reports (strip tags; handle both `<pre>` and absolutely positioned div variants).
   - Hy-Tek real-time result event pages (`<YYMMDD>F###.htm`).

   This unlocks:
   - State-association postings in GA, NC, TN, MS, NH, NM, WY and OK (ohstrack), plus iatrackcoaches.org.
   - Black Squirrel, Crescent City, Hero's, Diablo, Big Kahuna, AccuRace, Heartland, On The Mark.
   - YenTiming, TRXC, Eagle Eye.

   Header line and column layout are consistent ("Name Year School Finals Points"; relays and field
   events differ).
2. **MeetPro HTML parser.** It handles `results.tfmeetpro.com/<Timer>/<Meet>/` plus the same format on
   CFPI and rtspt.com (CIF State XC). The structure is regular and includes Yr and Team. Meet URLs have
   to be collected from timer pages (Mitchell, B&B, MAC-FAT) because the S3 host has no index.
3. **Source adapters for the best own-site timers.** Each lists meets cheaply:
   - YenTiming open directories.
   - Black Squirrel PDF lists.
   - Finished Results `/meets/<id>`.
   - GSE `cc_rslts.asp`.
   - TRXC yearly pages.
   - SportTrax: ask about the public API first; the embedded JSON is already structured.
4. **Hy-Tek `.mdb` reader** (mdbtools) for KHSAA region files. A small, cheap win.
5. **State-association pages** for official state meets, as a backstop in every state.
6. **Decide on AthleticLIVE deliberately.** It has the largest coverage (about half of all timers), but it
   is Athletic.net's product. Options: ask Athletic.net for permission or a license, or contact timers
   directly. Most timers keep the original Hy-Tek/MeetPro/Lynx files and might share exports (`.hy3`,
   semicolon, or Lynx `.lif`); none of the sites checked publish those exports publicly. Do not build
   against PTTiming without permission.

## Appendix: how the AthleticLIVE tenant list was obtained

Every AthleticLIVE page (for example https://results.wayzatatiming.com/meets/58504) includes an inline
script with `imagesByOrigin = { "<origin>": "<logo url with /assets/sites/<slug>/>" ... }`. A single page
fetch yields all 261 origin-to-slug pairs. They are in the CSV as "AthleticLIVE tenant: <slug>" rows for
any tenant not already researched by name. Non-US tenants (Canada, NZ, Australia, UK, Cayman, Caribbean)
were excluded from the CSV.

---

## Second pass (2026-10-23): West + Midwest, South + Northeast

`docs/timing_companies.csv` now has **1,054 rows**:
- 334 from the first pass
- 720 new timers, mostly from MileSplit's per-state timer directories (`<st>.milesplit.com/timing`)
- 239 field corrections applied, about 94 of them naming AthleticLIVE tenants
- 15 conflicts between the two passes, left in place and marked "CONFLICT between research passes"

The raw passes are in `docs/timing_passes/`. Most new rows are directory-only, with no website found and the platform unconfirmed. About 20 publish results on their own sites.

### Independent sources found in the second pass, by meet volume

| Source | Area | How it publishes | Volume / coverage |
|---|---|---|---|
| Wingfoot Finish | AZ | own ColdFusion database `result_details.cfm?ResultsID=`, PDFs under `/results/<YYYY>/` | ~1,400 results, 2008–2026 |
| Extra Mile Timing | CA LA / Ventura / Orange | static HTML `meetresults.net/extramile/meets/<MMDDYYYY><name>.htm`, yearly indexes since 2016 | ~170–180 meets/yr; LA City Section finals |
| Westside Timing | CA LA Westside | MeetPro (`results.tfmeetpro.com/Santa_Monica_Times/`) | ~170 meets |
| MyChipTime (RunFAR) | TX | own database `searchevent.php?id=`, JS-loaded | UIL district and region XC |
| Huber Timing | OR | own HTML `hubertiming.com/results/<YYYY><Event>` | ~670 pages, mostly road; Central Oregon district XC. First OR independent source |
| Apple Raceberry JaM | MN / WI | plain-text HTML | ~360 pages |
| The Runner's High | PA | static HTML and a database | PIAA District 9 XC |
| Platt Systems Timing | CT | own HTML (`pslive.php?eventid=`) | CT XC/TF. Fills the CT gap |
| CAG's Timing | VA | MeetPro on its own site | ~10 meets |
| Hawkeye (WI), River City Races (CO), Kandu (CO), RacePenguin (OH) | | PDFs / HTML | smaller |

- **MeetPro now covers six timers:** Mitchell, B&B, MAC-FAT, CFPI, Westside and CAG's. TF Meet Pro, the company behind MeetPro (Goddard, KS), is worth contacting about an index or feed for `results.tfmeetpro.com`.

### California: who times each CIF section's XC finals

| Section | Timer | Publishes on |
|---|---|---|
| Southern | Finished Results | own site |
| San Diego | Finished Results | own site |
| LA City | Extra Mile Timing | own site |
| Central Coast (2025) | Record Timing | AthleticLIVE |
| North Coast (2024–25) | Diablo Timing | own-site PDFs |
| Sac-Joaquin (2025) | Red Cap Timing | MileSplit |
| Oakland | Rogue Timing (track) | MileSplit Live |
| San Francisco | not confirmed | — |
| Central | not found (Buchanan Timing and CalTiming time track) | — |
| Northern | not found | — |

### Gaps that remain

| State | What the second pass found |
|---|---|
| ID | Only Snake River Timing (track) |
| MT | AthleticLIVE only |
| SD | Nothing independent |
| NV | Mentzer times the NIAA state meet; platform unconfirmed |
| OH | Mostly MileSplit and RunSignup |
| IL | The IHSA state XC timer was not identified |
| DE | DIAA PDFs, which returned 403 to a fetch |
| RI | MileSplit Live |

### Conflicts to settle

- **windsortiming:** one pass says Windsor Running (DC/MD/VA, ~190 meets); the other says Windsor, Ontario (Canada).
- **kjtiming:** the domain is now a spam site.
- **Michigan LP XC Finals 2025:** credited to Superior Timing in the first pass, to Michiana Timing by MileSplit.
- **nestiming:** duplicates the Northeast Sports Timing row.

### Access notes

- MileSplit pages carry a bot-detection script that names claudebot. They were read only through search snippets.
- athletic.net returns 403 to plain fetches, including its terms pages.
- No timer site checked had terms forbidding bots, except PTTiming (first pass).

---

## Third pass and AthleticLIVE (2026-10-23)

- **MeetPro is not independent.** `results.tfmeetpro.com` is MeetPro's results host. MeetPro belongs to DirectAthletics, which FloSports (MileSplit) bought in June 2023; DirectAthletics also runs TFRRS, which we already scrape.
  - The platform pass found about 84 US MeetPro accounts and 46 new rows.
  - It also added MeetPro to about 35 existing timers (Elite Timing FL, TimingInc, Rapid Results — which timed CHSAA 5A state XC 2025 — Snake River ID, Athletic Timing OR, and others).
  - `sources/registry.py` keeps MeetPro enabled, since its parser exists like TFRRS's, but does not count it as independent.
  - With that, the list has 1,100 timers: 61 independent, 86 on MeetPro.
- **TimerHub** (Red Cap Timing's live results) has terms that forbid collecting results without permission. It is off.
- **AthleticLIVE, 2025-26.** `docs/athleticlive/meets_2025_26.csv` comes from AthleticLIVE's public meet list, one request per week (53 in total).
  - 19,262 meets: 3,428 cross country, 13,795 outdoor track and 2,039 indoor.
  - 234 timers; 99% of meets link to an athletic.net meet.
  - That is roughly 45% of athletic.net's 28,786 spring track meets.
  - `scripts/diag_athleticlive_coverage.py` measures the share of athletic.net's meets and results it covers, by meet size and by state.
- **Own-site check of the MileSplit-directory timers (2026-10-23).** 348 checked: every timing company in TX, FL, GA, PA, NC, NY, NJ, VA, TN and SC, plus 18 others.
  - About 75% put school meets only on MileSplit. 253 have no own site found; about 56 run road-race-only sites; 13 link out to MileSplit.
  - 15 post XC/TF on or from their own site:
    - Hy-Tek HTML: Pirate Timing NC, Triangle Timing MA, trXCky KY.
    - PDF archives: DVGTCA PA, BAYTAF FL, Run Texas TX.
    - Indexes onto MeetPro/DirectAthletics: FT Timing TX (~150 DFW meets), Pure Gold FL, Bv Timing FL, MP Timing NC.
    - Others.
  - Corrections are in `docs/timing_passes/timing_corrections_ownsite.csv` and have been applied. The list now counts 87 independent sources.
- **Self-hosted results (2026-10-23):** `docs/timing_passes/self_hosted.csv`, 66 rows: schools, leagues, colleges, coaches associations, venues and hubs.
  - **Current and high-volume:**
    - Ocean Breeze (NYC indoor, ~70 meets a season, Hy-Tek PDFs)
    - LSU HS meets (Hy-Tek PDFs, dated file names)
    - Concordia NE indoor series
    - Wayne-Finger Lakes League (NY dual meets)
    - Spotlight On Berks (PA dual meets)
    - PSAL (NYC; database, school but no grade)
    - Baum's Page (OH database)
    - Tully Runners (NY; copies Leone/PTTiming output, so ask first)
  - **Historical:** MichTrack (scans), DyeStat archive, KTCCCA (2000–10), NCPrepTrack.
  - **Restricted:** Penn Relays results only with its permission.
  - The existing Hy-Tek parser read Ocean Breeze, LSU, Concordia and RITCA samples. Event lines with the gender last (youth pages) are now read too.
  - The pass hit the session's web-search limit before Brooks PR, Glenbard, the Nike regionals and the Kansas/Texas Relays.
- **Low-AthleticLIVE states (2026-10-23):** `docs/timing_passes/gap_states.csv` checked 226 big 2025-26 meets in GA, FL, UT, TX, NJ, WA, ID, NC and SC. ID, NC and SC are thin because the session's web-search limit ran out.
  - **FL:** `florida.tfrrs.org` carries 73% of the big meets. FHSAA uses TFRRS for official stats: all district, region and state track meets via `state_series.html?year=2026`, XC regions and state, and the big invitationals, with grade. TFRRS is DirectAthletics/FloSports, which we already scrape for college, but our tfrrs module has no HS state subdomain support.
  - **UT:** SportTrax/MeetTrax has 100%: state, divisional, region and invitationals, with full JSON (grade and team) in the page. Run It Green Timing is new.
  - **NJ:** `mctrack.org`, a coaches' site, has every NJSIAA sectional, group and Meet of Champions result, 1996–2026, as HTML.
  - **WA:** WIAA posts state meets as PDFs (GC Racing XC; Hy-Tek track under the PNTFO license).
  - **GA:** MileSplit has 96%. Only `ghsa.net` state Hy-Tek PDFs are independent.
  - **TX:** AthleticLIVE 66%. MyChipTime (RunFAR) carries the UIL regions and state, but duplicates AthleticLIVE. `uiltexas.org` posts state XC as HTML.
  - **ID:** AthleticLIVE has all the big meets.
  - **NC:** AthleticLIVE 59%, MeetPro (Mitchell) 29%. `nchsaa.org` posts state Hy-Tek PDFs.
  - **SC:** MeetPro (Mitchell) 54%. CFPI's own site has region pages.
  - Corrections (Half-Mile = TrackScoreboard and FHSAA state track; AllTrax = UIL state track; GC Racing = WIAA state XC; Perfect Timing Group = AthleticLIVE `perfecttiming`; Fulton Accurate Timing also NJ) are listed in the pass report and not yet applied.
- **State associations and results hubs (2026-10-23):** `docs/timing_passes/results_hubs.csv` has 109 rows: 56 associations, 15 sections, 5 leagues, 8 coaches associations, 19 hubs and 6 stats sites. It is thin where the web-search limit ran out.
  - **Largest by volume:**
    - TFRRS high school state sites: `fl.tfrrs.org` (the full FHSAA series since 2019, Broward weeklies) and `indiana.tfrrs.org` (100+ meets a year).
    - Tully Runners (NY, ~200 XC meets a year; reposts Leone/PTTiming output, so ask first).
    - ohstrack.com (OK, ~285 Hy-Tek PDFs a year).
    - mctrack.org (NJ).
    - montanatrack.com (~130 track meets a year).
    - WIAA Wisconsin (state and sectional PDFs since 2000).
    - RunMichigan.
    - KHSAA (XC region Hy-Tek HTML).
    - Baum's Page (OH).
    - Sub5 (ME).
    - Ocean State Running (RI).
    - IHSA (state finals since 1996).
  - **Do not collect without permission:**
    - Terms ban scraping or commercial use: CIAC (CT), MPA (ME), WyoPreps.
    - robots.txt bars all crawlers: PrepCalTrack, AHSAA, VHSL.
    - robots.txt bars ClaudeBot and AI crawlers by name: chsaanow (CO), IHSAA (IN).
    - Personal or non-commercial use only: LHSAA, MHSAA (MI), SIDEARM-hosted sites (NYSPHSAA, NY sections II/III/V, PIAA D3, WPIAL).
    - robots.txt disallows the results folder: PIAA, NYSPHSAA, FHSAA, KHSAA, UIL.
    - Bot challenges or 403s: the CIF PrestoSports sites, NIAA, DIAA, MHSAA (MS), IHSAA (IA), NMAA, OSAA, SDHSAA, VPA.
  - **FloSports' terms for TFRRS** need reading, since the project already scrapes TFRRS.
- **Invitationals, venues and colleges (2026-10-23):** `docs/timing_passes/events_venues.csv` has 44 rows: national meets, big venues and colleges that host HS meets. Few post HS results on their own sites.
  - **National meets** (NXR, Nike and New Balance Nationals, Brooks PR, the Kansas/Texas/Drake/Mt. SAC Relays) are indexed on WatchAthletics and link out to the timer. Most are on AthleticLIVE. WatchAthletics answers bots with a Cloudflare 403, so it is not fetched.
  - **Venues** (Reggie Lewis/MSTCA, Bennett in Toms River, Virginia Beach, PG Complex, CrossPlex) send results to MileSplit or athletic.net.
  - **Colleges' HS meets land on the main TFRRS site** (Kansas Relays HS, YSU WATTS, MSSU HS Open, SPIRE Scholastic). The TFRRS scraper already reaches them; filtering meet names on "High School|HS|Scholastic" would size them.
  - **The one self-hosted find:** Liberty University's HS indoor invitationals, as Hy-Tek PDFs on libertyflames.com (2-3 a year). It is a SIDEARM site, which allows personal or non-commercial use only, so ask first.
  - **Mt. SAC XC:** runmtsac.com now links to Finished Results, TrackScoreboard and RunnerSpace. This replaces the dead URLs in self_hosted.csv.
  - **To check:** gap_states.csv puts the 2025 NXR Northwest at Lake Wilderness; RunBlogRun says Spokane.
- **ID, NC and SC finished, plus results platforms (2026-10-23):** `docs/timing_passes/platforms_more.csv` has 124 rows: 107 state rows (ID 33, NC 44, SC 30) and 17 platforms. 73 of the state rows come from the AthleticLIVE meet list rather than new web checks.
  - **Idaho:** 76 of its 90 AthleticLIVE meets name no timer; host schools time their own meets. There is still no independent Idaho source. State XC 2025 was at Lewis-Clark State (AthleticLIVE meet 58742).
  - **North Carolina:** Foothills Timing timed NCHSAA state XC 2025 (Hy-Tek PDFs on nchsaa.org, with grade and school). Rhodes Race Timing timed the 2026 state track and indoor meets. Regional timers include TimingInc, 910 Athletics and Foothills.
  - **South Carolina:** SCHSL state XC and track appear on MileSplit only, timer unconfirmed. SCISA state XC was timed by Perfect Timing Group. Strictly Running posts to scrunners.com, a possible SC hub that the research proxy could not reach.
  - **Platforms, by estimated HS volume:**
    - TrackScoreboard: 500-1,000 meets a year. It is in the MeetPro/FloSports family; ask first.
    - RunSignup: 300-600 XC meets a year, usually with no grade.
    - RACE RESULT: 200-400. It carries Michigan state XC finals, NY Section 2 and UIL districts. robots.txt bars its JSON endpoints, so ask the timers for event API keys.
    - Arrow Live Results (Agee): 200-500, heavy in Indiana including IHSAA sectionals. No robots.txt or terms; worth asking for a feed.
    - Webscorer: 100-300.
    - RunnerCard (UT): 100-200.
    - Race Roster: 50-200. One JSON call returns a whole meet as Hy-Tek text, including Nebraska state XC 2012-2025. robots.txt allows everything.
    - OnlineRaceResults: 50-150. Every plain fetch answers 403.
  - **Do not fetch:** ChronoTrack (robots.txt names ClaudeBot), ItsYourRace (robots.txt bars results), TimerHub (terms).
- **League and conference archives (2026-10-23):** `docs/timing_passes/leagues.csv` has 23 rows. It is incomplete: the search limit ran out before GA, FL, VA and NC were searched.
  - **Ohio:** most rows are SIDtools sites, which share one layout (`ccMeet.aspx?sat=9|10&year=`, plus season-best pages with grade). Each has one championship per league per year, going back to 2004-2013. Their robots.txt blocks AI crawlers by name, so treat them as reference only and ask first.
  - **The few with depth:**
    - Carlisle Area SD (PA): about 150 Mid-Penn meet pages, 2008-2026, with no grade.
    - Suburban One (PA): prose recaps.
    - PIAA District II.
    - Southern Connecticut Conference: Hy-Tek from Paynes Corner, 2007-2026.
  - **Many league domains are gone:** they redirect to Arbiter or are parked. The CSV lists them so nobody re-checks them.
  - **Blocked:** the Eastern Connecticut Conference's robots.txt disallows its results pages.
- **More leagues and coach-run hubs (2026-10-23):** `docs/timing_passes/leagues_more.csv` has 26 rows, about 12 of them collectable. It was run under the owner's rule.
  - **Best finds:**
    - **Carroll County Running (MD):** Hy-Tek text pages with grade and school; ~140 meet pages a year, seasons back to 2000, plus a MD state-meet archive and an On Track Timing archive of 470+ meets. No robots.txt and no terms.
    - **MN Prep Track:** conference track PDFs 2010-2025, state XC 1943-2025.
    - **Fox River Classic Conference (WI):** XC PDFs 2007-2026, on rSchoolToday RST7, whose other league sites may have the same archive pages.
  - **Smaller:** Texas 1A Fan (district qualifiers), Cross Country Scoreboard (GA private schools, 2012-2024), NCSAA nationals (times written 15:08:30 for 15:08.30), Lexington MA (2012-2017).
  - **Off limits:** ScoreATL (bot challenge), Section IV NY (robots.txt disallows its /api/ for every crawler).
  - **To settle:** both midwesttiming.net (IN) and Midwest Timing & Results (KS/MO/OK) point at mwt.live.
- **More timers in the thin states (2026-10-23):** `docs/timing_passes/timers_more.csv` has 27 rows, merged into `docs/timing_companies.csv`: 15 new companies, and 12 known ones updated with an own-site archive (the new platform text first, the old kept after "|| before 2026-10-23:").
  - **Arrow Live Results** (arrowliveresults.com, Agee's platform; not athletic.net or FloSports) names the timer on each race. 19 timers use it. It is its own registry kind, `arrow`: one adapter covers them all.
    - 12:1 Running (OK): ~200 school races including OSSAA regionals.
    - athletic Scoring providers (FL): ~170.
    - Above and Beyond, RCC Timing and Terkhorn (IN), with IHSAA sectionals.
    - MOGO (FL), Hardy (GA, GHSA regions), RTS (CA).
    - Rows come from `/Results/pvResults?pEventId=<n>`; the per-state index is `/Home/pvRecentEvents?pStateId=<n>&pCountryId=1&showAll=true`. No robots.txt or terms.
  - **Own-site archives:**
    - T&H Timing (IN): IHSAA sectional PDFs back to 2008, with grade.
    - Live Running Results: Track Timing and Data's Hy-Tek site; NMAA state meets.
    - Maryland Race Timing: .TXT files 1991-2025.
    - CCR Timing, Snake River (Idaho's only source off athletic.net), APTiming (JSON API).
  - **Registry fixes:**
    - "own" alone now counts as an own site: Wingfoot Finish, Michiana, Motion, A2 and others are independent now.
    - A timer's own copy of MeetPro pages is independent.
    - "off limits" in a row's notes blocks it; Mac's Sports Timing is blocked for its bot challenge.
    - Independent sources went from 87 to 142. The states with none are now AK, DC, HI and MT.
- **Arrow Live Results, every state (2026-10-23):** `docs/timing_passes/arrow_timers.csv` (15 timers, merged into the registry: 10 new rows, 5 known timers now marked as on Arrow) and `docs/timing_passes/arrow_adapter_notes.md`, which describes how Arrow is built for the adapter.
  - **Volume:** 33 timers post school XC to Arrow. The biggest by HS races in the index:
    - 12:1 Running (OK): 165
    - athletic Scoring providers (FL): 135
    - Above and Beyond (IN): 75
    - MOGO (FL): 69
    - RCC (IN): 55
    - Advantage Timing (MN): 35
    - Run Roanoke (VA): 27
    - XM Timing (WI): 24
  - **Terms:** none. robots.txt is a 404, there is no terms page, and "The results are free to the general public."
  - **For the adapter:**
    - Every page is HTML. One race is one event; meets have to be rebuilt from timer, date, venue and race names.
    - Results come from `/Results/pvResults?pEventId=<n>&pDivision=<name>`, every runner in one response. School is the Team column. There is never a grade.
    - The state index is complete only from about 2026-09-19. About 14,000 races since September 2023 can be reached by sequential event id, a ~16 h walk at one request every 4 s.
    - The state field is unreliable (",AR" is the form's default); take the state from the timer or the venue.
- **Hubs, league platforms and results platforms (2026-10-23):** `docs/timing_passes/hubs_more.csv` has 49 rows. Most of the new volume is on Race Roster.
  - **Race Roster:**
    - **Listing:** `search.raceroster.com/search?q=<term>&l=100&p=<n>` gives event ids. `results.raceroster.com/v2/api/events/redirect/<id>` turns an id into a results code, and `/v2/api/events/<code>` gives the sub-events and the timer.
    - **Size:** about 550 US events for 2023-2026, roughly 130-200 of them HS/MS meets (WI 62, MO 41, IL 35, TX 17, KY 15).
    - **Rows:** structured, from `/v2/api/result-events/<id>/sub-events/<id>/results?start=0&limit=50` (limit at least 50), with a Grade column when the timer adds one.
    - **Terms:** none on the search host; the results host allows all.
  - **OnlineRaceResults now redirects into Race Roster** (`view_race.php?race_id=<n>` 301s to `results.raceroster.com/redirect/third-party?...`). Its ~25,700-event archive (NE, IA, OK, CO, IN) is reachable that way, so the old "every fetch 403" note is out of date.
  - **Timers on Race Roster:**
    - ERC Timing Solutions (SE Iowa and Quad Cities, including an IHSA regional)
    - Start 2 Finish St. Louis and Memphis
    - northern Wisconsin conferences
    - Alan and Robin (NE)
    - Negative Split (TX)
    - Threshold (LA)
    - C3 (KY), IW2R (IN)
  - **Other platforms:**
    - **RunnerCard:** `runner.Main?results=<YYYY>&class=11` lists 115 HS meets for 2025 (UT 63, El Paso 46).
    - **RunSignup:** about 150-250 HS meets per fall, including PIAA District 1 XC. Its API contract (2026-09-03) bans bulk extraction and competing products, so only its HTML result pages are usable.
    - **rSchoolToday RST7:** `<domain>/wp-json/wp/v2/media?per_page=100&search=<term>` lists every uploaded PDF on a league site; Fox River Classic has 310 results files.
  - **Archives:**
    - GIAA (GA private schools): state XC 2003-2020, track 2003-2024.
    - Washington Track: WA state track 1960-2025.
    - Seashore Striders: Delaware's first independent source.
    - AISA (AL private schools): state XC.
  - **Off limits:**
    - Bot challenges: Webscorer (AWS WAF, a correction), DigitalSports league sites (Sucuri), sjtrack.org.
    - robots.txt bars results for every crawler: TrackConference/Teammates, the Nassau County results database.
    - Bound (WAF 403).
    - RACE RESULT's data endpoints (robots.txt).
- **State and private-school association archives (2026-10-23):** `docs/timing_passes/assoc_archives.csv` has 36 rows. Private and Christian associations mostly post to MileSplit. The finds are deep state-meet archives that results_hubs.csv listed for a year or two:
  - **KHSAA (KY):** XC 1997-2026 and track 1997-2026, state and regions, Hy-Tek HTML with grade. Files are `khsaa.org/cross_country/<YYYY>/class<1a|2a|3a><boys|girls>finalindividualresults.htm` and `/track/<YYYY>/class<..>detailed.htm`. Only `/wp*` is disallowed in robots.txt.
  - **GHSA (GA):** state XC 2000-2024, 320 PDFs.
  - **LHSAA (LA):** XC 2002-2021, outdoor 2003-2026, indoor 2005-2026; Hy-Tek with grade.
  - **MHSA (MT):** XC and track 2002-2026, on rSchoolToday. Montana's first source.
  - **SDHSAA (SD):** yearbook PDFs 2004-2025 (placers only), South Dakota's first independent source.
  - **ASAA (AK):** XC 1973-2026, full fields about 2004-2021. Alaska's first source.
  - **Smaller:** NCHSAA (via its WordPress media listing, 2016-2022), Perfect Timing Group's old Hy-Tek live archive (2015-2018), the Wisconsin coaches' all-time list.
  - **TAPPS (TX private schools) and MTCCCA (MO):** the files are on Google Drive, whose download host bars all crawlers. Use the Drive API or ask.
  - **Off limits:** NSAA (NE; Cloudflare "you have been blocked" on the files, so ask NSAA), MIAA/IAAM (MD) and WCAC (DC) (CloudFront 403).
  - **States with nothing new:** AR, CO, CT, DE, FL, HI, IA, ID, IL, IN, ME, MN, NJ, NM, NV, OK, OR, RI, TN, UT, VA, VT, WA, WY.
