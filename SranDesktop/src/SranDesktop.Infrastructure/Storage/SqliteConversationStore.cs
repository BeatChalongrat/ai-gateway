using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Domain.ValueObjects;

namespace SranDesktop.Infrastructure.Storage;

public sealed class SqliteConversationStore : IConversationStore
{
    // Migration 0 = the original baseline schema, verbatim. It uses
    // CREATE TABLE IF NOT EXISTS throughout, so a legacy database (tables
    // present, user_version still 0) replays it as a no-op — see
    // SqliteMigrator's bootstrap rule. NEVER edit the baseline; add a new
    // migration string to Migrations instead.
    private const string Schema = """
        CREATE TABLE IF NOT EXISTS conversations (
            id              TEXT PRIMARY KEY,
            title           TEXT,
            model_id        TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            is_pinned       INTEGER DEFAULT 0,
            folder_id       TEXT,
            source          TEXT NOT NULL DEFAULT 'server' CHECK(source IN ('server','local')),
            last_synced_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
            content         TEXT,
            tool_calls      TEXT,
            tool_call_id    TEXT,
            name            TEXT,
            model_id        TEXT,
            token_usage     TEXT,
            created_at      TEXT NOT NULL,
            sort_order      INTEGER NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS files (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            name            TEXT NOT NULL,
            mime_type       TEXT,
            size_bytes      INTEGER,
            local_path      TEXT,
            server_url      TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tool_executions (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            message_id      TEXT,
            tool_name       TEXT NOT NULL,
            arguments       TEXT NOT NULL,
            result          TEXT,
            permission      TEXT NOT NULL CHECK(permission IN ('auto','allowed','denied')),
            duration_ms     INTEGER,
            error           TEXT,
            executed_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tool_permissions (
            tool_name       TEXT NOT NULL,
            scope           TEXT NOT NULL,
            permission      TEXT NOT NULL CHECK(permission IN ('allow_always','deny_always','ask')),
            updated_at      TEXT NOT NULL,
            PRIMARY KEY (tool_name, scope)
        );

        -- No FK to conversations: a branch can be saved before the conversation
        -- row syncs locally. DeleteConversationAsync cascades manually.
        CREATE TABLE IF NOT EXISTS branch_sets (
            branch_key      TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            active_index    INTEGER NOT NULL DEFAULT 0,
            tails_json      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_convo ON messages(conversation_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_tool_exec_convo ON tool_executions(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_files_convo ON files(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_branch_sets_convo ON branch_sets(conversation_id);
        """;

