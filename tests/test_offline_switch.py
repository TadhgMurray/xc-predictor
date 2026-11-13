"""deploy/offline.sh (owner, 2026-11-13): take the site offline as a 503 with
Retry-After and a short page, never a dead origin; and back."""
import os
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(_ROOT, "deploy", "offline.sh")


def test_the_switch_parses_and_sends_a_503_with_retry_after():
    subprocess.run(["bash", "-n", SH], check=True)
    src = open(SH).read()
    assert "if (-f $FLAG) { return 503; }" in src
    assert "add_header Retry-After 86400 always;" in src
    assert "xcp-backup" not in src.split("TIMERS=")[1].split("\n")[0]   # the backup keeps running
    assert "systemctl restart" not in src


def test_the_page_names_no_feed_and_no_person():
    page = open(os.path.join(_ROOT, "deploy", "offline", "index.html")).read().lower()
    assert "taking a break" in page
    for word in ("athletic.net", "tfrrs", "@gmail", "@"):
        assert word not in page
