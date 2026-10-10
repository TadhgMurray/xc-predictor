/*
 * The boards' and the predictor's one clock formatter rounds BEFORE it splits
 * (sweep 2026-10-10): 959.96 printed "15:60.0", 249.97 "4:010.0", 3659.6
 * "1:00:60". Table-driven against the shipped file, and both callers must
 * delegate to it:   node tests/test_fmt_time.js
 */
const fs = require("fs");
const path = require("path");
const STATIC = path.join(__dirname, "..", "racecast", "static");
const TEMPLATES = path.join(__dirname, "..", "racecast", "templates");
const { rcFmtTime } = require(path.join(STATIC, "fmt-time.js"));

const CASES = [
  [959.96, "16:00.0"],               // was 15:60.0
  [249.97, "4:10.0"],                // was 4:010.0
  [3659.6, "1:01:00"],               // was 1:00:60 / 60:59.6
  [3599.96, "1:00:00"],
  [987.84, "16:27.8"],
  [59.96, "1:00.0"],
  [9.04, "0:09.0"],
  [null, " - "],
  [undefined, " - "],
  ["x", " - "],
];
let failed = 0;
for (const [sec, want] of CASES) {
  const got = rcFmtTime(sec);
  if (got !== want) { console.error(`  FAIL ${sec} -> ${got}, want ${want}`); failed++; }
}
for (const f of ["rankings.js", "predictions.js"]) {
  const src = fs.readFileSync(path.join(STATIC, f), "utf8");
  const m = /function fmtTime\([^)]*\) \{[\s\S]*?\n\}/.exec(src);
  if (!m || !/rcFmtTime\(/.test(m[0]) || /Math\.floor/.test(m[0])) {
    console.error(`  FAIL ${f}: fmtTime must delegate to rcFmtTime`); failed++;
  }
}
for (const t of ["rankings", "predictions"]) {
  const src = fs.readFileSync(path.join(TEMPLATES, t + ".html"), "utf8");
  const a = src.indexOf("fmt-time.js"), b = src.indexOf(`'${t}.js'`);
  if (a < 0 || b < 0 || a > b) { console.error(`  FAIL ${t}.html: fmt-time.js must load first`); failed++; }
}
if (failed) { console.error(`${failed} failed`); process.exit(1); }
console.log("fmt-time: all OK");
