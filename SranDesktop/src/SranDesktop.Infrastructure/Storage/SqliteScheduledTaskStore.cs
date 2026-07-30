using Microsoft.Data.Sqlite;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Services;

namespace SranDesktop.Infrastructure.Storage;

/// <summary>Scheduled tasks live in their own SQLite file (tasks.db) so the
/// scheduler can't contend with the conversation store (R9 Feature G).</summary>
public sealed class SqliteScheduledTaskStore : IScheduledTaskStore
{
    // Migration 0 = baseline (CREATE TABLE IF NOT EXISTS — legacy DBs replay
    // it as a no-op; see SqliteMigrator). tasks.db versions independently of
    // data.db: same runner, per-database migration list.
    private const string Schema = """
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            prompt          TEXT NOT NULL,
            conversation_id TEXT,
            cron_expression TEXT NOT NULL,
            is_enabled      INTEGER NOT NULL DEFAULT 1,
            next_run_at     TEXT,
            last_run_at     TEXT,
            last_result     TEXT,
            created_at      TEXT NOT NULL
        );
        """;

    internal static readonly string[] Migrations =
    {
        Schema,
        "ALTER TABLE scheduled_tasks ADD COLUMN last_run_id TEXT;"
    };

    private readonly string _connectionString;
    private bool _initialized;
    private readonly SemaphoreSlim _initLock = new(1, 1);

    public SqliteScheduledTaskStore() : this(DefaultDbPath())
    {
    }

    public SqliteScheduledTaskStore(string dbPath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(dbPath)!);
        // Pooling off — see SqliteAgentTaskStore: a pooled connection outlives
        // the operation that opened it and keeps the file locked.
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            Pooling = false
        }.ToString();
    }

    private static string DefaultDbPath() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop", "tasks.db");

    private async Task<SqliteConnection> OpenAsync(CancellationToken ct)
    {
        var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync(ct);
        if (!_initialized)
        {
            await _initLock.WaitAsync(ct);
            try
            {
                if (!_initialized)
                {
                    await SqliteMigrator.MigrateAsync(conn, Migrations, logger: null, ct);
                    await UpgradeLegacyPayloadsAsync(conn, ct);
                    _initialized = true;
                }
            }
            finally
            {
                _initLock.Release();
            }
        }
        return conn;
    }

    public async Task<IReadOnlyList<ScheduledTask>> GetAllAsync(CancellationToken ct = default)
    {
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, name, prompt, conversation_id, cron_expression, is_enabled,
                   next_run_at, last_run_at, last_result, created_at,last_run_id
            FROM scheduled_tasks ORDER BY created_at
            """;

        var result = new List<ScheduledTask>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(new ScheduledTask
            {
                Id = reader.GetString(0),
                Name = ProtectedPayloadCodec.Unprotect(reader.GetString(1)),
                Prompt = ProtectedPayloadCodec.Unprotect(reader.GetString(2)),
                ConversationId = reader.IsDBNull(3) ? null : reader.GetString(3),
                CronExpression = reader.GetString(4),
                IsEnabled = reader.GetInt64(5) != 0,
                NextRunAt = ReadDate(reader, 6),
                LastRunAt = ReadDate(reader, 7),
                LastResult = reader.IsDBNull(8)
                    ? null
                    : ProtectedPayloadCodec.Unprotect(reader.GetString(8)),
                CreatedAt = ReadDate(reader, 9) ?? DateTimeOffset.UtcNow,
                LastRunId = reader.IsDBNull(10) ? null : reader.GetString(10)
            });
        }
        return result;
    }

    public async Task UpsertAsync(ScheduledTask task, CancellationToken ct = default)
    {
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO scheduled_tasks
                (id, name, prompt, conversation_id, cron_expression, is_enabled,
                 next_run_at, last_run_at, last_result, created_at,last_run_id)
            VALUES ($id, $name, $prompt, $conv, $cron, $enabled, $next, $last, $result, $created,$last_run_id)
            ON CONFLICT(id) DO UPDATE SET
                name = $name, prompt = $prompt, conversation_id = $conv,
                cron_expression = $cron, is_enabled = $enabled,
                next_run_at = $next, last_run_at = $last, last_result = $result,
                last_run_id = $last_run_id
            """;
        cmd.Parameters.AddWithValue("$id", task.Id);
        cmd.Parameters.AddWithValue("$name", ProtectedPayloadCodec.Protect(task.Name));
        cmd.Parameters.AddWithValue("$prompt", ProtectedPayloadCodec.Protect(task.Prompt));
        cmd.Parameters.AddWithValue("$conv", (object?)task.ConversationId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$cron", task.CronExpression);
        cmd.Parameters.AddWithValue("$enabled", task.IsEnabled ? 1 : 0);
        cmd.Parameters.AddWithValue("$next", (object?)task.NextRunAt?.ToString("O") ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$last", (object?)task.LastRunAt?.ToString("O") ?? DBNull.Value);
        cmd.Parameters.AddWithValue(
            "$result",
            task.LastResult is null
                ? DBNull.Value
                : ProtectedPayloadCodec.Protect(task.LastResult));
        cmd.Parameters.AddWithValue("$created", task.CreatedAt.ToString("O"));
        cmd.Parameters.AddWithValue("$last_run_id", (object?)task.LastRunId ?? DBNull.Value);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task DeleteAsync(string id, CancellationToken ct = default)
    {
        await using var conn = await OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM scheduled_tasks WHERE id = $id";
        cmd.Parameters.AddWithValue("$id", id);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static DateTimeOffset? ReadDate(SqliteDataReader reader, int ordinal) =>
        reader.IsDBNull(ordinal) ? null : DateTimeOffset.Parse(reader.GetString(ordinal));

    private static async Task UpgradeLegacyPayloadsAsync(
        SqliteConnection connection,
        CancellationToken ct)
    {
        var rows = new List<(string Id, string Name, string Prompt, string? Result)>();
        await using (var read = connection.CreateCommand())
        {
            read.CommandText = "SELECT id,name,prompt,last_result FROM scheduled_tasks";
            await using var reader = await read.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                rows.Add((
                    reader.GetString(0),
                    reader.GetString(1),
                    reader.GetString(2),
                    reader.IsDBNull(3) ? null : reader.GetString(3)));
            }
        }

        await using var tx = (SqliteTransaction)await connection.BeginTransactionAsync(ct);
        foreach (var row in rows.Where(row =>
                     !ProtectedPayloadCodec.IsProtected(row.Name)
                     || !ProtectedPayloadCodec.IsProtected(row.Prompt)
                     || row.Result is { } result
                     && !ProtectedPayloadCodec.IsProtected(result)))
        {
            await using var update = connection.CreateCommand();
            update.Transaction = tx;
            update.CommandText = """
                UPDATE scheduled_tasks
                SET name=$name,prompt=$prompt,last_result=$result
                WHERE id=$id
                """;
            update.Parameters.AddWithValue("$id", row.Id);
            update.Parameters.AddWithValue("$name", ProtectedPayloadCodec.Protect(row.Name));
            update.Parameters.AddWithValue("$prompt", ProtectedPayloadCodec.Protect(row.Prompt));
            update.Parameters.AddWithValue(
                "$result",
                row.Result is null
                    ? DBNull.Value
                    : ProtectedPayloadCodec.Protect(row.Result));
            await update.ExecuteNonQueryAsync(ct);
        }
        await tx.CommitAsync(ct);
    }
}
