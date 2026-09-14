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
    assert 'ALTER TABLE search_index_new RENAME TO search_index' in src
    assert 'ALTER INDEX idx_search_new_prefix RENAME TO idx_search_prefix' in src
    assert src.index("DROP TABLE IF EXISTS search_index_new") < src.index("_LOADERS[args.only](conn)")
