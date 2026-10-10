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
    assert sh.index("11b_indexes") < sh.index("12_courses") < sh.index("12b_prepare")
    assert sh.index("12b_prepare") < sh.index("shards 12b_course_pages \"$XCP_COURSE_SHARDS\"") < sh.index("12b_finish")
    assert "shards() {" in sh and '--shard "$k/$n"' in sh
    assert "13c_search_index" in sh and "search_index.py --only units" not in sh
    assert sh.index("13b_pool_consts") < sh.index("13c_search_index")


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
    assert (main.index("DROP TABLE IF EXISTS search_index_new")
            < main.index("for key, fn in _LOADERS.items():"))
