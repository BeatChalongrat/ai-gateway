# Changelog

2026-11-13 — Artifact workspace UX: file tabs and automatic output opening, selection-scoped rewrites, draft autosave/recovery and Undo/Redo, before/after AI proposals with partial acceptance, cancel/retry checkpoints, cross-chat latest-file search, snapping/resize/fit/thumbnails, and 12 real-preview template starters. [Contracts and validation scope](docs/ARTIFACT_WORKSPACE.md).

## [Unreleased] — 2026-11-13

- Shared Web/Electron visual paragraph/text-box editor, slide/PDF geometry controls and actual saved-page previews; image pencil entry point, rotation/brightness/contrast and PNG revisions.
- Native PDF text-object replacement: original text removed from page content, searchable replacement, preserved other page objects; scans/forms/rotated pages explicitly limited.
- Optional vision-model review and up to two correction attempts for Word/PPT/images, with typed edits, text/page safeguards, score/pass acceptance and immutable history.
- Raw review-page egress requires tenant `media_pii_egress=allow`; no model-authored editing code executes. Both agents expose visual modes through `edit_office_file`.
- Local real-render/file/database tests use mocked providers only; see [configuration, tests and known limits](docs/VISUAL_EDITOR.md).

- Shared Web/Electron structured Office/text editor; immutable revision history/restore with PostgreSQL conflict detection and source fingerprints.
- Both agents can inspect/edit/restore conversation files with `edit_office_file`; revisions are re-ingested and attached through the existing file pipeline.
- Bounded PPTX visual feedback: render, detect edge ink, repair, re-render; accept only measured improvement with content/page safeguards.
- Desktop `generate_video` and both clients' `video_job` status/cancel tools; shared recoverable Video jobs panel for explicit price approval, playback/download and saving copies.
- Video job list/status/output/cancel enforce tenant plus user ownership. See [implementation and validation](docs/OFFICE_EDITOR.md).

- Web/Desktop reference-image editing with conversation-scoped inputs and reusable result IDs; binary HTTP transport fixed.
- Three editable motion templates export MP4/poster/contact sheet/storyboard in the isolated sandbox; MP4 MIME and oversized-artifact rejection.
- Video readiness uses DB provider keys and installed adapters; unsupported resolution/audio controls are refused, duration is not silently shortened.
- Premium video waits for owner confirmation on the server before dispatch; shared UI confirms the job, and the chat tool reports pending approval instead of polling forever.
- Migration 090 and defaults account for GPT Image 1/Sora retirement; current image token usage feeds explicitly estimated costs.
- Detailed comparison, activation and remaining gaps: `docs/OFFICE_MEDIA_PARITY.md`.

### Office work from November 12

- Office follow-up: 12 shared PPTX/DOCX/PDF/XLSX/CSV starters, typed data,
  formula-safe text export, bounded slide content and invoice totals.
- Optional internal Office converter for Web Chat/Desktop Python and plan cards:
  XLSX recalculation/error checks, full PDF page rendering, downloadable Python
  previews and explicit partial/checked UI evidence. See `docs/OFFICE_TEMPLATES.md`.
- Added isolated Office Compose overlay and real converter acceptance tests;
  review images are separate from production image tags.
- Desktop: explicit local-file import and conversation-file save tools, with
  folder grants, upload disclosure, bounded transfers and exclusive creation.
- Desktop Python: forwards input file IDs, returns reusable output IDs and
  reports process/timeout failures correctly.
- Chat Edge: conversation-scoped inputs on named `/code/run` requests; generated
  file references available to subsequent Web Chat tool calls.
- PowerPoint: ordered text replacement, notes, slide movement/deletion and
  append support; edit plans and result logs rendered in the shared UI.
- Word: preserve source template defaults and replace text across runs/stories.
- Office generators: action exceptions no longer return a successful result.
- Real-file regressions and expanded architecture, security, API and Office
  documentation. See `docs/OFFICE_WORKFLOWS.md` for evidence and limitations.

This section describes source changes; it does not assert a deployed release.

## 2026-05-19 — historical DLP token migration

### DLP Token Shape Migration (`<rN>` neutral tokens)

**Breaking change**: DLP masking tokens changed from `[CATEGORY_N]`
(e.g. `[EMAIL_0]`, `[THAI_ID_1]`) to neutral `<rN>` format
(e.g. `<r0>`, `<r1>`).

**Why**: The old format exposed category names to the LLM, causing:
1. AI safety filters refusing to process messages containing tokens
   like `[BEARER_TOKEN_0]`
2. LLM hallucinating/renaming tokens (e.g. `[AUTH_TOKEN_0]` instead
   of `[BEARER_TOKEN_0]`), breaking unmask

**What changed**:
- Token format: `[CATEGORY_N]` → `<rN>` (sequential counter, resets per request)
- DLP system prompt: v3 — no longer contains literal token examples
- Orphan token sanitizer: strips hallucinated `<rN>` from LLM output
- New detectors: `date_of_birth`, `thai_tax_id` (strong-prefix mode)
- Detector fixes: phone rejects all-same-digit, NER excludes Thai
  imperative verbs

**E2E test results (50 cases)**:
- Mask accuracy: 97% (38/39)
- Full round-trip: 90% (35/39)
- AI refusal: 0% (0/50)
- False positives: 3 (medical terms + salary — documented as correct
  behavior per PDPA)
- False negatives: 1 (short API key `sk-test-abc123` — precision >
  recall decision)

**Known limitations**:
- T19/T20: Medical condition names (เบาหวาน, ภูมิแพ้) masked — correct
  per PDPA Section 26 (health data is sensitive PII)
- T27: Labeled salary amounts masked — income is PII under most
  frameworks
- T41: `sk-test-*` keys under 20 chars not detected — cannot
  distinguish from product codes without context
- Orphan `<rN>` may appear in ~5% of responses due to LLM variance
  (outbound scan mints new tokens that can collide with hallucinated
  ones)

**Migration commits**: ffc6704 (token shape) → subsequent fixes
(prompt leak, false positives, false negatives, orphan sanitizer)