    // Ordered migrations; index = the user_version AFTER applying. SQLite has
    // no ADD COLUMN IF NOT EXISTS — the version gate is what makes the ALTERs
    // safe to ship.
    internal static readonly string[] Migrations =
    {
        Schema,

        // 1 — server-id mapping for the one-way agent-conversation push sync
        // (Phase 3 D). conversations.server_id = the chat-edge conversation id
        // this local conversation mirrors to; messages.server_id = the server
        // CONVERSATION id the row was pushed to (non-NULL ⇒ synced marker; the
        // server stores our message id as its client_id, so no per-message
        // server id exists or is needed).
        """
        ALTER TABLE conversations ADD COLUMN server_id TEXT;
        ALTER TABLE messages ADD COLUMN server_id TEXT;
        CREATE INDEX IF NOT EXISTS idx_messages_server_id ON messages(server_id);
        """,

        // 2 — composer draft persistence (Phase 3 C4).
        """
        CREATE TABLE IF NOT EXISTS drafts (
            conversation_id TEXT PRIMARY KEY,
            text            TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        """,

        // 3 — agent memory notes (Phase B PART 3). No FK (matches branch_sets);
        // DeleteConversationAsync cascades manually.
        """
        CREATE TABLE IF NOT EXISTS agent_notes (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_notes_convo ON agent_notes(conversation_id, created_at);
        """,

        // 4 — per-message attachment linkage (FIX 10). JSON array of SERVER
        // file ids attached to a user message, so the push sync can send
        // file_ids on import and the file shows under its bubble on web.
        """
        ALTER TABLE messages ADD COLUMN file_ids TEXT;
        """,

        // 5 - durable goal/plan/state runtime. Structured details are JSON,
        // while identity, status, version and correlation fields remain
        // queryable and constrained. Existing conversation rows are untouched.
        """
        CREATE TABLE IF NOT EXISTS goals (
            goal_id          TEXT PRIMARY KEY,
            conversation_id  TEXT NOT NULL,
            original_request TEXT NOT NULL,
            normalized_goal  TEXT NOT NULL,
            desired_outcome  TEXT NOT NULL,
            risk_level       TEXT NOT NULL,
            version          INTEGER NOT NULL,
            goal_json        TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS definition_of_done_items (
            item_id          TEXT PRIMARY KEY,
            goal_id          TEXT NOT NULL,
            description      TEXT NOT NULL,
            verification_type TEXT NOT NULL,
            required_evidence TEXT NOT NULL,
            required         INTEGER NOT NULL,
            status           TEXT NOT NULL,
            verification_result TEXT,
            evidence_ids_json TEXT NOT NULL,
            FOREIGN KEY (goal_id) REFERENCES goals(goal_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_tasks (
            task_id          TEXT PRIMARY KEY,
            conversation_id  TEXT NOT NULL,
            goal_id          TEXT NOT NULL,
            owner_scope      TEXT NOT NULL,
            mode             TEXT NOT NULL,
            status           TEXT NOT NULL,
            current_plan_version INTEGER NOT NULL,
            current_milestone_id TEXT,
            current_step_id  TEXT,
            task_json        TEXT NOT NULL,
            version          INTEGER NOT NULL,
            checkpoint_version INTEGER NOT NULL,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            completed_at     TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY (goal_id) REFERENCES goals(goal_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_plans (
            plan_id          TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            version          INTEGER NOT NULL,
            goal_version     INTEGER NOT NULL,
            status           TEXT NOT NULL,
            planning_reason  TEXT NOT NULL,
            supersedes_plan_id TEXT,
            created_by       TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            plan_json        TEXT NOT NULL,
            UNIQUE(task_id, version),
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_milestones (
            milestone_id     TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            plan_id          TEXT NOT NULL,
            sequence         INTEGER NOT NULL,
            title            TEXT NOT NULL,
            objective        TEXT NOT NULL,
            status           TEXT NOT NULL,
            milestone_json   TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
            FOREIGN KEY (plan_id) REFERENCES task_plans(plan_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_steps (
            step_id          TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            milestone_id     TEXT NOT NULL,
            status           TEXT NOT NULL,
            side_effect_class TEXT NOT NULL,
            attempt_count    INTEGER NOT NULL,
            failure_code     TEXT,
            step_json        TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
            FOREIGN KEY (milestone_id) REFERENCES task_milestones(milestone_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_checkpoints (
            checkpoint_id    TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            version          INTEGER NOT NULL,
            plan_version     INTEGER NOT NULL,
            task_status      TEXT NOT NULL,
            current_milestone_id TEXT,
            current_step_id  TEXT,
            pending_action_id TEXT,
            resume_safety_status TEXT NOT NULL,
            checksum         TEXT NOT NULL,
            checkpoint_json  TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            UNIQUE(task_id, version),
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_actions (
            action_id        TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            plan_id          TEXT NOT NULL,
            milestone_id     TEXT NOT NULL,
            step_id          TEXT NOT NULL,
            tool_call_id     TEXT NOT NULL,
            tool_name        TEXT NOT NULL,
            arguments_fingerprint TEXT NOT NULL,
            idempotency_key  TEXT NOT NULL,
            side_effect_class TEXT NOT NULL,
            status           TEXT NOT NULL,
            attempt          INTEGER NOT NULL,
            failure_code     TEXT,
            failure_reason   TEXT,
            action_json      TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            UNIQUE(task_id, idempotency_key),
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_evidence (
            evidence_id      TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            step_id          TEXT,
            action_id        TEXT,
            evidence_type    TEXT NOT NULL,
            source           TEXT NOT NULL,
            summary          TEXT NOT NULL,
            structured_data  TEXT NOT NULL,
            artifact_reference TEXT,
            confidence       REAL NOT NULL,
            sensitivity      TEXT NOT NULL,
            retention_policy TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS world_state_snapshots (
            world_state_id   TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            version          INTEGER NOT NULL,
            source           TEXT NOT NULL,
            confidence       REAL NOT NULL,
            observed_at      TEXT NOT NULL,
            expires_at       TEXT,
            snapshot_json    TEXT NOT NULL,
            UNIQUE(task_id, version),
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_approvals (
            approval_id      TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            step_id          TEXT NOT NULL,
            action_fingerprint TEXT NOT NULL,
            scope            TEXT NOT NULL,
            status           TEXT NOT NULL,
            expires_at       TEXT NOT NULL,
            approval_json    TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_artifacts (
            artifact_id      TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            step_id          TEXT,
            path_or_uri      TEXT NOT NULL,
            content_hash     TEXT,
            sensitivity      TEXT NOT NULL,
            retention_policy TEXT NOT NULL,
            artifact_json    TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_events (
            event_id         TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            event_type       TEXT NOT NULL,
            plan_id          TEXT,
            milestone_id     TEXT,
            step_id          TEXT,
            action_id        TEXT,
            tool_call_id     TEXT,
            checkpoint_id    TEXT,
            world_state_version INTEGER,
            event_json       TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_goals_conversation ON goals(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_conversation_status ON agent_tasks(conversation_id, status);
        CREATE INDEX IF NOT EXISTS idx_plans_task_version ON task_plans(task_id, version);
        CREATE INDEX IF NOT EXISTS idx_steps_task_status ON task_steps(task_id, status);
        CREATE INDEX IF NOT EXISTS idx_checkpoints_task_version ON task_checkpoints(task_id, version DESC);
        CREATE INDEX IF NOT EXISTS idx_actions_task_status ON task_actions(task_id, status);
        CREATE INDEX IF NOT EXISTS idx_evidence_task_step ON task_evidence(task_id, step_id);
        CREATE INDEX IF NOT EXISTS idx_world_state_task_version ON world_state_snapshots(task_id, version DESC);
        CREATE INDEX IF NOT EXISTS idx_events_task_created ON task_events(task_id, created_at);
        """,

        // 6 - Prompt 4 real-world skill runtime health, durable metrics and
        // verified compensation ledger. Existing task JSON remains compatible.
        """
        CREATE TABLE IF NOT EXISTS capability_health (
            capability_id   TEXT PRIMARY KEY,
            provider        TEXT NOT NULL,
            availability    TEXT NOT NULL,
            health          TEXT NOT NULL,
            capability_json TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_metrics (
            metric_id       TEXT PRIMARY KEY,
            task_id         TEXT NOT NULL,
            skill_id        TEXT,
            name            TEXT NOT NULL,
            value           REAL NOT NULL,
            dimensions_json TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS compensation_ledger (
            compensation_id TEXT PRIMARY KEY,
            task_id         TEXT NOT NULL,
            step_id         TEXT NOT NULL,
            action_id       TEXT NOT NULL,
            target          TEXT NOT NULL,
            reversible      INTEGER NOT NULL,
            status          TEXT NOT NULL,
            compensation_json TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_metrics_task_name
            ON task_metrics(task_id, name, created_at);
        CREATE INDEX IF NOT EXISTS idx_compensation_task_status
            ON compensation_ledger(task_id, status);
        """,

        // 7 - semantic outcome graph and complete action correlation. New
        // columns are nullable so existing persisted tasks remain readable and
        // are upgraded by the durable runtime before consequential dispatch.
        """
        CREATE TABLE IF NOT EXISTS task_outcomes (
            outcome_id       TEXT PRIMARY KEY,
            task_id          TEXT NOT NULL,
            plan_id          TEXT NOT NULL,
            parent_outcome_id TEXT,
            kind             TEXT NOT NULL,
            required         INTEGER NOT NULL,
            prohibited       INTEGER NOT NULL,
            status           TEXT NOT NULL,
            source_goal_version INTEGER NOT NULL,
            outcome_json     TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
            FOREIGN KEY (plan_id) REFERENCES task_plans(plan_id) ON DELETE CASCADE
        );

        ALTER TABLE task_actions ADD COLUMN outcome_id TEXT;
        ALTER TABLE task_actions ADD COLUMN parent_action_id TEXT;
        ALTER TABLE task_actions ADD COLUMN retry_id TEXT;
        ALTER TABLE task_actions ADD COLUMN observation_version INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE task_actions ADD COLUMN recovery_strategy TEXT;
        ALTER TABLE task_actions ADD COLUMN capability_change_reason TEXT;
        ALTER TABLE task_evidence ADD COLUMN outcome_id TEXT;
        ALTER TABLE task_events ADD COLUMN parent_action_id TEXT;
        ALTER TABLE task_events ADD COLUMN retry_id TEXT;
        ALTER TABLE task_events ADD COLUMN model_request_id TEXT;
        ALTER TABLE task_events ADD COLUMN capture_id TEXT;
        ALTER TABLE task_events ADD COLUMN outcome_id TEXT;

        CREATE INDEX IF NOT EXISTS idx_outcomes_task_status
            ON task_outcomes(task_id, status);
        CREATE INDEX IF NOT EXISTS idx_evidence_task_outcome
            ON task_evidence(task_id, outcome_id);
        """,

        // 8 - Phase 1 security cleanup. Legacy tool payloads were stored before
        // redaction was guaranteed at the persistence boundary. Their content
        // cannot be classified reliably in SQL, so remove it instead of trying
        // to display-time redact unknown plaintext.
        """
        UPDATE tool_executions
        SET arguments = '{"legacy_payload_removed":true}',
            result = NULL,
            error = CASE WHEN error IS NULL THEN NULL ELSE '[legacy error removed]' END;

        UPDATE messages
        SET content = '[legacy tool output removed by security migration]'
        WHERE role = 'tool';

        UPDATE messages
        SET tool_calls = NULL
        WHERE tool_calls IS NOT NULL;

        UPDATE branch_sets
        SET tails_json = '[]';
        """,

        // 9 - unified durable runtime metadata. Existing JSON rows remain
        // readable; additive scalar columns make trigger/parent/recovery state
        // queryable without weakening the Phase 1 redaction boundary.
        """
        ALTER TABLE agent_tasks ADD COLUMN parent_run_id TEXT;
        ALTER TABLE agent_tasks ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'Chat';
        ALTER TABLE agent_tasks ADD COLUMN security_context_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE agent_tasks ADD COLUMN permission_context_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE agent_tasks ADD COLUMN workspace_context_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE agent_tasks ADD COLUMN model_context_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE agent_tasks ADD COLUMN cancellation_requested_at TEXT;
        ALTER TABLE agent_tasks ADD COLUMN failure_summary TEXT;
        ALTER TABLE agent_tasks ADD COLUMN final_result_reference TEXT;

        ALTER TABLE task_steps ADD COLUMN parent_step_id TEXT;
        ALTER TABLE task_steps ADD COLUMN step_type TEXT NOT NULL DEFAULT 'tool';
        ALTER TABLE task_steps ADD COLUMN tool_name TEXT;
        ALTER TABLE task_steps ADD COLUMN arguments_reference TEXT;
        ALTER TABLE task_steps ADD COLUMN idempotency_key TEXT;
        ALTER TABLE task_steps ADD COLUMN started_at TEXT;
        ALTER TABLE task_steps ADD COLUMN completed_at TEXT;
        ALTER TABLE task_steps ADD COLUMN observation_ids_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE task_steps ADD COLUMN verification_disposition TEXT;
        ALTER TABLE task_steps ADD COLUMN tool_outcome TEXT;
        ALTER TABLE task_steps ADD COLUMN failure_classification TEXT;

        ALTER TABLE task_evidence ADD COLUMN trust_level TEXT NOT NULL DEFAULT 'ToolObservation';
        ALTER TABLE task_evidence ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE task_evidence ADD COLUMN allowed_uses_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE task_evidence ADD COLUMN prohibited_uses_json TEXT NOT NULL DEFAULT '[]';

        ALTER TABLE task_events ADD COLUMN sequence_no INTEGER NOT NULL DEFAULT 0;
        UPDATE task_events SET sequence_no = rowid WHERE sequence_no = 0;

        CREATE TABLE IF NOT EXISTS task_verifications (
            verification_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            action_id TEXT,
            disposition TEXT NOT NULL,
            expected_state TEXT NOT NULL,
            actual_state_summary TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL,
            suggested_recovery_action TEXT,
            verification_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_parent ON agent_tasks(parent_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_trigger_status ON agent_tasks(trigger_type, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_task_events_sequence
            ON task_events(task_id, sequence_no);
        CREATE INDEX IF NOT EXISTS idx_verifications_task_step
            ON task_verifications(task_id, step_id, created_at);
        """,
    };

