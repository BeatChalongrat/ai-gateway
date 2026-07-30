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

/// <summary>
/// RS-001 semantic regression (Phase 12): "open Calculator, calculate 125 × 48,
/// then write the result into Notepad." Driven deterministically through the
/// durable runtime's own gate/verify contract (PrepareAction → RecordResult) —
/// the same path ChatOrchestrator drives — with fake tools whose effects are
/// graded by runtime evidence, never by model output. This locks:
///   * the five-outcome graph decomposition (Calc ready → op processed →
///     display read = 6000 → Notepad ready → exact read-back),
///   * the deterministic semantic route (UIA/target-bound before raw input),
///   * zero false step/task completion, wrong-receiver, and unrelated actions.
/// It is a regression case, not the sole architecture target.
/// </summary>
public sealed class RS001RegressionTests : IDisposable
{
    private const string EnglishPrompt =
        "open Calculator and calculate 125 x 48 then write the result into Notepad";
    private const string ThaiPrompt =
        "เปิด Calculator แล้วคำนวณ 125 × 48 จากนั้นเขียนผลลง Notepad";

    private readonly string _directory;
    private readonly SqliteConversationStore _conversationStore;
    private readonly SqliteAgentTaskStore _taskStore;
    private readonly DurableTaskRuntime _runtime;

    public RS001RegressionTests()
    {
        _directory = Path.Combine(
            Path.GetTempPath(), $"sran-rs001-{Guid.NewGuid():N}");
        var databasePath = Path.Combine(_directory, "data.db");
        _conversationStore = new SqliteConversationStore(databasePath);
        _taskStore = new SqliteAgentTaskStore(databasePath);
        _runtime = new DurableTaskRuntime(
            _taskStore, new GoalInterpreter(), new CapabilityRouter());
    }

    [Theory]
    [InlineData(EnglishPrompt)]
    [InlineData(ThaiPrompt)]
    public async Task Compiles_The_Five_Outcome_Graph(string prompt)
    {
        var context = await StartAsync(prompt);
        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        var kinds = snapshot.Plan.Outcomes.Select(o => o.Kind).ToList();

        kinds.Should().ContainInOrder(
            OutcomeKind.ApplicationReady,   // Calculator input-ready
            OutcomeKind.OperationProcessed, // 125 × 48 processed
            OutcomeKind.StateObserved,      // display independently read = 6000
            OutcomeKind.ApplicationReady,   // Notepad input-ready
            OutcomeKind.TextValue);         // exact result read-back
        snapshot.Plan.Outcomes.Should().OnlyContain(o => o.Required);
        // The observed-value outcome carries the exact evaluated result.
        snapshot.Plan.Outcomes
            .Single(o => o.Kind == OutcomeKind.StateObserved)
            .ExpectedValue.Should().Be("6000");
        // Every step is linked to an outcome; none is a monolithic multi-app step.
        snapshot.Plan.Milestones.SelectMany(m => m.Steps)
            .Should().OnlyContain(s => !string.IsNullOrEmpty(s.OutcomeId));
    }

    [Theory]
    [InlineData(EnglishPrompt)]
    [InlineData(ThaiPrompt)]
    public async Task Deterministic_20_Attempts_Complete_With_Zero_False_Success(string prompt)
    {
        for (var attempt = 1; attempt <= 20; attempt++)
        {
            var report = await RunOneAttemptAsync(prompt, attempt);

            report.VerifiedOutcomes.Should().Be(5,
                $"attempt {attempt} must verify all five outcomes");
            report.Completed.Should().BeTrue($"attempt {attempt} must reach the DoD");
            report.FalseSuccess.Should().BeFalse($"attempt {attempt} false success");
            report.WrongReceiver.Should().BeFalse($"attempt {attempt} wrong receiver");
            report.UnrelatedActions.Should().Be(0, $"attempt {attempt} unrelated actions");
            // The Calculator operation must route through the UIA semantic path,
            // not raw pixel or browser DOM (RS-001 expected preferred route).
            report.OperationCapability.Should().Be("uia_click");
            report.TextCapability.Should().Be("uia_set_value");
        }
    }

