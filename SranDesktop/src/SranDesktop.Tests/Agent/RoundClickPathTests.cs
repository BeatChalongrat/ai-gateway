using FluentAssertions;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Click-path round: R5 human-error mapping for click FailureCodes/sentences and
/// the retryable classification for click transients. (R1 readiness wait + focus-retry
/// live in ComputerClickTool/UiaClickTool and need a live desktop to exercise — spot-check
/// there.)</summary>
public class RoundClickPathTests
{
    // ── R5: click red codes/sentences resolve to a human error (never raw) ──

    [Theory]
    [InlineData("Element \"Save\" is disabled.", "tool.error.disabled")]
    [InlineData("Element \"X\" has no clickable area.", "tool.error.noClickable")]
    [InlineData("OsInputRejected: Windows accepted 0 of 2 events", "tool.error.osRejected")]
    [InlineData("TargetResolutionTimeout: UI Automation did not respond in time.", "tool.error.timeout")]
    [InlineData("ForegroundWindowMismatch: the wrong window is in front", "tool.error.validate")]
    [InlineData("PointCoveredByOtherWindow: occluded", "tool.error.validate")]
    [InlineData("CaptureStale: capture_id is stale", "tool.error.stale")]
    [InlineData("WrongWindow: target control PID changed after observation.", "tool.error.wrongWindow")]
    [InlineData("FocusLost: the target window did not reach the foreground", "tool.error.focusLost")]
    [InlineData("ApplicationNotReady: app not input-ready", "tool.error.notReady")]
    public void Click_FailureCode_Maps_To_Human_Error_Key(string text, string expectedKey)
    {
        var code = TransientFailurePolicy.ExtractCode(text);
        ToolExecutionItemViewModel.FailureKey(code).Should().Be(expectedKey);
    }

    [Theory]
    [InlineData("tool.error.disabled")]
    [InlineData("tool.error.noClickable")]
    [InlineData("tool.error.osRejected")]
    [InlineData("tool.error.timeout")]
    public void New_Click_Keys_Resolve_In_Both_Locales(string key)
    {
        var original = LocaleService.Instance.Locale;
        try
        {
            foreach (var locale in new[] { "th", "en" })
            {
                LocaleService.Instance.Locale = locale;
                var resolved = LocaleService.Instance.T(key);
                resolved.Should().NotBe(key, $"{key} must resolve in {locale}");
                resolved.Should().NotBeNullOrWhiteSpace();
            }
        }
        finally { LocaleService.Instance.Locale = original; }
    }

    // ── Phase 0 (UIA-first): OS-rejection mapping must be EXACT, not substring ──
    // A substring "rejected" match wrongly claimed "Windows didn't accept the input"
    // for policy/consent codes that merely CONTAIN "rejected".

    [Fact]
    public void OsInputRejected_Code_Still_Maps_To_OsRejected()
        => ToolExecutionItemViewModel.FailureKey("OsInputRejected")
            .Should().Be("tool.error.osRejected");

    [Theory]
    [InlineData("CapabilityPolicyRejected")]     // router policy block — not an OS rejection
    [InlineData("CapabilitySelectionRequired")]  // router swap request
    [InlineData("Consent rejected by user")]     // permission denial
    [InlineData("The action was rejected by policy")]
    public void NonOs_Rejections_Do_Not_Map_To_OsRejected(string code)
        => ToolExecutionItemViewModel.FailureKey(code)
            .Should().NotBe("tool.error.osRejected");

    // ── classification: click transients auto-retryable; hard-fails not ──

    [Theory]
    [InlineData("CaptureStale")]        // capture went stale — re-observe and retry
    [InlineData("FocusLost")]           // physical click couldn't reach foreground
    [InlineData("ApplicationNotReady")] // readiness timeout before a click
    [InlineData("TargetNotFound")]
    public void Click_Transients_Are_Retryable(string code)
        => TransientFailurePolicy.IsRetryable(code).Should().BeTrue(code);

    [Theory]
    [InlineData("OsInputRejected")]  // Windows hard-rejected the input
    [InlineData("WrongWindow")]      // targeting a logically wrong control
    [InlineData("ElementDisabled")]  // a disabled control won't enable on retry
    public void Click_Hard_Fails_Are_Not_Retryable(string code)
        => TransientFailurePolicy.IsRetryable(code).Should().BeFalse(code);
}
