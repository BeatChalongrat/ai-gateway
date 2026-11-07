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

/// <summary>FIX-F orchestrator plumbing: an out-of-scope read flips an Auto
/// decision to consent — but an explicit stored decision, or a handler-less
/// run (run_subagent fan-out), keeps the old behavior.</summary>
public sealed class ReadScopeGateTests
{
    private sealed class ScriptedGateway : IGatewayApiClient
    {
        private readonly Queue<string[]> _responses = new();
        public void Enqueue(params string[] frames) => _responses.Enqueue(frames);

        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            var frames = _responses.Count > 0
                ? _responses.Dequeue()
                : new[] { """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""" };
            foreach (var frame in frames)
            {
                yield return new SseFrame(null, frame);
                await Task.Yield();
            }
        }

        public Task<GatewaySearchResponse> SearchAsync(
            string query, int maxResults = 5, CancellationToken ct = default) =>
            Task.FromResult(new GatewaySearchResponse(query, "fake", 0,
                new List<GatewaySearchResultItem>()));
    }

    private sealed class ReadTool : IMcpTool
    {
        public string Name => "read_file";
        public string Description => "read";
        public JsonElement ParametersSchema => JsonDocument.Parse("{}").RootElement.Clone();
        public ToolCategory Category => ToolCategory.FileSystem;
        public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;
        public int Executions;
        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
        {
            Executions++;
            return Task.FromResult(ToolResult.Ok("file contents"));
        }
    }

    private sealed class AlwaysOutsideGuard : ISensitiveReadGuard
    {
        public bool RequiresConsent(IMcpTool tool, string argumentsJson) => true;
    }

    private const string StopFrame =
        """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""";
    private const string FinishToolCallsFrame =
        """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""";

    private static string ReadFileCallFrame =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c1\"," +
        "\"type\":\"function\",\"function\":{\"name\":\"read_file\"," +
        "\"arguments\":\"{\\\"path\\\":\\\"C:\\\\\\\\Users\\\\\\\\x\\\\\\\\.ssh\\\\\\\\id_rsa\\\"}\"}}]},\"finish_reason\":null}]}";

    private static (ChatOrchestrator Orchestrator, ScriptedGateway Gateway, ReadTool Tool, Mock<IPermissionService> Permissions)
        Create(bool storedDecision)
    {
        var gateway = new ScriptedGateway();
        var tool = new ReadTool();

        var host = new Mock<IMcpToolHost>();
        host.Setup(h => h.Resolve("read_file")).Returns(tool);
        host.Setup(h => h.GetToolDefinitions()).Returns(new List<GatewayToolDefinition>
        {
            new() { Function = new GatewayFunctionDef
                { Name = tool.Name, Description = tool.Description, Parameters = tool.ParametersSchema } }
        });

        var permissions = new Mock<IPermissionService>();
        permissions.Setup(p => p.CheckAsync(It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        permissions.Setup(p => p.HasStoredDecisionAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(storedDecision);

        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, new Mock<IConversationStore>().Object,
            NullLogger<ChatOrchestrator>.Instance,
            readGuard: new AlwaysOutsideGuard());
        return (orchestrator, gateway, tool, permissions);
    }

    private static async Task<List<AgentStreamEvent>> RunAsync(ChatOrchestrator orchestrator)
    {
        var events = new List<AgentStreamEvent>();
        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "hi" } };
        await foreach (var evt in orchestrator.RunAsync("gpt-4o-mini", messages, "conv-1"))
            events.Add(evt);
        return events;
    }

    [Fact]
    public async Task OutOfScope_Read_Asks_For_Consent_When_A_Handler_Exists()
    {
        var (orchestrator, gateway, tool, _) = Create(storedDecision: false);
        gateway.Enqueue(ReadFileCallFrame, FinishToolCallsFrame);
        gateway.Enqueue(StopFrame, "[DONE]");

        var consentRequests = 0;
        orchestrator.ConsentRequested += request =>
        {
            consentRequests++;
            request.ToolName.Should().Be("read_file");
            return Task.FromResult(new ToolConsentResponse(Denied: false, AllowOnce: true, AllowAlways: false));
        };

        var events = await RunAsync(orchestrator);

        consentRequests.Should().Be(1, "an out-of-scope read must pause for the user");
        tool.Executions.Should().Be(1, "the user allowed it once");
        events.OfType<AgentErrorEvent>().Should().BeEmpty();
    }

    [Fact]
    public async Task A_Stored_Always_Allow_Wins_Over_The_Gate()
    {
        var (orchestrator, gateway, tool, _) = Create(storedDecision: true);
        gateway.Enqueue(ReadFileCallFrame, FinishToolCallsFrame);
        gateway.Enqueue(StopFrame, "[DONE]");

        var consentRequests = 0;
        orchestrator.ConsentRequested += _ =>
        {
            consentRequests++;
            return Task.FromResult(ToolConsentResponse.Deny);
        };

        await RunAsync(orchestrator);

        consentRequests.Should().Be(0, "the user already said Always Allow for this scope");
        tool.Executions.Should().Be(1);
    }

    [Fact]
    public async Task HandlerLess_Runs_Keep_The_Old_Silent_Read_Behavior()
    {
        // run_subagent fan-out attaches no consent handler; flipping to
        // NeedsConsent there would silently deny every read.
        var (orchestrator, gateway, tool, _) = Create(storedDecision: false);
        gateway.Enqueue(ReadFileCallFrame, FinishToolCallsFrame);
        gateway.Enqueue(StopFrame, "[DONE]");

        await RunAsync(orchestrator);

        tool.Executions.Should().Be(1, "no handler → the gate must not turn reads into denials");
    }
}
