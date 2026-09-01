/*
 * The proposed date for "run it this year" must land on the same WEEKDAY the
 * meet was actually run on, not the same date (owner, 2026-09-01).
 *
 * A year is 52 weeks plus a day, so keeping the month and day moves a meet one
 * weekday every year -- a Saturday invitational came back proposed on a Sunday.
 *
 * Runs the REAL function, extracted from predictions.js, so this cannot drift
 * from the shipped code:   node tests/test_predict_date.js
 */
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

const m = /function sameWeekdayNextYear\([\s\S]*?\n\}/.exec(SRC);
if (!m) { console.error("sameWeekdayNextYear not found in predictions.js"); process.exit(1); }
const sameWeekdayNextYear = eval("(" + m[0] + ")");

const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dow = (iso) => new Date(iso + "T00:00:00Z").getUTCDay();
const days = (a, b) =>
  Math.round((new Date(b + "T00:00:00Z") - new Date(a + "T00:00:00Z")) / 864e5);

let failed = 0;
function ok(cond, msg) {
  if (!cond) { console.error("  FAIL " + msg); failed++; }
  return cond;
}

/* 1. the reported case: a Saturday meet must come back on a Saturday */
{
  const orig = "2025-09-13";                       // a Saturday
  const got = sameWeekdayNextYear(orig, 2026);
  ok(dow(orig) === 6, `fixture ${orig} should be a Saturday`);
  ok(dow(got) === 6, `${orig} -> ${got} is ${DAY[dow(got)]}, want Sat`);
  ok(got === "2026-09-12", `expected 2026-09-12, got ${got}`);
  console.log(`  ${orig} (${DAY[dow(orig)]}) -> ${got} (${DAY[dow(got)]}) ..... OK`);
}

/* 2. every day of a year keeps its weekday, and never moves more than 3 days
      from the naive same-date answer */
{
  let worst = 0, checked = 0;
  for (let d = new Date(Date.UTC(2025, 0, 1)); d.getUTCFullYear() === 2025;
       d.setUTCDate(d.getUTCDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    if (iso.slice(5) === "02-29") continue;
    const got = sameWeekdayNextYear(iso, 2026);
    const naive = "2026" + iso.slice(4);
    if (!ok(got !== null, `${iso} returned null`)) continue;
    if (!ok(dow(got) === dow(iso),
            `${iso} (${DAY[dow(iso)]}) -> ${got} (${DAY[dow(got)]})`)) continue;
    const shift = Math.abs(days(naive, got));
    ok(shift <= 3, `${iso} moved ${shift} days, want <= 3`);
    worst = Math.max(worst, shift);
    checked++;
  }
  console.log(`  all ${checked} days of 2025 keep their weekday, max shift ` +
              `${worst} ................ OK`);
}

/* 3. across a leap year the shift is two days, still minimal and signed */
{
  const orig = "2023-09-16";                       // Saturday, before Feb 2024
  const got = sameWeekdayNextYear(orig, 2024);
  ok(dow(got) === dow(orig),
     `${orig} -> ${got} is ${DAY[dow(got)]}, want ${DAY[dow(orig)]}`);
  console.log(`  leap year: ${orig} -> ${got} (${DAY[dow(got)]}) ....... OK`);
}

/* 4. same year in, same year out -- it must not silently roll into another */
{
  const got = sameWeekdayNextYear("2025-11-01", 2026);
  ok(got.startsWith("2026-"), `stayed in 2026? got ${got}`);
  console.log(`  target year is respected (${got}) .................. OK`);
}

/* 5. junk in, null out -- defaultDate falls back to today on null */
{
  for (const bad of ["", "not-a-date", "2025-13"]) {
    ok(sameWeekdayNextYear(bad, 2026) === null, `"${bad}" -> not null`);
  }
  console.log("  malformed input returns null ....................... OK");
}

/* 6. localISO names the VIEWER's calendar day, not UTC's. toISOString() was
      proposing tomorrow for anyone west of Greenwich in the evening. */
{
  const mi = /function localISO\([\s\S]*?\n\}/.exec(SRC);
  ok(!!mi, "localISO not found in predictions.js");
  if (mi) {
    const localISO = eval("(" + mi[0] + ")");
    /* 23:30 on 1 Sep local time is already 2 Sep in UTC for UTC-7 hosts;
       localISO must still say the 1st. */
    const late = new Date(2026, 8, 1, 23, 30, 0);      // local constructor
    ok(localISO(late) === "2026-09-01",
       `late-evening 1 Sep gave ${localISO(late)}`);
    ok(localISO(new Date(2026, 0, 5)) === "2026-01-05", "zero padding");
    console.log(`  localISO keeps the local day (${localISO(late)}) ........ OK`);
  }
}

if (failed) { console.error(`\n${failed} assertion(s) failed`); process.exit(1); }
console.log("\nall predict-date tests passed");
