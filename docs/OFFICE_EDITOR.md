# File editor, revisions, visual repair and video agents

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

Source status: **2026-11-13**. This is an implementation and test record, not a
production deployment or a claim of equivalent Claude/ChatGPT output quality.
The shared React UI runs in Web Chat and the Electron desktop shell. The older
WPF application does not receive a new native editor in this change.

The later [visual editor and AI review guide](VISUAL_EDITOR.md) owns direct
paragraph/text-box editing, native PDF replacement, image controls and the new
vision-model repair loop. This document retains shared version/storage/video
contracts and the preceding deterministic Office quality path.

## Using the editor

Open an attachment preview, then choose **Edit / versions**. Inspect the
identified fields, change their contents, and choose **Save new version**. The
preview/download toolbar switches to the newly saved file. History lists the
original and its descendants, timestamps, change records and downloads.

Open an old version to inspect it. To restore it, return to the current head and
choose **Restore as new version** on the desired history row. Restore copies
the chosen bytes into a new stored file; it never overwrites an older version.
A new conversation-file row is produced, so closing/reopening the app does not
erase history. Reopen the file through history or the conversation's generated
files. An old message attachment continues to identify its original revision.

| Format | Editable fields | Preservation and limits |
|---|---|---|
| DOCX | Visual body/table paragraphs and typography; structured API also exposes headers/footers | Other paragraphs remain. Whole-paragraph text replacement can flatten mixed runs; visual mode rejects text replacement when drawings/fields would be lost. Flow preview is not exact Word pagination. At most 1,000 editable paragraphs. |
| PPTX | Visual horizontal top-level text frames, geometry and typography; structured API also exposes group/table paragraphs | Real rendered background preserves noneditable objects; source deck stays native. No drawing/chart/SmartArt editor. Complex OOXML is not universally lossless. |
| XLSX | Typed text, numeric values and explicitly selected formulas | Sheet selector, 50-row windows, first 20 columns. Existing formulas/styles on untouched cells remain subject to openpyxl/LibreOffice limits. This is not a full spreadsheet grid or coauthoring engine. |
| CSV | Existing cells in 50-row/20-column windows | UTF-8/BOM or CP874 input, UTF-8 BOM output. Edited formula-leading text is apostrophe-escaped. CSV has no formula/style model. |
| PDF | Visual native text-object replacement and geometry | Removes selected original text objects and writes searchable replacements. No scanned text/form/rotation editor or certified redaction. Legacy structured modes retain footer notes. At most 100 pages. |
| PNG/JPEG/WebP | Visual rotation/brightness/contrast, PNG revisions and optional AI reference editing | Alpha retained by manual controls; no masks/layers. Static images up to 16 MP. |
| TXT, MD, JSON | Complete UTF-8 content | 1 MiB text limit; JSON must parse before saving. |

Editor input is bounded to 16 MiB. Office archives are checked for duplicate
parts, macros/external workbook links and expanded-size limits (64 MiB, 10,000
parts). Unsupported formats remain downloadable/read-only. Images gain direct controls, versions and bounded AI review; they do not gain a painted-mask UI.

For XLSX, choosing **Text** makes `=1+2` literal text. Choosing **Formula** is
required to write a formula. Explicit external-workbook references and selected
external-function patterns are refused; the sandbox has no network access.
This guard is not a comprehensive Excel formula-language security validator.
Office output still passes the existing structural/formula quality checks.

Unsaved edits disable sheet/row-window changes and switching back to preview.
File-editor drafts now have local recovery, server autosave with revision
conflict checks, and in-session Undo/Redo. Closing/reloading can restore the
draft; failed local and server persistence is shown explicitly. There is no
collaborative cursor model. See [draft lifecycle](ARTIFACT_WORKSPACE.md).

## API and version consistency

`POST /api/v1/files/{fileID}/editor` is authenticated and tenant/user scoped.

```json
{"mode":"inspect","sheet":"Sheet1","offset":0}
```

Inspection returns `inspection.sha256`, `format`, `fields` (`id`, `value`,
`type`), `sheets`, `offset` and `pdf_notes_only`. Treat field IDs as opaque.

```json
{
  "mode":"edit",
  "sha256":"<hash returned by inspect>",
  "sheet":"Sheet1",
  "offset":0,
  "changes":[{"id":"A1","value":"42","type":"number"}]
}
```

`history` returns `versions`; `restore` takes `target_id`. Edit/restore return a
normal attachment under `file`; Office edits also return `quality`. Optional
`conversation_id` constrains the source to that conversation. Agent adapters
always populate it from their trusted execution context, overriding model input.

