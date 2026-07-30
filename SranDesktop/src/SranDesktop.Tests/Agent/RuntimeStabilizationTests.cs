using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Agent;

public sealed class RuntimeStabilizationTests
{
    [Fact]
    public void Rs001_Compiles_Five_Correlated_Outcomes()
    {
        var graph = Compile("เปิด Calculator แล้วคำนวณ 125 × 48 จากนั้นเขียนผลลง Notepad");

        graph.Outcomes.Should().HaveCount(5);
        graph.Outcomes.Select(item => item.Kind).Should().Equal(
            OutcomeKind.ApplicationReady,
            OutcomeKind.OperationProcessed,
            OutcomeKind.StateObserved,
            OutcomeKind.ApplicationReady,
            OutcomeKind.TextValue);
        graph.Outcomes[2].ExpectedValue.Should().Be("6000");
        graph.Outcomes[4].ExpectedValue.Should().Be($"outcome:{graph.Outcomes[2].OutcomeId}");
        graph.Outcomes.Skip(1).Should().OnlyContain(item => item.Dependencies.Count == 1);
        graph.Steps.Should().HaveCount(5).And.OnlyContain(step => step.OutcomeId != null);
        graph.DefinitionOfDone.Should().Contain(item => item.OutcomeId == graph.Outcomes[4].OutcomeId);
    }

    [Fact]
    public void English_Cross_Application_Request_Compiles_Complete_Chain()
    {
        var graph = Compile(
            "open Calculator then calculate 87 * 36 and then write the result into Notepad");

        graph.Outcomes.Should().HaveCount(5);
        graph.Outcomes[2].ExpectedValue.Should().Be("3132");
        graph.Outcomes.Should().OnlyContain(item => item.Required);
    }

    [Fact]
    public void Open_Then_Type_Without_Separator_Still_Compiles_ApplicationReady_First()
    {
        var graph = Compile("เปิด Notepad พิมพ์ข้อความ \"X\" แล้วบันทึกไฟล์ชื่อ a.txt");

        graph.Outcomes[0].Kind.Should().Be(OutcomeKind.ApplicationReady);
        graph.Outcomes[0].ExpectedApplication.Should().Be("Notepad");
    }

    [Fact]
    public void Open_Then_Type_Without_Separator_Preserves_Text_Entry_Intent()
    {
        var graph = Compile("เปิด Notepad พิมพ์ข้อความ \"X\"");

        graph.Outcomes[0].Kind.Should().Be(OutcomeKind.ApplicationReady);
        graph.Outcomes.Should().Contain(item =>
            item.Kind == OutcomeKind.TextValue
            && item.ExpectedApplication == "Notepad"
            && item.ExpectedValue == "X");
    }

    [Fact]
    public void Open_Then_Separator_Still_Compiles_ApplicationReady_First()
    {
        var graph = Compile("เปิด Notepad แล้วพิมพ์ข้อความ \"Y\"");

        graph.Outcomes[0].Kind.Should().Be(OutcomeKind.ApplicationReady);
        graph.Outcomes[0].ExpectedApplication.Should().Be("Notepad");
    }

    [Fact]
    public void Pure_Calculation_Does_Not_Force_ApplicationReady()
    {
        var graph = Compile("คำนวณ 23*47");

        graph.Outcomes.Should().NotContain(item => item.Kind == OutcomeKind.ApplicationReady);
    }

    [Fact]
    public void Known_Content_File_Outcome_Recommends_Write_File_First()
    {
        var withContent = new TaskOutcome
        {
            OutcomeId = "o", TaskId = "t", Description = "save the note",
            IntendedState = "note.txt contains the text", Kind = OutcomeKind.FileState,
            ExpectedValue = "hello world", SourceGoalVersion = 1
        };

        CapabilityRouter.RecommendedCapabilities(withContent)[0].Should().Be("write_file");
    }

