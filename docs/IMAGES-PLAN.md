# Images for schools, courses and athletes (issue 305)

The owner wants to understand the job before it exists, and may write it.
This is the whole plan, in the order it would be built.

> **BUILT, 2026-09-12: the schools half.** Steps 1 to 5 below exist and
> are wired into the site. Courses and athletes are still plan only.
> The runbook, what changed against this plan, and what is not yet known
> are at the end of this file, under **THE SCHOOL LOGO JOB AS BUILT**.

## What is scrapable, and what is not

- Schools: logos, from each school's own website. Scrapable.
- Courses: a map tile for every course (the coordinates are in the
  database already), and a licensed photo from Wikimedia Commons for the
  famous ones. Half scrapable.
- Athletes: not scrapable. The only sources (Athletic.net, MileSplit,
  social media) forbid it in their terms, and most of the people are
  minors. The photo slot on the card is filled by the athlete or the
  coach once accounts (283) exist. The initials placeholder stays.

## Schools: the logo job

1. Find the website.
   - Public high schools: the NCES Common Core of Data, a public-domain
     CSV (name, state, city, website) for every public school.
   - Private schools: the NCES private school survey, same shape.
   - Colleges: Wikidata (official website, and often a logo file).
   - Match to our school names by name plus state, the way
     build_college_directory matches; write the unmatched to a review
     file instead of guessing.
2. Fetch the logo: one request to the homepage, then read, in order,
   the Open Graph image, the Apple touch icon, any icon link with a
   size, the favicon. Keep the first that is at least 96 px and roughly
   square. Skip a file that shows up for many schools (the district's
   shared logo).
3. Store: normalise to a 512 px PNG under a data directory outside the
   repo (the cards directory pattern), one row per school in a table:
   school, state, path, source URL, kind, fetched date, and an override
   column the owner can set by hand to a URL or to "none".
4. Serve: /img/school/<name>.png?state=, 404 when there is none. The
   school page header, the school card's empty top-left slot, and the
   athlete card beside the initials use it. A missing logo changes
   nothing.
5. Refresh quarterly, only-if-changed; the override survives.

Manners: one request a second or slower, a user agent naming the site
with a contact address, robots.txt respected, one fetch per school with
no retry on a refusal, run once from tmux at a quiet hour, never beside
a pipeline step. Twenty thousand schools is a few hours. Expect a few
percent to refuse; that is lost coverage, nothing more.

## Courses

Same table shape. The map tile from OpenStreetMap at the course's
coordinates, with its attribution line, is the guaranteed image. On top,
a Wikimedia Commons API search by park name and city for a CC-licensed
photo, stored with its license and author so the credit can be shown.
Google Places photos are not cached under their license, so no.


---

# THE SCHOOL LOGO JOB AS BUILT (2026-09-12)

Four files, one route, three places on the site. Nothing runs on the
pipeline's clock and nothing is on by default: with no table and no files,
every page renders exactly as it did before -- proved for the share cards
by a test that renders one with a crest path pointing at a missing file
and compares it byte for byte with a card that has no crest at all.

| where | what |
|---|---|
| `scripts/build_school_websites.py` | step 1: which school lives at which address, from a public directory export and/or Wikidata, into `school_website` |
| `scripts/scrape_school_logos.py` | steps 2 and 3: one visit per school, the best icon it declares, normalised to a 512 px PNG, into `school_logo` and `XCP_LOGO_DIR` |
| `racecast/school_logo.py` | the site's read side: a path, or None for every kind of failure |
| `racecast/app.py` | step 4: `GET /img/school/<name>.png?state=&px=`, 404 when there is none; the start-up cache; `crest()` for templates and `stampCrests()` for the boards |
| `racecast/templates/*.html`, `static/style.css` | a crest beside **every** mention of a school -- see the list below |
| `racecast/static/rankings.js`, `search.js` | the same mark on the boards and the search rows the browser draws |
| `racecast/cards.py` | the crest left of the title on the school share card, and beside the athlete's name on the athlete card |
| `tests/test_school_logos.py` | 81 tests, no network, no database, Pillow optional |

## Where a crest appears

The school's own pages wear a 44 px crest in the `<h1>` (`school.html`,
`school_prs.html`). Everywhere a school is *named*, an 18 px mark goes
before the name:

