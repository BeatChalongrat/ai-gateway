using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Security;

public sealed class SensitivePersistenceSecurityTests : IDisposable
{
    private readonly string _directory =
        Path.Combine(Path.GetTempPath(), $"sran-secret-state-{Guid.NewGuid():N}");

    public SensitivePersistenceSecurityTests() => Directory.CreateDirectory(_directory);

    [Theory]
    [InlineData("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")]
    [InlineData("""{"refresh_token":"1//abcdefghijklmnopqrstuv"}""")]
    [InlineData("postgresql://user:super-secret@db.example/database")]
    [InlineData("-----BEGIN PRIVATE KEY-----\nABCDEF123456\n-----END PRIVATE KEY-----")]
    [InlineData("ghp_1234567890abcdefghijklmnop")]
    [InlineData("AKIAIOSFODNN7EXAMPLE")]
    [InlineData("person@example.test")]
    [InlineData("081-234-5678")]
    [InlineData("1-2345-67890-12-1")]
    public void Synthetic_Secrets_And_Personal_Data_Are_Redacted(string value)
    {
        var redacted = SensitiveDataRedactor.Redact(value);

        redacted.Should().NotContain(value);
        SensitiveDataRedactor.Classify(value)
            .Should().NotBe(SensitiveDataClassification.Internal);
    }

    [Fact]
    public void Redactor_Does_Not_Change_Internal_Identifiers_With_Incidental_Digits()
    {
        const string actionId = "action_5d6cdd3908217d7d18dfac6bab";
        const string actionIdWithPhoneLikeDigits = "action_1e5d0812345678e59c84a7";
        const string actionIdWithValidThaiIdDigits = "action_a1234567890121b";

        SensitiveDataRedactor.Redact(actionId).Should().Be(actionId);
        SensitiveDataRedactor.Redact(actionIdWithPhoneLikeDigits)
            .Should().Be(actionIdWithPhoneLikeDigits);
        SensitiveDataRedactor.Redact(actionIdWithValidThaiIdDigits)
            .Should().Be(actionIdWithValidThaiIdDigits);
        SensitiveDataRedactor.RedactJson(
                $$"""{"action_id":"{{actionId}}"}""")
            .Should().Contain(actionId);
    }

    [Fact]
    public void Json_Redaction_Preserves_Nested_Json_And_Unicode_Escapes()
    {
        var nested = JsonSerializer.Serialize(new
        {
            prompt = "เปิด Calculator แล้วคำนวณ 125 x 48",
            dataJson = JsonSerializer.Serialize(new
            {
                phone = "081-234-5678",
                token = "ghp_1234567890abcdefghijklmnop"
            })
        });

        var redacted = SensitiveDataRedactor.RedactJson(nested);
        using var outer = JsonDocument.Parse(redacted);
        using var inner = JsonDocument.Parse(
            outer.RootElement.GetProperty("dataJson").GetString()!);

        outer.RootElement.GetProperty("prompt").GetString()
            .Should().Contain("Calculator");
        inner.RootElement.GetProperty("phone").GetString()
            .Should().Be("[REDACTED_PHONE]");
        inner.RootElement.GetProperty("token").GetString()
            .Should().Be("[REDACTED]");
    }

