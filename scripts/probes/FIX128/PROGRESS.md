# FIX128 progress

Start 2026-09-22 ~19:30 +07. State: main = gitlab/main = 32c3747; chat-edge/web-chat 32c3747; backend/gateway 380b3f4; e53.
Peer ai-gateway-9b idle (not on this card). Model: qwen primary; no manual switch.

- [x] B guard: Go tests fail-closed since FIX118/119 (2026-09-14). Leak writer = services/chat-edge-api/internal/service/import_service_test.go (opened live DB by default until bea156f). Backend lane had NO Postgres tripwire → tests/_pg_isolation.py + test_fix128_pg_isolation.py (red with opt-in: engine from settings reached live DB, select 1 ran → green). Lane with tripwire: 4489 passed, 0 failed.
- [x] B inventory (live chat DB, read-only): 2,582 tenants / 2,619 users / 3,145 conversations / 3,808 messages / 313 files / 3 memories / 966 audit rows whose user is not in identity.users; 2026-01-02 → 2026-09-14. identity.users: 154 fake-domain (@example.com/@x.com/@local) 2026-05-25 → 2026-09-04, 1 is_test. NOT deleted — owner decision.
- [x] A manifest results/a_manifest.txt: 272 rows (112 real 14,986,440 B + 160 five-byte test rows); 95 blobs present, 177 already absent. Keep-set baseline results/a_keep_before.txt (351 attached/generated rows).
- [x] A audit: one summary per run; dry run only on change (Go red "3 rows for 3 starts" → green). .env: UNATTACHED_FILE_SWEEP_MODE=delete (owner-approved).
- [x] C test stack up (12 services, sran-test); seed; live snapshot before/after identical.
- [x] C B3 on current backend: dead model → all_models_failed + provider-wide marker skips qwen next turn (BUG). Fix = account_of(provider@endpoint) (worktree 5f9fce3). On fix image: B3 PASS, L1 PASS. B2a PASS, B2b PASS (gateway rescue writes the title; chat-edge reports the model it asked).
- [x] D parity scan: reads sidecar + Electron host tools (31); update_task_schedule classified local_execution (worktree 558e9be).
- [x] D4: 360 px composer toolbar (send button was clipped), arrow-key chat list, skip link. Artifact versions: left over.
- [x] Deploy: backend cherry-picked (47b08be, 1a43288) → reload 24 s, recreate 18 s (new mounts); chat-edge + web-chat 1a43288 swap 9 s. Sweep first pass: candidates 272, deleted 272, kept 0, errors 0 = manifest; 0 manifest rows/blobs left; keep-set 351/351. Audit spam 966 rows deleted (audit only); 1 summary row.
- [x] Live regression: a1 PASS, d1 correct by rows (harness race), b1 PASS, rename PASS, files5 PASS (5 chips, 422 on 6), failed turn = 0 credits (test stack, all_models_failed). D4 live web + desktop PASS. Desktop reopen c2 PASS. 8 [FIX128] chats deleted by id.
- [x] Test stack down (volumes kept). Live snapshot before/after identical.