    [Fact]
    public async Task Open_Application_Cannot_Complete_The_Calculate_Step()
    {
        var context = await StartAsync(EnglishPrompt);
        // Verify only the Calculator-ready step so the calculate step is next.
        context = await VerifyReadyStepAsync(
            context, "open_application", ToolCategory.Screen,
            """{"application":"Calculator"}""",
            "Calculator window is visible and input-ready",
            ReadinessEvidence("Calculator"));

        // Re-using open_application against the calculate step must be rejected:
        // an application-ready effect can never satisfy an operation outcome.
        var prep = await _runtime.PrepareActionAsync(
            context, "call-x", "open_application", ToolCategory.Screen,
            """{"application":"Calculator"}""");
        prep.Proceed.Should().BeFalse();
        prep.FailureCode.Should().Be("GoalIrrelevantAction");
    }

    [Fact]
    public async Task Unrelated_File_Read_Is_Blocked_As_Goal_Irrelevant()
    {
        var context = await StartAsync(EnglishPrompt);

        var prep = await _runtime.PrepareActionAsync(
            context, "call-docs", "list_directory", ToolCategory.FileSystem,
            """{"path":"Documents"}""");

        prep.Proceed.Should().BeFalse();
        prep.FailureCode.Should().Be("GoalIrrelevantAction");
    }

    [Fact]
    public async Task Injected_Answer_Without_Independent_Read_Cannot_Verify_Operation()
    {
        var context = await StartAsync(EnglishPrompt);
        context = await VerifyReadyStepAsync(
            context, "open_application", ToolCategory.Screen,
            """{"application":"Calculator"}""",
            "Calculator ready", ReadinessEvidence("Calculator"));

        // The operation step: a tool that merely *claims* success with no
        // independent effect evidence must not verify the step.
        var prep = await _runtime.PrepareActionAsync(
            context, "call-op", "uia_click", ToolCategory.Screen,
            """{"application":"Calculator","target_description":"equals button"}""");
        prep.Proceed.Should().BeTrue($"{prep.FailureCode}: {prep.FailureReason}");
        var outcome = await _runtime.RecordResultAsync(
            context, prep,
            ToolResult.ExecutedUnverified(
                "clicked", "EffectNotObserved", "no display was read"),
            5);

        outcome.Result.Verified.Should().BeFalse();
        outcome.Result.StepCompleted.Should().BeFalse();
    }

    // ── attempt driver ───────────────────────────────────────────────────

    private sealed record AttemptReport(
        int VerifiedOutcomes,
        bool Completed,
        bool FalseSuccess,
        bool WrongReceiver,
        int UnrelatedActions,
        string? OperationCapability,
        string? TextCapability);

    private async Task<AttemptReport> RunOneAttemptAsync(string prompt, int attempt)
    {
        var context = await StartAsync(prompt);

        // 1 — Calculator application input-ready.
        context = await VerifyReadyStepAsync(
            context, "open_application", ToolCategory.Screen,
            """{"application":"Calculator"}""",
            "Calculator window is visible and input-ready",
            ReadinessEvidence("Calculator"));

        // 2 — Operation processed (125 × 48) via UIA semantic control.
        var (opContext, opCapability) = await VerifyReadyStepTracedAsync(
            context, "uia_click", ToolCategory.Screen,
            """{"application":"Calculator","target_description":"equals button","sequence":"125 * 48 ="}""",
            "Calculator processed 125 × 48",
            new[] { "receiver=Calculator", "display=6000", "control=CalculatorResults" });
        context = opContext;

        // 3 — Display independently read = 6000 (observation, not injection).
        context = await VerifyReadyStepAsync(
            context, "read_screen_elements", ToolCategory.Screen,
            """{"application":"Calculator","target":"display"}""",
            "Calculator display independently read = 6000",
            new[] { "display_value=6000", "receiver=Calculator" });

        // 4 — Notepad editor input-ready.
        context = await VerifyReadyStepAsync(
            context, "open_application", ToolCategory.Screen,
            """{"application":"Notepad"}""",
            "Notepad editor is visible and input-ready",
            ReadinessEvidence("Notepad"));

        // 5 — Exact result read-back in the bound Notepad control. RS-001's
        // preferred editor route is UIA ValuePattern (uia_set_value); the
        // runtime ranks it above raw target-bound typing (computer_type).
        var (textContext, textCapability) = await VerifyReadyStepTracedAsync(
            context, "uia_set_value", ToolCategory.Screen,
            """{"application":"Notepad","target_description":"editor","value":"6000"}""",
            "Notepad contains exactly 6000",
            new[]
            {
                "hwnd=0x2222", "pid=6789", "control=NotepadEdit",
                "value_read_back=true", "read_back_value=6000",
            });
        context = textContext;

        var completion = await _runtime.EvaluateCompletionAsync(context);
        var snapshot = (await _taskStore.GetAsync(context.TaskId))!;
        var verified = snapshot.Plan.Outcomes
            .Count(o => o.Status == OutcomeStatus.Verified);
        // False success = the runtime marking the task complete while any
        // required outcome remains unverified.
        var falseSuccess = completion.Completed
                           && snapshot.Plan.Outcomes.Any(o => o.Required
                               && o.Status != OutcomeStatus.Verified);
        var trace = await _taskStore.GetEventsAsync(context.TaskId);
        var unrelated = trace.Count(e => e.EventType == "ActionRejected");
        var wrongReceiver = trace.Any(e => e.EventType == "RoutingDecisionRejected");

        return new AttemptReport(
            verified, completion.Completed, falseSuccess, wrongReceiver,
            unrelated, opCapability, textCapability);
    }

