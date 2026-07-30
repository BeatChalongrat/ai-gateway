using System.Runtime.CompilerServices;
using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
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

    private sealed class UnverifiedPerceptualTool : IMcpTool, IPerceptualActionTool
    {
        public string Name => "perceptual_tool";
        public string Description => "A state-changing tool with an unchanged post-state.";
        public JsonElement ParametersSchema =>
            JsonSerializer.SerializeToElement(new { type = "object" });
        public ToolCategory Category => ToolCategory.Screen;
        public PermissionLevel DefaultPermission => PermissionLevel.Ask;

        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
            Task.FromResult(ToolResult.Verified("native dispatch completed", ["native=true"]));

        public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
            new(
                InteractionActionKind.Click,
                "fixture:button",
                "label changes",
                SideEffectClass.ReversibleLowRisk,
                Reversible: true,
                Idempotent: false,
                RequiresConfirmation: true,
                Timeout: TimeSpan.FromSeconds(1),
                MaximumAttempts: 1);

        public Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
            JsonElement arguments,
            CancellationToken ct) =>
            Task.FromResult(Snapshot("before", 1));

        public Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
            JsonElement arguments,
            ToolResult result,
            InteractionObservationSnapshot before,
            CancellationToken ct) =>
            Task.FromResult(Snapshot("after", 2));

        public ToolContractVerification VerifyAction(
            JsonElement arguments,
            ToolResult result,
            InteractionObservationSnapshot before,
            InteractionObservationSnapshot after) =>
            new(
                VerificationDisposition.Inconclusive,
                ["independent_state_changed=false"],
                "The independent post-state did not change.");

        private static InteractionObservationSnapshot Snapshot(
            string observationId,
            long version) =>
            new(
                observationId,
                "run-1",
                "step-1",
                InteractionObservationSource.Desktop,
                version,
                DateTimeOffset.UtcNow,
                1,
                InteractionCoordinateSpace.PhysicalScreenPixels,
                [],
                null,
                [],
                null,
                null,
                null,
                null,
                "unchanged-state",
                ObservationTrust.ToolObservation,
                SensitiveDataClassification.Confidential,
                "metadata_only",
                true,
                []);
    }

    private sealed class SensitiveTool(string content) : IMcpTool, ISensitiveDataTool
    {
        public string Name => "sensitive_tool";
        public string Description => "Reads local sensitive data";
        public JsonElement ParametersSchema => JsonDocument.Parse("{}").RootElement.Clone();
        public ToolCategory Category => ToolCategory.Clipboard;
        public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;
        public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
            "Clipboard.ReadLocal",
            "Clipboard.SendToModel",
            "clipboard",
            "unknown",
            "configured model provider",
            ExternalTransmission: true,
            AlwaysRequireConfirmation: true);
        public int Executions;

        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
        {
            Executions++;
            return Task.FromResult(ToolResult.Ok(content));
        }
    }

    private sealed class ScreenCaptureTool : IMcpTool, ISensitiveDataTool
    {
        public string Name => "screen_capture_test";
        public string Description => "Captures a local screen image";
        public JsonElement ParametersSchema => JsonDocument.Parse("{}").RootElement.Clone();
        public ToolCategory Category => ToolCategory.Screen;
        public PermissionLevel DefaultPermission => PermissionLevel.Ask;
        public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
            "Screen.CaptureLocal",
            "Screen.SendToModel",
            "screen_capture",
            "unknown",
            "configured model provider",
            ExternalTransmission: true,
            AlwaysRequireConfirmation: true,
            AllowPersistence: false);

        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
            Task.FromResult(ToolResult.OkWithImage(
                "capture metadata",
                "data:image/png;base64,AAAA"));
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
        Create(PermissionDecision decision = PermissionDecision.Auto, int? maxIterations = null)
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
            NullLogger<ChatOrchestrator>.Instance,
            maxIterations ?? ChatOrchestrator.DefaultMaxIterations);
        return (orchestrator, gateway, tool, permissions);
    }

    private static (ChatOrchestrator Orchestrator, ScriptedGateway Gateway,
        SensitiveTool Tool, Mock<IPermissionService> Permissions)
        CreateSensitive(string result)
    {
        var gateway = new ScriptedGateway();
        var tool = new SensitiveTool(result);
        var host = new Mock<IMcpToolHost>();
        host.Setup(h => h.Resolve(tool.Name)).Returns(tool);
        host.Setup(h => h.GetToolDefinitions()).Returns(
        [
            new GatewayToolDefinition
            {
                Function = new GatewayFunctionDef
                {
                    Name = tool.Name,
                    Description = tool.Description,
                    Parameters = tool.ParametersSchema
                }
            }
        ]);
        var permissions = new Mock<IPermissionService>();
        permissions.Setup(p => p.CheckAsync(
                It.IsAny<IMcpTool>(),
                It.IsAny<string>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        var store = new Mock<IConversationStore>();
        var orchestrator = new ChatOrchestrator(
            gateway,
            host.Object,
            permissions.Object,
            store.Object,
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
    public async Task Perceptual_Action_Without_Postcondition_Is_Not_Presented_As_Success()
    {
        var gateway = new ScriptedGateway();
        var tool = new UnverifiedPerceptualTool();
        var host = new Mock<IMcpToolHost>();
        host.Setup(h => h.Resolve(tool.Name)).Returns(tool);
        host.Setup(h => h.GetToolDefinitions()).Returns(
        [
            new GatewayToolDefinition
            {
                Function = new GatewayFunctionDef
                {
                    Name = tool.Name,
                    Description = tool.Description,
                    Parameters = tool.ParametersSchema
                }
            }
        ]);
        var permissions = new Mock<IPermissionService>();
        permissions.Setup(p => p.CheckAsync(
                tool,
                It.IsAny<string>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        var orchestrator = new ChatOrchestrator(
            gateway,
            host.Object,
            permissions.Object,
            new Mock<IConversationStore>().Object,
            NullLogger<ChatOrchestrator>.Instance,
            maxIterations: 5);
        gateway.Enqueue(
            ToolCallFrame("call_perception", tool.Name),
            FinishToolCallsFrame,
            "[DONE]");
        gateway.Enqueue(ContentFrame("Done"), StopFrame, "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        var events = await RunAsync(
            orchestrator,
            [new GatewayMessage { Role = "user", Content = "click the fixture button" }]);

        var toolEvent = events.OfType<AgentToolResultEvent>().Single();
        toolEvent.Success.Should().BeFalse();
        toolEvent.FullResult.Should().Contain("The independent post-state did not change");
        toolEvent.FullResult.Should().Contain(ChatOrchestrator.ToolEffectMarker);
        ChatOrchestrator.HasPendingUnverifiedAction(
                [new GatewayMessage { Role = "tool", Content = toolEvent.FullResult }])
            .Should().BeTrue();
        events.OfType<AgentContentDeltaEvent>()
            .Should().NotContain(evt => evt.Text.Contains("Done", StringComparison.Ordinal));
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
    public async Task Sensitive_Data_Tool_Overrides_Auto_And_Cannot_Persist_Always()
    {
        var (orchestrator, gateway, tool, permissions) = CreateSensitive("ordinary clipboard text");
        var requests = new List<ToolConsentRequest>();
        orchestrator.ConsentRequested += seen =>
        {
            requests.Add(seen);
            return Task.FromResult(ToolConsentResponse.Always);
        };
        gateway.Enqueue(
            ToolCallFrame("call_sensitive", tool.Name),
            FinishToolCallsFrame,
            "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        await RunAsync(
            orchestrator,
            [new GatewayMessage { Role = "user", Content = "read clipboard" }]);

        tool.Executions.Should().Be(1);
        requests.Should().HaveCount(2);
        requests[0].Action.Should().Be("Clipboard.ReadLocal");
        requests[0].ExternalTransmission.Should().BeFalse();
        requests[0].Destination.Should().Be("local device");
        requests[1].Action.Should().Be("Clipboard.SendToModel");
        requests[1].AlwaysRequireConfirmation.Should().BeTrue();
        requests[1].ExternalTransmission.Should().BeTrue();
        requests[1].Destination.Should().Be("configured model provider");
        permissions.Verify(p => p.PersistDecisionAsync(
            It.IsAny<string>(),
            It.IsAny<string>(),
            It.IsAny<string>(),
            It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Secret_Local_Data_Is_Discarded_When_Transmission_Consent_Is_Denied()
    {
        const string secret = "ghp_1234567890abcdefghijklmnop";
        var (orchestrator, gateway, tool, _) = CreateSensitive(secret);
        var requests = new List<ToolConsentRequest>();
        orchestrator.ConsentRequested += request =>
        {
            requests.Add(request);
            return Task.FromResult(
                requests.Count == 1 ? ToolConsentResponse.Once : ToolConsentResponse.Deny);
        };
        gateway.Enqueue(
            ToolCallFrame("call_secret", tool.Name),
            FinishToolCallsFrame,
            "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "read clipboard" }
        };

        await RunAsync(orchestrator, messages);

        requests.Should().HaveCount(2);
        requests[1].Sensitivity.Should().Be("secret");
        requests[1].ArgumentsJson.Should().Be("{}");
        var resultMessage = messages.Single(message => message.Role == "tool");
        resultMessage.Content!.ToString().Should().Contain("transmission was denied");
        resultMessage.Content!.ToString().Should().NotContain(secret);
    }

    [Fact]
    public async Task Screen_Image_Is_Discarded_When_Separate_Transmission_Is_Denied()
    {
        var gateway = new ScriptedGateway();
        var tool = new ScreenCaptureTool();
        var host = new Mock<IMcpToolHost>();
        host.Setup(item => item.Resolve(tool.Name)).Returns(tool);
        host.Setup(item => item.GetToolDefinitions()).Returns(
        [
            new GatewayToolDefinition
            {
                Function = new GatewayFunctionDef
                {
                    Name = tool.Name,
                    Description = tool.Description,
                    Parameters = tool.ParametersSchema
                }
            }
        ]);
        var permissions = new Mock<IPermissionService>();
        permissions.Setup(item => item.CheckAsync(
                It.IsAny<IMcpTool>(),
                It.IsAny<string>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        var orchestrator = new ChatOrchestrator(
            gateway,
            host.Object,
            permissions.Object,
            Mock.Of<IConversationStore>(),
            NullLogger<ChatOrchestrator>.Instance);
        var requests = new List<ToolConsentRequest>();
        orchestrator.ConsentRequested += request =>
        {
            requests.Add(request);
            return Task.FromResult(
                requests.Count == 1 ? ToolConsentResponse.Once : ToolConsentResponse.Deny);
        };
        gateway.Enqueue(
            ToolCallFrame("call_screen", tool.Name),
            FinishToolCallsFrame,
            "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        var events = await RunAsync(
            orchestrator,
            [new GatewayMessage { Role = "user", Content = "capture screen" }]);

        requests.Should().HaveCount(2);
        requests[0].Action.Should().Be("Screen.CaptureLocal");
        requests[0].ExternalTransmission.Should().BeFalse();
        requests[1].Action.Should().Be("Screen.SendToModel");
        requests[1].ExternalTransmission.Should().BeTrue();
        var result = events.OfType<AgentToolResultEvent>().Single();
        result.Success.Should().BeFalse();
        result.ImageDataUri.Should().BeNull();
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
    public async Task Max_Iterations_Cap_Pauses_Instead_Of_Abandoning()
    {
        var (orchestrator, gateway, tool, _) = Create(maxIterations: 5);
        for (var i = 0; i < 10; i++)
            gateway.Enqueue(ToolCallFrame($"call_{i}", "fake_tool"), FinishToolCallsFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "loop forever" } };
        var events = await RunAsync(orchestrator, messages);

        gateway.Requests.Should().HaveCount(5);
        tool.Executions.Should().Be(5);
        // PART 1/6: the cap is a resumable checkpoint, not a terminal error.
        events.OfType<AgentErrorEvent>().Should().BeEmpty();
        events.Last().Should().BeOfType<AgentPausedEvent>()
            .Which.Reason.Should().Contain("5-step");
        // The accumulated tool history is intact in the caller's list, ending on
        // a tool result — a valid chain the loop can be re-entered with.
        messages.Should().HaveCountGreaterThan(5);
        messages[^1].Role.Should().Be("tool");
    }

    [Fact]
    public async Task Paused_Turn_Resumes_And_Completes_Across_A_Continue()
    {
        var (orchestrator, gateway, tool, _) = Create(maxIterations: 5);
        // 5 tool iterations exhaust the cap → pause with history intact.
        for (var i = 0; i < 5; i++)
            gateway.Enqueue(ToolCallFrame($"call_{i}", "fake_tool"), FinishToolCallsFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "long task" } };
        var first = await RunAsync(orchestrator, messages);
        first.Last().Should().BeOfType<AgentPausedEvent>();

        // Continue: re-enter the loop with the SAME accumulated chain. This time
        // the model produces a final answer instead of another tool call.
        gateway.Enqueue(ContentFrame("All done"), StopFrame, "[DONE]");
        var second = await RunAsync(orchestrator, messages);

        second.Last().Should().BeOfType<AgentStreamDoneEvent>();
        string.Concat(second.OfType<AgentContentDeltaEvent>().Select(e => e.Text))
            .Should().Be("All done");
        // The resumed request carried the full prior tool history.
        gateway.Requests.Last().Messages.Count(m => m.Role == "tool").Should().Be(5);
    }

    [Fact]
    public void Default_Iteration_Budget_Is_Raised_For_Real_Multi_Step_Work()
    {
        // PART 6: a much higher soft ceiling (the cap is now resumable).
        ChatOrchestrator.DefaultMaxIterations.Should().BeGreaterThanOrEqualTo(100);
        ChatOrchestrator.DefaultMaxDuration.Should().BeGreaterThanOrEqualTo(TimeSpan.FromMinutes(20));
    }

    [Fact]
    public async Task Time_Limit_Zero_Pauses_Before_Any_Request()
    {
        var gateway = new ScriptedGateway();
        var host = new Mock<IMcpToolHost>();
        host.Setup(h => h.GetToolDefinitions()).Returns(new List<GatewayToolDefinition>());
        var permissions = new Mock<IPermissionService>();
        var store = new Mock<IConversationStore>();
        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, store.Object,
            NullLogger<ChatOrchestrator>.Instance, maxIterations: 25, maxDuration: TimeSpan.Zero);

        var events = await RunAsync(orchestrator,
            new List<GatewayMessage> { new() { Role = "user", Content = "hi" } });

        gateway.Requests.Should().BeEmpty();
        events.Should().ContainSingle().Which.Should().BeOfType<AgentPausedEvent>();
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

    [Fact]
    public async Task Final_Response_Does_Not_Claim_Success_While_Action_Is_Unverified()
    {
        var (orchestrator, gateway, _, _) = Create();
        gateway.Enqueue(ContentFrame("Microphone muted successfully."), StopFrame, "[DONE]");
        var action = ComputerAction(taskCompleted: false);
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "mute me" },
            new()
            {
                Role = "tool",
                ToolCallId = "click-1",
                Content = ChatOrchestrator.ComputerActionMarker + JsonSerializer.Serialize(action)
            }
        };

        var events = await RunAsync(orchestrator, messages);
        var visible = string.Concat(
            events.OfType<AgentContentDeltaEvent>().Select(e => e.Text));

        visible.Should().Be(ChatOrchestrator.UnverifiedActionMessage);
        visible.Should().NotContain("muted successfully");
    }

    [Fact]
    public void Completed_Action_Clears_Unverified_Final_Response_Guard()
    {
        var messages = new[]
        {
            new GatewayMessage
            {
                Role = "tool",
                Content = ChatOrchestrator.ComputerActionMarker +
                          JsonSerializer.Serialize(ComputerAction(taskCompleted: true))
            }
        };

        ChatOrchestrator.HasPendingUnverifiedAction(messages).Should().BeFalse();
    }

    private static ComputerActionResult ComputerAction(bool taskCompleted) =>
        new(
            ActionRequested: true,
            TargetResolved: true,
            TargetValidated: true,
            ExecutionAttempted: true,
            OsInputAccepted: true,
            EffectVerified: taskCompleted,
            TaskCompleted: taskCompleted,
            FailureCode: taskCompleted ? null : "EffectNotVerified",
            FailureReason: null,
            Retryable: !taskCompleted,
            Evidence: Array.Empty<string>(),
            CaptureBeforeId: "before",
            CaptureAfterId: taskCompleted ? "after" : null,
            WindowHandle: 10,
            ProcessId: 42,
            ExecutedX: 10,
            ExecutedY: 20,
            TargetX: 0,
            TargetY: 0,
            TargetWidth: 20,
            TargetHeight: 40,
            DurationMs: 10);
}
