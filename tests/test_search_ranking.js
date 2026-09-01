/*
 * Two search bugs, both site-wide.
 *
 * 1. A LONGER QUERY COULD LOSE A ROW. Both searches ranked on keys that
 *    every row matching the same tokens ties on, so the real tiebreak was
 *    popularity -- and typing more of a name could push the exact row past
 *    the LIMIT. (#97, and the same shape again on the athlete picker.)
 * 2. A RESPONSE COULD RE-OPEN A BOX THE READER HAD LEFT. blur hid the list,
 *    the debounce fired after it, and the fetch painted over the page with
 *    no focus left to blur again.
 *
 *   node tests/test_search_ranking.js
 */
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const read = (...p) => fs.readFileSync(path.join(root, ...p), "utf8");

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };

/* ---- 1a. /search/api and /search rank the whole phrase first ---- */
{
  const py = read("racecast", "app.py");
  const hits = py.match(
    /ORDER {2}BY \(CASE WHEN search_text LIKE %\(t_phrase\)s\n\s+THEN 1 ELSE 0 END\) DESC,\n\s+\(\{word_score\}\) DESC/g) || [];
  ok(hits.length === 2,
     `both search surfaces rank an exact prefix first (found ${hits.length}` +
     ` of 2) -- the dropdown and the results page drifted apart once before`);
  ok(/params\[f"\{prefix\}_phrase"\] = " "\.join\(tokens\) \+ "%"/.test(py),
     "and the phrase parameter is still the whole typed query, anchored");
}

/* ---- 1b. the athlete picker ranks BEFORE it cuts ---- */
{
  const py = read("racecast", "app.py");
  const i = py.indexOf("def api_predict_athletes(");
  const body = py.slice(i, py.indexOf("\n@app.route", i));

  ok(/SELECT \* FROM \(/.test(body),
     "the DISTINCT ON is wrapped, so the outer query can rank it");
  ok(/ORDER {2}BY \(CASE WHEN lower\(name\) LIKE %\(prefix\)s/.test(body),
     "a name that starts with what was typed wins outright");
  ok(/mean_rating DESC NULLS LAST/.test(body),
     "ties go to the better athlete");
  const order = body.lastIndexOf("ORDER  BY (CASE WHEN lower(name)");
  const limit = body.lastIndexOf("LIMIT  40");
  ok(order > 0 && limit > order,
     "the ranking comes BEFORE the limit -- with the limit inside the "
     + "subquery it kept the 40 lowest person_ids, an arbitrary set");
  ok(!/rows\.sort\(key=lambda/.test(body),
     "and the Python re-sort is gone: sorting after a cut cannot recover "
     + "rows the cut removed");
  ok(/params\["prefix"\] = q\.lower\(\) \+ "%"/.test(body),
     "the prefix parameter is the typed query");
}

/* ---- 1c. the name match is indexable at all ---- */
{
  const idx = read("scripts", "add_page_indexes.py");
  ok(/idx_athletes_name_trgm/.test(idx),
     "there is a trigram index for the picker's ILIKE '%tok%'");
  ok(/gin_trgm_ops/.test(idx), "a GIN trigram index -- a btree cannot serve "
     + "a leading wildcard");
  ok(/COALESCE\(first_name,''\) \|\| ' ' \|\| COALESCE\(last_name,''\)/.test(idx),
     "on the same expression the query searches, or the planner ignores it");
  ok(/spec\.strip\(\)\.upper\(\)\.startswith\("USING"\)/.test(idx),
     "and the existence check tolerates Postgres rewriting an expression "
     + "index, or it reports MISS forever");
}

/* ---- 2. no picker may be re-opened by a late response ---- */
{
  for (const f of ["predictions.js", "compare.js", "conversions.js",
                   "topbar-search.js"]) {
    const js = read("racecast", "static", f);
    ok(/closed = true/.test(js), `${f}: leaving the box marks it closed`);
    ok(/closed = false/.test(js), `${f}: typing re-opens it`);
    ok(/\|\| closed\) return|!live\(\)\) return/.test(js),
       `${f}: a response checks it before painting`);
  }
  /* And the flag must be set at once, not after the hide delay -- the whole
     point is to drop a response that arrives during that delay. */
  const pj = read("racecast", "static", "predictions.js");
  const blur = pj.slice(pj.indexOf('input.addEventListener("blur"'));
  const head = blur.slice(0, 200);
  ok(head.indexOf("closed = true") < head.indexOf("setTimeout"),
     "the flag is set before the delayed hide, not inside it");
}

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("  both searches rank an exact prefix first .......... OK");
console.log("  the athlete picker ranks before it cuts ........... OK");
console.log("  its name match has an index that can serve it ..... OK");
console.log("  no picker re-opens after the reader leaves ........ OK");
console.log("\nall search-ranking checks passed");