    /// <summary>Newest-N notes kept per conversation; older ones pruned on add.</summary>
    private const int MaxNotesPerConversation = 20;
    /// <summary>Global notes (IConversationStore.GlobalNotesId) get a higher cap:
    /// durable user facts accumulate across every conversation.</summary>
    private const int MaxGlobalNotes = 50;
    private const int MaxNoteChars = 2000;

    private readonly string _connectionString;
    private bool _initialized;
    private readonly SemaphoreSlim _initLock = new(1, 1);

    public SqliteConversationStore() : this(DefaultDbPath())
    {
    }

    public SqliteConversationStore(string dbPath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(dbPath)!);
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            ForeignKeys = true,
            // Off for the same reason as SqliteAgentTaskStore: a pooled
            // connection keeps the file handle after the caller has finished
            // with it, so the database is still locked when nothing is using it.
            Pooling = false
        }.ToString();
    }

    public async Task InitializeAsync(CancellationToken ct = default)
    {
        if (_initialized) return;
        await _initLock.WaitAsync(ct);
        try
        {
            if (_initialized) return;
            await using var conn = await OpenAsync(ct);
            await SqliteMigrator.MigrateAsync(conn, Migrations, logger: null, ct);
            _initialized = true;
        }
        finally
        {
            _initLock.Release();
        }
    }

    // ---------- Conversations ----------

    public async Task<IReadOnlyList<Conversation>> GetConversationsAsync(CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, title, model_id, created_at, updated_at, is_pinned, folder_id, source, last_synced_at, server_id
            FROM conversations
            ORDER BY is_pinned DESC, updated_at DESC
            """;

        var result = new List<Conversation>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            result.Add(ReadConversation(reader));
        return result;
    }

    public async Task<Conversation?> GetConversationAsync(string id, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, title, model_id, created_at, updated_at, is_pinned, folder_id, source, last_synced_at, server_id
            FROM conversations WHERE id = $id
            """;
        cmd.Parameters.AddWithValue("$id", id);

        await using var reader = await cmd.ExecuteReaderAsync(ct);
        return await reader.ReadAsync(ct) ? ReadConversation(reader) : null;
    }

    public async Task UpsertConversationAsync(Conversation c, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        // server_id and source are deliberately NOT in the UPDATE clause, and
        // last_synced_at only overwrites with a non-null value: rename/pin
        // upserts rebuild entities without sync state, and wiping the mapping
        // or timestamp would re-import everything. server_id changes only via
        // SetConversationServerIdAsync.
        cmd.CommandText = """
            INSERT INTO conversations (id, title, model_id, created_at, updated_at, is_pinned, folder_id, source, last_synced_at, server_id)
            VALUES ($id, $title, $model_id, $created_at, $updated_at, $is_pinned, $folder_id, $source, $last_synced_at, $server_id)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                model_id = excluded.model_id,
                updated_at = excluded.updated_at,
                is_pinned = excluded.is_pinned,
                folder_id = excluded.folder_id,
                last_synced_at = COALESCE(excluded.last_synced_at, conversations.last_synced_at)
            """;
        cmd.Parameters.AddWithValue("$id", c.Id);
        cmd.Parameters.AddWithValue("$title",
            c.Title is null ? DBNull.Value : SensitiveDataRedactor.Redact(c.Title));
        cmd.Parameters.AddWithValue("$model_id", (object?)c.ModelId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$created_at", ToIso(c.CreatedAt));
        cmd.Parameters.AddWithValue("$updated_at", ToIso(c.UpdatedAt));
        cmd.Parameters.AddWithValue("$is_pinned", c.IsPinned ? 1 : 0);
        cmd.Parameters.AddWithValue("$folder_id", (object?)c.FolderId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$source", c.Source == ConversationSource.Local ? "local" : "server");
        cmd.Parameters.AddWithValue("$last_synced_at", c.LastSyncedAt is { } ts ? ToIso(ts) : DBNull.Value);
        cmd.Parameters.AddWithValue("$server_id", (object?)c.ServerId ?? DBNull.Value);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task SetConversationServerIdAsync(
        string conversationId, string serverId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            UPDATE conversations
            SET server_id = $server_id, last_synced_at = $now
            WHERE id = $id
            """;
        cmd.Parameters.AddWithValue("$id", conversationId);
        cmd.Parameters.AddWithValue("$server_id", serverId);
        cmd.Parameters.AddWithValue("$now", ToIso(DateTimeOffset.UtcNow));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task DeleteConversationAsync(string id, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        // branch_sets + agent_notes have no FK (see schema) — cascade manually.
        cmd.CommandText = """
            DELETE FROM branch_sets WHERE conversation_id = $id;
            DELETE FROM agent_notes WHERE conversation_id = $id;
            DELETE FROM conversations WHERE id = $id;
            """;
        cmd.Parameters.AddWithValue("$id", id);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    // ---------- Messages ----------

    public async Task<IReadOnlyList<Message>> GetMessagesAsync(string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, conversation_id, role, content, tool_calls, tool_call_id, name, model_id, token_usage, created_at, sort_order, server_id, file_ids
            FROM messages WHERE conversation_id = $cid
            ORDER BY sort_order
            """;
        cmd.Parameters.AddWithValue("$cid", conversationId);

        var result = new List<Message>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            result.Add(ReadMessage(reader));
        return result;
    }

    public async Task<IReadOnlyList<Message>> GetUnsyncedMessagesAsync(
        string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, conversation_id, role, content, tool_calls, tool_call_id, name, model_id, token_usage, created_at, sort_order, server_id, file_ids
            FROM messages WHERE conversation_id = $cid AND server_id IS NULL
            ORDER BY created_at, sort_order
            """;
        cmd.Parameters.AddWithValue("$cid", conversationId);

        var result = new List<Message>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
            result.Add(ReadMessage(reader));
        return result;
    }

    public async Task MarkMessagesSyncedAsync(
        IReadOnlyList<string> messageIds, string serverConversationId, CancellationToken ct = default)
    {
        if (messageIds.Count == 0) return;
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "UPDATE messages SET server_id = $sid WHERE id = $id";
        var sidParam = cmd.Parameters.Add("$sid", SqliteType.Text);
        var idParam = cmd.Parameters.Add("$id", SqliteType.Text);
        sidParam.Value = serverConversationId;
        foreach (var id in messageIds)
        {
            idParam.Value = id;
            await cmd.ExecuteNonQueryAsync(ct);
        }
        await tx.CommitAsync(ct);
    }

    public async Task UpsertMessageAsync(Message m, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        BindMessageUpsert(cmd, m);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    /// <summary>All rows land in ONE transaction — a crash mid-turn can never
    /// persist the user message without its assistant reply (Phase 3 C3).</summary>
    public async Task UpsertMessagesAsync(IReadOnlyList<Message> messages, CancellationToken ct = default)
    {
        if (messages.Count == 0) return;
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
        foreach (var m in messages)
        {
            await using var cmd = conn.CreateCommand();
            cmd.Transaction = tx;
            BindMessageUpsert(cmd, m);
            await cmd.ExecuteNonQueryAsync(ct);
        }
        await tx.CommitAsync(ct);
    }

    // server_id is NOT in the UPDATE clause — the synced marker survives
    // content re-upserts and changes only via MarkMessagesSyncedAsync.
    private static void BindMessageUpsert(SqliteCommand cmd, Message m)
    {
        cmd.CommandText = """
            INSERT INTO messages (id, conversation_id, role, content, tool_calls, tool_call_id, name, model_id, token_usage, created_at, sort_order, server_id, file_ids)
            VALUES ($id, $cid, $role, $content, $tool_calls, $tool_call_id, $name, $model_id, $token_usage, $created_at, $sort_order, $server_id, $file_ids)
            ON CONFLICT(id) DO UPDATE SET
                content = excluded.content,
                tool_calls = excluded.tool_calls,
                model_id = excluded.model_id,
                token_usage = excluded.token_usage,
                file_ids = COALESCE(excluded.file_ids, messages.file_ids)
            """;
        cmd.Parameters.AddWithValue("$id", m.Id);
        cmd.Parameters.AddWithValue("$cid", m.ConversationId);
        cmd.Parameters.AddWithValue("$role", RoleToString(m.Role));
        cmd.Parameters.AddWithValue("$content",
            m.Content is null
                ? DBNull.Value
                : m.Role == MessageRole.Tool && m.Name != "update_plan"
                    ? SensitiveDataRedactor.SummarizeTextForPersistence(
                        "tool output",
                        m.Content)
                    : SensitiveDataRedactor.Redact(m.Content));
        cmd.Parameters.AddWithValue("$tool_calls",
            m.ToolCallsJson is null
                ? DBNull.Value
                : AgentChainCodec.SanitizeToolCallsJsonForPersistence(
                    m.ToolCallsJson));
        cmd.Parameters.AddWithValue("$tool_call_id", (object?)m.ToolCallId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$name", (object?)m.Name ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$model_id", (object?)m.ModelId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$token_usage",
            m.TokenUsage is { } u ? JsonSerializer.Serialize(u) : DBNull.Value);
        cmd.Parameters.AddWithValue("$created_at", ToIso(m.CreatedAt));
        cmd.Parameters.AddWithValue("$sort_order", m.SortOrder);
        cmd.Parameters.AddWithValue("$server_id", (object?)m.ServerId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$file_ids", (object?)m.FileIdsJson ?? DBNull.Value);
    }

    public async Task<int> GetNextSortOrderAsync(string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM messages WHERE conversation_id = $cid";
        cmd.Parameters.AddWithValue("$cid", conversationId);
        var value = await cmd.ExecuteScalarAsync(ct);
        return Convert.ToInt32(value, CultureInfo.InvariantCulture);
    }

    // ---------- Files ----------

    public async Task<IReadOnlyList<FileAttachment>> GetFilesAsync(string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, conversation_id, name, mime_type, size_bytes, local_path, server_url, created_at
            FROM files WHERE conversation_id = $cid
            ORDER BY created_at
            """;
        cmd.Parameters.AddWithValue("$cid", conversationId);

        var result = new List<FileAttachment>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(new FileAttachment
            {
                Id = reader.GetString(0),
                ConversationId = reader.GetString(1),
                Name = reader.GetString(2),
                MimeType = reader.IsDBNull(3) ? null : reader.GetString(3),
                SizeBytes = reader.IsDBNull(4) ? 0 : reader.GetInt64(4),
                LocalPath = reader.IsDBNull(5) ? null : reader.GetString(5),
                ServerUrl = reader.IsDBNull(6) ? null : reader.GetString(6),
                CreatedAt = FromIso(reader.GetString(7))
            });
        }
        return result;
    }

    public async Task UpsertFileAsync(FileAttachment f, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO files (id, conversation_id, name, mime_type, size_bytes, local_path, server_url, created_at)
            VALUES ($id, $cid, $name, $mime, $size, $local, $server, $created)
            ON CONFLICT(id) DO UPDATE SET
                local_path = excluded.local_path,
                server_url = excluded.server_url
            """;
        cmd.Parameters.AddWithValue("$id", f.Id);
        cmd.Parameters.AddWithValue("$cid", f.ConversationId);
        cmd.Parameters.AddWithValue("$name", f.Name);
        cmd.Parameters.AddWithValue("$mime", (object?)f.MimeType ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$size", f.SizeBytes);
        cmd.Parameters.AddWithValue("$local", (object?)f.LocalPath ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$server", (object?)f.ServerUrl ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$created", ToIso(f.CreatedAt));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    // ---------- Tool executions ----------

    public async Task InsertToolExecutionAsync(ToolExecution e, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO tool_executions (id, conversation_id, message_id, tool_name, arguments, result, permission, duration_ms, error, executed_at)
            VALUES ($id, $cid, $mid, $tool, $args, $result, $perm, $dur, $err, $at)
            """;
        cmd.Parameters.AddWithValue("$id", e.Id);
        cmd.Parameters.AddWithValue("$cid", e.ConversationId);
        cmd.Parameters.AddWithValue("$mid", (object?)e.MessageId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$tool", e.ToolName);
        cmd.Parameters.AddWithValue(
            "$args",
            e.ToolName == "update_plan"
                ? SensitiveDataRedactor.RedactJson(e.ArgumentsJson)
                : SensitiveDataRedactor.SummarizeJsonForPersistence(e.ArgumentsJson));
        cmd.Parameters.AddWithValue("$result",
            e.Result is null
                ? DBNull.Value
                : e.ToolName == "update_plan"
                    ? SensitiveDataRedactor.Redact(e.Result)
                    : SensitiveDataRedactor.SummarizeTextForPersistence(
                        "tool result",
                        e.Result));
        cmd.Parameters.AddWithValue("$perm", e.Permission);
        cmd.Parameters.AddWithValue("$dur", (object?)e.DurationMs ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$err",
            e.Error is null
                ? DBNull.Value
                : SensitiveDataRedactor.SummarizeTextForPersistence(
                    "tool error",
                    e.Error));
        cmd.Parameters.AddWithValue("$at", ToIso(e.ExecutedAt));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<ToolExecution>> GetToolExecutionsAsync(string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, conversation_id, message_id, tool_name, arguments, result, permission, duration_ms, error, executed_at
            FROM tool_executions WHERE conversation_id = $cid
            ORDER BY executed_at
            """;
        cmd.Parameters.AddWithValue("$cid", conversationId);

        var result = new List<ToolExecution>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(new ToolExecution
            {
                Id = reader.GetString(0),
                ConversationId = reader.GetString(1),
                MessageId = reader.IsDBNull(2) ? null : reader.GetString(2),
                ToolName = reader.GetString(3),
                ArgumentsJson = reader.GetString(4),
                Result = reader.IsDBNull(5) ? null : reader.GetString(5),
                Permission = reader.GetString(6),
                DurationMs = reader.IsDBNull(7) ? null : reader.GetInt64(7),
                Error = reader.IsDBNull(8) ? null : reader.GetString(8),
                ExecutedAt = FromIso(reader.GetString(9))
            });
        }
        return result;
    }

    // ---------- Tool permissions ----------

    public async Task<string?> GetToolPermissionAsync(string toolName, string scope, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT permission FROM tool_permissions WHERE tool_name = $tool AND scope = $scope";
        cmd.Parameters.AddWithValue("$tool", toolName);
        cmd.Parameters.AddWithValue("$scope", scope);
        return await cmd.ExecuteScalarAsync(ct) as string;
    }

    public async Task SetToolPermissionAsync(string toolName, string scope, string permission, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO tool_permissions (tool_name, scope, permission, updated_at)
            VALUES ($tool, $scope, $perm, $at)
            ON CONFLICT(tool_name, scope) DO UPDATE SET
                permission = excluded.permission,
                updated_at = excluded.updated_at
            """;
        cmd.Parameters.AddWithValue("$tool", toolName);
        cmd.Parameters.AddWithValue("$scope", scope);
        cmd.Parameters.AddWithValue("$perm", permission);
        cmd.Parameters.AddWithValue("$at", ToIso(DateTimeOffset.UtcNow));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    // ---------- Drafts (Phase 3 C4) ----------

    public async Task SaveDraftAsync(string conversationId, string text, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        if (string.IsNullOrEmpty(text))
        {
            cmd.CommandText = "DELETE FROM drafts WHERE conversation_id = $cid";
            cmd.Parameters.AddWithValue("$cid", conversationId);
        }
        else
        {
            cmd.CommandText = """
                INSERT INTO drafts (conversation_id, text, updated_at)
                VALUES ($cid, $text, $at)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    text = excluded.text,
                    updated_at = excluded.updated_at
                """;
            cmd.Parameters.AddWithValue("$cid", conversationId);
            cmd.Parameters.AddWithValue("$text", SensitiveDataRedactor.Redact(text));
            cmd.Parameters.AddWithValue("$at", ToIso(DateTimeOffset.UtcNow));
        }
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<string?> GetDraftAsync(string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT text FROM drafts WHERE conversation_id = $cid";
        cmd.Parameters.AddWithValue("$cid", conversationId);
        return await cmd.ExecuteScalarAsync(ct) as string;
    }

    // ---------- helpers ----------

    // ---------- Branch sets (R10 Fix 3) ----------

    public async Task SaveBranchSetAsync(Application.Models.BranchSetRecord record, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO branch_sets (branch_key, conversation_id, active_index, tails_json, updated_at)
            VALUES ($key, $convo, $active, $tails, $updated)
            ON CONFLICT(branch_key) DO UPDATE SET
                active_index = $active, tails_json = $tails, updated_at = $updated
            """;
        cmd.Parameters.AddWithValue("$key", record.BranchKey);
        cmd.Parameters.AddWithValue("$convo", record.ConversationId);
        cmd.Parameters.AddWithValue("$active", record.ActiveIndex);
        cmd.Parameters.AddWithValue("$tails",
            SensitiveDataRedactor.RedactJson(JsonSerializer.Serialize(record.Tails)));
        cmd.Parameters.AddWithValue("$updated", DateTimeOffset.UtcNow.ToString("O"));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<Application.Models.BranchSetRecord>> GetBranchSetsAsync(
        string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT branch_key, active_index, tails_json
            FROM branch_sets WHERE conversation_id = $convo
            ORDER BY updated_at
            """;
        cmd.Parameters.AddWithValue("$convo", conversationId);

        var result = new List<Application.Models.BranchSetRecord>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            List<List<string>>? tails = null;
            try
            {
                tails = JsonSerializer.Deserialize<List<List<string>>>(reader.GetString(2));
            }
            catch (JsonException)
            {
                // Corrupt row — skip; the conversation falls back to flat display.
            }
            if (tails is { Count: > 0 })
                result.Add(new Application.Models.BranchSetRecord(
                    conversationId, reader.GetString(0), (int)reader.GetInt64(1), tails));
        }
        return result;
    }

    // ---------- Agent memory notes (Phase B PART 3) ----------

    public async Task AddNoteAsync(string conversationId, string content, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        var trimmed = content.Length > MaxNoteChars ? content[..MaxNoteChars] : content;
        await using var conn = await OpenAsync(ct);
        await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);

        await using (var insert = conn.CreateCommand())
        {
            insert.Transaction = tx;
            insert.CommandText = """
                INSERT INTO agent_notes (id, conversation_id, content, created_at)
                VALUES ($id, $cid, $content, $at)
                """;
            insert.Parameters.AddWithValue("$id", Guid.NewGuid().ToString("N"));
            insert.Parameters.AddWithValue("$cid", conversationId);
            insert.Parameters.AddWithValue("$content", SensitiveDataRedactor.Redact(trimmed));
            insert.Parameters.AddWithValue("$at", ToIso(DateTimeOffset.UtcNow));
            await insert.ExecuteNonQueryAsync(ct);
        }

        // Prune to the newest N so the block can never balloon the system prompt.
        await using (var prune = conn.CreateCommand())
        {
            prune.Transaction = tx;
            prune.CommandText = """
                DELETE FROM agent_notes
                WHERE conversation_id = $cid AND id NOT IN (
                    SELECT id FROM agent_notes WHERE conversation_id = $cid
                    ORDER BY created_at DESC, id DESC LIMIT $keep
                )
                """;
            prune.Parameters.AddWithValue("$cid", conversationId);
            prune.Parameters.AddWithValue("$keep",
                conversationId == IConversationStore.GlobalNotesId ? MaxGlobalNotes : MaxNotesPerConversation);
            await prune.ExecuteNonQueryAsync(ct);
        }

        await tx.CommitAsync(ct);
    }

    public async Task<IReadOnlyList<AgentNote>> GetNotesAsync(string conversationId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, conversation_id, content, created_at
            FROM agent_notes WHERE conversation_id = $cid
            ORDER BY created_at, id
            """;
        cmd.Parameters.AddWithValue("$cid", conversationId);

        var result = new List<AgentNote>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(new AgentNote
            {
                Id = reader.GetString(0),
                ConversationId = reader.GetString(1),
                Content = reader.GetString(2),
                CreatedAt = FromIso(reader.GetString(3))
            });
        }
        return result;
    }

    /// <summary>Global notes live under the reserved GlobalNotesId row space —
    /// no schema change; existing conversation-scoped rows are untouched.</summary>
    public Task<IReadOnlyList<AgentNote>> GetGlobalNotesAsync(CancellationToken ct = default) =>
        GetNotesAsync(IConversationStore.GlobalNotesId, ct);

    public async Task DeleteNoteAsync(string noteId, CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM agent_notes WHERE id = $id";
        cmd.Parameters.AddWithValue("$id", noteId);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private async Task<SqliteConnection> OpenAsync(CancellationToken ct)
    {
        var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync(ct);
        return conn;
    }

    private static Conversation ReadConversation(SqliteDataReader reader) => new()
    {
        Id = reader.GetString(0),
        Title = reader.IsDBNull(1) ? null : reader.GetString(1),
        ModelId = reader.IsDBNull(2) ? null : reader.GetString(2),
        CreatedAt = FromIso(reader.GetString(3)),
        UpdatedAt = FromIso(reader.GetString(4)),
        IsPinned = !reader.IsDBNull(5) && reader.GetInt32(5) != 0,
        FolderId = reader.IsDBNull(6) ? null : reader.GetString(6),
        Source = reader.GetString(7) == "local" ? ConversationSource.Local : ConversationSource.Server,
        LastSyncedAt = reader.IsDBNull(8) ? null : FromIso(reader.GetString(8)),
        ServerId = reader.IsDBNull(9) ? null : reader.GetString(9)
    };

    private static Message ReadMessage(SqliteDataReader reader) => new()
    {
        Id = reader.GetString(0),
        ConversationId = reader.GetString(1),
        Role = RoleFromString(reader.GetString(2)),
        Content = reader.IsDBNull(3) ? null : reader.GetString(3),
        ToolCallsJson = reader.IsDBNull(4) ? null : reader.GetString(4),
        ToolCallId = reader.IsDBNull(5) ? null : reader.GetString(5),
        Name = reader.IsDBNull(6) ? null : reader.GetString(6),
        ModelId = reader.IsDBNull(7) ? null : reader.GetString(7),
        TokenUsage = reader.IsDBNull(8)
            ? null
            : JsonSerializer.Deserialize<TokenUsage>(reader.GetString(8)),
        CreatedAt = FromIso(reader.GetString(9)),
        SortOrder = reader.GetInt32(10),
        ServerId = reader.IsDBNull(11) ? null : reader.GetString(11),
        FileIdsJson = reader.IsDBNull(12) ? null : reader.GetString(12)
    };

    private static string RoleToString(MessageRole role) => role switch
    {
        MessageRole.System => "system",
        MessageRole.User => "user",
        MessageRole.Assistant => "assistant",
        MessageRole.Tool => "tool",
        _ => throw new ArgumentOutOfRangeException(nameof(role))
    };

    private static MessageRole RoleFromString(string role) => role switch
    {
        "system" => MessageRole.System,
        "user" => MessageRole.User,
        "assistant" => MessageRole.Assistant,
        "tool" => MessageRole.Tool,
        _ => throw new InvalidDataException($"Unknown message role '{role}'")
    };

    private static string ToIso(DateTimeOffset value) =>
        value.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture);

    private static DateTimeOffset FromIso(string value) =>
        DateTimeOffset.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);

    private static string DefaultDbPath()
    {
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        return Path.Combine(appData, "SRAN Desktop", "data.db");
    }
}
