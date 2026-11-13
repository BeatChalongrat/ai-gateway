# Database and storage map

Migration 051 adds owner-scoped `chat.file_drafts` (2 MiB bodies with revision CAS) and `chat.file_work` (bounded checkpoints/candidates, expiring proposals, conditional state transitions). Accepted revisions continue in `chat.conversation_files`; unpublished candidates are not conversation attachments. [Retention and recovery](ARTIFACT_WORKSPACE.md#storage-privacy-and-deployment).

Source review: 2026-11-12. This is a responsibility map, not a generated DDL dump.
Check migrations and the deployed migration table before changing a database.

| Store | Schema authority | Responsibilities |
|---|---|---|
| Gateway PostgreSQL | backend/migrations/versions, Alembic through 090 in this checkout | Tenants/projects/keys, providers/models/policies, identity, credits, partitioned request logs, audit |
| Chat PostgreSQL | services/chat-edge-api/migrations, SQL through 051 | Conversations/messages/branches, files, memories/projects, knowledge bases, tasks, exports/deletion |
| Agent Market | services/chat-edge-api/internal/agentmarket migrations | Catalog/install/thread/run/queue/artifact state in agent_market |
| Blob storage | services/chat-edge-api/internal/storage | Original uploads and generated/knowledge files, local or S3-compatible |
| Redis | Runtime configuration | Transient tokens/cache, rate limits and coordination |
| Desktop | apps/desktop/src/main stores | Host state, grants, permissions and local conversations; protected credentials |

## Office lineage

`chat.conversation_files` stores metadata and storage keys, not Office bytes.
Fields include conversation/user/tenant, original filename, MIME, size,
extraction/inspection state, generated flag, parent ID, version, message ID
and change log. Plan-generated copies record source lineage. Generic Python
outputs do not automatically infer a parent file for every edit.

Desktop import uses the same upload path as Web Chat. Saving a new local copy
does not change the server version or remove the upload. Named-conversation
Python inputs require both ownership and membership.

Request-log partitioning is implemented. Soft deletion, delayed hard purge,
object cleanup, exports and backup expiry are separate mechanisms:
[retention](DATA_RETENTION_PLAN.md), [user data](ops/user-data.md).
Source migration numbers do not establish a running host's schema; an image
rollback does not reverse migrations or restore deleted data.
