# Site survey — 2026-09-02

What is obscure on Racecast today, what is missing, and what to change,
read against who uses a site like this and what the sites they already use
do. Written from the code and templates, not from a browser; anything about
the live look is from the owner's screenshots today.

---

## 1. Who comes to a site like this, and for what

| who | the first thing they do | what keeps them |
|---|---|---|
| **an athlete** | search their own name | a number that means something, PRs, "where do I rank", a page to share |
| **a parent** | search the athlete | the same, plus "is this good for a sophomore" |
| **a coach** | their school page | roster depth, who to run where, what a meet will look like, recruiting-style lists of nearby athletes |
| **a fan / message-board reader** | rankings, a meet | arguing: who is better, which course is harder, would X beat Y |
| **a college recruiter** | state or event lists | filters by grade and event, converted marks, contact context |
| **a meet director** | their meet | results correctness, course difficulty, records at the venue |

The distinctive thing Racecast has that none of the incumbents has for high
school is **one comparable number across courses, distances, sports and
years**, with the course difficulty solved from the whole corpus. Everything
below is about making that number legible and putting it where these people
already look.

## 2. What the comparable sites do

- **Athletic.net** — the canonical result store for HS XC and track. Athlete
  pages with season tabs and PRs, team pages with rosters and meet lists,
  meet pages with every division, "top times" lists by state, grade and
  event, season-best strips. Free, ubiquitous, no cross-course
  normalisation. Its athlete page layout is what every user already knows:
  header, PR strip, seasons newest first, each season a table.
- **MileSplit** — rankings by raw time at state and national level, virtual
  meets, freshman/sophomore lists, recruiting profiles, news and video
  behind a paywall. Some states carry Tully-style speed ratings. The
  *virtual meet* and the *grade lists* are its stickiest tools.
- **TFRRS** — college. Performance lists by event, division and region,
  qualifying lists, USTFCCCA team rankings. Clean, dense, event-first.
- **XCStats** — a coach's tool (California-centred): team scoring
  projections, course-adjusted times, season progress charts, the "if we
  ran this meet" question answered per team. Paid, per school.
- **TullyRunners** — the original speed ratings (NY). Plain lists, an
  explanation page people still cite. Proves that a rating with a clear
  one-paragraph definition earns trust.
- **DyeStat / RunnerSpace** — elite lists, video, news. Not a competitor
  for the daily lookups.

What Racecast does that none of them do for HS: the solved difficulty, the
XC/track bridge, the era correction, the head-to-head compare, the
predictions plumbing, and a rating for *every* result rather than a curated
list.

## 3. What is obscure today

Things a first-time visitor cannot decode without reading About.

1. **The five board names.** *Ability / Performances / Best times/marks /
   Teams / Courses.* "Ability" is the season average and is the default;
   nothing on the tab says so. Each board has a one-line subtitle once
   selected, but the tabs themselves do not say what differs.
2. **HS-equivalent vs Own pool.** The single most confusing control on the
   site. A reader who has not read About sees two numbers for one race
   with no idea which to quote. The toggle is now on every athlete page
   (owner's call) but its explanation is a "?" tooltip.
3. **Pool codes.** `hs_m`, `college_f` appear in URLs and in some tables;
   the words exist (`POOL_LABEL`) but not everywhere.
4. **Difficulty.** A signed percentage with no anchor word beside it on
   most tables ("+4.3%" of what?). The Courses board adds "harder"; the
   athlete table does not. The About page's paragraph still describes the
   old per-sport zero.
5. **The record badges** — CR, CSR, RSR, SR, PR — five acronyms in a column
   headed "PR/SR Flags", explained only on hover.
6. **The "Ranked" line.** "Nation #6 · NCAA DI #5 · South Central #3 ·
   SEC #3 · AR #3 · Team #14". Good information, but the units (section,
   area, division, class) are never introduced, and the rank is at "min
   races 1" while the board defaults to 3, so the number on the board can
   differ from the number on the line.
7. **"Compiled" races.** A merged result for divisions that raced apart is
   a Racecast invention; the meet page lists them above the real divisions
   with no sentence saying what they are until you open one.
8. **"View the other meet".** The anet/tfrrs id collision surfaces as a
   link a visitor cannot interpret.
9. **The min-races box.** A number field with no unit and, until today,
   a default that changed with the sport.
10. **What a rating is worth in seconds.** "33% faster than average" is
    the definition, but people think in times. Nothing converts a rating
    back into "about a 16:20 5K for a high-school boy".

