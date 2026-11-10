# Site audit, 2026-11-09: every page, what it is for, and keep / fix / cut

Live checks on racecast.co with the agent header. Usage counts are not in
yet: they come from `usage_daily` (the beacon table, monitor.py), and the
"Use" column is filled in once the owner pastes them.

Status key: **FIXED** = pushed to master in this audit · **TODO** = a site
fix this track will make next · **PIPELINE** = for the pipeline/data track
(not this one) · **OWNER** = a cut that needs the owner's yes.

## Fixed in this audit (on master)

| What was wrong | Fix | Where |
|---|---|---|
| Hereford HS course page: "Boys best Matt Mullally 168.2", an 18:17 three-mile from 1986. 11 Sherwood rows in the elem_m pool (no HS value) were sorted on their own-pool number above every HS number. The 1986 state-meet field read 13:10-14:10 5K equivalents. None of those rows are on the boards. | Course rating lists (best ratings, team bests, the header "best") keep only rows that are in ranking_results (they passed the board gates) and, when the list has HS values, rows that have one. This applies at render time on the precomputed pages too. The same HS-only rule applies in `rankByHs` (track venue bests). | app.py `_COURSE_GATE_SQL`, `_gateCourseRatings`, `rankByHs` |
| Home and /records "On this day" headlined a one-race 1991 Maryland season (Amanda White, 152.3). | The card takes a race only from a season of 3 or more rated races, which is the all-time season lists' own floor. This is checked at read time and in the build. | record_books.onThisDay, build_record_books.otdSql |
| /compare with nothing picked showed two empty boxes. | For each level, it shows the pair from this season's top 8 who have raced each other most. | compare.suggestions, app._compareSuggestions |
| Board share card `/card/board.png?...` printed own-pool ratings (Cole Hoff 163.1 on ms_m; HS 139.7). | The card now stamps and prints the HS value. | cards.py board card |
| `/venue/tf/<v>/xyz` answered 200 as a copy of the outdoor page; an unused side rendered "Venue 63470" empty. | Only `in` and `out` are accepted. An empty side redirects to the other side, or 404s. | app.venue_tf |
| What it takes, TX 5A: 96.0 ≈ 20:29, read off the private-school TAPPS final because its field was bigger than UIL's. | The race whose schools agree with the division is picked first, then the biggest. | cuts.py |
| What it takes: a stray "All" cell (MD: 3 runners, 2021) shown beside real divisions, with a page of its own. | "All" is dropped where a state has divisions. A one-race state needs 20 or more finishers. | cuts.py |
| /ncaa had no link anywhere. | Added to the Tools menu. | _topbar.html |
| NCAA conference names printed "Acc", "Mac". | Names are title-cased only when the source was all lower case. | ncaa.html |
| /recaps: 64 meets said "no results posted" (OSAA State: 980 finishers, 0 of 10 races scored); the forecast's division ids come from the entry list or last edition. | Each forecast race is matched to the result division sharing the most predicted runners (at least 3 and 20%), one to one. The model file name is gone from the page. Stored scores refresh at the next nightly forecast build. | meet_recap.matchDivisions |
| Every school page linked "Record book →" to "still being compiled". | The link shows only when the book exists. | school.html |
| All-time athletes board sorted by name: 503 after 55 s; grade/races/last 4-7 s. | Grade, last race and races get indexes matching the board order (built by step 10 with the board; until then and for other columns the unnarrowed board serves rating order and says so). The headers it cannot serve are greyed with a tooltip; a state, school or conference filter turns them back on. OWNER: name/school/state/first/year sorts on the whole country stay off (cut). | rankings.sortServed, build_ranking_results as_sort_*, rankings.js |
| /compare "best race" was a 7th-grade own-pool 153.5; youth seasons with no HS value printed raw and took the edge. | The best race is the best HS value; season cells with no HS value are left out. | compare.athleteCard, stampEdgeHs |
| Projections and state odds listed league classes as state divisions (CA "Class AAA", SF's city league; /projections/ca/aaa projected Lowell). | A school's class counts as a state division only when the school has no state division. State odds follow at the next build. | projections._divisionsUncached |
| /schools/<state> "Colleges" was mostly high schools (name lookup). | A college by its school_unit college units in that state; the name lookup is only the fallback. | app.schools_directory |
| Recruiting school page: "↑ faster" beside a slower best race. | The cell names the two season numbers it compares. | recruiting_school.html |
| Track courses CSV named 27k venues "loc:100003:out". | Venue names in bulk, "(indoor)" for the indoor side. | data_snapshots._trackVenueNames |
| Sitemap left out the tool pages. | Added the tools and the per-state what-it-takes and projections pages. | build_sitemap |
| /what-it-takes index was 51 bare links. | A table: each state's hardest division mark to make state (and its easiest), boys and girls, from the stored marks only (never a live compute). | cuts.indexRows |
| /ncaa index cards said only "32 teams · 9 regions". | Each card names the favourites by win chance, the last team in and the first out, from the stored weekly projection. | ncaa_pages.ncaa_index |
| Every XC meet share card fell back to the default image (KeyError: slice(None, 10, None): scoreRows returns a dict now). | The card reads scoreRows' teams list. | cards.meetCardData |
| /meets ?year= showed 0 meets, a date range 1, college level old HS meets: candidates were an arbitrary 3,200 and the date was applied after. | scripts/backfill_meet_dates.py dates every meet from its results (only 172k of 862k had a date) and indexes it; once the index exists the browse picks by date, newest first. | meets_filter.meetDatesIndexed, dateBounds |
| Tools menu "Training paces" landed on the XC course table (the Paces tab stays hidden until an athlete is chosen). | `#paces` switches the source to an athlete and focuses the athlete box. | conversions.js |

## Every page

| Page | For | Live, with real data | Verdict |
|---|---|---|---|
| `/` home | front door: boards snapshot, latest results, on this day | 190 links all 200; page is 311 KB | keep (OTD FIXED) |
| `/rankings` Athletes (season) / Best races / Fastest times | the main boards | all 6 pools × XC/TF load; every filter changes the result; no rows until JS runs | keep; TODO `sort=name` on the all-time ability board 503s after 55 s, and sort=grade/races/last take 4-7 s: allow non-rating sorts only on a filtered board |
| `/rankings` Teams tab | hypothetical top-7 meet | works | keep |
| `/rankings` Conferences tab, `/api/rankings/units` | rank leagues/sections | works; junk unit names ("Ihsaa Boys", "2", "Div Iii", "JUCO"/"NJCA") | keep; PIPELINE school_units names |
| `/rankings` Courses tab | courses by difficulty | 144 of the top 200 have 0 meets and no state; top is "Lowell Scott MS 1500m +68%" | TODO join n_meets/state; default minimum results |
| `/rankings/<sport>/<pool>[/<state>]` | crawlable season top 100 | all work | keep; TODO print the HS value in the HTML (JS swaps it now) |
| `/rankings/units` | redirect to units tab | ok | keep |
| `/unit/<kind>/<name>` | one conference/league | works | keep |
| `/schools`, `/schools/<state>` | crawlable school directory | works; the "Colleges" list is mostly high schools (CA: San Marcos, Poly…) | TODO classify by school_identity level, not name |
| `/breakouts` | biggest jumps this week | works; led by a merged person (Tien Nguyen: two results in one race) | keep; PIPELINE two-results-in-one-race check |
| `/movers` index | weekly movers | state picker only, nothing until you pick | OWNER: send the bare `/movers` to `/breakouts` (see cuts) |
| `/movers?state=` | this week in a state | works; 2-2.4 s first load; Breakouts/Above tabs repeat /breakouts | keep |
| `/state-odds/<st>` | state-meet odds | works; CA lists "Class AAA" (SF city league) as a state division | TODO drop class values a state's state_div covers (also projections) |
| `/scale` | rating to time | works | keep |
| `/goal` | time needed for a goal | HS goals work; NCAA DI "no results yet"; SEC/Big Ten from 2012-15 | TODO college_goal.py: recent championships are not matched |
| `/card/board/...png` | share images | work (query card FIXED) | keep |
| `/meets` | browse meets | default/state/school work; `?year=2025` gives 0 meets, `?from=..&to=..` 1 meet, college level gives 2015 HS meets, TF college 55 s | TODO meets_filter `_BROWSE_SQL`: order and date-filter the picked set (check first that today's "/meets browse" commit did not already do it) |
| `/meet/xc/<id>` | one meet | works | keep |
| `/race/xc/<m>/<d>` | one race (results/teams/track) | works; fresh races show "-" ratings with no note | TODO "ratings arrive after tonight's run" note |
| compiled race pages | a distance across divisions | duplicates the race page when there is one division; merges JV with varsity | OWNER: hide when one division (see cuts) |
| `/meet/tf`, `/race/tf`, `/meet/tf/<id>/compiled` | track meets | work; "Team scores" printed twice | TODO de-dupe |
| `/meet/preview/xc/<id>`, `/api/meet-preview` | forecast of an upcoming meet | works | keep |
| `/meet/recap/xc/<id>`, `/recaps` | how the forecasts did | 64 meets say "results not posted" while their meet pages have 980-1,458 finishers (OSAA, IHSA State); shows "model race_trees.pkl" | TODO meet_recap.scoreMeet: map forecast divisions to result divisions by gender + class + distance; remove the model file name |
| `/course/<name>` | course records, difficulty, eras | works; FIXED the elem/gated rows; 1986-94 MD races still rate ~17 points above the same time in 2016 | keep; PIPELINE race-day term on fields with no other races |
| `/venue/tf/<v>/<in/out>` | track venue bests | works (FIXED validation); boys and girls share one table | keep |
| `/search`, `/search/api` | search | works | keep; small: "1 athletes", empty query says `No results for ""` |
| `/card/meet/xc/<id>.png` | meet share card | fails for every meet (falls back to og-image) | TODO needs the server log line |
| `/athlete/<id>` | career page | works | keep; PIPELINE wrong home state (Oliver Horton CO→AZ; `_season_state` counts venue states), merged identities (Jackson Spencer, Cole Sprout ×2), pros in ms pools |
| `/athlete/<id>/profile` | printable recruiting sheet | works | keep |
| `/recruit/<id>` | recruit view | works for HS; renders for pros/college; "runners like you" college outcomes empty before ~2010 | TODO 404 for non-HS pools; PIPELINE HS-college links pre-2010 |
| `/compare` | head to head | works (empty state FIXED); "best race 153.5" is a 7th-grade own-pool number | TODO compare.py:351: no own-pool fallback |
| `/school/<name>` and `/prs` | team page | works; TF PRs 1.7 MB, 2.7 s | keep |
| `/school/<name>/records` | school record book | "still being compiled" for nearly every school (Stanford, Oregon, Herriman…) but linked from every school page | PIPELINE step 13h school part; TODO hide the link when no book exists |
| `/school/<name>/season` | weekly team tracker | HS works; colleges say "No rated boys races" | TODO 404/hide for non-HS |
| `/school/<name>/meets.ics` | calendar feed | valid; empty off-season | keep |
| `/embed/school/<name>` | widget | leaves out Herriman's #1 runner | TODO |
| `/records`, `/records/<st>` | state record books | work; composite teams listed as schools ("UNA UT") | keep; TODO index content (see thin pages) |
| `/recruiting` | where would I fit | works; `/api/recruiting/schools` sends 1.3 MB per filter change | keep; TODO page it |
| `/coaches` | coaches hub | works; "Recruiting projections: being built" though the search has them | TODO remove the stale placeholder |
| `/coaches/recruits` (`/recruiting/search` redirects here) | recruit search | filters work; projection sorts led by 96.9→158.7 "projections", 3-5 s | OWNER: cut the projection/gain sorts (see cuts) |
| `/recruiting/school/<name>` | a college's recruits | "↑ faster" on a slower race (compares a season average with a best) | TODO compare the same number shown |
| `/conversions` | equivalents | works (paces FIXED) | keep |
| `/predictions` | predict a meet | loads, controls work; NAIA championship listed twice (two feeds) | keep; TODO upcoming_preview.championshipKey (list only, not the model) |
| `/projections` index | who wins state | 51 state links; the same picker is on /predictions | OWNER: redirect to /predictions' state mode (see cuts) |
| `/projections/<st>[/<div>]` | state meet projection | works; CA invents "Class AAA" etc. | TODO (as state-odds) |
| `/what-it-takes` index | marks to make state | 51 state links | TODO show the national make-state table |
| `/what-it-takes/<st>[/<div>]` | per-state marks | works (TX/All FIXED) | keep |
| `/ncaa` index, `/ncaa/<div>/<g>` | NCAA field projection | division pages are rich; index is 6 cards | keep (link FIXED); TODO bubble teams on the index |
| `/data` and CSVs | ratings download | XC fine; track courses CSV has internal keys for names, 82% zero difficulty, 3,981 athletes | TODO data_snapshots: venue names, drop empty columns, check the athlete count |
| `/about`, `/report`, `/contact`, `/updates`, `/login` | info and forms | work | keep |
| signed-out `/account*` | account pages | redirect to login; guest upload works | keep |
| `/sitemap.xml` | crawl map | leaves out /projections, /what-it-takes (and states), /ncaa, /data, /updates, /movers, /scale, /goal, /breakouts | TODO build_sitemap FIXED_PAGES |
| robots, manifest, sw.js, card PNGs | plumbing | fine | keep |
| admin pages (`/account/admin/*`, `/account/status*`, `/debug/*`) | owner tools | not opened (need sign-in) | keep, not audited |

## Thin index pages (the owner's list)

/movers, /projections, /what-it-takes, /ncaa and /records each open on a
bare list of states. The plan:

- /projections duplicates the "Who wins state" picker on /predictions: cut
  (OWNER).
- /movers adds nothing without a state, and its state pages repeat
  /breakouts' lists: cut the bare index (OWNER).
- /what-it-takes: TODO a national table, each state's make-state mark for
  its biggest division.
- /ncaa: TODO the bubble (last in / first out) on each card.
- /records: held. Each state's top record is exactly the kind of row the solver fix will move (MD girls #1 is Amanda White's one-race 1990 season), so the index features record rows only after that fix.

## Proposed cuts (nothing deleted until the owner says yes)

1. **/movers with no state**: 301 to /breakouts. Keep /movers?state=.
2. **/projections index**: 301 to /predictions' state mode. Keep
   /projections/<st> and its divisions.
3. **Compiled race pages with one division**: no link, and 301 to the race
   page.
4. **Recruit search projection sorts** (proj, proj_rating, gain_pct, gain_z,
   gain_resid): they put nonsense at the top and take 3-5 s.
5. **The /coaches "being built" placeholders.**
6. **/school/<college>/season and /recruit/<id> for non-HS athletes**: 404.
   They show HS words and empty states for colleges and pros.

## Notes for the chats that follow

- **Editing racecast/cuts.py puts What it takes on the slow path.** Each
  stored state row (wit_marks) carries a hash of cuts.py (`cuts.CODE`); any
  edit to the file makes every row unservable, so every state page computes
  live (the slow path the table exists to avoid) and the /what-it-takes
  index shows dashes until the rows are rebuilt. After a deploy that touches
  cuts.py, have the owner rebuild at once rather than wait for the nightly
  run (a few minutes, four states at a time):

      cd /srv/xc-predictor && set -a && . /etc/xc-predictor.env && set +a && /srv/venv/bin/python -u racecast/build_wit_marks.py


## For the pipeline / data track

- Step 10 now builds six more athlete_season indexes (as_sort_grade, as_sort_last and as_sort_races, each all-time and per season; build_ranking_results' index list). They add build time and disk to step 10. They serve the athletes board's grade, last-race and races sorts (rankings.sortServed).
- 1986-94 Maryland (Hereford) and 1980s Woodward Park races rate about 17
  points above the same time on the same course in 2016. "Field ran" reads
  +3 to +6% in those years against 0 to -4% now. The race-day term is
  reading slow fields as slow days when the runners have no other races.
  Amanda White's one-race 1990 season is #22 all-time hs_f (157.7 for
  18:43).
- Pools: adults from open races in ms_m (Bowerman 5K "Adults and Youth":
  Cole Hoff #1 all-time ms_m); college and pro runners in ms TF pools; a
  college runner merged into an elem_m identity (Robert Grote 179.6, Van
  Cortlandt).
- A women's race carrying the men's distance (Steve Reeder 2021, Utah
  State women "7000 m"): college_f #1-3 all-time.
- Merged identities: two results in one race (Tien Nguyen), Jackson
  Spencer's Ohio season, Cole Sprout split in two.
- Home state from venue counts (Oliver Horton CO→AZ, Conner Mantz "Sky
  View (OR)").
- School record books (step 13h school part) are nearly empty.
- school_units junk names; "Remove Remove" (107 races); grade "2010";
  school "11 34-Unattached".

## Second pass, 2026-11-10: features inside the pages

The first pass judged whole pages. This one went through every block,
tab and button inside them.

**Fixed in this pass**
- School page "Best athletes" left out the school's best runner (Denton
  Guyer: Ruel Newberry, national #2): one row per person took the max
  across pools, and a middle-school number won. Now one row per person per
  pool (school.schoolTopAthletes).
- State-meet forecasts kept forecasting meets already run ("64% to finish
  top 8 if Ruel makes state" after he won it; /projections/mi/1 projecting
  Jenison 156 pts after it won with 100). The athlete line, the athlete and
  school odds lines stand down once the state's final is on record; the
  projections and state-odds pages say the meet has been run and link the
  result (cuts.stateFinalFor).

**Same job, several places (owner decides)**
- Forecast a state meet: /projections/<st>/<div> and /state-odds/<st> (the
  same simulation twice), the /predictions picker, the school tracker's
  "Projected state place", the athlete line. Keep /projections.
- Rating to time: /conversions, /scale, the race and course "On the track"
  tabs, every "5K equiv" column.
- "What do I need": /goal (74 views), /what-it-takes, the athlete
  "points short of" line, /recruiting "Enter a time".
- College fit: /recruiting, /recruit "In college" ranks, athlete "College
  fit", "Similar runners" and "One-page profile" links.
- Find recruits: /coaches (0 views, no link anywhere), /coaches/recruits
  (3 views), /recruiting.
- Who improved: /breakouts, /movers (0 views), five "most improved" sorts
  in the recruit search, /recruit "Gain".
- Forecast one meet: /predictions, /meet/preview, meet "Predict this
  meet", race "Predict next year" (a field whose seniors have left),
  /compare "Both athletes in one race".
- Grade our forecasts: /meet/recap (2 views), /recaps (10), /updates.
- Team scores: the race page's Teams tab and its Team scores sidebar, both
  on desktop.

**Bad or noisy features**
- Athlete page record badges: 215 badges on 116 races; R, RSR and "CR
  (Overall)" on courses run once.
- Athlete sidebar "Average rating" (a blend of grade 7 through senior
  year) and "Best rating" (repeats the header).
- "1st on team" on club, Junior Olympic and national meets.
- Course difficulty shown as "+7.8%" with no sign explained.
- /recruit "Runners like you": "0 of 1 (0%) ran in college" for the
  national #2 junior; medians over one runner.
- State projections race every school in the division (MI D1: 159 teams;
  the real meet had about 27), so team scores are inflated.
- Home page: 4 levels × 3 tabs × 25 rows of rankings; previews that link
  to "Nothing to project for this meet yet"; "On this day".
- Rankings "Show each level's own scale" and the predictions copy of it:
  against the HS-equivalent rule.
- /updates prints "MS XC ratings rose 4.0" one day and "fell 4.0" the next.
- Wrong labels: "IHSAA GIRLS" on a boy; "Grade 19+", "SR-4", "2027 Track",
  an NCAA season labelled "Professional"; "League 1 · #2 of 18 in 1" on
  /unit; MI "Class A/B/C/D/OPEN" phantom divisions on projections and what
  it takes.
- The same venue as two courses (Michigan International Speedway and
  "Michigan International Speedway (MIS)"), so the 2026 MHSAA final has no
  difficulty and is missing from the record history.
- Nothing tracks clicks on tabs, pickers or most buttons (usage.EVENTS),
  so most in-page features have no usage numbers.
