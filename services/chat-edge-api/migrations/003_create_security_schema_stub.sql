-- 003_create_security_schema_stub.sql
--
-- The ``security`` schema (Token Vault + Secure Trace) is OWNED by
-- the Gateway service. This file exists to:
--
--   1. Document the boundary so an operator reading the chat-edge
--      migrations knows what is intentionally absent.
--   2. Pre-create the schema so subsequent FK-by-name references
--      from ``chat.messages_masked.secure_trace_id`` (logical FK
--      only — no enforced reference; the cross-service boundary
--      forbids that) are unambiguous.
--   3. Pin the privileges contract: chat-edge's database role must
--      have ZERO permissions on this schema. The REVOKE statement
--      below is a no-op when ``chat_edge_role`` doesn't exist yet
--      (the production migration creates the role first); during
--      local dev with the ``chatedge`` superuser the REVOKE is
--      skipped via the IF EXISTS guard.

CREATE SCHEMA IF NOT EXISTS security;

COMMENT ON SCHEMA security IS
  'Gateway-owned. Token Vault + Secure Trace tables live here. '
  'chat-edge-api DB role has zero permissions on this schema. '
  'Created and populated by gateway-api migrations.';

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chat_edge_role') THEN
    REVOKE ALL ON SCHEMA security FROM chat_edge_role;
  END IF;
END
$$;
