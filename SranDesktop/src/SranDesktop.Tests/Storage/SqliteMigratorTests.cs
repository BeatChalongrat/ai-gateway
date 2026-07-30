using FluentAssertions;
using Microsoft.Data.Sqlite;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Storage;

public class SqliteMigratorTests : IDisposable
{
    private readonly string _dbPath;

    public SqliteMigratorTests()
    {
        _dbPath = Path.Combine(Path.GetTempPath(), $"sran-migrate-{Guid.NewGuid():N}.db");
    }

    private SqliteConnection Open()
    {
        var conn = new SqliteConnection($"Data Source={_dbPath}");
        conn.Open();
        return conn;
    }

    private static async Task<int> UserVersionAsync(SqliteConnection conn)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "PRAGMA user_version;";
        return Convert.ToInt32(await cmd.ExecuteScalarAsync());
    }

    private static async Task<bool> ColumnExistsAsync(SqliteConnection conn, string table, string column)
    {
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = $"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name = '{column}'";
        return Convert.ToInt32(await cmd.ExecuteScalarAsync()) > 0;
    }

    private static readonly string[] ProbeMigrations =
    {
        "CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY, name TEXT);",
        "ALTER TABLE probe ADD COLUMN extra TEXT;",
        "CREATE TABLE IF NOT EXISTS probe2 (id INTEGER PRIMARY KEY);",
    };

    [Fact]
    public async Task Fresh_Db_Applies_All_Migrations()
    {
        await using var conn = Open();

        await SqliteMigrator.MigrateAsync(conn, ProbeMigrations);

        (await UserVersionAsync(conn)).Should().Be(ProbeMigrations.Length);
        (await ColumnExistsAsync(conn, "probe", "extra")).Should().BeTrue();
    }

    [Fact]
    public async Task Db_At_Version_1_Runs_Only_Later_Migrations()
    {
        await using (var conn = Open())
        {
            // Simulate an install that stopped at version 1.
            await SqliteMigrator.MigrateAsync(conn, ProbeMigrations.Take(1).ToArray());
            (await UserVersionAsync(conn)).Should().Be(1);
        }

        await using (var conn = Open())
        {
            await SqliteMigrator.MigrateAsync(conn, ProbeMigrations);

            (await UserVersionAsync(conn)).Should().Be(3);
            (await ColumnExistsAsync(conn, "probe", "extra")).Should().BeTrue();
            // Re-running migration 0 on the existing table would have been a
            // no-op anyway, but the version gate means it never even ran —
            // proven by the ALTER (which would throw on a duplicate column)
            // succeeding exactly once.
        }
    }

    [Fact]
    public async Task Failed_Migration_Rolls_Back_And_Keeps_Version()
    {
        var withBroken = new[]
        {
            ProbeMigrations[0],
            // Multi-statement step: the INSERT lands first, then the bad
            // statement fails — the whole step must roll back.
            "INSERT INTO probe (name) VALUES ('should-roll-back');\nALTER TABLE missing_table ADD COLUMN x TEXT;",
        };

        await using var conn = Open();
        var act = () => SqliteMigrator.MigrateAsync(conn, withBroken);

        await act.Should().ThrowAsync<SqliteException>();
        (await UserVersionAsync(conn)).Should().Be(1); // step 0 retained, step 1 rolled back
        await using var count = conn.CreateCommand();
        count.CommandText = "SELECT COUNT(*) FROM probe";
        Convert.ToInt32(await count.ExecuteScalarAsync()).Should().Be(0);
    }

    [Fact]
    public async Task Legacy_Db_With_Tables_But_Version_Zero_Migrates_Cleanly()
    {
        // Simulate a pre-migrator install: the real baseline schema exists,
        // but user_version was never set.
        await using (var conn = Open())
        {
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = SqliteConversationStore.Migrations[0];
            await cmd.ExecuteNonQueryAsync();
            (await UserVersionAsync(conn)).Should().Be(0);
        }

        await using (var conn = Open())
        {
            await SqliteMigrator.MigrateAsync(conn, SqliteConversationStore.Migrations);

            (await UserVersionAsync(conn)).Should().Be(SqliteConversationStore.Migrations.Length);
            (await ColumnExistsAsync(conn, "conversations", "server_id")).Should().BeTrue();
            (await ColumnExistsAsync(conn, "messages", "server_id")).Should().BeTrue();
        }
    }

    public void Dispose()
    {
        try
        {
            if (File.Exists(_dbPath)) File.Delete(_dbPath);
        }
        catch (IOException)
        {
        }
    }
}
