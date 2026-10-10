/*
 * Owner, 2026-10-10: a find box over the predicted results, in the result's
 * tab bar, that hides rows by runner or team and changes no place or score.
 * And the rankings' rating cells carry the rating as a time on hover.
 *     node tests/test_predict_finder.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const STATIC = path.join(__dirname, "..", "racecast", "static");
const SRC = fs.readFileSync(path.join(STATIC, "predictions.js"), "utf8");
const RK = fs.readFileSync(path.join(STATIC, "rankings.js"), "utf8");
let fails = 0;
function check(ok, msg) { if (!ok) { console.error("FAIL: " + msg); fails++; } }

/* pull one top-level function out of a script, by name */
function fn(src, name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error("no function " + name);
  let depth = 0, j = src.indexOf("{", i);
  for (; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) break;
  }
  return src.slice(i, j + 1);
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(fn(SRC, "findKey") + fn(SRC, "findMatches"), ctx);
const key = ctx.findKey(["Owen Castellano", "Jesuit (OR)"]);
check(key === "owen castellano jesuit (or)", "the key is lower case: " + key);
check(ctx.findKey(["José Núñez"]) === "jose nunez", "accents fold");
check(ctx.findMatches(key, "jesuit"), "a school matches");
check(ctx.findMatches(key, "  OWEN  jes "), "every word, any order, any case");
check(!ctx.findMatches(key, "owen summit"), "a word that is not there misses");
check(ctx.findMatches(key, ""), "an empty box matches everything");
check(ctx.findMatches(ctx.findKey(["Jose"]), "José"), "a typed accent folds too");

// the box is in each predicted race's own tab bar, with race.html's class
const team = fn(SRC, "renderTeam");
check(/class="rc-find pred-find"/.test(team), "renderTeam draws the finder");
check(team.indexOf("pred-find") > team.indexOf("rc-tbar-end")
      && team.indexOf("pred-find") < team.indexOf('data-panel="results"'),
      "the finder ends the tab bar");
// both tables' rows carry a key; nothing renumbers
check(/<tr data-find=/.test(fn(SRC, "finishTable")), "finisher rows carry data-find");
check(/<tr data-find=[\s\S]{0,200}runners\.map\(\(r\) => r\.name\)/.test(fn(SRC, "teamScoreTable")),
      "team rows match their runners' names too");
const filt = fn(SRC, "filterPredicted");
check(/tr\.hidden = !hit/.test(filt) && !/textContent\s*=\s*[^;]*place/.test(filt),
      "filtering only hides rows");
check(/closest\("\.team-result"\)/.test(filt), "each race's box filters its own race");
check(/team-tool-out/.test(filt), "a dual meet or best seven answer is left whole");

// rankings: the rating cell's title is the API's rating_clock, escaped
const rk = {};
vm.createContext(rk);
vm.runInContext(fn(RK, "esc") + fn(RK, "clockAttr"), rk);
check(rk.clockAttr({ rating_clock: "≈ 14:39 5K on a typical course" })
      === ' title="≈ 14:39 5K on a typical course"', "clockAttr writes a title");
check(rk.clockAttr({}) === "" && rk.clockAttr(null) === "", "no clock, no title");
check((RK.match(/[,{]\s*clockAttr\(r\)/g) || []).length === 3,
      "ability, performance and best-times rating cells all carry it");

if (fails) process.exit(1);
console.log("ok: the prediction finder and the rating clock titles");
