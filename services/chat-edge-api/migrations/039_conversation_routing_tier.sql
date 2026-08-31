-- 039_conversation_routing_tier.sql
--
-- FIX35 Part C — per-conversation cost-tier ceiling for the Auto router.
-- '' (default) = use the tenant / deployment default; otherwise one of
-- low | medium | high | xhigh | max. Forwarded to the gateway as
-- routing_hints.tier on every "auto" turn; ignored for an explicit model.
--
-- Idempotent — safe to re-run.

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS routing_tier TEXT NOT NULL DEFAULT '';
