# System design

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

Source review: 2026-11-12. [Root architecture](../ARCHITECTURE.md) owns service and port tables.

## Execution paths

1. Caddy provides browser-facing TLS. The Go gateway fronts FastAPI for model
   calls; FastAPI owns auth resolution, DLP, routing, providers and usage.
2. Web Chat sends conversations to Go chat-edge, which assembles context,
   invokes the gateway, executes tools and persists messages and files.
3. Electron hosts the shared web UI but owns its desktop agent loop. It calls
   the gateway for inference and chat-edge for history, memory and files.
   Local tools execute through the .NET MCP sidecar or TypeScript host handlers.
4. Office plans and Python run in fresh server sandbox containers. Selected
   inputs are read-only; outputs become conversation files. Desktop import/save
   tools join this path to granted local folders.
5. Agent Market has a separate agent-runner leasing queued work from the
   agent_market schema. Converter/fetcher services serve its specialized jobs.

## Data ownership

PostgreSQL holds gateway metadata, partitioned request logs, chat data and
Agent Market records. Backend Alembic and chat-edge SQL have separate histories.
Redis holds transient tokens, caches, limits and coordination state.

Conversation/knowledge bytes use the implemented local or S3 storage backend.
Desktop keeps host settings, grants, permissions and conversation state; its
cache does not prove offline inference capability. Retention and deletion are
defined in [retention](DATA_RETENTION_PLAN.md) and [user data](ops/user-data.md).

## Boundaries

Tenant/user/conversation checks guard server files; folder permissions guard
local operations. Text DLP has explicit exceptions and cannot redact image
pixels. A networkless sandbox can return results which later travel to a model
through a separate policy-controlled path.

The shared Office quality stage adds formula recalculation and page rendering
when its isolated converter is configured. Disabled conversion is reported as
partial verification; rendered pages still need visual/semantic review. The
[template guide](OFFICE_TEMPLATES.md) describes the 12 starters and deployment;
[Office workflows](OFFICE_WORKFLOWS.md) records tests and remaining parity gaps.
