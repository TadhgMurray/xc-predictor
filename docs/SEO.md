# Getting racecast.co into Google

What the site does for search, and the two things only the owner can do.

## What is in the code

- Every page has a title, a description, Open Graph tags, breadcrumbs and
  JSON-LD (`_meta.html`), and a `<link rel="canonical">` on the site's own
  origin, so `/athlete/123?r=456` and `/athlete/123` count as one page.
  The origin is `XCP_SITE_ORIGIN`, default `https://racecast.co`.
- `/robots.txt` allows everything but the API, search, debug and compare
  pages, and names the sitemap.
- `/sitemap.xml` is an index of files under `/static/sitemaps/`, written by
  `racecast/build_sitemap.py` after every pipeline run (step 13d): the
  fixed pages, every school, course and meet, and every athlete with a
  ranked season, with the season's last race as `lastmod`. Files hold
  45,000 URLs each; the athlete set is dozens of files.
- The 404 page is `noindex`.

## What only the owner can do

1. **Google Search Console.** Add the property `racecast.co` (the Domain
   type), verify with the TXT record Google gives you in Cloudflare DNS,
   then Sitemaps > add `https://racecast.co/sitemap.xml`. Within a few
   days the Pages report shows what is indexed and why anything is not,
   and the Performance report shows which searches show the site.
2. **Links.** Google recommends what other sites link to. A coach's page,
   a team site, a forum post about the difficulty scale, a mention on a
   running subreddit each count. Nothing technical substitutes for this.

## Reading Search Console

- Pages > "Discovered, currently not indexed": Google knows the URL from
  the sitemap and has not crawled it yet. Normal for a new site with
  millions of pages; it grows into it over weeks.
- Pages > "Duplicate, Google chose different canonical": the canonical tag
  is being ignored somewhere. Report it with the URL.
- Performance: sort by impressions to see what people search for; the
  long tail is athletes' own names.


## Added 2026-09-06

- **State ranking pages.** `/rankings/<xc|tf>/<pool>/<st>` (and the
  national `/rankings/<xc|tf>/<pool>`): one server-rendered top 100 per
  sport, pool and state, with its own title ("2026 California High
  School Boys Cross Country Rankings"), description, breadcrumbs and
  canonical, every athlete and school linked, and links to every sibling
  page. The board itself is JavaScript with the filters in the query
  string, so before this there was no page for the search people
  actually type. All 624 are in the sitemap and linked from the home
  page and the board's foot. `racecast/landing.py`.
- **Entity JSON-LD.** Athlete pages carry a Person, school pages a
  SportsOrganization, meet and race pages a SportsEvent with the date and
  the course. `meta_ld` in `_meta.html`.
- **IndexNow.** Step 13e (`scripts/indexnow_submit.py`) posts every URL
  whose lastmod is within three days, plus the fixed and landing pages,
  to api.indexnow.org after each sitemap build. Bing, Yandex, Seznam and
  Naver act on it within hours (DuckDuckGo rides on Bing). Google
  ignores it. Owner's side, once: pick a key (32 hex characters, for
  example `openssl rand -hex 16`), put `XCP_INDEXNOW_KEY=<key>` in
  /etc/xc-predictor.env, restart the site; the app serves it at
  `/<key>.txt`, which is how the engines verify it. Then add the site in
  Bing Webmaster Tools (it can import the Search Console property) and
  submit the sitemap there too.
