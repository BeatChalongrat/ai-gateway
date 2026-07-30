using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class PermissionServiceTests : IDisposable
{
    private sealed class FakeTool : IMcpTool
    {
        public string Name { get; init; } = "fake_tool";
        public string Description => "fake";
        public JsonElement ParametersSchema => JsonDocument.Parse("{}").RootElement.Clone();
        public ToolCategory Category => ToolCategory.FileSystem;
        public PermissionLevel DefaultPermission { get; init; } = PermissionLevel.Ask;
        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
            Task.FromResult(ToolResult.Ok("ok"));
    }

    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly PermissionService _service;

    public PermissionServiceTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-perm-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
        _service = new PermissionService(_store);
    }

    [Fact]
    public async Task Default_Ask_Needs_Consent()
    {
        var decision = await _service.CheckAsync(new FakeTool(), "C:\\x");

        decision.Should().Be(PermissionDecision.NeedsConsent);
    }

    [Fact]
    public async Task Default_AllowAlways_Is_Auto()
    {
        var tool = new FakeTool { DefaultPermission = PermissionLevel.AllowAlways };

        (await _service.CheckAsync(tool, "*")).Should().Be(PermissionDecision.Auto);
    }

    [Fact]
    public async Task Session_AllowOnce_Becomes_Auto_For_Same_Scope()
    {
        var tool = new FakeTool();
        _service.RecordSessionDecision(tool.Name, "C:\\x", PermissionLevel.AllowOnce);

        (await _service.CheckAsync(tool, "C:\\x")).Should().Be(PermissionDecision.Auto);
        (await _service.CheckAsync(tool, "C:\\other")).Should().Be(PermissionDecision.NeedsConsent);
    }

    [Fact]
    public async Task Session_Deny_Blocks()
    {
        var tool = new FakeTool();
        _service.RecordSessionDecision(tool.Name, "*", PermissionLevel.Deny);

        (await _service.CheckAsync(tool, "*")).Should().Be(PermissionDecision.Denied);
    }

    [Fact]
    public async Task Persisted_AllowAlways_Wins_And_Survives_New_Service()
    {
        var tool = new FakeTool();
        await _service.PersistDecisionAsync(tool.Name, "C:\\proj", "allow_always");

        var freshService = new PermissionService(_store);
        (await freshService.CheckAsync(tool, "C:\\proj")).Should().Be(PermissionDecision.Auto);
    }

    [Fact]
    public async Task Persisted_DenyAlways_Blocks()
    {
        var tool = new FakeTool();
        await _service.PersistDecisionAsync(tool.Name, "*", "deny_always");

        (await _service.CheckAsync(tool, "*")).Should().Be(PermissionDecision.Denied);
    }

    public void Dispose()
    {
        if (Directory.Exists(_dbDir))
            Directory.Delete(_dbDir, recursive: true);
    }
}
