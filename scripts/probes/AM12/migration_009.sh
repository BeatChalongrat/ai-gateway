#!/usr/bin/env bash
# AM12 — migration 009 on the TEST stack: up, down, up again, with what each step leaves.
#
# 009 adds four tables to schema agent_market and alters nothing that was there: web_deny_hosts,
# org_suppliers, web_policy, web_calls. The test stack's chat database has 009 already (the runner
# applied it at start) and the rows the walks made (searches and addresses of rounds on the web, a
# site an admin denied, a supplier). From there:
#
#   up    what is there: the four tables, the migration's row, and how many rows each holds
#   down  009_am12.down.sql in one transaction → the four tables are gone, no other table lost or
#         gained a row, and the schema is exactly the one migrations 001–008 make on an empty
#         database (built here beside it and compared)
#   up    the runner starts and applies 009 again → the schema is exactly the first one
#
# The runner and chat-edge of the test stack are stopped for the down (they are built for 009) and
# started again after the second up. Only containers of the test projects are named; the scratch
# database lives on the test stack's own Postgres and is dropped at the end.
#
#   bash scripts/probes/AM12/migration_009.sh <tag>      (the image tag the test stack runs)
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
TAG="${1:?the image tag the test stack runs}"
PG=sran-test-postgres-webchat-1
MIG=services/chat-edge-api/internal/agentmarket/migrations
OUT=scripts/probes/AM12/results/migration_009
mkdir -p "$OUT"
q() { docker exec -i "$PG" psql -U chatedge -d "${DB:-sran_ai}" -v ON_ERROR_STOP=1 -At -F ' | ' "$@"; }
dump() { # the schema as statements, without comments, blank lines or the dump's own session lines
  docker exec "$PG" pg_dump -U chatedge -d "${1:-sran_ai}" --schema-only --no-owner --no-privileges -n agent_market |
    grep -v -e '^--' -e '^$' -e '^\\' -e '^SET ' -e '^SELECT pg_catalog.set_config' | tr -d '\r'
}
shape() {
  q <<'SQL'
SELECT 'table ' || table_name FROM information_schema.tables
 WHERE table_schema = 'agent_market' AND table_name IN ('web_deny_hosts', 'org_suppliers', 'web_policy', 'web_calls') ORDER BY 1;
SELECT 'tables in the schema: ' || count(*) FROM information_schema.tables WHERE table_schema = 'agent_market' AND table_type = 'BASE TABLE';
SELECT 'keys that point out of the schema: ' || count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
 JOIN pg_class t ON t.oid = c.confrelid JOIN pg_namespace tn ON tn.oid = t.relnamespace
 WHERE c.contype = 'f' AND n.nspname = 'agent_market' AND tn.nspname <> 'agent_market';
SELECT 'keys that point into the schema: ' || count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
 JOIN pg_class t ON t.oid = c.confrelid JOIN pg_namespace tn ON tn.oid = t.relnamespace
 WHERE c.contype = 'f' AND n.nspname <> 'agent_market' AND tn.nspname = 'agent_market';
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
  awk '{ n[$1] = $2; t += $2 } END { printf "rows: installs %s · runs %s · steps %s · artifacts %s · web calls %s · denied sites %s · suppliers %s · all %d tables %d rows\n",
    n["agent_installs"], n["agent_runs"], n["agent_run_steps"], n["agent_output_artifacts"], n["web_calls"] == "" ? "-" : n["web_calls"],
    n["web_deny_hosts"] == "" ? "-" : n["web_deny_hosts"], n["org_suppliers"] == "" ? "-" : n["org_suppliers"], NR, t }' "$1"
}
fail=0

echo "== 0. the test stack's runner and chat-edge stop (they are built for 009)"
docker stop sran-test-agent-runner sran-test-chat-edge-api-1 >/dev/null 2>&1
echo "stopped: $(docker inspect -f '{{.Name}} {{.State.Status}}' sran-test-agent-runner sran-test-chat-edge-api-1 2>/dev/null | tr '\n' ' ')"

echo "== 1. UP — as the runner applied it"
shape; rows > "$OUT/rows_up_1.txt"; said "$OUT/rows_up_1.txt"
dump > "$OUT/schema_up_1.sql"
echo "schema: $(wc -l < "$OUT/schema_up_1.sql") statements' lines"

echo "== 2. DOWN — 009_am12.down.sql, one transaction"
if q -1 -q < "$MIG/009_am12.down.sql"; then echo "down applied: exit 0"; else echo "DOWN FAILED"; fail=1; fi
shape; rows > "$OUT/rows_down.txt"; said "$OUT/rows_down.txt"
# every table that is still there has the row count it had: the down took its own four tables, its own
# line in the list of migrations, and nothing else
other=$(diff <(grep -v -E '^(web_deny_hosts|org_suppliers|web_policy|web_calls|schema_migrations) ' "$OUT/rows_up_1.txt")   <(grep -v -E '^schema_migrations ' "$OUT/rows_down.txt") | grep -c '^[<>]')
echo "tables other than 009's own whose row count changed: $other"
[ "$other" -eq 0 ] || fail=1
was=$(grep -E '^schema_migrations ' "$OUT/rows_up_1.txt" | cut -d' ' -f2); now=$(grep -E '^schema_migrations ' "$OUT/rows_down.txt" | cut -d' ' -f2)
echo "the list of migrations: $was lines → $now (009's own line is gone)"
[ "$now" -eq $((was - 1)) ] || fail=1
dump > "$OUT/schema_down.sql"

echo "== 2b. the same schema as 001–008 on an empty database"
docker exec "$PG" psql -U chatedge -d sran_ai -q -c 'DROP DATABASE IF EXISTS am12_m008' -c 'CREATE DATABASE am12_m008' >/dev/null 2>&1 ||
  { echo "the scratch database could not be made"; fail=1; }
for f in $(ls "$MIG" | grep '\.up\.sql$' | sort | head -8); do
  DB=am12_m008 q -1 -q < "$MIG/$f" || { echo "scratch: $f failed"; fail=1; }
done
dump am12_m008 > "$OUT/schema_001_008.sql"
docker exec "$PG" psql -U chatedge -d sran_ai -q -c 'DROP DATABASE IF EXISTS am12_m008' >/dev/null 2>&1
n=$(diff "$OUT/schema_down.sql" "$OUT/schema_001_008.sql" | wc -l)
echo "diff(schema after the down, schema of 001–008): $n lines"
[ "$n" -eq 0 ] || fail=1

echo "== 3. UP again — the runner starts and migrates"
bash scripts/probes/AM7/test_stack.sh runner "$TAG" | grep -E "schema migrated|ERROR" | cut -c1-220
shape; rows > "$OUT/rows_up_2.txt"; said "$OUT/rows_up_2.txt"
dump > "$OUT/schema_up_2.sql"
n=$(diff "$OUT/schema_up_1.sql" "$OUT/schema_up_2.sql" | wc -l)
echo "diff(schema after the second up, schema after the first): $n lines"
[ "$n" -eq 0 ] || fail=1
n=$(diff "$OUT/schema_down.sql" "$OUT/schema_up_2.sql" | grep -c '^[<>]')
echo "lines 009 adds to the schema dump: $n"

echo "== 4. chat-edge back"
bash scripts/probes/AM7/test_stack.sh edge "$TAG" on | tail -1
echo "migration_009_exit=$fail"
exit $fail
