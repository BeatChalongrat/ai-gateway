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

        // A database AHEAD of this build's list was written by a different
        // lineage (e.g. a build from an abandoned branch that reached a
        // higher user_version). Migration index N there is NOT index N here,
        // so re-running is wrong — but so is pretending everything is
        // applied: every future migration in this build silently becomes a
        // no-op. Say so loudly; the store's schema self-repair
        // (EnsureRequiredColumnsAsync) is what actually keeps the read path
        // working in that state.
        if (version > migrations.Count)
        {
            logger?.LogWarning(
                "SQLite user_version {Version} is AHEAD of this build's {Total} migrations — "
                + "migrations from this build will NOT run. The database was written by a "
                + "different build lineage; schema checks must not rely on the version counter.",
                version, migrations.Count);
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