Status codes: 400 invalid envelope/ID, 404 missing or non-owned file, 409 newer
child revision already exists, 415 unsupported format, 422 invalid changes or
source fingerprint, 503 sandbox unavailable. Other infrastructure/quality
failures return 500 without publishing an edited file. The request body is
bounded to 2 MiB; Python execution is bounded to 90 seconds; the shared review/repair loop has a
120-second overall budget for the deterministic quality loop. Visual requests use a separate ten-minute overall budget; see [visual API limits](VISUAL_EDITOR.md#api-and-agent-contract).

The original bytes are immutable. New edits use the existing migration-012
`parent_file_id`, `version`, `change_log` columns. `CreateRevision` locks the
expected parent, then checks for children in a fresh PostgreSQL statement.
Two concurrent editor saves against one head yield one success and one conflict.
The losing upload is removed. Restore must target the same file lineage; a
different file from the same conversation is insufficient.

This compare-and-save rule applies to the new editor/restore paths. Older plan
executors can still produce branches; history displays those descendants too.
There is no new migration, cross-format semantic diff, binary merge or undo of
arbitrary local filesystem changes. Ordinary conversation/file deletion and
retention rules still apply. New revisions are re-ingested for subsequent chat
context; `processing` is reported until extraction completes.

Both agents expose `edit_office_file` with `inspect`, `edit`, `history`,
`restore`, `visual_inspect`, `visual_edit` and `ai_repair`. Web tool results link successful revisions to the assistant turn;
Desktop returns `attachedFiles` through its existing import pipeline. Desktop
local files first use `import_local_file`; use `save_conversation_file` to write
a selected revision into a granted folder. Edits do not silently overwrite the
original local file. The Desktop host tool is consent-classified as `Ask`.

## Render, inspect, repair, render again

The shared `OfficeQuality` gate now uses the configured sandbox for bounded
PPTX repairs after LibreOffice renders the file. This path is used by editor
saves, Office plan outputs and `run_python` artifacts on both clients.

1. Convert to PDF and render/check every page.
2. Detect dark pixels in the converter's outer 0.3-inch strips. A page with
   edge ink over 0.001 contributes to the repair score.
3. For PPTX only, attempt up to two repairs. Move/resize top-level text boxes
   inside a 0.35-inch margin, enable wrapping/autofit; the second attempt can
   shrink explicitly sized runs by 15%, with a 12-point floor.
4. The script verifies that all extracted slide/shape/table paragraph text is
   unchanged. Pictures and text are not deleted.
5. Re-render. Accept only a lower measured edge score, unchanged page count,
   no increase in blank pages and no decrease in extracted word count. Otherwise
   retain the previous candidate/original.

Reports expose `repair_attempts`, `repaired`, `edge_ink`, rendering/calculation
results and warnings. The UI distinguishes accepted repairs from retained
originals. Editor history records the quality report in the change log; plan
reports and Python tool output retain their existing quality-report channel.

This particular quality-gate path is a deterministic pixel-feedback loop.
The separate [AI review action](VISUAL_EDITOR.md#ai-review-and-bounded-automatic-correction) now adds vision-model Word/PPT/image review and correction. The deterministic path does not detect all overlaps, poor typography, image defects,
incorrect charts, missing semantic facts or video continuity. Grouped text is
editable but not geometrically repaired. Full-bleed backgrounds can trigger an
edge warning; a repair without measured improvement is discarded. Page counts
and word counts are useful checks, not proof of equivalent layout/content.
DOCX/PDF are rendered and reported but have no automatic layout repair in this deterministic path.
XLSX retains its existing recalculation/structural checks.

Without `OFFICE_CONVERTER_URL`, Office review reports `partial`, and visual
repair does not run. Rebuild the sandbox image to include `file_editor.py` and `visual_editor.py`;
deploy the optional converter as described in [Office templates](OFFICE_TEMPLATES.md).

## Video generation through agents

The Desktop host now offers `generate_video(prompt, duration_sec?)` and
`video_job(job_id, action=status|cancel)`. It reads `/video/config`, including
the `agent_model` supplied from chat-edge's configured `VIDEO_GEN_MODEL`, and
refuses disabled capability, missing model or out-of-range duration. It submits
through the authenticated chat-edge proxy with the current conversation ID.
Credentials stay in the host. Cancellation aborts an HTTP request; use the
explicit cancel action/panel to cancel an already-created provider job.

The Web agent retains its existing submit/poll/store path and gains the same
`video_job` status/cancel action, scoped to the current conversation. Both clients now
show **Video jobs** above the composer, populated by
`GET /api/v1/jobs?conversation_id=<UUID>` → backend `/v1/jobs`. It returns the
latest 20 video jobs owned by that tenant **and user** in that conversation,
including estimated price and status. The panel polls every 10 seconds while
mounted. It can rediscover a job after a reload even if the agent turn ended.

`pending_confirm` jobs remain undispatched. Only the user's **Confirm cost and
generate** click calls the existing confirmation endpoint. Neither new host
tool offers confirmation. The panel can cancel active jobs, load finished video
bytes on demand, play/download them, and explicitly save a copy to conversation
files. The copy action is not globally idempotent: using it again after a reload
can create another copy. Stored backend job outputs follow their existing
retention rules, so saving a conversation copy remains useful.

Polling, output download and cancellation now enforce end-user ownership in
addition to tenant ownership; knowing another tenant member's job UUID is not
enough. Direct callers need their end-user identity. Confirmation already had
that ownership boundary. No provider-facing API keys or arbitrary output URLs
are passed to the UI.

This adds host-agent submission/status/cancel and shared recovery/confirmation
UI. It does not add image-to-video, audio generation inside a clip, timeline
editing, provider-independent model parameters, automatic paid retries or live
provider entitlement. The existing motion templates remain the local-code path
for silent explainers/charts/slideshows; generative footage needs a configured,
enabled and owner-verified supported provider/model.

## Verification and rollout

The following counts describe the preceding editor/video pass. Current visual-editor verification is recorded in [the follow-up](VISUAL_EDITOR.md#verification-record).

Validation performed in an isolated review environment:

- Desktop: 916 tests in 94 files; TypeScript check and build.
- Sandbox: 36 Python tests, including nine new editor tests, run as nonroot
  with network disabled, read-only root, seccomp and resource limits.
- PostgreSQL: all 50 chat migrations applied to a temporary test template.
  Concurrent-save/ownership test and real sandbox inspect/edit/restore/re-ingest
  lifecycle passed against disposable databases.
- Real LibreOffice/PPTX test: edge score **0.00762 → 0.00000**, one repair,
  one page before and after. Before/after PNGs visually inspected: the clipped
  leading title moved inside the slide and the full text remained visible.
- Unit cases reject worse edge scores, missing rendered words, changed page
  counts and new blank pages; no rejected candidate replaces the original.
- Web: production build and 30 targeted tests across six files; TypeScript, i18n parity (1,819 app
  keys plus 1,300 Agent Market keys) and theme checks passed. Tests cover save
  fingerprints, retained conflict drafts, restoration, job recovery, explicit
  approval/cancellation and existing preview/media behavior.
- Backend: 104 focused tests passed across public video/audio, media readiness,
  video config, tool argument policy and registry parity. This also caught and
  fixed missing classifications for the earlier local import/save tools; the
  parity scanner now includes the new split Go/TypeScript tool modules.
- Go: full handler and router packages passed with the isolated PostgreSQL
  template; focused service/repository behavior and vet checks passed.
- Documentation: 81 Markdown files, zero missing local Markdown links; the
  tracked-pointer/ignore gate passed against an isolated index. Real git index
  was not staged.

Representative commands (from the respective package directories):

```text
apps/desktop: npm run typecheck; npm test; npm run build
apps/web-chat: npm run build; npx tsc --noEmit; npm run lint:i18n; npm run lint:theme
backend: python -m pytest tests/unit/test_public_videos.py tests/unit/test_public_audio.py tests/test_media_readiness.py tests/test_video_config_capability.py tests/test_dlp_tool_arg_policy.py tests/test_dlp_tool_policy_parity.py -q -o addopts=''
chat-edge: go test ./internal/router ./internal/handler -count=1
chat-edge: go test ./internal/service -run 'TestFileRevisionConcurrent|TestFileEditorReal|TestOfficeRepairReal' -count=1 -v
```

Database integration requires `CHAT_EDGE_TEST_DSN` pointing to a **test** server
with `chat_test_template` prepared from all migrations. The real sandbox/visual
cases additionally require `EDITOR_REVIEW_SANDBOX` and
`EDITOR_REVIEW_CONVERTER`; `EDITOR_REVIEW_OUTPUT` optionally retains before/after
fixtures. Tests otherwise explicitly skip these opt-in Docker/renderer cases.
Do not point the test harness at a live application database.

Review images use separate Docker tags (`ai-gateway-sandbox:editor-review` and
`ai-gateway-doc-converter:office-review`). Production images, migrations and
services were not deployed. Native Electron interaction, Microsoft Office
rendering, a paid provider video and a real autonomous model benchmark remain
unverified. The changes require rollout of backend, chat-edge, Web Chat,
Desktop host and the rebuilt sandbox; converter-enabled visual repair also
requires its service. Existing Office/media constraints still apply.
