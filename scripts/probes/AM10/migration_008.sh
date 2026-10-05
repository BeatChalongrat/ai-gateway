#!/usr/bin/env bash
# AM10 — migration 008 on the TEST stack: up, down, up again, with what each step leaves.
#
# The test stack's chat database has 008 already (the runner applied it at start) and the rows the
# walks made (named chats, a form dropped into a round). From there:
#
#   up    what is there: the seven columns, the two constraints, the migration's row, and the rows
#         that hold 008's data
#   down  008_am10.down.sql in one transaction → none of it is left, and the schema is exactly the
#         one migrations 001–007 make on an empty database (built here beside it and compared)
#   up    the runner starts and applies 008 again → the schema is exactly the first one
#
# The runner and chat-edge of the test stack are stopped for the down (they are built for 008) and
# started again after the second up. Only containers of the test projects are named; the scratch
# database lives on the test stack's own Postgres and is dropped at the end.
#
#   bash scripts/probes/AM10/migration_008.sh <tag>      (the image tag the test stack runs)
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
TAG="${1:?the image tag the test stack runs}"
PG=sran-test-postgres-webchat-1
MIG=services/chat-edge-api/internal/agentmarket/migrations
OUT=scripts/probes/AM10/results/migration_008
mkdir -p "$OUT"
q() { docker exec -i "$PG" psql -U chatedge -d "${DB:-sran_ai}" -v ON_ERROR_STOP=1 -At -F ' | ' "$@"; }
dump() { # the schema as statements, without comments, blank lines or the dump's own session lines
  docker exec "$PG" pg_dump -U chatedge -d "${1:-sran_ai}" --schema-only --no-owner --no-privileges -n agent_market |
    grep -v -e '^--' -e '^$' -e '^\\' -e '^SET ' -e '^SELECT pg_catalog.set_config' | tr -d '\r'
}
shape() {
  q <<'SQL'
SELECT 'column ' || table_name || '.' || column_name || ' ' || data_type FROM information_schema.columns
 WHERE table_schema = 'agent_market' AND (
   (table_name = 'agent_threads' AND column_name IN ('title', 'pinned_at', 'deleted_at')) OR
   (table_name = 'agent_installs' AND column_name IN ('first_chat_title', 'first_chat_pinned_at', 'first_chat_deleted_at')) OR
   (table_name = 'agent_runs' AND column_name = 'round_template'))
 ORDER BY 1;
SELECT 'constraint ' || conname || ' allows form: ' || (pg_get_constraintdef(oid) LIKE '%''form''%')
  FROM pg_constraint WHERE conname IN ('agent_files_status_check', 'agent_thread_items_kind_check') ORDER BY 1;
SELECT 'migrations recorded: ' || string_agg(version, ' ' ORDER BY version) FROM agent_market.schema_migrations;
SQL
}
rows() { # every table of the schema with its row count, one per line — no table is named by hand
  q <<'SQL'
SELECT format('SELECT %L || '' '' || count(*) FROM agent_market.%I', table_name, table_name)
  FROM information_schema.tables WHERE table_schema = 'agent_market' AND table_type = 'BASE TABLE' ORDER BY table_name \gexec
SQL
}
said() { # <rows file>: the tables a person asks about first, and the total
  awk '{ n[$1] = $2; t += $2 } END { printf "rows: installs %s · chats %s · runs %s · steps %s · files %s · artifacts %s · thread items %s · all %d tables %d rows\n",
    n["agent_installs"], n["agent_threads"], n["agent_runs"], n["agent_run_steps"], n["agent_files"], n["agent_output_artifacts"], n["agent_thread_items"], NR, t }' "$1"
}
held() { # what 008's own columns hold (only while they exist)
  q <<'SQL'
SELECT 'held  chats named ' || (SELECT count(*) FROM agent_market.agent_threads WHERE title IS NOT NULL)
    || ' · pinned ' || (SELECT count(*) FROM agent_market.agent_threads WHERE pinned_at IS NOT NULL)
    || ' · deleted ' || (SELECT count(*) FROM agent_market.agent_threads WHERE deleted_at IS NOT NULL)
    || ' · first chats named/pinned/deleted ' || (SELECT count(*) FROM agent_market.agent_installs
          WHERE first_chat_title IS NOT NULL OR first_chat_pinned_at IS NOT NULL OR first_chat_deleted_at IS NOT NULL)
    || ' · rounds with a form ' || (SELECT count(*) FROM agent_market.agent_runs WHERE round_template IS NOT NULL);
SQL
}
fail=0

