#!/usr/bin/env python3
"""FIX128 C — seed the test stack (project ``sran-test``).

What it writes, all inside the TEST containers:

* the model catalogue copied read-only from the live gateway DB —
  ``providers``, ``provider_keys`` (still encrypted with the same
  MASTER_ENCRYPTION_KEY from .env; the dump goes through a pipe, never a
  file), ``model_configs``, ``keyword_rules``, ``system_settings``;
* a DEAD model: provider key ``ts-dead`` (sranai) whose endpoint is a closed
  port inside its own container (``http://127.0.0.1:1/v1``) and a model
  ``ts-dead-model`` served only by it, in qwen's cost tier, set as Auto's
  admin preference (``auto_default_model``) so Auto reaches for it first;
* ``ts-mock-model`` served by the test mock provider through key ``ts-mock`` —
  a model that answers and can then be made dead (flip the key's base_url);
* a test tenant and owner (``is_test``-style ids ``f1280000-…``) with credits.

Idempotent: re-running replaces the catalogue and upserts the test rows.
Nothing is read from or written to the live stack except the read-only
``pg_dump`` of the catalogue tables.
"""
from __future__ import annotations

import json
import subprocess
import sys

LIVE_PG = "ai-gateway-postgres-1"
TEST_PG = "sran-test-postgres-1"
TEST_WEBCHAT_PG = "sran-test-postgres-webchat-1"

TENANT = "f1280000-0000-4000-8000-000000000001"
USER = "f1280000-0000-4000-8000-000000000011"
EMAIL = "ts-owner@fix128.test"
DEAD_KEY = "f1280000-0000-4000-8000-00000000dead"
DEAD_MODEL = "ts-dead-model"
MOCK_KEY = "f1280000-0000-4000-8000-00000000a0c0"
MOCK_MODEL = "ts-mock-model"   # answers from the test mock provider; its key can be flipped dead mid-test
REAL_MODEL = "qwen3.8-27b-fp8"

# Global catalogue only — tenant-scoped tables (search_providers,
# policy_profiles) reference live tenants and are not copied.
CATALOGUE = ["providers", "provider_keys", "model_configs", "keyword_rules", "system_settings"]


