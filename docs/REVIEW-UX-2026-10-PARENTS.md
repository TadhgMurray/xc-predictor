# Racecast for parents and runners: task walk-throughs (October 2026)

This is part of the October UX review. I worked through 15 tasks end to end on a 390 px phone, as a first-time visitor, using the real templates and JS with mocked data. The screenshots are in `ux-2026-10/walk-*.png`.

**Correction to the main review.** Its first version said the 5K column shows on sprints ("an 11.16 100 m reads 5K equiv 15:36"). That came from a test render with made-up ratings. In fact the engine doesn't rate track events under 600 m (`engine/normalize_distance.py:68-80`; `app.py:1956-1962` says "a sprinter's or a thrower's whole year, which the engine never rates"). So sprinters, hurdlers, jumpers and throwers get **no rating at all**, which is a bigger problem (task 10).

---

## 1. Landing from Google on your kid's athlete page

**What you see in the first 5 seconds:**
- sport tags;
- the name, plus Follow with two lines of help;
- school, grade and league chips;
- "ATHLETE RATING ⓘ · 2026 XC" at 9.5 px in uppercase;
- **123.0**, then "≈ 15:12 5K on a track" and "#212 of seniors nationally · top 3% · #9 in Oregon";
- Best race / Races / Seasons, then a "Ranked" line, a "State" line, 5 buttons and a "Recruiting" line.

The first race row is about 900 px down.

- **Nothing says whether 123.0 is good.**
  - The answer ("top 3%") is a 10.5 px grey sub-line.
  - The ⓘ is a link to /scale (`_explain.html:35-38`), so a tap on a phone may take you away from the page.
- **The ranks appear twice and can disagree.**
  - `_grade_rank.html` gives "#N of seniors nationally · #M in Oregon".
  - The "Ranked" line (`app.py:1785-1830`) gives "Nation #X · OR #Y · Team #Z".
  - One counts by grade and the other counts everyone, with no label saying so. You see two national ranks and two Oregon ranks.

<img src="ux-2026-10/walk-athlete-two-rank-lines.png" width="300">

- **The headline time is a track 5K for an XC runner.** "≈ 15:12 5K on a track" (`conversions.py:1399-1400`) is a race most XC kids never run. A parent who knows the 15:40s on courses reads it as wrong.
- **The "State" line mixes three number systems in one sentence.** "1.4 points (≈ 10 s over 5K) short of the OR Class 6A state podium mark: last year's top 8 needed 124.4 (≈ 14:50 on a track). Owen is at 123.0." (`_wit_line.html:35-44`)
- **The State line comes and goes.** `cuts.athleteLine` returns None when the state cache is cold and only warms it for next time (`cuts.py:1227-1231`). Because pages are edge-cached, a page cached cold can miss the line for the whole cache life.
- **"breaks out"** in the Follow help (`follow.js:45`) is jargon.
- **The SEO title always says "cross country & track"** (`seo.py:347-348`), even for an XC-only middle schooler or a track-only runner.
- **The search snippet leads with "Season rating 123.0"** (`seo.py:357-367`). The PR is the fact a searcher recognises.

**Ambitious fix: a "your runner" summary at the top.**
> "Owen is a senior at Jesuit. This season he's one of the top 10 boys in Oregon (top 3% nationally), running about 15:10 for 5K. His best race was District on Oct 23 (15:11.9, a PR)."

- Under it, three chips: Last race · PRs · State outlook.
- The rating moves to a small "123.0 rating ⓘ" chip.
- One rank line, labelled ("#9 of all Oregon boys · #3 of Oregon seniors").
- The same sentence becomes the search description and the share text.

## 2. "My kid raced Saturday — where are the results?" (meet name unknown)

**Path A, by name (the best path):**
1. Search and tap the result.
2. Swipe 2–3 screens past the header and the open chart.
3. Tap the meet in the first row.
4. The race page opens **mid-table, with no title and no column headers** (`race.html:241-248`).
5. Swipe up to find out where you are.

