/*
 * fmt-time.js -- the one seconds -> clock formatter the boards and the
 * predictor share (sweep 2026-10-10).
 *
 * ! ROUND FIRST, THEN SPLIT. Both copies this replaces split off the minutes
 *   and THEN rounded the seconds, so 959.96 printed "15:60.0", 249.97 printed
 *   "4:010.0" and 3659.6 printed "1:00:60". Rounding the whole value to the
 *   shown precision before any division means the carry lands in the minutes.
 *
 * ★ PRECISION. A tenth under the hour (16:27.8), whole seconds past it
 *   (1:01:00) -- what rankings.js printed before, and what the predictor's
 *   one-decimal times already read as. The server's app.format_time keeps the
 *   stored precision instead; the boards round for width.
 *
 * Loaded by a plain <script> before rankings.js / predictions.js; node tests
 * require() it (tests/test_fmt_time.js).
 */
function rcFmtTime(sec) {
  if (sec === null || sec === undefined || sec === "") return " - ";
  const s = Number(sec);
  if (!isFinite(s)) return " - ";
  const pad = (n) => String(n).padStart(2, "0");
  const tenths = Math.round(s * 10);
  if (tenths >= 36000) {                       // an hour once rounded
    const w = Math.round(s);
    return `${Math.floor(w / 3600)}:${pad(Math.floor((w % 3600) / 60))}:${pad(w % 60)}`;
  }
  const m = Math.floor(tenths / 600);
  const t = tenths - m * 600;                  // tenths past the minute
  return `${m}:${pad(Math.floor(t / 10))}.${t % 10}`;
}

if (typeof module !== "undefined" && module.exports) module.exports = { rcFmtTime };
