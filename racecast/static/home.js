// home.js -- the home snapshot's level tabs. No data fetching: every table
// is already in the page. This only flips which level is VISIBLE, via CSS.
//
// ★ ONE ROW OF LEVEL TABS, NOTHING ELSE (owner, 2026-10-10). The sport and
//   scope toggles and the pool chips are gone; race-page.js runs the board
//   tabs (athletes / performances / teams). A level tab sets data-level on
//   .rankings, and race.css shows that level's table in every panel, so
//   switching boards keeps the level you were reading.

(function () {
    var rankings = document.querySelector('.rankings');
    if (!rankings) return;
    var bar = rankings.querySelector('.hm-levels');
    if (!bar) return;

    function paint() {
        var current = rankings.getAttribute('data-level');
        bar.querySelectorAll('[data-level]').forEach(function (b) {
            var on = b.getAttribute('data-level') === current;
            b.classList.toggle('is-on', on);          // the site's .seg-btn.is-on
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    bar.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-level]');
        if (!btn) return;
        rankings.setAttribute('data-level', btn.getAttribute('data-level'));
        paint();
    });

    // ! THE LEVELS HIDE ONLY ONCE THIS HAS RUN: without the script every
    //   level's table shows, one under another, and the tabs are inert
    rankings.classList.add('hm-levels-on');
    paint();
})();