race results and team standings (`race.html`), compiled meet pages
(`compiled.html`, `compiled_tf.html`), the track meet page and its team
points (`meet_tf.html`, `_tf_points.html`), all five course-page tables
(`course.html`), the athlete page header and every season line
(`athlete.html`), the home page, the landing pages, the compare page, the
recruiting profile, the state directory (`schools.html`), the search
results, and the three boards the browser draws (`rankings.js`).

Two rules make that affordable and safe:

- **One query, at start-up.** `school_logo.loadCrests` reads which
  `(school, state)` rows serve into memory, next to where
  `school_identity.loadLabels` reads the labels and for the same reason: a
  race page names forty schools and every one of them must know whether
  there IS a crest *before* it writes an `<img>`, because a tag that 404s
  is worse than no tag. Restart the site after a scrape to pick up new
  crests, exactly as the labels want a restart after a pipeline.
- **`?px=` for the small ones.** An 18 px mark has no use for a 512 px
  file, so the route resizes on demand into a cache the site's own user can
  write (`XCP_LOGO_CACHE`, default `/var/tmp/racecast-logo-thumbs`) and
  serves the full file if anything about that fails.

The boards are drawn by the browser, which cannot ask whether a crest
exists without fetching it, so `/api/rankings` and `/api/teams` stamp a
`crest` field onto the rows that have one. The athlete board keys off
`school_state`, not `state`: a result row's state is the VENUE's, and a
travel state would fetch a different school's crest.

A school without a crest emits no tag at all, so its name simply sits
where it always did. Until coverage is high a table therefore reads a
little ragged -- some names indented by a mark, some not. That is
deliberate: reserving the space on every row would indent every table on
the site for a crest most rows do not have. If the ragged look wins out
over the empty look once the real hit rate is known, it is one line in
`school_logo.crestImg` (return a spacer instead of `""`).

## The runbook

Both jobs run on the box, from tmux, at a quiet hour, never beside a
pipeline step -- they write the database the site reads.

```bash
cd /srv/xc-predictor
set -a; . /etc/xc-predictor.env; set +a      # XCP_LOGO_DIR lives here

# 1. the address book. --check parses and reports, writes nothing.
/srv/venv/bin/python scripts/build_school_websites.py --wikidata --check
/srv/venv/bin/python scripts/build_school_websites.py \
    --csv ~/ccd_directory.csv --wikidata --write
#    unmatched names land in scripts/school_website_unmatched.tsv

# 2. the crests. Look at fifty before you look at twenty thousand.
/srv/venv/bin/python scripts/scrape_school_logos.py --check
/srv/venv/bin/python scripts/scrape_school_logos.py --limit 50 --dry-run
/srv/venv/bin/python scripts/scrape_school_logos.py --write --rate 1.0

# 3. quarterly, the same command: --refresh-days 90 is the default, so a
#    school already done this quarter is not asked again. A school that
#    IS due costs one conditional request against the file we kept -- a
#    304 ends it and no home page is read -- so the second run is a
#    fraction of the first.
```

`XCP_LOGO_DIR` (default `/var/lib/racecast/logos`) must be writable by
whoever runs the scraper and readable by the site's user -- the same
split the share cards learnt with `XCP_CARD_DIR`. Set it in
`/etc/xc-predictor.env` and in the service unit, or both sides will
disagree about where the files are and every crest will 404.

**Restart the site after a scrape.** The crest cache is read once per
worker at start-up; without a restart the new crests are on disk and
served by `/img/school/...`, but no page knows to ask for them.

## What changed against the plan above

- **The NCES CCD does not reliably carry a school website.** The plan
  assumed a (name, state, city, website) CSV; the school-level file does
  not always have that last column, and in some years only the AGENCY
  file does. So `--csv` takes ANY export whose headers name a school, a
  state and a website (`_COLS` lists the spellings CCD, PSS and a
  hand-made list use) and says what it saw when one is missing. A
  district URL is still worth loading: it yields the district crest, and
  the shared-crest rule then throws that out for every school wearing it,
  which is the right answer reached by itself.
- **Wikidata hands over the logo file itself** (P154) for a good share of
  colleges, so those cost no website visit at all: the scraper fetches
  the file and never reads a home page.
- **Names are matched more shyly than `build_college_directory` matches.**
  "High School" and its spellings come off; "university" and "college"
  stay on, because dropping them makes Boston College and Boston
  University one school and one of them then wears the other's crest. A
  feed short name ("BYU") reaches its long name only through the college
  directory's own normalisation, and only for a name that directory
  already knows.
