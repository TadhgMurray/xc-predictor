# Site notes -- current issues and the page-by-page catalogue

Updated 2026-08-26. Two lists: what is wrong or at risk right now, and what
each page of the site could gain or should change. Items marked [mock] are
UI work that gets a mock for approval before any code, per the house rule.

---

## Part 1 -- Current issues

### Data correctness

1. **Duplicate races across feeds** (backlog id "9c"). The same real-world
   race often exists once per feed with different meet/div keys. Splits an
   athlete's results, double-counts on boards, and made the Ox Bow fix
   confusing (one copy corrected, the twin not). Needs a canonical-race
   sweep keyed on (canon_meet_id, person overlap, date).
2. **Small distance errors survive detection.** Labels 10-20 percent too
   long sit inside athlete-variance noise; the bars are deliberately above
   it. Accepted trade -- lowering the bars re-creates the 378-bad-proposals
   failure. Documented so nobody mistakes it for a bug later.
3. **TF distance detection is weaker than XC.** Track distance comes from
   event names; the pass tools and pace tests are XC-centric. A mislabeled
   TF event mostly relies on the anchor gate at board build.
4. **Apparent temperature is inflated 20-35 C daytime.** The grid writer
   feeds raw solar irradiance where net absorbed radiation belongs
   (Steadman term). Consistent, so usable as a model feature; wrong for
   display or for the weather-correction refit. Proper fix = derivation fix
   in atmost_era5_zarr + grid column rebuild + correction refit.
5. **One-night blindness in the reset.** Pass 0 judges the pre-wipe state,
   so an override the wipe removes and the passes fail to re-propose is
   wrong for exactly one night. It is named the same evening in 05c_lost
   and self-nuked on the next reset; residual risk accepted.
6. **School alias variants.** Identity clustering splits on home state but
   cannot join spelling/campus variants ("Highland - P"). Needs a small
   hand alias map consulted by school_identity.
7. **Unpriceable rows still show dashes.** No person link, no gender, or no
   resolvable pool means no rating on any scale. Honest, but the counts
   should be watched in fill_ratings' census -- growth means a linking
   regression upstream.

### Site behavior

8. **About page is out of date.** It describes the pre-fill world. It must
   now say: every row with a normalized time carries a rating; out-of-band
   rows are visible but excluded from every board by the pace band.
9. **Search cold tail ~2.6 s.** Needs the pg_trgm index on search_text.
10. **Course boards beyond the top 1200 render live.** Fast enough warm;
    first hit on an obscure course pays hundreds of ms. Incremental builder
    (build on first request, persist) would close it.
11. **Venue TF pages are thin.** Meets list + best performances only; no
    per-event records grid, unlike the course page's full treatment.
12. **Meet TF division naming.** "Open 1/2/3" style shards and identical
    twin divisions still render as separate sections (Arcadia items 2 and
    3, discussed, awaiting go).

### Operational / model

13. **Model untrained until the first successful features+train night.**
    Predictions tab is fully wired but refuses without artifacts; chunk
    files did not exist before the weather fix.
14. **Post-rebuild canaries not yet re-verified** after this correction
    wave: rosters and home boards populated, 9:01 3200 rating in 135-137,
    normalized 5K around 15:05-15:10, Ox Bow / NLC pages corrected or
    dropped, record-flag covering index in use, then the cold sweep
    benchmark.
15. **Metric drift after fill.** Tools that counted `speed_rating IS NULL`
    as "refused" (audit_overrides, the pass "recovered" counter,
    n_shadow) now read near zero. Print-only, but a reader who remembers
    the old meaning will be misled; re-point them at ranking_results
    membership when next touched.
16. **scripts/config.py carries the DB password in git.** Private repo and
    a local-only database, but it should move to an env var or an ignored
    local file before the repo is ever shared.

---

## Part 2 -- Page-by-page catalogue

Route map: home, /meets, /search, /rankings, /predictions, /compare,
/conversions, /athlete, /race/xc, /meet/xc, compiled XC, /race/tf,
/meet/tf, compiled TF, /school, /school/prs, /course, /venue/tf, /about,
/report.

### Home `/`
Has: rankings snapshot, latest results.
- Add an **upcoming meets strip** once future meets exist in the tables --
  the natural front door in season. [mock]
- Persist the visitor's **state filter** (localStorage) so the snapshot
  opens on their state.
- Surface the **Predictions tab** from the home page once the model ships;
  it is currently only reachable if you know it exists.

### Meets `/meets`
Has: recent meets grouped by month; per-course listing.
- **State and sport filters** (XC/TF badge per row).
- A season picker rather than infinite recency.

