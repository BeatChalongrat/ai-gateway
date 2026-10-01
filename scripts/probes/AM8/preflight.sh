#!/usr/bin/env bash
# AM8 — before a live swap: no chat turn generating and none in the last 10 minutes (the chat DB),
# no Agent Market round queued or running, the backend's turn queue empty. Read-only; prints counts.
set -uo pipefail
q() { docker exec ai-gateway-postgres-webchat psql -U chatedge -d sran_ai -At -c "$1" 2>/dev/null; }
echo "turns streaming now:          $(q "select count(*) from chat.messages_masked where status='streaming' and created_at > now() - interval '30 minutes'")"
echo "assistant turns last 10 min:  $(q "select count(*) from chat.messages_masked where role='assistant' and created_at > now() - interval '10 minutes'")"
echo "rounds queued or running:     $(q "select count(*) from agent_market.agent_runs where status in ('queued','running')")"
echo "rounds updated last 10 min:   $(q "select count(*) from agent_market.agent_runs where updated_at > now() - interval '10 minutes'")"
echo "backend turn queue:           $(curl -s http://127.0.0.1:8081/ready 2>/dev/null | tr -d '\n' | grep -o '"turn_queue":{[^}]*}' || echo unknown)"