    [Fact]
    public async Task Normal_Sqlite_Writes_Redact_Message_And_Tool_Payloads_Before_Storage()
    {
        const string secret = "ghp_1234567890abcdefghijklmnop";
        const string unknownSecret = "synthetic-unknown-confidential-value";
        var db = Path.Combine(_directory, "state.db");
        var store = new SqliteConversationStore(db);
        await store.UpsertConversationAsync(new Conversation
        {
            Id = "c1",
            Title = $"title {secret}",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
        await store.UpsertMessagesAsync(
        [
            new Message
            {
                Id = "m1",
                ConversationId = "c1",
                Role = MessageRole.Tool,
                Content = $"stdout {secret} {unknownSecret}",
                ToolCallsJson = System.Text.Json.JsonSerializer.Serialize(
                    new[] { new { function = new { arguments = unknownSecret } } }),
                CreatedAt = DateTimeOffset.UtcNow,
                SortOrder = 0
            }
        ]);
        await store.InsertToolExecutionAsync(new ToolExecution
        {
            Id = "e1",
            ConversationId = "c1",
            ToolName = "run_command",
            ArgumentsJson = System.Text.Json.JsonSerializer.Serialize(
                new { command = $"echo {secret} {unknownSecret}" }),
            Result = $"{secret} {unknownSecret}",
            Error = $"failure {secret} {unknownSecret}",
            Permission = "allowed",
            ExecutedAt = DateTimeOffset.UtcNow
        });

        (await store.GetMessagesAsync("c1")).Single().Content.Should().NotContain(secret);
        var execution = (await store.GetToolExecutionsAsync("c1")).Single();
        execution.ArgumentsJson.Should().NotContain(secret);
        execution.Result.Should().NotContain(secret);
        execution.Error.Should().NotContain(secret);
        execution.ArgumentsJson.Should().NotContain(unknownSecret);
        execution.Result.Should().NotContain(unknownSecret);
        execution.Error.Should().NotContain(unknownSecret);
        execution.ArgumentsJson.Should().Contain("persisted_payload_removed");
        var rawDatabase = Encoding.Latin1.GetString(await File.ReadAllBytesAsync(db));
        rawDatabase.Should().NotContain(secret);
        rawDatabase.Should().NotContain(unknownSecret);
    }

    [Fact]
    public async Task Security_Migration_Removes_Legacy_Raw_Tool_Payloads()
    {
        const string secret = "ghp_legacy_1234567890abcdefghijkl";
        var db = Path.Combine(_directory, "legacy.db");
        await using (var connection = new SqliteConnection(
                         // Pooling off: disposing a pooled connection returns it to a
                         // pool that keeps the file handle open, and the directory
                         // teardown below then cannot delete legacy.db. Same reason
                         // the production stores set it — see SqliteConversationStore.
                         new SqliteConnectionStringBuilder
                         {
                             DataSource = db,
                             Pooling = false
                         }.ToString()))
        {
            await connection.OpenAsync();
            await SqliteMigrator.MigrateAsync(
                connection,
                SqliteConversationStore.Migrations.Take(7).ToArray(),
                NullLogger.Instance);
        }

        await using (var connection = new SqliteConnection(
                         // Pooling off: disposing a pooled connection returns it to a
                         // pool that keeps the file handle open, and the directory
                         // teardown below then cannot delete legacy.db. Same reason
                         // the production stores set it — see SqliteConversationStore.
                         new SqliteConnectionStringBuilder
                         {
                             DataSource = db,
                             Pooling = false
                         }.ToString()))
        {
            await connection.OpenAsync();
            await using var command = connection.CreateCommand();
            command.CommandText = """
                INSERT INTO conversations
                    (id, created_at, updated_at, source)
                VALUES ('c1', $at, $at, 'local');

                INSERT INTO tool_executions
                    (id, conversation_id, tool_name, arguments, result, permission, error, executed_at)
                VALUES ('legacy', 'c1', 'run_command', $secret, $secret, 'allowed', $secret, $at);
                """;
            command.Parameters.AddWithValue("$secret", secret);
            command.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
            await command.ExecuteNonQueryAsync();
        }

        var upgraded = new SqliteConversationStore(db);
        await upgraded.InitializeAsync();
        var record = (await upgraded.GetToolExecutionsAsync("c1")).Single();

        record.ArgumentsJson.Should().Contain("legacy_payload_removed");
        record.Result.Should().BeNull();
        record.Error.Should().NotContain(secret);
    }

    [Fact]
    public async Task Durable_Runtime_Scalar_Columns_Redact_Secrets_Before_Sqlite()
    {
        const string secret = "ghp_durable1234567890abcdefghijkl";
        var db = Path.Combine(_directory, "durable-state.db");
        var conversations = new SqliteConversationStore(db);
        await conversations.UpsertConversationAsync(new Conversation
        {
            Id = "durable-c1",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
        var tasks = new SqliteAgentTaskStore(db);
        var runtime = new DurableTaskRuntime(
            tasks,
            new GoalInterpreter(),
            new CapabilityRouter());

        var context = await runtime.StartOrResumeAsync(
            "durable-c1",
            [new GatewayMessage
            {
                Role = "user",
                Content = $"open Calculator using token={secret}"
            }],
            "conversation:durable-c1");
        context.Should().NotBeNull();
        await tasks.AddEvidenceAsync(new TaskEvidence
        {
            EvidenceId = "secret-evidence",
            TaskId = context!.TaskId,
            EvidenceType = EvidenceType.ToolResult,
            Source = $"web:{secret}",
            Summary = $"exception {secret}",
            StructuredData = JsonSerializer.Serialize(new { nested = new { token = secret } }),
            Confidence = 0.5,
            RetentionPolicy = "task-default",
            Timestamp = DateTimeOffset.UtcNow
        });
        var rawDatabase = Encoding.Latin1.GetString(await File.ReadAllBytesAsync(db));
        rawDatabase.Should().NotContain(secret);
    }

    [Fact]
    public async Task Scheduler_Payloads_RoundTrip_But_Are_Dpapi_Protected_At_Rest()
    {
        const string secret = "sk-schedulerSyntheticSecret123456789";
        var db = Path.Combine(_directory, "scheduled-state.db");
        var store = new SqliteScheduledTaskStore(db);
        await store.UpsertAsync(new ScheduledTask
        {
            Id = "schedule-1",
            Name = $"secret job {secret}",
            Prompt = $"inspect workspace with {secret}",
            LastResult = $"stdout {secret}",
            CronExpression = "0 6 * * *"
        });

        var loaded = (await store.GetAllAsync()).Single();
        loaded.Name.Should().Contain(secret);
        loaded.Prompt.Should().Contain(secret);
        loaded.LastResult.Should().Contain(secret);
        var rawDatabase = Encoding.Latin1.GetString(await File.ReadAllBytesAsync(db));
        rawDatabase.Should().NotContain(secret);
        rawDatabase.Should().Contain("dpapi:v1:");
    }

    [Fact]
    public async Task Crash_Report_Redacts_Exception_Before_Http_Transmission()
    {
        const string secret = "Bearer abcdefghijklmnopqrstuvwxyz";
        var handler = new RecordingHandler();
        using var http = new HttpClient(handler);
        var service = new CrashReportService(
            new EnabledSettings(),
            "https://crash.example.test/report",
            NullLogger<CrashReportService>.Instance,
            http);

        (await service.ReportAsync(new InvalidOperationException(secret)))
            .Should().BeTrue();

        handler.Body.Should().NotContain(secret);
        handler.Body.Should().Contain("[REDACTED_TOKEN]");
    }

    public void Dispose()
    {
        if (Directory.Exists(_directory))
            Directory.Delete(_directory, recursive: true);
    }

    private sealed class EnabledSettings : ISettingsService
    {
        public UserSettings Current { get; } = new() { CrashReportingEnabled = true };
        public bool IsFirstRun => false;
        public event EventHandler? SettingsChanged;
        public void Save(UserSettings settings) => SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    private sealed class RecordingHandler : HttpMessageHandler
    {
        public string Body { get; private set; } = string.Empty;

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Body = await request.Content!.ReadAsStringAsync(cancellationToken);
            return new HttpResponseMessage(HttpStatusCode.OK);
        }
    }
}
