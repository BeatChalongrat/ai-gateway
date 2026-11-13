# Artifact workspace: editing, recovery and review

Implemented and reviewed on **2026-11-13** in `apps/web-chat` and the shared
Electron renderer in `apps/desktop`. This is source implementation and local
acceptance evidence, not a production rollout or a Claude quality benchmark.
All model/provider responses used in this verification are mocks.

## The eight UX changes

| Area | Delivered behavior | Boundary |
|---|---|---|
| 1. Workspace beside chat | Files open directly in the editor; recent files have tabs in the shared side pane. New assistant files open automatically when no pane is active. Revisions replace their parent tab. Opening an old attachment resolves the latest head; history still opens a specific old version. | An active pane is not replaced by a new result, so edits keep focus. The result is added as a tab. Existing HTML/code Canvas retains its own editor and version model in the same pane slot. |
| 2. Edit a selection | Select plain text in DOCX/PPTX/native PDF, or select the entire active text frame, enter an instruction and request a rewrite proposal. | Text offsets are UTF-16, checked against the original including surrogate boundaries. Save manual changes first. PDF text must fit the supported box; a failed rewrite never publishes a clipped replacement. |
| 3. Draft recovery | Visual and structured file editors save drafts locally immediately and debounce server saves by 700 ms. Undo/Redo retains up to 80 in-session states; nearby edits to the same field are grouped. Reopening/reloading recovers the draft. | Drafts are not published file versions. Conflicting devices require recovery/export and reloading the server draft. Undo stacks are in memory. This does not replace the separate code Canvas persistence contract or implement collaborative cursors. |
| 4. Review AI changes | Actual original/candidate renders, text/style/geometry differences, per-change checkboxes, accept selected/all, reject. Images are one indivisible proposal. | AI scores are advisory. Partial acceptance is rendered/checked again, but the combined partial result is not scored again by AI. There is no arbitrary comparison of any two historical versions yet. |
| 5. Progress and recovery | Actual inspect/render/review/apply/verify stages, cancel, and retry the same job using persisted checkpoints. Closing the pane leaves the server job running; reopening the file retrieves it. | This is a bounded background worker, not a general distributed job queue. Interrupted workers become retryable after the stale-work window; no automatic paid retry on startup. |
| 6. File library | Search filenames across the current user's live conversations, filter extensions, show latest versions, open/download, or continue in the original conversation. | The library covers conversation files, not a merged index of code Canvas blocks or unsent local folders. At most 40 rows per page; the first six cards load image/document thumbnails to bound conversion work. |
| 7. Editor manipulation | PPT/PDF move and resize handle, page/peer alignment snapping and guides, numeric geometry, fit-width zoom, nearby real page/slide thumbnails, contextual text controls. | Word remains a flowing paragraph/table editor, not a Word pagination engine. PPT groups/charts/SmartArt and PDF scans/forms/rotated objects keep the limitations in VISUAL_EDITOR.md. Save/discard before changing a PDF/slide page. |
| 8. Template gallery | Twelve DOCX/PPTX/PDF/XLSX/CSV starters, real sample-file previews, essential content fields, spreadsheet-table paste, create and immediately open. | No invented facts or model call. Gallery starters do not reproduce an arbitrary brand template. Generic analysis/export columns stay text to preserve IDs; budget/invoice fields have explicit numeric types. |

The shared interface runs in Web Chat and Electron. Desktop local import/export
and folder permissions continue through the existing host tools; the browser
does not gain unrestricted filesystem access. The legacy WPF app is unchanged.

## User flow

Open **Artifacts** in a conversation header for the cross-chat library and
**Templates**. A template collects a title and supplied sections, or a table.
Empty sections are omitted. Invoice fields require a customer, invoice number
and line items; currency in this gallery is THB. Tracker dates use ISO
`YYYY-MM-DD`. Table paste uses tabs, with a maximum of 1,000 rows and 12 columns.

Opening an editable attachment in the docked chat workspace goes directly to
its editor. Standalone preview/share modals start with the saved-file preview;
they do not load editor history until editing is opened. **Preview** switches
the workspace to the saved file view. Typing changes a draft; **Save new version** publishes an
immutable revision. **Discard changes** deletes the draft using its expected
revision. History provides open/download/restore-as-a-new-version.

For AI, save manual edits first. **AI inspect and repair** creates a proposal
for Word/PPT/images. Selecting text and **Propose rewrite** creates a textual
proposal for Word/PPT/supported native PDF. Review the rendered pages and
selected changes before accepting. Reject/cancel does not publish a file.

## HTTP contract