## 4. What to add

Ordered by value to the people in §1 against the work.

1. **A rating-to-time line everywhere a rating leads.** "146 ≈ 15:05
   5K-equivalent (HS boys)". One function, the pool's anchor time and the
   rating; put it in the athlete header and the explainer. Answers the
   question every parent has.
2. **Grade lists.** Freshman / sophomore / junior / senior boards are the
   most-shared pages on the incumbents. The ability board has a grade
   filter; make them one click: "Top freshmen, CA, boys".
3. **A plain-language glossary section on About** covering the five
   boards, the two scales, the badges, the units (league, area, section,
   division, class), compiled races, and the twin-meet link. Link the
   "i" and "?" marks to it. One page, not more tooltips.
4. **"Where am I" surfaced.** The API already finds an athlete's row on any
   board (`/api/rankings/rank`). A search box on the rankings page that
   scrolls to the athlete is the feature people ask for first.
5. **The virtual meet, from the school page.** The predictions plumbing
   exists; the entry point is a tab nobody finds. "Race these teams" from
   a school page, and "re-run this meet with team X added" from a meet
   page, are the coach's two questions.
6. **Season trajectory on the school page** (mean of top-7 by meet) and the
   PR-progression chart on the athlete page. Both are in the owner's own
   list (SITE_NOTES) and both are what XCStats charges for.
7. **Course pages with a map and "records here"** — the course page has the
   records; a map from the GPS the corpus already carries is cheap and is
   what makes a course page feel like a place.
8. **Share and permalink hygiene.** Every board state round-trips through
   the URL already; add a "copy link" and make the athlete page's season
   anchors (#2026-TF) visible so a coach can link a season.
9. **Percentiles.** "146 · 99th percentile of HS boys" beside the rating.
   Cheap (the pool distribution is known nightly) and it is what "is this
   good" means to a parent.
10. **Entries / upcoming meets** — the strip the owner mocked. Needs a data
    source the feeds do not carry; park it, but it is the one thing that
    would make people come *before* a meet rather than after.

## 5. What to change

1. **Rename the boards in the tabs**: *Athletes (season average)*,
   *Performances (single races)*, *Best times/marks*, *Teams*, *Courses*.
   The parenthetical is the explanation; the subtitle can go.
2. **Pick a default scale and say it once.** HS-equivalent is the default;
   put "(HS scale)" in the column header on pool-mixed boards and drop the
   toggle from pages that only have one pool (owner overruled this today;
   keeping the control everywhere, so at least make the header say which
   scale is showing).
3. **Words for pools, always.** No `hs_m` on any page or in any label the
   user reads; the URL can keep it.
4. **Difficulty with its sense word on every table**, the way the Courses
   board does it: "+4.3% harder". And update About's difficulty paragraph
   to the one-scale definition.
5. **Badges: spell them once per table.** A legend line under a table that
   has any badge ("PR personal record · SR season record · CR course
   record · CSR course season record · RSR ..."), instead of five hover
   targets.
6. **The Ranked line and the board agree.** Rank at the board's default
   floor (3), or say "(any number of races)" on the line.
7. **Home page: search first.** The home page is boards; the incumbents
   are a search box. Put the search box in the hero, boards below.
8. **Mobile.** Issue 88 is reopened and untouched; the athlete table now
   scrolls the page rather than squashing, which is a fix for one table.
   The rankings filters and the predictions page have not been looked at
   on a phone at all.
9. **Names.** "Compiled" → "All divisions merged"; "Own pool" → "Own age
   group"; "Ability" → "Season average". The engine's words are precise;
   the site's should be ordinary.
10. **About's numbers.** The page states corpus figures and two difficulty
    means that today's changes made stale; it should read them from the
    pipeline (`homepage_meta` already feeds part of it) or say when they
    were measured.

## 6. What not to do

- No paywall or accounts before there is traffic; the incumbents' users
  leave at the first login wall.
- No forum until there is something to argue about; off-the-shelf when it
  comes (SITE_NOTES has the reasoning).
- No more tooltip-only explanations. Each of today's explainer rebuilds
  was a tooltip; the fix is a glossary.

## 7. Open item from today

The "undefined" race count on the rankings page's first render. Both API
paths return `n_races`, an empty box sends the default, and the cell is now
tolerant, but the cause is not found. The page URL at the moment it shows,
and whether the address bar carried `min_races=`, would settle it.