    private async Task<DurableTurnContext> VerifyReadyStepAsync(
        DurableTurnContext context, string tool, ToolCategory category,
        string argsJson, string content, IReadOnlyList<string> evidence)
    {
        var (next, _) = await VerifyReadyStepTracedAsync(
            context, tool, category, argsJson, content, evidence);
        return next;
    }

    private async Task<(DurableTurnContext Context, string Capability)>
        VerifyReadyStepTracedAsync(
            DurableTurnContext context, string tool, ToolCategory category,
            string argsJson, string content, IReadOnlyList<string> evidence)
    {
        var callId = $"call-{Guid.NewGuid():N}";
        var prep = await _runtime.PrepareActionAsync(
            context, callId, tool, category, argsJson);
        prep.Proceed.Should().BeTrue(
            $"step for '{tool}' should route ({prep.FailureCode}: {prep.FailureReason})");
        var selected = prep.Route!.SelectedCapability;
        var outcome = await _runtime.RecordResultAsync(
            context, prep, ToolResult.Verified(content, evidence), 5);
        outcome.Result.Verified.Should().BeTrue(
            $"step for '{tool}' should verify from independent evidence");
        outcome.Result.StepCompleted.Should().BeTrue();
        return (outcome.Context, selected);
    }

    private static string[] ReadinessEvidence(string application) =>
    [
        $"application={application}", "pid=1234", "hwnd=0x1111",
        "visibility=true", "foreground=true",
        "input_receiver=true", "readiness=InputReady",
    ];

    private async Task<string> CreateConversationAsync()
    {
        var conversationId = $"rs001-{Guid.NewGuid():N}";
        await _conversationStore.UpsertConversationAsync(new Conversation
        {
            Id = conversationId,
            Title = "RS-001",
            ModelId = "test",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local,
        });
        return conversationId;
    }

    private async Task<DurableTurnContext> StartAsync(string prompt)
    {
        var conversationId = await CreateConversationAsync();
        return await _runtime.StartOrResumeAsync(
                   conversationId,
                   new[] { new GatewayMessage { Role = "user", Content = prompt } },
                   $"conversation:{conversationId}")
               ?? throw new InvalidOperationException("RS-001 prompt was not actionable.");
    }

    // ── end-to-end through ChatOrchestrator (user-facing path) ────────────

