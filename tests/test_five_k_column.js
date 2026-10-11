/*
 * The 5K column on the JS-built tables (owner, 2026-10-10: "add [a 5K
 * column] to all pages with ratings"). rankings.js and predictions.js draw
 * r.<key>_5k (conversions.stampFiveK) beside the rating: the header names
 * the distance every row shares ("5K", "3200m") or "Track ≈" over a mix,
 * and a cell names its own distance only when the header does not. Every
 * board's 5K header sits right after its rating column, and the phone
 * order (leadColumnUp) carries it up with the rating.
 *
 *   node tests/test_five_k_column.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const read = (f) => fs.readFileSync(path.join(__dirname, "..", "racecast", "static", f), "utf8");

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };
const slice = (src, from, to) => {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found: " + from);
  return src.slice(a, b);
};
const esc = (x) => String(x).replace(/&/g, "&amp;").replace(/</g, "&lt;");

// ---- rankings.js -------------------------------------------------------
const rk = read("rankings.js");
const ctx = { esc };
vm.createContext(ctx);
vm.runInContext(slice(rk, "const FK_TITLE", "\n/* ★ THE CREST COMES FROM THE ROW") +
  "\nthis.fkLabelFor = fkLabelFor; this.fkCell = fkCell; this.fkTh = fkTh;" +
  "\nthis.setHead = (h) => { fkHead = h; };", ctx);

const hs = { rating_5k: "15:02", rating_5k_dist: "5K" };
const ms = { rating_5k: "10:31", rating_5k_dist: "3200m" };
ok(ctx.fkLabelFor([hs, hs], "rating") === "5K", "all 5K -> 5K");
ok(ctx.fkLabelFor([ms, ms], "rating") === "3200m", "all middle school -> 3200m");
ok(ctx.fkLabelFor([hs, ms], "rating") === "Track ≈", "a mix -> Track ≈");
ok(ctx.fkLabelFor([{}, null], "rating") === "5K", "nothing stamped -> 5K");
ok(ctx.fkLabelFor([{ top5_mean_5k: "16:00", top5_mean_5k_dist: "3200m" }], "top5_mean") === "3200m",
   "the teams board reads its own key");

ctx.setHead("5K");
ok(ctx.fkCell(hs, "rating") === '<td class="fk">15:02</td>', "a 5K cell under a 5K header is the time alone");
ok(/fk-d">3200m/.test(ctx.fkCell(ms, "rating")), "a 3200 under a 5K header names itself");
ok(/fk-none/.test(ctx.fkCell({ rating_5k: null }, "rating")), "no clock -> a muted dash");
ctx.setHead("3200m");
ok(!/fk-d/.test(ctx.fkCell(ms, "rating")), "under its own header a 3200 is the time alone");

// every board: a 5K header right after the rating it glosses
const cols = vm.runInNewContext("(" + slice(rk, "const COLUMNS = {", "\n};\n").replace("const COLUMNS = ", "") + "\n})");
for (const [board, after] of [["ability", "rating"], ["performance", "rating"], ["pr", "rating"],
                              ["teams", "rating"], ["teamscourse", "Top 5 avg"]]) {
  const list = cols[board];
  const i = list.findIndex((c) => c.fk);
  ok(i > 0, `${board} has a 5K column`);
  ok(list[i - 1].key === after || list[i - 1].label === after, `${board}: the 5K follows ${after}`);
}
ok(!cols.courses.some((c) => c.fk), "the courses board carries no rating, so no 5K");

// renderBoard sets the header before drawing, on the teams' own key
ok(/fkHead = fkLabelFor\(rows, state\.board === "teams" \? "top5_mean" : "rating"\)/.test(rk),
   "renderBoard labels the header from the rows it draws");
// the phone order moves the 5K with the rating
ok(/if \(fk\) tr\.insertBefore\(cells\[from \+ 1\], cells\[to \+ 1\]\)/.test(rk),
   "leadColumnUp carries the 5K up beside the rating");

// ---- predictions.js ----------------------------------------------------
const pr = read("predictions.js");
const pctx = { esc };
vm.createContext(pctx);
vm.runInContext(slice(pr, "/* ★ \"5K EQUIV\"", "\nfunction finishTable") +
  "\nthis.fkHead = fkHead; this.fkCell = fkCell; this.fkTh = fkTh;", pctx);
ok(pctx.fkHead([hs, ms]) === "Track ≈", "predictions: a mixed field -> Track ≈");
ok(/>15:02 <span class="fk-d">5K/.test(pctx.fkCell(hs, "Track ≈")), "predictions: under Track ≈ a cell names its distance");
ok(/class="fk fk-sm"/.test(pctx.fkCell(hs, "5K")), "predictions: the column folds away on a phone");
ok((pr.match(/<th>Rating<\/th>\$\{fkTh\(fk, "fk-sm"\)\}/g) || []).length === 2,
   "predictions: the 5K header follows Rating, on both tables");

// ---- "5K equiv" with its ⓘ (owner, 2026-10-11) ----------------------------
// every JS-built 5K header is _scale.html's fk_th, character for character
// (tests/test_five_k_column.py holds the macro to the same strings)
const WANT_5K = '<th class="fk no-break">5K equiv<a class="flag info-i" href="/scale"'
  + ' data-title="Track 5K equivalent" data-blurb="The time on a track 5K that this rating'
  + ' is worth. It reads the rating, not this race\'s course or distance."'
  + ' aria-label="Track equivalent: the time on a track this rating is worth">i</a></th>';
const fkThs = { "rankings.js": ctx.fkTh, "predictions.js": pctx.fkTh };
for (const f of ["meet-preview.js", "recruiting.js", "recruiting-search.js"]) {
  const src = read(f);
  const c = { esc };
  vm.createContext(c);
  vm.runInContext(slice(src, "  function fkTh", "\n  }\n") + "\n  }\nthis.fkTh = fkTh;", c);
  fkThs[f] = c.fkTh;
}
for (const [f, fn] of Object.entries(fkThs)) {
  ok(fn("5K") === WANT_5K, `${f}: the 5K header is fk_th's`);
  ok(fn("3200m").startsWith('<th class="fk no-break">3200m equiv<a') && /data-title="Track 3200m equivalent"/.test(fn("3200m")),
     `${f}: a 3200m header says "3200m equiv"`);
  ok(fn("Track \u2248", "fk-sm").startsWith('<th class="fk no-break fk-sm">Track \u2248<a') &&
     /data-title="Track equivalent"/.test(fn("Track \u2248")), `${f}: "Track ≈" stays as it is`);
}
ok(/if \(c\.fk\) return fkTh\(fkHead\)/.test(rk), "rankings: every board's 5K header is fkTh");
ok(/th\.outerHTML = fkTh\(fkLabel, "fk-sm pv-fk"\)/.test(read("meet-preview.js")),
   "meet preview: the header is redrawn whole, ⓘ and all");
for (const f of ["rankings.js", "predictions.js", "meet-preview.js", "recruiting.js", "recruiting-search.js"]) {
  const src = read(f);
  ok(!/<th[^>]*class="[^"]*\bfk\b[^"]*"[^>]*title=/.test(src), `${f}: no 5K header carries a title`);
  ok(!/<td class="(n|num) fk/.test(src), `${f}: 5K cells are plain .fk, not right-aligned`);
}

if (failed) { console.error(`${failed} failed`); process.exit(1); }
console.log("ok");