That is 3 taps, about 5 swipes and some typing.

- **Search requires every word to match exactly** (the search API near `app.py:8852`, "KEYWORDS, AND-ED").
  - There's no typo or nickname tolerance: "Jake" won't find "Jacob", and "Castelano" finds nothing.
  - Upload linking already handles nicknames (commit `679cb6a`). Trigram similarity is already indexed (`search_index.py:74-78`), so it could be the fallback when strict matching finds nothing.
- **Same-name kids look identical** in search.
- **The athlete page never says "Latest race".** It only has a "Next race" line, and only for XC (`athlete.html:458`).

**Path B, from the home page:**
- "Latest results" is about 2,200 px down on a phone, under the 25-row national board.
- It's a **national** list with no state filter, so a dual meet probably isn't there.
- The number column ("2,301") has no label; it's the count of results.
- "All recent meets →" opens 8 filter fields.
- **The School filter is an exact text match** on the stored school name (`meets_filter.py:205`, `WHERE r.school = %(school)s`), with no autocomplete. "Jesuit HS" or "Jesuit High School" returns nothing, and nothing says why.

**Path C, from the school page:** search the school → page → swipe past the ~880 px header → the Races tab (not the default) → the meet → the division → the race. That's 5–6 taps.

**Also:** the home board's header overlaps on a phone (the Rating ⓘ sits on top of "5K EQUIV").

<img src="ux-2026-10/walk-home-board-header-overlap.png" width="300">

**Fixes:**
- **A "Latest race" card on the athlete page:** "Sat Oct 18 · Clovis Invitational · 15:11.9 · 12th · PR", plus "See full results". It lands at the table header with the row lit.
- **"Results near you" on home:** the state is remembered from the last athlete or school viewed, plus a school finder with autocomplete.
- **Search that tolerates typos and nicknames.**

## 3. "Is my kid improving this season?"

- **The chart is a bare rating line.**
  - Its title is "XC SPEED RATING - HS-EQUIVALENT"; the axis runs 115–125.
  - Nothing says "higher = faster", and it has no PR markers and no times.
- **An XC time chart is computed but never drawn.**
  - `build_chart_data` builds `xc_pace` for every race (`athlete_chart_data.py:211`, `:235`, `:265`) and ships it in the page JSON.
  - No chart uses it: nothing in `athlete.html` or `athlete-charts.js`. It's bytes for nothing.
- **Tapping a chart dot on a phone jumps away.** Hover and click both fire on touch, and the click scrolls to the race row (`athlete-charts.js:603-614`). You can't read a dot without leaving the chart.
- **Comparing seasons is mental arithmetic.** "2026 XC 123.0 ≈ 15:00" and "2025 XC 118.0 ≈ 15:38" are two headings about 600 px apart. Nothing says "up 5.0 from last season, about 38 s faster over 5K".
- **Same-course comparisons are hidden**: a CR badge, but no "vs last year at this course".
- **The season bests come last on a phone.**
- **Excluded races get three harsh labels**: "not counted", "not ranked" and "out".
- **The board notes use jargon**: "rated as normal against their believed pool".
- **Ratings change after a recompute and the athlete page never says so.** A parent sees 123.9 become 122.7 overnight. The explanation is site-wide only (`public_changes.py`).

**Fixes:**
- **A season story above each season:**
  > "Up 5.0 from last fall (≈ 38 s faster over 5K). Best race: District (PR). Biggest jump: Metro → District, +2.5."

  On phones it's the default view, with the table under it.
- **A chart that answers the question:**
  - time on a typical course as the axis (the `xc_pace` data already exists);
  - PR stars;
  - a faint line for last season;
  - tapping a dot opens a sheet and does not navigate.
- **A 30-day note after a recompute:** "Ratings were recalculated on Oct 22; Owen's season moved from 122.6 to 123.0."

