# SRAN Desktop architecture

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

Visual editor follow-up (2026-11-13): the Electron renderer shares Word/PPT/PDF/image editing and version controls with Web Chat. Host `edit_office_file` exposes visual modes and `ai_repair`, with binary previews excluded from tool-result text and a bounded 660-second review request. See [current behavior and limits](VISUAL_EDITOR.md).

Media follow-up (2026-11-13): image-mode `generate_image` accepts conversation `source_file_ids`, size and quality, returning reusable output IDs. `run_python` shares three silent MP4 motion templates. Local transfer also supports WebP/GIF/MP4/WAV. The host now also exposes `edit_office_file`, `generate_video` and `video_job`; see [editor/revision/repair/video details](OFFICE_EDITOR.md) and [the capability matrix](OFFICE_MEDIA_PARITY.md).

Reviewed against source on **2026-11-12**. This replaces the July WPF design as
the current client reference; that implementation remains in repository history.

## Runtime and responsibilities

The shipped client source is `apps/desktop`: Electron 44 and TypeScript.
Its BrowserWindow hosts the Next.js Web Chat UI. The renderer does not receive
general Node or shell access; preload/IPC and navigation policy define the bridge.

| Component | Responsibility |
|---|---|
| main/index.ts | Startup, BrowserWindow, protected session/config, sidecar wiring |
| main/turn-coordinator.ts | Desktop tool loop, gateway requests, consent, cancellation, events, transcript import |
| main/host-tools.ts | Tools requiring host session/orchestration, including Python and local file transfer |
| main/local-office-files.ts | Bounded import/save between granted folders and conversation storage |
| main/chat-edge-client.ts | History, files, memory and code-run HTTP calls using the host's user session |
| main/gateway-client.ts | Model/tool inference through gateway |
| main/permissions.ts | Tool metadata, danger classification, scope and Auto/Manual/Skip policy |
| main/grants.ts | Persistent chosen folders and grant validation |
| main/attachments.ts | Conversation-bound attachment resolution and untrusted document envelopes |
| main/prompt-assembler.ts | Context assembly, language rules, memory and host facts |
| .NET ToolsSidecar / BrowserHost | Local MCP filesystem, document, shell, browser and screen capabilities |

The .NET components live under `SranDesktop`. Its WPF window is legacy; treating
the whole directory as dead would remove runtime dependencies. The packaged app
uses its bundled `resources/sidecar/SranDesktop.ToolsSidecar.exe`; development
uses the configured sidecar path. Packaging is defined in `scripts/dist.js`
inside the desktop project.

## One UI, two agent loops

Web Chat normally sends turns to chat-edge. In Desktop the host coordinates
model/tool turns and emits bridge events into the shared UI. It imports the
visible transcript into chat-edge for history/synchronization. Neither a shared
UI nor shared history means Web Chat can issue commands to a remote Desktop.

Use [STREAM_CONTRACT.md](STREAM_CONTRACT.md) for event ordering, terminal state
and reconnect semantics. Agent Market jobs have a separate server queue and
must not be confused with a desktop turn that depends on the host process.

## Office/local-file path

1. Grant a working folder in Desktop Settings.
2. Read/search local files through sidecar tools; use `import_local_file` to
   copy supported original bytes into conversation storage when editing.
3. The host returns a file ID. `run_python` forwards selected IDs to chat-edge
   with the conversation ID; inputs mount read-only in the server sandbox.
4. Python writes outputs under `/job/out`. Chat-edge persists files and returns
   IDs; the host emits attachments and imports their linkage with the transcript.
5. `save_conversation_file` downloads a verified conversation file into a new
   local path. Existing files are refused, so editing does not silently replace
   the original.

The [Office workflow reference](OFFICE_WORKFLOWS.md) specifies extensions, 25 MiB
transfer limits, PowerPoint edits, Word preservation, tests and parity gaps.
Generated-file IDs are reusable in later calls; Python process state is not.
The shared sandbox also contains 12 document/data starters. Server quality
review can recalculate workbooks and return rendered previews. Desktop's code
HTTP deadline is 300 seconds to cover execution and bounded review; cancellation
still applies. See [templates and checks](OFFICE_TEMPLATES.md).
The host now marks failed/expired Python runs as failures instead of successful
tool results.

## Permissions

The host chooses scope from host-owned folder grants or browser-site policy.
The model cannot grant itself access by supplying extra arguments.
Auto/Manual/Skip behavior is defined in `permissions.ts`; safe reads can run
without a new prompt, while writes/transfers normally ask. “Every tool always
asks” is not the implemented policy.

Import explicitly discloses server upload. Import/save still enforce absolute
paths, containment, size/type constraints and link/device-path rejection when
Skip is selected. Save verifies conversation membership and uses exclusive
creation. Local shell execution is a separate capability under shell consent;
it is not inside the server Python container.

## Identity and configuration

The host holds the Portal session; the sidecar does not need the JWT. Google
sign-in uses the configured portal/gateway flow and registered deep-link scheme.
Admin authentication is separate from end-user identity.

Configuration resolution:
1. `SRAN_DESKTOP_CONFIG` absolute-path override.
2. Packaged `resources/desktop.config.json`.
3. Development `config/desktop.config.json`.

Page origins, API origins, CSP and trusted CA settings are validated before
startup. Current localhost defaults use Caddy HTTPS. See
[desktop config](../apps/desktop/config/README.md) and [TLS](ops/tls.md).
Do not disable certificate validation to work around an absent local CA.

## State and lifecycle

Grants, permissions, task state and local conversation state live in host stores.
Shared conversation files live on the server. Desktop memory handling follows
the conversation's memory/temporary-chat policy. Sign-out, cache wipe and
server retention have different scopes; see [user data](ops/user-data.md).

The presence of local state does not make cloud model inference or server file
generation work without a connection. The new transfers need chat-edge online.
A source edit does not update an installed executable or server container.

## Build and validation

From `apps/desktop`: `npm run typecheck`, `npm test`, `npm run build`.
Installer generation uses `npm run dist -- --config <path>` and the packaging
configuration; it is distinct from a TypeScript build. Rebuild Web Chat,
chat-edge and the sandbox image when shipping the Office changes.

The local-file regression uses actual temporary files and checks upload bytes,
reusable IDs, new-file saves, refusal to overwrite, folder/link rejection,
ownership, size and cancellation. Host unit tests do not replace a native UI
walkthrough or Microsoft Office visual verification.
