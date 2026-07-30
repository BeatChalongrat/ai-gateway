using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// Round-1 gate tests — automate the two manual acceptance cases (R3′ amber path,
/// R5 human error) as deterministic checks so CI catches a round-2 regression.
///
/// Both drive the REAL card pipeline: <see cref="ToolExecutionItemViewModel.Complete"/>
/// is the exact mapping <c>ChatViewModel</c> runs for every <see cref="AgentToolResultEvent"/>
/// (ChatViewModel.cs — the <c>AgentToolResultEvent</c> arm calls <c>item.Complete(result)</c>).
/// Feeding it a synthetic event is the production path with the LLM/orchestrator boundary
/// replaced by a hand-built event — no live model, no window, no Notepad injection.
/// State is asserted (IsFailed / IsVerificationPending) rather than pixel colour.
/// </summary>
public class Round1GateTests
{
    // ── Test 1 (R3′): read-back-unavailable → amber, NOT red ──────────────────

    /// <summary>The Win11-Notepad-RichEdit case: input was accepted by the confirmed
    /// receiver but the control exposes no value to read back. computer_type reports
    /// this as an unverified (Success=true) ComputerAction with code "ReadBackUnavailable".
    /// The card must be amber (verification_pending), never a red failure.</summary>
    [Fact]
    public void ReadBackUnavailable_Result_Is_Amber_Not_Failed()
    {
        var evt = UnverifiedTypeEvent(
            failureCode: "ReadBackUnavailable",
            readBackStatus: "unavailable");

        var card = new ToolExecutionItemViewModel(
            "call-type", "computer_type", """{"text":"SRAN round1 works"}""");
        card.Complete(evt); // requireVerification: true (top-level authoritative card)

        card.IsVerificationPending.Should().BeTrue("input accepted but not yet independently verified");
        card.IsFailed.Should().BeFalse("read-back-unavailable is amber, not a red failure");
        card.IsOk.Should().BeFalse("green is reserved for an independently verified effect");
        card.HumanError.Should().BeNull("an amber card carries no failure sentence");

        // R3′ marker: this branch is "ReadBackUnavailable", distinct from a genuine mismatch.
        ToolExecutionItemViewModel.ExtractFailureCode(evt).Should().Be("ReadBackUnavailable");
    }

    /// <summary>Guard the other side of the split: a genuine value mismatch is still an
    /// unverified (amber) card — round 1 did NOT turn mismatch red — but keeps the
    /// distinct "EffectMismatch" marker.</summary>
    [Fact]
    public void EffectMismatch_Result_Is_Also_Amber_With_Its_Own_Marker()
    {
        var evt = UnverifiedTypeEvent(
            failureCode: "EffectMismatch",
            readBackStatus: "mismatch");

        var card = new ToolExecutionItemViewModel(
            "call-type", "computer_type", """{"text":"SRAN round1 works"}""");
        card.Complete(evt);

        card.IsVerificationPending.Should().BeTrue();
        card.IsFailed.Should().BeFalse();
        ToolExecutionItemViewModel.ExtractFailureCode(evt).Should().Be("EffectMismatch");
    }

    // ── Test 2 (R5): FocusLost hard-fail → localized human error, both locales ──

    [Fact]
    public void FocusLost_Failure_Shows_Localized_HumanError_In_Both_Locales()
    {
        // Mirrors the early ToolResult.Fail path: Success=false, no ComputerAction,
        // the code lives as the leading token of the "Error: <Code>: reason" text.
        var evt = new AgentToolResultEvent(
            "call-type", "computer_type", "{}",
            Success: false,
            ResultPreview: "Error: FocusLost: the expected receiver lost foreground before dispatch.",
            DurationMs: 5,
            FullResult: "Error: FocusLost: the expected receiver lost foreground before dispatch.");

        var original = LocaleService.Instance.Locale;
        try
        {
            LocaleService.Instance.Locale = "th";
            var th = new ToolExecutionItemViewModel("call-type", "computer_type", "{}");
            th.Complete(evt);

            th.IsFailed.Should().BeTrue();
            th.HumanError.Should().Be(Dict.Th["tool.error.focusLost"]);
            th.HumanError.Should().NotBe("tool.error.focusLost", "the key must resolve, not leak through");
            // Raw technical code stays reachable for the expandable Output section.
            th.FullResult.Should().Contain("FocusLost");

            LocaleService.Instance.Locale = "en";
            var en = new ToolExecutionItemViewModel("call-type", "computer_type", "{}");
            en.Complete(evt);

            en.HumanError.Should().Be(Dict.En["tool.error.focusLost"]);
            en.HumanError.Should().NotBe(th.HumanError, "switching locale must change the sentence");
        }
        finally
        {
            LocaleService.Instance.Locale = original;
        }
    }

    /// <summary>Every red FailureCode round 1 maps must resolve to a real, non-key
    /// sentence in both locales (no dangling/unresolved key on a failed card).</summary>
    [Theory]
    [InlineData("FocusLost")]
    [InlineData("TargetNotFound")]
    [InlineData("StaleObservation")]
    [InlineData("WrongWindow")]
    [InlineData("UnboundInput")]
    [InlineData("ApplicationNotReady")]
    [InlineData("ForegroundWindowMismatch")]
    public void Every_Red_Code_Resolves_To_A_Sentence_In_Both_Locales(string code)
    {
        var key = ToolExecutionItemViewModel.FailureKey(code);
        var original = LocaleService.Instance.Locale;
        try
        {
            foreach (var locale in new[] { "th", "en" })
            {
                LocaleService.Instance.Locale = locale;
                var resolved = LocaleService.Instance.T(key);
                resolved.Should().NotBe(key, $"key {key} must resolve in {locale}");
                resolved.Should().NotBeNullOrWhiteSpace();
            }
        }
        finally
        {
            LocaleService.Instance.Locale = original;
        }
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    /// <summary>An accepted-but-unverified computer_type event (Success=true carrying a
    /// ComputerAction with EffectVerified=false), matching what the tool returns via
    /// <c>ToolResult.FromComputerAction</c> for the not-yet-verified branches.</summary>
    private static AgentToolResultEvent UnverifiedTypeEvent(string failureCode, string readBackStatus)
    {
        var action = new ComputerActionResult(
            ActionRequested: true, TargetResolved: true, TargetValidated: true,
            ExecutionAttempted: true, OsInputAccepted: true, EffectVerified: false,
            TaskCompleted: false,
            FailureCode: failureCode,
            FailureReason: "unverified in-call read-back",
            Retryable: true,
            Evidence: new[]
            {
                "os_input_accepted=true",
                "receiver_confirmed=true",
                "value_read_back=false",
                "read_back_status=" + readBackStatus,
            },
            CaptureBeforeId: "cap-1", CaptureAfterId: null,
            WindowHandle: 0x1234, ProcessId: 4242,
            ExecutedX: null, ExecutedY: null, TargetX: null, TargetY: null,
            TargetWidth: null, TargetHeight: null, DurationMs: 0,
            TargetSelectedCorrectly: true, TargetActivated: true);

        return new AgentToolResultEvent(
            "call-type", "computer_type", """{"text":"SRAN round1 works"}""",
            Success: true,
            ResultPreview: "Input was accepted but not yet independently verified.",
            DurationMs: 12,
            FullResult: "Input was accepted but not yet independently verified.",
            ComputerAction: action,
            UnifiedResult: null);
    }
}