## 4. "How does my kid compare to teammates and the state?"

- **"Team #1" goes to the school roster with nothing lit** (`app.py:1822-1829`), below an ~880 px header.
- **"National #212" opens page 1 of the board (ranks 1–50) with nothing lit.**
  - `app.py:1631-1647` builds the link without the person.
  - The board already has `jumpTo(personId)`, which finds the right page and lights the row (`rankings.js:2844-2866`), but it's only wired to the find box.
- **Compare opens with box B empty**: no placeholder and no suggestions (`compare.html:73-82`).
- **"Similar runners" is a recruiting statistic**, not "kids like yours in your state".

**Fixes:**
- Every rank deep-links with `&find=<id>` and calls `jumpTo` on load.
- A "Team" panel on the athlete page: "3rd of 14 on Jesuit · 1.2 points (≈ 9 s) behind #2 Theo · 4.0 ahead of #7".
- Compare suggests teammates, the 3 runners rated just above and below in the state, and the rivals they've raced most.

## 5. "What does my kid need for state, the varsity 7, or a goal time?"

- **The athlete "State" line is good but jargony**, HS-only, and drops out when its cache is cold.
- **/goal ("What do I need to run?") is the best answer page on the site, but:**
  - It's **HS cross country only**: the tag reads "High school {{gender}} cross country". No track goals ("what 1600 makes state?") and no MS goals.
  - Its main button is styled as a secondary link (`goal.html:112`).
  - The Tools menu calls it "Goal calculator".
- **Nothing answers "make the varsity 7"**, though the team ranks exist.
- **The times mix references**: "on a track" next to an XC state course.

**Fix: a "Goals" card on the athlete page with three rows**, each giving the gap in **seconds** and "about N s/mi":
- Varsity 7: "you're 9th; #7 is 0.8 points (≈ 6 s) ahead"
- State qualify: ≈ 12 s
- State podium: ≈ 10 s

Add track goals by event, and MS goals ("top 10 at the city meet").

## 6. "Could my kid run in college, and where?"

**Path:** College fit → `/recruiting?athlete=ID`. The default tab is "Every college", where **You** is column 10 of 10, off-screen on a phone.

- **The grouping is explained in jargon**: "Bands are quartiles of each school's own recent recruits, fastest programmes first…"
- **"N schools, fastest 3 shown"** has no way to see the rest.
- **Two different 5K figures** on the subject card: "PRs 15:11.9" and "Rating equivalents 15:12".
- **Saving a school only works from that college's own page** (`recruiting_school.html:151-152`). The shortlist is only reachable after saving, or from the account pages (`my_page.html:53`, `account.html:33`).
- **No next step** after "Solid recruit": no coach contact, questionnaire, roster size or conference.
- **Speed is the only fit measure**, and the page doesn't say so.
- **College fit is offered to unrated athletes** (sprinters, throwers). The buttons check only `team_pool` (`athlete.html:435-441`), and the page then has nothing to place them with.

**Fix: "College options" in plain words.**
> "Owen's times fit the typical recruit at 31 programs: 9 DI, 12 DII, 10 DIII."

- Group by division and region, with a map.
- Each school shows its typical recruit's 5K and **"you'd be about their 3rd runner"**.
- A Save star on every row.
- One tap for a coach-ready summary.

## 7. "Who wins this weekend or state, and where will my kid place?"

- **The athlete's "Next race" line**, from `next-race.js` (lines 1-6, 60-66):
  - It reads "Clovis Invite, Sat Oct 18: predicted ~15:02, about 12th of 180 · … · course +2.1% · forecast 14°C, sunny".
  - **The weather is in Celsius, km/h and mm** (`forecast.py:220-238`, used by `upcoming_preview.py:443`), while race pages use °F and mph. The rating explainer's hourly strip is also °C (`rating-explain.js:305`, `:312`).
  - **It appears for XC only.**
