// topbar-search.js -- the live search dropdown. Loaded on every page.
(function () {
    function init() {
        var input = document.getElementById('search-input');
        var box   = document.getElementById('search-results');
        if (!input || !box) return;

        var timer = null, seq = 0;
        (function () {
            var input = document.getElementById('search-input');
            var box   = document.getElementById('search-results');
            if (!input || !box) return;

            var timer = null, seq = 0;

            input.addEventListener('input', function () {
                var q = input.value.trim();
                clearTimeout(timer);
                if (q.length < 2) { box.innerHTML = ''; return; }
                var mySeq = ++seq;
                timer = setTimeout(function () {
                    fetch('/search/api?q=' + encodeURIComponent(q))
                        .then(function (r) { return r.json(); })
                        .then(function (rows) {
                            if (mySeq !== seq) return;
                            render(rows, q);
                        });
                }, 150);
            });

            function render(rows, q) {
                var more = '<a class="sr-more" href="/search?q=' +
                        encodeURIComponent(q) + '">See all results →</a>';
                if (!rows.length) { box.innerHTML = more; return; }
                var items = rows.map(function (r) {
                    var sub = r.sublabel ? '<span class="sr-sub">' + esc(r.sublabel) + '</span>' : '';
                    return '<a class="sr-item" href="' + r.link + '">' +
                        '<span class="sr-kind">' + r.kind + '</span>' +
                        '<span class="sr-label">' + esc(r.label) + '</span>' + sub + '</a>';
                }).join('');
                box.innerHTML = '<div class="sr-scroll">' + items + '</div>' + more;
            }

            // Enter -> full results page
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    var q = input.value.trim();
                    if (q) window.location = '/search?q=' + encodeURIComponent(q);
                }
            });

            document.addEventListener('click', function (e) {
                if (!input.contains(e.target) && !box.contains(e.target)) box.innerHTML = '';
            });

            function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
        })();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();