    [Fact]
    public async Task Orchestrator_Path_Completes_RS001_From_Verified_Evidence()
    {
        var conversationId = await CreateConversationAsync();
        var gateway = new ScriptGateway();
        var host = new MultiToolHost(new Dictionary<string, (ToolResult, ToolCategory)>
        {
            ["open_application"] = (
                ToolResult.Verified("application input-ready", ReadinessEvidence("app")),
                ToolCategory.Screen),
            ["uia_click"] = (
                ToolResult.Verified("processed 125 × 48",
                    new[] { "receiver=Calculator", "display=6000", "control=CalculatorResults" }),
                ToolCategory.Screen),
            ["read_screen_elements"] = (
                ToolResult.Verified("display read",
                    new[] { "display_value=6000", "receiver=Calculator" }),
                ToolCategory.Screen),
            ["uia_set_value"] = (
                ToolResult.Verified("value set in bound control",
                    new[]
                    {
                        "hwnd=0x2222", "pid=6789", "control=NotepadEdit",
                        "value_read_back=true", "read_back_value=6000",
                    }),
                ToolCategory.Screen),
        });
        var permissions = new Mock<IPermissionService>();
        permissions.Setup(p => p.CheckAsync(
                It.IsAny<IMcpTool>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(PermissionDecision.Auto);
        var orchestrator = new ChatOrchestrator(
            gateway, host, permissions.Object, _conversationStore,
            NullLogger<ChatOrchestrator>.Instance, durableRuntime: _runtime);

        gateway.Enqueue(ToolCallFrame("c1", "open_application",
            """{"application":"Calculator"}"""), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ToolCallFrame("c2", "uia_click",
            """{"application":"Calculator","target_description":"equals button"}"""),
            FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ToolCallFrame("c3", "read_screen_elements",
            """{"application":"Calculator","target":"display"}"""),
            FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ToolCallFrame("c4", "open_application",
            """{"application":"Notepad"}"""), FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ToolCallFrame("c5", "uia_set_value",
            """{"application":"Notepad","target_description":"editor","value":"6000"}"""),
            FinishToolCallsFrame, "[DONE]");
        gateway.Enqueue(ContentFrame("All done."), StopFrame, "[DONE]");

        var events = new List<AgentStreamEvent>();
        await foreach (var item in orchestrator.RunAsync(
                           "test-model",
                           new List<GatewayMessage>
                           {
                               new() { Role = "user", Content = EnglishPrompt },
                           },
                           conversationId))
            events.Add(item);

        var results = events.OfType<AgentToolResultEvent>().ToList();
        results.Should().HaveCount(5, "one durable-graded result per RS-001 step");
        results.Should().OnlyContain(r => r.UnifiedResult != null && r.UnifiedResult.Verified,
            "every step is graded by independent runtime evidence");
        results[1].UnifiedResult!.ToolName.Should().Be("uia_click");
        results[4].UnifiedResult!.ToolName.Should().Be("uia_set_value");

        var taskId = results[0].UnifiedResult!.TaskId;
        (await _taskStore.GetAsync(taskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Completed);
        var trace = (await _taskStore.GetEventsAsync(taskId))
            .Select(e => e.EventType).ToList();
        trace.Should().Contain("TaskCompleted");
        trace.Should().NotContain("RoutingDecisionRejected");
        trace.Should().NotContain("ActionRejected");
    }

    private sealed class ScriptGateway : IGatewayApiClient
    {
        private readonly Queue<string[]> _responses = new();

        public void Enqueue(params string[] frames) => _responses.Enqueue(frames);

        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            foreach (var frame in _responses.Dequeue())
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

    private sealed class MultiToolHost : IMcpToolHost
    {
        private readonly Dictionary<string, ScriptedTool> _tools;

        public MultiToolHost(Dictionary<string, (ToolResult Result, ToolCategory Category)> spec) =>
            _tools = spec.ToDictionary(
                pair => pair.Key,
                pair => new ScriptedTool(pair.Key, pair.Value.Result, pair.Value.Category));

        public IReadOnlyList<IMcpTool> GetRegisteredTools() => _tools.Values.ToList<IMcpTool>();

        public IMcpTool? Resolve(string name) =>
            _tools.TryGetValue(name, out var tool) ? tool : null;

        public IReadOnlyList<GatewayToolDefinition> GetToolDefinitions() =>
            _tools.Values.Select(tool => new GatewayToolDefinition
            {
                Function = new GatewayFunctionDef
                {
                    Name = tool.Name,
                    Description = tool.Description,
                    Parameters = tool.ParametersSchema,
                },
            }).ToList();
    }

    private sealed class ScriptedTool : IMcpTool
    {
        private readonly ToolResult _result;

        public ScriptedTool(string name, ToolResult result, ToolCategory category)
        {
            Name = name;
            _result = result;
            Category = category;
        }

        public string Name { get; }
        public string Description => "RS-001 deterministic tool.";
        public JsonElement ParametersSchema { get; } =
            JsonDocument.Parse("""{"type":"object"}""").RootElement.Clone();
        public ToolCategory Category { get; }
        public PermissionLevel DefaultPermission => PermissionLevel.Ask;

        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
            Task.FromResult(_result);
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

    public void Dispose()
    {
        for (var attempt = 0; attempt < 5 && Directory.Exists(_directory); attempt++)
        {
            try
            {
                Directory.Delete(_directory, recursive: true);
                break;
            }
            catch (IOException) when (attempt < 4)
            {
                Thread.Sleep(50);
            }
        }
    }
}
