# Office, image and video capability review

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

Follow-up implementation, 2026-11-13: [file editor, revisions, visual repair and video agents](OFFICE_EDITOR.md). Also see [visual editor, native PDF editing and AI review](VISUAL_EDITOR.md). The matrix and remaining-work rows below incorporate these follow-ups; older verification counts describe the preceding media work.

Review completed **2026-11-13 (Asia/Bangkok)**. Public sources checked November
12–13, 2026. Scope: repository `e0313410` plus the current uncommitted changes,
Web Chat and the Electron Desktop host. This is a source/test assessment, not a
claim that the installed application or production deployment has been updated.

## Findings

ระบบใกล้เคียงขึ้นในเส้นทาง **ไฟล์ต้นทาง → เครื่องมือ → ตรวจผล → ไฟล์ที่แก้ต่อได้**
แต่ยังไม่เท่ากับผลิตภัณฑ์ Claude/ChatGPT ทั้งชุด การเพิ่ม prompt หรือ template
อย่างเดียวไม่ทำให้คุณภาพเท่ากัน: ยังขึ้นกับโมเดลที่เรียกใช้ การเลือกเครื่องมือ
บริบทจากไฟล์ การตรวจภาพจริง และ UI สำหรับแก้ผลลัพธ์ร่วมกับผู้ใช้

งาน Office มีตัวสร้าง/แก้ไฟล์และ 12 templates อยู่แล้ว รอบนี้เติมการแก้ภาพจากไฟล์
อ้างอิงทั้งสอง client, 3 motion templates, การตรวจความพร้อมวิดีโอ และแก้ค่าเริ่มต้น
โมเดลที่เลิกให้บริการ โดยไม่อ้างว่าทราบโค้ดภายในหรือฝึกโมเดลให้เหมือนผู้ให้บริการ

## What public evidence actually establishes