echo "== 0. the test stack's runner and chat-edge stop (they are built for 008)"
docker stop sran-test-agent-runner sran-test-chat-edge-api-1 >/dev/null 2>&1
echo "stopped: $(docker inspect -f '{{.Name}} {{.State.Status}}' sran-test-agent-runner sran-test-chat-edge-api-1 2>/dev/null | tr '\n' ' ')"

echo "== 1. UP — as the runner applied it"
shape; rows > "$OUT/rows_up_1.txt"; said "$OUT/rows_up_1.txt"; held
dump > "$OUT/schema_up_1.sql"
echo "schema: $(wc -l < "$OUT/schema_up_1.sql") statements' lines"

echo "== 2. DOWN — 008_am10.down.sql, one transaction"
if q -1 -q < "$MIG/008_am10.down.sql"; then echo "down applied: exit 0"; else echo "DOWN FAILED"; fail=1; fi
shape; rows > "$OUT/rows_down.txt"; said "$OUT/rows_down.txt"
echo "row counts that differ from before the down: $(diff "$OUT/rows_up_1.txt" "$OUT/rows_down.txt" | grep '^[<>]' | tr '\n' ' ')— only thread items of kind form go; a form file stays, as a removed file"
dump > "$OUT/schema_down.sql"

echo "== 2b. the same schema as 001–007 on an empty database"
docker exec "$PG" psql -U chatedge -d sran_ai -q -c 'DROP DATABASE IF EXISTS am10_m007' -c 'CREATE DATABASE am10_m007' >/dev/null 2>&1 ||
  { echo "the scratch database could not be made"; fail=1; }
for f in $(ls "$MIG" | grep '\.up\.sql$' | sort | head -7); do
  DB=am10_m007 q -1 -q < "$MIG/$f" || { echo "scratch: $f failed"; fail=1; }
done
dump am10_m007 > "$OUT/schema_001_007.sql"
docker exec "$PG" psql -U chatedge -d sran_ai -q -c 'DROP DATABASE IF EXISTS am10_m007' >/dev/null 2>&1
n=$(diff "$OUT/schema_down.sql" "$OUT/schema_001_007.sql" | wc -l)
echo "diff(schema after the down, schema of 001–007): $n lines"
[ "$n" -eq 0 ] || fail=1

echo "== 3. UP again — the runner starts and migrates"
bash scripts/probes/AM7/test_stack.sh runner "$TAG" | grep -E "schema migrated|ERROR" | cut -c1-220
shape; rows > "$OUT/rows_up_2.txt"; said "$OUT/rows_up_2.txt"; held
dump > "$OUT/schema_up_2.sql"
n=$(diff "$OUT/schema_up_1.sql" "$OUT/schema_up_2.sql" | wc -l)
echo "diff(schema after the second up, schema after the first): $n lines"
[ "$n" -eq 0 ] || fail=1
n=$(diff "$OUT/schema_down.sql" "$OUT/schema_up_2.sql" | grep -c '^[<>]')
echo "lines 008 adds to the schema dump: $n"

echo "== 4. chat-edge back"
bash scripts/probes/AM7/test_stack.sh edge "$TAG" on | tail -1
echo "migration_008_exit=$fail"
exit $fail
