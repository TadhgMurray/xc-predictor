# Racecast wording: glossary, house style and every string worth changing (October 2026)

This is part of the October UX review. It covers every piece of text a visitor can see on the site:
- page templates (2,785 unique strings);
- static JS strings;
- display text produced by Python (`explain_rating.py`, `race_story.py`, `cuts.py`, `recruiting.py`, `record_books.py`, `meet_recap.py`, `panels.py`, `seo.py`, `cards.py`, `follow_alerts.py`, `alert_email.py`, `decision_email.py`, `social.py`).

Admin pages are left out. Counts are approximate. Line numbers point to where each string starts.

---

## 1. Glossary: one public word per idea

| Idea | Use | Stop using |
|---|---|---|
| The number for a race | **rating** (first mention on a page: "speed rating") | "athlete rating", "HS-equivalent rating", "rating's own scale" |
| The number for a season | **season rating** | "season ability", "athlete rating", "level" (above-level, movers) |
| A group rated together | **level** (HS boys, MS girls, college women) | **pool** (≈35 visible uses), "group", "board" (for the group) |
| The one public scale | no name; "on one scale" | "HS-equivalent", "high-school scale", "own-pool scale", "the scale the model fitted" |
| The time a rating is worth | **Track 5K ≈** (MS: "Track 3200 ≈") | "5K equiv", "Track equivalent", "Rating equivalents", "flat-5K equivalent" |
| How hard a course is | **course difficulty**, in words and seconds ("about 20 s slower than average on a 5K") | a bare "+3.5%", "Venue 0.0%", "gives or takes", "the ground" |
| What the day was like | **conditions** | "Day", "Race Day", "race-day term", "Field ran", "Together" |
| Divisions merged into one list | **combined results** | "Compiled" (also used to mean "built") |
| The number of track results | **results** | "Marks" used as a count |
| 6th and 7th runners | "6th and 7th runners (don't score, but push others back)" | "displacers" |
| Team strength | **team average (top 5)** | "Top-5 avg", "Top 5", "top-5 mean", "mean of the top five", "Team rating" |
| Best rating this season | badge **SB** | "RSR", "Rating Season Record", "SR" next to a rating on race pages |
| Best rating ever | badge **PB** | "R", "Rating Record", "PR" next to a rating on race pages |
| Time PR / season best time | **PR / SR** (keep) | — |
| A simulated chance | **chance** | "odds", "model estimate", "draws", "simulated runnings", "sd" |
| A forecast | **prediction**; "forecast" only for the one saved before the meet | "projection" for the same thing |
| Compare tool | **Compare** | "Head to head" |
| College tools | **College fit** (athletes), **Recruit search** (coaches), one **Recruiting profile** | "Where would I fit in college", "Athletes: where you'd fit", "Similar runners" as a profile, two profile pages |
| Gender | HS/MS: **Boys/Girls**; college: **Men/Women** | "Boys / men", "Boy or girl" on a college tool |
| Grade | **Grade 11** or **Class of 2027**, one per context | "gr", "Gr", Fr/So/Jr/Sr mixed with numbers |
| Sports | **Cross country / Track**; "XC" only in tight table cells | "Track & field", "TF" (visible), "Track & Field" |
| League etc. | name it: **league / section / division / conference** | "unit", "units" |

## 2. House style

- **Voice:**
  - Write "you" to the reader and "we" for Racecast.
  - Never name the model, engine, pipeline, server, solve, refit or rebuild.
  - Say what it means for the runner. Put how it's computed in About, behind "How this works".
- **Case:**
  - Use sentence case for headings, tabs, buttons, tooltip titles and page titles.
  - Don't type ALL CAPS. If small capitals are wanted, do it in CSS.
- **US spelling:** program, normalize, labeled, modeled, neighboring, honors, favors, meters.
- **Units:**
  - Tables: "5000 m", "1600 m" (with a space).
  - Prose: "5K", "8K"; "2 mi".
  - °F; wind in mph; rain in inches, or no number at all. Never "metres" or "mm".
- **Numbers:**
  - Put thousands separators on every count (1,284).
  - Show ratings to one decimal.
  - Show times to tenths, unless the meet recorded hundredths.
  - Use "≈" for "about", never "~".
- **Dates:**
  - "Oct 17, 2026"; with the day, "Fri, Oct 17". Never UTC.
  - Seasons: "2026 XC", "2027 Track", never "2026-27".
  - One definition of "week", the same everywhere. Movers starts weeks on Friday; the tracker ends them on Sunday.
- **Dashes:**
  - Use an em dash with no spaces (—) for asides, and "–" for ranges.
  - Never " - " or " -- " as punctuation (there are currently 53 and 8).
