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
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Agent;

public sealed class DurableTaskRuntimeTests : IDisposable
{
    private readonly string _directory;
    private readonly string _databasePath;
    private readonly SqliteConversationStore _conversationStore;
    private readonly SqliteAgentTaskStore _taskStore;
    private readonly DurableTaskRuntime _runtime;

    public DurableTaskRuntimeTests()
    {
        _directory = Path.Combine(
            Path.GetTempPath(), $"sran-durable-runtime-{Guid.NewGuid():N}");
        _databasePath = Path.Combine(_directory, "data.db");
        _conversationStore = new SqliteConversationStore(_databasePath);
        _taskStore = new SqliteAgentTaskStore(_databasePath);
        _runtime = NewRuntime(_taskStore);
    }

    [Fact]
    public void Thai_Outcome_Preserves_Constraint_And_Creates_Definition_Of_Done()
    {
        var result = new GoalInterpreter().Interpret(
            "ติดตั้งเครื่องมือให้โปรเจกต์นี้ แต่ห้ามแก้ dependency เดิม");

        result.IsActionable.Should().BeTrue();
        result.Mode.Should().Be(AgentTaskMode.LongHorizonTask);
        result.Goal!.Constraints.Should().ContainSingle()
            .Which.Should().Contain("ห้ามแก้ dependency เดิม");
        result.Goal.DefinitionOfDone.Should().Contain(i =>
            i.Description.Contains("constraints", StringComparison.OrdinalIgnoreCase));
        result.Goal.DefinitionOfDone.Should().OnlyContain(i =>
            i.Status == DefinitionOfDoneStatus.Pending);
    }

    [Fact]
    public void Blocking_Reference_Requires_Clarification()
    {
        var result = new GoalInterpreter().Interpret("close this");

        result.Goal!.MissingInformation.Should().NotBeEmpty();
        result.ClarificationQuestion.Should().NotBeNull();
    }

    [Fact]
    public async Task Clarification_Updates_Existing_Goal_And_Checkpoint_Without_New_Task()
    {
        var context = await StartAsync("close this");
        var waiting = (await _taskStore.GetAsync(context.TaskId))!;
        waiting.Task.Status.Should().Be(AgentTaskStatus.WaitingForClarification);

        var resumed = await _runtime.StartOrResumeAsync(
            context.ConversationId,
            new[]
            {
                new GatewayMessage { Role = "user", Content = "close this" },
                new GatewayMessage { Role = "user", Content = "Calculator" }
            },
            $"conversation:{context.ConversationId}");
        var resolved = (await _taskStore.GetAsync(context.TaskId))!;

        resumed!.TaskId.Should().Be(context.TaskId);
        resolved.Task.Status.Should().Be(AgentTaskStatus.Ready);
        resolved.Goal.Version.Should().Be(2);
        resolved.Goal.MissingInformation.Should().BeEmpty();
        resolved.Goal.NormalizedGoal.Should().Contain("Calculator");
        resolved.Plan.GoalVersion.Should().Be(2);
        CheckpointIntegrity.IsValid(resolved.Checkpoint!).Should().BeTrue();
    }

    [Fact]
    public async Task Goal_Plan_Task_And_Checkpoint_RoundTrip_Through_Sqlite()
    {
        var context = await StartAsync(
            "run the mock project workflow without modifying dependency files");

        var snapshot = await _taskStore.GetAsync(context.TaskId);

        snapshot.Should().NotBeNull();
        snapshot!.Goal.Constraints.Should().ContainSingle();
        snapshot.Plan.Milestones.Should().ContainSingle();
        snapshot.Plan.Milestones[0].Steps.Should().ContainSingle();
        snapshot.Checkpoint.Should().NotBeNull();
        CheckpointIntegrity.IsValid(snapshot.Checkpoint!).Should().BeTrue();
    }

    [Fact]
    public async Task Optimistic_Concurrency_Rejects_Stale_Task_Update()
    {
        var context = await StartAsync("open Calculator");
        var first = (await _taskStore.GetAsync(context.TaskId))!.Task;
        var stale = (await _taskStore.GetAsync(context.TaskId))!.Task;

        var expected = first.Version;
        first.Status = AgentTaskStatus.Paused;
        (await _taskStore.UpdateTaskAsync(first, expected)).Should().BeTrue();

        stale.Status = AgentTaskStatus.Cancelled;
        (await _taskStore.UpdateTaskAsync(stale, expected)).Should().BeFalse();
        (await _taskStore.GetAsync(context.TaskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Paused);
    }

    [Fact]
    public async Task Model_Plan_Done_Status_Does_Not_Verify_A_Step()
    {
        var context = await StartAsync("open Calculator");
        context = await _runtime.ApplyPlanUpdateAsync(context, JsonSerializer.Serialize(new
        {
            steps = new[] { new { step = "Open Calculator", status = "done" } }
        }));

        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        snapshot.Plan.Milestones[0].Steps[0].Status
            .Should().Be(TaskStepStatus.Ready);
        var decision = await _runtime.EvaluateCompletionAsync(context);
        decision.Completed.Should().BeFalse();
    }

    [Fact]
    public async Task Tool_Execution_Without_Effect_Verification_Cannot_Complete_Step()
    {
        var context = await StartAsync("open Calculator");
        var preparation = await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition",
            ToolCategory.Network, """{"state":"opened"}""");

        preparation.Proceed.Should().BeTrue();
        var outcome = await _runtime.RecordResultAsync(
            context, preparation, ToolResult.Ok("native call returned"), 2);

        outcome.Result.NativeSuccess.Should().BeTrue();
        outcome.Result.Verified.Should().BeFalse();
        outcome.Result.StepCompleted.Should().BeFalse();
        (await _runtime.EvaluateCompletionAsync(outcome.Context)).Completed
            .Should().BeFalse();
    }

