# Visual editor, native PDF editing and AI image review

Current UX/publication contract: [Artifact workspace](ARTIFACT_WORKSPACE.md). The new UI and agent tool create reviewable proposals; the legacy direct `ai_repair` endpoint described below retains its synchronous semantics.

Implementation status: **2026-11-13**. Applies to the shared Web Chat renderer
and the Electron desktop application in `apps/desktop`. It does not add a
native editor to the legacy WPF application. No production rollout or live
provider test was performed for this change. Provider responses in acceptance
tests are mocked; document conversion, PDF editing, sandbox execution and
version storage use real local services/files.

## User workflow

1. Open an attached Word, PowerPoint or PDF file in the docked chat workspace
   to enter the editor directly. Standalone preview/share modals start in
   preview mode and load editor history only when editing is opened.
   For a PNG/JPEG/WebP attachment, the pencil action opens the same workspace;
   clicking the thumbnail still opens the image lightbox.
2. Click a paragraph/text frame to edit plain text directly. The toolbar changes
   font size, bold, color and, for Office, italic/alignment. Formatting applies
   to the whole paragraph or frame, not a selected character range.
3. On PPT/PDF, drag **Move** or the resize handle, use page/peer snap guides,
   or enter geometry in page points. Fit-width and real nearby page/slide
   thumbnails aid navigation. Save/discard before changing pages.
4. On images, rotate by 90 degrees and adjust brightness/contrast. Saving creates
   a PNG revision and retains alpha. The API also accepts a bounded crop box;
   there is no drag-to-crop/mask/paint UI in this implementation.
5. **Save new version** creates an immutable revision. **Render saved file**
   displays the real saved document render with page navigation. It does not
   render an unsaved draft; save first. The rendered result is authoritative
   when the browser approximation differs from Office/PDF layout.
6. **AI inspect and repair** checks saved Word/PPT/image content and creates a
   proposal with rendered before/after pages. Select changes and accept to
   publish a revision, or reject. Selected-text rewrites also support native PDF.
   AI is explicit, disabled while the draft is dirty, and can consume credits.
   Opening, typing, manual saving and history do not invoke AI.
7. History provides versions, timestamps, change records, open/download and
   restore-as-new-version. Open the latest revision before restoring an older
   one. An older message attachment still refers to its original version.

The browser canvas uses plain `contentEditable` text, not file/model-supplied
HTML. Paste inserts plain text; drag/drop content insertion is disabled.
Bundled Sarabun regular/bold and its OFL notice are served locally. Unsaved
edits are saved as separate drafts with local recovery and debounced server
compare-and-swap. Undo/Redo is available within the open draft. Conflict and
unsaved states provide recovery/export; collaborative editing is not implemented.
Page switches still require saving/discarding edits. See ARTIFACT_WORKSPACE.md
for cache privacy, quotas and exact recovery guarantees.

## Exact editing scope

| Format | Implemented | Fidelity boundaries |
|---|---|---|
| DOCX | Flowing document canvas, body paragraphs, tables in source order, embedded raster previews, paragraph text and typography | Browser flow is not Word pagination. Section-specific layouts, lists, mixed runs, floating shapes, headers/footers and fields are not fully represented. Inspect/AI metadata includes the legacy paragraph targets; header/footer text remains accessible through structured API modes. Replacing text in a paragraph containing drawings or fields is rejected to avoid deleting them; style changes remain possible. |
| PPTX | Actual slide render as background with editable text removed, native top-level horizontal text frames, drag/move/resize, text and whole-frame style | Insets and vertical anchor are shown. Tables, groups, charts, rotated frames, SmartArt and pictures remain on the background and are preserved in the source, not edited as canvas objects. Theme/inherited fonts, mixed runs, bullets and line metrics can differ. |
| PDF | Enumerate and replace native top-level horizontal text objects, change text/font size/bold/color/box, render saved pages | Rejects encrypted files, forms/signatures, rotated or non-zero-origin cropped pages. Text nested in Form XObjects or rotated text is not editable. Scanned pages show their background with no invented text fields. No OCR rewrite, annotation/form editor, page rearrangement or PDF-to-Word conversion. |
| PNG/JPEG/WebP | Static image controls, reference-image AI correction, PNG revision and history | Up to 16 MP. No animated images, painted masks, layers, arbitrary composition editor or image-to-video. AI can alter semantic details even when a visual reviewer accepts it. |
| XLSX/CSV/text | Existing structured editor and revision behavior retained | No new spreadsheet WYSIWYG engine or live Office connector. See [Office editor](OFFICE_EDITOR.md). |

DOCX/PPTX keep native OOXML source rather than replacing every page with a
bitmap. Unchanged runs remain intact when an AI change only adjusts styles.
A manual whole-paragraph/frame replacement may flatten its mixed formatting.
OOXML library round-trips do not guarantee lossless preservation of every
Microsoft Office extension. Review complicated source files separately.

## How real PDF editing works

The sandbox uses PDFium to enumerate text objects and obtain their text,
geometry, font size and fill color. Inspection removes editable objects only
from an in-memory copy to produce the clean background. Original stored bytes
are unchanged.

