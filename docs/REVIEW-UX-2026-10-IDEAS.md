# Racecast: bigger ideas (October 2026)

This is the companion to `REVIEW-UX-2026-10.md`. The review lists what is wrong today; this file is about what the site could become. It covers research on what parents, runners and coaches already use, what Racecast already has but hides, the ideas themselves, and a proposed site map. Nothing here is ranked.

## The core argument

Racecast is not short of features. It is short on **packaging**. The repo already has:
- follows with an email digest (`follows.py`, `follow_alerts.py`);
- a "My page" that predicts each followed runner's next meet (`my_page.py`);
- share cards for about 10 page types (`cards.py`, `/card/*`);
- weekly Instagram posts (`social.py`);
- forecast recaps (`meet_recap.py`);
- a public accuracy scorecard (`accuracy.py`) and a changelog (`public_changes.py`);
- "runners like you" (`comps.py`), goals (`goal.py`), state odds (`state_odds.py`), a team season tracker (`team_tracker.py`), next-race previews (`upcoming_preview.py`) and a home-screen manifest (`app.py:877`).

All of it sits behind about 88 templates built for someone who already knows the site, and several of the best parts are off or buried:
- The follow digest only sends when `XCP_ALERTS=1` (and weeknights only with `XCP_NIGHTLY_ALERTS=1`; `deploy/nightly_update.sh:324`).
- A signed-out Follow never completes (`follows.py:429`).
- My page is reachable only through the avatar, and the avatar goes to Settings.
- Goals are item 8 in the Tools menu.

Competitors own the raw results and live timing. Athletic.net owns AthleticLIVE and has charged for rankings since 2018; MileSplit paywalls profiles and in-depth rankings. Racecast can't win at publishing results first, and its own notes say it shouldn't scrape live timers (`docs/TIMING-COMPANIES.md`).

What Racecast can win is the **meaning of a result**: one comparable number, what a time is worth, what it takes, and what happens next. So the ambitious move is to rebuild the site around three people (parent, runner, coach) and three questions:

1. **How did my kid do?**
2. **How good is that?**
3. **What's next?**

Every page should open with a sentence that answers one of them.

---

## What others do, and what to borrow

### Results sites parents already use
- **Athletic.net vs MileSplit** varies by state; Ohio's governing body uses MileSplit as its official results source. Both get complaints about wrong marks ([LetsRun](https://www.letsrun.com/forum/flat_read.php?thread=11243683), [LetsRun](https://www.letsrun.com/forum/flat_read.php?thread=6705450)).
  - **Borrow:** be the companion, not the replacement. Paste an Athletic.net/MileSplit/TFRRS URL into search and land on the Racecast page. Credit the source on every race.
