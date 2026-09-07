# Images for schools, courses and athletes (issue 305)

The owner wants to understand the job before it exists, and may write it.
This is the whole plan, in the order it would be built.

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
