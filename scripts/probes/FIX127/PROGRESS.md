# FIX127 progress

Start 2026-09-22. Session ai-gateway-63. Peer ai-gateway-9b idle (read-only session from FIX124, not on this card).
State at start: main = gitlab/main = 36a9aa6; chat-edge 36a9aa6; web-chat/gateway/backend 380b3f4; desktop e52.
Model: qwen primary. Switches to deepseek-v4-flash logged here with time + reason.

## Checkpoints
- [x] A1 server: FileRepo.ListForModelContext (message_id IS NOT NULL OR is_generated) used by waitForExtraction + excel/doc/pptx; DELETE /conversations/{id}/files/by-client/{clientID}; Go red (old: "a file that was never sent reached the model") → green 3/3. Web: uploadRegistry (abort + delete by id / by client_id + late retry; queued chip skipped); vitest red 3/4 → green 4/4
- [x] A2 UnattachedFileSweep: UNATTACHED_FILE_TTL=48h, UNATTACHED_FILE_SWEEP_MODE=dry_run (default)|delete|off, interval 1h, cap 500, audited. Tests 3/3. Live dry-run numbers (read-only SQL, results/a2_dryrun_before.txt): 470 unattached rows / 104 MB total; real 223 / 44 MB (77 older than 48 h); test residue 164 ≤5 B; test tenant 35. 30-day purge of deleted chats = existing transcript retention sweep, but chat.tenant_retention_settings is EMPTY on live → not enabled for any tenant (owner decision).
- [x] A3 strandSend(): text → NEW_CHAT draft + strandedSend + banner "ห้องสนทนานี้ถูกลบไปแล้ว ข้อความของคุณยังอยู่ในช่องพิมพ์" + button "ส่งในแชทใหม่" (ErrorBanner → landing page listener); handleConversationGone migrates the typed draft. vitest red 3/3 → green (gone.test 2 pins updated).
- [x] A4 MessageBubble: action-row label yields to either ErrorNotice (liveFailureNotice/reloadedFailureNotice). vitest red 2/3 → green; FIX124 pins updated to the one-reason rule.
- [x] B1 REAL BUG: memory_enabled=false was the only "temporary" marker (title_service.go planTitle + web nameConversationOnce). Added chat.conversations.is_temporary (migration 048, additive; test template rebuilt; LIVE NOT APPLIED — needs owner approval), POST /conversations {is_temporary:true} (memory off from creation), wire is_temporary, planTitle keys on IsTemporary; web create sends it, title skip keys on it. Go red (memory off is still named: got keep/temporary_chat) → green; web red 1 → green; 4 old pins updated.
- [ ] B2 auto-title live proof
- [ ] B3 auto skips dead model (test-only provider)
- [x] C2 host onUiReady: a run with a terminal is not replayed to a page that was not following it (no/other run cursor); page swapMessageId collapses an id collision. desktop vitest red 2/4 → green 4/4. C1 = symptom of C2 (replayed row has no files) — verify live.
- [x] C3 chose HOST IPC: op conversation.discardEmpty (page pagehide in HOSTED_MODE → host GET message_count==0 → DELETE). Why not a server sweep: immediate, no 24–48 h window with an orphan row, and the host re-checks the server count so the page can never delete a used chat; a server sweep would also delete legitimately empty new chats a user still has open. Tests 5/5 (new capability).
- [x] C4 retryWhenBackOnline(): HOSTED_MODE + cause=disconnected → row reconnecting, waits online/visibility/15 s poll (RESUME_WAIT), regenerate when back; ≤3 attempts/row; stands down if the row moved on. vitest 4/4 (new capability).
- [ ] D1-D4 polish
- [ ] E1 runner proposal, E2 quarantine list
- [ ] gate · deploy · live test · push · report

## Model switches
(none yet)

## Shared-state changes
(none yet)
