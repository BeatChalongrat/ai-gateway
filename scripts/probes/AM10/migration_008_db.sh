#!/usr/bin/env bash
# AM10 — migration 008 rehearsed on the TEST stack's chat database alone: up, down, up, and back as
# found. Only one container of the test project runs (its Postgres, on its own volume with the rows
# earlier cards' walks left at 001–007) — the way to see the migration when the machine has no memory
# for the whole stack. migration_008.sh is the same look on the whole stack, after the walks.
#
#   as found  what the database records (001–007 expected) and its schema, kept for comparison
#   up        008_am10.up.sql and its row in one transaction — what the migrator does (migrate.go);
#             then a value in every place 008 adds: on the oldest install and run that are there,
#             and on three tagged rows (a chat named, pinned and deleted; a file marked "form"; a
#             thread item of kind "form")
#   down      008_am10.down.sql in one transaction → the schema is line for line the one found
#   up again  → the schema is line for line the one after the first up
#   as found  the down once more and the tagged chat and file rows deleted by id: every table has
#             the row count it had
#
# Nothing of the live project is named. The database's password is never read: psql runs inside the
# container over its local socket.
#
#   bash scripts/probes/AM10/migration_008_db.sh [keep]     ("keep" leaves the Postgres container up)
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
PG=sran-test-postgres-webchat-1
MIG=services/chat-edge-api/internal/agentmarket/migrations
OUT=scripts/probes/AM10/results/migration_008_db
mkdir -p "$OUT"
q() { docker exec -i "$PG" psql -U chatedge -d sran_ai -v ON_ERROR_STOP=1 -At -F ' | ' "$@"; }
dump() {
  docker exec "$PG" pg_dump -U chatedge -d sran_ai --schema-only --no-owner --no-privileges -n agent_market |
    grep -v -e '^--' -e '^$' -e '^\\' -e '^SET ' -e '^SELECT pg_catalog.set_config' | tr -d '\r'
}
shape() {
  q <<'SQL'
SELECT 'columns of 008 present: ' || count(*) || ' of 7' FROM information_schema.columns
 WHERE table_schema = 'agent_market' AND (
   (table_name = 'agent_threads' AND column_name IN ('title', 'pinned_at', 'deleted_at')) OR
   (table_name = 'agent_installs' AND column_name IN ('first_chat_title', 'first_chat_pinned_at', 'first_chat_deleted_at')) OR
   (table_name = 'agent_runs' AND column_name = 'round_template'));
SELECT conname || ' allows ''form'': ' || (pg_get_constraintdef(oid) LIKE '%''form''%')
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
up() { { cat "$MIG/008_am10.up.sql"; echo "INSERT INTO agent_market.schema_migrations (version) VALUES ('008_am10') ON CONFLICT DO NOTHING;"; } | q -1 -q; }
down() { q -1 -q < "$MIG/008_am10.down.sql"; }
fail=0
same() { # <a> <b> <what>
  local n
  n=$(diff "$OUT/$1" "$OUT/$2" | wc -l)
  echo "diff($3): $n lines"
  [ "$n" -eq 0 ] || fail=1
}

echo "== 0. the test stack's chat database, alone"
docker compose -p sran-test -f docker-compose.test-stack.yml up -d --no-deps --wait --wait-timeout 120 postgres-webchat 2>&1 |
  grep -v "level=warning" | tail -2
echo "containers of the test project running: $(docker ps --filter label=com.docker.compose.project=sran-test --format '{{.Names}}' | tr '\n' ' ')"

echo "== 1. AS FOUND"
if [ "$(q -c "SELECT to_regclass('agent_market.schema_migrations') IS NOT NULL")" != t ]; then
  echo "this database has no agent_market schema (or cannot be reached): nothing to rehearse on — stopping without a change"
  exit 2
fi
shape; rows > "$OUT/rows_as_found.txt"; said "$OUT/rows_as_found.txt"
dump > "$OUT/schema_as_found.sql"
if [ "$(q -c "SELECT count(*) FROM agent_market.schema_migrations WHERE version = '008_am10'")" != 0 ]; then
  echo "008 is already recorded here: this rehearsal starts from 007 — stopping without a change"
  exit 2
fi

echo "== 2. UP — 008_am10.up.sql and its row, one transaction"
if up; then echo "up applied: exit 0"; else echo "UP FAILED"; fail=1; fi
shape; rows > "$OUT/rows_up_1.txt"; said "$OUT/rows_up_1.txt"
dump > "$OUT/schema_up_1.sql"
echo "lines 008 adds to the schema: $(diff "$OUT/schema_as_found.sql" "$OUT/schema_up_1.sql" | grep -c '^>') · removes: $(diff "$OUT/schema_as_found.sql" "$OUT/schema_up_1.sql" | grep -c '^<')"

echo "== 2b. something in every place 008 adds"
q <<'SQL'
INSERT INTO agent_market.agent_threads (id, install_id, tenant_id, user_id, title, pinned_at, deleted_at)
 SELECT '00000000-0000-4000-8000-0000000c1008', id, tenant_id, user_id, '[AM10] rehearsal', now(), now()
   FROM agent_market.agent_installs ORDER BY created_at LIMIT 1;
UPDATE agent_market.agent_installs SET first_chat_title = '[AM10] rehearsal', first_chat_pinned_at = now(), first_chat_deleted_at = now()
 WHERE id = (SELECT id FROM agent_market.agent_installs ORDER BY created_at LIMIT 1);
UPDATE agent_market.agent_runs SET round_template = '{"status": "filled", "name": "[AM10] rehearsal.docx"}'
 WHERE id = (SELECT id FROM agent_market.agent_runs ORDER BY created_at LIMIT 1);
INSERT INTO agent_market.agent_files (id, tenant_id, user_id, install_id, name, size, sha256, storage_key, status)
 SELECT '00000000-0000-4000-8000-0000000f1008', tenant_id, user_id, id, '[AM10] rehearsal form.docx', 1, 'am10-rehearsal', 'agent-market/am10-rehearsal', 'form'
   FROM agent_market.agent_installs ORDER BY created_at LIMIT 1;
INSERT INTO agent_market.agent_thread_items (install_id, tenant_id, user_id, kind, payload, client_id)
 SELECT id, tenant_id, user_id, 'form', '{"am10": "rehearsal"}', 'am10-rehearsal' FROM agent_market.agent_installs ORDER BY created_at LIMIT 1;
SELECT 'held: chats ' || (SELECT count(*) FROM agent_market.agent_threads WHERE title IS NOT NULL AND pinned_at IS NOT NULL AND deleted_at IS NOT NULL)
    || ' · first chats ' || (SELECT count(*) FROM agent_market.agent_installs WHERE first_chat_title IS NOT NULL)
    || ' · rounds with a form ' || (SELECT count(*) FROM agent_market.agent_runs WHERE round_template IS NOT NULL)
    || ' · files marked form ' || (SELECT count(*) FROM agent_market.agent_files WHERE status = 'form')
    || ' · items of kind form ' || (SELECT count(*) FROM agent_market.agent_thread_items WHERE kind = 'form');
SQL
rows > "$OUT/rows_held.txt"; said "$OUT/rows_held.txt"

echo "== 3. DOWN — 008_am10.down.sql, one transaction"
if down; then echo "down applied: exit 0"; else echo "DOWN FAILED"; fail=1; fi
shape; rows > "$OUT/rows_down.txt"; said "$OUT/rows_down.txt"
echo "row counts that differ from as found: $(diff "$OUT/rows_as_found.txt" "$OUT/rows_down.txt" | grep '^[<>]' | tr '\n' ' ')— the two tagged rows: the chat (its row stays, 008's columns go) and the form's file (kept, as removed)"
q -c "SELECT 'the form''s file is kept, as removed: ' || count(*) FROM agent_market.agent_files WHERE id = '00000000-0000-4000-8000-0000000f1008' AND status = 'removed'"
q -c "SELECT 'the form''s item is gone: ' || (count(*) = 0) FROM agent_market.agent_thread_items WHERE client_id = 'am10-rehearsal'"
dump > "$OUT/schema_down.sql"
same schema_down.sql schema_as_found.sql "schema after the down, schema as found at 007"

echo "== 4. UP AGAIN"
if up; then echo "up applied: exit 0"; else echo "SECOND UP FAILED"; fail=1; fi
shape; rows > "$OUT/rows_up_2.txt"; said "$OUT/rows_up_2.txt"
dump > "$OUT/schema_up_2.sql"
same schema_up_2.sql schema_up_1.sql "schema after the second up, schema after the first"

echo "== 5. BACK AS FOUND — the down once more, the two tagged rows deleted by id"
if down; then echo "down applied: exit 0"; else echo "LAST DOWN FAILED"; fail=1; fi
q -c "DELETE FROM agent_market.agent_files WHERE id = '00000000-0000-4000-8000-0000000f1008'" \
  -c "DELETE FROM agent_market.agent_threads WHERE id = '00000000-0000-4000-8000-0000000c1008'" >/dev/null
shape; rows > "$OUT/rows_end.txt"; said "$OUT/rows_end.txt"
dump > "$OUT/schema_end.sql"
same schema_end.sql schema_as_found.sql "schema at the end, schema as found"
same rows_end.txt rows_as_found.txt "every table's row count at the end, as found"

if [ "${1:-}" != keep ]; then
  docker compose -p sran-test -f docker-compose.test-stack.yml stop postgres-webchat 2>&1 | grep -v "level=warning" | tail -1
  docker compose -p sran-test -f docker-compose.test-stack.yml rm -f postgres-webchat 2>&1 | grep -v "level=warning" | tail -1
fi
echo "containers of the test project running: $(docker ps --filter label=com.docker.compose.project=sran-test --format '{{.Names}}' | tr '\n' ' ')"
echo "migration_008_db_exit=$fail"
exit $fail
