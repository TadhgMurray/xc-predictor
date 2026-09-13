"""robots.txt, the canonical tag and the sitemap (Google, 2026-09-02).

    python -m pytest -q tests/test_sitemap.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))

import build_sitemap as BS                                       # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_files_split_at_the_cap_and_the_index_names_them():
    with tempfile.TemporaryDirectory() as d:
        by_kind = {
            "pages": [("/", None), ("/rankings", None)],
            "athletes": [(f"/athlete/{i}", "2025-11-22") for i in range(BS.MAX_PER_FILE + 5)],
            "schools": [("/school/De%20La%20Salle", None)],
        }
        files = BS.writeSitemaps(by_kind, d, "https://racecast.co",
                                 gzipped=False)
        assert files == ["sitemap-pages.xml", "sitemap-athletes-1.xml",
                         "sitemap-athletes-2.xml", "sitemap-schools.xml"]
        idx = io.open(os.path.join(d, "sitemap.xml")).read()
        assert idx.count("<sitemap>") == 4
        assert "https://racecast.co/static/sitemaps/sitemap-athletes-2.xml" in idx
        a2 = io.open(os.path.join(d, "sitemap-athletes-2.xml")).read()
        assert a2.count("<url>") == 5 and "<lastmod>2025-11-22</lastmod>" in a2
        sc = io.open(os.path.join(d, "sitemap-schools.xml")).read()
        assert "<loc>https://racecast.co/school/De%20La%20Salle</loc>" in sc
        # a rebuild replaces, never accumulates
        files = BS.writeSitemaps({"pages": [("/", None)]}, d,
                                 "https://racecast.co", gzipped=False)
        assert sorted(os.listdir(d)) == ["sitemap-pages.xml", "sitemap.xml"]


def test_the_files_ship_gzipped():
    """⚠ THE SITEMAP WAS THE CRAWL (2026-09-13). Bing had made 501 requests
    to this site: about 450 were these files at FOUR MEGABYTES each, and
    exactly one was a page. gzip is allowed by sitemaps.org and taken by
    both engines, and it is roughly twenty times smaller."""
    import gzip
    with tempfile.TemporaryDirectory() as d:
        rows = [(f"/race/xc/{i}/1", None) for i in range(2000)]
        files = BS.writeSitemaps({"races": rows}, d, "https://racecast.co")
        assert files == ["sitemap-races.xml.gz"]
        path = os.path.join(d, files[0])
        raw = gzip.open(path, "rb").read().decode("utf-8")
        assert raw.count("<url>") == 2000
        assert "https://racecast.co/race/xc/7/1" in raw
        assert os.path.getsize(path) * 5 < len(raw), "barely compressed?"
        # the INDEX stays readable: robots.txt names it and people open it
        idx = io.open(os.path.join(d, "sitemap.xml")).read()
        assert "sitemap-races.xml.gz" in idx
        # a rebuild must clear the old .gz too, or they pile up for ever
        BS.writeSitemaps({"pages": [("/", None)]}, d, "https://racecast.co")
        assert sorted(os.listdir(d)) == ["sitemap-pages.xml.gz", "sitemap.xml"]


def test_the_same_urls_produce_the_same_bytes():
    """An unchanged sitemap keeps its ETag and is not re-fetched -- so the
    gzip header must not carry a timestamp."""
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        rows = [("/a", None), ("/b", "2025-01-01")]
        BS.writeSitemaps({"pages": rows}, d1, "https://racecast.co")
        BS.writeSitemaps({"pages": rows}, d2, "https://racecast.co")
        a = io.open(os.path.join(d1, "sitemap-pages.xml.gz"), "rb").read()
        b = io.open(os.path.join(d2, "sitemap-pages.xml.gz"), "rb").read()
        assert a == b


def test_xml_escapes_and_empty_kinds():
    xml = BS.urlsetXml([("/course/Mt.%20SAC%20%26%20Hills", None)])
    assert "&amp;" not in xml and "%26" in xml, "paths arrive URL-encoded, not entity-escaped"
    xml = BS.urlsetXml([("/a<b", None)])
    assert "&lt;" in xml
    assert BS.chunk([]) == [[]]


def test_app_serves_robots_canonical_and_the_index():
    app = read("racecast", "app.py")
    assert '@app.route("/robots.txt")' in app and "Sitemap: {SITE_ORIGIN}/sitemap.xml" in app
    assert '@app.route("/sitemap.xml")' in app
    assert 'app.jinja_env.globals["site_origin"]' in app
    meta = read("racecast", "templates", "_meta.html")
    assert '<link rel="canonical" href="{{ site_origin ~ (meta_path | default(request.path)) }}">' in meta
    assert "request.url_root" not in meta
    sh = read("deploy", "run_pipeline.sh")
    assert "13d_sitemap" in sh and sh.index("13c_search_index") < sh.index("13d_sitemap")
    assert "racecast/static/sitemaps/" in read(".gitignore")


def test_indexnow_reads_the_gzipped_files():
    """It is the fast path to Bing, and reading only ".xml" made it find
    nothing at all -- which looks exactly like having nothing to say."""
    src = read("scripts", "indexnow_submit.py")
    assert 'name.endswith(".xml.gz")' in src
    assert "gzip.open(path" in src
