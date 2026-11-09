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
| All-time athletes board sorted by name: 503 after 55 s; grade/races/last 4-7 s. | A sort no index serves needs a state, school, grade or conference filter; otherwise the board explains this. | rankings.parseFilters |
| /compare "best race" was a 7th-grade own-pool 153.5; youth seasons with no HS value printed raw and took the edge. | The best race is the best HS value; season cells with no HS value are left out. | compare.athleteCard, stampEdgeHs |
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
- /records: TODO each state's top XC and track record inline.

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

## For the pipeline / data track

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
