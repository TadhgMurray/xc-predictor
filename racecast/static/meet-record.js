/*
 * meet-record.js -- the model's track record at the chosen meet, one line
 * under step 1 of /predictions (owner, 2026-10-10, item 14): "On 3 past
 * editions of this meet (2023 to 2025, 6 races) the model picked the winner
 * 4 of 6 times; median miss 9.8 s (1.0%)."
 *
 * ★ IT WATCHES, IT DOES NOT HOOK. predictions.js draws the chosen meet into
 *   #meet-chosen (renderChosenMeet) and clears it on Change; this file reads
 *   the meet off that block's own link (/meet/xc/<id>?alt=N) whenever it is
 *   redrawn, so neither file has to know the other is there.
 *
 * ! PRECOMPUTED ONLY. /api/predict/track_record sums stored backtests
 *   (meet_track.py); a meet with none says nothing at all.
 */
"use strict";

(function () {
  var box = document.getElementById("meet-chosen");
  var line = document.getElementById("meet-record");
  if (!box || !line) return;
  var shown = null, seq = 0;

  function hide() { line.hidden = true; line.textContent = ""; shown = null; }

  function read() {
    var a = box.classList.contains("hidden") ? null : box.querySelector("a.mc-name");
    var m = a && /\/meet\/(xc|tf)\/(\d+)(?:\?alt=(\d+))?/.exec(a.getAttribute("href") || "");
    if (!m) { hide(); return; }
    if (m[1] !== "xc") { hide(); return; }            // cross country only
    var key = m[2] + "|" + (m[3] || "");
    if (key === shown) return;
    shown = key;
    line.hidden = true;
    var q = new URLSearchParams({ meet_id: m[2], sport: "XC" });
    if (m[3]) q.set("alt", m[3]);
    // a coming-up link names its feed (&src=); only for the meet it named
    var here = new URLSearchParams(location.search);
    if (here.get("src") && here.get("meet_id") === m[2]) q.set("src", here.get("src"));
    var mine = ++seq;
    fetch("/api/predict/track_record?" + q.toString())
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (mine !== seq || shown !== key) return;
        if (body && body.available && body.text) {
          line.textContent = body.text;
          line.hidden = false;
        }
      })
      .catch(function () { /* a line, not a page */ });
  }

  new MutationObserver(read).observe(box, { childList: true, subtree: true,
                                             attributes: true, attributeFilter: ["class", "href"] });
  read();
})();
