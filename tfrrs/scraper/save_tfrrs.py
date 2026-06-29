# Project: xc-predictor
# File:    tfrrs/glue/save_tfrrs.py
# Purpose: Turn PARSED TFRRS rows (from parse_tf_page / parse_xc) into rows in
#          the SAME results / results_tf tables anet writes to, distinguished by
#          source='tfrrs'. Two halves:
#
#            TRANSFORM (pure, testable, no DB) — buildTFRRSResultRow:
#              a parsed-row dict + meet meta  ->  one normalized dict shaped for
#              the DB. Routes result_kind (running->time, field->mark,
#              combined->points-in-mark), resolves identity (native_id/id_system/
#              name), carries source. Commits to nothing; just reshapes.
#
#            WRITE (draft — runs only AFTER the migration adds the new columns):
#              saveTFRRSResultsBulk — execute_values insert mirroring anet's
#              saveResultsTFBulk, extended with source/id_system/native_id/
#              person_id/athlete_name/result_kind.
#
# TWO THINGS THIS LAYER DELIBERATELY DOES NOT SOLVE (flagged, not hidden):
#   1. result_id MINTING. TFRRS gives no result id; results_tf.result_id is the
#      PK. The minting scheme (content hash / reserved range / sequence) is an
#      OPEN decision — see _mintResultId. The transform leaves result_id out;
#      the write-half is where it gets assigned.
#   2. IDENTITY RESOLUTION. A TFRRS athlete has a native_id in the tfrrs/
#      directathletics namespace, NOT an anet athlete_id. We store native_id +
#      id_system + name and leave athlete_id / person_id NULL. Linking a TFRRS
#      athlete to an anet person (so their results join + enter training) is a
#      SEPARATE later pass. Until it runs, TFRRS rows sit unlinked. That's
#      expected, not a bug.
 
import sys
 
sys.path.insert(0, "scripts")
import psycopg2.extras
import hashlib

# Mask to fold the hash into PostgreSQL's signed BIGINT range. BIGINT is 64-bit
# signed (max ~9.2e18). We take 62 bits (max ~4.6e18) to stay safely under the
# positive ceiling BEFORE negating — so the negated value can't underflow.
_BIGINT_62 = (1 << 62) - 1
 
# The source tag stamped on every TFRRS row (matches the migration's scheme).
TFRRS_SOURCE = "tfrrs"

from database import executeWithRetry

# ================================================================== #
# Create TFRRS Table
# ================================================================== #

