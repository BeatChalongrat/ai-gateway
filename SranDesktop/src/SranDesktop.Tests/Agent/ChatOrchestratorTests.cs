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
using Xunit;

namespace SranDesktop.Tests.Agent;

public class ChatOrchestratorTests
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

    private static (ChatOrchestrator Orchestrator, ScriptedGateway Gateway, FakeTool Tool, Mock<IPermissionService> Permissions)
        Create(PermissionDecision decision = PermissionDecision.Auto)
    {
        var gateway = new ScriptedGateway();
        var tool = new FakeTool();

        var host = new Mock<IMcpToolHost>();
        host.Setup(h => h.Resolve("fake_tool")).Returns(tool);
        host.Setup(h => h.GetToolDefinitions()).Returns(new List<GatewayToolDefinition>
        {
            new() { Function = new GatewayFunctionDef
                { Name = tool.Name, Description = tool.Description, Parameters = tool.ParametersSchema } }
        });

        var permissions = new Mock<IPermissionService>();
        permissions.Setup(p => p.CheckAsync(It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(decision);

        var store = new Mock<IConversationStore>();

        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, store.Object,
            NullLogger<ChatOrchestrator>.Instance);
        return (orchestrator, gateway, tool, permissions);
    }

    private static async Task<List<AgentStreamEvent>> RunAsync(
        ChatOrchestrator orchestrator, List<GatewayMessage> messages)
    {
        var events = new List<AgentStreamEvent>();
        await foreach (var evt in orchestrator.RunAsync("gpt-4o-mini", messages, "conv-1"))
            events.Add(evt);
        return events;
    }

    [Fact]
    public async Task Plain_Answer_Streams_Content_And_Finishes()
    {
        var (orchestrator, gateway, tool, _) = Create();
        gateway.Enqueue(ContentFrame("Hello"), ContentFrame(" world"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "hi" } };
        var events = await RunAsync(orchestrator, messages);

        string.Concat(events.OfType<AgentContentDeltaEvent>().Select(e => e.Text))
            .Should().Be("Hello world");
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
        tool.Executions.Should().Be(0);
        gateway.Requests.Should().HaveCount(1);
        gateway.Requests[0].Tools.Should().NotBeNull();
    }

    [Fact]
    public async Task Tool_Call_Executes_And_Loops_Back()
    {
        var (orchestrator, gateway, tool, _) = Create();
        gateway.Enqueue(
            ToolCallFrame("call_1", "fake_tool", """{"path":"x"}"""),
            FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ContentFrame("Done!"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "run the tool" } };
        var events = await RunAsync(orchestrator, messages);

        tool.Executions.Should().Be(1);
        gateway.Requests.Should().HaveCount(2);

        // History: user, assistant(with tool_calls), tool result appended in place.
        messages.Should().HaveCount(3);
        messages[1].Role.Should().Be("assistant");
        messages[1].ToolCalls.Should().ContainSingle(tc => tc.Id == "call_1");
        messages[2].Role.Should().Be("tool");
        messages[2].ToolCallId.Should().Be("call_1");
        messages[2].Content.Should().Be("fake result");

        var result = events.OfType<AgentToolResultEvent>().Single();
        result.Success.Should().BeTrue();
        result.ToolName.Should().Be("fake_tool");
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public async Task Consent_Denied_Skips_Execution_And_Informs_Model()
    {
        var (orchestrator, gateway, tool, _) = Create(PermissionDecision.NeedsConsent);
        orchestrator.ConsentRequested += _ => Task.FromResult(ToolConsentResponse.Deny);
        gateway.Enqueue(ToolCallFrame("call_1", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ContentFrame("Understood."), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "go" } };
        var events = await RunAsync(orchestrator, messages);

        tool.Executions.Should().Be(0);
        messages.Should().Contain(m => m.Role == "tool" && ((string)m.Content!).Contains("denied permission"));
        events.OfType<AgentToolResultEvent>().Single().Success.Should().BeFalse();
        events.OfType<AgentToolConsentRequestEvent>().Should().ContainSingle();
    }

    [Fact]
    public async Task Consent_AllowAlways_Persists_Decision()
    {
        var (orchestrator, gateway, tool, permissions) = Create(PermissionDecision.NeedsConsent);
        orchestrator.ConsentRequested += _ => Task.FromResult(ToolConsentResponse.Always);
        gateway.Enqueue(ToolCallFrame("call_1", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        await RunAsync(orchestrator, new List<GatewayMessage> { new() { Role = "user", Content = "go" } });

        tool.Executions.Should().Be(1);
        permissions.Verify(p => p.PersistDecisionAsync(
            "fake_tool", It.IsAny<string>(), "allow_always", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task No_Consent_Handler_Defaults_To_Deny()
    {
        var (orchestrator, gateway, tool, _) = Create(PermissionDecision.NeedsConsent);
        gateway.Enqueue(ToolCallFrame("call_1", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        await RunAsync(orchestrator, new List<GatewayMessage> { new() { Role = "user", Content = "go" } });

        tool.Executions.Should().Be(0);
    }

    [Fact]
    public async Task Dlp_Blocked_Arguments_Are_Not_Executed()
    {
        var (orchestrator, gateway, tool, _) = Create();
        gateway.Enqueue(
            ToolCallFrame("call_1", "fake_tool",
                """{"_dlp_blocked": true, "reason": "unknown_tool_blocked_masked_args", "tool": "fake_tool"}"""),
            FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "go" } };
        var events = await RunAsync(orchestrator, messages);

        tool.Executions.Should().Be(0);
        messages.Should().Contain(m => m.Role == "tool" && ((string)m.Content!).Contains("DLP policy"));
        events.OfType<AgentToolResultEvent>().Single().ResultPreview.Should().Contain("DLP");
    }

    [Fact]
    public async Task Max_Iterations_Cap_Stops_The_Loop()
    {
        var (orchestrator, gateway, tool, _) = Create();
        for (var i = 0; i < 30; i++)
            gateway.Enqueue(ToolCallFrame($"call_{i}", "fake_tool"), FinishToolCallsFrame, "[DONE]");

        var events = await RunAsync(orchestrator,
            new List<GatewayMessage> { new() { Role = "user", Content = "loop forever" } });

        gateway.Requests.Should().HaveCount(25);
        tool.Executions.Should().Be(25);
        events.Last().Should().BeOfType<AgentErrorEvent>()
            .Which.Message.Should().Contain("25-step");
    }

    [Fact]
    public async Task Gateway_Error_Envelope_Stops_With_Error()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue("""{"error":{"code":"authentication_error","message":"Invalid token"}}""");

        var events = await RunAsync(orchestrator,
            new List<GatewayMessage> { new() { Role = "user", Content = "hi" } });

        events.OfType<AgentErrorEvent>().Should().ContainSingle()
            .Which.Message.Should().Be("Invalid token");
    }

    [Fact]
    public async Task Unknown_Tool_Reports_Error_Result()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ToolCallFrame("call_1", "not_a_tool"), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "go" } };
        await RunAsync(orchestrator, messages);

        messages.Should().Contain(m => m.Role == "tool" && ((string)m.Content!).Contains("unknown tool"));
    }

    private const string ContextErrorFrame =
        """{"error":{"code":"context_length_exceeded","message":"The request exceeds the model's context window."}}""";

    [Fact]
    public async Task Every_Request_Carries_MaxTokens()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ToolCallFrame("call_1", "fake_tool"), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ContentFrame("Done"), StopFrame, "[DONE]");

        await RunAsync(orchestrator,
            new List<GatewayMessage> { new() { Role = "user", Content = "go" } });

        gateway.Requests.Should().HaveCount(2)
            .And.OnlyContain(r => r.MaxTokens == 8192);
    }

    [Fact]
    public async Task Context_Error_Trims_And_Retries_Once_Then_Succeeds()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ContextErrorFrame);
        gateway.Enqueue(ContentFrame("Recovered"), StopFrame, "[DONE]");

        var events = await RunAsync(orchestrator,
            new List<GatewayMessage> { new() { Role = "user", Content = "hi" } });

        gateway.Requests.Should().HaveCount(2);
        events.OfType<AgentContextTrimmedEvent>().Should().ContainSingle();
        events.OfType<AgentErrorEvent>().Should().BeEmpty();
        string.Concat(events.OfType<AgentContentDeltaEvent>().Select(e => e.Text))
            .Should().Be("Recovered");
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
        // The retry replays the SAME step — it must not consume an iteration.
        events.OfType<AgentStatusEvent>().Should().HaveCount(2)
            .And.OnlyContain(s => s.Iteration == 1);
    }

    [Fact]
    public async Task Second_Context_Error_Surfaces_With_New_Chat_Suggestion()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ContextErrorFrame);
        gateway.Enqueue(ContextErrorFrame);

        var events = await RunAsync(orchestrator,
            new List<GatewayMessage> { new() { Role = "user", Content = "hi" } });

        gateway.Requests.Should().HaveCount(2); // exactly one retry, no loop
        events.OfType<AgentContextTrimmedEvent>().Should().ContainSingle();
        var error = events.OfType<AgentErrorEvent>().Single();
        error.Code.Should().Be("context_length_exceeded");
        error.Message.Should().Contain("start a new chat");
        events.Last().Should().Be(error);
    }

    [Fact]
    public async Task Non_Context_Error_Is_Not_Retried()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue("""{"error":{"code":"upstream_bad_response","message":"bad request"}}""");

        var events = await RunAsync(orchestrator,
            new List<GatewayMessage> { new() { Role = "user", Content = "hi" } });

        gateway.Requests.Should().HaveCount(1);
        events.OfType<AgentContextTrimmedEvent>().Should().BeEmpty();
        events.OfType<AgentErrorEvent>().Single().Code.Should().Be("upstream_bad_response");
    }

    [Fact]
    public async Task Stream_Ending_Without_Finish_Reason_Still_Executes_Tools()
    {
        var (orchestrator, gateway, tool, _) = Create();
        // Connection closes after the tool-call delta: no finish_reason, no [DONE].
        gateway.Enqueue(ToolCallFrame("call_1", "fake_tool", """{"path":"x"}"""));
        gateway.Enqueue(ContentFrame("Done!"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "go" } };
        var events = await RunAsync(orchestrator, messages);

        tool.Executions.Should().Be(1);
        gateway.Requests.Should().HaveCount(2);
        messages.Should().Contain(m => m.Role == "tool" && m.ToolCallId == "call_1");
        events.OfType<AgentToolResultEvent>().Single().Success.Should().BeTrue();
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
    }

    private static async Task RunToEndAsync(
        ChatOrchestrator orchestrator, List<GatewayMessage> messages, int contextWindow)
    {
        await foreach (var _ in orchestrator.RunAsync(
                           "gpt-4o-mini", messages, "conv-1", contextWindow))
        {
        }
    }

    [Fact]
    public async Task Main_Loop_Keeps_Four_Screenshots_For_A_Large_Window()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ContentFrame("ok"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "look" } };
        for (var i = 1; i <= 6; i++)
            messages.Add(ScreenshotMessage.Create("computer_screenshot", $"data:image/png;base64,IMG{i}"));

        await RunToEndAsync(orchestrator, messages, contextWindow: 200_000);

        // KeepLatestFor(200_000) == 4 → two oldest of six pruned, four survive.
        messages.Count(ScreenshotMessage.IsScreenshotMessage).Should().Be(4);
    }

    [Fact]
    public async Task Main_Loop_Keeps_Two_Screenshots_For_A_Small_Window()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ContentFrame("ok"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "look" } };
        for (var i = 1; i <= 6; i++)
            messages.Add(ScreenshotMessage.Create("computer_screenshot", $"data:image/png;base64,IMG{i}"));

        await RunToEndAsync(orchestrator, messages, contextWindow: 40_000);

        // KeepLatestFor(40_000) == 2 → the same input keeps fewer on a small window.
        messages.Count(ScreenshotMessage.IsScreenshotMessage).Should().Be(2);
    }

    [Fact]
    public async Task Context_Error_Recovery_Still_Prunes_To_A_Single_Screenshot()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ContextErrorFrame);
        gateway.Enqueue(ContentFrame("Recovered"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "hi" } };
        for (var i = 1; i <= 5; i++)
            messages.Add(ScreenshotMessage.Create("computer_screenshot", $"data:image/png;base64,IMG{i}"));

        // Large window so the main loop would keep four — the recovery path must
        // still clamp to one screenshot under context pressure.
        await RunToEndAsync(orchestrator, messages, contextWindow: 200_000);

        messages.Count(ScreenshotMessage.IsScreenshotMessage).Should().Be(1);
    }

    [Fact]
    public async Task Cancellation_Propagates()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ContentFrame("a"), ContentFrame("b"), StopFrame, "[DONE]");
        using var cts = new CancellationTokenSource();

        var act = async () =>
        {
            await foreach (var _ in orchestrator.RunAsync("m",
                               new List<GatewayMessage> { new() { Role = "user", Content = "x" } },
                               "conv-1", ct: cts.Token))
            {
                cts.Cancel();
            }
        };

        await act.Should().ThrowAsync<OperationCanceledException>();
    }
}