- **Nothing is guessed.** A school string that matches no directory row,
  or matches two that disagree, goes to the review TSV. A school whose
  name is shared by two real schools (Kingston WA and MO) answers only
  when the caller says which state it means.
- **The picking order is the plan's**, and the rule behind it is worth
  restating: the first candidate that is at least 96 px and roughly
  square wins, which is exactly why an Open Graph banner at 1200x630
  loses to the touch icon declared after it, and why a 16 px favicon
  loses to nothing at all.

## What is not known until it runs

- **The hit rate.** Unknown, and it is the number that decides whether
  this was worth doing. `--limit 50 --dry-run` prints one line per school
  and costs about two minutes; read those fifty before spending hours.
- **Whether the shared-crest threshold is right.** `SHARED_MIN = 4`
  distinct schools is a guess. After the first real run,
  `scrape_school_logos.py --sweep-only` re-runs just that sweep, so the
  threshold can be re-argued without re-fetching anything.
- **Whether Wikidata's SPARQL shape is right.** It could not be run from
  the sandbox that wrote it (no outbound network). `--wikidata --check`
  prints per state and writes nothing; a state that errors is reported
  and skipped rather than killing the run.
- **SVG-only sites are lost.** Pillow will not read one, so they are
  skipped rather than fetched. If they turn out to be a large share of
  the misses, `cairosvg` in the venv is the fix.


---

# WHAT THE FIRST REAL RUN TAUGHT (2026-09-12)

The first full run reached about 40,000 of ~110,000 schools with an
address, and got a crest for roughly a third of those. The owner's verdict
was three things, and two of them were my design errors.

## 1. "Way too slow" — the pace was global, and that was simply wrong

