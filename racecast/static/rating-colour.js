/* rating-colour.js -- the rating, coloured by what it means.

   ★ OWNER, 2026-10-07: "you just added color randomly, it is just small
     bits. I was hoping for something more meaningful." The rating is the
     site's one number, so the colour carries it: one orange ramp, deeper
     for faster, in tiers of 10 points -- 10% faster than the pool average,
     the scale's own unit -- from 100 (the pool average) to 140 (national
     class). Below 100 stays uncoloured. Validated as an ordinal ramp
     (dataviz validate_palette.js --ordinal: monotone, visible steps, light
     end >= 2:1 on the page).

   It reads the number each .rv span is SHOWING, so the HS/own-pool toggle
   (scale-view.js, "rc-scale-change") recolours with it. */
(function () {
  function tier(v) {
    if (!(v >= 100)) return 0;
    return Math.min(5, Math.floor((v - 100) / 10) + 1);
  }
  window.rcTier = tier;
  function paint() {
    var spans = document.querySelectorAll("[data-rcolour] .rv");
    for (var i = 0; i < spans.length; i++) {
      var host = spans[i].closest("td") || spans[i].parentNode;
      var t = tier(parseFloat(spans[i].textContent));
      host.setAttribute("data-tier", String(t));
    }
  }
  document.addEventListener("rc-scale-change", function () { requestAnimationFrame(paint); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", paint);
  else paint();
})();