- **The meet preview it links to can't find your kid.** It has no find box (`meet_preview.html`) and no highlight, and the list can be the whole field.
- **"Edit the field →"** goes to the predictions setup screen.
- **Predictions from a meet** opens a setup screen, not a prediction.
- **The state odds line on athlete pages is good**, but the state odds page says "Model estimates, not predictions" inside Predictions.

**Fixes:**
- Arriving from Next race, the preview opens with "Owen: about 12th (range 8th–18th), ~15:02" pinned at the top, and his row lit.
- All weather in °F and mph.
- A Next race line for track too.

## 8. "A result is wrong, my kid is missing, or two kids are merged"

- **"Report it →"**:
  - It sits at the bottom of every page.
  - On athlete pages it preselects "A rating looks wrong" (`athlete.html:574`, `kind=rating`), even for a merge.
  - It's free text, and the page is shown as a raw path.
- **"Suggest a fix"** has structured kinds: grade, school, not_mine, missing, same_person (`fixes.py:71`).
  - It appears **only to a signed-in claimant** (`_suggest_fix.html`).
  - Claiming lives in Settings ("Type your name and pick yourself", `account.html:84-100`), is worded for the athlete, and is unverified.
  - So the best fixing tool sits behind a step that's hard to find.
- **"Add missing results"** accepts .txt, .htm, .csv or a link (`uploads.html:59-65`).
  - **No PDF and no photo**, which is what most parents have.
  - The Report form's "A result or meet is missing" choice doesn't point to it; only the Contact page does (`contact.html:73`).
- **There is no way to ask for a minor's page to be hidden.**
  - The backend supports it (`person_optout.py`).
  - Contact topics are question, data, press, help and other (`app.py:12752-12753`).
  - About's privacy section covers analytics only (`about.html:215-220`).

**Fixes:**
- **A ⋯ menu on each race row of an athlete's table**: "Not my kid's race", "Wrong time", "Wrong grade", "This is two people". It pre-fills the structured fix, and a visitor who isn't signed in can send it with an email address.
- **The Report form branches by kind:**
  - "missing" goes straight to uploads;
  - "merged" asks "which races aren't theirs?" with checkboxes.
- **Accept PDFs and photos** (queue them for manual handling or OCR), or at least say "Have a PDF? Paste the link."
- **A clear "Hide my child's page" request**, linked from About, the athlete page footer and Contact.

## 9. "Tell me when my kid races"

1. Tap Follow while signed out.
2. Log in with Google, or with an email link that works once and expires in 15 minutes, plus a 13+ checkbox.
3. You come back **not following** (`follows.py:429`, "Changes nothing else").
4. Tap Follow again.

That's 4–5 steps plus an email round-trip, and the second tap is never explained.

- **The login page's "What an account does" never mentions following.** It says an account "**places you on the recruiting boards**" (`login.html`), which alarms a parent of a minor.
- **The avatar goes to Settings**, not My page.
- **"Following. Click to stop."** (`follow.js:132`): it says "click" on a phone, and one tap unfollows with no undo.
- **No choice of how often to be emailed** (each race / weekly / PRs only) at the moment you follow.

**Fixes:**
- **Follow without an account:** "Email me when Owen races", with an email box right there. Confirm by link; no account needed until the parent wants one.
- **After sign-in, finish the follow** and say "You'll get an email after each of Owen's races. Change."
- **The login page explains accounts:** "Follow runners, claim your page or your child's, save colleges."

## 10. Pages that aren't HS boys cross country

