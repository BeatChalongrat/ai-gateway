using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;
using SranDesktop.Application.Interfaces;
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

        // 5 — Phase 9: folders were merged into projects. Mirrors chat-edge
        // migration 031 on the local mirror: add project_id and carry every
        // existing local grouping across, so a chat the user filed offline
        // keeps its section instead of falling back to the time buckets.
        // folder_id is deliberately KEPT (not dropped, not cleared) for one
        // release so this is reversible — SQLite cannot drop a column
        // without a table rebuild anyway.
        """
        ALTER TABLE conversations ADD COLUMN project_id TEXT;
        UPDATE conversations SET project_id = folder_id WHERE folder_id IS NOT NULL;
        """,

        // 6 — Phase 14: archive. Mirrors chat-edge migration 032 on the local
        // mirror. Deliberately its OWN flag rather than reusing a delete
        // marker: archiving hides a chat, deleting destroys it, and a user who
        // archives must still be able to open and read the conversation. The
        // constant DEFAULT 0 is what makes this legal as a plain ADD COLUMN
        // with NOT NULL in SQLite.
        """
        ALTER TABLE conversations ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;
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
            ForeignKeys = true
        }.ToString();
    }

    /// <summary>
    /// Columns the READ PATH depends on, with the DDL that adds them.
    /// Verified on every init and added when missing.
    ///
    /// Why this exists on top of the migration list: PRAGMA user_version is
    /// a single counter shared by whatever build last touched this file, and
    /// migration index N in one build is not migration N in another. A user
    /// who ran a build from an abandoned branch (which had reached
    /// user_version 10) and then moved to a build whose list has 6 entries
    /// leaves SqliteMigrator with nothing to do — <c>for (i = 10; i &lt; 6)</c>
    /// never runs — so every later migration is skipped SILENTLY and the
    /// first SELECT of a new column dies with "no such column". That is
    /// exactly how the Phase-9 project_id column went missing.
    ///
    /// So the counter stays as the fast path, and this is the safety net:
    /// idempotent, cheap (one PRAGMA per process), and correct whether the
    /// database is behind the code, ahead of it, or from a different lineage.
    /// SQLite has no ADD COLUMN IF NOT EXISTS, hence the table_info probe.
    /// </summary>
    private static readonly (string Table, string Column, string Ddl)[] RequiredColumns =
    {
        ("conversations", "project_id",
            "ALTER TABLE conversations ADD COLUMN project_id TEXT;"),
        // Phase 14 — the read path selects is_archived to filter the sidebar,
        // so it goes here as well as in the migration list (the rule this
        // safety net exists for).
        ("conversations", "is_archived",
            "ALTER TABLE conversations ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;"),
    };

    public async Task InitializeAsync(CancellationToken ct = default)
    {
        if (_initialized) return;
        await _initLock.WaitAsync(ct);
        try
        {
            if (_initialized) return;
            await using var conn = await OpenAsync(ct);
            await SqliteMigrator.MigrateAsync(conn, Migrations, logger: null, ct);
            await EnsureRequiredColumnsAsync(conn, ct);
            _initialized = true;
        }
        finally
        {
            _initLock.Release();
        }
    }

    /// <summary>Adds any RequiredColumns the database is missing. Carries the
    /// same data-preserving intent as migration 5: a column that has to be
    /// created here is back-filled from its legacy source so the user's
    /// existing grouping is not lost.</summary>
    private static async Task EnsureRequiredColumnsAsync(
        SqliteConnection conn, CancellationToken ct)
    {
        foreach (var (table, column, ddl) in RequiredColumns)
        {
            if (await ColumnExistsAsync(conn, table, column, ct))
                continue;

            await using var add = conn.CreateCommand();
            add.CommandText = ddl;
            // Repeat migration 5's back-fill: without it a repaired database
            // would come back with every chat un-grouped.
            if (table == "conversations" && column == "project_id"
                && await ColumnExistsAsync(conn, "conversations", "folder_id", ct))
            {
                add.CommandText +=
                    " UPDATE conversations SET project_id = folder_id WHERE folder_id IS NOT NULL;";
            }
            await add.ExecuteNonQueryAsync(ct);
        }
    }

    private static async Task<bool> ColumnExistsAsync(
        SqliteConnection conn, string table, string column, CancellationToken ct)
    {
        await using var cmd = conn.CreateCommand();
        // table_info() takes an identifier, not a bindable value; the names
        // come from the constant above, never from user input.
        cmd.CommandText = $"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name = $c;";
        cmd.Parameters.AddWithValue("$c", column);
        var n = Convert.ToInt64(await cmd.ExecuteScalarAsync(ct), CultureInfo.InvariantCulture);
        return n > 0;
    }

    // ---------- Conversations ----------

    public async Task<IReadOnlyList<Conversation>> GetConversationsAsync(CancellationToken ct = default)
    {
        await InitializeAsync(ct);
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, title, model_id, created_at, updated_at, is_pinned, is_archived, project_id, source, last_synced_at, server_id
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
            SELECT id, title, model_id, created_at, updated_at, is_pinned, is_archived, project_id, source, last_synced_at, server_id
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
            INSERT INTO conversations (id, title, model_id, created_at, updated_at, is_pinned, is_archived, project_id, source, last_synced_at, server_id)
            VALUES ($id, $title, $model_id, $created_at, $updated_at, $is_pinned, $is_archived, $project_id, $source, $last_synced_at, $server_id)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                model_id = excluded.model_id,
                updated_at = excluded.updated_at,
                is_pinned = excluded.is_pinned,
                is_archived = excluded.is_archived,
                project_id = excluded.project_id,
                last_synced_at = COALESCE(excluded.last_synced_at, conversations.last_synced_at)
            """;
        cmd.Parameters.AddWithValue("$id", c.Id);
        cmd.Parameters.AddWithValue("$title", (object?)c.Title ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$model_id", (object?)c.ModelId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$created_at", ToIso(c.CreatedAt));
        cmd.Parameters.AddWithValue("$updated_at", ToIso(c.UpdatedAt));
        cmd.Parameters.AddWithValue("$is_pinned", c.IsPinned ? 1 : 0);
        cmd.Parameters.AddWithValue("$is_archived", c.IsArchived ? 1 : 0);
        cmd.Parameters.AddWithValue("$project_id", (object?)c.ProjectId ?? DBNull.Value);
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
        cmd.Parameters.AddWithValue("$content", (object?)m.Content ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$tool_calls", (object?)m.ToolCallsJson ?? DBNull.Value);
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
        cmd.Parameters.AddWithValue("$args", e.ArgumentsJson);
        cmd.Parameters.AddWithValue("$result", (object?)e.Result ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$perm", e.Permission);
        cmd.Parameters.AddWithValue("$dur", (object?)e.DurationMs ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$err", (object?)e.Error ?? DBNull.Value);
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
            cmd.Parameters.AddWithValue("$text", text);
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
        cmd.Parameters.AddWithValue("$tails", JsonSerializer.Serialize(record.Tails));
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
            insert.Parameters.AddWithValue("$content", trimmed);
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
        IsArchived = !reader.IsDBNull(6) && reader.GetInt32(6) != 0,
        ProjectId = reader.IsDBNull(7) ? null : reader.GetString(7),
        Source = reader.GetString(8) == "local" ? ConversationSource.Local : ConversationSource.Server,
        LastSyncedAt = reader.IsDBNull(9) ? null : FromIso(reader.GetString(9)),
        ServerId = reader.IsDBNull(10) ? null : reader.GetString(10)
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
