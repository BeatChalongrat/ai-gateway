# API surfaces and contracts

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

Visual contract: [visual inspection/edit/preview and AI repair schemas](VISUAL_EDITOR.md#api-and-agent-contract). Backend `POST /v1/chat/completions` accepts `visual_review: true` for nonstreaming, tool-free assessments with tenant-approved inline page images.

Editor/media API addition (2026-11-13): `POST /api/v1/files/{fileID}/editor` supports inspect/edit/history/restore plus visual_inspect/visual_edit/visual_preview/ai_repair; `GET /api/v1/jobs?conversation_id=<UUID>` lists owner-scoped video jobs; video config includes the configured agent model. See [schemas, ownership and conflicts](OFFICE_EDITOR.md).

Media extensions (2026-11-13): `POST /v1/images/generations` accepts bounded inline `source_images`; Chat Edge proxies `GET /api/v1/video/config` and `POST /api/v1/jobs/{id}/confirm`. Premium video submission returns `pending_confirm`, not runnable work. See [media API contracts](api-media-generation.md).

Source review: 2026-11-12. Route registration, schemas and middleware are authoritative.

| Surface | Owner | Authentication/reference |
|---|---|---|
| Inference/media | Go gateway → FastAPI | Project key or supported service identity; route-specific public exceptions; [Public API](PUBLIC_API.md) |
| Admin | FastAPI | Admin session/RBAC and CSRF for cookie mutations; [Admin API](ADMIN_API.md) |
| Portal | FastAPI + portal | Portal session/JWT and OAuth; [SSO](SSO-GO-LIVE.md) |
| Chat/history/files | Go chat-edge /api/v1 | Portal JWT and tenant/user/conversation ownership |
| Local tools | Electron + MCP sidecar | Folder/site grants and consent; [Desktop](DESKTOP_APP_ARCHITECTURE.md) |
| Agent jobs | chat-edge + agent-runner | User control plane and service credentials; [Agent Market](agent-market/ARCHITECTURE.md) |

## Office endpoints

These routes belong to chat-edge, prefixed with `/api/v1`, and require user identity.

| Method/path | Purpose |
|---|---|
| POST /conversations/{convID}/files | Multipart upload |
| GET /conversations/{convID}/files | List conversation files |
| GET /files/{fileID}/download | Bytes or storage redirect |
| POST /files/{fileID}/inspect | Office structure inspection |
| POST /conversations/{convID}/excel/execute | Excel JSON plan |
| POST /conversations/{convID}/document/execute | DOCX/PDF/PPTX plan |
| GET /conversations/{convID}/files/{fileID}/versions | File lineage |
| POST /code/run | Python with optional input IDs and conversation ID |

`/code/run` accepts `code`, `files: string[]` and optional `conversation_id`.
Named-conversation inputs must be owned by the caller and belong to that
conversation. IDs are deduplicated. Outputs receive durable IDs when a
conversation is supplied. Without one, the legacy owner-scoped input and
inline/nonpersistent output behavior remains. A nonzero exit or timeout is
execution failure even when the HTTP transport succeeds.

The shared Office quality stage can change `exit` to nonzero and remove a failed
Office artifact before persistence. When the converter is configured, Python
outputs can include PDF/PNG previews and stdout includes a quality report.
Plan responses expose `validation.office_quality` (status, recalculated,
formulas, rendered_pages, warnings). It is optional for older responses.
See [Office templates](OFFICE_TEMPLATES.md) for limits and configuration.

Plan requests carry `plan`, optional `source_file_id`, `output_filename` and
`message_id`; document plans also carry `format`. Same-format Office sources
are edited as copies; other sources provide content for a new file. PDF plans
always build new PDFs. See [Office workflows](OFFICE_WORKFLOWS.md) for actions,
failure semantics and Desktop transfer tools.

Web SSE and Electron events share the [stream contract](STREAM_CONTRACT.md).
A Windows path in a browser message is data, not a local filesystem grant.
