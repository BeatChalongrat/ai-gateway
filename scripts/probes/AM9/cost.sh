#!/usr/bin/env bash
# AM9 (J) — did AM8's read-back raise the cost of an ordinary round? One identical round on each runner
# image: the catalog's report sample (the same three files, the same settings), made on a fresh
# agent_market schema of the TEST stack, twice per image; each step's credits and time read back from
# the test database. Never the live database.
#
#   bash scripts/probes/AM9/cost.sh 761ec3c 2b39824 <am9-tag>  > scripts/probes/AM9/results/cost.tsv
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TS="$ROOT/scripts/probes/AM7/test_stack.sh"
DB=sran-test-postgres-webchat-1
q() { docker exec "$DB" psql -U chatedge -d sran_ai -At -F $'\t' -c "$1"; }
echo -e "image\trep\tversion\tstep\tcredits\tms\ttokens"
for tag in "$@"; do
  for rep in 1 2; do
    bash "$TS" fresh >/dev/null 2>&1
    bash "$TS" runner "$tag" >/dev/null 2>&1
    for _ in $(seq 1 120); do
      st=$(q "select r.status from agent_market.agent_runs r join agent_market.agent_installs i on i.id = r.install_id
              where r.kind = 'self_test' and i.slug = 'sran.financial-report' order by r.created_at desc limit 1")
      [ "$st" = "succeeded" ] || [ "$st" = "failed" ] && break
      sleep 5
    done
    q "select '$tag', $rep, r.definition_version, s.name, round(s.credits::numeric, 1), s.duration_ms,
              coalesce(s.output->>'tokens', '')
       from agent_market.agent_runs r join agent_market.agent_installs i on i.id = r.install_id
       join agent_market.agent_run_steps s on s.run_id = r.id
       where r.kind = 'self_test' and i.slug = 'sran.financial-report' order by s.seq"
  done
done