# ================================================================== #
# MEET METADATA — the missing glue. parseXCMeetMeta produces a meet-level
# dict (name/date/venue/city/state/host/location_raw) that the driver carried
# through every bundle and then DROPPED. This persists it, so every TFRRS meet
# has a venue/city/state row to geocode later and to join results against.
#
# Dedicated table (not anet's meets_tf_meta) on purpose: TFRRS meet_ids live in
# a SEPARATE id space from anet's, so writing into a meet_id-keyed anet table
# would risk a coincidental-integer collision (wrong venue on the wrong meet).
# Keyed (meet_id, sport) here — a TFRRS id can host both an XC and a TF meet,
# and they carry different pages. gps_lat/long are NULL now; a geocoding pass
# fills them from (venue_name, city, state) later, the same backfill anet's
# null-coordinate meets will use. Unify the two sources for geocoding with a
# UNION over (venue_name, city, state) when that pass runs.
# ================================================================== #
 
 
# _createMeetsTFRRSTable
# Purpose: Create meets_tfrrs if missing. One row per (meet_id, sport). Holds
#          exactly what parseXCMeetMeta yields plus source/native_id, so the
#          saver is a straight dict->columns write with no invented fields.
#          Idempotent (IF NOT EXISTS); call once at startup.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None.
def _createMeetsTFRRSTable(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meets_tfrrs (
            meet_id         BIGINT,
            sport           TEXT,
            meet_name       TEXT,
            date            TEXT,
            date_end        TEXT,
            venue_name      TEXT,
            city            TEXT,
            state           TEXT,
            location_raw    TEXT,
            host            TEXT,
            director        TEXT,         -- NEW: "NCAA" etc. (raw value kept)
            timing          TEXT,         -- NEW
            referee         TEXT,         -- NEW
            is_championship INTEGER,      -- NEW: 1/0 derived flag
            gps_lat         REAL,
            gps_long        REAL,
            source          TEXT NOT NULL,
            native_id       BIGINT,
            PRIMARY KEY (meet_id, sport)
        )
    """)


# ================================================================== #
# TRANSFORM HALF — pure functions, no DB. Fully testable on parser output.
# ================================================================== #
 
# _routeResult
# Purpose: Decide what the ONE result value is and where it goes, based on the
#          parser's result_kind. anet stores field marks in `mark` (raw string)
#          with time_seconds NULL; we extend that: combined points ALSO go in
#          `mark` (reusing the column, per the schema decision), with result_kind
#          telling consumers what `mark` holds.
# Arguments:
#           parsed: one parsed row dict (parseTFRow / parseXCRow output).
# Output:   a dict slice with exactly the three result fields set correctly:
#             {time_seconds, mark, is_field}
#           - running : time_seconds set, mark None, is_field 0
#           - field   : time_seconds None, mark = raw mark string, is_field 1
#           - combined: time_seconds None, mark = points (as string), is_field 0
def _routeResult(parsed: dict) -> dict:
    kind = parsed.get("result_kind", "running")
 
    if kind == "field":
        # Match anet: store the RAW mark text (e.g. "8.28m"), not parsed metres,
        # so TFRRS and anet field marks share one type in the `mark` column.
        return {
            "time_seconds": None,
            "mark":         parsed.get("mark_raw"),
            "is_field":     1,
        }
 
    if kind == "combined":
        # Reuse `mark` for the decathlon/heptathlon points total. str() so the
        # column holds one consistent text type; result_kind disambiguates.
        points = parsed.get("points_total")
        return {
            "time_seconds": None,
            "mark":         str(points) if points is not None else None,
            "is_field":     0,   # not a field event; result_kind says "combined"
        }
 
    # running (default) — the common case. A real time, no mark.
    return {
        "time_seconds": parsed.get("time_seconds"),
        "mark":         None,
        "is_field":     0,
    }
 
 
# _resolveIdentity
# Purpose: Pull the cross-source identity fields out of a parsed row. TFRRS gives
#          a native_id + id_system (or neither, for ancient name-only rows). We
#          keep the NAME regardless — it's the only handle on a profile-less row
#          (the fix for the anet anonymity problem we found).
# Arguments:
#           parsed: one parsed row dict.
# Output:   a dict slice: {native_id, id_system, athlete_name}.
def _resolveIdentity(parsed: dict) -> dict:
    return {
        # XC rows expose only native_id (tfrrs namespace, implicit); TF rows
        # expose both native_id AND id_system (tfrrs vs directathletics). .get
        # with a default keeps both row shapes working through one path.
        "native_id":    parsed.get("athlete_native_id"),
        # `or "tfrrs"` catches BOTH a missing key AND a present-but-None value;
        # .get(key, "tfrrs") only catches the missing case. id_system is NOT NULL
        # on results_tf (migration stage 4), so it can never be None.
        "id_system":    parsed.get("id_system") or "tfrrs",
        # name is always kept — None-id rows are anonymous WITHOUT this, which is
        # exactly the data loss we're fixing. " ".join(split()) already done by
        # the parser, so it's clean here.
        "athlete_name": parsed.get("name"),
    }
 
 
# buildTFRRSResultRow
# Purpose: THE transform entry point. Turn one parsed TFRRS row + the meet's meta
#          into a single normalized dict shaped for results_tf. Pure: no DB, no
#          result_id (minted later), no athlete_id/person_id (resolved later).
# Arguments:
#           parsed:    one parsed row dict from parse_tf_page / parse_xc. Carries
#                      result_kind, the result value(s), identity, place, etc.
#           meet_meta: the dict from parseXCMeetMeta — meet_name, date, venue,
#                      state, etc. Supplies the meet-level fields each result
#                      needs (date especially).
#           meet_id:   the TFRRS meet id (carried onto every row).
#           event_id:  this event's id (from the page parser's event context).
#           event_short: the event code/name string for this event.
# Output:   a normalized dict ready for saveTFRRSResultsBulk, OR None if the row
#           didn't parse (ok=False) — caller decides whether to log/skip.
def buildTFRRSResultRow(parsed: dict, meet_meta: dict,
                        meet_id, event_id, event_short) -> dict:
    # Don't transform a row the parser already rejected. Returning None lets the
    # caller count/flag skips without this function having to know the policy.
    if not parsed.get("ok", False):
        return None
 
    result   = _routeResult(parsed)       # time/mark/is_field
    identity = _resolveIdentity(parsed)   # native_id/id_system/athlete_name
 
    # Assemble the full normalized record. Keys mirror results_tf column names so
    # the write-half is a straight dict->tuple read with no renaming.
    return {
        # identity / provenance
        "source":        TFRRS_SOURCE,
        "athlete_id":    None,                       # anet-space id — unresolved
        "person_id":     None,                       # cross-source link — unresolved
        "native_id":     identity["native_id"],
        "id_system":     identity["id_system"],
        "athlete_name":  identity["athlete_name"],
 
        # meet / event context
        "meet_id":       meet_id,
        "event_id":      event_id,
        "event_short":   event_short,
        "date":          meet_meta.get("date"),      # first day (parser convention)
 
        # the result itself
        "result_kind":   parsed.get("result_kind", "running"),
        "time_seconds":  result["time_seconds"],
        "mark":          result["mark"],
        "is_field":      result["is_field"],
 
        # per-row extras TFRRS gives us
        "grade":         parsed.get("year_raw", ""),
        "place":         parsed.get("place"),
        "score":         parsed.get("score"),
        "wind":          parsed.get("wind"),
        "splits":        parsed.get("splits"),
        "is_relay":      0,                          # TFRRS individual rows; relays handled elsewhere
 
        # school — TFRRS gives team_name; mirror anet's "Unknown" fallback so the
        # (athlete_id, school) composite FK still has a value to key on.
        "school":        parsed.get("team_name") or "Unknown",
    }
 
 
# buildTFRRSResultRows
# Purpose: Transform a whole page's worth of parsed rows. Thin wrapper that maps
#          buildTFRRSResultRow over the list and drops the None (unparsed) ones.
# Arguments:
#           parsed_rows: list of parsed row dicts (one event or a whole page).
#           meet_meta, meet_id: as above. event_id/event_short come from each
#                      row's own context (the page parser attached them).
# Output:   list of normalized dicts (parsed-failures dropped).
def buildTFRRSResultRows(parsed_rows: list, meet_meta: dict, meet_id) -> list:
    out = []
    for parsed in parsed_rows:
        row = buildTFRRSResultRow(
            parsed,
            meet_meta,
            meet_id,
            parsed.get("event_id"),       # attached by parse_tf_page
            parsed.get("event_name"),     # attached by parse_tf_page
        )
        if row is not None:
            out.append(row)
    return out
 
# ================================================================== #
# WRITE HALF — DRAFT. Runs only AFTER the migration adds the new columns
# (source, id_system, native_id, person_id, athlete_name, result_kind).
# ================================================================== #
 

# _naturalKey
# Purpose: Build the string that UNIQUELY identifies one performance, used as the
#          hash input. The id derived from this is stable across re-scrapes (same
#          performance -> same string -> same id -> ON CONFLICT updates, no
#          duplicate). Picks native_id when present, else the name (for ancient
#          profile-less rows), plus place as a tie-breaker for same-time finishers.
# Arguments:
#           row: a normalized dict from the transform half.
# Output:   a single delimited string, the hash input.
def _naturalKey(row: dict) -> str:
    # athlete handle: the stable native_id if we have one, otherwise the name
    # (the only handle a profile-less row has). Prefix tells them apart so a
    # numeric name can't masquerade as an id.
    if row["native_id"] is not None:
        who = f"id:{row['native_id']}"
    else:
        who = f"nm:{row['athlete_name']}"

    # the result value: time for running, mark/points string for field/combined.
    # Whichever is set is what distinguishes this row's performance.
    value = row["time_seconds"] if row["time_seconds"] is not None else row["mark"]

    # Join with a delimiter that can't appear inside the parts, so
    # ("12", "3") and ("1", "23") can't collide into the same string.
    return "|".join(str(p) for p in (
        who,
        row["meet_id"],
        row["event_id"],
        row.get("event_short"),   # "...Finals" vs "...Preliminaries" — same athlete,
                                  # same event_id, different ROUND would otherwise
                                  # collide on identical time+place. This separates them.
        value,
        row["place"],     # final tie-breaker: two identical times, diff places
    ))


# _mintResultId
# Purpose: Manufacture a result_id for a TFRRS row, since TFRRS supplies none.
#          CONTENT-DERIVED (stable across re-scrapes) and NEGATIVE (can never
#          collide with anet's positive IDResults).
# Arguments:
#           row: a normalized dict from the transform half.
# Output:   a negative BIGINT result_id.
def _mintResultId(row: dict) -> int:
    key = _naturalKey(row)

    # blake2b, NOT Python's hash(): hash() is salted per-process so it changes
    # between runs (would defeat re-scrape stability). blake2b is deterministic.
    # .digest() -> bytes; int.from_bytes -> a big integer; mask to 62 bits.
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    folded = int.from_bytes(digest, "big") & _BIGINT_62

    # Negate so it lands in the TFRRS (negative) half of the id space.
    # -folded - 1 avoids the one collision risk: folded==0 would give -0 == 0,
    # a positive-space value. Shifting by 1 keeps every result strictly negative.
    return -folded - 1
 
# _rowToTuple
# Purpose: Turn one normalized dict into the positional tuple execute_values
#          wants, in the exact column order of the INSERT below. One place so the
#          column/tuple order can't drift apart.
# Arguments:
#           row: a normalized dict from the transform half.
#           scraped_at: one batch timestamp shared by every row.
# Output:   a tuple in INSERT-column order.
def _rowToTuple(row: dict, scraped_at) -> tuple:
    splits_value = psycopg2.extras.Json(row["splits"]) if row.get("splits") else None
    return (
        _mintResultId(row),          # result_id (minted — see open decision)
        row["athlete_id"],           # NULL until identity resolution
        row["person_id"],            # NULL until identity resolution
        row["source"],
        row["id_system"],
        row["native_id"],
        row["athlete_name"],
        row["meet_id"],
        row["event_id"],
        row["event_short"],
        row["time_seconds"],
        row["mark"],
        row["result_kind"],
        row["is_field"],
        row["grade"],
        row["date"],
        row["is_relay"],
        row["school"],
        row["place"],
        row["score"],
        row["wind"],
        scraped_at,
        splits_value,
    )
 
 
# saveTFRRSResultsBulk
# Purpose: DRAFT bulk-insert of normalized TFRRS rows into results_tf, mirroring
#          anet's saveResultsTFBulk (execute_values + ON CONFLICT). Extended with
#          the TFRRS/identity columns. Conn-taking like every other saver; the
#          CALLER commits (per database.py's interface contract).
# Arguments:
#           conn: open connection from the pool (caller owns commit).
#           rows: list of normalized dicts from the transform half.
# Output:   None. (Cannot run until _mintResultId + the migration columns exist.)
def saveTFRRSResultsBulk(conn, rows: list) -> None:
    if not rows:
        return
 
    import datetime
    scraped_at = datetime.datetime.now(datetime.timezone.utc)
 
    tuples = [_rowToTuple(r, scraped_at) for r in rows]

    # Dedup by result_id (tuple[0]) before execute_values. After the event_short
    # fix, the only remaining same-id rows are relay mis-parses (two squads that
    # both parse to garbage name/null-time -> identical key). Postgres can't DO
    # UPDATE the same conflict target twice in one statement, so collapse here.
    deduped = {}
    for t in tuples:
        deduped[t[0]] = t          # t[0] = result_id; later row overwrites earlier
    tuples = list(deduped.values())

    cursor = conn.cursor()
    # Column list MUST match _rowToTuple's order. ON CONFLICT mirrors anet's
    # "refresh on re-scrape" stance; COALESCE protects school the same way.
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO results_tf (
            result_id, athlete_id, person_id, source, id_system, native_id,
            athlete_name, meet_id, event_id, event_short, time_seconds, mark,
            result_kind, is_field, grade, date, is_relay, school,
            place, score, wind, scraped_at, splits_json
        )
        VALUES %s
        ON CONFLICT (result_id) DO UPDATE SET
            time_seconds  = EXCLUDED.time_seconds,
            mark          = EXCLUDED.mark,
            result_kind   = EXCLUDED.result_kind,
            is_field      = EXCLUDED.is_field,
            place         = EXCLUDED.place,
            score         = EXCLUDED.score,
            wind          = EXCLUDED.wind,
            athlete_name  = COALESCE(EXCLUDED.athlete_name, results_tf.athlete_name),
            school        = COALESCE(EXCLUDED.school, results_tf.school),
            scraped_at    = EXCLUDED.scraped_at,
            splits_json   = EXCLUDED.splits_json
    """, tuples)