One request a second **everywhere** is twenty-four hours for forty thousand
schools. The reasoning behind it ("twenty thousand hosts means a per-host
delay is no delay") had it backwards: what a server experiences is the gap
between requests to **it**, and each school's server sees three requests in
total. A global clock protects nobody and costs a day.

Now: politeness is per host — a second between requests to the same server,
robots.txt and its Crawl-delay obeyed, one attempt, no retry — and
`--workers` (24 by default) of them run at once. Measured on twelve
loopback servers in `tests/test_school_logos.py`: 1.0 s where the old code
would have taken 14.

And the work is ordered **by athlete count, descending**. Alphabetical
spent the first hour on academies with four athletes. `--limit 2000` now
means "the schools that appear on most pages", not "the ones that sort
early".

## 2. "The coverage is atrocious" — two separate causes

**The picking was far too strict.** A 96 px floor and a 1.6:1 cap threw
away most of what school sites actually publish: 64 px favicons, 48 px CMS
icons, wide wordmarks. These are drawn at 18 px inline and 44 px in a
header, so 48 px is real detail. The floor is now 48 px, and the aspect cap
depends on **who declared the image** — because shape alone cannot separate
a 1.9:1 social banner from a 3:1 wordmark, but the declaration can. A file
a site names as its own icon is its mark whatever shape it is (up to 3:1);
an `og:image` is a banner until proven otherwise (1.6:1).

Three more sources of misses closed:

- **The web manifest.** `<link rel="manifest">` → its `icons` array, which
  on a modern CMS is 192 and 512 px and square. Kept as a second tier,
  tried only when no declared icon worked, because discovering it costs a
  request.
- **SVG.** Skipped entirely before, which was pure lost coverage. Read now
  when `cairosvg` is in the venv (`/srv/venv/bin/pip install cairosvg`);
  without it, still skipped rather than crashing.
- **og:image is no longer tried first.** The plan put it first; it is a
  1200x630 banner on nearly every CMS, so every school spent a request on a
  certain rejection. Declared icons go first, og is the fallback.

**But the real ceiling is the address book, not the picking.** 70,000 of
the 110,000 schools have no website on file at all, and no scraper can help
that. `--wikidata` alone was the only source used. That is the number to
attack next, with `--csv` and any directory export that carries a website
column. It is also worth remembering that the denominator includes every
middle school and club the site has ever seen: coverage weighted by how
often a school actually appears on a page is a very different number, and
the new priority ordering is what lets it be measured.

## 3. "It didn't find the images I wanted" — MIT

The owner's example: we scraped `web.mit.edu`'s icon, which is the
institutional wordmark. What belongs beside a school in a results table is
the **MIT Engineers** mark, which lives on `mitathletics.com`.

This was a real conceptual miss, and the fix is cheap because the link is
already on the page we fetch. The home page is now read for its athletics
link **before any icon is fetched**, and if there is one, that site's icons
are tried first:

- a separate athletics domain wins outright (`mitathletics.com`,
  `gobearcats.com`);
- otherwise an `/athletics` path on the school's own site;
- otherwise any link the page itself captions "Athletics" — which is the
  case that matters most for colleges, because an athletics domain is very
  often a nickname with no tell in it (`gopoets.com`, `rolltide.com`) and
  the caption is the only thing that identifies it.

Social links are never it, matched on the registrable domain so that
`x.com` does not also match `phoenix.com`.

`school_logo.kind` now records which site the crest came from —
`athletics:apple-touch`, `school:manifest`, `direct` — so
`scrape_school_logos.py --stats` says how much of the coverage is actually
athletics marks.

## Reading the damage

Every failure's reason is stored on its row, so there is no need to guess:

```
/srv/venv/bin/python scripts/scrape_school_logos.py --stats
```

prints how many crests there are, where each came from, and a histogram of
why the rest failed (sizes collapsed, so "too small" is one bucket rather
than a hundred). That output decides what to fix next.


---

# READING THE SECOND RUN'S HISTOGRAM (2026-09-13)

```
2,603 crests of 9,600 tried (27.1%), 0 flagged as a district's
addresses on file: 11,231
```

Sorted, the 6,997 misses say something very clear:

| what | n | where it went |
|---|---|---|
| **never reached the site** | **~4,556** | 1,886 connection failures, 1,844 HTTP 404, 487 HTTP 403, 137 robots, and a tail of Cloudflare origin errors |
| the site was fine, no usable icon | ~2,300 | 1,267 favicon 404, 666 too small or wrong shape, 358 unreadable |

**Two thirds of the misses were the ADDRESS, not the picking.** That is
where this round went.

- **HTTP 404 (1,844)** is a directory's deep link into a page that moved
  while the site itself is fine. `homeVariants` now tries the address as
  given, then the host's own root, then the other scheme, then www
  toggled -- at most three, and only while the failure is the kind a
  different spelling could fix. A refusal (robots, 403, 429) is never
  re-asked in another spelling.
- **"URLError" (1,886)** was a useless label: DNS, a dead certificate and a
  refused connection all arrive as one class. It now reports `dns`, `ssl`,
  `refused`, `reset` or `timeout`, so the next histogram is actionable.
- **`ssl`** gets one unverified retry. School district certificates expire,
  go self-signed and lose their intermediates constantly, and the school is
  still the school. The verified attempt always happens first; what is at
  stake if this is ever abused is a wrong PNG beside a school's name, and
  the hosts that needed it are named at the end of the run.
- **HTTP 403 (487)** is a WAF rejecting a User-Agent that does not look
  like a crawler it knows. The UA is now the conventional
  `Mozilla/5.0 (compatible; racecast/1.0; +url; contact: ...)` -- the shape
  Googlebot uses. It still names us and still carries a contact address.
- **666 too small / wrong shape** and **358 unreadable** are the previous
  round's fixes landing: the 48 px floor and SVG.

`og` was the single biggest source of the crests that DID work (1,204 of
2,603), which is worth knowing: schools really do put their logo in the
Open Graph tag. It is no longer tried first (it is a banner as often as a
logo, and trying it first cost a request per school), but it is still tried.

## What --redo had to learn

The quarterly refresh asks the file we kept last time and stops at a 304.
That is right for a quarterly run and exactly wrong after the picking has
changed: every school already holding its institutional logo would keep it
and never be offered the athletics one. `--redo` now forces full
rediscovery.

## The number that actually matters

`--stats` now also prints coverage among the biggest programmes:

```
coverage where it counts, by athlete count:
  top 500      ... have a crest, ... have an address
  top 2,000    ...
  top 10,000   ...
  all N        ...
```

"2,603 of 110,000" counts every middle school and club the corpus has ever
seen equally with the programmes that appear on every other page. A crest
is worth having where a school is NAMED. That table is also the order the
scraper works in, so `--limit` reads straight off it.

**And the ceiling is still the address book: 11,231 addresses.** No change
to the scraper can crest a school it has no URL for. Only `--wikidata` was
ever loaded. That is the next piece of work, and it is directory work, not
crawler work.
