# Arrow Live Results: adapter notes (2026-10-23)

Arrow Live Results (www.arrowliveresults.com, "Arrow Software, LLC", bought by Agee Race Timing) is a live-results host that many timers subscribe to. One adapter (registry kind `arrow`) covers every timer on it. This pass fetched every state index once, plus about 25 race pages and result fragments as samples. New timers are in `arrow_timers.csv`.

## Access

- **robots.txt:** returns 404 (an IIS error page), so none is in effect. ageeracetiming.com's robots.txt allows everything.
- **Terms:** none. `/Home/Terms` and `/Home/Privacy` redirect away, and `/Home/About` is the unedited ASP.NET template. `/Payment/Subscribe` says: "Subscription costs are for timers only. The results are free to the general public."
- **Bot challenge:** none. The site is plain ASP.NET MVC 5.2 on IIS (Azure; it sets ARRAffinity cookies, which can be ignored). All requests returned 200 with a plain user agent.
- **Verdict:** fine under the owner's rule. Stay polite: one request every 3-5 seconds.

## Endpoints

All of these are GET requests that return HTML fragments. **No JSON endpoint exists.** The only non-HTML endpoint is `/Results/GetRaceStartTime?pEventId=<n>`, which returns plain text.

| Endpoint | Returns |
|---|---|
| `/Home/pvStates` | The state picker as a `<select>` (see the state ids below). |
| `/Home/pvRecentEvents?pStateId=<s>&pCountryId=1&showAll=true` | A state's race index: one `<tr>` per race with date (M/D/YYYY), a link to `http://www.arrowliveresults.com/<Slug>`, race name, presenter (the timer), and location ("venue city, ST zip ST United States"). `showAll=false` returns only about the latest 30 rows. `pStateId=-1` or `0` returns nothing ("Choose a State"); there is no all-US list. |
| `/<Slug>` or `/Results/Events?pEventId=<n>` | The race page (both URL forms work). It holds the race name (`h1.raceName`), "Presented By" (the timer), the location, and the start time ("Tuesday, October 6, 2026 4:59:11 PM"). If no start time is shown, only a short date, the race never went live and its results are empty. In inline JS: `var EventId = <n>`, `LoadDivisions()` (`<option val=<divisionId>>Overall Male Runner ...`, padded with spaces), and `LoadOtherRaces()` (the same presenter's other races near that date). An unknown or purged id returns HTTP 500. |
| `/Results/pvResults?pEventId=<n>&pDivision=<division text>&pChipTime=false` | The results. See the next section. |
| `/ParticipantDetails/Details?pParticipantID=<n>` | One athlete's result: bib, name, race, start and finish timestamps, "Overall: 1 of 170" and division places, pace, speed, gun and chip time. Splits are shown when present. Not needed for the adapter. |
| `/AthleteSearch/pvAthleteSearchResults?pLastName=&pFirstName=` | Every race a name appears in, all years: date, race name, presenter and race URL. This reaches races missing from the state index. Use it for spot checks, not for discovery. |

### pvResults fields

1. **Team Scores (optional):** an `<h1>Team Scores</h1>` block with `<li class="list-group-item">` entries, each a team name plus a `<span class="badge">` score. It is present when the timer set up team scoring.
2. **Results table:** `<tbody id="resultsTableBody">` with columns **Place, Bib#, Name, Team, Gender, Age, Finish**. Some timers add more columns, such as **Split 1..n** (Bloomfield).
   - **Name** links to `/ParticipantDetails/Details?pParticipantID=<n>`. The cell also holds hidden mobile `<text>` copies of the bib and team; strip `<text>` before reading it.
   - **Team** is the school: either the full name ("Dickson High School") or a short one ("Nueva", "Olympia"). Some timers add a state or section tag ("Shadow Ridge (NV)", "Golden Valley (CE)").
   - **Gender** is M or F.
   - **Age** is not a grade. It is 0 (or 1 for some timers) for most rows, and a real age (13-18) for some athletes where the timer had it. **No grade column was seen in any sample.**
   - **Finish** is `HH:MM:SS.ss`, with an empty grey `<text>` after it.
3. **Paging and filters:**
   - There is no pagination: one response holds every finisher (144 rows in 120 KB).
   - `pDivision` takes the division's **text**, not its id. Empty means all; "Overall Female Runner" on a boys' race returns 0 rows.
   - `pChipTime=true` switches to chip times.
   - DNS and DNF entries are not listed. ParticipantDetails' "1 of 170" counts more entries than the 144 rows.

## How races group into meets

**Arrow has no meet entity.** Each Arrow "event" (pEventId) is one race, usually one division (for example "Waurika HS Boys"). Timers often add a numeric suffix to the slug, and that suffix is usually the EventId (WaurikaHSBoys25239 is pEventId 25239), but not always.

To group races into a meet, use **(presenter, date, venue/location, shared name prefix)**. For example, "Bridge Creek Regional 2A/3A/4A Boys/Girls" on 10/17/2026 from 12:1 Running is one meet.

- **`LoadOtherRaces()` is not a meet list.** It shows the presenter's other races around that date, which can include other meets.
- **Some races have only bare names.** Peak Performance ("Varsity Men"), Milepost ("Varsity Boys") and 915timing ("Men's 5000 Meters (Varsity)") give no meet name, so the meet needs a synthetic name from the presenter, date and venue.

## The index is not complete

**The state index covers only recent races.** Its daily counts jump around 2026-09-19. It looks near-complete for the last ~5 weeks, with older races appearing only sporadically. On 2026-10-23 the index held 1,843 US races in total:

| Period | Races in index |
|---|---|
| Sept 2026 | 399 |
| Oct 2026 | 751 |
| All of Sept 2025 | 123 |

- **Checked against athlete search:** one 12:1 athlete's 9 races (2025-2026) included 6 that are not in the index (for example BridgeCreekHSBoys18453, 8/23/2025).
- **Event ids are sequential and global:**

  | Event id | Date |
  |---|---|
  | ~11,700 | Sept 2023 (the first retained) |
  | 12,000 | 9/30/2023 |
  | 15,000 | 9/13/2024 |
  | 20,000 | 9/27/2025 |
  | 25,870 | 10/24/2026 (upcoming) |

  - Ids 1,500, 5,000, 10,000, 11,000 and 11,500 return 500, so everything before about Sept 2023 has been purged.
  - About **14,000 races are retained (Sept 2023 to now)**, roughly 3.3k, 5k and 5.9k per year. 2023 results are still there: pEventId 12000 returned 43 rows.
- **The state field is unreliable:**
  - 489 of the 493 races under "Arkansas" (pStateId 6) have a blank city and ", AR" location. AR is the form's default, so this bucket is a catch-all: 318 of 12:1's Oklahoma races are there.
  - "Other" (61) holds races with no state at all.
  - Take a race's state from its venue address when there is one, otherwise from the presenter's home state.
- **Duplicates:** races come as "(COPY) ..." clones, "TEST HS BOY", "Test Race", and the same race name with and without an id suffix (XM Timing's Mishicot races appear in both WI and AR). Drop any race whose page shows no start time or whose pvResults has 0 rows.

## Crawl plan for the adapter

1. **Discovery:**
   - **Ongoing:** fetch the 30 state indexes daily or weekly (30 requests). This covers the current season, because the index is near-complete for recent weeks.
   - **Also ongoing:** walk new event ids upward from the last maximum seen, about 100-150 a week in season. This catches races filed in the wrong state.
   - **Backfill (Sept 2023 onward):** only by id walk, about 14k race pages at 1 per 4 s, roughly 16 hours of crawling. Fetch pvResults only for races whose presenter is on our timer list and whose name looks like a school race, about a third of races.
   - **Better:** ask Agee (ageeracetiming.com) for an export or feed.
2. **Filter by presenter** against the registry's Arrow timer list. Then classify race names:
   - **HS:** HS, Varsity, JV, Sectional, Regional, District, Conference, Invitational.
   - **MS:** MS, JH, Middle School, 6th-8th Grade.
   - **Not wanted:** Elementary, K-2nd, 8U, age-group races like "(15-16)", Masters, 5K Run/Walk, Turkey Trot, Ruck, Triathlon, MTB.
   - **Not HS:** races at college meets (Peachbelt, ULM, FAU, Cameron College, LSU, Julius Johnson Men's 8K).
3. **Per race:** read the race page for EventId, date and start time, presenter and venue, then call pvResults with `pDivision=` (empty).
   - School comes from Team.
   - Gender comes from the Gender column (or the race name).
   - There is no grade, so mark grade as unknown. Ages above 0 can bound the class year loosely.
   - Distance is only in the race name ("5K", "5000 Meter", "2 Mile", "3200m"); otherwise default by level and state.

## State ids (from the site's picker, `/Home/pvStates`)

The picker offers 29 states plus Other. The ids look like an alphabetical list that includes DC and the territories.

| State | Id | State | Id | State | Id |
|---|---|---|---|---|---|
| AL | 2 | KY | 21 | NC | 39 |
| AK | 3 | LA | 22 | OH | 42 |
| AR | 6 | MI | 27 | OK | 43 |
| CA | 7 | MN | 29 | OR | 44 |
| CO | 8 | MS | 30 | SC | 49 |
| CT | 9 | MO | 31 | TN | 51 |
| FL | 12 | NV | 34 | TX | 52 |
| GA | 13 | NJ | 36 | VA | 55 |
| IL | 17 | NY | 38 | WA | 57 |
| IN | 18 |  |  | WI | 59 |

- **Other** is 61. Other countries use `pCountryId` 17, 32, 58, 86, 144, 178, 197, 203, 206, 235, 236.
- **States not in the picker** have no Arrow races at all, since the picker lists every state that has ever had a race: AZ, DE, DC, HI, ID, IA, KS, ME, MD, MA, MT, NE, NH, NM, ND, PA, RI, SD, UT, VT, WV, WY. One PA race, Wolf Creek's "Boys 3200m" (2023), is filed under Other.
- **KY, MO, NC and SC** are in the picker but their indexes are empty now ("No Results"). Any races there are older and reachable only by id.

## Timers with school races, by state

Counts are school races (HS + MS divisions) **listed in the index on 2026-10-23**, so they are floors. Rows filed under AR or Other are counted in the timer's real state. Timers marked "known" were already Arrow rows in `timing_companies.csv` (from `timers_more.csv`).

| State | Timers |
|---|---|
| AL | none (Allan Timing: trail and road only) |
| AK | none (Fall Line Fitness: road, bike, duathlon) |
| AR | none (the bucket is mis-filed races; Hudson Race Timing has 1 road race) |
| CA | RPM Timing Solutions 26 (new); RTS Race Timing 21 (known); J & M Timing Service 14, 2023 only, results empty (known company, new host); ELE Race Timing 4 (new). Total Health & Sport has youth-club XC only (Oakland Cubs, 9-10 age groups), not school. Bizz Running and SingleTrack Running: road and MTB only. |
| CO | none (2Rivers Race Timing: road only) |
| CT | Bloomfield Timing 4 (known company, new host) |
| FL | athletic Scoring providers 208 (known); MOGO 87 (known); J7 16 (known); Florida Race Day ~15 (known company, new host); Bv Timing ~14 (known company, new host); Positive Vibes 5 MS, 2024 (known company, new host). Timed Events: road only. |
| GA | Hardy Race Timing 28 (known) |
| IL | 5Kevents.org 11 (known). Affordable Race Timing: road only. |
| IN | Above and Beyond 111 (known); RCC Timing (Indiana) 84 (known); Watt's My Time 22 (known); Terkhorn 17 (known); Perfect Timing Race Management 2 MS (new) |
| KY, MO, NC, SC | none listed (empty indexes) |
| LA | McDuffie 21 (known); Chipped Timing 5 (known) |
| MI | Josh Carpenter 30 (known) |
| MN | Advantage Timing 43, plus 2 in WI (known company, new host) |
| MS | Run The Race 15 (known) |
| NV | decania 6 (new). Desert Dash / Battle Born Timing: ultras only. |
| NJ | Viper Timing Systems 12, 2023 only, results empty in the sample (known company, new host) |
| NY | none (Mid-Hudson Timing: road and trail only) |
| OH | none (Above and Beyond's 2 OH races are road) |
| OK | 12:1 Running 255, of which 205 are filed under AR (known) |
| OR | Milepost Adventures 6 (new) |
| TN | We Run Faster 1, 2024 (known) |
| TX | 915timing.com 26 (known; its USATF-style age-group races excluded); Eastwood Running Club Timing 14 (known company, new host); FlamesTC 9 (known). YRTiming: road only. |
| VA | Run Roanoke 46 (known company, new host) |
| WA | Peak Performance Timing 19 (known company, new host) |
| WI | XM Timing 35 (known); Advantage Timing 2 |

### Top 10 by HS volume

HS divisions only, from the index on 2026-10-23; the HS + MS total is in brackets.

| Rank | Timer | State | HS | HS + MS |
|---|---|---|---|---|
| 1 | 12:1 Running | OK | 165 | 255 |
| 2 | athletic Scoring providers | FL | 135 | 208 |
| 3 | Above and Beyond Timing | IN | 75 | 111 |
| 4 | MOGO Timing | FL | 69 | 87 |
| 5 | RCC Timing (Indiana) | IN | 55 | 84 |
| 6 | Advantage Timing | MN | 35 | 45 |
| 7 | Run Roanoke | VA | 27 | 46 |
| 8 | XM Timing | WI | 24 | 35 |
| 9 | Watt's My Time | IN | 22 | 22 |
| 10 | RTS Race Timing | CA | 21 | 21 |

Just below the top 10: 915timing.com (20 / 26), Peak Performance (19 / 19), Hardy (18 / 28).

### Sports

**Only XC was seen.** The index covers fall, and the few spring races it shows are road races, apart from one "Gulf HS last-chance track" race found earlier for athletic Scoring providers. Track on Arrow is rare at most.

## Corrections to earlier notes

- **The index is not all-time.** The "~390 races 2023-2026" for 12:1 in `timers_more.csv` is index rows (394 today, 318 of them under AR), so it is a floor.
- **Volume:** the earlier "200-500 HS meets/yr" for Arrow is plausible. The ~14k retained races since Sept 2023 include the road races.
- **Grade:** the earlier note "Age column was 0" holds for most rows, but some timers fill real ages for some athletes (RPM, Peak Performance, Milepost, ELE). There is still no grade.
