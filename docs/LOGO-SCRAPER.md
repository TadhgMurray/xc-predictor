# The logo scraper, explained (305)

Written to be rewritten. If the comments in the code do not sound like
you, change them -- comments are not tested, and nothing here breaks when
the prose does. What follows is what the code actually does, in the order
the data moves, so you can read it once and then own it.

There is **no API anywhere in the scraping half**. The guess that it
"looked over the page with API scraping" is half right: the ADDRESS BOOK
uses a real query API (Wikidata's SPARQL endpoint), but finding the logo
is nothing more than downloading a school's home page as text and reading
the tags it puts in its own `<head>`.

---

## Two programs, one table between them

```
  build_school_websites.py          scrape_school_logos.py
  "which URL is this school?"       "what is that site's logo?"
            |                                  |
            v                                  v
      school_website  ------------------>  school_logo + PNGs on disk
      (school, state, url)                (school, state, path, kind, ...)
                                                   |
                                                   v
                                          racecast/school_logo.py
                                          "does this school have one?"
                                                   |
                                                   v
                                          the pages, the cards, the boards
```

Keeping them apart matters: the first is a matching problem (is this
Wikidata row the same school as our free-text string?), the second is a
network problem. They fail for completely different reasons and are worth
re-running independently.

---

## Following one school through `scrape_school_logos.py`

Read it from the bottom. `main()` (line ~960) is the spine; everything
above it is a helper it calls.

**1. Which schools, in what order** -- `targets()` (~729)

One SQL query: every school that has an address and either has never been
tried or was last tried outside the refresh window. Ordered by
`n_athletes DESC`, so `--limit 2000` means "the schools that appear on
most pages", not "the ones that sort early alphabetically".

**2. Hand each one to a worker** -- `main()` -> `workOne()` (~919)

24 threads. Each worker does network and image work only; the main thread
does every database write, because a psycopg2 connection is not
thread-safe. A worker cannot raise -- an exception becomes that school's
failure reason -- because one malformed page must not end a two-hour run.

**3. Fetch the home page** -- `fetchLogo()` (~645) -> `_fetchPage()` (~600)

Through `Manners` (~350), which is the politeness layer: robots.txt read
and obeyed per host, one second between requests to the SAME host (many
hosts at once, which is what makes it fast), one attempt, no retry after a
refusal. If the address fails in a way a different spelling could fix,
`homeVariants()` (~571) tries the host root, the other scheme, and www
toggled -- at most three.

**4. Find the athletics site** -- `athleticsLink()` (~246)

Reads every `<a>` on the page and scores it. A separate athletics domain
(`mitathletics.com`) wins outright; then an `/athletics` path; then any
link the page itself captions "Athletics", which is the case that matters
for colleges, because the domain is often a nickname with no tell in it
(`gopoets.com`). If there is one, its icons are tried FIRST -- that is the
difference between MIT's institutional wordmark and the Engineers mark.

**5. Read what the page declares** -- `iconCandidates()` (~196)

This is the part that answers "how did you choose the right one". A web
page declares its own icons, in its `<head>`:

```html
<link rel="apple-touch-icon" sizes="180x180" href="/at.png">
<link rel="icon" sizes="32x32" href="/favicon-32.png">
<meta name="msapplication-TileImage" content="/tile.png">
<meta property="og:image" content="/social-banner.jpg">
<link rel="manifest" href="/site.webmanifest">
```

`_Icons` (an `HTMLParser` subclass, ~120) collects them and stops at
`<body>`. They are then sorted into a fixed preference order, `KINDS`:

| order | why |
|---|---|
| `apple-touch` | a square logo by convention, usually 180 px |
| `icon-sized` | a `rel=icon` that declares its size, biggest first |
| `tile` | the Windows tile image, square by spec |
| `og` | the social preview -- a logo about half the time, a banner the rest |
| `icon` | a bare `rel=icon`, then the conventional `/favicon.ico` |

The web manifest is a second tier (`_fromText`, ~619): its icons are the
BEST source (192 and 512 px, square) but discovering them costs an extra
request, so it is only opened when nothing above worked.

**6. Download candidates in order and keep the first that qualifies**

`normalise()` (~530) opens each downloaded file with Pillow, trims its
transparent margin, and asks `acceptable()` (~502):

- at least **48 px** on the short side (rejects a 16 px favicon);
- aspect at most **3:1** if the site declared it an icon, **1.6:1**
  otherwise.

That second rule is the only clever thing in the file, and it exists
because shape alone cannot separate a 1200x630 social banner (1.9:1) from
a school's wordmark (3:1) -- but the DECLARATION can. A file a site names
as its own icon is its mark whatever shape it is.

The keeper is scaled into a 512 px transparent square and hashed.

**7. Store** -- `writeFile()` (~776) and `record()` (~790)

The PNG goes to `XCP_LOGO_DIR` under a name derived from
`sha1(school|state)`, so free-text school names never touch the
filesystem. The row records where it came from (`kind`), the source URL,
the hash, and -- on failure -- **why**, which is what `--stats` reads back.

**8. Afterwards** -- `markShared()` (~821)

Counts identical image bytes across schools. Anything worn by four or more
DISTINCT schools is a district's shared crest, not a school's, and the
site skips it.

---

## What is load-bearing and what is not

**Load-bearing** (change these and something real breaks):

- `Manners` -- the politeness. Getting this wrong gets us blocked, or
  worse, deserves to.
- `acceptable()` and `KINDS` -- these two decide every picture on the site.
- `fileFor()` in `racecast/school_logo.py` -- the scraper and the site both
  derive the filename from it. If they disagree, every crest 404s.
- `loadCrests()` -- the site asks it before writing any `<img>`, which is
  why a school without a crest never shows a broken image.

**Not load-bearing** (change freely): every comment, every log line, the
`★`/`⚠` markers, the docstrings, the ordering of helpers in the file.

---

## How to change it without fear

The 126 tests in `tests/test_school_logos.py` run in about eight seconds
with no network and no database:

```bash
python -m pytest -q tests/test_school_logos.py
```

They are the contract. Three of them stand up real HTTP servers on
loopback and run the real code against them, because the bugs that got
through were in the seams where robots and pacing and threads and Pillow
meet.

A useful way in:

1. Read `main()`, then `workOne()`, then `fetchLogo()`. Ignore everything
   else on the first pass.
2. Watch one school you know: `--only 'De La Salle' --redo --dry-run`.
   It prints a line per school saying which site and which tag won.
3. Change one threshold -- make `MIN_PX` 96 again -- and run the tests.
   The failures name the real cases that choice breaks. Change it back.
4. Now rewrite the comments in your own voice. Nothing will break.

If a change makes a test fail and you believe the test is wrong, the test
is a claim about a real school site; check the claim before deleting it.
