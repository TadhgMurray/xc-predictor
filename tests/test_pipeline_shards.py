"""Issue 140: course pages in shards, page indexes first, the search index
rebuilt every run into a shadow table.

    python -m pytest -q tests/test_pipeline_shards.py
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_pipeline_order_and_shards():
    sh = read("deploy", "run_pipeline.sh")
    assert sh.index("11b_indexes") < sh.index("12_courses") < sh.index("step 12b_prepare")
    assert (sh.index("step 12b_prepare") < sh.index("shards 12b_course_pages \"$XCP_COURSE_SHARDS\"")
            < sh.index("step 12b_finish"))
    assert "shards() {" in sh and '--shard "$k/$n"' in sh
    assert "13c_search_index" in sh and "search_index.py --only units" not in sh
    # after 11b (its long reads would stall 11b's concurrent index builds),
    # collected before the sitemap, which reads its meet_agg tables (2026-11-12)
    assert sh.index("step 11b_indexes") < sh.index("bgstep 13c_search_index")
    assert sh.index("bgjoin 13c_search_index") < sh.index("_G13=(13d_sitemap")


def test_course_boards_cli_modes():
    src = read("racecast", "build_course_boards.py")
    for flag in ('"--prepare"', '"--shard"', '"--finish"'):
        assert flag in src
    assert "courses[shard_k::shard_n]" in src
    assert "ORDER BY n DESC, course_name" in src, "every shard sees the same list"
    assert "if shard_n is None:" in src, "a shard never swaps"
    assert "refusing to swap" in src


def test_search_index_swaps():
    src = read("racecast", "search_index.py")
    assert 'INSERT INTO {_TARGET}' in src
    # through dbfast.swapTable since the sweep of 2026-10-10 (D5/D6)
    assert 'swapTable(conn, "search_index", renames=[' in src
    assert '("idx_search_new_prefix", "idx_search_prefix")' in src
    # the loaders run in one loop now (each prints its own time; --only
    # skips the rest), and the shadow is dropped before that loop starts
    main = src[src.index("def main("):]
    assert (main.index('_dropShadow(cur, "search_index_new")')
            < main.index("for key, fn in _LOADERS.items():"))


def test_a_skipped_shard_step_skips_its_prepare_and_finish():
    # run 20261108_155220 (--fast): 12b_course_pages skipped, 12b_prepare
    # emptied the shadow, 12b_finish refused to swap it and FAILED the run
    import subprocess, tempfile, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sh = open(os.path.join(root, "deploy", "run_pipeline.sh")).read()
    for g in ("12b_course_pages", "12c_venue_pages"):
        i = sh.index(f"if willRun {g}; then")
        block = sh[i:sh.index("\nfi\n", i)]
        assert "prepare" in block and "finish" in block and "else" in block
    assert "12b_course_pages" in sh[sh.index("FAST_SKIP="):sh.index('if [ "$FAST" -eq 1 ]')]
    # willRun answers --skip and --from like the steps do
    a = sh.index("beforeFrom() {"); b = sh.index("\n}\n", a) + 3
    w = sh.index("willRun() {"); we = sh.index("\n", w)
    fns = ('skipped() { case "$SKIP" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }\n'
           + sh[sh.index("_ALWAYS="):sh.index("\n", sh.index("_ALWAYS="))] + "\n"
           + sh[a:b] + sh[w:we] + "\n")
    def run(skip, frm):
        out = subprocess.run(["bash", "-c", fns + f'SKIP="{skip}"; FROM={frm}; willRun 12b_course_pages && echo yes || echo no'],
                             capture_output=True, text=True)
        return out.stdout.strip()
    assert run(",12b_course_pages,", 0) == "no"
    assert run("", 13) == "no"
    assert run("", 12) == "yes"


def test_the_search_index_runs_beside_the_tail_and_is_joined_before_its_reader():
    # 2026-11-10: 729 s in line; nothing reads search_index until the
    # watchdog (17d) and the prewarm (19b)
    sh = read("deploy", "run_pipeline.sh")
    assert (sh.index("bgstep 13c_search_index") < sh.index("bgjoin 13c_search_index")
            < sh.index("bgstep 17d_watchdog") < sh.index("step 19b_prewarm"))
    sm = read("racecast", "build_sitemap.py")
    assert "search_index " not in sm.replace("from search_index import", "")


def test_state_odds_runs_beside_the_tail_after_its_only_reader():
    # 2026-11-10: 810 s in line; 13g_social reads the last run's tables
    sh = read("deploy", "run_pipeline.sh")
    assert sh.index("step 13g_social") < sh.index("bgstep 13i_state_odds") < sh.index("\nbgwait\n")
    assert "step 13i_state_odds" not in sh.replace("bgstep 13i_state_odds", "")
