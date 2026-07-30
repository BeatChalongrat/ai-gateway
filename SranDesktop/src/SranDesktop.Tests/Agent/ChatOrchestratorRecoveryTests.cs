using System.Runtime.CompilerServices;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>R1: when the model stops calling tools but the durable task is NOT complete
/// and still recoverable, the loop feeds a bounded nudge back in so the agent tries
/// again (instead of giving up after one or two failed actions). The ceiling and the
/// reset-on-real-tool-call keep it from looping forever, and completion stays
/// authoritative — success is never claimed while the task is incomplete.</summary>
public sealed class ChatOrchestratorRecoveryTests
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

    private const string StopFrame =
        """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""";

    private static string ContentFrame(string text) =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"content\":\"" + text + "\"},\"finish_reason\":null}]}";

    private static DurableTurnContext Context() =>
        new("task-1", "conv-1", AgentTaskMode.StructuredTask, "pinned", false);

    private static DurableCompletionDecision NotComplete() =>
        new(false, AgentTaskStatus.Running, "Step 'type text' failed with 'UiaKeyboardValueNotVerified'.", []);

    private static DurableCompletionDecision Complete() =>
        new(true, AgentTaskStatus.Completed, "All outcomes verified.", []);

    private static (ChatOrchestrator Orchestrator, ScriptedGateway Gateway, Mock<IDurableTaskRuntime> Runtime)
        Create(Queue<DurableCompletionDecision> completions)
    {
        var gateway = new ScriptedGateway();
        var host = new Mock<IMcpToolHost>();
        host.Setup(h => h.GetToolDefinitions()).Returns(new List<GatewayToolDefinition>());
        var permissions = new Mock<IPermissionService>();
        var store = new Mock<IConversationStore>();

        var runtime = new Mock<IDurableTaskRuntime>();
        runtime.Setup(r => r.StartOrResumeAsync(
                It.IsAny<string>(), It.IsAny<IReadOnlyList<GatewayMessage>>(),
                It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Context());
        runtime.Setup(r => r.EvaluateCompletionAsync(
                It.IsAny<DurableTurnContext>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(() => completions.Count > 0 ? completions.Dequeue() : NotComplete());
        runtime.Setup(r => r.PauseAsync(
                It.IsAny<DurableTurnContext>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask);

        // AuthoritativePlanUI off so the completion path doesn't need a plan view.
        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, store.Object,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: runtime.Object,
            stabilization: new RuntimeStabilizationOptions { AuthoritativePlanUI = false });
        return (orchestrator, gateway, runtime);
    }

    private static async Task<List<AgentStreamEvent>> RunAsync(
        ChatOrchestrator orchestrator, List<GatewayMessage> messages)
    {
        var events = new List<AgentStreamEvent>();
        await foreach (var evt in orchestrator.RunAsync("gpt-4o", messages, "conv-1"))
            events.Add(evt);
        return events;
    }

    [Fact]
    public async Task Model_That_Stops_On_An_Incomplete_Task_Is_Nudged_Back_Until_It_Completes()
    {
        // not-complete twice, then complete on the third evaluation.
        var completions = new Queue<DurableCompletionDecision>(
            new[] { NotComplete(), NotComplete(), Complete() });
        var (orchestrator, gateway, _) = Create(completions);
        gateway.Enqueue(ContentFrame("I couldn't do it."), StopFrame, "[DONE]");
        gateway.Enqueue(ContentFrame("Still stuck."), StopFrame, "[DONE]");
        gateway.Enqueue(ContentFrame("Done now."), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "type into Notepad" } };
        var events = await RunAsync(orchestrator, messages);

        // It kept going instead of giving up after the first stop.
        gateway.Requests.Should().HaveCount(3);
        // Two recovery nudges were injected as system messages.
        messages.Count(m => m.Role == "system" && ((string)m.Content!).Contains("NOT complete"))
            .Should().Be(2);
        events.OfType<AgentVerificationModeEvent>()
            .Count(e => e.Message.Contains("Recovering")).Should().Be(2);
        // The turn ends normally once the task verified as complete.
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public async Task Nudges_Are_Bounded_Then_The_Turn_Ends_Incomplete_With_A_Resumable_TaskId()
    {
        // Never completes → the ceiling must stop the loop.
        var (orchestrator, gateway, runtime) = Create(new Queue<DurableCompletionDecision>());
        for (var i = 0; i < 6; i++)
            gateway.Enqueue(ContentFrame($"attempt {i}"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "type into Notepad" } };
        var events = await RunAsync(orchestrator, messages);

        // Initial stop + exactly MaxRecoveryNudges further attempts, then it stops.
        gateway.Requests.Should().HaveCount(ChatOrchestrator.MaxRecoveryNudges + 1);
        messages.Count(m => m.Role == "system" && ((string)m.Content!).Contains("NOT complete"))
            .Should().Be(ChatOrchestrator.MaxRecoveryNudges);
        // Incomplete: success is NOT claimed, and the TaskId is surfaced for resume.
        var visible = string.Concat(events.OfType<AgentContentDeltaEvent>().Select(e => e.Text));
        visible.Should().Contain("TaskId=task-1");
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public async Task Terminal_Failed_Status_Is_Not_Nudged()
    {
        var completions = new Queue<DurableCompletionDecision>(
            new[] { new DurableCompletionDecision(false, AgentTaskStatus.Failed, "Unrecoverable.", []) });
        var (orchestrator, gateway, _) = Create(completions);
        gateway.Enqueue(ContentFrame("cannot proceed"), StopFrame, "[DONE]");

        var messages = new List<GatewayMessage> { new() { Role = "user", Content = "do it" } };
        var events = await RunAsync(orchestrator, messages);

        // A terminal (Failed) task is not recoverable — no nudge, ends immediately.
        gateway.Requests.Should().HaveCount(1);
        messages.Should().NotContain(m => m.Role == "system" && ((string)m.Content!).Contains("NOT complete"));
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
    }
}