def sh(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def test_sql(sql: str, db: str = "ai_gateway") -> str:
    r = subprocess.run(["docker", "exec", "-i", TEST_PG, "psql", "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-At"],
                       input=sql, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"test SQL failed: {r.stderr[-800:]}")
    return r.stdout


def main() -> int:
    # Refuse to run unless the target really is the test stack.
    label = sh(["docker", "inspect", TEST_PG, "--format", '{{index .Config.Labels "com.docker.compose.project"}}']).strip()
    if label != "sran-test":
        print(f"REFUSED: {TEST_PG} belongs to project {label!r}, not sran-test", file=sys.stderr)
        return 1

    # 1. catalogue: live (read-only dump) -> test, through a pipe
    dump = subprocess.run(
        ["docker", "exec", LIVE_PG, "pg_dump", "-U", "postgres", "-d", "ai_gateway", "--data-only",
         "--column-inserts", "--no-owner"] + sum((["-t", t] for t in CATALOGUE), []),
        check=True, capture_output=True)
    test_sql("TRUNCATE " + ", ".join(CATALOGUE) + " CASCADE;")
    r = subprocess.run(["docker", "exec", "-i", TEST_PG, "psql", "-U", "postgres", "-d", "ai_gateway", "-q",
                        "-v", "ON_ERROR_STOP=1"], input=dump.stdout, capture_output=True)
    if r.returncode != 0:
        print("catalogue restore failed:", r.stderr.decode("utf-8", "replace")[-800:], file=sys.stderr)
        return 1

    # 2. the dead model: a key at a closed port, a model served only by it
    enc = test_sql(f"select api_key_encrypted from provider_keys where provider_name='sranai' and label='9beat' limit 1").strip()
    served = json.dumps({DEAD_KEY: {"at": "2026-09-22T00:00:00+00:00", "label": "ts-dead", "base_url": "http://127.0.0.1:1/v1"}})
    test_sql(f"""
      insert into provider_keys (id, provider_name, label, api_key_encrypted, is_active, created_at, base_url)
      values ('{DEAD_KEY}', 'sranai', 'ts-dead', '{enc}', true, now(), 'http://127.0.0.1:1/v1')
      on conflict (id) do update set base_url = excluded.base_url, is_active = true;
      insert into model_configs (id, provider, model, supports_streaming, supports_tools, supports_vision,
          max_context_tokens, cost_tier, latency_tier, cost_per_input_token, cost_per_output_token, source,
          is_manual_override, is_active, created_at, updated_at, show_in_chat, served_by_keys)
      select (select coalesce(max(id),0)+1 from model_configs), 'sranai', '{DEAD_MODEL}', true, true, false, 131072, cost_tier, latency_tier,
             0, 0, 'manual', true, true, now(), now(), true, '{served}'::jsonb
      from model_configs where model = '{REAL_MODEL}'
      on conflict do nothing;
      insert into provider_keys (id, provider_name, label, api_key_encrypted, is_active, created_at, base_url)
      values ('{MOCK_KEY}', 'sranai', 'ts-mock', '{enc}', true, now(), 'http://mock-provider:9090/v1')
      on conflict (id) do update set base_url = excluded.base_url, is_active = true;
      insert into model_configs (id, provider, model, supports_streaming, supports_tools, supports_vision,
          max_context_tokens, cost_tier, latency_tier, cost_per_input_token, cost_per_output_token, source,
          is_manual_override, is_active, created_at, updated_at, show_in_chat, served_by_keys)
      select (select coalesce(max(id),0)+1 from model_configs), 'sranai', '{MOCK_MODEL}', true, false, false, 131072,
             cost_tier, latency_tier, 0, 0, 'manual', true, true, now(), now(), true,
             '{json.dumps({MOCK_KEY: {"at": "2026-09-22T00:00:00+00:00", "label": "ts-mock", "base_url": "http://mock-provider:9090/v1"}})}'::jsonb
      from model_configs where model = '{REAL_MODEL}'
        and not exists (select 1 from model_configs where model = '{MOCK_MODEL}');
      -- the test models look exactly like qwen to the router's capability filters
      update model_configs d set supports_reasoning = q.supports_reasoning, reasoning_strategy = q.reasoning_strategy,
             default_reasoning_mode = q.default_reasoning_mode, capability_provenance = q.capability_provenance,
             supports_tools = q.supports_tools, max_context_tokens = q.max_context_tokens
        from model_configs q where q.model = '{REAL_MODEL}' and d.model in ('{DEAD_MODEL}', '{MOCK_MODEL}');
      update system_settings set value = '{DEAD_MODEL}' where key = 'auto_default_model';
      update system_settings set value = '{REAL_MODEL},deepseek-v4-flash' where key = 'auto_default_fallbacks';
    """)

    # 3. test tenant + owner with credits
    test_sql(f"""
      insert into tenants (tenant_id, name, slug, status, region, created_at, updated_at)
      values ('{TENANT}', 'FIX128 test tenant', 'fix128-test', 'active', 'th', now(), now())
      on conflict (tenant_id) do nothing;
      insert into portal_users (id, email, password_hash, display_name, credit_balance, credit_used, plan, status,
          email_verified, created_at, updated_at, tenant_id, tenant_role, token_version)
      values ('{USER}', '{EMAIL}', '!', 'FIX128 test owner', 10000000, 0, 'free', 'active', true, now(), now(),
              '{TENANT}', 'owner', 0)
      on conflict (id) do update set credit_balance = 10000000, credit_used = 0;
    """)

    counts = test_sql("select (select count(*) from model_configs) || ' models, ' || (select count(*) from provider_keys) || ' keys, ' || "
                      "(select count(*) from tenants) || ' tenants, ' || (select value from system_settings where key='auto_default_model')")
    dead = test_sql(f"select model, served_by_keys::text from model_configs where model='{DEAD_MODEL}'")
    print("seeded:", counts.strip())
    print("dead model:", dead.strip()[:160])
    print(f"tenant={TENANT} user={USER} ({EMAIL})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
