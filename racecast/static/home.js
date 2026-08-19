// home.js -- landing-page toggles. No data fetching: every board is already
// in the page. These handlers only flip which one is VISIBLE, via CSS.

(function () {
    var rankings = document.querySelector('.rankings');
    if (!rankings) return;

    // ---- sport / scope: set an attribute on .rankings; CSS reads it -------
    // .board-pair is shown by CSS only when its data-sport/data-scope match
    // the ones on .rankings. So changing the attribute swaps the visible pair.
    function wireToggle(name) {
        var group = rankings.querySelector('[data-toggle="' + name + '"]');
        if (!group) return;

        function paint() {
            var current = rankings.getAttribute('data-' + name);
            group.querySelectorAll('button').forEach(function (b) {
                b.classList.toggle('is-active', b.getAttribute('data-value') === current);
            });
        }

        group.addEventListener('click', function (e) {
            var btn = e.target.closest('button');
            if (!btn) return;
            rankings.setAttribute('data-' + name, btn.getAttribute('data-value'));
            paint();
        });

        paint();  // reflect the initial state on load
    }

    wireToggle('sport');
    wireToggle('scope');

    // ---- pool chips: hide/show one pool across ALL boards -----------------
    // A chip toggles a body class `hide-<pool>`; CSS uses it to collapse every
    // .pool-block for that pool at once. Filter, not selector -- all pools
    // start visible and you switch ones OFF.
    rankings.querySelectorAll('.chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
            var pool = chip.getAttribute('data-pool');
            chip.classList.toggle('is-on');
            document.body.classList.toggle('hide-' + pool, !chip.classList.contains('is-on'));
        });
    });
})();
