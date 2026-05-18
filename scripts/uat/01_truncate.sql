-- UAT reset — wipes all customer data while keeping schema + alembic_version.
--
-- Run BEFORE 02_seed_uat.py. Idempotent (TRUNCATE works on empty tables).
-- alembic_version is intentionally NOT in the list so migrations stay at HEAD.
--
-- Tables order is irrelevant because CASCADE handles FK chains.
--
-- Verification queries are in 03_verify.sql.

BEGIN;

TRUNCATE TABLE
  request_logs,
  request_logs_archive,
  portal_users,
  tenant_invites,
  project_members,
  projects,
  api_keys,
  provider_keys,
  keyword_rules,
  alert_rules,
  alert_events,
  policies,
  policy_profiles,
  org_settings,
  token_packs,
  admin_audit_log,
  tenants,
  admin_users,
  model_configs
RESTART IDENTITY CASCADE;

-- Sanity checks (visible in psql output)
SELECT 'tenants'      AS table_name, COUNT(*) AS rows FROM tenants
UNION ALL SELECT 'portal_users', COUNT(*) FROM portal_users
UNION ALL SELECT 'admin_users',  COUNT(*) FROM admin_users
UNION ALL SELECT 'model_configs', COUNT(*) FROM model_configs;

COMMIT;
