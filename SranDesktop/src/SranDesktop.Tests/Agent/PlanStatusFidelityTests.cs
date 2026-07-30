using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// Plan/run status fidelity — the status the user reads must mean what it says.
///
/// T4 closed the false success one layer down: a tool reporting failure could no
/// longer be verified by the runtime. This is the same lie one layer up, where a
/// run whose real work failed still closed as <c>Completed</c> with its goal
/// struck through.
///
/// Observed live (GPT-4o, 2026-07-30): "ดู docker ที่ว่ามีอะไรรันอยู่บ้าง" →
/// <c>run_command docker ps</c> failed (no docker in the sandbox lane, which is
/// correct by design); an unrelated observation then succeeded on the same step
/// and flipped both the step and the goal outcome to Verified; completion
/// evaluated honestly against that corrupted state and returned Completed. The
/// user's question was never answered and the run claimed success anyway.
///
/// The defect is therefore in what may verify an outcome, not only in what may
/// close a run — so both layers are covered here.
/// </summary>
public sealed class PlanStatusFidelityTests : IDisposable
{
    private readonly string _directory;
    private readonly SqliteConversationStore _conversationStore;
    private readonly SqliteAgentTaskStore _taskStore;
    private readonly DurableTaskRuntime _runtime;

    public PlanStatusFidelityTests()
    {
        _directory = Path.Combine(
            Path.GetTempPath(), $"sran-plan-status-{Guid.NewGuid():N}");
        var databasePath = Path.Combine(_directory, "data.db");
        _conversationStore = new SqliteConversationStore(databasePath);
        _taskStore = new SqliteAgentTaskStore(databasePath);
        _runtime = new DurableTaskRuntime(
            _taskStore, new GoalInterpreter(), new CapabilityRouter());
    }

    /// <summary>
    /// The live repro. The command that would have answered the question failed;
    /// an unrelated observation succeeded afterwards. That observation is not
    /// evidence about containers, so neither the outcome nor the run may be
    /// reported as done.
    /// </summary>
    [Fact]
    public async Task An_Unrelated_Observation_Cannot_Verify_The_Goal_A_Failed_Command_Left_Open()
    {
        var context = await StartAsync("check the project containers");
        var failed = await _runtime.PrepareActionAsync(
            context, "call-1", "run_command",
            ToolCategory.Shell, """{"command":"docker ps"}""");
        failed.Proceed.Should().BeTrue(
            $"code={failed.FailureCode} reason={failed.FailureReason}");
        await _runtime.RecordResultAsync(
            context, failed,
            ToolResult.FailedEffect(
                "CommandFailed",
                "'docker' is not recognised inside the sandbox.",
                retryable: false,
                exitCode: 9009),
            12);

        // "Check open windows" — succeeds, and says nothing about containers.
        var unrelated = await _runtime.PrepareActionAsync(
            context, "call-2", "get_computer_state",
            ToolCategory.Screen, """{"scope":"windows"}""");
        if (unrelated.Proceed)
            await _runtime.RecordResultAsync(
                context, unrelated,
                ToolResult.Verified(
                    "Foreground window: Visual Studio Code. 7 windows open.",
                    new[] { "window=Code", "window_count=7" }),
                138);

        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        snapshot.Plan.Outcomes.Where(outcome => outcome.Required)
            .Should().NotContain(outcome => outcome.Status == OutcomeStatus.Verified,
                "an observation of open windows is not evidence about containers");

        var decision = await _runtime.EvaluateCompletionAsync(context);
        decision.Completed.Should().BeFalse(
            "the command the user asked about never produced an answer");
    }

    /// <summary>
    /// The counter-case, and the reason this cannot be fixed by blocking
    /// completion whenever any tool call failed: trying, failing, and then
    /// succeeding another way is correct agent behaviour and must still close as
    /// Completed. A guard that punished recovery would teach the agent not to retry.
    /// </summary>
    [Fact]
    public async Task A_Failure_That_A_Later_Success_Recovers_Still_Completes()
    {
        var context = await StartAsync("check the project containers");
        var failed = await _runtime.PrepareActionAsync(
            context, "call-1", "run_command",
            ToolCategory.Shell, """{"command":"docker ps"}""");
        failed.Proceed.Should().BeTrue();
        await _runtime.RecordResultAsync(
            context, failed,
            ToolResult.FailedEffect(
                "CommandFailed", "'docker' is not on PATH.", retryable: true, exitCode: 9009),
            12);

        // Second attempt actually answers the question that was asked.
        var recovered = await _runtime.PrepareActionAsync(
            context, "call-2", "run_command",
            ToolCategory.Shell, """{"command":"docker container ls"}""");
        recovered.Proceed.Should().BeTrue();
        await _runtime.RecordResultAsync(
            context, recovered,
            ToolResult.Verified(
                "CONTAINER ID   IMAGE     STATUS\n9f2a1c   redis:7   Up 3 hours",
                new[] { "exit_code=0", "observed_value=containers listed" }),
            40);

        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        snapshot.Plan.Milestones.SelectMany(milestone => milestone.Steps)
            .Should().NotContain(step => step.Status == TaskStepStatus.Failed,
                "the retry recovered the step");
        snapshot.Plan.Outcomes.Where(outcome => outcome.Required)
            .Should().OnlyContain(outcome => outcome.Status == OutcomeStatus.Verified,
                "the goal really was reached, by the second attempt");
    }

    /// <summary>
    /// The conversation-mode half of the same rule. A turn that dispatched work
    /// which definitely failed must not close as Completed either — the existing
    /// guard only asked whether anything was still *unresolved*, and a definite
    /// failure is resolved.
    /// </summary>
    [Fact]
    public async Task A_Conversation_Turn_With_A_Failed_Action_Does_Not_Close_As_Completed()
    {
        var context = await StartAsync("What is the capital of Thailand?");
        context.Mode.Should().Be(AgentTaskMode.Conversation);
        var preparation = await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition",
            ToolCategory.Network, """{"state":"opened"}""");
        preparation.Proceed.Should().BeTrue();
        await _runtime.RecordResultAsync(
            context, preparation,
            ToolResult.FailedEffect(
                "ToolExecutionFailed", "The capability refused the request.",
                retryable: false),
            9);

        await _runtime.CompleteConversationAsync(context, "sha256:ABCDEF");

        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        snapshot.Task.Status.Should().NotBe(AgentTaskStatus.Completed);
        snapshot.Task.CompletedAtUtc.Should().BeNull();
        (await _runtime.GetEventsAsync(context.TaskId))
            .Should().NotContain(item => item.EventType == "RunCompleted");
    }

    /// <summary>A turn that never dispatched anything still completes normally.</summary>
    [Fact]
    public async Task A_Conversation_Turn_With_No_Failed_Work_Completes_Normally()
    {
        var context = await StartAsync("What is the capital of Thailand?");

        await _runtime.CompleteConversationAsync(context, "sha256:ABCDEF");

        (await _taskStore.GetAsync(context.TaskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Completed);
    }

    private async Task<DurableTurnContext> StartAsync(string goal)
    {
        var conversationId = $"plan-status-{Guid.NewGuid():N}";
        await _conversationStore.UpsertConversationAsync(new Conversation
        {
            Id = conversationId,
            Title = goal,
            ModelId = "test-model",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow
        });
        return await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Chat,
            goal,
            $"conversation:{conversationId}"));
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_directory))
                Directory.Delete(_directory, recursive: true);
        }
        catch (IOException)
        {
        }
    }
}