All paths below have the `/api/v1` prefix and use the existing authenticated
Chat Edge transport in both clients. File and task access checks tenant, user,
and a live conversation. File IDs do not grant access.

| Method/path | Request / response |
|---|---|
| GET `/files/{fileID}/draft` | `{draft: null}` or owner-scoped draft |
| PUT `/files/{fileID}/draft` | `{sha256, revision, body}` → `{draft}`; revision 0 creates, subsequent saves must match the current revision |
| DELETE `/files/{fileID}/draft` | `{revision}`; stale deletion returns conflict |
| POST `/files/{fileID}/work` | `{action: "repair" \| "rewrite", sha256, page, instruction?, selection?}` → `{work}` |
| GET `/files/{fileID}/work` | Latest unexpired owned job, or null |
| GET `/file-work/{workID}` | Status/stage/error; ready proposals also return before/after page images and change list |
| POST `/file-work/{workID}` | `{action: "accept" \| "reject" \| "cancel" \| "retry", ids?: string[]}`; accept returns a saved file and quality information |
| GET `/files/library?q=&kind=&offset=` | `{items: [{file, conversation_id}], has_more}`; latest heads only, descending creation order |
| POST `/conversations/{convID}/templates` | `{template, data}` → `{file, quality}`; fixed allowlisted sandbox script |

A rewrite selection is `{id, start, end, text}`. `id` is an opaque inspected
field ID; offsets are JavaScript UTF-16 code units, not bytes or Unicode rune
indices. The selected substring must exactly match the saved source. Prefix
and suffix are retained by the service rather than asking the model to return
the whole document. PDF proposal IDs refer to the original object even when
native replacement renumbers PDF objects.

Draft bodies contain visual field changes, image controls, page/selection and,
for the structured editor, typed field changes plus sheet/offset. They are
bounded to 2 MiB. A draft's source SHA is a recovery guard; publishing still
uses the normal source/version checks. A stale draft does not overwrite a newer
server draft silently.

Ready work includes `before_pages`, `after_pages`, `changes` and optional
`review`. Every change has `id`, `before`, `after`. Acceptance requires a
nonempty unique subset of the proposed IDs. Unknown/duplicate IDs fail. An
accepted job is idempotent: repeating accept returns the same resulting file.
There can be only one running/ready/accepting proposal per source file.

Ownership failures are 404, draft/revision/active-proposal conflicts 409,
invalid editor requests 422, malformed JSON 400. A newer child revision makes
the original ineligible for a new proposal or acceptance. Reopen the latest
revision instead of retrying a stale source.

## Job and publication model

`running → ready → accepting → accepted` is the normal path. Terminal
alternatives are `unchanged`, `rejected`, `cancelled`, and `failed`. Only failed
work can retry. It reuses inspected metadata, rendered originals, model results
and candidate bytes already checkpointed; a failed/in-flight external call
itself may need repeating. This is not an exactly-once guarantee for provider
billing or a database outage immediately after a provider response.

Stages are `inspect`, `render_before`, `review` (or `suggest` for a rewrite),
`apply`, `render_after`, `verify`, and `ready`/`complete`. The worker has a
10-minute context. Visual repair retains the maximum of two correction
attempts. Office rewrites and repairs render the entire document, bounded to
eight complete pages, so a selection near the end of a flowing document is
not reviewed against only page one. Native PDF rewrites render the selected page. Checkpoint payloads are capped
at 32 MiB; candidate bytes at 16 MiB. Larger reviews fail without publication.

Cancellation changes persisted state before cancelling the local worker. Every
worker update is conditional on still being running, so a late model response
cannot resurrect a cancelled job. Across replicas, an already-running external
call can continue until its next checkpoint; its result still cannot publish.

After 11 minutes without an update, a running job is marked interrupted and
can be retried explicitly. A stale accepting job reconciles a revision whose
change log contains `user_accept:<job UUID>`; if none exists, it returns to
review. Acceptance rechecks source ownership/hash/head and uses the normal
locked immutable revision creation. Partial acceptance rebuilds only the
selected changes against the original, then uses the standard quality gate.

The model cannot accept its own work through `edit_office_file`. Web and Desktop
agent calls using `ai_repair` are mapped to the asynchronous `ai_propose` editor
mode. The existing direct HTTP `ai_repair` mode remains a legacy synchronous
contract; it can publish a verified result and is not used by the new UI or
agent tool. Do not confuse the two contracts.

## Storage, privacy and deployment

Apply `services/chat-edge-api/migrations/051_artifact_workspace.sql` before
deploying the new API/renderer. It creates `chat.file_drafts`, `chat.file_work`
and supporting indexes. Rebuild the sandbox image so `workspace_template.py`
is present, then rebuild Chat Edge, Web Chat and Desktop.

