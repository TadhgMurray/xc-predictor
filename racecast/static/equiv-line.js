/* equiv-line.js -- the track-equivalents number line on race and course pages.
 *
 * ★ WHAT IT SHOWS. A horizontal strip of times on this course; under every
 *   tick, the same fitness on a typical track at the distance the reader
 *   picked. Scroll (or drag, or arrow-key) the strip: the time under the
 *   centre marker is read out with its track equivalent. The numbers come
 *   from /api/equivalence -- one point per rating, 40 to 170 -- and
 *   everything between two points is a straight-line interpolation, which at
 *   one rating point apart is well under a second.
 *
 * ! ONE FETCH PER (course distance, group, track distance). Scrolling never
 *   asks the server anything.
 */
(function () {
  "use strict";

  var PX_PER_SEC = 6;            // strip scale: 10 s of course time = 60 px
  var TICK = 10;                 // a tick every 10 s of course time
  var LABEL = 30;                // a label every 30 s

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

  // piecewise-linear lookup along the course-time column
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
    var f = (t - a[1]) / (b[1] - a[1]);
    return a[col] + f * (b[col] - a[col]);
  }

  function init(root) {
    var strip = root.querySelector(".eq-strip");
    var track = root.querySelector(".eq-track");
    var out = root.querySelector(".eq-out");
    var tlabel = root.querySelector(".eq-tlabel");
    var rating = root.querySelector(".eq-rating");
    var input = root.querySelector(".eq-time");
    var status = root.querySelector(".eq-status");
    var selTarget = root.querySelector(".eq-target");
    var selPool = root.querySelector(".eq-pool");
    var selDist = root.querySelector(".eq-dist");

    var state = {
      dist: parseFloat(root.dataset.dist) || null,
      diff: root.dataset.diff === "" ? null : parseFloat(root.dataset.diff),
      course: root.dataset.course || "",
      lo: parseFloat(root.dataset.lo) || null,
      hi: parseFloat(root.dataset.hi) || null,
      points: [], tmin: 0, tmax: 0, pad: 0, current: null, seq: 0
    };
    if (selDist && !state.dist) {
      var o0 = selDist.options[selDist.selectedIndex];
      state.dist = parseFloat(o0.value);
      state.diff = o0.dataset.diff === "" ? null : parseFloat(o0.dataset.diff);
    }

    function targetLabel() {
      return selTarget.options[selTarget.selectedIndex].text;
    }
    function tenths() { return parseFloat(selTarget.value) < 3000; }

    function centreTime() {
      return state.tmin + strip.scrollLeft / PX_PER_SEC;
    }
    function scrollTo(t) {
      t = Math.max(state.tmin, Math.min(state.tmax, t));
      strip.scrollLeft = (t - state.tmin) * PX_PER_SEC;
      update(t);
    }

    function update(t) {
      if (!state.points.length) return;
      if (t == null) t = centreTime();
      state.current = t;
      var tt = interp(state.points, t, 2);
      var r = interp(state.points, t, 0);
      out.textContent = fmt(tt, tenths());
      tlabel.textContent = "on a track " + targetLabel();
      rating.textContent = r ? "· rating " + Math.round(r) : "";
      if (document.activeElement !== input) input.value = fmt(t, false);
      strip.setAttribute("aria-valuetext",
        fmt(t, false) + " here, " + fmt(tt, tenths()) + " on a track " + targetLabel());
    }

    function draw() {
      var pts = state.points;
      state.tmin = Math.floor(pts[0][1] / TICK) * TICK;
      state.tmax = Math.ceil(pts[pts.length - 1][1] / TICK) * TICK;
      state.pad = strip.clientWidth / 2;
      var width = (state.tmax - state.tmin) * PX_PER_SEC;
      // ticks sit at pad + offset, so the strip can scroll the first and
      // last time right under the centre marker
      track.style.width = (width + 2 * state.pad) + "px";
      var html = [];
      if (state.lo && state.hi && state.hi > state.lo) {
        var x0 = state.pad + (state.lo - state.tmin) * PX_PER_SEC;
        var w = (state.hi - state.lo) * PX_PER_SEC;
        html.push('<div class="eq-field" style="left:' + x0 + "px;width:" + w + 'px"></div>');
        var k = root.querySelector(".eq-key-field-wrap");
        if (k) k.hidden = false;
      }
      for (var t = state.tmin; t <= state.tmax; t += TICK) {
        var x = state.pad + (t - state.tmin) * PX_PER_SEC;
        var major = t % LABEL === 0;
        html.push('<div class="eq-tick' + (major ? " eq-major" : "") +
                  '" style="left:' + x + 'px"></div>');
        if (major) {
          html.push('<div class="eq-lab eq-lab-course" style="left:' + x + 'px">' +
                    fmt(t, false) + "</div>");
          html.push('<div class="eq-lab eq-lab-track" style="left:' + x + 'px">' +
                    fmt(interp(pts, t, 2), tenths()) + "</div>");
        }
      }
      track.innerHTML = html.join("");
    }

    function load(keepTime) {
      if (!state.dist) {
        status.hidden = false;
        status.textContent = "No distance for this course, so no equivalents.";
        return;
      }
      var q = new URLSearchParams({
        pool: selPool.value, dist: String(state.dist), target: selTarget.value
      });
      if (state.diff != null && isFinite(state.diff)) q.set("difficulty", String(state.diff));
      if (state.course) q.set("course", state.course);
      var seq = ++state.seq;
      fetch("/api/equivalence?" + q.toString())
        .then(function (r) { return r.json(); })
        .then(function (body) {
          if (seq !== state.seq) return;            // a newer request won
          if (!body.points || !body.points.length) {
            status.hidden = false;
            status.textContent = body.error || "No equivalents for this group here.";
            return;
          }
          status.hidden = true;
          state.points = body.points;
          draw();
          // open on the race's winner, else on a rating-100 runner (the
          // group's average) -- points run fastest first, rating descending
          var avg = null;
          for (var i = 0; i < state.points.length; i++) {
            if (state.points[i][0] <= 100) { avg = state.points[i][1]; break; }
          }
          var start = keepTime != null ? keepTime : state.lo ? state.lo : avg;
          if (start == null || !isFinite(start)) start = state.points[0][1];
          scrollTo(start);
        })
        .catch(function () {
          status.hidden = false;
          status.textContent = "Could not load the equivalents.";
        });
    }

    var raf = null;
    strip.addEventListener("scroll", function () {
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = null; update(); });
    });
    strip.addEventListener("keydown", function (e) {
      var step = e.shiftKey ? 10 : 1;
      if (e.key === "ArrowLeft") { scrollTo(centreTime() - step); e.preventDefault(); }
      if (e.key === "ArrowRight") { scrollTo(centreTime() + step); e.preventDefault(); }
    });
    // drag with a mouse, as well as the touch and wheel scrolling the strip
    // already has
    var drag = null;
    strip.addEventListener("mousedown", function (e) {
      drag = { x: e.clientX, left: strip.scrollLeft };
      strip.classList.add("eq-dragging");
      e.preventDefault();
    });
    window.addEventListener("mousemove", function (e) {
      if (drag) strip.scrollLeft = drag.left - (e.clientX - drag.x);
    });
    window.addEventListener("mouseup", function () {
      drag = null;
      strip.classList.remove("eq-dragging");
    });
    input.addEventListener("change", function () {
      var t = parseTime(input.value);
      if (t != null) scrollTo(t);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { input.blur(); var t = parseTime(input.value); if (t != null) scrollTo(t); }
    });
    selTarget.addEventListener("change", function () { load(state.current); });
    selPool.addEventListener("change", function () { load(null); });
    if (selDist) {
      selDist.addEventListener("change", function () {
        var o = selDist.options[selDist.selectedIndex];
        state.dist = parseFloat(o.value);
        state.diff = o.dataset.diff === "" ? null : parseFloat(o.dataset.diff);
        load(null);
      });
    }
    window.addEventListener("resize", function () {
      if (state.points.length) { var t = state.current; draw(); scrollTo(t); }
    });
    load(null);
  }

  function boot() {
    var nodes = document.querySelectorAll(".eqline");
    for (var i = 0; i < nodes.length; i++) init(nodes[i]);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
