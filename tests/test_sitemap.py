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
        files = BS.writeSitemaps(by_kind, d, "https://racecast.co")
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
        files = BS.writeSitemaps({"pages": [("/", None)]}, d, "https://racecast.co")
        assert sorted(os.listdir(d)) == ["sitemap-pages.xml", "sitemap.xml"]


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