- **Separators:** " · " between facts. Page titles: "Page | Racecast" everywhere.
- **Tooltips:** a title of 4 words or fewer in sentence case, and one sentence of 140 characters or fewer.
- **Links:** say where they go; an arrow is decoration only.
- **Placeholders:** start with "e.g.", in grey. Never pre-fill a real-looking value.

---

## 3. Every string, by type of problem

### A. Jargon

**"pool" (visible):**
- `_scale.html:89` "Show each pool's own scale".
- `_scale.html:90` "…the scale the model fitted".
- `about.html:86-88` "the model fits each pool…"; `about.html:100` "a curve fitted per pool".
- `about.html:192` heading "Some athletes are in the wrong pool"; `about.html:197-198` "the scale is pool relative".
- `about.html:260` "pool by pool"; `about.html:265` "Own-pool scale (Advanced)".
- `athlete_profile.html:108` "(100 is the pool's average)". This contradicts the one-scale rule; use "(100 is an average high-school runner)".
- `conversions.html:63` "Pool".
- `landing.html:84` "Pool".
- `rankings.html:147` "Pool"; `rankings.html:153` "All pools".
- `rankings.js:1170` column "Pool".
- `scale.html:20` title "Rating to time: every pool".
- `conversions.js:420` "(own-pool scale: …)"; `conversions.js:436` "(own pool)".
- `recruiting-search.js:188` "built per pool and season".
- `explain_rating.py:366`, `:387`, `:568`.
- "Group", a third word for the same thing: `rankings.js:1935`; `_equiv_line.html:45`, `:49`, `:107`.
- Use **level** for all of these.

**"HS-equivalent" / "high-school scale" (a second scale named on a one-scale site):**
- Athlete chart titles "XC SPEED RATING - HS-EQUIVALENT" (`athlete-charts.js`, `data-title`).
- `_equiv_line.html:108`; `equiv-line.js:168`; `conversions.js:419`.
- `rating-explain.js:266` "This page shows X on the HS-equivalent scale; the steps are on the HS boys scale, where it is Y".
- `about.html:264`.
- `recruiting.html:39`.
- `recruiting_school.html:95`, `:131` "college scale" column, `:144`.
- `embed_school.html:92`; `scale.html:83`; `shortlist.html:115`.
- `recruit.html:178` "through the same scale conversion the mixed boards use".

**Statistics talk:**
- **The season rating is explained two ways that contradict each other:**
  - "the 80th-percentile race": `projections.html:325`, `team_season.html:94`;
  - "averaged across a season": `rankings.html:112`, `rankings.js:2239`.
  - Use one sentence everywhere: "A season rating is close to a runner's best few races, so one bad day doesn't sink it."
- `what_it_takes.html:358`, `:375` "the {x}th percentile…". Use "the rating 90% of qualifiers had reached".
- `athlete_profile.html:91` "25th-percentile recruit"; `recruiting.html:134` "quartiles".
- `recruit.html:112` and `comps-fan.js:46` "Middle half". Use "most runners like this".
- `predictions.js:3002` "in 8 draws out of 10"; `:3004` "mean X, sd Y"; `:3009` "% of draws"; `:3218` "simulated runnings".
- `state_odds.html:179-180` "…with unlimited runs, so the whole percents are not rounding noise".
- `ncaa.html:243` "the measured race-to-race spread"; `ncaa_pages.py:41` "point gap ratio"; `ncaa.html:45` and `ncaa_pages.py:38` "In · pushed".

**Model and engine talk (mostly the "How this was rated" sheet):**