# saveTFRRSMeetMeta
# Purpose: Upsert one TFRRS meet's metadata row from the parseXCMeetMeta dict.
#          Conn-taking (runs inside the caller's transaction, like every other
#          saver here). ON CONFLICT refreshes so a re-scrape updates in place;
#          gps_lat/long are NOT overwritten on conflict, so a geocoding backfill
#          that already filled them isn't nulled back out by a later re-scrape.
# Arguments:
#           conn:    open DB connection (caller commits).
#           meta:    the dict from parseXCMeetMeta (meet_name/date/date_end/
#                    venue_name/city/state/location_raw/host/gps_lat/gps_long).
#           meet_id: the TFRRS meet id.
#           sport:   'XC' or 'TF' (second half of the PK).
# Output:   None. One upserted row in meets_tfrrs.
def saveTFRRSMeetMeta(conn, meta: dict, meet_id, sport) -> None:
    # No meta parsed (e.g. a page that classified as a meet but whose panel was
    # malformed) -> nothing to write. Guard so a None meta can't crash the save.
    if not meta:
        return
 
    cursor = conn.cursor()
 
    executeWithRetry(cursor, """
        INSERT INTO meets_tfrrs (
            meet_id, sport, meet_name, date, date_end,
            venue_name, city, state, location_raw, host,
            director, timing, referee, is_championship,
            gps_lat, gps_long, source, native_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meet_id, sport) DO UPDATE SET
            meet_name    = EXCLUDED.meet_name,
            date         = EXCLUDED.date,
            date_end     = EXCLUDED.date_end,
            venue_name   = EXCLUDED.venue_name,
            city         = EXCLUDED.city,
            state        = EXCLUDED.state,
            location_raw = EXCLUDED.location_raw,
            host         = EXCLUDED.host,
            director     = EXCLUDED.director,
            timing       = EXCLUDED.timing,
            referee      = EXCLUDED.referee,
            is_championship = EXCLUDED.is_championship
            -- gps_lat/gps_long intentionally NOT refreshed: a geocoding backfill
            -- fills them after the scrape; don't let a re-scrape null them out.
    """, (
        meet_id,
        sport,
        meta.get("meet_name"),
        meta.get("date"),
        meta.get("date_end"),
        meta.get("venue_name"),
        meta.get("city"),
        meta.get("state"),
        meta.get("location_raw"),
        meta.get("host"),
        meta.get("director"),                          # raw "NCAA" etc.
        meta.get("timing"),
        meta.get("referee"),
        1 if meta.get("is_championship") else 0,        # bool -> 1/0
        meta.get("gps_lat"),               # None now; geocoding fills later
        meta.get("gps_long"),              # None now; geocoding fills later
        TFRRS_SOURCE,                      # 'tfrrs' — NOT NULL
        meet_id,                           # native_id = the tfrrs meet id
    ))
 