| Evidence | What it supports; what it does not establish |
|---|---|
| [Claude file creation](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude) | Creates downloadable Excel, Word, PowerPoint and PDF through code/file capabilities. Does not guarantee perfect fidelity for every Office feature. |
| [Public Anthropic skills](https://github.com/anthropics/skills) | Published file workflows separate generation, editing, recalculation and inspection. These are useful design references, not a disclosure of every production component. |
| [Claude Sheets](https://support.claude.com/en/articles/18324300-get-started-with-claude-sheets) | A collaborative spreadsheet artifact with formulas, direct editing and version history. Its beta itself has limits, including no VBA/Power Query. Downloadable XLSX generation is only part of this experience. |
| [Claude Docs](https://support.claude.com/en/articles/16923645-get-started-with-claude-docs) and [Artifacts](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them) | Rich editable artifacts, including documents and slides, rather than only attachments. |
| [Claude Design](https://support.claude.com/en/articles/14604416-get-started-with-claude-design) | Canvas editing and design-system-guided visual work; not evidence that its internal renderer is our renderer. |
| [Claude Motion](https://support.claude.com/en/articles/17454997-get-started-with-claude-motion) | Animations are made from code for text/charts/shapes/images, can be revised and exported as MP4. It is not a model producing realistic footage. |
| [ChatGPT file workflows](https://learn.chatgpt.com/docs/artifacts-viewer) | Source files, preview, focused revisions and downloadable deliverables. Public product documentation does not expose all underlying Office libraries or orchestration. |
| [OpenAI image API](https://developers.openai.com/api/docs/guides/image-generation) | Distinct generation/edit endpoints and conversational workflows; current image families expose more controls than this gateway currently offers. |
| [GPT Image 1 retirement](https://developers.openai.com/api/docs/models/gpt-image-1) | Shut down October 23, 2026. The old repository default should not be described as current. |
| [GPT Image 2.6 Flare](https://developers.openai.com/api/docs/models/gpt-image-2.6-flare) | Current documented image generation/edit model and standard token rates. An account still needs access and a valid key. |
| [Sora / Videos API status](https://developers.openai.com/api/docs/guides/video-generation) | Shut down September 24, 2026; the retained examples are historical and the page says there is no one-to-one replacement API. We did not implement a new Sora adapter against a retired API. |

**Engineering interpretation:** the reusable pattern is an agent selecting
specialized tools, structured document/media inputs, isolated execution,
artifact persistence and review/revision. The concrete implementation here is
our own. No proprietary binaries, private APIs, weights or account data were
reverse engineered. A capability or model name in a database is not evidence of
provider access or a successful generation.

## Capability matrix after this change

“Implemented” below means source plus the stated test evidence. Deployment and
real-provider acceptance remain separate.

| Workflow | Web Chat | Electron Desktop | Remaining difference |
|---|---|---|---|
| Read/produce DOCX, XLSX, PPTX, PDF, CSV | Shared sandbox and plan tools | Same server services via host tools | Complex layout/semantic correctness still requires review |
| Reusable Office starters | 12 shared templates | Same 12 | No comprehensive brand-library UI or automatic style extraction |
| Edit existing documents | Direct Word paragraphs/PPT text frames; native PDF text replacement; structured spreadsheet editor | Same shared editor and visual agent modes after import | Scoped visual editing; not full Word/Acrobat fidelity or coauthoring. Mixed runs, scans, forms and complex OOXML have limits |
| File revisions | Immutable edit/restore revisions, history and conflict detection | Same shared UI and agent actions | No semantic diff/merge; older plan paths can branch |
| Visual repair | Configured vision review and bounded Word/PPT/image correction, plus deterministic PPTX edge checks | Same shared UI/backend and `ai_repair` host mode | Requires configured models, renderer and raw-image egress policy. No live quality benchmark, PDF auto-repair or video-frame QA |
| Recalculate and inspect Office output | Optional internal LibreOffice gate | Same gate | Converter must be deployed; it is not Microsoft Office; cached-value patch-only round trips are not implemented |
| Local folders | Uploaded attachments | Explicit import/save inside granted folders | Web cannot browse Desktop folders through a remote-device bridge |
| Text-to-image | Direct image route/tool | Image-mode host tool | Provider access, model quality and moderation are external dependencies |
| Reference-image editing | Current-turn attachments on direct image path; `source_file_ids` on the tool | `source_file_ids` on image-mode host tool | Shared image controls/history now available; no painted-mask editor or automatic last-image selection |
| Reuse generated image | Attached file IDs returned by tool | Attached file IDs returned by tool | Direct Web image mode without an attachment does not automatically choose a previous picture; reattach it or use an explicit tool reference |
| Animated explainer, metric chart, slideshow | `run_python` motion templates → MP4 and previews | Same; output can be saved to a granted local folder | Silent exports; no timeline GUI, real-time collaborative editor or generated narration |
| Generative footage | Submit/poll tool plus `video_job` status/cancel and recoverable jobs panel | `generate_video`/`video_job` host tools and shared jobs panel | Provider verification required; no image-to-video, remix, extension or audio-control contract |
| Video readiness | Config-backed form, DB key, installed adapter, active model and owner verification | Shared form where exposed | No paid provider smoke test in this review; each Replicate model schema must be verified |
| Audio | Existing TTS/music adapters and jobs | Existing shared surfaces | Not connected to the new motion renderer as automatic narration |
| Live Office/coauthoring | Existing canvas is not a full native Office editor | No live Excel/Word add-in introduced | Collaborative cells/slides/docs, semantic revision diff/merge and connectors remain distinct work |

## Implemented backend and client changes

### Reference-image editing

1. The existing `generate_image` tool accepts `source_file_ids` on both clients.
   It preserves requested literal text and its original language instead of
   requiring every visible label to be translated into English.
2. Chat Edge resolves each ID with `OpenConversationFileCopy` using tenant,
   user and conversation scope. Desktop checks conversation membership then
   downloads with the authenticated, bounded file client. No model-supplied URL
   or arbitrary local path becomes an image source.
3. The public `POST /v1/images/generations` contract gains the SRAN extension
   `source_images`: 1–4 inline PNG/JPEG/WebP data URLs when editing. Maximum
   4 MiB per source, 12 MiB total, 16 megapixels per still image. Body allowance
   for this route is 20 MiB; the gateway's existing default cap is 25 MiB.
4. Backend validates base64, actual file format, dimensions and animation status.
   Raw references require an existing tenant with `media_pii_egress=allow`, even
   when prompt DLP is disabled. Prompt DLP does not inspect or redact the pixels.
5. Generation uses the provider generation endpoint; references use its edit
   endpoint and multipart `image[]`. The shared HTTP client now handles binary
   multipart requests without trying to inspect them as JSON or mislabelling
   them with `Content-Type: application/json`.
6. Auth, rate limits, prompt checks, credit gates and destination attribution
   remain shared. An edit-unsupported fallback raises an error; it never drops
   the input images and silently returns a new text-only generation.
7. Generated files persist in conversation storage. Tool responses include the
   resulting file ID for subsequent edits. Image content is not added to logs.

There is no pixel-level DLP guarantee. The request audit uses a raw-egress action
for references; it does not invent PII categories detected in pixels. Image cost
uses reported text/image/output token counts at standard rates when complete;
otherwise it falls back to the historical output-only estimate. The response
now says `usage.cost_estimated=true`; missing usage, cache discounts and provider
invoices still need reconciliation. This is not a new exact-billing subsystem.

### Editable motion templates

Library: `services/chat-edge-api/scripts/motion_templates.py`.

| Template | Inputs | Output behavior |
|---|---|---|
| `motion.explainer` | Title and short text per scene | Fade-in title/caption sequence |
| `motion.metrics` | Named, nonnegative numeric values and explicit unit | Animated proportional bars; values supplied by the caller |
| `motion.slideshow` | Local image asset per scene, title and caption | Image-and-caption sequence |

```python
import sys
sys.path.insert(0, "/sandbox/scripts")
import motion_templates as motion
print(motion.catalog())
result = motion.render("motion.metrics", {
    "title": "Project status — sample data",
    "width": 960, "height": 540, "fps": 12,
    "scenes": [{"title": "Completed tasks", "duration": 3,
                "body": "Use verified project data before delivery",
                "bars": [{"label": "Done", "value": 42}], "unit": " tasks"}]
}, "/job/out/project-status.mp4")
print(result)
```

The renderer writes four artifacts: MP4, poster PNG, contact-sheet PNG and
storyboard JSON. Load `template_id` and `data` from that JSON, change specific
fields and render to a **new** output filename for a revision. Image paths in a
storyboard refer to mounted assets; those assets must be supplied again in a new
run. The JSON is editable source, not a self-contained asset archive.

Limits: 1–12 scenes, 1–10 seconds each, total up to 30 seconds, 6–24 fps, even
dimensions within 320–1280 × 240–720, at most five bars per scene and output below
15 MiB. Text is fitted and overflow rejected before encoding. Existing outputs
are not overwritten. FFmpeg encodes H.264/yuv420p MP4 with fast-start metadata;
Pillow and bundled Sarabun fonts render frames. Frames stream to the encoder;
the library does not retain an entire uncompressed clip in memory.

FFmpeg is included only in the code sandbox image. The existing network-none,
read-only root, nonroot user, seccomp, CPU/memory/process limits remain. Code
cells still have the existing 90-second wall limit. MP4 has an explicit MIME
mapping, and files above the 16 MiB artifact reader limit are rejected instead
of silently truncated. These are generated graphics, not realistic footage.

### Video and model readiness

- `/v1/video/config` uses the same DB-backed key service as the Replicate adapter;
  an old environment token alone no longer makes the UI claim readiness.
- Only active models with an installed real video adapter count as available.
  Owner verification and `VIDEO_GEN_ENABLED` remain required. Real-video model
  validation applies the flags on the internal job path too.
- Chat Edge proxies `GET /api/v1/video/config`. The shared form waits for this
  answer, shows an unavailable reason and uses the configured duration maximum.
- Resolution/audio were previously accepted but ignored by the adapter. Current
  public and internal contracts accept only `resolution="auto"` and
  `has_audio=false`; unsupported requests return validation errors. The UI no
  longer offers misleading 720p/1080p and audio controls. Durations outside the
  configured range (at most 60 seconds) are rejected before spending, rather than shortened.
- Alembic **090** adds `gpt-image-2.6-flare` as an image-only catalogue entry,
  replaces only the exact legacy seeded image order, hides retired GPT Image 1
  from the image catalogue and disables OpenAI `sora-*` video entries. Image
  models remain outside chat auto-routing. Custom destination orders are not
  rewritten; operators must review any retired models in them.
- Premium video jobs are stored as `pending_confirm`, which the worker cannot claim. Owner-only `POST /v1/jobs/{id}/confirm` rechecks expiry, model gates, balance and quote before queueing. Cancellation does not refund an unmade reservation, and confirmation cannot revive a cancelled job. The shared video form now confirms on the server. The chat tool reports the pending quote instead of polling indefinitely; the shared Video jobs panel now provides explicit in-chat approval and recovery. Audio has not gained this video-specific gate.
- No API key, model entitlement or verification flag is created automatically.
  Migration downgrade does not resurrect retired providers or delete history.

## Activation and acceptance

1. Review/deploy backend migration 090, backend service, Go gateway/Chat Edge and
   updated Web/Desktop builds. This review did not apply migrations to the user's
   database or replace the installed Desktop app.
2. Rebuild the sandbox from `services/chat-edge-api/sandbox/Dockerfile.sandbox`.
   A separate `ai-gateway-sandbox:media-review` image was used for this review;
   production `:latest` was not overwritten. Include FFmpeg's distributed
   license notices when shipping the image.
3. For Office rendering/recalculation, enable the existing
   [Office overlay](OFFICE_TEMPLATES.md#deployment). Without it, validation reports
   partial evidence rather than pretending a visual check occurred.
4. Configure provider keys in the admin UI, review media destination order,
   account model access, tenant raw-image policy, budgets and current Replicate
   input schema. Use the configured provider model identifier, not an old sample
   alias from a historical migration.
5. Run a bounded real-provider smoke test with explicit reference images, then
   inspect the output, attribution, cost and stored file IDs. This review used
   mocked provider HTTP contracts, so it does not certify live account access.

## Verification evidence

- Desktop: TypeScript check and **910 tests / 93 files** passed, including source
  membership, reference limits, cancellation and the existing local-file suite.
- Backend: **69 focused tests** passed across image providers/fallbacks, raw
  egress, multipart edit transport, cost calculation, video capability and
  public submission, premium confirmation and migration semantics. No paid image/video API call was made. Test-session cleanup now disposes pooled SQLite connections so Windows pytest exits cleanly.
- Go: image tool/direct-path ownership, artifact overflow, Office/code and media
  proxy tests; handler/service vet checks. Tests exercise request boundaries,
  not a running model deciding which tool to call. The preceding media pass did not have PostgreSQL. The editor follow-up started an isolated migrated template and passed the full handler/router packages; see OFFICE_EDITOR.md.
- Web: **11 targeted tests / 3 files**, TypeScript, i18n parity (1788 app keys plus 1300 Agent Market keys), theme
  check and targeted media/Office component tests. Video form tests cover loading,
  disabled capability, duration bounds, submitted controls and explicit premium confirmation.
- Sandbox: **27 Python tests passed** with nonroot, network-none, read-only root,
  seccomp and resource limits. The motion test encodes and probes a real MP4.
- Three representative 6-second, 960×540, 12-fps clips were rendered (explainer,
  metrics, slideshow). All three were fully decoded and probed as H.264, 72 frames, 6 seconds. Thai/English contact sheets and sampled decoded motion frames were visually reviewed.
  These samples do not prove every possible template input or timing is correct.

Not verified here: native Electron interaction, real-model autonomous Office
delivery, Microsoft Office rendering, provider paid generation, production
migration/deployment, long-running multi-user workloads or equivalent output
quality to Claude/ChatGPT on a common benchmark.

## Remaining work, ordered by user impact

| Priority | Gap | Concrete acceptance target |
|---|---|---|
| P1 | Semantic revision diff and media lineage | Office/image history/restore and AI proposal before/after differences implemented; add arbitrary historical semantic comparisons and video lineage |
| P1 | Broader visual correction | Word/PPT/image vision loop implemented and provider-mocked; validate live quality on a measured corpus and add PDF/video QA |
| P1 | Brand/template fidelity | Reuse actual source layouts, logos and typography; regression corpus covering difficult OOXML features |
| P1 | Rich document, sheet and slide editing | Scoped visual paragraph/text-box/PDF editing implemented; persistent file drafts now implemented; add full Office fidelity, grid operations and collaboration |
| P1 | Generative-video production acceptance | Host tools and shared confirmation/recovery panel now implemented; verify a live model and add idempotent automatic output collection |
| P2 | Image editor and advanced API options | Painted masks/layers and composition controls; existing controls preserve alpha and revisions. Validate live reference editing and capability-specific pricing |
| P2 | Model-specific generative video inputs | Validated reference-image/audio/size/duration schemas per provider model, retry/idempotency and live contract acceptance |
| P2 | Narrated motion and timeline editor | User-editable timing, supplied audio, subtitles and synchronized export without truncation |
| P2 | Office and cloud connectors | Explicit OAuth scopes and live Excel/Google Docs/Sheets/Slides editing; do not pretend file upload is a live connector |
| P2 | Web-to-local-device bridge | Authenticated connected-folder requests, device availability and per-operation approval; browser currently requires uploads |
| P2 | Artifact quality benchmark | Same Thai/English tasks and source files, formula truth, visual quality, edit preservation, latency and cost measured across products |

Related implementation detail: [Office workflows](OFFICE_WORKFLOWS.md),
[Office templates](OFFICE_TEMPLATES.md), [media API](api-media-generation.md),
[Desktop architecture](DESKTOP_APP_ARCHITECTURE.md).
