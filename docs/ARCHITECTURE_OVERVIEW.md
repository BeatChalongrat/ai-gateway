# Architecture overview

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

The 2026-11-13 [Office/media review](OFFICE_MEDIA_PARITY.md) adds bounded reference-image editing, offline motion rendering and capability-backed video controls. Office artifacts and editable collaborative Office applications are separate capabilities.

Reviewed against source on 2026-11-12.

| Plane | Current implementation |
|---|---|
| Model/data | Go gateway → FastAPI orchestrator → provider adapter; DLP, routing, usage and fallback |
| User/chat | Portal identity, Go chat-edge, Next.js Web Chat; history, projects, memory, RAG, files and tools |
| Desktop | Electron/TypeScript host + shared web UI + .NET ToolsSidecar/BrowserHost |
| Documents | Shared templates/generators in per-run Docker sandbox; optional isolated Office recalculation/render gate; local/S3 artifacts |
| Agent jobs | Agent Market control plane, PostgreSQL queue, agent-runner, converter/fetcher |
| Operations | Admin UI, audit/traces, metrics, backups, retention and deployment scripts |

Web Chat runs its tool loop on the server. Desktop runs its loop in Electron.
Shared transcripts do not create a server-to-device execution bridge.

Read [system design](SYSTEM_DESIGN.md) for data ownership,
[root architecture](../ARCHITECTURE.md) for services and request flow,
[desktop architecture](DESKTOP_APP_ARCHITECTURE.md) for host responsibilities,
and [Office workflows](OFFICE_WORKFLOWS.md) for file capabilities and parity limits.