- **Paywalls**: Athletic.net rankings since 2018 ([LetsRun](https://www.letsrun.com/forum/flat_read.php?thread=8923983)); MileSplit network-wide, with profiles and in-depth rankings paid ([NJ MileSplit](https://nj.milesplit.com/articles/109962/entire-milesplit-network-going-to-subscriptions), [IA MileSplit](https://ia.milesplit.com/articles/112494/subscriptions-but-even-greater-benefits-updated)).
  - **Borrow:** say "free" plainly on landing pages and in search snippets.
- **Athletic.net following**: a star on every list, a follow button on athlete, team and meet pages, a star on the live display, and app notifications. Email exists only per meet on AthleticLIVE ([support](https://support.athletic.net/article/8h387cc4dr-following-and-connecting-with-teams-and-athletes), [AthleticLIVE](https://support.athletic.net/article/ndscjlv6gc-what-is-athletic-live)).
  - **Borrow:** a follow star on every list row. Racecast's per-runner email digest is something they don't have.
- **Athletic.net college coaches**: followed recruits are pinned to the top of a live meet dashboard ([support](https://support.athletic.net/article/703rqk3x4s-athlete-tracking-for-college-coaches)).
  - **Borrow:** "your followed runners in this race" at the top of every race page.
- **MileSplit virtual meets**: start from a previous meet, from scratch, or from an upcoming meet. Users can edit times, add a freshman, scratch the injured, and set scoring and entry limits ([NJ](https://nj.milesplit.com/articles/201484), [CO](https://co.milesplit.com/articles/131436/doing-a-virtual-meet)).
  - **Borrow:** the edit, add and scratch workflow on top of Racecast's course-normalised predictions.

### Explaining a rating
- **Tully speed ratings** are summarised as "roughly one point per three seconds". Their published list ranks a 14:54 above a 14:48.7 without saying why ([MileSplit](https://www.milesplit.com/articles/247287)).
  - **Lesson:** every rating needs its time beside it and a one-line reason.
- **Beyer speed figures**: one number, higher is better, and a **par** for each class level ([ABR](https://www.americasbestracing.net/node/40479)).
  - **Borrow:** state pars ("what a state qualifier runs"). `cuts.py` already has them.
- **FloRatings and Georgia meet ratings** describe courses in **seconds** against a baseline course ([Flo](https://www.flotrack.org/articles/5028991-flotrack-presents-floratings), [GA](https://ga.milesplit.com/articles/325752/introducing-meet-speed-ratings-for-georgia-xc-courses)).
  - **Lesson:** parents think in seconds, not percentages.
- **VDOT** names 10 levels (Beginner to Professional), and one time in gives three answers out: race equivalents, training paces and equivalent performances ([App Store](https://apps.apple.com/us/app/vdot-running-calculator/id973571014)).
  - **Borrow:** named bands, and a single calculator that returns all three.
- **Chess.com** shows "better than X% of active members" ([help](https://support.chess.com/article/857-what-does-percentile-mean)). **Lichess** flags provisional ratings for new players ([Lichess](https://lichess.org/page/rating-systems)).
  - **Borrow:** "faster than 97% of Oregon boys racing this season", plus a "still settling" mark for runners with only 1 or 2 races.
- **FBref** scouting reports show percentile bars against positional peers, in the same layout for every player ([FBref](https://fbref.com/en/about/scouting-reports-explained)). **Baseball-Reference** lists similar players through age X ([blog](https://www.baseball-reference.com/blog/archives/3611.html)).
  - **Borrow:** both, for the recruiting profile. `comps.py` already has the data.
- **Garmin's race predictor** is distrusted when it ignores the races people actually ran ([the5krunner](https://the5krunner.com/garmin-features/performance/race-predictor/), [forum](https://forums.garmin.com/sports-fitness/running-multisport/f/forerunner-255-series/370836/race-predictor-broken)).
  - **Lesson:** every prediction says which races it rests on and how often it has been right. `accuracy.py` has the numbers.
- **FiveThirtyEight** published its methodology and ran a "pick against our model" game ([game](https://fivethirtyeight.com/methodology/how-to-play-our-nfl-forecasting-game)).

### Following, notifications and parents
- **GameChanger**: a free team follow, and alerts opted into per team ([gc.com](https://gc.com/app-features/scorekeeping)).
- **MaxPreps**: athletes and parents claim and customise profiles, with alerts for favourite teams and athletes ([MaxPreps](https://www.maxpreps.com/getting-started/)).
  - **Borrow:** a "Claim this page (athlete or parent)" button on the page itself.
- **Strava Year in Sport**: a season told in chapters, with a share image for each one ([help](https://support.strava.com/hc/en-us/articles/22067973274509-Your-Year-in-Sport), [case study](https://manualcreative.com/work/strava-year-in-sport-2021)).
- **Phone navigation**: a bottom tab bar beats a hamburger menu for engagement ([Waikato study](https://researchcommons.waikato.ac.nz/entities/publication/3fa280bc-9c78-4449-b83c-6a39e93ee191), [SEW](https://searchenginewatch.com/?p=1748)).

---

## Built but hidden

| Feature | Code | Visibility today |
|---|---|---|
| Follow + email digest | `follows.py`, `follow_alerts.py` | sends only with `XCP_ALERTS=1`; a signed-out Follow never completes |
| My page: ratings, rank moves, progress to the state marks, next meet with a prediction | `my_page.py` | reached only through the avatar, which goes to Settings |
| Next-race preview ("Clovis Sat: ~15:02, about 12th") | `upcoming_preview.py` | a line on athlete and school pages |
| Share cards (about 10 page types) | `cards.py` | only seen as link previews; no "download this card" |
| Weekly Instagram posts | `social.py` | owner-only admin |
| "This week in <state>" | `movers.py` | nothing links to it |
| Runners like you, and what they became | `comps.py`, `recruit.html` | labelled "Similar runners"; leads with statistics |
| Goal planner | `goal.py` | Tools → "Goal calculator" |
| Team season tracker | `team_tracker.py` | a link on the school page |
| Accuracy scorecard + changelog | `accuracy.py`, `public_changes.py` | About page, What's new |
| Head-to-head prediction | `h2h.py` | Compare, 4th tab (cut off on phones) |
| Opt-out | `person_optout.py` | only by script (`scripts/optout.py`) |
| Home-screen manifest | `app.py:877` | no service worker, no push notifications |

---

## The ideas

Effort: S = days, M = 1–2 weeks, L = more.

### A. "My runners" becomes the home page for parents and runners
When a signed-in reader follows or has claimed someone, `/` shows their runners. Signed out, it shows "Follow your runner" plus search. `my_page.py` already computes most of this.

```
┌ My runners ─────────────────────────────┐
│ Owen Castellano · Jesuit · Gr 12        │
│ Last: Districts, Oct 23 — 15:11.9 (PR)  │
│ This season ≈ 15:12 5K · #9 in Oregon   │
│ ▓▓▓▓▓▓▓▓▓░  14 s from 6A top-8 pace     │
│ Next: State, Nov 4 · ~15:05, about 6th  │
│ [See races]  [Share]                    │
└─────────────────────────────────────────┘
+ Follow another runner
```
M · low risk. Depends on Follow completing after sign-in and the digest being on.

### B. A first-visit strip for people arriving from Google
Athlete pages are about 85% of search impressions (per the SEO note in `athlete.html`). Show one dismissible line: "New here? Owen's 123 means about a 15:12 5K on a flat course, faster than 97% of Oregon boys. [How ratings work] [Follow Owen: email when he races]". It is drawn by JS from a cookie, so the page stays cacheable. S · low.

### C. A rating everyone understands: time first, a ladder, named bands
1. Every rating appears with its time ("123 · ≈15:12"), or with the time first.
2. A **ladder** built from the reader's own state marks (`cuts.py`):
```
 Oregon 6A boys   ·state·top25·top8·win
 ─────────┼──────┼─────┼────●┼──────
                             ▲ Owen 123
```
3. **Named bands** in words ("Varsity", "State qualifier", "All-state", "National-class"), with cutoffs taken from real percentiles.
4. **A percentile against active peers**, and a "still settling" mark for runners with fewer than 3 races.

It goes in the athlete header, the race-row sheet and My runners. M · low–medium (bands need care for MS and college).

### D. A season story, then a season recap
- 2–4 sentences at the top of each athlete page, in the style of `race_story.py`: "Owen opened at 15:34, broke 15:15 at Metro, ran his best race at Districts (15:11.9). He's 9th in Oregon and on pace for a state top-10."
- At the end of the season, a Strava-style recap with a share card per chapter.

M · low.

### E. Goals live on the athlete page
A card: "Next goal: Oregon 6A top 8 → ≈ 15:02 at Lane CC (9 s faster)". Tap it to change the goal (state, top 25, a college's typical recruit). It shows the training paces for that goal and a progress bar across the season, and the digest says "4 s closer this week". This merges three Tools items into one weekly habit. M · low.

### F. Meet weekend: preview Friday, recap Sunday
For each followed runner and team:
- **Friday:** the meet, course, forecast weather, predicted time and place, predicted team place.
- **Saturday:** the meet page in an "upcoming" state.
- **Sunday:** "How it went": time against the prediction, rating, PR, team place against the forecast. Positive framing only, for minors.

`weekend.py`, `upcoming_preview.py`, `meet_forecast`/`meet_recap` and the digest all exist. Be honest that the field is a guess ("if Jesuit runs"). For speed, use coach uploads as a fast lane, never AthleticLIVE or PTTiming. M · medium.

### G. Team dashboard and lineup tool for coaches
The roster with season ratings and times, the 1–5 split **in seconds**, who is trending up or down, and projected state place week by week (`team_tracker.py`). **"Pick your seven"**: drag runners in and out, scratch, add a freshman at a guessed time, choose the next meet, and see projected scores against the expected field. This is MileSplit's virtual meet, free and course-normalised.
```
Next: Metro Champs (Sat) — projected 2nd, 61 pts (Central Catholic 48)
 #  Runner        rating  ≈5K    pred pl   ☐ scratch
 1  Castellano    123.0  15:12     4
 7  Nguyen        112.4  16:38    41      ← swap with Patel (+3 pts)
[Export entries CSV]  [Share preview with team]
```
`predict.predictTeam`, `projections.scoreField` and the coach role exist. M–L · medium (team claims need verifying).

### H. One recruiting "scouting report"
Merge `/athlete/<id>/profile` and `/recruit/<id>` into one fixed-layout, printable card with:
- percentile bars against same-grade peers;
- **named** comparison runners (adults or college runners only), e.g. "most like A. Smith ('24, now at Portland, 14:41 as a senior)";
- college fit as a few pills;
- "Send to a coach" and a PDF.

For coaches, shortlist + follow + digest becomes "new races by your recruits this week". M · low–medium.

### I. "Beat the model"
Before state and the big invitationals, users pick the top 3 individuals and teams, scored against the stored forecast, with a weekly leaderboard per state. `meet_forecast` is stored before the race and `meet_recap` scores it. M · medium (moderation; no betting language).

### J. Digests on by default, plus a state newsletter
A follow means an email unless turned off. Add "This week in Oregon" built from `movers.digest`, breakouts and the posts already made for Instagram, and a coach version, "your team this week". S–M · medium (deliverability; the unsubscribe flow already exists).

### K. Share moments for users
A Share button beside every PR, breakout and season best, opening the card with a ready-made caption. Coaches get the `social.py` templates as "make a post for your team". S–M · low.

### L. Trust built into every page
- On every race: "Source: Athletic.net (meet 123456) · imported Oct 18 · rated with the Oct 22 model · what changed".
- Each prediction page states its measured typical miss.
- A public log of corrections.
- One-tap "Is this wrong?" on every row.

S–M · low. This targets the "wrong marks" complaints both incumbents get.

### M. Search becomes the way around the site
Search accepts:
- a name, shown with class year and last race (to tell same-name runners apart);
- a pasted Athletic.net, MileSplit or TFRRS link;
- a time ("16:05 5k" → the conversion inline);
- "jesuit boys" → the team;
- "oregon state meet" → the projection or results.

M · low.

### N. Make it feel like a phone app, without an app store
- A real PWA: the service worker caches the site shell and the last 20 pages, which helps at meets with bad signal.
- A bottom tab bar on phones: **Home · Search · Rankings · Meets · Me**.
- Web Push for follow alerts (iOS 16.4+ for home-screen web apps).
- Offer "Add to Home Screen" after someone follows a runner.

M · low–medium.

### O. Simple by default, deep on request, with no mode switch
Every row and every number opens a details level in place: course and conditions in words, records, "how this was rated", splits. Coaches can choose "always show details" in their account. This keeps the one-scale rule. M · low.

### P. A performance budget, checked in CI
- LCP under 2 s on mid-range 4G.
- Under 40 KB of blocking CSS (gzipped).
- Under 60 KB of JS on data pages.
- Server-rendered board rows (`landing.py` already does this).
- Under 200 KB of photos on the home page.
- Comments stripped at deploy.

Measured today: 90 KB of blocking CSS, `predictions.js` at 74 KB gzipped, 528 KB of home photos, and a 500-row race freezing a mid-range phone for 0.8 s. S–M · low.

### Q. Parents' controls and a clear stance on minors
A claimed page (athlete or parent) gets self-serve controls: hide the photo, hide from search engines, hide entirely (today `person_optout` is script-only), and alert frequency. No negative lists of named minors. A visible "Racecast and minors" note. M · low.

### R. Community contributions with credit
- Uploads earn visible credit ("results added by Coach R.").
- Teams claim their page to fix the roster and schedule; the schedule feeds `weekend.py`, which fixes "who's running is a guess".
- Corrections are acknowledged by email.

M–L · medium.

### S. One story for college runners too
`/ncaa` and the college boards move under Rankings → College. A college runner's page shows their high-school self beside now. M · low.

---

## Proposed site map

### Five top-level sections (a bottom tab bar on phones)
| Section | What's in it |
|---|---|
| **Home / My runners** | followed runners and teams, their weekend, the state newsletter; signed out: search, how ratings work, this week's top races |
| **Search** | names, links, times, teams |
| **Rankings** | athletes, teams, single races, fastest times, courses; HS / MS / College in one picker; "This week" (movers + breakouts); All-time (records) |
| **Meets** | recent and upcoming; each meet page changes state from preview → results → forecast against result |
| **Plan** | goal, what it takes, conversions, training paces, college fit, compare; for coaches, the team dashboard and recruit search |

"Me" holds My page, follows, alerts, claimed pages, uploads and settings. About, the changelog, Report and Contact go to the footer.

### Merge, rename, retire
| Today | Proposal |
|---|---|
| `/athlete/<id>/profile` + `/recruit/<id>` | one **Recruiting profile**, a tab on the athlete page |
| `/recruiting`, `/recruiting/search`, `/coaches`, `/coaches/recruits`, `/account/shortlist` | **College fit** (athletes) and **Recruit search** (coaches), each with a shortlist inside |
| `/goal`, `/what-it-takes/*`, `/scale`, conversions paces | **Plan → Times & goals**: "I ran ___ → what it's worth / what it takes / my paces" (old URLs redirect) |
| `/projections/*` + `/state-odds/*` | **Who wins state**: tabs Teams · Individuals · Chances |
| `/breakouts` + `/movers` | **Rankings → This week** |
| meet preview + meet + recap + `/recaps` + compiled | **one meet URL** that changes with time; tabs Races · Combined · Forecast vs result |
| `/school/<x>/prs` + `/records` + the school's "best" tabs | school tabs **Roster · Records · Races · Season** |
| `/course/<x>` + `/venue/tf/...` | one course/track page type with the same tabs |
| `/records` | Rankings → All-time |
| `/schools` | folded into search and the state pages (keep for SEO) |
| `/rankings/<sport>/<pool>/<state>` | keep for SEO, but render them from the same template as the interactive board |
| `/ncaa/*` | Rankings → College |
| `/updates` | the footer "Changelog"; keep the new-results dot on Home |
| `/compare` | keep; call it Compare everywhere; "Compare with…" on athlete pages |
| `/embed/school/<x>` | keep, and offer it to coaches |
| "Advanced: each pool's own scale" (on 7 pages) | remove from public pages; at most a per-account setting for coaches |

### One page-type system
Every page type has the same parts:
1. a **header sentence** that answers first;
2. **one tab bar**;
3. **one main table** with two-line rows;
4. **side cards** that fold to the top on phones;
5. a **follow star** wherever it applies;
6. **Share** in one place;
7. a **source and trust line** at the bottom.

The meet page is closest to this today. Writing the system down (components, row shapes, a word list) would stop the drift the review keeps finding.

---

## Suggested order
1. **Turn on what's built**:
   - Follow completes after sign-in.
   - Digest on.
   - Avatar → My page.
   - CSS comments stripped.
   - The recap's "fell short" list dropped.
2. **A + B + C**: My runners, the first-visit strip, the rating ladder (the parent-facing core).
3. **E + F + D**: goals, the meet weekend, the season story (the weekly habit).
4. **The site-map merge, the page-type system and the PWA (N).**
5. **Coach and recruiting depth**: G, H, then I, R and S.
