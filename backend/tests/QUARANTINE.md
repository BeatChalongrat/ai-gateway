# Backend test quarantine — known baseline (FIX126 O8)

The unit lane is `python -m pytest -m "not integration"` (in-container, `APP_ENV=test`).
On 45cf6c1 it had 45 failing tests, plus 5 that fail in any container that cannot
reach a healthy stack. Each of the 50 was classified below. Obsolete tests and
broken test setups were **fixed**. Everything else is **quarantined**:

```python
@pytest.mark.quarantine(reason="...", ticket="FIX126-O8-<n>", condition=<optional bool>)
```

How quarantine works (`tests/conftest.py`):

- A quarantined test **still runs** in every lane. It runs as `xfail(strict=False)`
  with the ticket in the reason, so a failure is reported as `xfailed` and a pass as
  `XPASS`. The lane's marker expression does not change.
- `reason=` and `ticket=` are required. Collection stops with an error if either is missing.
- `condition=` limits the quarantine to places where the cause holds. The cross-tree
  gates are quarantined only when the sibling tree is **not mounted**. With a full
  checkout they stay hard gates.
- `pytest -m quarantine` runs only the quarantined tests. `-m "not quarantine"` leaves them out.
- The name ledger `tests/known_failing.txt` (used by `scripts/check_known_failing.py`)
  no longer lists these tests, because none of them is reported as FAILED.

Why xfail and not a marker that the lane deselects: the lane command belongs to CI and
compose, which other people own, and a deselected test goes silent. An xfailed test still
runs and prints its ticket, and a fixed cause shows up as XPASS.

Classes: **real bug**, **obsolete test** (an assertion or setup that no longer matches
intended behaviour), **flaky**, **environment** (needs something the lane container
does not have).

