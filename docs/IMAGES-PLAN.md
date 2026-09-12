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
| `racecast/app.py` | step 4: `GET /img/school/<name>.png?state=`, 404 when there is none |
| `racecast/templates/school.html`, `static/style.css` | the crest in the school page's `<h1>`, inside the heading so it stays with the name when the row wraps |
| `racecast/cards.py` | the crest left of the title on the school share card, and as a badge on the corner of the athlete card's photo slot |
| `tests/test_school_logos.py` | 65 tests, no network, no database, Pillow optional |

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
