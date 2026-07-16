using Microsoft.Data.Sqlite;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

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

    internal static readonly string[] Migrations = { Schema };

    private readonly string _connectionString;
    private bool _initialized;
    private readonly SemaphoreSlim _initLock = new(1, 1);

    public SqliteScheduledTaskStore() : this(DefaultDbPath())
    {
    }

    public SqliteScheduledTaskStore(string dbPath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(dbPath)!);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = dbPath }.ToString();
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
                   next_run_at, last_run_at, last_result, created_at
            FROM scheduled_tasks ORDER BY created_at
            """;

        var result = new List<ScheduledTask>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(new ScheduledTask
            {
                Id = reader.GetString(0),
                Name = reader.GetString(1),
                Prompt = reader.GetString(2),
                ConversationId = reader.IsDBNull(3) ? null : reader.GetString(3),
                CronExpression = reader.GetString(4),
                IsEnabled = reader.GetInt64(5) != 0,
                NextRunAt = ReadDate(reader, 6),
                LastRunAt = ReadDate(reader, 7),
                LastResult = reader.IsDBNull(8) ? null : reader.GetString(8),
                CreatedAt = ReadDate(reader, 9) ?? DateTimeOffset.UtcNow
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
                 next_run_at, last_run_at, last_result, created_at)
            VALUES ($id, $name, $prompt, $conv, $cron, $enabled, $next, $last, $result, $created)
            ON CONFLICT(id) DO UPDATE SET
                name = $name, prompt = $prompt, conversation_id = $conv,
                cron_expression = $cron, is_enabled = $enabled,
                next_run_at = $next, last_run_at = $last, last_result = $result
            """;
        cmd.Parameters.AddWithValue("$id", task.Id);
        cmd.Parameters.AddWithValue("$name", task.Name);
        cmd.Parameters.AddWithValue("$prompt", task.Prompt);
        cmd.Parameters.AddWithValue("$conv", (object?)task.ConversationId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$cron", task.CronExpression);
        cmd.Parameters.AddWithValue("$enabled", task.IsEnabled ? 1 : 0);
        cmd.Parameters.AddWithValue("$next", (object?)task.NextRunAt?.ToString("O") ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$last", (object?)task.LastRunAt?.ToString("O") ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$result", (object?)task.LastResult ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$created", task.CreatedAt.ToString("O"));
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
}
