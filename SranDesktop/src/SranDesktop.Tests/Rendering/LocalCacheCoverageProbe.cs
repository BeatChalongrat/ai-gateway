using Microsoft.Data.Sqlite;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// TEMPORARY MEASUREMENT — Part 3 of the open-latency study: is the local
/// store even usable as a first paint?
///
/// Reads a COPY of the live desktop database (%APPDATA%\SRAN Desktop\data.db)
/// so nothing touches the running app's file. Read-only either way; the copy
/// is belt and braces.
///
/// Skips silently when there is no database, so this is not a test that fails
/// on another machine.
/// </summary>
public class LocalCacheCoverageProbe
{
    private static string LiveDb =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "SRAN Desktop", "data.db");

    [Fact]
    public void Coverage()
    {
        if (!File.Exists(LiveDb))
        {
            TestOutput.Record("[cache] no live database at " + LiveDb);
            return;
        }

        var copy = Path.Combine(Path.GetTempPath(), "sran-cache-probe.db");
        File.Copy(LiveDb, copy, overwrite: true);
        // The live DB is WAL-mode; rows still in the -wal file are invisible to
        // a bare copy, so bring those two along when they exist.
        foreach (var suffix in new[] { "-wal", "-shm" })
        {
            var side = LiveDb + suffix;
            if (File.Exists(side))
                File.Copy(side, copy + suffix, overwrite: true);
        }

        using var conn = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = copy,
            Mode = SqliteOpenMode.ReadOnly,
        }.ToString());
        conn.Open();

        TestOutput.Record("[cache] ── local store coverage ──────────────────────");
        TestOutput.Record($"[cache] db size = {new FileInfo(LiveDb).Length / 1024.0 / 1024.0:F2} MB");

        Scalar(conn, "SELECT COUNT(*) FROM conversations", "conversations (local)");
        Scalar(conn, "SELECT COUNT(*) FROM messages", "messages (all rows)");
        Scalar(conn,
            "SELECT COUNT(*) FROM messages WHERE role IN ('user','assistant') AND content IS NOT NULL AND TRIM(content) <> ''",
            "messages (renderable bubbles)");
        Scalar(conn,
            "SELECT COUNT(*) FROM messages WHERE tool_calls IS NOT NULL OR role = 'tool'",
            "messages (agent chain rows, never bubbles)");

        // Conversations with ZERO renderable rows would paint blank under a
        // local-first scheme and still need the skeleton.
        Scalar(conn, @"
            SELECT COUNT(*) FROM conversations c
            WHERE NOT EXISTS (
                SELECT 1 FROM messages m
                WHERE m.conversation_id = c.id
                  AND m.role IN ('user','assistant')
                  AND m.content IS NOT NULL AND TRIM(m.content) <> '')",
            "conversations with NO cached bubbles");

        TestOutput.Record("[cache] ── 15 most recent conversations ─────────────");
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = @"
                SELECT c.id,
                       COALESCE(c.updated_at, c.created_at) AS ts,
                       (SELECT COUNT(*) FROM messages m
                         WHERE m.conversation_id = c.id
                           AND m.role IN ('user','assistant')
                           AND m.content IS NOT NULL AND TRIM(m.content) <> '') AS bubbles,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS rows_all,
                       (SELECT MAX(COALESCE(m.created_at,'')) FROM messages m
                         WHERE m.conversation_id = c.id) AS newest_msg
                FROM conversations c
                ORDER BY ts DESC
                LIMIT 15";
            using var r = cmd.ExecuteReader();
            var n = 0;
            while (r.Read())
            {
                n++;
                var id = r.GetString(0);
                TestOutput.Record(
                    $"[cache]  {n,2}. {id[..Math.Min(12, id.Length)],-12} " +
                    $"updated={Trunc(r.IsDBNull(1) ? "" : r.GetString(1))} " +
                    $"bubbles={r.GetInt32(2),4} allRows={r.GetInt32(3),4} " +
                    $"newestMsg={Trunc(r.IsDBNull(4) ? "" : r.GetString(4))}");
            }
            if (n == 0) TestOutput.Record("[cache]  (no conversations)");
        }

        // Distribution of thread sizes — decides whether a 50-row page is even
        // the common case.
        TestOutput.Record("[cache] ── bubble-count distribution ───────────────");
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = @"
                SELECT bucket, COUNT(*) FROM (
                  SELECT CASE
                     WHEN b = 0 THEN '0 (empty)'
                     WHEN b < 10 THEN '1-9'
                     WHEN b < 50 THEN '10-49'
                     ELSE '50+'
                   END AS bucket
                  FROM (SELECT c.id,
                          (SELECT COUNT(*) FROM messages m
                            WHERE m.conversation_id = c.id
                              AND m.role IN ('user','assistant')
                              AND m.content IS NOT NULL AND TRIM(m.content) <> '') AS b
                        FROM conversations c)
                ) GROUP BY bucket ORDER BY bucket";
            using var r = cmd.ExecuteReader();
            while (r.Read())
                TestOutput.Record($"[cache]  {r.GetString(0),-10} : {r.GetInt32(1)}");
        }

        CompareWithServer(conn);
        Assert.True(true);
    }

    /// <summary>
    /// Join local counts against the server's own numbers, exported from
    /// chat-edge's Postgres (chat.messages_masked) rather than through the API
    /// — same figures, and no auth token has to be handled to read them.
    ///
    /// This is what decides whether local-first paint is even possible: a
    /// conversation whose local count is short of the server's would paint an
    /// incomplete transcript and then visibly grow.
    /// </summary>
    private static void CompareWithServer(SqliteConnection conn)
    {
        var csv = Path.Combine(Path.GetTempPath(), "sran-server-counts.csv");
        if (!File.Exists(csv))
        {
            TestOutput.Record("[cache] no server export at " + csv + " — comparison skipped");
            return;
        }

        var server = new Dictionary<string, (int Bubbles, int All)>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in File.ReadAllLines(csv))
        {
            var parts = line.Split(',');
            if (parts.Length >= 3 && int.TryParse(parts[1], out var b) && int.TryParse(parts[2], out var a))
                server[parts[0].Trim()] = (b, a);
        }
        TestOutput.Record($"[cache] ── vs server ({server.Count} server conversations) ──");

        int complete = 0, partial = 0, ahead = 0, emptyLocal = 0, notOnServer = 0;
        var detail = new List<string>();

        using var cmd = conn.CreateCommand();
        cmd.CommandText = @"
            SELECT c.id,
                   (SELECT COUNT(*) FROM messages m
                     WHERE m.conversation_id = c.id
                       AND m.role IN ('user','assistant')
                       AND m.content IS NOT NULL AND TRIM(m.content) <> '') AS bubbles,
                   COALESCE(c.updated_at, c.created_at) AS ts
            FROM conversations c
            ORDER BY ts DESC";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            var id = r.GetString(0);
            var localBubbles = r.GetInt32(1);
            if (!server.TryGetValue(id, out var srv))
            {
                notOnServer++;
                detail.Add($"[cache]   {id[..8]} local={localBubbles,3} server=ABSENT");
                continue;
            }
            string verdict;
            if (localBubbles == 0 && srv.Bubbles > 0) { emptyLocal++; verdict = "EMPTY-LOCAL"; }
            else if (localBubbles == srv.Bubbles) { complete++; verdict = "complete"; }
            else if (localBubbles < srv.Bubbles) { partial++; verdict = "PARTIAL"; }
            else { ahead++; verdict = "local-ahead"; }
            detail.Add($"[cache]   {id[..8]} local={localBubbles,3} server={srv.Bubbles,3}  {verdict}");
        }

        foreach (var line in detail) TestOutput.Record(line);
        TestOutput.Record(
            $"[cache] VERDICT complete={complete} partial={partial} emptyLocal={emptyLocal} " +
            $"localAhead={ahead} notOnServer={notOnServer}");
    }

    private static string Trunc(string s) => s.Length >= 19 ? s[..19] : s;

    private static void Scalar(SqliteConnection conn, string sql, string label)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        TestOutput.Record($"[cache] {label,-42} = {cmd.ExecuteScalar()}");
    }
}
