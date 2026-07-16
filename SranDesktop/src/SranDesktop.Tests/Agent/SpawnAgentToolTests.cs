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
using Xunit;

namespace SranDesktop.Tests.Agent;

public class SpawnAgentToolTests
{
    private sealed class ScriptedGateway : IGatewayApiClient
    {
        private readonly Queue<string[]> _responses = new();
        public readonly List<GatewayCompletionRequest> Requests = new();

        public void Enqueue(params string[] frames) => _responses.Enqueue(frames);

        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            Requests.Add(request);
            var frames = _responses.Count > 0 ? _responses.Dequeue() : new[] { StopFrame };
            foreach (var frame in frames)
            {
                ct.ThrowIfCancellationRequested();
                yield return new SseFrame(null, frame);
                await Task.Yield();
            }
        }

        public Task<GatewaySearchResponse> SearchAsync(
            string query, int maxResults = 5, CancellationToken ct = default) =>
            Task.FromResult(new GatewaySearchResponse(query, "fake", 0,
                new List<GatewaySearchResultItem>()));
    }

    /// <summary>Streams one delta, then blocks until the token is cancelled —
    /// models a nested run stuck mid-stream when the parent cancels.</summary>
    private sealed class HangingGateway : IGatewayApiClient
    {
        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            yield return new SseFrame(null, ContentFrame("thinking"));
            await Task.Delay(Timeout.Infinite, ct);
        }

        public Task<GatewaySearchResponse> SearchAsync(
            string query, int maxResults = 5, CancellationToken ct = default) =>
            Task.FromResult(new GatewaySearchResponse(query, "fake", 0,
                new List<GatewaySearchResultItem>()));
    }

    private sealed class FakeTool : IMcpTool
    {
        public string Name => "fake_tool";
        public string Description => "A fake tool";
        public JsonElement ParametersSchema => JsonDocument.Parse("{}").RootElement.Clone();
        public ToolCategory Category => ToolCategory.FileSystem;
        public PermissionLevel DefaultPermission => PermissionLevel.Ask;
        public int Executions;

        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
        {
            Executions++;
            return Task.FromResult(ToolResult.Ok("fake result"));
        }
    }

    private const string StopFrame =
        """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""";

    private static string ContentFrame(string text) =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"content\":\"" + text + "\"},\"finish_reason\":null}]}";

    private static string ToolCallFrame(string id, string name, string args = "{}") =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"" + id +
        "\",\"type\":\"function\",\"function\":{\"name\":\"" + name +
        "\",\"arguments\":\"" + args.Replace("\\", "\\\\").Replace("\"", "\\\"") +
        "\"}}]},\"finish_reason\":null}]}";

    private const string FinishToolCallsFrame =
        """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""";

    private static JsonElement Args(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private sealed record Harness(
        SpawnAgentTool Tool,
        ScriptedGateway Gateway,
        FakeTool Fake,
        ChatOrchestrator Parent,
        Mock<IPermissionService> Permissions);

    /// <summary>
    /// Builds the full production shape: a real registry hosting fake_tool AND
    /// run_subagent itself, a real McpToolHost, and a real parent
    /// ChatOrchestrator whose ConsentHandler the tool forwards to.
    /// </summary>
    private static Harness Create(
        PermissionDecision decision = PermissionDecision.Auto,
        IGatewayApiClient? gatewayOverride = null)
    {
        var gateway = new ScriptedGateway();
        var effectiveGateway = gatewayOverride ?? gateway;
        var fake = new FakeTool();

        var registry = new McpToolRegistry();
        registry.Register(fake);

        var host = new McpToolHost(registry);

        var permissions = new Mock<IPermissionService>();
        // run_subagent itself is AllowAlways; the fake Ask-tool resolves to the
        // scenario's decision — mirrors the real PermissionService's fallback
        // to the tool's declared default.
        permissions.Setup(p => p.CheckAsync(It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .Returns((IMcpTool t, string _, CancellationToken _) => Task.FromResult(
                t.DefaultPermission == PermissionLevel.AllowAlways ? PermissionDecision.Auto : decision));

        var store = new Mock<IConversationStore>();

        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings { DefaultModel = "test-model" });

        var parent = new ChatOrchestrator(
            effectiveGateway, host, permissions.Object, store.Object,
            NullLogger<ChatOrchestrator>.Instance);

        var tool = new SpawnAgentTool(
            effectiveGateway, permissions.Object, store.Object,
            () => host, () => parent, settings.Object, NullLoggerFactory.Instance);
        registry.Register(tool); // present in the parent surface, must vanish from the nested one

        return new Harness(tool, gateway, fake, parent, permissions);
    }

    // ---- (a) nested loop completes and returns the final text -------------

    [Fact]
    public async Task Nested_Run_Executes_Tool_And_Returns_Final_Answer()
    {
        var h = Create();
        // Nested iteration 1: call fake_tool; iteration 2: final answer.
        h.Gateway.Enqueue(ToolCallFrame("c1", "fake_tool", """{"path":"x"}"""), FinishToolCallsFrame, "[DONE]");
        h.Gateway.Enqueue(ContentFrame("The answer is 42."), StopFrame, "[DONE]");

        var result = await h.Tool.ExecuteAsync(Args("""{"task":"compute the answer"}"""), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("The answer is 42.");
        result.Content.Should().Contain("fake_tool (ok)"); // brief trace summary
        h.Fake.Executions.Should().Be(1);
        h.Gateway.Requests.Should().HaveCount(2);
    }

    [Fact]
    public async Task Nested_Run_Uses_Fresh_Context_With_SubAgent_System_Prompt()
    {
        var h = Create();
        h.Gateway.Enqueue(ContentFrame("done"), StopFrame, "[DONE]");

        await h.Tool.ExecuteAsync(Args("""{"task":"just answer"}"""), CancellationToken.None);

        var first = h.Gateway.Requests.Single();
        first.Model.Should().Be("test-model"); // settings default, not the parent's turn model
        first.Messages.Should().HaveCount(2);  // system + task only — no parent history
        first.Messages[0].Role.Should().Be("system");
        ((string)first.Messages[0].Content!).Should().Contain("sub-agent")
            .And.Contain("cannot delegate further");
        first.Messages[1].Role.Should().Be("user");
        first.Messages[1].Content.Should().Be("just answer");
    }

    [Fact]
    public async Task Missing_Task_Fails_Without_Calling_The_Gateway()
    {
        var h = Create();

        var result = await h.Tool.ExecuteAsync(Args("{}"), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("'task' is required");
        h.Gateway.Requests.Should().BeEmpty();
    }

    // ---- (b) recursion refused ---------------------------------------------

    [Fact]
    public async Task Nested_Tool_List_Excludes_Run_Subagent()
    {
        var h = Create();
        h.Gateway.Enqueue(ContentFrame("done"), StopFrame, "[DONE]");

        await h.Tool.ExecuteAsync(Args("""{"task":"t"}"""), CancellationToken.None);

        var names = h.Gateway.Requests.Single().Tools!.Select(t => t.Function.Name).ToList();
        names.Should().Contain("fake_tool");
        names.Should().NotContain("run_subagent");
    }

    [Fact]
    public async Task Explicit_Allowlist_Cannot_Reintroduce_Run_Subagent()
    {
        var h = Create();
        h.Gateway.Enqueue(ContentFrame("done"), StopFrame, "[DONE]");

        var result = await h.Tool.ExecuteAsync(
            Args("""{"task":"t","tools":["run_subagent"]}"""), CancellationToken.None);

        // The intersection is empty: the nested run gets NO tools at all.
        h.Gateway.Requests.Single().Tools.Should().BeNull();
        result.Success.Should().BeTrue();
    }

    [Fact]
    public async Task Allowlist_Restricts_Nested_Tools_To_The_Intersection()
    {
        var h = Create();
        h.Gateway.Enqueue(ContentFrame("done"), StopFrame, "[DONE]");

        await h.Tool.ExecuteAsync(
            Args("""{"task":"t","tools":["fake_tool","no_such_tool"]}"""), CancellationToken.None);

        h.Gateway.Requests.Single().Tools!.Select(t => t.Function.Name)
            .Should().ContainSingle().Which.Should().Be("fake_tool");
    }

    [Fact]
    public async Task Model_Requesting_Run_Subagent_By_Name_Gets_Unknown_Tool()
    {
        var h = Create();
        // The nested model tries to spawn recursively by name anyway.
        h.Gateway.Enqueue(
            ToolCallFrame("c1", "run_subagent", """{"task":"go deeper"}"""),
            FinishToolCallsFrame, "[DONE]");
        h.Gateway.Enqueue(ContentFrame("ok, stopping"), StopFrame, "[DONE]");

        var result = await h.Tool.ExecuteAsync(Args("""{"task":"t"}"""), CancellationToken.None);

        result.Success.Should().BeTrue();
        // Only the two nested turns hit the gateway — no third, deeper run.
        h.Gateway.Requests.Should().HaveCount(2);
        h.Gateway.Requests[1].Messages.Should().Contain(m =>
            m.Role == "tool" && ((string)m.Content!).Contains("unknown tool 'run_subagent'"));
    }

    // ---- (c) parent cancellation aborts the nested run ----------------------

    [Fact]
    public async Task Parent_Cancellation_Aborts_The_Nested_Run_Promptly()
    {
        var h = Create(gatewayOverride: new HangingGateway());
        using var cts = new CancellationTokenSource();

        var run = h.Tool.ExecuteAsync(Args("""{"task":"t"}"""), cts.Token);
        cts.CancelAfter(TimeSpan.FromMilliseconds(100));

        var act = async () => await run.WaitAsync(TimeSpan.FromSeconds(10));
        await act.Should().ThrowAsync<OperationCanceledException>();
    }

    // ---- (d) nested consent flows through the parent's handler --------------

    [Fact]
    public async Task Nested_Ask_Tool_Without_Consent_Handler_Is_Denied()
    {
        var h = Create(PermissionDecision.NeedsConsent); // parent has NO handler subscribed
        h.Gateway.Enqueue(ToolCallFrame("c1", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        h.Gateway.Enqueue(ContentFrame("understood"), StopFrame, "[DONE]");

        var result = await h.Tool.ExecuteAsync(Args("""{"task":"t"}"""), CancellationToken.None);

        h.Fake.Executions.Should().Be(0); // deny-by-default preserved in the nested run
        h.Gateway.Requests[1].Messages.Should().Contain(m =>
            m.Role == "tool" && ((string)m.Content!).Contains("denied permission"));
        result.Success.Should().BeTrue(); // the sub-agent still finished with a text answer
        result.Content.Should().Contain("fake_tool (failed)");
    }

    [Fact]
    public async Task Nested_Ask_Tool_Flows_Through_The_Parent_Consent_Handler()
    {
        var h = Create(PermissionDecision.NeedsConsent);
        ToolConsentRequest? seen = null;
        h.Parent.ConsentRequested += request =>
        {
            seen = request;
            return Task.FromResult(ToolConsentResponse.Once);
        };
        h.Gateway.Enqueue(ToolCallFrame("c1", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        h.Gateway.Enqueue(ContentFrame("done"), StopFrame, "[DONE]");

        var result = await h.Tool.ExecuteAsync(Args("""{"task":"t"}"""), CancellationToken.None);

        h.Fake.Executions.Should().Be(1); // granted via the parent's handler
        seen.Should().NotBeNull();
        seen!.ToolName.Should().Be("fake_tool");
        result.Success.Should().BeTrue();
        // Allow-once decisions land in the SAME shared permission service.
        h.Permissions.Verify(p => p.RecordSessionDecision(
            "fake_tool", It.IsAny<string>(), PermissionLevel.AllowOnce), Times.Once);
    }

    [Fact]
    public async Task Parent_Handler_Denial_Blocks_The_Nested_Tool()
    {
        var h = Create(PermissionDecision.NeedsConsent);
        h.Parent.ConsentRequested += _ => Task.FromResult(ToolConsentResponse.Deny);
        h.Gateway.Enqueue(ToolCallFrame("c1", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        h.Gateway.Enqueue(ContentFrame("ok"), StopFrame, "[DONE]");

        await h.Tool.ExecuteAsync(Args("""{"task":"t"}"""), CancellationToken.None);

        h.Fake.Executions.Should().Be(0);
    }

    // ---- (e) nested iteration cap; parent loop continues ---------------------

    [Fact]
    public async Task Nested_Iteration_Cap_Truncates_And_The_Parent_Loop_Continues()
    {
        var h = Create();

        // Parent iteration 1: the model delegates to run_subagent.
        h.Gateway.Enqueue(
            ToolCallFrame("p1", "run_subagent", """{"task":"loop forever"}"""),
            FinishToolCallsFrame, "[DONE]");
        // Nested run: a model that never finishes — exactly the nested cap's
        // worth of tool rounds get consumed before the cap fires.
        for (var i = 0; i < SpawnAgentTool.NestedMaxIterations; i++)
            h.Gateway.Enqueue(ToolCallFrame($"n{i}", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        // Parent iteration 2: consumes the truncation result and answers.
        h.Gateway.Enqueue(ContentFrame("Parent recovered."), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "delegate" } };
        var events = new List<AgentStreamEvent>();
        await foreach (var evt in h.Parent.RunAsync("m", messages, "conv-1"))
            events.Add(evt);

        // 1 parent + NestedMaxIterations nested + 1 parent.
        h.Gateway.Requests.Should().HaveCount(SpawnAgentTool.NestedMaxIterations + 2);
        h.Fake.Executions.Should().Be(SpawnAgentTool.NestedMaxIterations);

        var subResult = events.OfType<AgentToolResultEvent>().Single(e => e.ToolName == "run_subagent");
        subResult.Success.Should().BeFalse();
        subResult.FullResult.Should().Contain($"{SpawnAgentTool.NestedMaxIterations}-step");

        // The parent loop continued past the truncated delegate to a final answer.
        string.Concat(events.OfType<AgentContentDeltaEvent>().Select(e => e.Text))
            .Should().Contain("Parent recovered.");
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public void Nested_Caps_Are_Tighter_Than_The_Parent_Defaults()
    {
        SpawnAgentTool.NestedMaxIterations.Should().BeLessThan(ChatOrchestrator.DefaultMaxIterations);
        SpawnAgentTool.NestedMaxDuration.Should().BeLessThan(ChatOrchestrator.DefaultMaxDuration);
    }

    [Fact]
    public void Nested_Budget_Raised_For_Real_Delegated_Work()
    {
        // PART 6: a delegate can do a real build/fix or read-many-files loop.
        SpawnAgentTool.NestedMaxIterations.Should().BeGreaterThanOrEqualTo(25);
        SpawnAgentTool.NestedMaxDuration.Should().BeGreaterThanOrEqualTo(TimeSpan.FromMinutes(8));
    }
}
