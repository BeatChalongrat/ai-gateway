using System.Runtime.CompilerServices;
using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// Phase 16 A: nested sub-agent tool activity surfaced live under the parent
/// run_subagent card. Two seams are covered: the VM child-collection routing
/// (ToolExecutionItemViewModel) and the tool's ISupportsNestedProgress
/// publication (SpawnAgentTool), including depth-1 preservation.
/// </summary>
public class NestedProgressTests
{
    // ── VM routing: child collection lifecycle ────────────────────────────

    private static ToolExecutionItemViewModel ParentCard() =>
        new("call-parent", "run_subagent", """{"task":"do it"}""");

    private static AgentToolCallsEvent ToolCall(string id, string name, string args = "{}") =>
        new(new List<GatewayToolCall>
        {
            new() { Id = id, Function = new GatewayToolCallFunction { Name = name, Arguments = args } },
        });

    private static AgentToolResultEvent NestedResult(string id, string name, bool success) =>
        new(id, name, "{}", success, success ? "ok" : "boom", 12, FullResult: success ? "ok" : "boom");

    [Fact]
    public void RouteNestedEvent_Populates_Child_And_Completes_Ok()
    {
        var parent = ParentCard();
        parent.HasChildren.Should().BeFalse();

        parent.RouteNestedEvent(ToolCall("c1", "read_file", """{"path":"a.txt"}"""));

        parent.HasChildren.Should().BeTrue();
        var child = parent.Children.Should().ContainSingle().Subject;
        child.ToolName.Should().Be("read_file");
        child.IsPending.Should().BeTrue();

        parent.RouteNestedEvent(NestedResult("c1", "read_file", success: true));

        child.IsOk.Should().BeTrue();
        child.IsPending.Should().BeFalse();
        parent.Children.Should().ContainSingle(); // completion updates in place
    }

    [Fact]
    public void RouteNestedEvent_Failure_Marks_Child_Failed()
    {
        var parent = ParentCard();
        parent.RouteNestedEvent(ToolCall("c1", "run_command"));

        parent.RouteNestedEvent(NestedResult("c1", "run_command", success: false));

        parent.Children.Single().IsFailed.Should().BeTrue();
    }

    [Fact]
    public void RouteNestedEvent_Result_Without_Prior_Call_Still_Creates_Child()
    {
        // A result can arrive for a tool whose call frame we never saw (mirrors
        // the top-level loop's defensive create-then-complete path).
        var parent = ParentCard();

        parent.RouteNestedEvent(NestedResult("orphan", "search_files", success: true));

        var child = parent.Children.Should().ContainSingle().Subject;
        child.ToolName.Should().Be("search_files");
        child.IsOk.Should().BeTrue();
    }

    [Fact]
    public void Normal_Tool_Card_Has_No_Children()
    {
        // A regular (non-subagent) card never gets routed nested events, so its
        // child collection stays empty — no regression to top-level cards.
        var card = new ToolExecutionItemViewModel("c", "read_file", "{}");
        card.HasChildren.Should().BeFalse();
        card.Children.Should().BeEmpty();
    }

    // ── Tool publication: SpawnAgentTool raises NestedProgress ─────────────

    private sealed class ScriptedGateway : IGatewayApiClient
    {
        private readonly Queue<string[]> _responses = new();
        public void Enqueue(params string[] frames) => _responses.Enqueue(frames);

        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            var frames = _responses.Count > 0 ? _responses.Dequeue() : new[] { Stop };
            foreach (var frame in frames)
            {
                ct.ThrowIfCancellationRequested();
                yield return new SseFrame(null, frame);
                await Task.Yield();
            }
        }