On save, PDFium removes the selected original objects and regenerates the page
content stream. ReportLab embeds replacement Sarabun text; pypdf merges it into
a fresh writer output. Other native text/vector/image objects remain. New text
is searchable, and the replaced phrase is absent from page text extraction in
the regression test. Width/multiline-height overflow is rejected instead of
silently clipping the replacement. Original embedded fonts are not reused.

This is **editing, not certified redaction**. Metadata, annotations, attachments
or duplicate content elsewhere may retain sensitive information. Do not use it
as a promise that every byte representing a secret has been purged. The older
structured `inspect/edit` PDF path still adds footer notes for compatibility;
the default visual UI uses `visual_inspect/visual_edit` and replaces native text.

Implementation references: [PDFium editing API](https://pdfium.googlesource.com/pdfium/+/aa5324e1dc80adecfb0b6a4bdb339e2c15e87195/public/fpdf_edit.h)
and [pypdfium2 helpers](https://pypdfium2.readthedocs.io/en/stable/python_api.html).

## AI review and bounded automatic correction

This new path is separate from the earlier deterministic PPTX edge-ink repair.
It calls the configured vision-capable chat model through the existing gateway.
No model-generated Python, JavaScript, shell, HTML, URL or filesystem path is
executed to implement a correction.

1. Resolve the owned conversation file; verify the inspected SHA and check that
   the source has no newer child before any model request.
2. Inspect the source into stable editable IDs. Convert Word/PPT with the
   isolated LibreOffice service to PDF, then render **every page**. Images use
   normalized visual previews. All-page AI review supports 1–8 pages; longer
   documents are refused before a model request, not silently sampled.
3. Send actual page images and bounded field metadata to the gateway with
   `visual_review: true`. The prompt treats visible document content as
   untrusted data and requests one strict JSON assessment.
4. Validate score 0–100, pass/fail, issues and typed corrections. Unknown fields,
   missing/null required fields, extra output, truncated completion and
   contradictory passing assessments fail. Only supplied IDs are allowed.
5. For Word/PPT, every proposed `value` must equal the source value exactly.
   Only whitelisted styles/geometry are applied by the fixed sandbox script.
   Word paragraph boxes are refused because Word uses flow layout.
6. For images, request a reference-image edit through the configured image
   model. The source is a normalized inline PNG. Accept one bounded inline
   generated image; remote output URLs are not fetched. Normalize the result
   with Pillow before reviewing it again.
7. Re-render the candidate and review its images again. Refuse changed page
   counts or changed editable Office text. Image candidates must retain aspect
   ratio within 0.01 and must not become fully opaque if the source had alpha.
   This checks the presence of transparency, not exact alpha-mask identity.
8. Require an increased model score **and** a passing follow-up review to
   publish. At most two correction attempts (up to three vision calls and two
   reference-image edits). Otherwise retain the original and return `unchanged`.
9. Save accepted bytes with compare-and-save revision conflict detection. Run
   Office structural/render checks without applying another heuristic edit
   after AI acceptance. Store the assessment/quality report in `change_log`.

Reports include `model`, `status` (`passed`, `repaired`, `unchanged`), rendered
page count, correction attempts, assessments and warning. `passed` means the
initial saved file needed no correction; it produces no new revision. `repaired`
includes the new file. Served model IDs are recorded from gateway responses
(configuration is the fallback if a response omits the ID).

Scores and verdicts are model judgments, not objective guarantees of aesthetics
or factual accuracy. Exact Office field equality does not prove every embedded
chart, image or hidden object is unchanged. Image text/identity preservation is
prompted and visually reviewed, not mathematically enforced. Higher-quality
models and a Thai/English regression benchmark remain necessary for a claim of
Claude/ChatGPT-equivalent results. PDF auto-repair and video-frame QA are not
part of this AI action.

## API and agent contract

The existing authenticated endpoint is reused:
`POST /api/v1/files/{fileID}/editor`.

```json
{"mode":"visual_inspect","page":0}
```

Returns `visual` with `kind`, source `sha256`, point/pixel dimensions, fields,
styles, optional boxes/insets, document blocks/background, page information and
AI configuration availability. `ai_available` indicates local configuration,
not live provider entitlement, tenant egress approval or proven model quality.

```json
{
  "mode":"visual_edit",
  "sha256":"<from visual_inspect>",
  "page":0,
  "changes":[{
    "id":"<opaque field ID>",
    "value":"Updated text",
    "style":{"size":20,"bold":true,"color":"#223344"},
    "box":{"left":36,"top":48,"width":360,"height":72}
  }]
}
```

Omit `box` for Word. For images use
`"image":{"rotation":90,"brightness":1.1,"contrast":1.05}` instead of
changes. `visual_preview` takes `page` and returns actual saved-page images;
it is read-only, needs no SHA, and works on old revisions. `ai_repair` requires
the source SHA and takes no model-authored code. The existing `history` and
`restore` contracts are unchanged.

Both agents' `edit_office_file` tools expose `visual_inspect`, `visual_edit`
and `ai_repair`, alongside the structured modes. The server pins agent requests
to the trusted conversation. Binary previews/embedded image blocks are stripped
from agent tool-result text to avoid filling context with base64; the internal
reviewer receives actual image blocks. Desktop uses a 660-second request bound
for `ai_repair`, and the service enforces an overall ten-minute budget. A closed
request cancels its context; concurrent expensive reviews can still be billed
before one loses the final revision race.

Limits include a 2 MiB editor request, 16 MiB source/artifact reads, existing
archive expansion checks, 1,000 editable fields, 20,000 characters per field,
100 model-proposed changes, 100 PDF/PPT pages for manual editing, 90 seconds per
sandbox task, 256 KiB AI field context and 128 KiB assessment JSON. The gateway
validates 1–8 inline review images, 4 MiB per image, 12 MiB total, and 16 MP per
static image. Large normalized image references can exceed the existing image
endpoint's 4 MiB input limit and be refused without publishing a revision.

## Deployment and data boundary

- Rebuild backend, chat-edge, shared Web renderer, Desktop host and the sandbox
  image containing `visual_editor.py`. No new database migration is required.
- `OFFICE_CONVERTER_URL` supplies the isolated Office renderer. The optional
  `docker-compose.office.yml` connects it to chat-edge on an internal network.
- Set `OFFICE_VISUAL_MODEL` to an enabled vision-capable chat model accepted by
  the existing gateway. Empty disables AI review; no implicit default model is
  selected. The optional Compose overlay passes this variable through.
- `IMAGE_GEN_MODEL` supplies reference-image corrections and must support that
  operation. Vision-only review can still return findings when no usable image
  edit model is available. Existing provider keys stay in backend configuration.
- The backend explicitly requires the tenant's `media_pii_egress=allow` for
  `visual_review`, even if prompt DLP is disabled. Missing tenants fail closed.
  Review is nonstreaming/tool-free, and remote image URLs are rejected.
- Raw page images can contain personal/confidential information that text DLP
  cannot redact. Text fields still pass through the normal gateway pipeline.
  Existing routing, billing and request attribution remain authoritative.

## Verification record

Validation uses synthetic local fixtures, not user production data. No live
vision, image-generation or video-provider API was invoked.

- Python sandbox: 45 tests cover the existing Office/motion stack plus native
  PDF replacement, retained searchable text, immutable source, Word styles and
  embedded-image safeguards, PPT geometry/backgrounds, real PDF rendering,
  complete-page coverage, rotated/scanned PDFs and image alpha/dimensions. A separate native PDF before/after render was visually inspected: the new title is visible, the other paragraph and vector rectangle remain intact.
- Go integration: real network-isolated sandbox, LibreOffice converter and
  disposable PostgreSQL databases cloned from all 50 chat migrations. Cases
  exercise native PDF revision/preview and mocked vision correction of DOCX,
  PPTX and PNG, rejection of a lower-scored candidate, source-text preservation
  and stale-revision refusal before another model call.
- Frontend tests cover contenteditable text/style saves, native PDF geometry,
  explicit AI invocation, report-only unchanged results, retained conflict
  drafts and the image attachment's editor entry point.
- Browser E2E passed against the real shared UI with mocked HTTP: open the attachment, edit Thai/English text, change bold/size, save a revision and verify no implicit AI call. The resulting screenshot was visually inspected.
- Web production build, TypeScript, i18n parity (1,843 app keys / 1,300 Agent Market keys) and theme checks passed.
- Backend: 114 distinct focused cases passed across visual policy and existing media/video/audio/tool-policy regressions (113-case suite plus the added route guard test).
- Backend policy tests cover missing/blocking tenants, eight valid local page
  images, external/corrupt images, page-count limits and nonstreaming policy.
- Full Go handler/router packages and vet passed against the isolated test template. Documentation inventory: 82 Markdown files, zero missing local link targets; both Office Compose combinations validate.
- Desktop regression suite: 917 tests in 94 files, typecheck and build passed;
  host tests cover conversation pinning, longer AI timeout, attachment outputs
  and stripping image bytes from tool-result text.

Representative local commands:

```text
backend: python -m pytest -o addopts= tests/test_visual_review.py tests/test_media_readiness.py tests/test_dlp_tool_policy_parity.py -q
web: npx vitest run src/components/chat/VisualFileEditor.test.tsx src/components/chat/MessageAttachments.download.test.tsx
web: npx playwright test tests/e2e/visual-editor.spec.ts
web: npx tsc --noEmit; npm run lint:i18n; npm run lint:theme; npm run build
desktop: npm test; npm run typecheck; npm run build
chat-edge: go test ./internal/service -run TestVisual -v -count=1
```

The Go integration requires `VISUAL_REVIEW_SANDBOX`,
`EDITOR_REVIEW_CONVERTER` and `CHAT_EDGE_TEST_DSN` for a prepared isolated test
server. Without the opt-in sandbox variable it explicitly skips the real
integration case. Review containers/tags are separate from application services.
Production deployment, native Electron window interaction, Microsoft Office
rendering, live model entitlement/cost and comparative output quality remain
unverified.
