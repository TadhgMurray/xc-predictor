"""The sitemap rebuild never leaves the live index naming a missing file."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import build_sitemap as B  # noqa: E402


def test_old_files_go_only_after_the_new_index():
    with tempfile.TemporaryDirectory() as d:
        B.writeSitemaps({"a": [("/x", None)], "b": [("/y", None)]}, d, "https://e")
        assert os.path.exists(os.path.join(d, "sitemap-a.xml.gz"))
        B.writeSitemaps({"b": [("/y", None)], "c": [("/z", None)]}, d, "https://e")
        names = set(os.listdir(d))
        assert names == {"sitemap.xml", "sitemap-b.xml.gz", "sitemap-c.xml.gz"}
        idx = open(os.path.join(d, "sitemap.xml")).read()
        assert "sitemap-a" not in idx and "sitemap-c.xml.gz" in idx