    [Fact]
    public void File_Outcome_Without_Known_Content_Does_Not_Force_Write_File_First()
    {
        var noContent = new TaskOutcome
        {
            OutcomeId = "o", TaskId = "t", Description = "save what is on screen",
            IntendedState = "the document is saved", Kind = OutcomeKind.FileState,
            SourceGoalVersion = 1
        };

        CapabilityRouter.RecommendedCapabilities(noContent)[0].Should().NotBe("write_file");
    }

    [Fact]
    public void File_Saving_Guidance_Steers_Toward_Write_File_Over_Save_As()
    {
        ChatViewModel.FileSavingGuidance.Should().Contain("write_file");
        ChatViewModel.FileSavingGuidance.Should().Contain("Save As");
        ChatViewModel.FileSavingGuidance.Should().Contain("Do NOT");
    }

    [Fact]
    public void Desktop_Text_Entry_Recommends_Uia_Set_Value_First()
    {
        var desktop = new TaskOutcome
        {
            OutcomeId = "o", TaskId = "t", Description = "type into Notepad",
            IntendedState = "Notepad contains the text", Kind = OutcomeKind.TextValue,
            ExpectedApplication = "Notepad", ExpectedValue = "hello", SourceGoalVersion = 1
        };

        var recommended = CapabilityRouter.RecommendedCapabilities(desktop);
        recommended[0].Should().Be("uia_set_value");
        recommended.Should().NotContain("browser_dom");
        // computer_type stays available as a last-resort fallback, never first.
        recommended.Should().Contain("computer_type");
    }

    [Fact]
    public void Browser_Text_Entry_Recommends_Browser_Dom_First()
    {
        var browser = new TaskOutcome
        {
            OutcomeId = "o", TaskId = "t", Description = "fill the web form",
            IntendedState = "the field contains the text", Kind = OutcomeKind.TextValue,
            ExpectedApplication = "Google Chrome", ExpectedValue = "hello", SourceGoalVersion = 1
        };

        CapabilityRouter.RecommendedCapabilities(browser)[0].Should().Be("browser_dom");
    }

    [Fact]
    public void Typing_Guidance_Is_Present_Alongside_File_Saving_Guidance()
    {
        ChatViewModel.TypingGuidance.Should().Contain("uia_set_value");
        ChatViewModel.TypingGuidance.Should().Contain("read_screen_elements");
        ChatViewModel.TypingGuidance.Should().Contain("Do NOT use computer_type");
        // Both guidance blocks coexist (both are injected into the system prompt).
        ChatViewModel.FileSavingGuidance.Should().Contain("write_file");
    }

    [Fact]
    public void Capability_That_Failed_The_Step_Limit_Is_Exhausted_But_Verified_Paths_Are_Not()
    {
        var actions = new[]
        {
            FailedAction("computer_type"),
            FailedAction("computer_type")
        };

        // computer_type failed twice on this step → exhausted, must switch approach.
        DurableTaskRuntime.IsCapabilityExhaustedForStep("computer_type", actions).Should().BeTrue();
        // The verified text path and re-observation are NEVER blocked by anti-churn.
        DurableTaskRuntime.IsCapabilityExhaustedForStep("uia_set_value", actions).Should().BeFalse();
        DurableTaskRuntime.IsCapabilityExhaustedForStep("read_screen_elements", actions)
            .Should().BeFalse();
    }

    [Fact]
    public void A_Single_Capability_Failure_Is_Not_Yet_Exhausted()
    {
        var actions = new[] { FailedAction("computer_type") };

        DurableTaskRuntime.IsCapabilityExhaustedForStep("computer_type", actions).Should().BeFalse();
    }

    // ── T3 PART 2: verify from a post-type observation of the same control ─────

    [Fact]
    public void Post_Type_Observation_With_Real_Observed_Value_Verifies_Text()
    {
        var (outcome, step, action) = Correlated(OutcomeKind.TextValue, "read_screen_elements");
        outcome = outcome with { ExpectedValue = "hello world" };

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified("observed",
                ["hwnd=13579", "pid=42", "observed_value=hello world"]),
            true);

