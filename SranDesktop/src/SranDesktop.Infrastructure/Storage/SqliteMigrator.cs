using System.Globalization;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging;

namespace SranDesktop.Infrastructure.Storage;

/// <summary>
/// Versioned SQLite migrations driven by <c>PRAGMA user_version</c>.
///
/// Each store owns a static migration list where index 0 is the BASELINE —
/// the full original schema, written with CREATE TABLE IF NOT EXISTS.
/// Bootstrap rule: a legacy database (tables exist but user_version is
/// still 0) simply re-runs the baseline as a no-op, then applies the real
/// migrations; from that point on user_version is authoritative. A fresh
/// database runs 0..N the same way.
/// </summary>
public static class SqliteMigrator
{
    /// <summary>Applies every migration with index ≥ the database's current
    /// user_version, each inside its own transaction together with the
    /// version bump — a failed step rolls back completely and leaves the
    /// version untouched.</summary>
    public static async Task MigrateAsync(
        SqliteConnection conn,
        IReadOnlyList<string> migrations,
        ILogger? logger = null,
        CancellationToken ct = default)
    {
        int version;
        await using (var read = conn.CreateCommand())
        {
            read.CommandText = "PRAGMA user_version;";
            version = Convert.ToInt32(await read.ExecuteScalarAsync(ct), CultureInfo.InvariantCulture);
        }

        for (var i = version; i < migrations.Count; i++)
        {
            await using var tx = (SqliteTransaction)await conn.BeginTransactionAsync(ct);
            await using var cmd = conn.CreateCommand();
            cmd.Transaction = tx;
            // PRAGMA user_version cannot be parameterized — the value is the
            // trusted migration-list index, never user input.
            cmd.CommandText = migrations[i]
                + $"\nPRAGMA user_version = {i + 1};";
            await cmd.ExecuteNonQueryAsync(ct);
            await tx.CommitAsync(ct);
            logger?.LogInformation("Applied SQLite migration {Version}/{Total}", i + 1, migrations.Count);
        }
    }
}