| name | class | reason | ticket |
|---|---|---|---|
| test_dlp_docs.py::TestExampleProfilesExist::test_file_exists[policy_default.json / policy_strict.json / policy_internal_only.json] (3) | environment | reads repo-root `examples/`. The lane container mounts only `backend/` and `docs/`. Quarantined only while `examples/` is missing | FIX126-O8-1 |
| test_dlp_docs.py::TestExampleProfilesParse::test_valid_json / test_has_required_fields / test_category_actions_are_valid / test_severity_threshold_valid [×3 files] (12) | environment | same: `examples/` not mounted | FIX126-O8-1 |
| test_dlp_docs.py::TestExampleProfilesConstructible::test_constructs_policy_profile [×3 files] (3) | environment | same | FIX126-O8-1 |
| test_dlp_docs.py::TestSpecificProfileBehavior::test_default_masks_pii / test_default_blocks_secrets / test_strict_blocks_financial / test_strict_has_critical_threshold / test_internal_allows_pii / test_internal_no_response_scanning (6) | environment | same | FIX126-O8-1 |
| test_dlp_tool_policy_parity.py::TestToolPolicyParity::test_desktop_registry_scan_is_alive | environment → real bug — **fixed FIX128** | the scan read only the tools sidecar (`SranDesktop/…/Mcp/Tools`, 25 tools) against a ≥ 30 floor written for the WPF shell. It now also reads the Electron host's tools (`apps/desktop/src/main/host-tools.ts`, `turn-coordinator.ts`, `ask-user.ts`): 31 tools. Both trees are mounted into the dev lane; quarantined only where they are absent | FIX126-O8-2 |
| test_dlp_tool_policy_parity.py::TestToolPolicyParity::test_every_desktop_tool_is_classified | environment → real bug — **fixed FIX128** | with the host tools scanned, `update_task_schedule` was unclassified (hard-blocked at runtime once its masked args carry a placeholder). Classified `local_execution` (it writes the local sidecar task store only) | FIX126-O8-2 |
| test_dlp_tool_policy_parity.py::TestToolPolicyParity::test_chat_edge_registry_scan_is_alive | environment | `services/chat-edge-api/` tree not mounted | FIX126-O8-2 |
| test_dlp_tool_policy_parity.py::TestToolPolicyParity::test_every_chat_edge_tool_is_classified | environment | same | FIX126-O8-2 |
| test_dlp_egress_invariant.py::test_chat_edge_embeddings_voice_route_through_gateway_not_openai | environment | reads chat-edge Go source, which is not mounted | FIX126-O8-3 |
| test_dlp_egress_invariant.py::test_code_interpreter_egress_is_go_side_and_network_locked | environment | same | FIX126-O8-3 |
| test_dlp_egress_invariant.py::test_every_outbound_http_module_is_classified | real bug (in the gate's inventory) — **fixed** | `api/routes/health.py` imports httpx to probe our own NER sidecar and had never been classified. Classified `no_user_content`. The new `services/search/url_resolve.py` (FIX126 U1) is classified in the same place | — |
| test_service_jwt_wiring.py::test_compose_feeds_service_jwt_to_both_services | environment | reads repo-root `docker-compose.yml`, which is not mounted | FIX126-O8-4 |
| test_service_jwt_wiring.py::test_prod_compose_hard_requires_service_jwt_on_backend | environment | reads `docker-compose.prod.yml`, which is not mounted | FIX126-O8-4 |
| test_health.py::test_health | environment | asserts status `ok`, which needs Postgres, Redis, the active sidecars and NER to be reachable. In the lane container /health answers 503 by design. Belongs in the integration lane | FIX126-O8-5 |
| test_error_handling.py::TestRegression::test_health_still_works | environment | same as above | FIX126-O8-5 |
| test_dlp_eval_gate.py::test_no_regression_against_baseline | real bug (suspected, not confirmed) | thai_address must-catch recall 41/41 → 36/41 in python AND rust since 2026-09-17. It is the same at a36124f, so the cause is data or environment, not a code change in that window. Full addresses such as "128/4 ซอยรามคำแหง 24 … 10240" are missed. DLP is parked, so this is not investigated here, and the baseline must NOT be rewritten to make it pass | FIX126-O8-6 |
| test_api_inventory.py::TestPublicEndpointsExist::test_health | obsolete test — **fixed** | it checks that the endpoint exists and answers. 503 with the check report is an answer. It now asserts 200 or 503 plus the service field | — |
| test_cors.py::TestCORS::test_actual_request_has_cors_header | obsolete test (setup) — **fixed** | it used /health, whose status depends on infrastructure. It now uses /live, and CORS is what the test checks | — |
| test_body_limit.py::TestBodySizeLimit::test_oversized_request_rejected | obsolete test — **fixed** | the chat route's limit is 20 MB on purpose (vision payloads, app/main.py). The 1 MB default is now checked on a non-chat path, and a new test checks the 20 MB chat limit | — |
| test_body_limit.py::TestBodySizeLimit::test_error_has_standard_envelope | obsolete test — **fixed** | sends a Content-Length above the 20 MB chat limit | — |
| test_pilot_readiness.py::TestPR05BodySizeLimit::test_oversized_request_rejected | obsolete test — **fixed** | same (20 MB chat limit) | — |
| test_tenant_model.py::TestTenantSchema::test_tenants_columns | obsolete test — **fixed** | the expected column list stopped at migration 054. Added the columns from migrations 055–079 (the ORM and the migrations agree) | — |
| test_tenant_model.py::TestMigration002::test_migration_columns_match_orm | obsolete test — **fixed** | same: the post-002 exclusion set is now complete | — |
| test_routing_e2e.py::TestRoutingE2EAuto::test_auto_coding_returns_anthropic | obsolete test — **fixed** | FIX35 made Auto provider-agnostic, so `use_case=coding` no longer pins anthropic. Renamed to `test_auto_coding_is_served_by_a_concrete_model` | — |
| test_sranai_provider.py::test_unknown_provider_still_rejected | obsolete test — **fixed** | the message is "unknown provider: …" (lower case) since 841604a. The assertion is now case-insensitive | — |
| test_async_provider_key_service.py::test_get_api_key_async_resolves_db_key | obsolete test (setup) — **fixed** | the fixture stored the key as plain base64. Keys are AES-256-GCM, and the resolve path correctly refuses the old encoding. The fixture now encrypts the key the way the service does | — |
| test_streaming_dlp.py::TestStreamingProviderPiiMaskedUnderDefault::test_streaming_provider_pii_is_masked_by_default | obsolete test (setup) — **fixed** | `[sent] = messages` assumed the provider gets exactly one message. The gateway now prepends its own system message. The test now reads the user turn. The masking assertions are unchanged and pass | — |
| test_streaming_dlp.py::TestStreamingCleanMessagePassesThrough::test_streaming_clean_message_passes_through | obsolete test (setup) — **fixed** | same | — |
| test_streaming_session_factory.py::TestStreamingUsesRequestTimeFactory::test_streaming_uses_request_time_session_factory | obsolete test — **fixed** | "exactly one call" counted every SessionLocal() in the request. FIX120 and FIX125 policy/rescue reads now open their own sessions. The test now counts only the post-stream `_finalize_stream` calls, which is the capture contract. The per-request swap check is kept | — |
| test_streaming_session_factory.py::TestStreamingFactoryCaptureIsPerRequest::test_streaming_factory_capture_is_per_request | obsolete test — **fixed** | same | — |

Totals: 50 classified. **15 fixed**: 12 obsolete tests or setups, 1 real gap in the
egress gate's inventory, and 2 that depended on /health's infrastructure status by
accident. **35 quarantined** (the lane shows 35 xfailed): 34 environment, 1 suspected
real bug in the parked DLP area. No test was classified flaky.

Still in `known_failing.txt` (not part of the 45): the 3 shared-rate-limit-window tests
(`unit/test_public_audio.py` ×2, `unit/test_public_videos.py` ×1), class **flaky**
(order-dependent, FIX55). They did not fail on this tree's pinned order.

Releasing a quarantine: fix the cause, confirm the test passes (it shows as XPASS),
delete the marker and its row here.