Pending candidates and checkpoints live in bounded PostgreSQL fields, not in
conversation attachments or model history. Acceptance stores an ordinary file
revision; acceptance/reject/cancel clears candidate/checkpoint payloads.
Jobs expire after one day and are no longer readable/acceptable; physical
expired-row cleanup is opportunistic when that owner starts another job, not
a promise of deletion exactly at 24 hours. File deletion cascades drafts/work.
Soft-deleted conversations are excluded from access/library queries.

Server drafts persist until discard/save cleanup or file deletion. Browser
recovery copies use keys scoped by tenant, user and file. These are ordinary
localStorage data, not encrypted storage; shared-profile users or code with
same-origin access can inspect them. Account switching does not restore another
account's draft, but logout is not a secure erase of local cached drafts.
Quota/storage failures and failed server writes show an unsaved state with
retry/export; the UI must not report success when neither copy was saved.

Manual editing, recovery, library search and templates do not call a model.
Rewrite text uses the existing text gateway/DLP path. Rendered-page review
still requires the configured `OFFICE_VISUAL_MODEL` and tenant-approved raw
image egress; image correction also requires `IMAGE_GEN_MODEL`. Office
conversion uses `OFFICE_CONVERTER_URL`. See VISUAL_EDITOR.md for these limits.

## Verification and reproducibility

The implementation is exercised with mocked models/provider transports, real
local PostgreSQL, isolated Python sandbox, native PDF editing and LibreOffice
conversion. The template gallery's 12 PNGs were generated from actual sample
files; CSV previews render the produced table. Regenerate them from the repo
root with `python services/chat-edge-api/scripts/generate_workspace_previews.py`
using the documented local review image/converter. They contain synthetic data.

Focused coverage includes draft compare-and-swap, stale delete rejection,
cross-owner isolation, local restore, Undo/Redo, UTF-16 selection checks,
native PDF/Word/PPT selected-text replacement and typography preservation, unpublished proposals, partial and
idempotent acceptance, retry checkpoint reuse, cancel versus late updates,
latest-head library filtering, table validation and geometry snapping.
Playwright intercepts all API traffic for the full edit/recover/review/library/
template flow; no provider credentials are used.

Remaining acceptance boundaries: native Electron window interaction, Microsoft
Office render fidelity, concurrent distributed worker recovery under load,
real provider quality/latency/cost, collaboration, whole-document OCR rewrite,
arbitrary historical semantic diff, advanced spreadsheet grid operations and
equivalence to Claude on a shared evaluation corpus.

## Acceptance record (2026-11-13)

- Desktop: `npm test`, `npm run typecheck`, `npm run build` — **917 tests in
  94 files**, typecheck and build passed. The host-tool test verifies that agent
  `ai_repair` is sent as `ai_propose` and preserves conversation binding.
- Web focused unit coverage: **25 tests in 8 files**, including the artifact
  draft/auto-open/review/editor/template suites and existing attachment/Canvas
  adoption regressions. TypeScript and production Next build passed.
- Pre-merge Web regression run: **2,140 tests in 312 files** passed. Standalone
  preview/share modals retain preview-first behavior, while docked files open
  in the editor. TypeScript, translation parity and generated-theme-class checks
  passed; the final SidePane assertion cleanup also passed its 14-test suite.
- Pre-merge backend regression run: **70 tests** passed across media readiness,
  migration defaults, visual review, video capability/routes and DLP egress.
  These checks use mocked provider responses and do not establish live quality.
- Playwright: **2 browser scenarios** passed with intercepted API responses;
  the workspace scenario additionally passed after tightening file/job identity.
- Translation parity: **1,948 app keys**, plus 1,300 Agent Market keys, EN/TH;
  no new hardcoded-string violations and no dead theme utilities.
- Go: workspace storage/review/rewrite integration, the existing real visual
  editor/mocked-AI suite, full handler/router packages and vet checks passed
  against an isolated template database migrated through 051. Draft conflicts,
  no publication before accept, partial accept, retry, cancellation, ownership,
  source text removal and latest-file filtering are asserted.
- Sandbox: **45 Python tests** passed with network disabled, a read-only root
  and dropped capabilities. All 12 template preview assets were generated and
  their contact sheet inspected.
- Documentation: **83 maintained Markdown files** inventoried; local Markdown
  link validation found no missing targets.

No production database was migrated and no service was deployed. Review-only
PostgreSQL/converter containers are removed after verification. Test screenshots
remain under the ignored `apps/web-chat/test-results` directory. Live provider
calls and native Electron window interaction were not performed.