### Race XC `/race/xc/<meet>/<div>`
Has: team scores, results with ratings/PR flags, difficulty, scale toggle.
- **Corrected-distance badge**: when dist_override changed the label, show
  "listed 8046 m, corrected to 5000 m" instead of silently rendering the
  corrected number. Builds trust and catches bad corrections via reports.
- **Withheld-ratings notice** for nuked divisions: "ratings withheld --
  recorded distance fails plausibility" beats a silent column of dashes.
- **Race-day weather line** (temp, wind, humidity at race hour) -- the
  weather table now exists and is joined per meet; displaying it is cheap.
- **Twin-race banner** when the same race exists from the other feed
  (depends on issue 1).
- Per-km pace column next to time.

### Meet XC `/meet/xc/<meet>` and compiled
Has: division list, compiled boards by distance/gender.
- Show each division's (corrected) distance and rated share in the list --
  a broken division is then visible from the meet page.

### Race TF `/race/tf/...`
Has: results, event naming via prettifiers, staged standings.
- Event-record flags (meet record, venue record) like XC's record flags.
- Wind display for sprint/jump events if the feed ever carries it.

### Meet TF `/meet/tf/<meet>` and compiled
Has: per-division tables, computed + official scores with 100 percent
coverage gate, hurdle/steeple naming, EnRoute exclusion, school links.
- **Combined headline team table** with per-division collapsed under it
  [mock -- proposed, awaiting go].
- **Fold "Open 1/2/3" into the stem** when a bare stem exists (Arcadia 2).
- **Twin-division dedupe** (Arcadia 3).

### Athlete `/athlete/<id>`
Has: XC and TF sections, charts, per-row ratings, scale toggle.
- **PR progression chart** per event/distance over career.
- **Season-best summary strip** (SB, PR, rating percentile in pool).
- Quick **"compare with..."** action wired to /compare.
- Filled-but-out-of-band rows could carry a subtle marker (tooltip: "rating
  outside the plausibility band, not ranked") so absurd numbers on a
  wrong-distance race read as data problems, not achievements.

### Compare `/compare`
Has: head-to-head, meetings, season by season, bests, ratings over time.
- **Three-plus athletes** instead of pairs.
- Shareable URL state (probably already partial; verify).

### School `/school/<name>` and `/school/<name>/prs`
Has: identity chips with state split, roster, PR boards including hurdles.
- **Team season trajectory** (top-5 average rating over the season, per
  year). [mock]
- **Dual-meet simulator**: pick an opponent school, predictions engine
  scores a hypothetical dual. Reuses /api/predict plumbing. [mock]
- Relay PRs section on the PR page (currently individual events only).

### Course `/course/<name>`
Has: distances raced, best ratings, team performances, records, meets.
- **Map embed** from stored GPS.
- **Difficulty context**: show n races and days behind the difficulty
  number, and a same-state course comparison ("runs ~12 s slower than X").
- Typical race-day weather (the grid has decades of hours).

### Venue TF `/venue/tf/<loc>/<indoor>`
Has: meets, best performances.
- Per-event records grid to match the course page's depth (issue 11).

### Rankings `/rankings`
Has: pool/sport/year boards via API.
- **Filters: state, grade/class year** on top of pool.
- Jump-to-athlete ("where am I") exists in API; surface it in UI.

### Predictions `/predictions`
Has: meet picker, three target modes, race chips, mode-aware fields with
top-7 display, aged-out carry, individual + team predictions.
- **Confidence intervals** once the model ships (predict variance or
  quantile head later; even a fixed +-MAE band helps).
- **Weather scenario knob** (hot/cold/wet day) -- the model consumes
  weather features, so the UI can expose them. [mock]
- **Hypothetical meet builder**: pick arbitrary teams, no source meet.
- Save/share a scenario via URL state.

### Search `/search`
Has: ranked multi-kind results, phrase boost, school identity rows.
- Keyboard navigation and type-filter chips (athlete/school/meet/course).
- The trigram index (issue 9) before any feature work.

### Conversions `/conversions`
Has: XC and TF converters, training paces.
- URL state so a conversion is linkable. Low priority.

### About `/about`
- Rewrite the rating explanation for the fill/band world (issue 8), and
  add a short "how distances get corrected" section -- users who see a
  corrected-distance badge will land here.

### Report `/report`
Has: free-text report with API.
- Prefill the reporting page's context (URL, meet/div ids) so reports
  arrive actionable; show a simple "received" state.

---

## Suggested order

Quick wins first: trigram index, About rewrite, corrected-distance badge +
withheld-ratings notice, race-day weather line, report prefill. Then the
approved-pending TF meet work (combined table mock, Open-fold, twin
dedupe). Then the bigger mocks: dual-meet simulator, team trajectory,
weather scenario knob. Data issues 1 (twin races) and 6 (alias map) unlock
several of the above and should ride along early.
