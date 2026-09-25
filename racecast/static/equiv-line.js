/* equiv-line.js -- the equivalent-times card on race and course pages.
 *
 * ★ WHAT IT SHOWS. A ruler of times at this course or track; under every
 *   labelled tick, the same fitness at the distance the reader picked (on a
 *   typical track, or an average cross country course from a track page).
 *   The time under the pointer is read out large on both sides, with the
 *   rating it corresponds to. Points come from /api/equivalence -- one per
 *   rating, 40 to 170 -- and everything between two points is interpolated,
 *   which at one rating point apart is well under a second.
 *
 * ★ THE RATING FOLLOWS THE PAGE'S SCALE TOGGLE (owner, 2026-09-25). On a page
 *   that offers "HS-equivalent", window.rcScale.mode === "hs" shows the
 *   rating times the pool's HS factor (the API sends it), and an
 *   rc-scale-change event flips it live.
 *
 * ! ONE FETCH PER (distance, group, target). Scrolling never asks again.
 */
(function () {
  "use strict";

  function fmt(sec, tenths) {
    if (sec == null || !isFinite(sec)) return "–";
    var s = tenths ? Math.round(sec * 10) / 10 : Math.round(sec);
    var m = Math.floor(s / 60), r = s - m * 60;
    var rs = tenths ? r.toFixed(1) : String(Math.round(r));
    if (r < 10) rs = "0" + rs;
    return m + ":" + rs;
  }

  function parseTime(txt) {
    txt = String(txt || "").trim();
    if (!txt) return null;
    var parts = txt.split(":");
    if (parts.length === 1) {
      var v = parseFloat(parts[0]);
      return isFinite(v) ? v : null;
    }
    var m = parseInt(parts[0], 10), s = parseFloat(parts[1]);
    return isFinite(m) && isFinite(s) ? m * 60 + s : null;
  }

  // piecewise-linear along the source-time column (points ascend in time)
  function interp(points, t, col) {
    var n = points.length;
    if (!n) return null;
    if (t <= points[0][1]) return points[0][col];
    if (t >= points[n - 1][1]) return points[n - 1][col];
    var lo = 0, hi = n - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (points[mid][1] <= t) lo = mid; else hi = mid;
    }
    var a = points[lo], b = points[hi];
    return a[col] + (t - a[1]) / (b[1] - a[1]) * (b[col] - a[col]);
  }

  // the ruler's scale follows the event: an 800 spread across the same
  // pixels as a 5K would be unreadable
  // and the labels are spaced by PIXELS, not seconds: about one every
  // 90 px whatever the event, so a phone still shows three or four
  var STEPS = [[5, 1, 0], [10, 1, 5], [15, 5, 0], [20, 5, 10], [30, 5, 15],
               [60, 10, 30]];                  // [label, tick, mid-tick] in s
  function scaleFor(dist) {
    var pxps = Math.max(4, Math.min(30, 6 * 5000 / dist));
    var pick = STEPS[STEPS.length - 1];
    for (var i = 0; i < STEPS.length; i++) {
      if (STEPS[i][0] * pxps >= 90) { pick = STEPS[i]; break; }
    }
    return { pxps: pxps, major: pick[0], minor: pick[1], mid: pick[2] || pick[0] };
  }

  function init(root) {
    var ruler = root.querySelector(".eqc-ruler");
    var scale = root.querySelector(".eqc-scale");
    var input = root.querySelector(".eq-time");
    var out = root.querySelector(".eqc-out");
    var tcap = root.querySelector(".eqc-tcap");
    var ratingEl = root.querySelector(".eqc-rating");
    var srcdist = root.querySelector(".eqc-srcdist");
    var status = root.querySelector(".eqc-status");
    var selPool = root.querySelector(".eq-pool");
    var fieldKey = root.querySelector(".eqc-field-key");

    var st = {
      sport: root.dataset.sport || "XC",
      dist: parseFloat(root.dataset.dist) || null,
      diff: root.dataset.diff === "" ? null : parseFloat(root.dataset.diff),
      course: root.dataset.course || "",
      lo: parseFloat(root.dataset.lo) || null,
      hi: parseFloat(root.dataset.hi) || null,
      target: 5000, tsport: "TF", tlabel: "5K",
      points: [], hs: null, tmin: 0, tmax: 0, cur: null, seq: 0, sc: null
    };

    function pressed(group) {
      return root.querySelector(group + " .eqc-pill[aria-pressed='true']");
    }
    function readTarget() {
      var b = pressed(".eqc-targets");
      if (!b) return;
      st.target = parseFloat(b.dataset.target);
      st.tsport = b.dataset.tsport;
      st.tlabel = b.textContent.trim();
    }
    function readDist() {
      var b = pressed(".eqc-dists");
      if (!b) return;
      st.dist = parseFloat(b.dataset.dist);
      st.diff = b.dataset.diff === "" ? null : parseFloat(b.dataset.diff);
    }
    readTarget();
    readDist();

    function srcTenths() { return st.dist < 3000; }
    function tgtTenths() { return st.target < 3000; }
    function hsOn() {
      return !!(window.rcScale && window.rcScale.mode === "hs" &&
                document.querySelector("button[data-scale]") && st.hs);
    }
    function targetCaption() {
      if (st.tsport === "XC") return "XC 5K · average course";
      return "On a track · " + st.tlabel;
    }

    function centre() { return st.tmin + ruler.scrollLeft / st.sc.pxps; }
    function goTo(t) {
      t = Math.max(st.tmin, Math.min(st.tmax, t));
      ruler.scrollLeft = (t - st.tmin) * st.sc.pxps;
      show(t);
    }

    function show(t) {
      if (!st.points.length) return;
      if (t == null) t = centre();
      st.cur = t;
      var tt = interp(st.points, t, 2);
      var r = interp(st.points, t, 0);
      out.textContent = fmt(tt, tgtTenths());
      tcap.textContent = targetCaption();
      if (r) {
        var hs = hsOn();
        ratingEl.textContent = (hs ? "HS-equivalent rating " : "Rating ") +
                               (hs ? r * st.hs : r).toFixed(1);
      } else {
        ratingEl.textContent = "";
      }
      if (document.activeElement !== input) input.value = fmt(t, srcTenths());
      ruler.setAttribute("aria-valuetext", fmt(t, srcTenths()) + " here is " +
                         fmt(tt, tgtTenths()) + ", " + targetCaption());
    }

    function draw() {
      var pts = st.points, sc = st.sc = scaleFor(st.dist);
      st.tmin = Math.floor(pts[0][1] / sc.minor) * sc.minor;
      st.tmax = Math.ceil(pts[pts.length - 1][1] / sc.minor) * sc.minor;
      var pad = ruler.clientWidth / 2;
      scale.style.width = ((st.tmax - st.tmin) * sc.pxps + 2 * pad) + "px";
      function x(t) { return pad + (t - st.tmin) * sc.pxps; }
      var html = [];
      if (st.lo && st.hi && st.hi > st.lo) {
        html.push('<div class="eqc-field" style="left:' + x(st.lo) + "px;width:" +
                  (st.hi - st.lo) * sc.pxps + 'px"></div>');
        if (fieldKey) fieldKey.hidden = false;
      }
      for (var t = st.tmin; t <= st.tmax; t += sc.minor) {
        var cls = t % sc.major === 0 ? "eqc-t eqc-t3" :
                  t % sc.mid === 0 ? "eqc-t eqc-t2" : "eqc-t";
        html.push('<i class="' + cls + '" style="left:' + x(t) + 'px"></i>');
        if (t % sc.major === 0) {
          html.push('<b class="eqc-l eqc-l-src" style="left:' + x(t) + 'px">' +
                    fmt(t, false) + "</b>");
          html.push('<b class="eqc-l eqc-l-tgt" style="left:' + x(t) + 'px">' +
                    fmt(interp(pts, t, 2), tgtTenths()) + "</b>");
        }
      }
      scale.innerHTML = html.join("");
    }

    function load(keep) {
      if (!st.dist) {
        status.hidden = false;
        status.textContent = "No distance here, so no equivalents.";
        return;
      }
      srcdist.textContent = Math.round(st.dist) + "m";
      var q = new URLSearchParams({ pool: selPool.value, dist: String(st.dist),
                                    target: String(st.target), sport: st.sport,
                                    tsport: st.tsport });
      if (st.diff != null && isFinite(st.diff)) q.set("difficulty", String(st.diff));
      if (st.course) q.set("course", st.course);
      var seq = ++st.seq;
      root.classList.add("eqc-loading");
      fetch("/api/equivalence?" + q.toString())
        .then(function (r) { return r.json(); })
        .then(function (body) {
          if (seq !== st.seq) return;               // a newer request won
          root.classList.remove("eqc-loading");
          if (!body.points || !body.points.length) {
            status.hidden = false;
            status.textContent = body.error || "No equivalents for this group here.";
            return;
          }
          status.hidden = true;
          st.points = body.points;
          st.hs = body.hs_factor || null;
          draw();
          // open on the race's winner, else a rating-100 runner (the group's
          // average) -- points run fastest first, rating descending
          var avg = null;
          for (var i = 0; i < st.points.length; i++) {
            if (st.points[i][0] <= 100) { avg = st.points[i][1]; break; }
          }
          var start = keep != null ? keep : st.lo ? st.lo : avg;
          goTo(start != null ? start : st.points[0][1]);
        })
        .catch(function () {
          root.classList.remove("eqc-loading");
          status.hidden = false;
          status.textContent = "Could not load the equivalents.";
        });
    }

    // --- interaction ---------------------------------------------------- //
    var raf = null;
    ruler.addEventListener("scroll", function () {
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = null; show(); });
    });
    ruler.addEventListener("keydown", function (e) {
      var step = (e.shiftKey ? 10 : 1) * (st.sc && st.sc.minor < 5 ? 0.5 : 1);
      if (e.key === "ArrowLeft") { goTo(centre() - step); e.preventDefault(); }
      if (e.key === "ArrowRight") { goTo(centre() + step); e.preventDefault(); }
    });
    var drag = null;
    ruler.addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse") return;       // touch scrolls natively
      drag = { x: e.clientX, left: ruler.scrollLeft };
      ruler.classList.add("is-dragging");
      ruler.setPointerCapture(e.pointerId);
    });
    ruler.addEventListener("pointermove", function (e) {
      if (drag) ruler.scrollLeft = drag.left - (e.clientX - drag.x);
    });
    function endDrag() { drag = null; ruler.classList.remove("is-dragging"); }
    ruler.addEventListener("pointerup", endDrag);
    ruler.addEventListener("pointercancel", endDrag);

    function commit() {
      var t = parseTime(input.value);
      if (t != null) goTo(t); else show(st.cur);
    }
    input.addEventListener("change", commit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); commit(); }
    });
    input.addEventListener("focus", function () { input.select(); });

    function pillGroup(sel, onPick) {
      var box = root.querySelector(sel);
      if (!box) return;
      box.addEventListener("click", function (e) {
        var b = e.target.closest(".eqc-pill");
        if (!b || b.getAttribute("aria-pressed") === "true") return;
        var all = box.querySelectorAll(".eqc-pill");
        for (var i = 0; i < all.length; i++) all[i].setAttribute("aria-pressed", "false");
        b.setAttribute("aria-pressed", "true");
        onPick();
      });
    }
    pillGroup(".eqc-targets", function () { readTarget(); load(st.cur); });
    pillGroup(".eqc-dists", function () { readDist(); load(null); });
    // ! KEEP THE TIME ON A GROUP CHANGE (owner, 2026-09-25: "changing the
    //   pool changed the predicted time"). It used to reopen on the new
    //   group's average runner, so the course time itself jumped. The time
    //   here stays put; only its equivalent may move, and by a little -- each
    //   group has its own time-over-distance curve.
    selPool.addEventListener("change", function () { load(st.cur); });
    document.addEventListener("rc-scale-change", function () { show(st.cur); });
    window.addEventListener("resize", function () {
      if (st.points.length) { var t = st.cur; draw(); goTo(t); }
    });
    load(null);
  }

  function boot() {
    var nodes = document.querySelectorAll(".eqc");
    for (var i = 0; i < nodes.length; i++) init(nodes[i]);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
