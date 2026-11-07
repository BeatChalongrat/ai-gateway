-- UAT verification — run after 02_seed_uat.py finishes.
--
-- Expected output:
--   tenants        | 3        (demo-tenant-000 + acme + techstart)
--   projects       | 3
--   portal_users   | 4
--   api_keys       | 3
--   model_configs  | 48       (= len(SEED_CANDIDATES); was 14+ when the
--                              discovery sync had also imported openrouter
--                              and openai catalogs)
--   policies       | 1        (the "strict" profile)
--   admin_users    | 1

SELECT 'tenants'       AS table_name, COUNT(*) AS rows FROM tenants
UNION ALL SELECT 'projects',      COUNT(*) FROM projects
UNION ALL SELECT 'portal_users',  COUNT(*) FROM portal_users
UNION ALL SELECT 'api_keys',      COUNT(*) FROM api_keys WHERE status = 'active'
UNION ALL SELECT 'model_configs (active)',
                                  COUNT(*) FROM model_configs WHERE is_active = true
UNION ALL SELECT 'policies',      COUNT(*) FROM policies
UNION ALL SELECT 'admin_users',   COUNT(*) FROM admin_users
ORDER BY table_name;

-- Tenant isolation spot-check: each portal user should reference exactly
-- one of the three seeded tenants.
SELECT tenant_id, COUNT(*) AS portal_user_count
FROM portal_users
GROUP BY tenant_id
ORDER BY tenant_id;

-- Per-provider model count (should match SEED_CANDIDATES distribution).
SELECT provider, COUNT(*) AS active_models
FROM model_configs
WHERE is_active = true
GROUP BY provider
ORDER BY provider;
