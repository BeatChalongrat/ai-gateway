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

public class RunPythonToolTests
{
    private static JsonElement Args(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private static CodeExecutionResult Result(
        int exit = 0, string stdout = "", string stderr = "",
        List<CodeExecutionImage>? images = null, List<CodeExecutionFile>? files = null) =>
        new(exit, stdout, stderr, false, false, images, files);

    // ---- (a) consent posture -----------------------------------------------

    [Fact]
    public void Declares_Ask_Permission_And_Network_Category()
    {
        var tool = new RunPythonTool(Mock.Of<IChatEdgeApiClient>());
        tool.Name.Should().Be("run_python");
        tool.DefaultPermission.Should().Be(PermissionLevel.Ask);
        tool.Category.Should().Be(ToolCategory.Network);
    }

    // ---- (b) payload + result shaping --------------------------------------

    [Fact]
    public async Task Posts_Code_And_File_Ids_And_Surfaces_Stdout_And_Image()
    {
        var mock = new Mock<IChatEdgeApiClient>();
        mock.Setup(c => c.RunPythonAsync(
                It.IsAny<string>(), It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Result(
                exit: 0, stdout: "hello\n",
                images: new List<CodeExecutionImage> { new("chart.png", "data:image/png;base64,AAA") }));

        var tool = new RunPythonTool(mock.Object);
        var result = await tool.ExecuteAsync(
            Args("""{"code":"print('hello')","files":["f1","f2"]}"""), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("Exit code: 0").And.Contain("hello").And.Contain("chart.png");
        result.ImageDataUri.Should().Be("data:image/png;base64,AAA"); // surfaced for vision

        mock.Verify(c => c.RunPythonAsync(
            "print('hello')",
            It.Is<IReadOnlyList<string>>(l => l.Count == 2 && l[0] == "f1" && l[1] == "f2"),
            It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Missing_Code_Fails_Without_Calling_The_Server()
    {
        var mock = new Mock<IChatEdgeApiClient>();
        var tool = new RunPythonTool(mock.Object);

        var result = await tool.ExecuteAsync(Args("{}"), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("'code' is required");
        mock.Verify(c => c.RunPythonAsync(
            It.IsAny<string>(), It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Surfaces_Stderr_On_NonZero_Exit()
    {
        var mock = new Mock<IChatEdgeApiClient>();
        mock.Setup(c => c.RunPythonAsync(
                It.IsAny<string>(), It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Result(exit: 1, stderr: "Traceback: boom"));

        var tool = new RunPythonTool(mock.Object);
        var result = await tool.ExecuteAsync(Args("""{"code":"raise SystemExit(1)"}"""), CancellationToken.None);

        result.Success.Should().BeTrue(); // a non-zero exit is still a completed run
        result.Content.Should().Contain("Exit code: 1").And.Contain("boom");
    }

    // ---- (c) consent flow: Ask + deny-without-handler (Phase 12 F) ----------

    private sealed class ScriptedGateway : IGatewayApiClient
    {
        private readonly Queue<string[]> _responses = new();
        public readonly List<GatewayCompletionRequest> Requests = new();
        public void Enqueue(params string[] frames) => _responses.Enqueue(frames);

        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request, [EnumeratorCancellation] CancellationToken ct = default)
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

        public Task<GatewaySearchResponse> SearchAsync(string query, int maxResults = 5, CancellationToken ct = default) =>
            Task.FromResult(new GatewaySearchResponse(query, "fake", 0, new List<GatewaySearchResultItem>()));
    }

    private const string StopFrame = """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""";
    private const string FinishToolCallsFrame = """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""";

    private static string ContentFrame(string text) =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"content\":\"" + text + "\"},\"finish_reason\":null}]}";

    private static string ToolCallFrame(string id, string name, string args) =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"" + id +
        "\",\"type\":\"function\",\"function\":{\"name\":\"" + name +
        "\",\"arguments\":\"" + args.Replace("\\", "\\\\").Replace("\"", "\\\"") +
        "\"}}]},\"finish_reason\":null}]}";

    [Fact]
    public async Task Ask_Tool_Without_Consent_Handler_Is_Denied_And_Server_Is_Never_Called()
    {
        var client = new Mock<IChatEdgeApiClient>();
        var gateway = new ScriptedGateway();
        // Turn 1: the model calls run_python. Turn 2: it wraps up.
        gateway.Enqueue(ToolCallFrame("c1", "run_python", """{"code":"print(1)"}"""), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ContentFrame("ok"), StopFrame, "[DONE]");

        var registry = new McpToolRegistry();
        registry.Register(new RunPythonTool(client.Object));
        var host = new McpToolHost(registry);

        // Real PermissionService fallback: an Ask tool with no persisted/session
        // decision resolves to NeedsConsent.
        var permissions = new Mock<IPermissionService>();
        permissions.Setup(p => p.CheckAsync(It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.NeedsConsent);
        var store = new Mock<IConversationStore>();

        // No ConsentHandler subscribed → deny-by-default (headless).
        var orchestrator = new ChatOrchestrator(
            gateway, host, permissions.Object, store.Object, NullLogger<ChatOrchestrator>.Instance);

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "run some python" } };
        await foreach (var _ in orchestrator.RunAsync("m", messages, "conv-1")) { }

        // The sandbox endpoint was NEVER hit — consent gated it.
        client.Verify(c => c.RunPythonAsync(
            It.IsAny<string>(), It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()), Times.Never);
        // The model saw a denial tool result on its follow-up request.
        gateway.Requests.Should().HaveCount(2);
        gateway.Requests[1].Messages.Should().Contain(m =>
            m.Role == "tool" && ((string)m.Content!).Contains("denied permission"));
    }
}