        public Task<GatewaySearchResponse> SearchAsync(
            string query, int maxResults = 5, CancellationToken ct = default) =>
            Task.FromResult(new GatewaySearchResponse(query, "fake", 0, new List<GatewaySearchResultItem>()));
    }

    private sealed class FakeTool : IMcpTool
    {
        public string Name => "fake_tool";
        public string Description => "fake";
        public JsonElement ParametersSchema => JsonDocument.Parse("{}").RootElement.Clone();
        public ToolCategory Category => ToolCategory.Network;
        public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;
        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
            Task.FromResult(ToolResult.Ok("fake result"));
    }

    private const string Stop = """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""";
    private const string FinishToolCalls = """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""";
    private static string Content(string t) =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"content\":\"" + t + "\"},\"finish_reason\":null}]}";
    private static string ToolCallFrame(string id, string name) =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"" + id +
        "\",\"type\":\"function\",\"function\":{\"name\":\"" + name + "\",\"arguments\":\"{}\"}}]},\"finish_reason\":null}]}";

    private static JsonElement Args(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private static (SpawnAgentTool Tool, ScriptedGateway Gateway) Build()
    {
        var gateway = new ScriptedGateway();
        var registry = new McpToolRegistry();
        registry.Register(new FakeTool());
        var host = new McpToolHost(registry);

        var permissions = new Mock<IPermissionService>();
        permissions.Setup(p => p.CheckAsync(It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);

        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings { DefaultModel = "m" });

        var parent = new ChatOrchestrator(
            gateway, host, permissions.Object, new Mock<IConversationStore>().Object,
            NullLogger<ChatOrchestrator>.Instance);

        var tool = new SpawnAgentTool(
            gateway, permissions.Object, new Mock<IConversationStore>().Object,
            () => host, () => parent, settings.Object, NullLoggerFactory.Instance);
        registry.Register(tool);
        return (tool, gateway);
    }

    [Fact]
    public async Task NestedProgress_Publishes_Call_And_Result_And_Feeds_Parent_Card()
    {
        var (tool, gateway) = Build();
        // Nested iter 1: call fake_tool; iter 2: final answer.
        gateway.Enqueue(ToolCallFrame("n1", "fake_tool"), FinishToolCalls, "[DONE]");
        gateway.Enqueue(Content("done"), Stop, "[DONE]");

        // Wire exactly like ChatViewModel: route each nested event into the card.
        var parent = ParentCard();
        tool.NestedProgress += evt => parent.RouteNestedEvent(evt);

        var events = new List<AgentStreamEvent>();
        tool.NestedProgress += events.Add;

        var result = await tool.ExecuteAsync(Args("""{"task":"go"}"""), CancellationToken.None);

        result.Success.Should().BeTrue();
        events.Should().Contain(e => e is AgentToolCallsEvent);
        events.Should().Contain(e => e is AgentToolResultEvent);

        // The parent card mirrors the nested tool's pending→ok lifecycle.
        var child = parent.Children.Should().ContainSingle().Subject;
        child.ToolName.Should().Be("fake_tool");
        child.IsOk.Should().BeTrue();
    }

    [Fact]
    public async Task NestedProgress_Never_Publishes_RunSubagent_DepthOne_Preserved()
    {
        var (tool, gateway) = Build();
        // The nested model tries to spawn run_subagent by name — it is
        // unresolvable (FilteredToolHost) and must not surface as a child card.
        gateway.Enqueue(ToolCallFrame("n1", "run_subagent"), FinishToolCalls, "[DONE]");
        gateway.Enqueue(Content("stopping"), Stop, "[DONE]");

        var parent = ParentCard();
        var seen = new List<AgentStreamEvent>();
        tool.NestedProgress += evt => { seen.Add(evt); parent.RouteNestedEvent(evt); };

        await tool.ExecuteAsync(Args("""{"task":"go deeper"}"""), CancellationToken.None);

        // No published event mentions run_subagent, and no child card exists.
        seen.OfType<AgentToolResultEvent>().Should().NotContain(e => e.ToolName == "run_subagent");
        seen.OfType<AgentToolCallsEvent>()
            .SelectMany(e => e.ToolCalls).Should().NotContain(c => c.Function.Name == "run_subagent");
        parent.HasChildren.Should().BeFalse();
    }
}