    [Fact]
    public async Task Verified_Effect_Completes_Step_And_Task_Definition_Of_Done()
    {
        var context = await StartAsync("open Calculator");
        var preparation = await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition",
            ToolCategory.Network, """{"state":"opened"}""");
        var outcome = await _runtime.RecordResultAsync(
            context, preparation,
            ToolResult.Verified(
                "Calculator window identity verified.",
                new[] { "window=Calculator" }),
            2);

        outcome.Result.StepCompleted.Should().BeTrue();
        var decision = await _runtime.EvaluateCompletionAsync(outcome.Context);
        decision.Completed.Should().BeTrue();
        decision.EvidenceIds.Should().ContainSingle();
        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        snapshot.Task.Status.Should().Be(AgentTaskStatus.Completed);
        snapshot.Plan.Status.Should().Be(PlanStatus.Completed);
        snapshot.Plan.Milestones.Should().OnlyContain(
            milestone => milestone.Status == MilestoneStatus.Verified);
    }

    [Fact]
    public async Task Completion_Is_Blocked_When_No_Real_Read_Value_Confirms_A_Text_Outcome()
    {
        // T5 PART A belt-and-suspenders: the mock adapter reports every step verified
        // (bypassing the matcher's value gate), so the outcome status flips to Verified —
        // but the TextValue outcome's ExpectedValue ("hello") was never actually read from
        // a control. The FINAL completion invariant must catch this and refuse to complete.
        var context = await StartAsync("เปิด Notepad พิมพ์ข้อความ \"hello\"");

        // Step 1 — application ready (mock verified).
        var ready = await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition", ToolCategory.Network,
            """{"state":"opened"}""");
        var afterReady = await _runtime.RecordResultAsync(
            context, ready,
            ToolResult.Verified("Notepad ready.", new[] { "window=Notepad", "input_receiver=true" }),
            2);

        // Step 2 — text step "verified" by mock but with NO read_back_value token.
        var typed = await _runtime.PrepareActionAsync(
            afterReady.Context, "call-2", "mock_state_transition", ToolCategory.Network,
            """{"state":"typed"}""");
        var afterTyped = await _runtime.RecordResultAsync(
            afterReady.Context, typed,
            ToolResult.Verified("Typed hello into Notepad.", new[] { "window=Notepad" }),
            2);

        var decision = await _runtime.EvaluateCompletionAsync(afterTyped.Context);

        decision.Completed.Should().BeFalse();
        decision.Message.Should().Contain("hello");
        (await _taskStore.GetAsync(context.TaskId))!.Task.Status
            .Should().NotBe(AgentTaskStatus.Completed);
    }

    [Fact]
    public async Task Completion_Succeeds_When_A_Real_Read_Value_Confirms_The_Text_Outcome()
    {
        var context = await StartAsync("เปิด Notepad พิมพ์ข้อความ \"hello\"");

        var ready = await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition", ToolCategory.Network,
            """{"state":"opened"}""");
        var afterReady = await _runtime.RecordResultAsync(
            context, ready,
            ToolResult.Verified("Notepad ready.", new[] { "window=Notepad", "input_receiver=true" }),
            2);

        var typed = await _runtime.PrepareActionAsync(
            afterReady.Context, "call-2", "mock_state_transition", ToolCategory.Network,
            """{"state":"typed"}""");
        // This time the evidence carries a REAL read-back value that matches ExpectedValue.
        var afterTyped = await _runtime.RecordResultAsync(
            afterReady.Context, typed,
            ToolResult.Verified("Typed and read back.",
                new[] { "window=Notepad", "read_back_value=hello" }),
            2);

        var decision = await _runtime.EvaluateCompletionAsync(afterTyped.Context);

        decision.Completed.Should().BeTrue();
        (await _taskStore.GetAsync(context.TaskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Completed);
    }

    [Fact]
    public void Completion_Value_Invariant_Reads_Actual_Token_Not_Content_Echo()
    {
        var withRealValue = new[]
        {
            Evidence("o1", """{"Verified":true,"evidence":["hwnd=1","read_back_value=hello"]}"""),
        };
        var echoOnly = new[]
        {
            // StructuredData carries NO read token; a Summary echo must not count.
            Evidence("o1", """{"Verified":true,"evidence":["hwnd=1"]}""", summary: "typed hello"),
        };

        DurableTaskRuntime.EvidenceConfirmsOutcomeValue(withRealValue, "o1", "hello")
            .Should().BeTrue();
        DurableTaskRuntime.EvidenceConfirmsOutcomeValue(echoOnly, "o1", "hello")
            .Should().BeFalse();
        // Wrong outcome id never matches.
        DurableTaskRuntime.EvidenceConfirmsOutcomeValue(withRealValue, "other", "hello")
            .Should().BeFalse();
    }

    private static TaskEvidence Evidence(string outcomeId, string structuredData, string summary = "") =>
        new()
        {
            EvidenceId = $"ev_{Guid.NewGuid():N}",
            TaskId = "task",
            OutcomeId = outcomeId,
            EvidenceType = EvidenceType.Verification,
            Source = "uia_set_value",
            Summary = summary,
            StructuredData = structuredData,
            Confidence = 1.0,
            RetentionPolicy = "task-default",
            Timestamp = DateTimeOffset.UtcNow
        };

    [Fact]
    public async Task Consequential_Action_Requires_Exact_Unexpired_Approval()
    {
        var context = await StartAsync("install the mock tool");
        const string arguments = """{"command":"install mock"}""";

        var blocked = await _runtime.PrepareActionAsync(
            context, "call-1", "run_command", ToolCategory.Shell, arguments);
        blocked.Proceed.Should().BeFalse();
        blocked.FailureCode.Should().Be("ApprovalRequired");

        await _runtime.RecordApprovalAsync(
            context, "approval-call", "run_command",
            """{"command":"install another"}""",
            "project:test", granted: true,
            DateTimeOffset.UtcNow.AddMinutes(1));
        (await _runtime.PrepareActionAsync(
                context, "call-2", "run_command", ToolCategory.Shell, arguments))
            .FailureCode.Should().Be("ApprovalRequired");

        await _runtime.RecordApprovalAsync(
            context, "approval-call-2", "run_command", arguments,
            "project:test", granted: true,
            DateTimeOffset.UtcNow.AddMinutes(1));
        (await _runtime.PrepareActionAsync(
                context, "call-3", "run_command", ToolCategory.Shell, arguments))
            .Proceed.Should().BeTrue();
    }

    [Fact]
    public async Task Expired_Approval_Does_Not_Authorize_Consequential_Action()
    {
        var context = await StartAsync("install the mock tool");
        const string arguments = """{"command":"install mock"}""";
        await _runtime.RecordApprovalAsync(
            context, "approval-call", "run_command", arguments,
            "project:test", granted: true,
            DateTimeOffset.UtcNow.AddSeconds(-1));

        var result = await _runtime.PrepareActionAsync(
            context, "call-1", "run_command", ToolCategory.Shell, arguments);

        result.Proceed.Should().BeFalse();
        result.FailureCode.Should().Be("ApprovalRequired");
    }

    [Fact]
    public async Task Unrelated_Evidence_Cannot_Satisfy_Version_Definition_Of_Done()
    {
        var context = await StartAsync("install the mock tool");
        const string arguments = """{"command":"install mock"}""";
        await _runtime.RecordApprovalAsync(
            context, "approval-call", "run_command", arguments,
            "project:test", granted: true,
            DateTimeOffset.UtcNow.AddMinutes(1));
        var preparation = await _runtime.PrepareActionAsync(
            context, "call-1", "run_command", ToolCategory.Shell, arguments);
        var outcome = await _runtime.RecordResultAsync(
            context, preparation,
            ToolResult.Verified("Mock process returned.", new[] { "state=installed" }),
            1);

        var completion = await _runtime.EvaluateCompletionAsync(outcome.Context);
        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;

        completion.Completed.Should().BeFalse();
        snapshot.Plan.Milestones.Single().DefinitionOfDone.Single(
                item => item.VerificationType == "ProcessOutput")
            .Status.Should().Be(DefinitionOfDoneStatus.NotVerifiable);
        snapshot.Goal.DefinitionOfDone.Single(
                item => item.VerificationType == "ProcessOutput")
            .Status.Should().NotBe(DefinitionOfDoneStatus.Satisfied);
        snapshot.Task.Status.Should().NotBe(AgentTaskStatus.Completed);
    }

    [Fact]
    public async Task Restart_With_Dispatched_Action_Requires_Reconciliation_And_Blocks_Duplicate()
    {
        var context = await StartAsync("open Calculator");
        var preparation = await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition",
            ToolCategory.Network, """{"state":"opened"}""");
        preparation.Proceed.Should().BeTrue();

        var restartedStore = new SqliteAgentTaskStore(_databasePath);
        var restarted = NewRuntime(restartedStore);
        var resumed = await restarted.StartOrResumeAsync(
            context.ConversationId, Array.Empty<GatewayMessage>(),
            $"conversation:{context.ConversationId}");

        resumed.Should().NotBeNull();
        resumed!.ReconciliationRequired.Should().BeTrue();
        var duplicate = await restarted.PrepareActionAsync(
            resumed, "call-2", "mock_state_transition",
            ToolCategory.Network, """{"state":"opened"}""");
        duplicate.Proceed.Should().BeFalse();
        duplicate.FailureCode.Should().Be("UnknownOutcome");
    }

    [Fact]
    public async Task Fresh_Observation_Clears_Resume_Block_But_Original_Dispatch_Is_Not_Repeated()
    {
        var context = await StartAsync("open Calculator");
        const string originalArguments = """{"state":"opened"}""";
        (await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition",
            ToolCategory.Network, originalArguments)).Proceed.Should().BeTrue();

        var restarted = NewRuntime(new SqliteAgentTaskStore(_databasePath));
        var resumed = (await restarted.StartOrResumeAsync(
            context.ConversationId, Array.Empty<GatewayMessage>(),
            $"conversation:{context.ConversationId}"))!;
        var observation = await restarted.PrepareActionAsync(
            resumed, "observe-1", "get_computer_state",
            ToolCategory.Screen, "{}");
        observation.Proceed.Should().BeTrue();
        var observed = await restarted.RecordResultAsync(
            resumed, observation,
            ToolResult.Verified("Current state observed.",
                new[] { "side_effect_occurred=false", "state=not_opened" }),
            1);

        observed.Context.ReconciliationRequired.Should().BeFalse();
        var duplicate = await restarted.PrepareActionAsync(
            observed.Context, "call-2", "mock_state_transition",
            ToolCategory.Network, originalArguments);
        duplicate.Proceed.Should().BeFalse();
        duplicate.FailureCode.Should().Be("DuplicateActionPrevented");

        var alternative = await restarted.PrepareActionAsync(
            observed.Context, "call-3", "mock_state_transition",
            ToolCategory.Network, """{"state":"opened","strategy":"alternative"}""");
        alternative.Proceed.Should().BeTrue();
    }

    [Fact]
    public async Task Read_Only_Observation_Can_Be_Repeated_After_World_State_Changes()
    {
        var context = await StartAsync("open Calculator");
        var first = await _runtime.PrepareActionAsync(
            context,
            "observe-1",
            "get_computer_state",
            ToolCategory.Screen,
            "{}");
        first.Proceed.Should().BeTrue();

        var observed = await _runtime.RecordResultAsync(
            context,
            first,
            ToolResult.Verified(
                "Initial state observed.",
                ["side_effect_occurred=false", "state=initial"]),
            1);
        var second = await _runtime.PrepareActionAsync(
            observed.Context,
            "observe-2",
            "get_computer_state",
            ToolCategory.Screen,
            "{}");

        second.Proceed.Should().BeTrue();
        second.FailureCode.Should().BeNull();
    }

    [Fact]
    public async Task Unresolved_Unknown_Outcome_Remains_Paused_And_Blocks_Side_Effects()
    {
        var context = await StartAsync("open Calculator");
        (await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition",
            ToolCategory.Network, "{\"state\":\"opened\"}" )).Proceed.Should().BeTrue();

        var restarted = NewRuntime(new SqliteAgentTaskStore(_databasePath));
        var resumed = (await restarted.StartOrResumeAsync(
            context.ConversationId, Array.Empty<GatewayMessage>(),
            $"conversation:{context.ConversationId}"))!;
        var observation = await restarted.PrepareActionAsync(
            resumed, "observe-1", "get_computer_state", ToolCategory.Screen, "{}");
        var observed = await restarted.RecordResultAsync(
            resumed, observation,
            ToolResult.Verified("State remains ambiguous.", ["state=unknown"]), 1);

        observed.Context.ReconciliationRequired.Should().BeTrue();
        var blocked = await restarted.PrepareActionAsync(
            observed.Context, "call-2", "mock_state_transition",
            ToolCategory.Network, "{\"state\":\"opened\",\"strategy\":\"retry\"}");
        blocked.Proceed.Should().BeFalse();
        blocked.FailureCode.Should().Be("UnknownOutcome");
    }

    [Fact]
    public async Task Pause_And_Process_Restart_Preserve_Current_Plan_Position()
    {
        var context = await StartAsync("run a two step project check");
        context = await _runtime.ApplyPlanUpdateAsync(context, JsonSerializer.Serialize(new
        {
            steps = new[]
            {
                new { step = "Inspect project", status = "pending" },
                new { step = "Verify project", status = "pending" }
            }
        }));
        var before = (await _taskStore.GetAsync(context.TaskId))!;
        await _runtime.PauseAsync(context, "test pause");

        var restarted = NewRuntime(new SqliteAgentTaskStore(_databasePath));
        var resumed = await restarted.StartOrResumeAsync(
            context.ConversationId, Array.Empty<GatewayMessage>(),
            $"conversation:{context.ConversationId}");
        var after = (await new SqliteAgentTaskStore(_databasePath)
            .GetAsync(context.TaskId))!;

        resumed.Should().NotBeNull();
        after.Task.CurrentStepId.Should().Be(before.Task.CurrentStepId);
        after.Plan.Version.Should().Be(before.Plan.Version);
    }

    [Fact]
    public async Task Pinned_Goal_Constraints_And_Dod_Survive_Context_Compaction()
    {
        var context = await StartAsync(
            "run project checks but do not modify dependency files");
        var messages = new List<GatewayMessage>
        {
            new() { Role = "system", Content = "base" },
            new() { Role = "system", Content = context.PinnedContext }
        };
        for (var i = 0; i < 20; i++)
        {
            messages.Add(new GatewayMessage
                { Role = "user", Content = $"turn-{i} " + new string('x', 500) });
            messages.Add(new GatewayMessage
                { Role = "assistant", Content = new string('y', 500) });
        }

        ContextBudgeter.FitToBudget(messages, 1_200, outputReserve: 0);

        messages.Any(m =>
            m.Role == "system"
            && m.Content is string text
            && text.Contains(DurableTaskRuntime.ContextMarker, StringComparison.Ordinal)
            && text.Contains("do not modify dependency files", StringComparison.OrdinalIgnoreCase)
            && text.Contains("Definition of Done", StringComparison.Ordinal))
            .Should().BeTrue();
    }

    [Fact]
    public void Corrupt_Checkpoint_Checksum_Is_A_Safe_Validation_Failure()
    {
        var checkpoint = new TaskCheckpoint
        {
            CheckpointId = "checkpoint",
            TaskId = "task",
            PlanVersion = 1,
            TaskStatus = AgentTaskStatus.Running,
            WorldStateVersion = 0,
            ApprovalState = "none",
            ContextInvariantSnapshot = "goal",
            CreatedAtUtc = DateTimeOffset.UtcNow,
            ResumeSafetyStatus = ResumeSafetyStatus.Safe,
            Checksum = "not-hex",
            Version = 1
        };

        CheckpointIntegrity.IsValid(checkpoint).Should().BeFalse();
    }

    [Fact]
    public async Task Corrupted_Persisted_Checkpoint_Safe_Fails_On_Restart()
    {
        var context = await StartAsync("open Calculator");
        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        await _taskStore.SaveCheckpointAsync(snapshot.Checkpoint! with
        {
            CheckpointId = $"corrupt-{Guid.NewGuid():N}",
            Version = snapshot.Checkpoint.Version + 1,
            Checksum = "corrupt"
        });

        var restarted = NewRuntime(new SqliteAgentTaskStore(_databasePath));
        var resumed = await restarted.StartOrResumeAsync(
            context.ConversationId, Array.Empty<GatewayMessage>(),
            $"conversation:{context.ConversationId}");

        resumed.Should().NotBeNull();
        (await _taskStore.GetAsync(context.TaskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Failed);
    }

    [Fact]
    public async Task Deterministic_Capability_Policy_Rejects_Pixel_For_Project_Step()
    {
        var context = await StartAsync("run the project build");
        var result = await _runtime.PrepareActionAsync(
            context, "call-1", "computer_click", ToolCategory.Screen,
            """{"capture_id":"capture","x":10,"y":10}""");

        result.Proceed.Should().BeFalse();
        result.FailureCode.Should().Be("CapabilityPolicyRejected");
    }

    [Fact]
    public async Task Deterministic_20_Step_Harness_Completes_With_Zero_False_Success()
    {
        var context = await StartAsync("run a mock long horizon project workflow");
        var result = await new MockLongHorizonHarness(_runtime)
            .RunAsync(context, 20, injectFailures: false);

        result.VerifiedSteps.Should().Be(20);
        result.Completed.Should().BeTrue();
        result.FalseSuccess.Should().BeFalse();
    }

    [Fact]
    public async Task Deterministic_50_Step_Harness_Recovers_Replans_And_Resumes()
    {
        var context = await StartAsync("run a mock long horizon project workflow");
        var result = await new MockLongHorizonHarness(_runtime)
            .RunAsync(context, 50, injectFailures: true);

        result.VerifiedSteps.Should().Be(50);
        result.Retries.Should().Be(2);
        result.PlanRevisions.Should().Be(1);
        result.ResumeCount.Should().Be(1);
        result.Completed.Should().BeTrue();
        result.FalseSuccess.Should().BeFalse();
    }

    [Fact]
    public async Task Orchestrator_Runtime_Caller_Blocks_Model_Success_Without_Verified_Effect()
    {
        var setup = await CreateOrchestratorAsync(
            ToolResult.Ok("native tool returned"));
        setup.Gateway.Enqueue(
            ToolCallFrame("call-1", "fake_tool"),
            FinishToolCallsFrame,
            "[DONE]");
        setup.Gateway.Enqueue(
            ContentFrame("The task is definitely complete."),
            StopFrame,
            "[DONE]");
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "open Calculator" }
        };

        var events = await RunOrchestratorAsync(
            setup.Orchestrator, messages, setup.ConversationId);
        var visible = string.Concat(
            events.OfType<AgentContentDeltaEvent>().Select(e => e.Text));

        setup.Tool.Executions.Should().Be(1);
        events.OfType<AgentToolResultEvent>().Single().UnifiedResult!
            .Verified.Should().BeFalse();
        visible.Should().NotContain("definitely complete");
        visible.Should().Contain("lack verification");
        (await _taskStore.GetActiveByConversationAsync(setup.ConversationId))!
            .Task.Status.Should().NotBe(AgentTaskStatus.Completed);
    }

    [Fact]
    public async Task Orchestrator_Runtime_Caller_Completes_Only_From_Verified_Dod()
    {
        var setup = await CreateOrchestratorAsync(
            ToolResult.Verified("Verified native state.", new[] { "state=ready" }));
        setup.Gateway.Enqueue(
            ToolCallFrame("call-1", "fake_tool"),
            FinishToolCallsFrame,
            "[DONE]");
        setup.Gateway.Enqueue(
            ContentFrame("Model summary."),
            StopFrame,
            "[DONE]");
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "open Calculator" }
        };

        var events = await RunOrchestratorAsync(
            setup.Orchestrator, messages, setup.ConversationId);
        var visible = string.Concat(
            events.OfType<AgentContentDeltaEvent>().Select(e => e.Text));

        events.OfType<AgentToolResultEvent>().Single().UnifiedResult!
            .StepCompleted.Should().BeTrue();
        visible.Should().Contain("Model summary.");
        visible.Should().Contain("Runtime verification:");
        visible.Should().Contain("Evidence: evidence_");
        var taskId = events.OfType<AgentToolResultEvent>().Single()
            .UnifiedResult!.TaskId;
        (await _taskStore.GetAsync(taskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Completed);
    }

    [Fact]
    public async Task Orchestrator_Streams_Body_Live_Under_Durable_Runtime_Then_Appends_Verification_Once()
    {
        // Verified DoD so the final path completes and appends the verification suffix.
        var setup = await CreateOrchestratorAsync(
            ToolResult.Verified("Verified native state.", new[] { "state=ready" }));
        setup.Gateway.Enqueue(
            ToolCallFrame("call-1", "fake_tool"),
            FinishToolCallsFrame,
            "[DONE]");
        // The body arrives as multiple content deltas — it must stream token-by-token
        // even under the durable runtime, not be withheld until a composed blob.
        setup.Gateway.Enqueue(
            ContentFrame("Model "),
            ContentFrame("summary."),
            StopFrame,
            "[DONE]");
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "open Calculator" }
        };

        var events = await RunOrchestratorAsync(
            setup.Orchestrator, messages, setup.ConversationId);
        var deltas = events.OfType<AgentContentDeltaEvent>().Select(e => e.Text).ToList();

        // The body streamed incrementally (≥2 body deltas before the verification).
        deltas.Count(d => d.Contains("Model") || d.Contains("summary"))
            .Should().BeGreaterThanOrEqualTo(2);
        // Exactly one verification delta closes the turn.
        deltas.Count(d => d.Contains("Runtime verification:")).Should().Be(1);

        // The body is not duplicated: "Model summary." appears exactly once overall.
        var visible = string.Concat(deltas);
        CountOccurrences(visible, "Model summary.").Should().Be(1);
        visible.Should().Contain("Runtime verification:");
        visible.Should().Contain("Evidence: evidence_");
    }

    [Fact]
    public async Task Non_Actionable_Request_Downgrades_Loudly_But_Still_Answers()
    {
        // Durable runtime is wired, but a plain question isn't an actionable goal,
        // so StartOrResumeAsync returns null. The turn must NOT silently drop the
        // verification guarantees — it emits a one-time conversation-mode notice —
        // and must still produce the plain answer.
        var setup = await CreateOrchestratorAsync(ToolResult.Ok("unused"));
        setup.Gateway.Enqueue(ContentFrame("Paris."), StopFrame, "[DONE]");
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "What is the capital of France?" }
        };

        var events = await RunOrchestratorAsync(
            setup.Orchestrator, messages, setup.ConversationId);

        events.OfType<AgentVerificationModeEvent>().Should().ContainSingle();
        string.Concat(events.OfType<AgentContentDeltaEvent>().Select(e => e.Text))
            .Should().Contain("Paris.");
        events.Last().Should().BeOfType<AgentStreamDoneEvent>();
    }

    private static int CountOccurrences(string haystack, string needle)
    {
        var count = 0;
        for (var i = haystack.IndexOf(needle, StringComparison.Ordinal); i >= 0;
             i = haystack.IndexOf(needle, i + needle.Length, StringComparison.Ordinal))
            count++;
        return count;
    }

    [Fact]
    public async Task Skip_Approval_Mode_Cannot_Bypass_Consequential_Invariant()
    {
        var gateway = new RuntimeGateway();
        var tool = new RuntimeTool(
            ToolResult.Verified("installed", new[] { "mock=true" }),
            "run_command", ToolCategory.Shell);
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
                It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, _conversationStore,
            NullLogger<ChatOrchestrator>.Instance,
            settings: new SkipSettings());
        gateway.Enqueue(
            ToolCallFrame("call-1", tool.Name, """{"command":"install mock"}"""),
            FinishToolCallsFrame,
            "[DONE]");
        gateway.Enqueue(StopFrame, "[DONE]");

        var events = await RunOrchestratorAsync(
            orchestrator,
            [new GatewayMessage { Role = "user", Content = "install mock tool" }],
            $"approval-{Guid.NewGuid():N}");

        tool.Executions.Should().Be(0);
        events.OfType<AgentToolConsentRequestEvent>().Should().ContainSingle();
        events.OfType<AgentToolResultEvent>().Single().Success.Should().BeFalse();
    }

    [Fact]
    public async Task Orchestrator_Persists_Scoped_Approval_And_Correlated_Task_Trace()
    {
        var conversationId = $"approval-trace-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var gateway = new RuntimeGateway();
        var tool = new RuntimeTool(
            ToolResult.Verified("mock install verified", new[] { "version=1.0" }),
            "run_command", ToolCategory.Shell);
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
                It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, _conversationStore,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        orchestrator.ConsentRequested += _ =>
            Task.FromResult(ToolConsentResponse.Always);
        gateway.Enqueue(
            ToolCallFrame("call-1", tool.Name, """{"command":"install mock"}"""),
            FinishToolCallsFrame,
            "[DONE]");
        gateway.Enqueue(ContentFrame("completed"), StopFrame, "[DONE]");

        var events = await RunOrchestratorAsync(
            orchestrator,
            [new GatewayMessage { Role = "user", Content = "install mock tool" }],
            conversationId);

        tool.Executions.Should().Be(1);
        permissions.Verify(p => p.PersistDecisionAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<CancellationToken>()), Times.Never);
        var taskId = events.OfType<AgentToolResultEvent>().Single()
            .UnifiedResult!.TaskId;
        var trace = await _taskStore.GetEventsAsync(taskId);
        trace.Select(item => item.EventType).Should().ContainInOrder(
            "RunCreated",
            "TaskCreated",
            "GoalInterpreted",
            "PlanCreated",
            "ApprovalGranted",
            "ActionDispatching",
            "ToolResultReceived",
            "VerificationCompleted",
            "StepCompleted",
            "TaskCompleted");
        trace.Single(item => item.EventType == "ApprovalGranted")
            .ToolCallId.Should().Be("call-1");
        trace.Single(item => item.EventType == "VerificationCompleted")
            .ActionId.Should().NotBeNull();
    }

    [Fact]
    public void Default_Approval_Expiry_Is_Five_Minutes()
    {
        new RuntimeStabilizationOptions().ApprovalExpiry
            .Should().Be(TimeSpan.FromMinutes(5));
    }

    [Fact]
    public void State_Machine_Rejects_Transition_Out_Of_Terminal_State()
    {
        var action = () => AgentRunStateMachine.EnsureTransition(
            AgentTaskStatus.Completed,
            AgentTaskStatus.Ready);

        action.Should().Throw<InvalidOperationException>()
            .WithMessage("*Invalid durable run state transition*");
    }

    [Fact]
    public async Task Child_Run_Is_Durable_And_Inherits_Parent_Security_Context()
    {
        var parentConversation = $"parent-{Guid.NewGuid():N}";
        var childConversation = $"child-{Guid.NewGuid():N}";
        await CreateConversationAsync(parentConversation);
        await CreateConversationAsync(childConversation);
        var parent = await _runtime.CreateRunAsync(new DurableRunRequest(
            parentConversation,
            AgentTriggerType.Chat,
            "open Calculator",
            $"conversation:{parentConversation}",
            SecurityContextJson: """{"workspace":"authorized"}""",
            PermissionContextJson: """{"mode":"auto"}""",
            WorkspaceContextJson: """{"mount":"test"}""",
            ModelContextJson: """{"model":"test-model"}""",
            ChildRunBudget: 2));

        var child = await _runtime.CreateChildRunAsync(
            parent.TaskId,
            new DurableRunRequest(
                childConversation,
                AgentTriggerType.Subagent,
                "read the workspace",
                $"conversation:{childConversation}",
                SecurityContextJson: """{"workspace":"attacker"}""",
                ParentRunId: parent.TaskId));

        var parentSnapshot = (await _taskStore.GetAsync(parent.TaskId))!;
        var childSnapshot = (await _taskStore.GetAsync(child.TaskId))!;
        childSnapshot.Task.ParentRunId.Should().Be(parent.TaskId);
        childSnapshot.Task.TriggerType.Should().Be(AgentTriggerType.Subagent);
        childSnapshot.Task.SecurityContextJson
            .Should().Be(parentSnapshot.Task.SecurityContextJson);
        childSnapshot.Task.PermissionContextJson
            .Should().Be(parentSnapshot.Task.PermissionContextJson);
        childSnapshot.Task.WorkspaceContextJson
            .Should().Be(parentSnapshot.Task.WorkspaceContextJson);
        childSnapshot.Task.ModelContextJson
            .Should().Be(parentSnapshot.Task.ModelContextJson);
        (await _taskStore.CountChildrenAsync(parent.TaskId)).Should().Be(1);
    }

    [Fact]
    public async Task Restart_Recovery_Resumes_Safe_Checkpoint_Without_New_Run()
    {
        var context = await StartAsync("open Calculator");
        var before = (await _taskStore.GetAsync(context.TaskId))!;

        var recovered = await _runtime.RecoverInterruptedRunsAsync(
            before.Task.OwnerScope);

        recovered.Should().ContainSingle();
        recovered[0].TaskId.Should().Be(context.TaskId);
        recovered[0].CurrentStatus.Should().Be(AgentTaskStatus.Ready);
        recovered[0].ResumeSafety.Should().Be(ResumeSafetyStatus.Safe);
        var events = await _runtime.GetEventsAsync(context.TaskId);
        events.Select(item => item.Sequence)
            .Should().BeInAscendingOrder()
            .And.OnlyHaveUniqueItems();
        events.Select(item => item.EventType)
            .Should().ContainInOrder("RunInterrupted", "RunResumed");
        (await _taskStore.GetIncompleteAsync(before.Task.OwnerScope))
            .Should().ContainSingle(item => item.Task.TaskId == context.TaskId);
    }

    [Fact]
    public async Task Parent_Cancellation_Propagates_To_Durable_Children()
    {
        var parentConversation = $"cancel-parent-{Guid.NewGuid():N}";
        var childConversation = $"cancel-child-{Guid.NewGuid():N}";
        await CreateConversationAsync(parentConversation);
        await CreateConversationAsync(childConversation);
        var parent = await _runtime.CreateRunAsync(new DurableRunRequest(
            parentConversation,
            AgentTriggerType.Chat,
            "open Calculator",
            $"conversation:{parentConversation}"));
        var child = await _runtime.CreateChildRunAsync(
            parent.TaskId,
            new DurableRunRequest(
                childConversation,
                AgentTriggerType.Subagent,
                "inspect the workspace",
                $"conversation:{childConversation}",
                ParentRunId: parent.TaskId));

        await _runtime.CancelAsync(parent.TaskId, "user cancelled the parent");

        (await _taskStore.GetAsync(parent.TaskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Cancelled);
        (await _taskStore.GetAsync(child.TaskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Cancelled);
        (await _runtime.GetEventsAsync(child.TaskId))
            .Should().Contain(item => item.EventType == "RunCancelled");
    }

    [Fact]
    public async Task Conversation_Run_Completes_Only_Through_Runtime_Event()
    {
        var conversationId = $"conversation-only-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var context = await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Api,
            "What is the capital of Thailand?",
            $"conversation:{conversationId}"));

        context.Mode.Should().Be(AgentTaskMode.Conversation);
        await _runtime.CompleteConversationAsync(context, "sha256:ABCDEF");

        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        snapshot.Task.Status.Should().Be(AgentTaskStatus.Completed);
        snapshot.Task.FinalResultReference.Should().Be("sha256:ABCDEF");
        (await _runtime.GetEventsAsync(context.TaskId))
            .Should().Contain(item => item.EventType == "RunCompleted");
    }

    [Fact]
    public async Task Conversation_Turn_With_An_Unresolved_Side_Effect_Ends_NeedsUser()
    {
        var conversationId = $"conversation-side-effect-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var context = await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Api,
            "What is the capital of Thailand?",
            $"conversation:{conversationId}"));
        context.Mode.Should().Be(AgentTaskMode.Conversation);
        var preparation = await _runtime.PrepareActionAsync(
            context, "call-1", "mock_state_transition",
            ToolCategory.Network, """{"state":"opened"}""");
        preparation.Proceed.Should().BeTrue();

        // The turn ends with the dispatch still outstanding — a killed bridge,
        // a lost answer. Closing it as "completed, no side effect" would be a
        // false success.
        await _runtime.CompleteConversationAsync(context, "sha256:ABCDEF");

        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        snapshot.Task.Status.Should().Be(AgentTaskStatus.NeedsUser);
        snapshot.Task.CompletedAtUtc.Should().BeNull();
        snapshot.Checkpoint!.ResumeSafetyStatus.Should().Be(
            ResumeSafetyStatus.ReconciliationRequired);
        var events = await _runtime.GetEventsAsync(context.TaskId);
        events.Should().Contain(item => item.EventType == "RunNeedsUser");
        events.Should().NotContain(item => item.EventType == "RunCompleted");
    }

    [Fact]
    public async Task Configured_Approval_Expiry_Flows_Into_The_Recorded_Approval()
    {
        var conversationId = $"approval-expiry-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var gateway = new RuntimeGateway();
        var tool = new RuntimeTool(
            ToolResult.Verified("mock install verified", new[] { "version=1.0" }),
            "run_command", ToolCategory.Shell);
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
                It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        // A non-default expiry proves the value is sourced from config, not hardcoded.
        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, _conversationStore,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime,
            stabilization: new RuntimeStabilizationOptions
            {
                ApprovalExpiry = TimeSpan.FromMinutes(45)
            });
        orchestrator.ConsentRequested += _ =>
            Task.FromResult(ToolConsentResponse.Once);
        gateway.Enqueue(
            ToolCallFrame("call-1", tool.Name, """{"command":"install mock"}"""),
            FinishToolCallsFrame,
            "[DONE]");
        gateway.Enqueue(ContentFrame("completed"), StopFrame, "[DONE]");

        var before = DateTimeOffset.UtcNow;
        var events = await RunOrchestratorAsync(
            orchestrator,
            [new GatewayMessage { Role = "user", Content = "install mock tool" }],
            conversationId);

        var taskId = events.OfType<AgentToolResultEvent>().Single().UnifiedResult!.TaskId;
        var granted = (await _taskStore.GetEventsAsync(taskId))
            .Single(item => item.EventType == "ApprovalGranted");
        using var doc = JsonDocument.Parse(granted.DataJson);
        var expiresAt = doc.RootElement.GetProperty("ExpiresAtUtc").GetDateTimeOffset();
        // ~45 minutes out — comfortably beyond the old hardcoded 5-minute default.
        (expiresAt - before).Should().BeGreaterThan(TimeSpan.FromMinutes(30));
    }

    private static DurableTaskRuntime NewRuntime(SqliteAgentTaskStore store) =>
        new(store, new GoalInterpreter(), new CapabilityRouter());

    private async Task<OrchestratorSetup> CreateOrchestratorAsync(ToolResult result)
    {
        var conversationId = $"orchestrator-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var gateway = new RuntimeGateway();
        var tool = new RuntimeTool(result);
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
                It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        var orchestrator = new ChatOrchestrator(
            gateway, host.Object, permissions.Object, _conversationStore,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        return new OrchestratorSetup(
            orchestrator, gateway, tool, conversationId);
    }

    private static async Task<List<AgentStreamEvent>> RunOrchestratorAsync(
        ChatOrchestrator orchestrator,
        List<GatewayMessage> messages,
        string conversationId)
    {
        var events = new List<AgentStreamEvent>();
        await foreach (var item in orchestrator.RunAsync(
                           "test-model", messages, conversationId))
            events.Add(item);
        return events;
    }

    private sealed record OrchestratorSetup(
        ChatOrchestrator Orchestrator,
        RuntimeGateway Gateway,
        RuntimeTool Tool,
        string ConversationId);

    private sealed class RuntimeGateway : IGatewayApiClient
    {
        private readonly Queue<string[]> _responses = new();

        public void Enqueue(params string[] frames) => _responses.Enqueue(frames);

        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            // Default to a bare stop once scripted responses run out, so an
            // orchestrator that legitimately loops again (e.g. R1 recovery nudges
            // on an incomplete task) sees an empty final turn instead of throwing.
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
            Task.FromResult(new GatewaySearchResponse(
                query, "test", 0, new List<GatewaySearchResultItem>()));
    }

    private sealed class RuntimeTool : IMcpTool
    {
        private readonly ToolResult _result;

        public RuntimeTool(
            ToolResult result,
            string name = "fake_tool",
            ToolCategory category = ToolCategory.Network)
        {
            _result = result;
            Name = name;
            Category = category;
        }

        public string Name { get; }
        public string Description => "Deterministic runtime integration tool.";
        public JsonElement ParametersSchema { get; } =
            JsonDocument.Parse("""{"type":"object"}""").RootElement.Clone();
        public ToolCategory Category { get; }
        public PermissionLevel DefaultPermission => PermissionLevel.Ask;
        public int Executions { get; private set; }

        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
        {
            Executions++;
            return Task.FromResult(_result);
        }
    }

    private sealed class SkipSettings : ISettingsService
    {
        public UserSettings Current { get; } = new() { ApprovalMode = "skip" };
        public bool IsFirstRun => false;
        public event EventHandler? SettingsChanged;
        public void Save(UserSettings settings) =>
            SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    private const string StopFrame =
        """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""";
    private const string FinishToolCallsFrame =
        """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""";

    private static string ContentFrame(string text) =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"content\":\"" + text +
        "\"},\"finish_reason\":null}]}";

    private static string ToolCallFrame(string id, string name, string args = "{}") =>
        "{\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"" +
        id + "\",\"type\":\"function\",\"function\":{\"name\":\"" + name +
        "\",\"arguments\":\"" + args.Replace("\\", "\\\\").Replace("\"", "\\\"") +
        "\"}}]},\"finish_reason\":null}]}";

    private async Task<DurableTurnContext> StartAsync(string request)
    {
        var conversationId = $"conversation-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        return await _runtime.StartOrResumeAsync(
                   conversationId,
                   new[] { new GatewayMessage { Role = "user", Content = request } },
                   $"conversation:{conversationId}")
               ?? throw new InvalidOperationException("The request was not actionable.");
    }

    private async Task CreateConversationAsync(string conversationId)
    {
        await _conversationStore.UpsertConversationAsync(new Conversation
        {
            Id = conversationId,
            Title = "Durable test",
            ModelId = "test",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
    }

    public void Dispose()
    {
        for (var attempt = 0; attempt < 5 && Directory.Exists(_directory); attempt++)
        {
            try
            {
                Directory.Delete(_directory, recursive: true);
            }
            catch (IOException) when (attempt < 4)
            {
                Thread.Sleep(25);
            }
        }
    }
}
