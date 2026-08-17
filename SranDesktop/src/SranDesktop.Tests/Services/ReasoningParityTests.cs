using System.Text.Json;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using SranDesktop.Application.Events;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// MR D — desktop reasoning parity. The three audit gaps, pinned headless:
/// the gateway request carries a reasoning config, the chunk parser turns
/// the gateway's typed reasoning frames into events, and reasoning text
/// survives the local store round-trip — including a database created
/// before the column existed.
/// </summary>
public sealed class ReasoningParityTests : IDisposable
{
    private readonly string _dbDir =
        Path.Combine(Path.GetTempPath(), "sran-tests-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        try { Directory.Delete(_dbDir, recursive: true); } catch { /* best effort */ }
    }

    // ── Gap 1: the request can ask ──────────────────────────────────────────

    [Fact]
    public void Request_serializes_reasoning_block_in_gateway_shape()
    {
        var json = JsonSerializer.Serialize(new GatewayCompletionRequest
        {
            Model = "gpt-5.4-mini",
            Messages = new List<GatewayMessage>(),
            Reasoning = new GatewayReasoningConfig()
        });
        using var doc = JsonDocument.Parse(json);
        var reasoning = doc.RootElement.GetProperty("reasoning");
        reasoning.GetProperty("mode").GetString().Should().Be("auto");
        reasoning.GetProperty("show_summary").GetBoolean().Should().BeTrue();
    }

    [Fact]
    public void Request_omits_reasoning_when_null()
    {
        var json = JsonSerializer.Serialize(new GatewayCompletionRequest
        {
            Model = "gpt-4o",
            Messages = new List<GatewayMessage>()
        });
        json.Should().NotContain("\"reasoning\"");
    }

    // ── Gap 2: the parser no longer drops the frames ────────────────────────

    [Fact]
    public void Parser_turns_reasoning_summary_delta_into_event()
    {
        var parser = new GatewayChunkParser();
        var events = parser.Parse(
            "{\"id\":\"r1\",\"type\":\"reasoning_summary_delta\"," +
            "\"model\":\"gpt-5.4-mini\",\"text\":\"first I check\"}");
        events.Should().ContainSingle()
            .Which.Should().BeOfType<AgentReasoningDeltaEvent>()
            .Which.Text.Should().Be("first I check");
    }

    [Fact]
    public void Parser_turns_analysis_status_into_event()
    {
        var parser = new GatewayChunkParser();
        var events = parser.Parse(
            "{\"id\":\"r1\",\"type\":\"analysis_status\",\"status\":\"started\"}");
        events.Should().ContainSingle()
            .Which.Should().BeOfType<AgentAnalysisStatusEvent>()
            .Which.Status.Should().Be("started");
    }

    [Fact]
    public void Parser_still_ignores_unknown_typed_frames()
    {
        var parser = new GatewayChunkParser();
        parser.Parse("{\"type\":\"reasoning_notice\",\"reason\":\"x\"}")
            .Should().BeEmpty();
    }

    // ── Gap 3: persistence survives reopen — and old databases upgrade ─────

    private Message AssistantRow(string id, string convoId, string? reasoning) => new()
    {
        Id = id,
        ConversationId = convoId,
        Role = MessageRole.Assistant,
        Content = "the answer",
        CreatedAt = DateTimeOffset.UtcNow,
        SortOrder = 0,
        Reasoning = reasoning
    };

    private async Task<Conversation> SeedConversationAsync(SqliteConversationStore store)
    {
        var convo = new Conversation
        {
            Id = "c1",
            Title = "t",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow
        };
        await store.UpsertConversationAsync(convo);
        return convo;
    }

    [Fact]
    public async Task Reasoning_survives_store_reopen()
    {
        var path = Path.Combine(_dbDir, "data.db");
        var store = new SqliteConversationStore(path);
        var convo = await SeedConversationAsync(store);
        await store.UpsertMessageAsync(AssistantRow("m1", convo.Id, "step one; step two"));

        var reopened = new SqliteConversationStore(path);
        var rows = await reopened.GetMessagesAsync(convo.Id);
        rows.Should().ContainSingle().Which.Reasoning.Should().Be("step one; step two");
    }

    [Fact]
    public async Task Old_database_without_reasoning_column_upgrades_in_place()
    {
        // Simulate a pre-MR-D database: apply every migration EXCEPT the
        // reasoning one, then stamp user_version PAST it — the exact
        // stale-user_version trap the RequiredColumns safety net exists
        // for (a build whose migration list already contains the entry,
        // pointed at a db whose version says "already applied").
        var path = Path.Combine(_dbDir, "old.db");
        Directory.CreateDirectory(_dbDir);
        var connString = new SqliteConnectionStringBuilder { DataSource = path }.ToString();
        await using (var conn = new SqliteConnection(connString))
        {
            await conn.OpenAsync();
            foreach (var migration in SqliteConversationStore.Migrations)
            {
                if (migration.Contains("ADD COLUMN reasoning")) continue;
                await using var cmd = conn.CreateCommand();
                cmd.CommandText = migration;
                await cmd.ExecuteNonQueryAsync();
            }
            await using var stamp = conn.CreateCommand();
            stamp.CommandText =
                $"PRAGMA user_version = {SqliteConversationStore.Migrations.Length};";
            await stamp.ExecuteNonQueryAsync();
        }

        var store = new SqliteConversationStore(path);
        var convo = await SeedConversationAsync(store);   // triggers Initialize
        await store.UpsertMessageAsync(AssistantRow("m1", convo.Id, "recovered"));
        var rows = await store.GetMessagesAsync(convo.Id);
        rows.Should().ContainSingle().Which.Reasoning.Should().Be("recovered");
    }

    [Fact]
    public async Task Null_reasoning_roundtrips_as_null()
    {
        var store = new SqliteConversationStore(Path.Combine(_dbDir, "n.db"));
        var convo = await SeedConversationAsync(store);
        await store.UpsertMessageAsync(AssistantRow("m1", convo.Id, null));
        (await store.GetMessagesAsync(convo.Id))
            .Should().ContainSingle().Which.Reasoning.Should().BeNull();
    }

    // ── The Expander header stays honest for hydrated rows ─────────────────

    [Fact]
    public void Hydrated_reasoning_shows_analyzed_not_thinking()
    {
        var vm = new MessageViewModel(
            "m1", MessageRole.Assistant, "answer", DateTimeOffset.UtcNow);
        vm.SetPersistedReasoning("persisted thinking");

        vm.Reasoning.Should().Be("persisted thinking");
        vm.IsReasoningComplete.Should().BeTrue();
        // No duration was persisted — the header must not claim the model
        // is thinking, and must not fabricate a "Thought for" time.
        vm.ReasoningHeader.Should().NotContain("Thinking");
        vm.ReasoningHeader.Should().NotContain("Thought for");
    }
}