| Where | Current | Use |
|---|---|---|
| `explain_rating.py:376-380` | "…converts to {x} at the {x} reference on the {x} distance curve…" | "{time} for {dist} is like {t} for 5K on an average track" |
| `:415-419` | "reference era" | "Times ran faster everywhere in {year}, so this one counts {x} less" |
| `:467-471` | "normalised before the latest refit … until the next backfill" | remove from the public sheet |
| `:498` | "(the engine applies {x}x of a course at this ability)" | remove (it's the tilt) |
| `:507` | "the solve kept no race-day term" | "Not available for this race" |
| `:518-519` | "ratings do not carry the race-day term (it only keeps the course number honest)" | "Track ratings don't adjust for the day" |
| `:526-527` | "ratings carry only a FAST day…" | "A fast day lowers ratings; a slow day doesn't raise them" |
| `:532`, `:549-550`, `:554-555`, `:562-564`, `:899-900` | weather model / distance curve / "priced without a solve" / "votes on no course" | short plain sentences |
| `:221`, `:227-228`, `:240`, `:243`, `:251-252` | "weather model is not loaded on this server", "no GPS location", "the weather grid", "grid cell…describes terrain" | "Weather unavailable for this race" |
| `:606-610` | "a track race is read at the day's peak heat even if it ran in the evening" | "Track races are rated with the day's afternoon weather" |
| `rating-explain.js:275` | "+ means the conditions were judged slow and the time is credited" | "+ means the time counts as faster" |

**Other model and engine talk:**
- `_course_eras.html:52` "a race the model set aside -- a twin…". Use "a duplicate".
- `_course_history.html:22` "Difficulty by era"; `:31` "(per-era numbers arrive with the next ratings publish)".
- `_course_history.html:32-37` "Field ran… Together… against an average **track**". That should say "course" for XC. Rename the columns "Conditions" and "Overall".
- `_course_history.html:76` "beyond the 10% cap: the result sheet, not the day, is suspect".
- `_explain.html:55-56`: the course/day tooltip is **451 characters**, the longest on the site. Use "Course: {words}. Conditions: {words}. Ratings include both."
- Era wording:
  - `about.html:110-111` "The era correction";
  - `home.html:216` "years, era-adjusted";
  - `records_index.html:12`;
  - `record_books.py:27-29` "every course is re-measured in each two-year era";
  - `record_pages.py:103`.
  - Use "Ratings adjust for how times have changed over the years."
- "corpus": `about.html:121`, `coaches.html:97`, `predict.py:1057`, `predict.py:1349` "No rated races in the corpus."
- `about.html:123-124` "Courses with not enough links are forced to a rating of 0.0. The average XC rating is about +0.01…". That calls difficulty a "rating".
- "refits / refitted": `about.html:249`, `updates.html:65`. Use "recalculates".
- `rankings.html:566` "difficulty is applied after distance is normalised".
- `rankings.html:586-588` "the far side of a thin linkage graph… Untick to compare like with like". Use "Ratings for athletes outside the US are less certain."
- `rankings.html:430` "3000m and up (XC projection)".
- Season floor:
  - `rankings.html:404` "Seasons from 2026 cross country on have no minimum unless you set one";
  - `about.html:270` "…seasons from 2026 cross country on have no floor".
- The teams board notes (`rankings.js:1462-1508`): "stored squad ratings", "scored the ordinary way", "every squad of every season in one field". Use "All teams raced in one imaginary meet."
- `rankings.js:2241` and `about.html:263` "Raw clock and tape". Use "Times and marks as recorded."
- `rankings.js:2246` "Single performances - the best individual races, noise and all".
- `rankings.html:558`, `home.html:80` "one hypothetical meet… scored the ordinary way".
- "unit", "units":
  - `_units.html:8` "…corroborated more than one unit here…";
  - `about.html:268`;
  - `coaches.html:89` "Rankings by your unit".
- `athlete.html:408-409` "a small unresolved state-to-state offset". Use "National ranks are approximate for close places."
- `athlete.html:142`, `:146` (board notes) "race fields too inconsistent to confirm it", "one class word from one race".
- "Level" collides with level = HS/MS/college, so "vs level" should become "vs usual":
  - `_above_level.html:15-30` "Ran above their level", "vs level";
  - `movers.html:159`, `:163`, `:179-181` "Over level", "the season statistic".
- `breakouts.html:162-163` "median". Use "typical".
- `recruit.html:104`, `:106` "rated within {x} of {x}".
- `recruit.html:131-134` "…the model is the one to doubt".
- `recruiting_school.html:128`, `:142-143` "less the measured freshman gain", "(est.)".
- `recruiting_search.html:61-67`: 4 kinds of "Most improved" and 3 "(model)" sorts. The tooltips read "the network asked…", "Favours the bottom of the range, because everybody at 80…".
- "test the model":
  - `predictions.html:46`, `:81`, `:159`, `:188` "check the model against the real result", "Test the model on a past race".
  - Use "See how a past race would have been predicted."
- `predictions.js:2415-2417`, `:1538` "Coalesce is on: these entries score as ONE squad…". Use "Count both divisions as one team."
- "the card" is never defined:
  - `predictions.js:2444`, `:3717`, `:3733`, `:3744`, `:3777`, `:4727`.
  - Use "your lineup" or "the race".
- `predictions.js:3375` "Dual meet · Best seven". Use "Team vs team · Best lineup".
- `predictions.html:251`, `:254` "Skip each team's top". Use "Leave out each team's top __ runners (e.g. 7 for JV)".
- Mode names:
  - `meet_preview.html:131-133`, `predictions.js:3242` "in 'This year' mode";
  - `meet_recap.html:175` "Times on the '{x}' basis".
- `predictions.js:3256` "flat-5K equivalents".
- `next-race.js:48` "the prediction model's on this course and distance…".
- "displacers":
  - `coaches.html:83`, `meet_preview.html:133`, `meet_recap.html:176`, `projections.html:77`, `projections.html:328-329`, `race.html:186`, `state_odds.html:178`: "five scorers and two displacers".
  - `ncaa.html:244` "five score, seven displace" (inconsistent with the others).
- Top-5 wording:
  - `rankings.js:1138`; `course.html:191`, `:200`; `_records_macros.html:100`; `team_season.html:45`, `:71`, `:97`; `cards.py:914`.
  - All say "Top-5 avg", "mean of the top five", "Team rating" and so on.
- "1–5 gap": `team_season.html:46`, `:74`, `:97`; `_next_race.html:28`. It's in rating points; give it in seconds.
- `compiled.html:91-92` "compiled"; `about.html:269` "Compiled race… A race that did not happen".
- `meet_tf.html:57` "Marks {n}".
- `race_tf.html:71` "Wind on the sheet".
- The conversions form:
  - `conversions.html:54` "Source", "A specific result";
  - `:94` "- pick athlete first -";
  - `:103` "Time (hh:mm:ss)", with a value (not a placeholder) of `00:00:00`;
  - `:111` "Distance (m)";
  - `:145-200` "Input weather (optional - the conditions the source was run in)", "Apparent temp", "Precip (mm)", "Soil (mud)", "Don't have apparent temp? Enter its parts".
- `conversions.html:287` "Training paces & VO" (renders "VO2max").
- `conversions.js:636-638`, `:681-682` "Critical speed is fitted…".
- `athlete.html:446` "VO2max: critical speed {x}/mi, fitted from {n} track races".
- **`scale.html:93` "A rating of 100 is the group's average runner".** This contradicts `_explain.html:36` "100 is an average high-school runner".
- `_explain.html:36` "133 is 33% faster": drop it. A parent can't map this to a time.
- `about.html:80-83` "close in percentiles but not one-to-one"; `about.html:99`, `:102`.
- `about.html:105-107` "Geometry… may show a residual **affect**". Typo: should be "effect".
- `goal.html:50` "{x} points to find".
- "Thin":
  - `goal.html:120`, `what_it_takes.html:237` "Thin data.";
  - `projections.html:264`, `:358`, `what_it_takes.html:396` "thin";
  - `about.html:185` "Thin courses get a default".
- `state_odds.html:96-98` (qualifying simulation, long).
- `cuts.py:392-412` caution chips ("most finishers' schools are on record in another division").
- `explain_rating.py:595-599` "the time is credited {x}".

### B. Developer and ops language

**Commands and setup:**
- `fixes.html:38` "…run `python racecast/fixes.py --init`".
- `uploads.html:50-51` "…run `python racecast/uploads.py --init`".
- `my_page.html:56` "(racecast/follows.py --init)".
- `shortlist.html:60` "(racecast/follows.py --init)"; `shortlist.html:65` "not built on this server yet"; `shortlist.py:290`, `:297`.
- `rankings.js:1463-1465` "rebuild with `racecast/build_team_season.py`".
- `recruiting.html:51`, `recruiting.js:277` "It arrives with the next pipeline run (step 10f)".
- `ncaa.html:92-93` "built once a week by the pipeline".
- `breakouts.html:139-140` "need this server's nightly Breakouts build".

**Raw exception text shown to users:**
- `breakouts.html:145`, `:199`, `team_season.html:66` "could not be computed just now ({x})".

**Server, rebuild and compile wording:**
- `records.html:125-126`, `school_records.html:53-54` "rebuilt with the site's nightly data run".
- `login.html:51`, `:56` "The reason is in the server log", "Mail is not set up on this server yet".
- `login.html:38`, `:89`, `:115` "switched on".
- `error.html:30`, `:33-34` "rebuilding / server error", "The data is being rebuilt".
- `404.html:36` "moved during a data rebuild".
- `school.html:174-175` "Season tables are rebuilding. The roster and the year filter return when the rebuild finishes; everything below is live."
- `_course_history.html:31` "arrive with the next ratings publish".
- `scale.html:84` "Computed {x}."; `records.html:34` "· compiled {x}"; `_record_book.html:19` "Compiled {x}."; `breakouts.html:65` "computed {x}".
- `state_odds.html:190` "Built {x}; rebuilt overnight…".
- `updates.html:77` "· {x} UTC".
- `compare.html:96` "There is no athlete with id {x}."

**Error messages:**
- `predictions.js:1049-1064` "The server took too long (504)…", "The request was too large for the server (414).", "The site is restarting".
- `predictions.js:2768` "TypeError"; `:3817` "That row carried no athlete id."
- `rankings.js:260-261`; `recruiting.js:139-140`, `:307`; `recruiting-search.js:102-103`, `:239` "The server sent an answer this page could not read."
- `account.js:56` "Search did not answer."
- `athlete-charts.js:764`, `:806` "chart data is not valid JSON".
- `conversions.js:560` "Error: Could not resolve source."
- `my_page.py:260` "The prediction failed; it is logged."; `error.html:37` "It has been logged."

**Process language:**
- `predictions.js:2888` "Open-Meteo forecast fetched {timestamp}".
- `fixes.html:118` "queued for the scrape".
- "nightly update": `fixes.html:158`, `uploads.html:124`, `upload_view.html:51`, `decision_email.py:67-68`.
- `movers.html:183` "The sentences at the top are templates that fire only when their facts are in these lists."
- `movers.html:55`, `:92-94` "two weekly snapshots", "last week's board".
- `my_page.html:88` "Movement: since before {x}".

**Coach pages and calendar wording:**
- `coaches.html:64` "Being built (3)"; `coaches.html:124-129` the roadmap list.
- `coaches.html:153` "every filter lives in the query string".
- "Link" / "link the page": `fixes.html:41`, `account.html:84`, `:103`.
- ".ics": `school.html:94`, `my_page.html:109`, `:178`.

**End of list:**
- `search.html:157`, `rankings.js:1913` "No more rows past this page."

### C. Inconsistent terms (approximate counts)

- **Rating names:**
  - "speed rating" 45, "season rating" 39, bare "rating" ≈290.
  - "athlete rating" 1 (`athlete.html:347`), "Season ability" 2.
  - "Best rating", "Best speed rating" and "Best race" are mixed (`athlete.html:600`, `compare.html:193`, `athlete.html:367`).
- **Badges disagree between pages:**
  - The race page (`_recflag.html:37-43`) badges a best-ever rating **"PR"** and a season-best rating **"SR"**. These sit right next to the time's PR and SR badges, so "PR" means two things in one row.
  - The athlete page (`athlete.html:75-94`) calls the same facts **"R"** and **"RSR"**.
  - Breakouts (`breakouts.html:181`) calls the season best **"SB"**.
  - Use PB and SB for ratings, PR and SR for times.
- **Sport names:** "Cross country" 107, "XC" 18, "Track & field", "Track" 48+, "TF" 6 visible (`about.html:124` "for TF"), "Track & Field", "track and field" (`landing.py:104`).
- **Gender:**
  - "Boys" 47, "Girls" 37, "Men" 11, "Women" 10.
  - Mixed forms: "Boys / men" (`_course_eras.html:19`), "Girls / women" (`upload_view.html:106`), "Gender not recorded" (`venue_tf.html:119`).
  - "Boy or girl" on a college tool (`recruiting.html:74`).
- **Level labels:**
  - "HS boys" vs "High School Boys" (`landing.py:25-30`).
  - "High School" (`landing.html:86`, `record_books.py:46-47`) vs "High school" (`meets.html:169`, `record_pages.py:124`).
  - "high-school" written 70 times with a hyphen.
  - "College Men" (`cards.py:1272`).
  - "Elem Boys" (`rankings.js:1196`).
- **Grade:**
  - "Grade 12"; "Gr" (`race.html`, `compiled.html:113`); "grade {x}" (`athlete_profile.html:75`).
  - "Fr/So/Jr/Sr" (`rankings.js:568-569`, social posts).
  - "Class of" (8); "class year" (`recruiting_search.html:69`, `fixes.html:79`, `:85`).
- **The track-time column:**
  - "5K equiv" (`_scale.html:52`), "3200m equiv", "Track ≈", "Track equivalent".
  - "Track equivalents" / "Equivalent times" (`_equiv_line.html:24`, `:37`, `race_tf.html:88`) vs the race tab "On the track".
  - "Rating equivalents" (recruiting).
  - "≈ 15:12 5K on a typical course" (`athlete.html:358`), while its own tooltip says "on a track".
  - "flat-5K equivalents" (`predictions.js:3256`), "for a flat {x}" (`social.py:214`).
- **Team scoring:** "Team scores" 28, "Event points" (`race_tf.html:155`), "Team points" (`_tf_points.html:30`, `compiled_tf.html:181`), "TEAM POINTS" (cards), "Points" (`rankings.js:1137`).
- **Course difficulty:**
  - "Difficulty" 42, "Venue" 9, "Course".
  - "how much the course gives or takes" (`course.html:17`), "the ground" (`rankings.js:2245`).
- **Combined:** "Compiled" 30+ visible, "Combined" 1. "compiled" is also used to mean "built" (`records.html:34`, `:125`, `_record_book.html:19`).
- **Predictions:**
  - "Prediction" ≈375, "Projection" ≈80, "Forecast" 38, "odds/chance" 26, "Model estimate" 6.
  - "State projection" (`projections.html:177`), "State odds" (`state_odds.html:48`), "Who wins state?" (Tools).
  - `state_odds.html:64` "Model estimates, not predictions" sits inside Predictions.
- **Compare:** "Head to head" 7 (`compare.html:40`, its title), "Compare" 9, "Compare two athletes" (Tools).
- **College:**
  - "College fit", "Where would I fit in college" (`home.html:232`), "Athletes: where you'd fit" (`coaches.html:49`), "Where your times fit", "Where you'd fit" (`shortlist.html:85`).
  - "Similar runners" links to `/recruit/<id>#like`, a page titled "Recruiting Profile".
  - "One-page profile" goes to `/athlete/<id>/profile`, a second "recruiting profile".
- **Recruit tiers have three vocabularies:**
  - `recruiting.py:562-569`: "Top recruit / Solid recruit / Recruit range / (slower than most) / Just under / Not yet", plus "Scholarship range".
  - `recruiting_school.html:105`: "Fastest recruit / Top quarter (scholarship range) / Typical recruit / Recruit range starts / Slowest recruit (walk-on range)".
  - `shortlist.py:128-132`: "top quarter of their recruits / in range…".
- **The same lists under different names:**
  - rankings `:77-84` "Athletes (season) / Best races / Fastest times / Teams / Courses";
  - `rankings.js:2246` "Single performances";
  - `about.html:261-263` "Athletes board / Best races board / Fastest times board";
  - home `:281` "Top athletes / Best performances / Teams";
  - school `:196` "Best athletes / …races";
  - course `:116-121` "Best ratings / Team bests / Team records";
  - records `:86-88` "Best seasons / By grade / Team seasons";
  - record book `:22`, `:57`, `:66`, `:76`.
- **Find boxes have 11 different placeholders:**
  - "Find a runner or school"
  - "Find an athlete or school"
  - "Find a runner, school or race"
  - "Find an athlete, school or event"
  - "Find your name or school"
  - "Find an athlete, school or meet"
  - "Search athletes, meets, courses…"
  - "Athlete, school or meet"
  - "Search for an athlete"
  - "Search athlete…"
  - "Name, or paste an athlete link"
  - Underlying this, "runner" (144) and "athlete" (400) are used interchangeably.
- **Seasons:** "2026-27" (`rankings.js:582`) vs "2026 XC" / "2027 TF" (`app.py:3162`, which claims the two agree); "Academic year" (`rankings.html:357`); "WEEK OF OCT 10".
- **"Level"** means HS/MS/college (`meets.html:168` and others) and also "the runner's usual" (`_above_level.html`, `movers.html`).
- **Minimum filters:** "Min results", "Min races", "Min athletes", "Races at least", "at least [3] recruits".
- **Spelling pairs:** "programme" vs "program"; "Modeled" (`ncaa_pages.py:42-44`) vs "modelled" (`projections.html:337`).
- **Account names:** "Sign in" vs "Racecast account" vs "Settings" vs "Your account" vs "My page".
- **Email names:** "Alert emails" vs "digests" vs "Email me:".

### D. British spellings (the audience is in the US)

- "programme(s)":
  - `athlete_profile.html:23`, `:90`, `:93` ("Programme" column);
  - `coaches.html:111`, `:126`;
  - `recruiting.html:134`;
  - `schools.html:44` "biggest programme first";
  - `recruiting.js:285`.
- "normalised": `rankings.html:566`, `explain_rating.py:467-468`.
- "labelled": `about.html:177`, `:269`. "mislabelled": `breakouts.html:183`, `:220`, `:245`.
- "neighbouring": `athlete.html:269`, `:295` (four times).
- "modelled": `projections.html:337`.
- "honours": `what_it_takes.html:362`.
- "Favours": `recruiting_search.html:66`.
- "metres": `uploads.html:94`; `teams.py:178`, `:180`, `:266`, `:270` (API errors shown in the UI).

### E. Tone (these are mostly minors)

- **`meet_recap.html:106` "Fell short of it the most"**: a public, named list of high schoolers, with `:113` "Nobody ran slower than we had them." The meta description of `recaps.html` boasts "the top ten named". Drop it for HS and MS, or turn it into "Our biggest misses" with no names.
- **`movers.html:100`, `:173` a "Fallers" tab**, also at `:94` and `:138`: a named list of minors who dropped. Keep "Risers" only, or call the tab "Rank changes".
- **`athlete.html:269`, `:295-296`:**
  - The tooltip reads "not counted: far off this athlete's neighbouring races — **a jog, a race run easy, a bad day**".
  - The badges read "not counted" and "**out**".
  - Use "not in average" with "Much slower than this runner's other races, so it isn't used in season numbers."
- **`athlete.html:295` "Not ranked: … usually a wrong distance, a wrong time, or two people's results under one name"**: this tells the public a kid's best race is probably an error. Use "Under review: kept off rankings until checked", plus a link to confirm it.
- **`breakouts.html:182-184`, `:220-221` "a jump this large is usually a data error"**: the same problem on a list meant to celebrate.
- **`recruit.html:109` "…which says something too."** and **`:133-134` "the model is the one to doubt"**: cute and vague.
- **`social.py:203`, `:256` "Who's too low?", "Who's missing?"** on public posts about ranked minors invite pile-ons. Use "Who's next?"
- **`rankings.js:2239` "one lucky race cannot carry an athlete"** and **`:2246` "noise and all"**: mildly dismissive.
- **`what_it_takes.html:378` "…known to be wrong right now"**: alarming. Use "Division assignments can be wrong; report one."

### F. Tooltip length, case and capitals

- **Tooltips over 140 characters:**
  - `_explain.html:56` (451)
  - `_above_level.html:26` (276)
  - `athlete.html:408` (234)
  - `athlete.html:295` (228)
  - `recruiting_search.html:65-66` (≈185)
  - `predictions.html:254` (≈190)
  - `athlete.html:142`, `:146` (≈190)
  - `_scale.html:90` (≈200)
- **Title Case on an otherwise sentence-case site:**
  - `_explain.html:35`, `:41` "What a Rating Means", "What Course Difficulty Means".
  - `_above_level.html:25`.
  - `_explain.html:55` "Race Day".
  - `_recflag.html:20-42` "Personal Record", "Season Best rating".
  - `athlete.html:63-94` "Personal Record (Overall)", "Rating Season Record".
  - `search.html:134` "Last Raced".
  - `landing.html:86`, `landing.py:21-30`, `record_books.py:46-47`, `cards.py:1272`.
- **ALL CAPS typed into strings:**
  - `explain_rating.py:527` "FAST day".
  - `predictions.js:2415`, `:2417` "ONE squad", "SEPARATE teams".
  - `social.py:109` "WEEK OF".
  - The `cards.py` image labels, including "MEDIAN MISS" (which is also jargon).
- **Lowercase tooltip starts:** `athlete.html:269` "not counted: …", `_course_history.html:76`, `_equiv_line.html:109` "shaded: …".
- **Odd title:** "Who wins state?" renders as "Who wins state? on Racecast" (`projections.html:73`, `:132`).

### G. Placeholders, buttons and link text

**Placeholders that look like real values:**
- `fixes.html:94` "Jesuit"; `:95` "OR"; `:85` "2027".
- `predictions.html:237` "7"; `:253` "0".
- `rankings.html:403` "3".
- `recruiting_search.html:56` "+0"; `:47` season year; `:57` the default floor.
- `recruiting.html:70` "16:32".
- `conversions.html:104` is not even a placeholder: the field is pre-filled with `value="00:00:00"`.
- "A typical course" looks like a choice already made: `goal.html:99`, `_h2h_predict.html:29`. Similarly "the meet's own course": `predictions.html:220`, `predictions.js:4131`.

**Buttons:**

| Current | Where | Use |
|---|---|---|
| "Go" | `breakouts.html:108`, `movers.html:82`, `recaps.html:48`, `what_it_takes.html:231` | "Show" |
| "Open" | `ncaa.html:88` | "Show board" |
| "Link" | `account.html:103` | "This is me" |
| "Apply", while other controls apply instantly | `recruiting.html:125`, `breakouts.html:135` | apply on change |
| "Update" | `shortlist.html:74` | "Update fit" |
| "Filter" | `meets.html:235` | "Show meets" |
| "Place me" | `recruiting.html:78` | "Find my fit" |
| "Show the time", styled as a secondary link | `goal.html:112` | style it as the main button |
| "tap yours" (a heading) | `_conv_side.html:8` | "Find your race" |

**Links:**
- `athlete.html:222`, `:235` use "Race results →" and "Meet results →" as the *meet name* when a meet has none. Use "Unnamed meet".
- `_next_race.html:22` "One-page profile →".
- `athlete_profile.html:41`, `:107` "Live page →".
- `meet_tf.html:52` "Share best marks" plus a second Share button.

### H. Typos, punctuation and formatting

**Typos:**
- `about.html:107` "affect" should be "effect".
- `about.html:205-210`:
  - The fallback text renders "**Tens of** millions" with a capital T mid-sentence, and "three decades **years**".
  - It says "Over 200 million stored results", while the home page says "59M rated results".

**Double spaces:**
- `coaches.js:48` "coach in  settings  and".
- `coaches.js:68` "in  settings .".
- `h2h.js:166` "Athlete  Predicted".
- `rankings.js:1465`, `:1481`, `:1493`, `:1500`, `:1601`.
- `next-race.js:74` ", led by  {x}  ~{x}".

**Separators run into a meta line:**
- "· Share" falls alone onto its own line on phones.
- `contact.html:30` "No account needed · we reply by email · something wrong on a page?"
- `report.html:41`.

**Dashes in visible strings:** " - " 53, " -- " 8, "&mdash;" 4, "&ndash;" 5, "–" 7. Examples:
- `_equiv_line.html:75` "Time here -- type one, or drag the ruler".
- `athlete.html:568` "Speed rating - all races".
- `_meta.html:78` "Racecast - every result…".
- `report.html:44`, `:86`.
- `meets.html:132`, `:408`.
- `about.html:174`, `:195`, `:212`, `:218`.

**Distances:**
- "5000m", "5000 m", "{x} m" with a non-breaking or thin space, and "{x} meters" (`race.html:58` aria) all appear.
- "5k" and "8k" in lowercase (`rankings.html:567`, `:573`).

**Counts without thousands separators:**
- `school.html:83` "Athletes 1843".
- `meet_tf.html:129`.
- `meet_recap.html:89`.
- `meets.html:116`, `:463`.
- `compiled_tf.html:92`.
- `recruit.html:103-104`: "1,284" in the sentence, "1012 of 1284" in the table.
- `ncaa.html:87`, `:132`.

**Times:** hundredths that were never recorded (`conversions.js:719-726` "16:25.26"). "~" and "≈" are mixed (`next-race.js:74`, `_wit_line.html:34`).

**Abbreviations:**
- "hum" (`race.html:72`), "Est. time", "Avg score", "Avg place".
- "Dist", "Min results", "Top-5 avg", "Gr", "Elem".
- "Precip", "temp", "sd".

**Dates and weeks:** formats from five different `strftime` patterns. "Week of", "WEEK OF", "Week ending". Weeks start on Friday on movers (`movers.html:174`) and end on Sunday on the tracker (`team_season.html:92`).

**"Show more" has seven versions:**
- `_records_macros.html:19` "Show 20 more ({x} left)", with the 20 hard-coded.
- `staged.js:62`, `:82` "Show" / "Show less".
- `search.html:160` "Load more".
- The rankings pager.
- `race.html` "All {n} teams".
- `projections.html:364` "All {x} athletes".
- `_capped.html:21` "See them all".

**Odd copy:**

| Where | Current | Use |
|---|---|---|
| `school.html:94` | "Subscribe in your calendar app: this season's meets this team ran last time, posted again" | "Add this team's likely meets to your calendar" |
| `school.html:259-263` | "until they turn out for someone else" | "Not raced yet this season; showing last season's numbers" |
| `account.html:133-134` | "Coaching yourself — An athlete entering their own races." | "I coach myself" |
| `account.html:109` | "for now a team claim is a statement" | "Coach status isn't verified yet" |

### I. Page titles and descriptions

**Separators:**
- " | Racecast" on most pages.
- " - Racecast" on `404.html:19`, `search.html:42` and `embed_school.html:13`.
- Athletes use an em dash (`seo.py:347`).
- " - " on `compiled.html:34` "… - Compiled", `compiled_tf.html` and `school_prs.html:21`.
- " · " on `recruit.html:13` and `recruiting_school.html`.

**Title Case titles:**
- `coaches.html:20` "Coaching Tools"
- `conversions.html:11` "Cross Country & Track Time Conversions"
- `course.html:12` "…Cross Country Course: Records & Difficulty"
- `home.html:142`
- `records_index.html:11` "All-Time … Records by State"
- `recruit.html:13` "Recruiting Profile"
- `recruiting.html:13` "College Recruiting: What It Takes" (this also collides with the What it takes page)
- `recruiting_search.html:13`
- `school_records.html` "Record Book"
- `record_pages.py:92`

**Weak titles:** "Rankings", "Predictions", "Settings". Also "Head to head: A vs B", "Rating to time: every pool", and `venue_tf.html:8`'s fallback "Venue {location_id}", which puts an id in the title. `projections.html:29` mixes a question with a noun phrase.

**Descriptions:**
- `recaps.html` "…the median miss and the top ten named".
- `meet_recap.html` "median miss {x}%, the biggest surprises".
- `course.html:17-18` "how much the course gives or takes, solved from athletes who raced both here and elsewhere".
- `compiled.html:36` "compiled together".
- `state_odds.html` "Model estimates of…".
- `athlete_profile.html:23` "programmes".

### J. Smaller notes

- `follow.js:45` "breaks out": site jargon. Use "has a big race".
- `alert_email.py:165`, `:209` "above the median of earlier races this season". Use "Best race in a while".
- `decision_email.py:100`, `fixes.html:49` "Fix not made" / "Not made". Use "Not changed".
- `upload_view.html:53` "Held: this looks like a meet Racecast already has". Use "On hold: we may already have this meet."
- `login.html:64-66` "accounts are for people 13 or older". Nothing tells parents of middle schoolers to make the account themselves and follow their child.
- `contact.html:74` "claim it from your athlete page". There is no claim link on athlete pages.
- `report.html:62` "Someone is on the wrong board (HS / MS / college)". "Board" here means level.
- `meets.html:139` and `:411-413` "real fields (+ results)", "cleared the results floor".
- **`embed_school.html:92` explains the rating best:** "100 = an average high school runner · ≈ the track 5K it is worth". Reuse it everywhere.