        result.Matched.Should().BeTrue();
    }

    [Fact]
    public void Observation_Whose_Description_Echoes_The_Text_But_Has_No_Read_Value_Does_Not_Verify()
    {
        // T4 REPRODUCE: the bug shape — an observation bound to a window whose free-text
        // CONTENT happens to contain the expected phrase, but with NO real observed_value
        // token. This must NOT verify (the old code matched the content blob).
        var (outcome, step, action) = Correlated(OutcomeKind.TextValue, "read_screen_elements");
        outcome = outcome with { ExpectedValue = "SRAN T3 ladder works" };

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified(
                "The user asked to type 'SRAN T3 ladder works' into Notepad.",
                ["hwnd=13579", "pid=42"]),
            true);

        result.Matched.Should().BeFalse();
        result.FailureCode.Should().Be("EffectOutcomeMismatch");
    }

    [Fact]
    public void Typed_Text_With_No_Real_Read_Back_Is_Not_Verified_Even_If_Content_Echoes_It()
    {
        // T4 REPRODUCE (typing side): computer_type "succeeds" reporting the intended
        // text in its content/description, but emits NO read_back_value token because the
        // control could not be read. Empty Notepad → must NOT verify.
        var (outcome, step, action) = Correlated(OutcomeKind.TextValue, "computer_type");
        outcome = outcome with { ExpectedValue = "SRAN T3 ladder works" };

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified(
                "Typed 'SRAN T3 ladder works' into the bound control.",
                ["hwnd=13579", "pid=42", "value_read_back=false", "os_input_accepted=true"]),
            true);

        result.Matched.Should().BeFalse();
    }

    [Fact]
    public void Read_Back_Value_That_Does_Not_Match_Yields_Value_Mismatch()
    {
        var (outcome, step, action) = Correlated(OutcomeKind.TextValue, "uia_set_value");
        outcome = outcome with { ExpectedValue = "SRAN T3 ladder works" };

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified("set",
                ["hwnd=13579", "pid=42", "read_back_value=something entirely different"]),
            true);

        result.Matched.Should().BeFalse();
        result.FailureCode.Should().Be("EffectValueMismatch");
    }

    [Fact]
    public void Real_Read_Back_Value_That_Matches_Verifies_Text()
    {
        var (outcome, step, action) = Correlated(OutcomeKind.TextValue, "uia_set_value");
        outcome = outcome with { ExpectedValue = "SRAN T3 ladder works" };

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified("set",
                ["hwnd=13579", "pid=42", "read_back_value=SRAN T3 ladder works"]),
            true);

        result.Matched.Should().BeTrue();
    }

    // ── T3 PART 4: anti-flail helpers ─────────────────────────────────────────

    [Fact]
    public void Typing_Exhaustion_Covers_Uia_Set_Value_Too()
    {
        var actions = new[]
        {
            FailedAction("computer_type"),
            FailedAction("uia_set_value"),
            FailedAction("computer_type"),
            FailedAction("uia_set_value")
        };

        // Aggregate typing failures reach the limit → typing is exhausted even though
        // no single capability hit its own per-capability limit alone.
        DurableTaskRuntime.IsTypingExhaustedForStep(actions).Should().BeTrue();
    }

    [Fact]
    public void A_Few_Typing_Failures_Are_Not_Yet_Exhausted()
    {
        var actions = new[] { FailedAction("computer_type"), FailedAction("uia_set_value") };

        DurableTaskRuntime.IsTypingExhaustedForStep(actions).Should().BeFalse();
    }

    [Theory]
    [InlineData("computer_key", "{\"sequence\":[\"Alt+F4\"]}", true)]
    [InlineData("computer_key", "{\"keys\":\"alt+f4\"}", true)]
    [InlineData("run_command", "{\"command\":\"taskkill /IM notepad.exe\"}", true)]
    [InlineData("computer_key", "{\"sequence\":[\"Ctrl+A\"]}", false)]
    [InlineData("uia_set_value", "{\"text\":\"Alt+F4 is just text\"}", false)]
    public void Destructive_Window_Actions_Are_Detected(string tool, string args, bool expected)
    {
        DurableTaskRuntime.IsDestructiveWindowAction(tool, args).Should().Be(expected);
    }

    // ── T3 PART 3: Auto routes desktop/computer-use work to the vision model ───

    [Theory]
    [InlineData("เปิด Notepad แล้วพิมพ์ข้อความ")]
    [InlineData("open Notepad and type hello")]
    [InlineData("take a screenshot and click the button")]
    [InlineData("เปิดเครื่องคิดเลขแล้วคำนวณ")]
    public void Auto_Picks_Vision_Model_For_Computer_Use(string prompt)
    {
        ChatViewModel.PreferredAutoModel(prompt).Should().Be("gpt-4o");
    }

    [Theory]
    [InlineData("What is the capital of France?")]
    [InlineData("เขียนกลอนให้หน่อย")]
    [InlineData("")]
    public void Auto_Keeps_Mini_For_Non_Desktop_Work(string prompt)
    {
        ChatViewModel.PreferredAutoModel(prompt).Should().Be("gpt-4o-mini");
    }

    public static IEnumerable<object[]> HundredMultiOutcomePrompts()
    {
        for (var index = 1; index <= 50; index++)
            yield return [$"open Calculator then calculate {index} * 2"];
        for (var index = 51; index <= 100; index++)
            yield return [$"เปิด Calculator แล้วคำนวณ {index} × 2"];
    }

    [Theory]
    [MemberData(nameof(HundredMultiOutcomePrompts))]
    public void Hundred_Thai_English_Prompts_Preserve_All_Outcomes(string prompt)
    {
        var graph = Compile(prompt);

        graph.Outcomes.Should().HaveCount(3);
        graph.Steps.Select(step => step.OutcomeId).Should().OnlyHaveUniqueItems();
        graph.Outcomes[1].Dependencies.Should().Equal(graph.Outcomes[0].OutcomeId);
        graph.Outcomes[2].Dependencies.Should().Equal(graph.Outcomes[1].OutcomeId);
    }

    [Fact]
    public void Plan_Revision_Dropping_Required_Outcome_Is_Rejected()
    {
        var graph = Compile("open Calculator then calculate 4 * 5");
        var result = new PlanCoverageValidator().Validate(
            Goal("open Calculator then calculate 4 * 5"),
            Plan(graph),
            graph.Outcomes,
            graph.Steps.Take(2).ToList(),
            []);

        result.Accepted.Should().BeFalse();
        result.FailureCode.Should().Be("GoalCoverageFailure");
        result.MissingOutcomeIds.Should().Contain(graph.Outcomes[2].OutcomeId);
    }

    [Fact]
    public void Plan_Revision_Dropping_Constraint_Is_Rejected()
    {
        var goal = Goal("open Notepad but do not save", ["do not save"]);
        var graph = Compile(goal);

        var result = new PlanCoverageValidator().Validate(
            goal, Plan(graph), graph.Outcomes, graph.Steps, []);

        result.Accepted.Should().BeFalse();
        result.FailureCode.Should().Be("PlanDrift");
    }

    [Fact]
    public void Verified_Outcome_Cannot_Be_Removed()
    {
        var graph = Compile("open Calculator then calculate 2 * 3");
        graph.Outcomes[0].Status = OutcomeStatus.Verified;
        var remaining = graph.Outcomes.Skip(1).ToList();

        var result = new PlanCoverageValidator().Validate(
            Goal("open Calculator then calculate 2 * 3"),
            Plan(graph), remaining, graph.Steps.Skip(1).ToList(), []);

        result.Accepted.Should().BeFalse();
        result.FailureCode.Should().Be("PlanDrift");
    }

    [Fact]
    public void Open_Application_Evidence_Cannot_Verify_Calculation_Outcome()
    {
        var (outcome, step, action) = Correlated(OutcomeKind.OperationProcessed, "open_application");
        var raw = ToolResult.Verified(
            "Calculator opened.", ["pid=10", "hwnd=20", "input_receiver=true"]);

        var result = new SemanticOutcomeMatcher().Match(outcome, step, action, raw, true);

        result.Matched.Should().BeFalse();
        result.FailureCode.Should().Be("EffectOutcomeMismatch");
    }

    [Fact]
    public void Computer_Click_With_Matching_App_Name_But_No_Confirmed_Window_Identity_Is_WrongWindow()
    {
        // The action's args/content name the right application ("Calculator"), but
        // target acquisition never confirmed the window (no HWND/PID, unvalidated).
        // A matching NAME is not proof — the effect must be rejected as WrongWindow.
        var (outcome, step, action) = Correlated(OutcomeKind.OperationProcessed, "computer_click");
        outcome = outcome with { ExpectedApplication = "Calculator" };
        var raw = ToolResult.FromComputerAction(
            "Clicked the equals button in Calculator.",
            ComputerAction(effectVerified: true, targetValidated: false,
                windowHandle: null, processId: null));

        var result = new SemanticOutcomeMatcher().Match(outcome, step, action, raw, true);

        result.Matched.Should().BeFalse();
        result.FailureCode.Should().Be("WrongWindow");
    }

    [Fact]
    public void Computer_Click_With_Confirmed_Hwnd_Pid_On_Validated_Target_Verifies()
    {
        // Same outcome, but now resolution confirmed a concrete window identity —
        // real HWND + PID on a validated target — so the effect verifies.
        var (outcome, step, action) = Correlated(OutcomeKind.OperationProcessed, "computer_click");
        outcome = outcome with { ExpectedApplication = "Calculator" };
        var raw = ToolResult.FromComputerAction(
            "Clicked the equals button.",
            ComputerAction(effectVerified: true, targetValidated: true,
                windowHandle: 0x1234, processId: 4242));

        var result = new SemanticOutcomeMatcher().Match(outcome, step, action, raw, true);

        result.Matched.Should().BeTrue();
    }

    private static ComputerActionResult ComputerAction(
        bool effectVerified, bool targetValidated, long? windowHandle, int? processId) =>
        new(
            ActionRequested: true,
            TargetResolved: true,
            TargetValidated: targetValidated,
            ExecutionAttempted: true,
            OsInputAccepted: true,
            EffectVerified: effectVerified,
            TaskCompleted: effectVerified,
            FailureCode: null,
            FailureReason: null,
            Retryable: false,
            Evidence: Array.Empty<string>(),
            CaptureBeforeId: "before",
            CaptureAfterId: effectVerified ? "after" : null,
            WindowHandle: windowHandle,
            ProcessId: processId,
            ExecutedX: 10,
            ExecutedY: 20,
            TargetX: 0,
            TargetY: 0,
            TargetWidth: 20,
            TargetHeight: 40,
            DurationMs: 5);

    [Fact]
    public void Native_Success_Without_Independent_Effect_Is_Not_Verified()
    {
        var (outcome, step, action) = Correlated(OutcomeKind.TextValue, "computer_type");

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action, ToolResult.Ok("SendInput accepted."), false);

        result.Matched.Should().BeFalse();
        result.FailureCode.Should().Be("EffectNotObserved");
    }

    [Fact]
    public void Runtime_Selects_Uia_Value_Before_Generic_Type()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.TextValue, "computer_type");
        var capabilities = new[]
        {
            Capability("uia_set_value", verification: true),
            Capability("computer_type", verification: true)
        };

        var route = new CapabilityRouter().Route(
            step, outcome, "computer_type", ToolCategory.Screen,
            "{\"application\":\"Notepad\"}", null, capabilities);

        route.Allowed.Should().BeFalse();
        route.FailureCode.Should().Be("CapabilitySelectionRequired");
        route.SelectedCapability.Should().Be("uia_set_value");
        route.Candidates.Should().Contain(item => item.CapabilityId == "uia_set_value");
    }

    [Fact]
    public void Runtime_Selects_Explicit_Browser_Transfer_Over_Generic_Click()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.BrowserState, "browser_upload");
        outcome = outcome with
        {
            Description = "Upload the authorized file and download the fixture result.",
            IntendedState = "The upload and download are independently verified."
        };
        step = step with { SideEffectClass = SideEffectClass.Consequential };
        var capabilities = new[]
        {
            Capability("browser_click", verification: true),
            Capability("browser_upload", verification: true),
            Capability("browser_download", verification: true)
        };

        var route = new CapabilityRouter().Route(
            step,
            outcome,
            "browser_upload",
            ToolCategory.Browser,
            """{"path":"authorized-upload.txt"}""",
            null,
            capabilities);

        route.Allowed.Should().BeTrue();
        route.SelectedCapability.Should().Be("browser_upload");
        route.SideEffectClass.Should().Be(SideEffectClass.Consequential);
    }

    [Fact]
    public void Referential_Verify_Both_Strengthens_Prior_Outcomes_Without_Fake_Step()
    {
        var compiled = Compile(
            "Upload authorized.txt to the browser, then download result.txt and verify both");

        compiled.Outcomes.Should().HaveCount(2);
        compiled.Steps.Should().HaveCount(2);
        compiled.Outcomes.Should().OnlyContain(outcome =>
            outcome.VerificationRequirements.Contains(
                "Retain independent evidence through final run completion."));
    }

    [Fact]
    public void Browser_Upload_Is_Irrelevant_When_Outcome_Does_Not_Request_Upload()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.BrowserState, "browser_upload");
        outcome = outcome with
        {
            Description = "Navigate to the fixture page.",
            IntendedState = "The page is visible."
        };

        var result = new GoalRelevanceGate().Evaluate(
            Goal("Navigate to the fixture page."),
            outcome,
            step,
            "browser_upload",
            """{"path":"authorized-upload.txt"}""");

        result.Allowed.Should().BeFalse();
        result.FailureCode.Should().Be("GoalIrrelevantAction");
    }

    [Fact]
    public void Unrelated_Documents_Read_Is_Blocked_For_Calculator_Result()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.StateObserved, "list_directory");
        outcome = outcome with { ExpectedApplication = "Calculator" };

        var result = new GoalRelevanceGate().Evaluate(
            Goal("read Calculator result"), outcome, step,
            "list_directory", "{\"path\":\"Documents\"}");

        result.Allowed.Should().BeFalse();
        result.FailureCode.Should().Be("GoalIrrelevantAction");
    }

    [Fact]
    public void Open_Application_Is_Allowed_As_Enabling_Precondition_For_Text_Outcome()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.TextValue, "open_application");
        outcome = outcome with { ExpectedApplication = "Notepad" };

        var result = new GoalRelevanceGate().Evaluate(
            Goal("เปิด Notepad พิมพ์ข้อความ"), outcome, step,
            "open_application", "{\"application\":\"Notepad\"}");

        result.Allowed.Should().BeTrue();
    }

    [Fact]
    public void Open_Application_Is_Allowed_As_Enabling_Precondition_For_File_Outcome()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.FileState, "open_application");

        var result = new GoalRelevanceGate().Evaluate(
            Goal("save a file after opening the editor"), outcome, step,
            "open_application", "{\"application\":\"Notepad\"}");

        result.Allowed.Should().BeTrue();
    }

    [Fact]
    public void Open_Application_Routes_Successfully_On_A_File_Outcome()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.FileState, "open_application");
        var capabilities = new[] { Capability("open_application", verification: true) };

        var route = new CapabilityRouter().Route(
            step, outcome, "open_application", ToolCategory.Screen,
            "{\"application\":\"Notepad\"}", null, capabilities);

        route.Allowed.Should().BeTrue();
        route.SelectedCapability.Should().Be("open_application");
    }

    [Fact]
    public void Side_Effect_Input_On_Unrelated_Outcome_Is_Still_Denied()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.FileState, "computer_type");

        var result = new GoalRelevanceGate().Evaluate(
            Goal("save a file"), outcome, step,
            "computer_type", "{\"text\":\"hello\"}");

        result.Allowed.Should().BeFalse();
        result.FailureCode.Should().Be("GoalIrrelevantAction");
    }

    [Fact]
    public void Semantic_Activation_Allows_Uia_Click_On_A_Generic_Ui_Outcome()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.Generic, "uia_click");

        var result = new GoalRelevanceGate().Evaluate(
            Goal("activate the fixture button"),
            outcome,
            step,
            "uia_click",
            """{"application":"SRAN Reliability Fixture","element_id":1}""");

        result.Allowed.Should().BeTrue();
    }

    [Fact]
    public void Semantic_Set_Allows_Uia_Value_On_A_Generic_Ui_Outcome()
    {
        var (outcome, step, _) = Correlated(OutcomeKind.Generic, "uia_set_value");

        var result = new GoalRelevanceGate().Evaluate(
            Goal("set Fixture input to phase-three-value"),
            outcome,
            step,
            "uia_set_value",
            """{"application":"SRAN Reliability Fixture","text":"phase-three-value"}""");

        result.Allowed.Should().BeTrue();
    }

    [Fact]
    public void Key_Sequence_Requires_Array_Not_Comma_Text()
    {
        using var invalid = JsonDocument.Parse("{\"keys\":\"Ctrl+A,Backspace\"}");
        using var valid = JsonDocument.Parse("{\"sequence\":[\"Ctrl+A\",\"Backspace\"]}");

        ComputerKeyTool.TryParseSequence(invalid.RootElement, out _, out var invalidError)
            .Should().BeFalse();
        invalidError.Should().StartWith("InvalidInputSchema");
        ComputerKeyTool.TryParseSequence(valid.RootElement, out var sequence, out var validError)
            .Should().BeTrue();
        validError.Should().BeNull();
        sequence.Should().Equal("Ctrl+A", "Backspace");
    }

    [Fact]
    public void Tool_Card_Is_Green_Only_For_Runtime_Verified_Result()
    {
        var pending = new ToolExecutionItemViewModel("a", "computer_type", "{}");
        pending.Complete(new AgentToolResultEvent(
            "a", "computer_type", "{}", true, "accepted", 1));

        pending.IsOk.Should().BeFalse();
        pending.IsVerificationPending.Should().BeTrue();

        var verified = new ToolExecutionItemViewModel("b", "computer_type", "{}");
        verified.Complete(new AgentToolResultEvent(
            "b", "computer_type", "{}", true, "verified", 1,
            UnifiedResult: new AgentToolResult(
                "b", "task", "plan", "milestone", "step", "action", "computer_type",
                true, true, true, true, true, null, null, true, true, true, true,
                null, null, false, [], ["evidence"], DateTimeOffset.UtcNow,
                DateTimeOffset.UtcNow, 1, SensitiveDataClassification.Public)));
        verified.IsOk.Should().BeTrue();
    }

    [Fact]
    public void Plan_View_Renders_Authoritative_Outcomes_And_Version()
    {
        var view = new AuthoritativePlanView(
            "task", "Goal text", "plan", 4, "Running", "Active",
            null, null, "uia_set_value", "InputReady", null,
            [new("outcome", "Required result", "EffectPending", true, [], 1)], []);
        var message = new MessageViewModel("m", MessageRole.Assistant, "", DateTimeOffset.UtcNow);

        message.UpdatePlan(view);

        message.PlanHeader.Should().Contain("plan v4");
        message.PlanSteps.Should().ContainSingle();
        message.PlanSteps[0].IsDone.Should().BeFalse();
        message.PlanSteps[0].IsInProgress.Should().BeTrue();
    }

    private static CompiledOutcomeGraph Compile(string request) => Compile(Goal(request));

    private static CompiledOutcomeGraph Compile(GoalSpec goal) =>
        new OutcomeCompiler().Compile(goal, "task", "milestone", Skill());

    private static GoalSpec Goal(string request, IReadOnlyList<string>? constraints = null) => new()
    {
        GoalId = "goal",
        OriginalUserRequest = request,
        NormalizedGoal = request,
        DesiredOutcome = request,
        Constraints = constraints?.ToList() ?? [],
        CreatedAtUtc = DateTimeOffset.UtcNow,
        UpdatedAtUtc = DateTimeOffset.UtcNow
    };

    private static SkillDefinition Skill() => new()
    {
        SkillId = "skill",
        Name = "Runtime test",
        Version = "1",
        Description = "Runtime semantic test skill",
        RecoveryPolicy = "Observe then change strategy.",
        CompensationPolicy = "No compensation required.",
        TrustedSourcesPolicy = "Structured runtime evidence only."
    };

    private static TaskPlan Plan(CompiledOutcomeGraph graph) => new()
    {
        PlanId = "plan",
        TaskId = "task",
        Version = 1,
        GoalVersion = 1,
        PlanningReason = "test",
        CreatedBy = "runtime",
        CreatedAtUtc = DateTimeOffset.UtcNow,
        Outcomes = graph.Outcomes.ToList(),
        Milestones =
        [
            new TaskMilestone
            {
                MilestoneId = "milestone",
                TaskId = "task",
                PlanId = "plan",
                Title = "test",
                Objective = "test",
                Steps = graph.Steps.ToList()
            }
        ]
    };

    private static (TaskOutcome Outcome, TaskStep Step, TaskAction Action) Correlated(
        OutcomeKind kind,
        string toolName)
    {
        var outcome = new TaskOutcome
        {
            OutcomeId = "outcome",
            TaskId = "task",
            Description = "intended outcome",
            IntendedState = "intended state",
            Kind = kind,
            SourceGoalVersion = 1
        };
        var step = new TaskStep
        {
            StepId = "step",
            MilestoneId = "milestone",
            TaskId = "task",
            OutcomeId = outcome.OutcomeId,
            Description = outcome.Description,
            IntendedOutcome = outcome.IntendedState,
            VerificationPlan = "independent evidence",
            IdempotencyPolicy = "reconcile",
            RetryPolicy = "bounded"
        };
        var action = new TaskAction
        {
            ActionId = "action",
            TaskId = "task",
            PlanId = "plan",
            MilestoneId = "milestone",
            StepId = "step",
            OutcomeId = outcome.OutcomeId,
            ToolCallId = "call",
            ToolName = toolName,
            ArgumentsFingerprint = "hash",
            IdempotencyKey = "key",
            CreatedAtUtc = DateTimeOffset.UtcNow,
            UpdatedAtUtc = DateTimeOffset.UtcNow
        };
        return (outcome, step, action);
    }

    private static TaskAction FailedAction(string toolName) => new()
    {
        ActionId = $"action_{Guid.NewGuid():N}",
        TaskId = "task",
        PlanId = "plan",
        MilestoneId = "milestone",
        StepId = "step",
        OutcomeId = "outcome",
        ToolCallId = "call",
        ToolName = toolName,
        ArgumentsFingerprint = "hash",
        IdempotencyKey = $"key_{Guid.NewGuid():N}",
        Status = TaskActionStatus.Failed,
        CreatedAtUtc = DateTimeOffset.UtcNow,
        UpdatedAtUtc = DateTimeOffset.UtcNow
    };

    private static CapabilityHealth Capability(string id, bool verification) => new()
    {
        CapabilityId = id,
        Provider = "test",
        Availability = CapabilityAvailability.Available,
        Health = CapabilityHealthState.Healthy,
        VerificationSupport = verification,
        UpdatedAtUtc = DateTimeOffset.UtcNow
    };
}