**Sprinters, hurdlers, jumpers and throwers (unrated):**
- The header shows "ATHLETE RATING ⓘ · 2026 Track" over a bare "–".
- The "Track speed rating" chart is **open by default and empty** (`athlete.html:525`).
- Every Rating and 5K cell is "–".
- College fit and Similar runners are still offered.
- "LJ" is unexplained, and sprints and jumps show no wind.
- The badge legend lists CR, R and RSR, which never apply.
- There's no Next race line.
- **Nothing says ratings cover 600 m and up**, while home says "Every result on one comparable scale" (`home.html:195`, `_meta.html:41`).
- **Fix:**
  - Say so plainly: "Ratings cover races from 600 m up. Sprints and field events are shown as results and PRs."
  - Hide the rating stat, chart and column when a season has no ratings.
  - Lead with PRs by event, with wind.
  - Draw a season-best chart per event; the time panes already exist.

<img src="ux-2026-10/walk-sprinter-unrated.png" width="300">

**Middle school:**
- MS is **missing from home**: the boards are HS and college only (`home.html:163`), and "Rankings by state" has no MS (`home.html:349`).
- **MS ratings sit below 100 on a "100 = average high schooler" scale** (`_explain.html:36`). A 7th grader at 85 reads as weak.
- The chart says "HS-EQUIVALENT", and the SEO title says "cross country & track".
- Accounts are 13+ (`login.html`), so the parent holds the account, and nothing says so.
- **Fix:** lead MS pages with their MS standing ("top 18% of 7th-grade girls in Oregon"), with the rating second; add MS to home.

**College:**
- The grade shows TFRRS codes such as "SO-2" (`grade_label.py:31-45`).
- An 8K runner's "Best race" sub-line shows a 5K equivalent, while the header is meant to use 8K (`app.py:2355-2373`).

**Girls:**
- **Every default is boys:**
  - rankings `hs_m` (`rankings.js:2225`);
  - home `hs_m` (`home.html:250`);
  - /goal falls back to boys (`goal.py:211`);
  - recruiting opens on Men;
  - course pages list boys first.
- So a girl's parent always has one more tap.
- **The rating tooltip says "an average high-school runner"** without "of the same sex" (`_explain.html:36`). A girl's 110 and a boy's 110 are different times.
- **Fix:** remember the last level picked, or infer it from the last athlete viewed. Say "an average high-school girl" on girls' pages.

## 11. Sharing a result with grandma

- The share preview card shows **RATING 123.0 · BEST RACE 123.9 · RACES 16** (`cards.py:524-528`). It has no time, no PR and no latest result.
- The share text is just "Owen Castellano on Racecast".
- **Sharing a race loses your kid.**
  - The race page's share URL drops `?r=` (`race.html:61`), so grandma lands at the top of a 250-row race.
  - If you copy the address bar instead, she lands mid-table with no header.
- There's no way to share one result.

**Fix: "Share this race" on each athlete row.**
- The card reads "Owen Castellano · 15:11.9 PR · 3rd · OSAA District · Oct 23", and that sentence is the share text.
- The link opens the race with the row lit **and** the header showing.
- The athlete card leads with PRs and the latest result, not three ratings.

## 12–15. More tasks

- **"What paces should I train at?"**
  - From a typed time it dead-ends: "Pick an athlete as the source…", with no picker there.
  - The paces are described with "critical speed" and "VO2max".
  - Fix: paces on the athlete page, e.g. "Easy 6:40–7:10/mi · Tempo 5:25 · Intervals 4:58".
- **"Why is a faster time rated lower?"** The explainer sheet exists, but its words are technical.
  - Fix: one sentence comparing the two races. Show it when a row's rating order differs from its time order.
- **"What do these badges mean?"**
  - The legend sits below the table, off-screen on a phone, and the badge tooltips get clipped.
  - Fix: tapping a badge opens the bottom sheet.
- **"First visit: what is this site?"**
  - On a phone: photos, then search, then the hero sentence, and no "Find your runner" path besides search.
  - Fix: a first screen that is one box, "Find your runner or school", plus "See this weekend's results in [state]".

## Small things
- The "Races" and "Seasons" stats take a row of the athlete header and answer no parent's question.
- "Best race" is a rating link; a parent expects "Best: 15:11.9".
- The login page says "The reason is in the server log".
