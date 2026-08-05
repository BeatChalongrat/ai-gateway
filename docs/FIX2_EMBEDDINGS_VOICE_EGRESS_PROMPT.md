# FIX 2 — Close the embeddings/voice DLP-bypass (Option B: intentional-raw-egress, controlled)

**The gap (found at runtime, not in the code audits):** chat-edge calls OpenAI **directly**, bypassing the gateway — therefore bypassing DLP entirely — on two paths:
- **Embeddings** (KB search): `services/chat-edge-api/internal/service/embedding_service.go` → `https://api.openai.com/v1/embeddings` directly. Its own comment admits "the Gateway doesn't currently surface an embeddings route." Called by `knowledge_service.go` (KB doc indexing + search).
- **Voice**: `services/chat-edge-api/internal/handler/voice.go` → OpenAI Whisper (STT) and TTS directly.

Today both are **inert** only because `OPENAI_API_KEY` is unset on chat-edge (smoke test: KB search 503, voice 503). But the moment an operator sets that key to "enable KB search / voice," **raw KB document content and raw voice audio egress to OpenAI unmasked** — a **third raw-egress channel outside the Phase 9 egress invariant** (which governs only `web_search` + `media_gen`, with credential-block + audit + per-tenant policy). This is a real masking-heart gap, not a config gap.

**The decision (already agreed — Option B):** embeddings and voice are like search/media — they need the *real* content to work (a masked embedding of `<PERSON_1>` returns garbage; masked audio is meaningless). So we do NOT force-mask them. Instead we bring them **into the same controlled intentional-raw-egress regime** as search/media: **block the credential/secret class, audit-flag, per-tenant policy, and machine-check them in the egress invariant** so they can never silently become an *un*controlled leak, and no NEW raw path can be added without the gate catching it.

Backend + chat-edge. Do NOT `git push`, do NOT commit secrets. R0 mandatory.

## R0 — Reconnaissance (answer all before coding)

1. **Embeddings egress:** read `embedding_service.go` (the direct OpenAI call, where the text comes from) + `knowledge_service.go` (what gets embedded: KB document chunks on index, and the user query on search). Confirm both the *document content* and the *search query* egress raw today.
2. **Voice egress:** read `voice.go` (Transcribe/Whisper + TTS) — what content egresses (raw audio for STT; the text-to-speak for TTS, which may contain PII).
3. **The Phase 9 egress regime (the model to mirror):** read `backend/app/services/dlp/egress_policy.py` (`RAW_EGRESS_ALLOWED`, `scan_search_query`, `audit_raw_egress`, `assert_egress_allowed`), `backend/app/api/routes/public_search.py` (how search calls the scan before egress), the tenant `search_pii_egress`/`media_pii_egress` policy columns (migration 064), and `backend/tests/test_dlp_egress_invariant.py` (the CI gate + `EGRESS_CLASSIFICATION` map). This is the exact pattern to extend.
3b. **Does the gateway have an embeddings route?** The embedding comment says no. Check `backend/app/api/routes/` for any `/v1/embeddings`. This decides the architecture (below).

## Architecture decision — where the credential-block scan runs

Two shapes; pick per R0.3b and justify:

- **Preferred if feasible — route through the gateway.** Add a gateway `/v1/embeddings` (and reuse the gateway's audio/voice proxy if one exists — `media_proxy.go` already proxies `/v1/audio/generations`) so chat-edge stops calling OpenAI directly. Then the credential-block scan + audit + per-tenant policy live in ONE place (the gateway, alongside search/media), and the egress invariant covers it automatically. This is the cleanest — it removes the bypass entirely rather than replicating controls in chat-edge.
- **If routing through the gateway is too large this pass — replicate the controlled-egress gate in chat-edge.** chat-edge runs a credential/secret scan on the embedding text + voice text BEFORE the direct OpenAI call (mirroring `scan_search_query`), audit-flags raw egress, and honors a per-tenant block policy. This keeps the direct call but makes it controlled. Weaker (controls now live in two languages) — only if gateway-routing is impractical now; document the residual.

**Recommend the gateway route** (removes the bypass, single control plane). State the choice.

## Implementation requirements (whichever shape)

1. **Credential/secret class ALWAYS blocked** on embeddings + voice text (same as search/media): an API key / password / PEM in a KB doc or a spoken command must never egress — block with a typed error, even though identity PII passes. (For STT audio you can't scan audio for secrets pre-transcription — note that; the credential block applies to text paths: embeddings, TTS input, and the *transcribed* text on its way onward.)
2. **Identity PII passes raw** (that's the point — embeddings/voice need real content), BUT:
3. **Per-tenant policy** — extend the Phase-10 `*_pii_egress` pattern: a `kb_pii_egress` / `voice_pii_egress` (or a shared `raw_egress` policy) column so a compliance-strict tenant can set `block` → fail-closed on identity PII too. Default `allow` (preserves the feature). Reuse the migration-064 pattern.
4. **Audit-flag** every raw egress on these channels (identity PII present but allowed) via the existing `audit_raw_egress` — category counts only, never values — so a tenant's compliance view can see "KB indexing / voice sent unmasked."
5. **Egress-invariant CI gate** — add the embeddings + voice channels to `RAW_EGRESS_ALLOWED` (or the equivalent) and to `EGRESS_CLASSIFICATION` in `test_dlp_egress_invariant.py`, classified as intentional-raw-whitelisted (credential-blocked). The gate must now assert these two channels are gated, and still fail if a NEW unclassified raw egress appears. If the controls live in chat-edge (Option 2), add a Go-side equivalent pin (like the Phase-10 `TestSandbox_DockerArgsAlwaysNoNetwork` structural test) so the chat-edge bypass can't regress.
6. **Docs:** update `docs/DLP_ARCHITECTURE.md` "Intentional raw egress" section — it currently lists search + media; add embeddings (KB) + voice, with the same disclosure (credential-blocked, per-tenant, audited) so enterprise/PDPA customers know KB indexing and voice send identity content unmasked by design.

## Tests
- Embeddings text with an API-key/secret → blocked (typed error), OpenAI never called; with a name/email → passes (raw, by design) and an audit row is written (categories only); `block`-policy tenant → identity PII also blocked.
- Voice TTS text with a secret → blocked; transcribed-text path credential-blocked; `block` tenant fails closed.
- Egress invariant: the gate now lists embeddings + voice as gated intentional-raw; a hypothetical NEW unclassified direct-OpenAI call → gate fails (drift guard). If gateway-routed: a seed-PII embedding request shows the credential blocked + identity audited.
- No regression: search/media egress behavior unchanged; DLP chat path unchanged.

## Final verification
1. Per stack: what you ran, green (backend pytest incl. egress invariant; chat-edge go test if controls added there). Note what needed a live OpenAI key and how you covered it (mock the OpenAI HTTP call; assert the scan runs BEFORE the outbound request via a spy — zero-request-on-block).
2. **Masking statement:** confirm there is now NO uncontrolled raw egress — embeddings + voice are either gateway-masked-path or credential-blocked+audited+policy-gated, and the CI invariant covers all raw channels (chat=masked, search/media/embeddings/voice=controlled-raw). A KB doc or spoken command containing a secret can no longer leak it to OpenAI.
3. **Security:** confirm identity-PII passthrough is intentional + per-tenant-blockable + audited (not an accidental leak), and the credential class is always withheld.
4. Summarize changes file by file (gateway route OR chat-edge scan, tenant policy migration, egress-invariant update, docs). Note the deploy requirement (the per-tenant policy default) and whether STT audio secret-scanning is a documented limitation (you can't scan audio pre-transcription).
