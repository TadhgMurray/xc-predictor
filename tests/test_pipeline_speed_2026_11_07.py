"""The 2026-11-07 pipeline speedups (owner-approved; every change must give
the same data, only sooner).

    python -m pytest -q -p no:cacheprovider tests/test_pipeline_speed_2026_11_07.py

Three kinds of check:
  * the ORDER constraints each move rests on, read off deploy/run_pipeline.sh
    (and the dependency facts behind them, read off the steps' sources);
  * the shell helpers RUN for real with stub commands (a failure in a
    background chain still fails the run; a failed twin build is never
    swapped; the rankings streams partition the dates);
  * the Python changes on a scratch Postgres when XCP_TWIN_TEST_DSN names
    one -- old path against new path, row for row and catalog for catalog:
      XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
"""
import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config
import contextlib
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    _p = os.path.join(ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

DSN = os.environ.get("XCP_TWIN_TEST_DSN")


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


SH = read("deploy", "run_pipeline.sh")


def at(s, text=None):
    text = SH if text is None else text
    assert s in text, s
    return text.index(s)


def _func(name, text=None):
    text = SH if text is None else text
    m = re.search(r"^%s\(\) \{.*?^\}$" % re.escape(name), text, re.M | re.S)
    assert m, name
    return m.group(0)


# ==================================================================== #
# 1. THE TWINS: BUILT BESIDE grade_sanity, SWAPPED AFTER IT
# ==================================================================== #

def test_twin_build_runs_beside_grade_sanity_and_swaps_after_it():
    order = ["step 04a3b_merges", "bgstep 04_grade_sanity", "bgstep 04b_wheelchair",
             'step 04d_gender "$PY"',
             'step 04c_twins        "$PY" -u engine/twin_flag.py --write --no-swap',
             "bgjoin 04_grade_sanity", "bgjoin 04b_wheelchair",
             'step 04c_twins_swap "$PY" -u engine/twin_flag.py --swap-only',
             "bgstep 04h_season_levels", "_PEOPLE_FAILED=\"\"", "steps2 05_backfill_xc"]
    pos = [at(s) for s in order]
    assert pos == sorted(pos), order
    # the swap only after a build that ran and succeeded this run
    guard = SH[at("bgjoin 04b_wheelchair"):at("step 04c_twins_swap")]
    assert "if ! skipped 04c_twins && ! failed 04c_twins; then" in guard
    # the people gate counts the swap too
    gate = re.search(r"for _s in (03_pro_flag[^;]*); do", SH).group(1).split()
    assert {"04c_twins", "04c_twins_swap", "04_grade_sanity", "04b_wheelchair",
            "04d_gender"} <= set(gate)
    # 04b keeps its run-on-every---from rule and its --skip warning
    always = re.search(r'_ALWAYS="([^"]*)"', SH).group(1).split()
    assert "04b_wheelchair" in always
    assert "skipped 04b_wheelchair && _skipCost 04b_wheelchair" in SH


def test_the_twin_build_reads_nothing_grade_sanity_or_wheelchair_writes():
    # what grade_sanity / wheelchair_flag write
    theirs = r"\b(grade_fix|grade_untrusted|ntschool|wheelchair_person|wheelchair_race|wheelchair_result)\b"
    for f in (("engine", "twin_flag.py"), ("engine", "level_conflict.py")):
        sql = " ".join(re.findall(r"(?:FROM|JOIN)\s+[a-z_]+", read(*f)))
        assert not re.search(theirs, sql), (f, sql)
    # and it does read person_gender, which is why it waits for 04d
    assert "JOIN person_gender" in read("engine", "twin_flag.py")
    # grade_sanity reads the previous result_twin: the swap waits for it
    assert "result_twin" in read("engine", "grade_sanity.py")


def test_twin_flag_flags():
    tf = read("engine", "twin_flag.py")
    assert '"--no-swap"' in tf and '"--swap-only"' in tf
    assert "ap.error(\"--no-swap needs --write\")" in tf
    build = tf[tf.index("def build("):tf.index("def main(")]
    # the mark goes on after every rule is in, in the same commit
    assert build.index("_markComplete(cur)") > build.index("_buildPooled(")
    assert "COMMENT ON TABLE result_twin_new IS NULL" in build


# ==================================================================== #
# 2. THE DISTANCE CHECKS AS A BACKGROUND CHAIN
# ==================================================================== #

def test_distance_checks_chain_order():
    body = _func("_distanceChecks")
    names = ["04g0_gender_pin", "04g1_data_checks", "04g_audit_overrides",
             "04g1b_wrong_distance"]
    pos = [at("step " + n, body) for n in names]
    assert pos == sorted(pos)
    assert "04g2_dump_overrides" not in body
    order = ["step 04a3b_merges", "wchain 04g_checks _distanceChecks",
             "bgstep 04_grade_sanity", "bgjoin 04_grade_sanity",
             "bgchainjoin 04g_checks", "_PEOPLE_FAILED=\"\"",
             "step 04g2_dump_overrides", "step 04y_course_pins"]
    pos = [at(s) for s in order]
    assert pos == sorted(pos), order


def test_distance_checks_read_no_people_table_and_people_read_none_of_theirs():
    people_tables = r"(?:FROM|JOIN)\s+(grade_fix|grade_untrusted|ntschool|wheelchair_\w+|person_gender|result_twin)\b"
    for f in ("gender_pace_pin.py", "data_checks.py", "audit_overrides.py",
              "propose_distances.py", "wrong_distance.py"):
        src = read("engine", f)
        assert not re.search(people_tables, src), f
    theirs = r"(import corrections|from corrections|corrections\.py|data_issue|distance_pin)"
    for f in ("grade_sanity.py", "wheelchair_flag.py", "person_gender.py",
              "twin_flag.py", "level_conflict.py"):
        assert not re.search(theirs, read("engine", f)), f


# ==================================================================== #
# 3. THE REPORT TAIL
# ==================================================================== #

def test_report_steps_in_groups_and_the_evidence_wait_just_before_17c():
    tail = SH[at("# ---- the owner's go/no-go"):at("step 18_vacuum")]
    assert SH.count('wait "$EVID_PID"') == 1
    order = ['_REPORTS=(17_checklist "$PY -u scripts/run_checklist.py"',
             '17b_status "$PY -u scripts/print_status.py"',
             '_REPORTS+=(16b_lacctic', '_REPORTS+=(16c_kailo_record', '_REPORTS+=(16d_backtest',
             'stepsN "${_REPORTS[@]}"', "step 17a_page_timing",
             '_REPORTS=(17c0_run_changes', '_REPORTS+=(17e_scoreboard',
             'wait "$EVID_PID"', "step 17c_report"]
    pos = [at(s, tail) for s in order]
    assert pos == sorted(pos), order
    assert tail.count('stepsN "${_REPORTS[@]}"') == 2
    between = tail[at('wait "$EVID_PID"', tail):at("step 17c_report", tail)]
    assert "step " not in between.replace("step 17c_report", "")


def test_only_the_report_reads_the_evidence_logs():
    assert "08a_holdout.log" in read("scripts", "run_report.py")
    for f in ("run_checklist.py", "print_status.py", "lacctic_compare.py",
              "kailo_compare.py", "backtest_predictions.py", "page_timing.py",
              "run_changes.py", "scoreboard.py"):
        src = read("scripts", f)
        assert not re.search(r"08a_|08b_ladder|08d_diag|08c_anchor|ladder_logs", src), f


# ==================================================================== #
# 4. THE RANKINGS STREAMS
# ==================================================================== #

def test_balanced_seams_partition_inside_the_default_range():
    import build_ranking_results as B
    hist = ["2000-01-01", "2010-01-01", "2015-01-01", "2020-01-01", "2025-01-01"]
    assert B.balancedSeams(4, hist=hist) == ["2010-01-01", "2015-01-01", "2020-01-01"]
    assert B.balancedSeams(1, hist=hist) == []
    # a most-common date carrying half the rows: the seams step around it
    got = B.balancedSeams(4, mcv=["2024-10-01"], mcf=[0.5],
                          hist=["1995-01-01", "2005-01-01", "2015-01-01", "2026-01-01"])
    assert got == sorted(set(got)) and all("1990-01-01" < d < "2100-01-01" for d in got)
    assert "2024-10-01" in got or got[-1] > "2015-01-01"
    # junk and out-of-range dates are never seams
    got = B.balancedSeams(3, hist=["1980-01-01", "garbage", "1990-01-01", "2001-05-05", "2101-01-01"])
    assert all("1990-01-01" < d < "2100-01-01" and re.match(r"^\d{4}-\d\d-\d\d$", d) for d in got)
    assert B._pgArray('{2001-01-01,"2002-02-02"}') == ["2001-01-01", "2002-02-02"]


def _rankBlock():
    return SH[at("# not wide: the pre-11-07 shape"):at('XCP_STREAMS="$_RSTREAMS" stepsN "${_RANK[@]}"')]


def _runRank(tmp_path, seams_out, env=""):
    stub = tmp_path / "py"
    stub.write_text(f"#!/bin/sh\necho '{seams_out}'\n")
    stub.chmod(0o755)
    script = (f"DRY=0; LOGDIR={tmp_path}; PY={stub}; WIDE=1\n{env}\n"
              "beforeFrom() { return 1; }\n" + _func("rankSeamsOk") + "\n"
              + _rankBlock() + '\nfor x in "${_RANK[@]}"; do echo "ARG $x"; done\n')
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    args = [ln[4:] for ln in out.splitlines() if ln.startswith("ARG ")]
    return list(zip(args[0::2], args[1::2]))


def _ranges(pairs, sport):
    out = []
    for name, cmd in pairs:
        if f"--sport {sport}" not in cmd:
            continue
        since = re.search(r"--since (\S+)", cmd)
        until = re.search(r"--until (\S+)", cmd)
        out.append((name, since.group(1) if since else "1990-01-01",
                    until.group(1) if until else "2100-01-01"))
    return out


def test_rank_streams_cover_every_date_once(tmp_path):
    pairs = _runRank(tmp_path, "2009-08-01 2016-03-01 2021-09-15")
    for sport in ("XC", "TF"):
        r = _ranges(pairs, sport)
        assert [n for n, _a, _b in r] == [f"10_rankings_{sport.lower()}_{c}" for c in "abcd"]
        assert r[0][1] == "1990-01-01" and r[-1][2] == "2100-01-01"
        assert all(r[i][2] == r[i + 1][1] for i in range(len(r) - 1)), r
        assert all(a < b for _n, a, b in r)
        # the first keeps the stream's own default --since, the last its --until
        assert "--since" not in dict(pairs)[r[0][0]] and "--until" not in dict(pairs)[r[-1][0]]


@pytest.mark.parametrize("bad", ["", "2016-03-01 2009-08-01", "1989-01-01 2001-01-01",
                                 "2001-01-01 2001-01-01", "not a date"])
def test_rank_streams_fall_back_to_the_old_seam(tmp_path, bad):
    r = _ranges(_runRank(tmp_path, bad), "XC")
    assert r == [("10_rankings_xc_a", "1990-01-01", "2018-01-01"),
                 ("10_rankings_xc_b", "2018-01-01", "2100-01-01")]


def test_rank_seam_override(tmp_path):
    r = _ranges(_runRank(tmp_path, "2009-08-01", env='XCP_RANK_SEAM="2012-01-01"'), "TF")
    assert [x[2] for x in r] == ["2012-01-01", "2100-01-01"]


def test_rank_streams_run_together():
    assert 'XCP_STREAMS="$_RSTREAMS" stepsN "${_RANK[@]}"' in SH
    assert at("step 10_rankings_prepare") < at("_RANK=()") < at("step 10_rankings_finish")


# ==================================================================== #
# 5. THE ROWGUARD CHAIN, THE EARLY BLOCK, THE FOUR STEPS BESIDE THE PACK
# ==================================================================== #

def test_rowguard_chain_from_09d_joined_before_11b_and_16():
    body = _func("_rowguard")
    assert at("steps2 15_rowguard_diag_xc", body) < at("steps2 15_rowguard_triage_xc", body)
    order = ["step 09d_outliers", "wchain 15_rowguard _rowguard", "step 10_rankings_prepare",
             "bgchainjoin 15_rowguard", "step 11b_indexes", "step 16_rowguard_apply"]
    pos = [at(s) for s in order]
    assert pos == sorted(pos), order
    # nothing between the chain's start and 16 rewrites corrections.py
    mid = SH[at("wchain 15_rowguard _rowguard"):at("step 16_rowguard_apply")]
    for writer in ("audit_overrides.py", "gender_pace_pin.py", "data_checks.py", "apply_triage.py"):
        assert writer not in mid


def test_early_block_in_parallel_then_team_pool():
    i = at('stepsN 02b_tf_venues "$PY -u scripts/backfill_tf_venues.py --write"')
    assert i < at('03_pro_flag "$PY -u engine/pro_flag.py --skip-dist --write"') \
        < at('03b_age_bands "$PY -u engine/age_band_grades.py --write"') \
        < at("step 03a_team_pool") < at("bgstep 04_grade_sanity")
    # 03 reads no venue name and 03b no pro table: nothing in common
    assert "venue_name" not in re.sub(r"#.*", "", read("engine", "pro_flag.py"))
    assert "pro_athlete_season" not in read("engine", "age_band_grades.py")
    assert "pro_athlete_season" in read("engine", "build_team_pool.py")    # why 03a waits


def test_four_late_steps_beside_the_pack_joined_before_the_golive():
    names = ["13c0_person_redirects", "13c0_search_helpers", "10b0_tfrrs_link", "10e_meet_units"]
    for n in names:
        assert at("step 06c_impossible") < at(f"wbg {n} ") < at("step 07_pack") \
            < at(f"bgjoin {n}") < at("  step 08_golive"), n
        assert f"step {n} " not in SH.replace(f"wbg {n} ", ""), n
    # each still precedes its reader
    assert at("wbg 10b0_tfrrs_link") < at("stepsN 10b_school_ids")
    assert at("wbg 13c0_search_helpers") < at("step 13c_search_index")
    assert at("wbg 13c0_person_redirects") < at("step 13d_sitemap")
    # and none of them reads a rating
    for f in (("scripts", "person_redirects.py"), ("racecast", "build_search_helpers.py"),
              ("scripts", "link_tfrrs_to_anet.py"), ("racecast", "build_meet_units.py")):
        code = re.sub(r"#.*", "", read(*f))
        assert not re.search(r"\b(speed_rating|ranking_results|athlete_season|course_difficulties)\b", code), f


# ==================================================================== #
# 6. THE FILLS SIDE BY SIDE, THE LOCK PER TABLE, THE SCRUB
# ==================================================================== #

def test_fills_side_by_side_and_the_scrub_flag_only_after_this_runs_pack_and_golive():
    assert 'stepsN 09b_fill "$PY -u engine/fill_ratings.py --sport XC --venue$_FILL_FLAG"' in SH
    assert '09b_fill_tf "$PY -u engine/fill_ratings.py --sport TF$_FILL_FLAG"' in SH
    cond = SH[at('_FILL_FLAG=""'):at('if [ "$WIDE" = 1 ]; then\n  stepsN 09b_fill')]
    for need in ('[ "${XCP_JOINT_LIVE:-1}" = "1" ]', '[ "$FROM" -le 7 ]',
                 "! skipped 07_pack", "! failed 07_pack", "! skipped 08_golive", "! failed 08_golive"):
        assert need in cond, need
    fr = read("engine", "fill_ratings.py")
    assert '"--after-golive"' in fr
    main = fr[fr.index("def main("):]
    assert "for t in (_TABLE[sp] for sp in sports):" in main
    assert main.index("if args.after_golive and not args.dry_run:") < main.index("UPDATE {t} SET speed_rating = NULL")
    # the claim the flag rests on: both pack loaders read only rows with a time
    srdb = read("engine", "speed_ratings_db.py")
    assert srdb.count("WHERE r.normalized_time IS NOT NULL") >= 2
    assert "preserve_unmatched=False" in srdb


def _bash(script):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_fill_flag_cases():
    cond = SH[at('_FILL_FLAG=""'):at('if [ "$WIDE" = 1 ]; then\n  stepsN 09b_fill')]
    stubs = ('skipped() { case "$SKIP" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }\n'
             'failed() { case " $FAILED " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }\n')
    def flag(pre):
        return _bash(stubs + pre + "\n" + cond + 'echo "F=[$_FILL_FLAG]"').stdout.strip()
    assert flag('FROM=0; SKIP=","; FAILED=""') == "F=[ --after-golive]"
    assert flag('FROM=8; SKIP=","; FAILED=""') == "F=[]"
    assert flag('FROM=0; SKIP=",07_pack,"; FAILED=""') == "F=[]"
    assert flag('FROM=0; SKIP=","; FAILED=" 08_golive"') == "F=[]"
    assert flag('FROM=0; SKIP=","; FAILED=""; XCP_JOINT_LIVE=0') == "F=[]"


# ==================================================================== #
# THE SHELL HELPERS, RUN FOR REAL WITH STUB COMMANDS
# ==================================================================== #

_FUNCS = ("skipped", "failed", "beforeFrom", "_stampTo", "_skipCost", "step", "_live",
          "steps2", "stepsN", "bgstep", "bgjoin", "bgwait", "bgchain", "bgchainjoin",
          "wbg", "wchain")


def _lib(tmp_path):
    parts = [re.search(r'^_ALWAYS="[^"]*"$', SH, re.M).group(0),
             "BG_PIDS=(); BG_NAMES=(); BG_T0=(); BGC_PIDS=(); BGC_NAMES=()"]
    for f in _FUNCS:
        if f == "_live":
            m = re.search(r"^_live\(\) \(.*?^\)$", SH, re.M | re.S)
            parts.append(m.group(0))
        else:
            parts.append(_func(f))
    return (f"set -u -o pipefail\nFROM=0; SKIP=','; DRY=0; LOGDIR={tmp_path}; "
            f"SUMMARY={tmp_path}/summary.log; NICE=; FAILED=''; XCP_STREAMS=4; XCP_LIVE=0; "
            f"WIDE=${{WIDE:-1}}\n"
            + "\n".join(parts) + "\n")


def test_a_failure_in_a_background_chain_fails_the_run(tmp_path):
    script = _lib(tmp_path) + '''
_c() { step 04g0_x sh -c "exit 0"; step 04g1_y sh -c "exit 3"; step 04g_z sh -c "echo zz"; }
bgchain 04g_checks _c
step 04d_a sh -c "exit 0"
bgchainjoin 04g_checks
echo "FAILED=[$FAILED]"
_bad() { exit 1; }
bgchain 15_rowguard _bad
bgchainjoin 15_rowguard
echo "FAILED2=[$FAILED]"
'''
    out = _bash(script)
    assert "FAILED=[ 04g1_y]" in out.stdout, out
    assert "FAILED2=[ 04g1_y 15_rowguard]" in out.stdout, out
    assert "zz" in (tmp_path / "04g_z.log").read_text()        # the chain went on
    assert "04g1_y FAILED" in (tmp_path / "summary.log").read_text()


def _peopleBlock():
    return SH[at("skipped 04b_wheelchair && _skipCost 04b_wheelchair"):
              at("# ★ THE SEASON LEVELS, REBUILT EVERY RUN")]


def _runPeople(tmp_path, fail_build=False, skip="", wide=1):
    calls = tmp_path / "calls"
    stub = tmp_path / "py"
    stub.write_text(f'''#!/bin/sh
echo "$*" >> {calls}
case "$*" in
  *grade_sanity*) sleep 1; touch {tmp_path}/gs_done ;;
  *--no-swap*) [ "{int(fail_build)}" = "1" ] && exit 1 ;;
  *--swap-only*) if [ -f {tmp_path}/gs_done ]; then echo SWAP_AFTER_GS >> {calls}; else echo SWAP_EARLY >> {calls}; fi ;;
esac
exit 0
''')
    stub.chmod(0o755)
    script = (f"WIDE={wide}\n" + _lib(tmp_path) + f"PY={stub}; SKIP=',{skip},'\n" + _peopleBlock()
              + 'echo "FAILED=[$FAILED]"\n')
    out = _bash(script)
    return out, (calls.read_text() if calls.exists() else "")


def test_the_twin_swap_waits_for_grade_sanity(tmp_path):
    out, calls = _runPeople(tmp_path)
    assert "FAILED=[]" in out.stdout, out
    assert "SWAP_AFTER_GS" in calls and "SWAP_EARLY" not in calls, calls
    lines = calls.splitlines()
    build = next(i for i, ln in enumerate(lines) if "--no-swap" in ln)
    gender = next(i for i, ln in enumerate(lines) if "person_gender.py" in ln)
    assert gender < build


def test_a_failed_twin_build_is_never_swapped(tmp_path):
    out, calls = _runPeople(tmp_path, fail_build=True)
    assert "FAILED=[ 04c_twins]" in out.stdout, out
    assert "--swap-only" not in calls


def test_a_skipped_twin_build_is_not_swapped(tmp_path):
    out, calls = _runPeople(tmp_path, skip="04c_twins")
    assert "--no-swap" not in calls and "--swap-only" not in calls


def test_both_scripts_parse():
    for f in ("run_pipeline.sh", "nightly_update.sh"):
        assert _bash(f"bash -n {os.path.join(ROOT, 'deploy', f)}").returncode == 0, f


# ==================================================================== #
# ON A SCRATCH POSTGRES
# ==================================================================== #

@contextlib.contextmanager
def scratch(name):
    if not DSN:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {name}")
        c.execute(f"CREATE DATABASE {name}")
    tdsn = " ".join(p for p in DSN.split() if not p.startswith("dbname=")) + f" dbname={name}"

    @contextlib.contextmanager
    def connect():
        c2 = psycopg2.connect(tdsn)
        try:
            yield c2
        finally:
            c2.close()
    conn = psycopg2.connect(tdsn)
    try:
        yield conn, connect
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


# ---- 1. twin_flag --no-swap / --swap-only ------------------------------ #

def test_twin_no_swap_then_swap_only_is_the_one_step_build():
    import twin_flag as TF
    with scratch("xcp_speed_twin") as (conn, connect):
        cur = conn.cursor()
        cur.execute(read("tests", "fixtures", "twin_rules.sql"))
        conn.commit()
        TF.build(conn, write=True, parallel=False)
        cur.execute("SELECT sport, result_id, reason FROM result_twin ORDER BY 1, 2")
        one_step = cur.fetchall()
        assert one_step
        # last run's flags: a sentinel row
        cur.execute("DELETE FROM result_twin")
        cur.execute("INSERT INTO result_twin VALUES ('XC', 999999, 'last_run')")
        conn.commit()
        for parallel in (False, True):
            TF.build(conn, write=True, parallel=parallel, connect=connect, swap=False)
            cur.execute("SELECT sport, result_id, reason FROM result_twin")
            assert cur.fetchall() == [("XC", 999999, "last_run")], "--no-swap left result_twin alone"
            assert TF._isComplete(cur)
            conn.rollback()
            TF.swapOnly(conn)
            cur.execute("SELECT sport, result_id, reason FROM result_twin ORDER BY 1, 2")
            assert cur.fetchall() == one_step
            cur.execute("SELECT obj_description('result_twin'::regclass, 'pg_class')")
            assert cur.fetchone()[0] is None
            cur.execute("SELECT to_regclass('result_twin_new')")
            assert cur.fetchone()[0] is None
            conn.commit()
            cur.execute("DELETE FROM result_twin")
            cur.execute("INSERT INTO result_twin VALUES ('XC', 999999, 'last_run')")
            conn.commit()
        # nothing to swap, or a half-built table: refused, nothing changes
        with pytest.raises(SystemExit):
            TF.swapOnly(conn)
        cur.execute("CREATE TABLE result_twin_new (LIKE result_twin INCLUDING ALL)")
        conn.commit()
        with pytest.raises(SystemExit):
            TF.swapOnly(conn)
        cur.execute("SELECT count(*) FROM result_twin")
        assert cur.fetchone()[0] == 1
        conn.rollback()


# ---- 4. seams from the statistics --------------------------------------- #

def test_seams_from_pg_stats_split_the_rows_evenly():
    import build_ranking_results as B
    with scratch("xcp_speed_seams") as (conn, _connect):
        cur = conn.cursor()
        # many more rows in recent years, as the corpus has
        cur.execute("""CREATE TABLE results AS
                       SELECT to_char(date '2025-12-31' - (floor(power(random(), 2.5) * 12000))::int,
                                      'YYYY-MM-DD') AS date
                       FROM generate_series(1, 200000)""")
        cur.execute("ANALYZE results")
        conn.commit()
        seams = B.seamsFor(conn, "XC", 4)
        assert len(seams) == 3 and seams == sorted(set(seams)), seams
        bounds = ["1990-01-01"] + seams + ["2100-01-01"]
        counts = []
        for lo, hi in zip(bounds, bounds[1:]):
            cur.execute("SELECT count(*) FROM results WHERE date >= %s AND date < %s", (lo, hi))
            counts.append(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM results WHERE date >= '1990-01-01' AND date < '2100-01-01'")
        assert sum(counts) == cur.fetchone()[0]
        assert min(counts) > 0.15 * sum(counts) and max(counts) < 0.35 * sum(counts), counts
        assert B.seamsFor(conn, "TF", 4) == []          # no results_tf: no seams
        conn.rollback()


# ---- 6. the go-live lock per table ---------------------------------------- #

def test_go_live_lock_is_per_table():
    import speed_ratings_db as S
    with scratch("xcp_speed_lock") as (conn, connect):
        with connect() as a, connect() as b:
            S._takeGoLiveLock(a, "results")
            S._takeGoLiveLock(a, "results_tf")          # the go-live: one session, both
            with pytest.raises(RuntimeError, match="results_tf"):
                S._takeGoLiveLock(b, "results_tf")
            with pytest.raises(RuntimeError, match="lock on results:"):
                S._takeGoLiveLock(b, "results")
        with connect() as a, connect() as b:
            S._takeGoLiveLock(a, "results")              # the XC fill
            S._takeGoLiveLock(b, "results_tf")           # beside the TF fill


# ---- 7. merge_column: indexes three at a time, NOT NULLs in one ALTER -------- #

_MT = """
CREATE TABLE mt_athletes (athlete_id bigint PRIMARY KEY);
INSERT INTO mt_athletes SELECT g FROM generate_series(1, 50) g;
CREATE TABLE mt (
    result_id   bigint PRIMARY KEY,
    athlete_id  bigint NOT NULL REFERENCES mt_athletes (athlete_id),
    name        text   NOT NULL DEFAULT 'nobody',
    grade       int    NOT NULL DEFAULT 0 CHECK (grade >= 0),
    school      text,
    date        text   NOT NULL,
    speed_rating real,
    rating_pool text);
INSERT INTO mt SELECT g, 1 + g % 50, 'n' || g, g % 13, 's' || (g % 97),
                      '2025-' || lpad((1 + g % 12)::text, 2, '0') || '-01',
                      g % 7, 'hs_m'
FROM generate_series(1, 20000) g;
CREATE INDEX mt_school ON mt (school);
CREATE INDEX mt_athlete_date ON mt (athlete_id, date DESC);
CREATE INDEX mt_rated ON mt (speed_rating) WHERE speed_rating > 0;
CREATE INDEX mt_lower_name ON mt (lower(name));
CREATE UNIQUE INDEX mt_name_uniq ON mt (name);
ALTER TABLE mt ADD CONSTRAINT mt_date_unique UNIQUE (date, result_id);
CREATE TABLE mt_child (id int, rid bigint REFERENCES mt (result_id));
"""


def _shape(cur, table="mt"):
    cur.execute("""SELECT indexname, indexdef FROM pg_indexes
                   WHERE schemaname = 'public' AND tablename = %s ORDER BY 1""", (table,))
    idx = cur.fetchall()
    cur.execute("""SELECT attname, attnotnull, pg_get_expr(d.adbin, d.adrelid)
                   FROM pg_attribute a LEFT JOIN pg_attrdef d
                     ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                   WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                   ORDER BY a.attnum""", (table,))
    cols = cur.fetchall()
    cur.execute("""SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conrelid = %s::regclass AND contype IN ('p','c','u','f') ORDER BY 1""", (table,))
    cons = cur.fetchall()
    cur.execute("""SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
                   FROM pg_constraint WHERE confrelid = %s::regclass ORDER BY 1, 2""", (table,))
    return idx, cols, cons, cur.fetchall()


def _merge(conn, connect, jobs, seed, monkeypatch):
    import merge_column as mc
    monkeypatch.setenv("XCP_MERGE_INDEX_JOBS", str(jobs))
    monkeypatch.setenv("XCP_MERGE_VACUUM", "1")
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS mt_old")
    cur.execute("DROP TABLE IF EXISTS stg")
    cur.execute(f"""CREATE TABLE stg AS SELECT result_id, (result_id % {seed})::real + 0.5 AS val,
                                         'pool' || (result_id % 3) AS pool
                    FROM mt WHERE result_id % 4 <> 0""")
    conn.commit()
    mc.mergeColumn(conn, "mt", "speed_rating", "stg", extra=(("rating_pool", "pool"),),
                   preserve_unmatched=False, connect=connect)
    cur.execute("""SELECT count(*) FILTER (WHERE m.speed_rating IS DISTINCT FROM s.val),
                          count(*) FILTER (WHERE m.rating_pool IS DISTINCT FROM s.pool)
                   FROM mt m LEFT JOIN stg s USING (result_id)""")
    assert cur.fetchone() == (0, 0)
    cur.execute("SELECT count(*) FROM mt")
    assert cur.fetchone()[0] == 20000
    conn.commit()


def test_merge_column_parallel_indexes_give_the_serial_table(monkeypatch):
    import merge_column as mc
    with scratch("xcp_speed_merge") as (conn, connect):
        cur = conn.cursor()
        cur.execute(_MT)
        conn.commit()
        _merge(conn, connect, 1, 11, monkeypatch)          # the old path, one by one
        serial = _shape(cur)
        cur.execute("SELECT * FROM mt ORDER BY result_id")
        serial_rows = cur.fetchall()
        conn.commit()
        _merge(conn, connect, 3, 11, monkeypatch)          # three at a time
        assert _shape(cur) == serial
        cur.execute("SELECT * FROM mt ORDER BY result_id")
        assert cur.fetchall() == serial_rows
        # every NOT NULL in one statement
        cap = mc._capture(cur, "mt")
        sql = mc._shapeSql("mt", cap)
        assert sql.count("ALTER TABLE") == 1 and sql.count("SET NOT NULL") == 4   # the PK's own excepted
        conn.commit()
        # a failed index build after the commit: <table>_new dropped, table untouched
        before = _shape(cur)
        conn.commit()
        real = mc._buildOneIndex

        def boom(connect_, name, ddl):
            if name == "mt_school":
                raise RuntimeError("index build failed")
            return real(connect_, name, ddl)
        monkeypatch.setattr(mc, "_buildOneIndex", boom)
        with pytest.raises(RuntimeError, match="index build failed"):
            _merge(conn, connect, 3, 5, monkeypatch)
        conn.rollback()
        cur.execute("SELECT to_regclass('mt_new')")
        assert cur.fetchone()[0] is None
        assert _shape(cur) == before
        cur.execute("SELECT * FROM mt ORDER BY result_id")
        assert cur.fetchall() == serial_rows
        conn.rollback()
        # and the next merge clears a leftover <table>_new from a killed one
        monkeypatch.setattr(mc, "_buildOneIndex", real)
        cur.execute("CREATE TABLE mt_new AS SELECT * FROM mt LIMIT 3")
        conn.commit()
        _merge(conn, connect, 3, 11, monkeypatch)
        assert _shape(cur) == serial


def test_merge_column_keeps_the_order_build_commit_vacuum_swap():
    src = read("engine", "merge_column.py")
    body = src[src.index("def mergeColumn("):]
    order = ["_buildNewTable(", "_restoreShape(", "conn.commit()", "_restoreIndexesParallel(",
             "_restoreConstraints(cur, table, cap)\n        except", "_vacuumNew(conn, table)",
             "_swapSiege(conn, table, cap)"]
    pos = [body.index(s) for s in order]
    assert pos == sorted(pos)


# ---- 8. the restamp by key gives the restamp by row ------------------------ #

_UNITS = """
CREATE TABLE school_unit (school text, state text, is_college boolean, votes int,
    division text, region text, conference text, league text, state_div text,
    section_div text, district text, county text, class text, section text);
CREATE TABLE college_directory (name_norm text, name text, state text, division text, source text);
INSERT INTO college_directory VALUES
  ('amherst', 'Amherst College', 'MA', 'D3', 'w'),
  ('jesuit', 'Jesuit University', 'LA', 'D2', 'w'),
  ('oregon', 'University of Oregon', 'OR', 'D1', 'w');
CREATE TABLE athlete_season (id serial PRIMARY KEY, person_id bigint, school text, state text, pool text,
    division text, region text, conference text, league text, state_div text,
    section_div text, district text, county text, class text, area text, section text);
"""


def _unitsFixture(cur):
    import random
    rnd = random.Random(7)
    schools = ["Amherst", "Jesuit", "Oregon", "Lincoln", "Unattached", "Washington",
               "Central", None, "Amherst College", "Northside", "Only One"]
    states = ["MA", "NE", "WI", "LA", "CA", "OR", "WA", None, "TX"]
    pools = ["college_m", "college_f", "hs_m", "hs_f", "ms_m", "pro_m", None]
    cur.execute(_UNITS)
    v = 0
    for sch in schools:
        if sch is None:
            continue
        for st in rnd.sample(states[:-2], rnd.randint(0, 4)):
            for col in (True, False, None):
                if rnd.random() < 0.6:
                    v += 1
                    cur.execute("INSERT INTO school_unit VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                (sch, st, col, v) + tuple(
                                    (f"{sch[:3]}-{st}-{c}" if rnd.random() < 0.85 else None)
                                    for c in ("div", "reg", "conf", "lg", "sd", "secd", "dist", "cty", "cls", "sec")))
    # the campus pass's own rows: each directory college at its campus state,
    # and the same name as a high school elsewhere
    for sch, st in (("Amherst", "MA"), ("Jesuit", "LA"), ("Oregon", "OR")):
        for col in (True, False):
            v += 1
            cur.execute("INSERT INTO school_unit (school, state, is_college, votes, division, conference)"
                        " VALUES (%s, %s, %s, %s, %s, %s)",
                        (sch, st, col, 1000 + v, f"campus-{sch}-{col}", None if col else f"hs-{sch}"))
    for i in range(3000):
        sch = rnd.choice(schools)
        cur.execute("""INSERT INTO athlete_season (person_id, school, state, pool, division, region,
                         conference, league, area) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (i, sch, rnd.choice(states), rnd.choice(pools),
                     *(("stale" if rnd.random() < 0.5 else None) for _ in range(5))))


def test_restamp_by_key_gives_the_restamp_by_row():
    import build_ranking_results as B
    with scratch("xcp_speed_restamp") as (conn, _connect):
        cur = conn.cursor()
        _unitsFixture(cur)
        cur.execute("CREATE TABLE as_old AS SELECT * FROM athlete_season")
        cur.execute("CREATE TABLE as_new AS SELECT * FROM athlete_season")
        conn.commit()
        B._restampInPlace(conn, "as_old")
        conn.commit()
        n = B._restampByKey(conn, "as_new")
        assert n and n > 0
        cur.execute("SELECT * FROM as_old ORDER BY id")
        old = cur.fetchall()
        cur.execute("SELECT * FROM as_new ORDER BY id")
        assert cur.fetchall() == old
        # the stamping did something, and the cleared column stays cleared
        cur.execute("SELECT count(*) FILTER (WHERE conference IS NOT NULL), count(area) FROM as_new")
        stamped, area = cur.fetchone()
        assert stamped > 0 and area == 0
        conn.commit()
        # a second restamp changes no row
        assert B._restampByKey(conn, "as_new") == 0
        # no school_unit at all: every unit column NULL, as the old clear left it
        cur.execute("ALTER TABLE school_unit RENAME TO school_unit_away")
        conn.commit()
        B._restampInPlace(conn, "as_old")
        conn.commit()
        B._restampByKey(conn, "as_new")
        cur.execute("SELECT * FROM as_old ORDER BY id")
        old = cur.fetchall()
        cur.execute("SELECT * FROM as_new ORDER BY id")
        assert cur.fetchall() == old
        cur.execute("SELECT count(*) FROM as_new WHERE conference IS NOT NULL")
        assert cur.fetchone()[0] == 0
        conn.rollback()


def test_a_failed_restamp_by_key_writes_nothing(monkeypatch):
    import build_ranking_results as B
    with scratch("xcp_speed_restamp_fail") as (conn, _connect):
        cur = conn.cursor()
        _unitsFixture(cur)
        conn.commit()
        cur.execute("SELECT * FROM athlete_season ORDER BY id")
        before = cur.fetchall()
        conn.commit()
        monkeypatch.setattr(B, "_isTeamName", lambda s: (_ for _ in ()).throw(ValueError("boom")))
        assert B._restampByKey(conn, "athlete_season") is None
        cur.execute("SELECT * FROM athlete_season ORDER BY id")
        assert cur.fetchall() == before
        conn.rollback()


# ---- 9. sizes from the catalog --------------------------------------------- #

def test_drop_old_and_the_backup_count_nothing_for_a_log_line():
    import drop_old as D
    with scratch("xcp_speed_counts") as (conn, _connect):
        cur = conn.cursor()
        cur.execute("CREATE TABLE results_old AS SELECT g AS result_id FROM generate_series(1, 5000) g")
        conn.commit()
        assert D.approxRows(cur, "results_old") is None       # never analysed: unknown
        cur.execute("ANALYZE results_old")
        assert D.approxRows(cur, "results_old") == 5000
        assert D.aboutRows(5000) == "about 5,000 rows" and D.aboutRows(None) == "size unknown"
        assert D.approxRows(cur, "no_such_table") is None
        # CREATE TABLE AS reports its own row count: the backup's exact size
        cur.execute("CREATE TABLE bk AS SELECT result_id FROM results_old WHERE result_id % 2 = 0")
        assert cur.rowcount == 2500
        conn.rollback()
    src = read("engine", "drop_old.py") + read("engine", "pair_golive.py")
    assert "SELECT count(*) FROM {table}" not in src and "SELECT count(*) FROM {backup}" not in src


# ---- XCP_PIPELINE_WIDE=0, the default since 2026-11-08 (the 11-07 23:28 OOM) ----

def test_not_wide_builds_twins_after_grade_sanity(tmp_path):
    """! The default: the twins build starts only once grade_sanity and
    wheelchair are collected, so the people block holds no more Python steps
    in memory than it did before 11-07; the swap still follows the build."""
    _runPeople(tmp_path, wide=0)
    calls = (tmp_path / "calls").read_text().splitlines()
    gs = next(i for i, c in enumerate(calls) if "grade_sanity" in c)
    build = next(i for i, c in enumerate(calls) if "--no-swap" in c)
    assert build > gs and (tmp_path / "gs_done").exists()
    assert "SWAP_AFTER_GS" in calls and "SWAP_EARLY" not in calls


def test_not_wide_ranks_in_the_old_shape(tmp_path):
    pairs = _runRank(tmp_path, "2009-08-01 2016-03-01 2021-09-15", env="WIDE=0")
    for sport in ("XC", "TF"):
        assert _ranges(pairs, sport) == [
            (f"10_rankings_{sport.lower()}_a", "1990-01-01", "2018-01-01"),
            (f"10_rankings_{sport.lower()}_b", "2018-01-01", "2100-01-01")]
    assert 'else _RPARTS="${XCP_RANK_PARTS:-2}"; _RSTREAMS="${XCP_RANK_STREAMS:-4}"; fi' in SH


def test_wide_is_off_unless_asked():
    assert 'WIDE="${XCP_PIPELINE_WIDE:-0}"' in SH
    for line in ("wbg 13c0_person_redirects", "wbg 13c0_search_helpers", "wbg 10b0_tfrrs_link",
                 "wbg 10e_meet_units", "wchain 15_rowguard _rowguard",
                 "wchain 04g_checks _distanceChecks"):
        assert line in SH, line


def test_not_wide_runs_a_chain_in_line(tmp_path):
    script = "WIDE=0\n" + _lib(tmp_path) + '''
_c() { step 04g0_x sh -c "echo X0"; step 04g1_y sh -c "echo Y1"; }
wchain 04g_checks _c
echo AFTER
bgchainjoin 04g_checks
echo "FAILED=[$FAILED]"
'''
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert "IN THE BACKGROUND" not in out
    assert out.index("04g1_y") < out.index("AFTER") and "FAILED=[]" in out
